"""Job indexing pengetahuan admin: berkas → bagian → chunk → (pengayaan) → store.

Job berjalan di thread daemon dan SERIAL (satu antrian, satu pekerja): dua PDF
besar diproses bersamaan adalah kandidat OOM paling nyata di server 3,8 GB yang
sudah dihuni torch/DINOv2. Progres ditulis ke `dokumen.json` supaya halaman admin
bisa menampilkannya; `pengetahuan.json` baru ditulis di AKHIR (atomik), jadi
selama re-index isi lama tetap tersaji — tak ada jendela kosong.

Pengayaan LLM (judul_id/kata_kunci/ringkasan Indonesia) dilakukan SEKALI di sini,
bukan saat query — biaya token nol setiap kali user bertanya. Bila LLM tak
dikonfigurasi/gagal/dimatikan admin, fallback deterministik dipakai dan status
tetap "selesai": fitur ini WAJIB jalan tanpa LLM.
"""
from __future__ import annotations

import json
import logging
import queue
import re
import threading

import requests

from ..core.config import get_settings
from . import pengetahuan, pengetahuan_extract as ext

logger = logging.getLogger("maspart.pengetahuan")

_TIMEOUT = 90
_BATCH_LLM = 10               # chunk per panggilan LLM
_MAX_LLM_CALLS = 40           # plafon biaya per dokumen
MAX_CHUNK_DOKUMEN = 400
MAX_CHUNK_STORE = 5000

_ANTRIAN: "queue.Queue[str]" = queue.Queue()
_PEKERJA: threading.Thread | None = None
_MULAI_LOCK = threading.Lock()

_STOPWORD = {
    "yang", "dan", "atau", "untuk", "dari", "pada", "dengan", "dalam", "akan",
    "adalah", "tidak", "bisa", "juga", "agar", "oleh", "sebagai", "ini", "itu",
    "the", "and", "for", "with", "from", "that", "this", "are", "was", "will",
}


# ── bangun chunk ─────────────────────────────────────────────────────
def _judul_fallback(bagian: dict, dok: dict) -> str:
    """Judul chunk tanpa LLM: heading DOCX / nama sheet / baris pertama yang
    tampak seperti judul / nomor halaman."""
    sub = (bagian.get("sub") or "").strip()
    hal = bagian.get("halaman") or 0
    teks = (bagian.get("teks") or "").strip()
    baris1 = teks.splitlines()[0].strip() if teks else ""
    if baris1 and len(baris1) <= 80 and not baris1.endswith("."):
        awal = baris1
    elif sub:
        awal = sub
    else:
        awal = dok.get("judul") or "Pengetahuan"
    if sub and awal != sub:
        awal = f"{sub} — {awal}"
    if hal:
        awal = f"{awal} (hal {hal})"
    return awal[:80]


def _kata_kunci_fallback(bagian: dict, dok: dict) -> list[str]:
    """Token paling sering muncul + judul dokumen + tag admin."""
    tabel = " ".join(" ".join(str(c) for c in (b or [])) for b in (bagian.get("tabel") or []))
    teks = f"{bagian.get('teks','')} {tabel}".lower()
    hitung: dict[str, int] = {}
    for w in re.findall(r"[a-z][a-z0-9\-]{3,}", teks):
        if w in _STOPWORD:
            continue
        hitung[w] = hitung.get(w, 0) + 1
    top = sorted(hitung, key=lambda w: (-hitung[w], w))[:8]
    return pengetahuan.clean_list([*(dok.get("tag") or []), *top], 8)


def _ringkas_fallback(bagian: dict) -> str:
    teks = (bagian.get("teks") or "").strip()
    if teks:
        return teks[:200]
    baris = bagian.get("tabel") or []
    if baris:
        return ("Tabel: " + ", ".join(str(c) for c in baris[0] if c))[:200]
    return ""


def bangun_chunks(dok: dict, bagian: list[dict], sumber: str,
                  tipe: str, mulai_seq: int = 0,
                  simpan_gambar=None) -> list[dict]:
    """Bagian mentah → record chunk siap simpan (belum diperkaya LLM)."""
    out: list[dict] = []
    for n, b in enumerate(bagian):
        seq = mulai_seq + n
        refs: list[str] = []
        for data in (b.get("gambar") or []):
            if simpan_gambar is None:
                break
            f = simpan_gambar(data, seq, len(refs))
            if f:
                refs.append(f)
        teks = (b.get("teks") or "")[:1800]
        tabel = [[str(c) for c in baris] for baris in (b.get("tabel") or [])]
        if not teks.strip() and not tabel and not refs:
            continue
        out.append({
            "id": pengetahuan.chunk_id(dok["id"], seq),
            "dok_id": dok["id"],
            "judul": dok.get("judul") or "",
            "judul_id": _judul_fallback(b, dok),
            "kata_kunci": _kata_kunci_fallback(b, dok),
            "ringkasan": _ringkas_fallback(b),
            "teks": teks,
            "tabel": tabel,
            "gambar_ref": refs,
            "sumber": sumber,
            "halaman": b.get("halaman") or 0,
            "tipe": tipe,
            "untuk_pembeli": bool(dok.get("untuk_pembeli")),
            "dicari": True,
            "kode": pengetahuan.kode_dari_teks(f"{teks} {' '.join(str(c) for baris in tabel for c in baris)}"),
        })
    return out


# ── pengayaan LLM ────────────────────────────────────────────────────
_PROMPT_SISTEM = (
    "Kamu pustakawan teknis suku cadang alat berat & truk di Indonesia. Untuk TIAP "
    "potongan dokumen yang diberikan, buat: judul_id (judul ringkas Bahasa Indonesia, "
    "maks 80 karakter), kata_kunci (maks 8 istilah Bahasa Indonesia yang biasa dipakai "
    "mekanik/pembeli saat mencari hal ini, termasuk sinonim lapangan), dan ringkasan "
    "(maks 2 kalimat Bahasa Indonesia). "
    'Jawab HANYA JSON dengan bentuk: {"chunk": [{"id": "<id potongan>", '
    '"judul_id": "...", "kata_kunci": ["..."], "ringkasan": "..."}]}. '
    "⛔ ATURAN KERAS: gunakan HANYA informasi yang tertulis di potongan. DILARANG "
    "menambah fakta, angka, satuan, atau nomor part yang tidak ada di potongan itu."
)


def _validasi_pengayaan(item: dict, chunk: dict) -> dict:
    """Pagar anti-halusinasi: kata kunci ber-ANGKA yang tak muncul di teks chunk
    DIBUANG — tanpa ini LLM bisa menyuntikkan nomor part karangan ke indeks, dan
    indeks yang salah lebih berbahaya daripada indeks yang miskin."""
    hay = f"{chunk.get('teks','')} {' '.join(str(c) for b in (chunk.get('tabel') or []) for c in b)}".lower()
    kk: list[str] = []
    for k in (item.get("kata_kunci") or [])[:12]:
        s = str(k).strip()
        if not s or len(s) < 2:
            continue
        if any(c.isdigit() for c in s) and s.lower() not in hay:
            continue
        kk.append(s)
    out = {}
    if isinstance(item.get("judul_id"), str) and item["judul_id"].strip():
        out["judul_id"] = item["judul_id"].strip()[:80]
    if kk:
        out["kata_kunci"] = pengetahuan.clean_list(kk, 8)
    if isinstance(item.get("ringkasan"), str) and item["ringkasan"].strip():
        out["ringkasan"] = item["ringkasan"].strip()[:300]
    return out


def _llm_batch(batch: list[dict]) -> dict[str, dict]:
    s = get_settings()
    muatan = [{"id": c["id"],
               "isi": (c.get("teks") or "")[:1200],
               "tabel": (c.get("tabel") or [])[:4]} for c in batch]
    r = requests.post(
        f"{s.deepseek_base_url.rstrip('/')}/chat/completions",
        headers={"Authorization": f"Bearer {s.deepseek_api_key}",
                 "Content-Type": "application/json"},
        json={
            "model": s.deepseek_model,
            "messages": [
                {"role": "system", "content": _PROMPT_SISTEM},
                {"role": "user", "content": json.dumps({"chunk": muatan}, ensure_ascii=False)},
            ],
            "temperature": 0.0,
            "max_tokens": 2000,
            "response_format": {"type": "json_object"},
        },
        timeout=_TIMEOUT,
    )
    if r.status_code >= 400:
        raise RuntimeError(f"DeepSeek error {r.status_code}")
    isi = ((r.json().get("choices") or [{}])[0].get("message") or {}).get("content") or "{}"
    data = json.loads(isi)
    items = data.get("chunk") if isinstance(data, dict) else None
    return {str(i.get("id")): i for i in (items or []) if isinstance(i, dict)}


def perkaya(chunks: list[dict], lapor=None) -> str:
    """Perkaya judul_id/kata_kunci/ringkasan lewat LLM. Return "llm" | "campuran"
    | "fallback". TIDAK PERNAH melempar — kegagalan LLM hanya berarti fallback
    deterministik yang sudah terpasang tetap dipakai."""
    s = get_settings()
    if not getattr(s, "ai_configured", False):
        return "fallback"
    batches = [chunks[i:i + _BATCH_LLM] for i in range(0, len(chunks), _BATCH_LLM)]
    batches = batches[:_MAX_LLM_CALLS]
    kena = 0
    for n, batch in enumerate(batches):
        if lapor:
            lapor(n + 1, len(batches))
        try:
            hasil = _llm_batch(batch)
        except Exception as err:
            logger.info("pengayaan LLM gagal batch %s: %s", n + 1, err)
            continue
        for c in batch:
            item = hasil.get(c["id"])
            if not item:
                continue          # id asing / tak dijawab → biarkan fallback
            upd = _validasi_pengayaan(item, c)
            if upd:
                c.update(upd)
                kena += 1
    if kena == 0:
        return "fallback"
    return "llm" if kena == len(chunks) else "campuran"


# ── job ──────────────────────────────────────────────────────────────
def _progres(langkah: str, kini: int = 0, total: int = 0) -> dict:
    persen = int(kini * 100 / total) if total else 0
    return {"langkah": langkah, "kini": kini, "total": total, "persen": persen}


def _simpan_gambar_factory(dok_id: str, counter: dict):
    def simpan(data: bytes, seq: int, idx: int) -> str:
        if counter["n"] >= ext.MAX_GAMBAR:
            return ""
        f = f"{dok_id}_{counter['n']:03d}.png"
        d = pengetahuan.media_dir()
        d.mkdir(parents=True, exist_ok=True)
        try:
            (d / f).write_bytes(data)
        except OSError:
            return ""
        counter["n"] += 1
        return f
    return simpan


def proses(dok_id: str) -> None:
    """Indeks satu dokumen sampai tuntas. Dipanggil pekerja; aman dipanggil
    langsung di test (sinkron)."""
    dok = pengetahuan.get_dokumen(dok_id)
    if not dok:
        return
    pengetahuan.set_status(dok_id, "proses", _progres("Menyiapkan"), error="")
    counter = {"n": 0}
    simpan = _simpan_gambar_factory(dok_id, counter)
    semua: list[dict] = []
    catatan: list[str] = []
    seq = 0

    # 1) isi yang diketik admin langsung
    teks_admin = (dok.get("teks_admin") or "").strip()
    if teks_admin:
        bagian = [ext._bagian(teks=t) for t in ext.potong_teks(teks_admin)]
        baru = bangun_chunks(dok, bagian, "teks-admin", "teks", seq, simpan)
        semua.extend(baru)
        seq += len(bagian)
    tabel_admin = dok.get("tabel_admin") or []
    if tabel_admin:
        bagian = ext._tabel_jadi_bagian(tabel_admin, 0, "")
        baru = bangun_chunks(dok, bagian, "tabel-admin", "tabel", seq, simpan)
        semua.extend(baru)
        seq += len(bagian)

    # 2) berkas unggahan
    berkas = dok.get("berkas") or []
    for i, b in enumerate(berkas):
        nama = b.get("nama") or ""
        simpan_nama = b.get("nama_simpan") or ""
        pengetahuan.set_status(
            dok_id, "proses",
            _progres(f"Membaca {nama}", i, len(berkas)))
        p = pengetahuan.berkas_dir() / simpan_nama
        try:
            data = p.read_bytes()
        except OSError:
            catatan.append(f"{nama}: berkas asli tak ditemukan di server.")
            continue
        try:
            bagian = ext.ekstrak(data, nama)
        except ValueError as err:
            catatan.append(f"{nama}: {err}")
            continue
        except Exception as err:                       # pragma: no cover
            logger.exception("ekstraksi gagal %s", nama)
            catatan.append(f"{nama}: gagal dibaca ({err}).")
            continue
        ekst = (b.get("ext") or "").lstrip(".")
        tipe = {"xlsx": "excel", "xlsm": "excel", "jpg": "gambar",
                "jpeg": "gambar", "png": "gambar"}.get(ekst, ekst or "teks")
        if ekst == "pdf" and ext.pdf_tanpa_teks(bagian):
            catatan.append(
                f"{nama}: PDF ini hasil pindaian (tanpa lapisan teks) — hanya "
                "gambar halaman yang terindeks. Tambahkan keterangan manual "
                "agar isinya bisa dicari.")
        sisa = MAX_CHUNK_DOKUMEN - len(semua)
        if sisa <= 0:
            catatan.append(f"{nama}: dilewati — dokumen sudah mencapai batas "
                           f"{MAX_CHUNK_DOKUMEN} bagian.")
            continue
        if len(bagian) > sisa:
            catatan.append(f"{nama}: hanya {sisa} bagian pertama yang diindeks "
                           f"(batas {MAX_CHUNK_DOKUMEN} bagian per dokumen).")
            bagian = bagian[:sisa]
        baru = bangun_chunks(dok, bagian, nama, tipe, seq, simpan)
        semua.extend(baru)
        seq += len(bagian)

    if not semua:
        pengetahuan.replace_chunks(dok_id, [])
        pengetahuan.set_status(
            dok_id, "gagal", _progres("Selesai", 1, 1), jumlah_chunk=0,
            error="; ".join(catatan) or "Tidak ada isi yang bisa diindeks.")
        return

    # 3) pengayaan
    pakai_ai = bool(dok.get("pakai_ai", True))
    pengayaan = "fallback"
    if pakai_ai:
        pengetahuan.set_status(dok_id, "proses", _progres("Memperkaya dengan AI"))

        def lapor(n, total):
            pengetahuan.set_status(
                dok_id, "proses", _progres("Memperkaya dengan AI", n, total))
        pengayaan = perkaya(semua, lapor)

    # 4) simpan
    pengetahuan.set_status(dok_id, "proses", _progres("Menyimpan indeks"))
    total_lain = pengetahuan.count() - len(pengetahuan.chunks_dokumen(dok_id))
    if total_lain + len(semua) > MAX_CHUNK_STORE:
        boleh = max(MAX_CHUNK_STORE - total_lain, 0)
        catatan.append(f"Hanya {boleh} bagian yang disimpan — store sudah mencapai "
                       f"batas {MAX_CHUNK_STORE} bagian. Hapus dokumen lama dulu.")
        semua = semua[:boleh]
    pengetahuan.replace_chunks(dok_id, semua)
    pengetahuan.set_status(
        dok_id,
        "selesai_sebagian" if catatan else "selesai",
        _progres("Selesai", 1, 1),
        jumlah_chunk=len(semua), pengayaan=pengayaan,
        error="; ".join(catatan),
    )


def _loop() -> None:                                   # pragma: no cover
    while True:
        dok_id = _ANTRIAN.get()
        try:
            proses(dok_id)
        except Exception:
            logger.exception("job indexing gagal untuk %s", dok_id)
            try:
                pengetahuan.set_status(dok_id, "gagal", _progres("Gagal", 1, 1),
                                       error="Terjadi gangguan internal saat mengindeks.")
            except Exception:
                pass
        finally:
            _ANTRIAN.task_done()


def antre(dok_id: str) -> None:
    """Masukkan dokumen ke antrian indexing. Pekerja SERIAL — job kedua menunggu
    yang pertama selesai (mencegah dua PDF besar diproses bersamaan)."""
    global _PEKERJA
    with _MULAI_LOCK:
        if _PEKERJA is None or not _PEKERJA.is_alive():
            _PEKERJA = threading.Thread(target=_loop, daemon=True,
                                        name="pengetahuan-index")
            _PEKERJA.start()
    _ANTRIAN.put(dok_id)
