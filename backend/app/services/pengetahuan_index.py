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
# Partikel Mandarin paling umum — tanpa ini bigram sampah seperti "的货"
# mendominasi daftar kata kunci.
_STOP_CJK = set("的了在是和与及或也都很就还把被为对")

_HAN_RE = re.compile(r"[㐀-䶿一-鿿豈-﫿]")
_KANA_RE = re.compile(r"[぀-ヿ]")
_CJK_RUN_RE = re.compile(r"[㐀-䶿一-鿿豈-﫿぀-ヿ]+")

_STOP_ID = {"yang", "dan", "untuk", "dari", "dengan", "pada", "tidak", "adalah",
            "akan", "atau", "ini", "itu", "harus", "dapat"}
_STOP_EN = {"the", "and", "for", "with", "from", "that", "this", "shall", "must",
            "will", "are", "was", "have", "been"}


def deteksi_bahasa(teks: str) -> str:
    """Bahasa dominan sebuah potongan: "zh" | "ja" | "en" | "id" | "".

    Dipakai HANYA untuk memutuskan apakah asisten perlu diperintahkan
    menerjemahkan. Ragu → "" (dianggap Indonesia) supaya tak ada token
    instruksi yang terbuang untuk dokumen Indonesia — mayoritas kasus.
    """
    t = (teks or "").strip()
    if len(t) < 12:
        return ""
    n = len(t)
    if len(_KANA_RE.findall(t)) / n > 0.05:
        return "ja"
    if len(_HAN_RE.findall(t)) / n > 0.15:
        return "zh"
    kata = set(re.findall(r"[a-z]+", t.lower()))
    id_n, en_n = len(kata & _STOP_ID), len(kata & _STOP_EN)
    if id_n >= 2 and id_n > en_n:
        return "id"
    if en_n >= 2 and en_n > id_n:
        return "en"
    return ""


def _bigram_cjk(teks: str, batas: int = 8) -> list[str]:
    """Bigram CJK paling sering muncul — kata kunci cadangan untuk dokumen
    non-Latin, yang kalau tidak begini kata kuncinya keluar KOSONG."""
    hitung: dict[str, int] = {}
    for run in _CJK_RUN_RE.findall(teks or ""):
        for i in range(len(run) - 1):
            bg = run[i:i + 2]
            if bg[0] in _STOP_CJK or bg[1] in _STOP_CJK:
                continue
            hitung[bg] = hitung.get(bg, 0) + 1
    return sorted(hitung, key=lambda b: (-hitung[b], b))[:batas]


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
    """Token paling sering muncul + judul dokumen + tag admin.

    Untuk teks non-Latin, pola huruf Latin menghasilkan NOL kata kunci sehingga
    dokumennya tak pernah bisa ditemukan; bigram CJK menambal itu.
    """
    tabel = " ".join(" ".join(str(c) for c in (b or [])) for b in (bagian.get("tabel") or []))
    mentah = f"{bagian.get('teks','')} {tabel}"
    teks = mentah.lower()
    hitung: dict[str, int] = {}
    for w in re.findall(r"[a-z][a-z0-9\-]{3,}", teks):
        if w in _STOPWORD:
            continue
        hitung[w] = hitung.get(w, 0) + 1
    top = sorted(hitung, key=lambda w: (-hitung[w], w))[:8]
    # Sinyal terstruktur ikut jadi kata kunci: nama kolom tabel & breadcrumb bab
    # sering justru istilah yang dipakai orang saat bertanya.
    ekstra = [*(bagian.get("kolom") or [])[:4], *(bagian.get("jalur") or [])[-2:]]
    if len(top) < 4:
        top.extend(_bigram_cjk(mentah, 8 - len(top)))
    return pengetahuan.clean_list([*(dok.get("tag") or []), *ekstra, *top], 8)


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
        info: list[dict] = []
        meta = b.get("gambar_meta") or []
        for j, data in enumerate(b.get("gambar") or []):
            if simpan_gambar is None:
                break
            f = simpan_gambar(data, seq, len(refs))
            if not f:
                continue
            refs.append(f)
            m = meta[j] if j < len(meta) else {}
            info.append({"file": f, "caption": (m.get("caption") or "")[:300],
                         "halaman": m.get("halaman") or b.get("halaman") or 0})
        teks = (b.get("teks") or "")[:1800]
        tabel = [[str(c) for c in baris] for baris in (b.get("tabel") or [])]
        jalur = [str(x) for x in (b.get("jalur") or [])][-4:]
        kolom = [str(x) for x in (b.get("kolom") or [])]
        # Caption gambar digabung jadi satu ladang cari — inilah yang membuat
        # gambar bisa DITEMUKAN sesuai konteks pertanyaan, bukan cuma ikut
        # menempel pada chunk yang kebetulan terpilih.
        gambar_teks = " ".join(i["caption"] for i in info if i["caption"])[:600]
        # Chunk tanpa sinyal APA PUN (teks/tabel/caption/breadcrumb/gambar)
        # skornya selalu 0 → sampah indeks, jangan disimpan. Chunk yang isinya
        # hanya GAMBAR tetap disimpan: judul dokumen + tag admin dari
        # _judul_fallback/_kata_kunci_fallback membuatnya tetap bisa ditemukan.
        if not teks.strip() and not tabel and not gambar_teks and not jalur and not refs:
            continue
        out.append({
            "id": pengetahuan.chunk_id(dok["id"], seq),
            "dok_id": dok["id"],
            "skema": 2,
            "bahasa": deteksi_bahasa(teks),
            "judul": dok.get("judul") or "",
            "judul_id": _judul_fallback(b, dok),
            "kata_kunci": _kata_kunci_fallback(b, dok),
            "ringkasan": _ringkas_fallback(b),
            "teks": teks,
            "tabel": tabel,
            "kolom": kolom,
            "baris_total": b.get("baris_total") or 0,
            "baris_dari": b.get("baris_dari") or 0,
            "jalur": jalur,
            "gambar_teks": gambar_teks,
            "gambar_info": info,
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
    "Potongan bisa berbahasa China/Inggris/Jepang — output WAJIB tetap Bahasa "
    "Indonesia (terjemahkan istilahnya; nomor part & kode tetap apa adanya). "
    'Jawab HANYA JSON dengan bentuk: {"chunk": [{"id": "<id potongan>", '
    '"judul_id": "...", "kata_kunci": ["..."], "ringkasan": "..."}]}. '
    "⛔ ATURAN KERAS: gunakan HANYA informasi yang tertulis di potongan. DILARANG "
    "menambah fakta, angka, satuan, atau nomor part yang tidak ada di potongan itu."
)

# Teguran putaran kedua: DeepSeek pada dokumen asing kerap IKUT bahasa sumber
# meski prompt minta Indonesia — tanpa teguran eksplisit hasil ulangannya sama.
_PROMPT_TEGUR = (
    "PENGINGAT KERAS: jawaban sebelumnya untuk potongan-potongan ini ikut bahasa "
    "dokumen (bukan Indonesia). judul_id, kata_kunci, dan ringkasan WAJIB 100% "
    "Bahasa Indonesia — TERJEMAHKAN istilah asingnya; nomor part/kode apa adanya."
)

# Porsi karakter CJK maksimal agar sebuah field pengayaan masih dianggap
# Bahasa Indonesia (istilah teknis China dalam kurung sesekali masih lolos).
_CJK_MAKS = 0.2


def _cjk_ratio(s: str) -> float:
    s = (s or "").strip()
    if not s:
        return 0.0
    return (len(_HAN_RE.findall(s)) + len(_KANA_RE.findall(s))) / len(s)


def _pengayaan_asing(item: dict) -> bool:
    """True bila LLM menjawab dalam bahasa sumber (CJK), bukan Indonesia.

    Inilah bug yang membuat 18% chunk terindeks dengan judul_id/kata_kunci
    Mandarin berstatus "llm" sukses: tak ada yang memeriksa BAHASA jawaban.
    Chunk seperti itu tak akan pernah ketemu dari pertanyaan Indonesia —
    padahal justru dokumen asing yang paling butuh jembatan pencarian ini.
    """
    if _cjk_ratio(str(item.get("judul_id") or "")) > _CJK_MAKS:
        return True
    if _cjk_ratio(str(item.get("ringkasan") or "")) > _CJK_MAKS:
        return True
    kk = [str(k) for k in (item.get("kata_kunci") or []) if str(k).strip()]
    asing = sum(1 for k in kk if _cjk_ratio(k) > _CJK_MAKS)
    return bool(kk) and asing > len(kk) / 2


def _validasi_pengayaan(item: dict, chunk: dict) -> dict:
    """Pagar anti-halusinasi: kata kunci ber-ANGKA yang tak muncul di teks chunk
    DIBUANG — tanpa ini LLM bisa menyuntikkan nomor part karangan ke indeks, dan
    indeks yang salah lebih berbahaya daripada indeks yang miskin.

    Pagar bahasa: field pengayaan yang dominan CJK juga DIBUANG per-field —
    tujuan pengayaan adalah jembatan pencarian Indonesia; jawaban berbahasa
    sumber hanya menduplikasi teks asli dan menyita kursi kata kunci."""
    hay = f"{chunk.get('teks','')} {' '.join(str(c) for b in (chunk.get('tabel') or []) for c in b)}".lower()
    kk: list[str] = []
    for k in (item.get("kata_kunci") or [])[:12]:
        s = str(k).strip()
        if not s or len(s) < 2:
            continue
        if any(c.isdigit() for c in s) and s.lower() not in hay:
            continue
        if _cjk_ratio(s) > _CJK_MAKS:
            continue
        kk.append(s)
    out = {}
    if (isinstance(item.get("judul_id"), str) and item["judul_id"].strip()
            and _cjk_ratio(item["judul_id"]) <= _CJK_MAKS):
        out["judul_id"] = item["judul_id"].strip()[:80]
    if kk:
        out["kata_kunci"] = pengetahuan.clean_list(kk, 8)
    if (isinstance(item.get("ringkasan"), str) and item["ringkasan"].strip()
            and _cjk_ratio(item["ringkasan"]) <= _CJK_MAKS):
        out["ringkasan"] = item["ringkasan"].strip()[:300]
    return out


# Perintah di UJUNG pesan user, bukan hanya di system prompt: terbukti di
# produksi DeepSeek mengabaikan instruksi bahasa di system prompt bila payload
# user murni JSON Mandarin (ikut bahasa payload) — dua putaran gagal semua.
# Probe dengan perintah di ujung pesan user: jawaban Indonesia sempurna.
_PERINTAH_USER = (
    "\n\nBuat judul_id, kata_kunci, dan ringkasan untuk TIAP potongan di atas. "
    "WAJIB 100% Bahasa Indonesia meski potongan berbahasa China/Inggris/Jepang "
    "— TERJEMAHKAN istilahnya; nomor part/kode apa adanya.")
_PERINTAH_TEGUR = (
    " Contoh terjemahan istilah: 缓速器=retarder, 变速箱=gearbox/transmisi, "
    "故障=kerusakan, 诊断=diagnosa, 发动机=mesin/engine.")


def _llm_batch(batch: list[dict], tegur: bool = False) -> dict[str, dict]:
    s = get_settings()
    muatan = [{"id": c["id"],
               "isi": (c.get("teks") or "")[:1200],
               "tabel": (c.get("tabel") or [])[:4]} for c in batch]
    sistem = _PROMPT_SISTEM + (" " + _PROMPT_TEGUR if tegur else "")
    user = (json.dumps({"chunk": muatan}, ensure_ascii=False)
            + _PERINTAH_USER + (_PERINTAH_TEGUR if tegur else ""))
    r = requests.post(
        f"{s.deepseek_base_url.rstrip('/')}/chat/completions",
        headers={"Authorization": f"Bearer {s.deepseek_api_key}",
                 "Content-Type": "application/json"},
        json={
            "model": s.deepseek_model,
            "messages": [
                {"role": "system", "content": sistem},
                {"role": "user", "content": user},
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


def perkaya(chunks: list[dict], lapor=None, catatan: list[str] | None = None) -> str:
    """Perkaya judul_id/kata_kunci/ringkasan lewat LLM. Return "llm" | "campuran"
    | "fallback". TIDAK PERNAH melempar — kegagalan LLM hanya berarti fallback
    deterministik yang sudah terpasang tetap dipakai.

    Jawaban berbahasa sumber (CJK) TIDAK diterima: chunk-nya diantre ke putaran
    kedua dengan teguran eksplisit; yang tetap asing dibiarkan fallback dan
    dilaporkan lewat `catatan` supaya admin tahu, bukan diam-diam sukses."""
    s = get_settings()
    if not getattr(s, "ai_configured", False):
        return "fallback"
    # Chunk yang sudah dikurasi admin TIDAK dikirim ke LLM: hemat token, dan
    # keputusan manusia tak boleh ditimpa tebakan model.
    chunks = [c for c in chunks if not c.get("kurasi")]
    if not chunks:
        return "fallback"
    batches = [chunks[i:i + _BATCH_LLM] for i in range(0, len(chunks), _BATCH_LLM)]
    batches = batches[:_MAX_LLM_CALLS]
    kena = 0
    ulang: list[dict] = []
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
            if _pengayaan_asing(item):
                ulang.append(c)   # ikut bahasa sumber → coba lagi dengan teguran
                continue
            upd = _validasi_pengayaan(item, c)
            if upd:
                c.update(upd)
                kena += 1
    # Putaran kedua, SEKALI: sisa plafon panggilan dipakai (minimal 1 batch).
    gagal_bahasa = 0
    if ulang:
        sisa = max(_MAX_LLM_CALLS - len(batches), 1)
        rbatches = [ulang[i:i + _BATCH_LLM]
                    for i in range(0, len(ulang), _BATCH_LLM)][:sisa]
        dicoba = {c["id"] for b in rbatches for c in b}
        for n, batch in enumerate(rbatches):
            try:
                hasil = _llm_batch(batch, tegur=True)
            except Exception as err:
                logger.info("pengayaan ulang gagal batch %s: %s", n + 1, err)
                hasil = {}
            for c in batch:
                item = hasil.get(c["id"])
                if item and not _pengayaan_asing(item):
                    upd = _validasi_pengayaan(item, c)
                    if upd:
                        c.update(upd)
                        kena += 1
                        continue
                gagal_bahasa += 1
        gagal_bahasa += sum(1 for c in ulang if c["id"] not in dicoba)
    if gagal_bahasa and catatan is not None:
        catatan.append(
            f"{gagal_bahasa} bagian: pengayaan AI tetap berbahasa asing setelah "
            "diulang — judul & kata kunci memakai fallback. Indeks ulang, atau isi "
            "kata kunci Indonesia manual agar bagian itu bisa dicari.")
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
    # Kurasi manual admin diselamatkan SEBELUM chunk lama ditimpa.
    kurasi = pengetahuan.snapshot_kurasi(dok_id)
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

    # 3) pulihkan kurasi manual, lalu pengayaan (LLM tak menimpa kurasi)
    hilang = pengetahuan.terapkan_kurasi(semua, kurasi)
    if hilang:
        catatan.append(f"{hilang} kurasi manual tidak bisa dipulihkan karena isi "
                       "bagiannya berubah — periksa ulang judul & kata kuncinya.")

    pakai_ai = bool(dok.get("pakai_ai", True))
    # Dokumen asing tanpa pengayaan AI = terindeks tapi tak akan pernah ketemu
    # lewat pertanyaan Bahasa Indonesia. Beri tahu admin, jangan diam saja.
    asing = sum(1 for c in semua if c.get("bahasa") in ("zh", "ja", "en"))
    if not pakai_ai and asing > len(semua) / 2:
        catatan.append(
            "Isi dokumen ini bukan Bahasa Indonesia. Nyalakan 'Perkaya dengan AI' "
            "lalu indeks ulang, atau isi kata kunci Indonesia manual — kalau tidak, "
            "asisten sulit menemukannya dari pertanyaan berbahasa Indonesia.")

    pengayaan = "fallback"
    if pakai_ai:
        pengetahuan.set_status(dok_id, "proses", _progres("Memperkaya dengan AI"))

        def lapor(n, total):
            pengetahuan.set_status(
                dok_id, "proses", _progres("Memperkaya dengan AI", n, total))
        pengayaan = perkaya(semua, lapor, catatan)

    # 4) simpan
    pengetahuan.set_status(dok_id, "proses", _progres("Menyimpan indeks"))
    total_lain = pengetahuan.count() - len(pengetahuan.chunks_dokumen(dok_id))
    if total_lain + len(semua) > MAX_CHUNK_STORE:
        boleh = max(MAX_CHUNK_STORE - total_lain, 0)
        catatan.append(f"Hanya {boleh} bagian yang disimpan — store sudah mencapai "
                       f"batas {MAX_CHUNK_STORE} bagian. Hapus dokumen lama dulu.")
        semua = semua[:boleh]
    pengetahuan.replace_chunks(dok_id, semua)
    pengetahuan.sapu_media(dok_id)     # buang gambar yatim dari indeks lama
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
