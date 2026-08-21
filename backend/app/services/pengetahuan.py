"""Pengetahuan TERINDEKS yang ditulis/diunggah ADMIN — store + pencarian.

Beda dari dataset pengetahuan lain (manual_teks, dtc_codes, ai_knowledge) yang
dibangun OFFLINE oleh tools/build_*.py lalu ikut image: isi modul ini ditulis
saat RUNTIME lewat halaman /admin/pengetahuan, jadi harus tinggal di `data/`
(bind-mount rw) supaya selamat dari Redeploy.

Dua file, sengaja dipisah:
  data/ai_pengetahuan/dokumen.json      metadata + status job (unit CRUD admin)
  data/ai_pengetahuan/pengetahuan.json  chunk terindeks (unit pencarian)
`dokumen.json` ditulis tiap beberapa detik selama indexing (progres); kalau
digabung, tiap tick progres mem-invalidasi cache pencarian.

Record chunk: {id, dok_id, judul, judul_id, kata_kunci[], ringkasan, teks,
tabel[][], gambar_ref[], sumber, halaman, tipe, untuk_pembeli, dicari, kode[]}.
`judul_id`+`kata_kunci` = kurasi/pengayaan Indonesia (pola manual_teks) — itulah
yang membuat isi berbahasa/berformat apa pun bisa dicari pakai istilah lapangan.

Pola: CRUD atomik + lock dari `sinonim`, skoring + cache per-mtime dari
`manual_teks`, whitelist gambar dari `manual_media`.
"""
from __future__ import annotations

import json
import os
import re
import threading
import uuid
from pathlib import Path

from ..core.config import get_settings
from . import search_boost, sinonim
from .knowledge_util import load_json

_lock = threading.Lock()

_FNAME_RE = re.compile(r"^[A-Za-z0-9_.\-]+\.(?:png|jpg|jpeg)$")

# Token yang layak dianggap KODE (part number / kode error): mengandung angka,
# cukup panjang, huruf-angka bercampur. Deterministik — tak melibatkan LLM.
_KODE_RE = re.compile(r"\b[A-Z]{0,3}\d[A-Z0-9][A-Z0-9\-./]{2,}\b", re.I)

STATUS = ("antre", "proses", "selesai", "selesai_sebagian", "gagal")


def _dir() -> Path:
    return get_settings().data_path / "ai_pengetahuan"


def _dok_file() -> Path:
    return _dir() / "dokumen.json"


def _chunk_file() -> Path:
    return _dir() / "pengetahuan.json"


def media_dir() -> Path:
    return _dir() / "media"


def berkas_dir() -> Path:
    return _dir() / "berkas"


# ── baca langsung (untuk CRUD tulis) ─────────────────────────────────
def _read(p: Path) -> list[dict]:
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8")) or []
    except Exception:
        return []
    return [e for e in data if isinstance(e, dict)] if isinstance(data, list) else []


def load_dokumen() -> list[dict]:
    return _read(_dok_file())


def load_chunks() -> list[dict]:
    return _read(_chunk_file())


# ── lookup panas (cache per-mtime) ───────────────────────────────────
def dokumen() -> list[dict]:
    """Metadata dokumen dgn cache per-mtime — editan admin langsung terpakai."""
    data = load_json(_dok_file())
    return [e for e in data if isinstance(e, dict)] if isinstance(data, list) else []


def chunks() -> list[dict]:
    """Chunk terindeks dgn cache per-mtime — murah dipanggil per-query."""
    data = load_json(_chunk_file())
    return [e for e in data if isinstance(e, dict)] if isinstance(data, list) else []


def available() -> bool:
    return bool(chunks())


def count() -> int:
    return len(chunks())


def count_dokumen() -> int:
    return len(dokumen())


# ── tulis atomik ─────────────────────────────────────────────────────
def _write(p: Path, rows: list[dict]) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, p)  # atomik: pembaca lihat file lama ATAU baru, tak pernah separuh


def _save_dokumen(rows: list[dict]) -> None:
    _write(_dok_file(), rows)


def _save_chunks(rows: list[dict]) -> None:
    _write(_chunk_file(), rows)


# ── util ─────────────────────────────────────────────────────────────
def new_dok_id() -> str:
    return uuid.uuid4().hex[:8]


def chunk_id(dok_id: str, seq: int) -> str:
    return f"{dok_id}#{seq:04d}"


def clean_list(vals, batas: int = 0) -> list[str]:
    """Rapikan daftar string: strip, buang kosong & duplikat (case-insensitive)."""
    out: list[str] = []
    seen: set[str] = set()
    for v in vals or []:
        s = str(v).strip()
        key = s.lower()
        if s and key not in seen:
            seen.add(key)
            out.append(s)
        if batas and len(out) >= batas:
            break
    return out


def kode_dari_teks(teks: str, batas: int = 12) -> list[str]:
    """Token PN/kode error dari teks — deterministik, tanpa LLM. Dipakai saat
    indexing; diberi bobot tinggi di _score supaya pencarian kode presisi."""
    out: list[str] = []
    seen: set[str] = set()
    for m in _KODE_RE.findall(teks or ""):
        k = m.strip(".-/").upper()
        if len(k) >= 4 and k not in seen:
            seen.add(k)
            out.append(k)
        if len(out) >= batas:
            break
    return out


# ── CRUD dokumen ─────────────────────────────────────────────────────
def add_dokumen(judul: str, deskripsi: str = "", tag=None, untuk_pembeli: bool = False,
                pakai_ai: bool = True, oleh: str = "", berkas=None,
                teks_admin: str = "", tabel_admin=None, asal: str = "") -> dict:
    """Daftarkan dokumen baru berstatus 'antre'. Chunk diisi belakangan oleh job.

    `teks_admin`/`tabel_admin` = isi yang diketik langsung di form (bukan berkas);
    disimpan di sini supaya re-index bisa mengulang tanpa admin mengetik lagi.

    `asal` = KANAL masuknya entri ('' = form /admin/pengetahuan seperti dulu,
    'chat' = diajarkan lewat asisten). Disimpan apa adanya di record supaya
    respons GET membawanya ke web/mobile tanpa mengubah endpoint — admin bisa
    membedakan mana yang diketik di menu dan mana yang lahir dari percakapan
    (dan menyaringnya) tanpa menebak dari `oleh`.
    """
    j = (judul or "").strip()
    if not j:
        raise ValueError("Judul wajib diisi.")
    if not (j and (teks_admin or "").strip() or tabel_admin or berkas):
        raise ValueError("Isi teks, tabel, atau lampirkan minimal satu berkas.")
    d = {
        "id": new_dok_id(),
        "judul": j,
        "deskripsi": (deskripsi or "").strip(),
        "tag": clean_list(tag, 12),
        "berkas": list(berkas or []),
        "teks_admin": (teks_admin or "").strip(),
        "tabel_admin": list(tabel_admin or []),
        "untuk_pembeli": bool(untuk_pembeli),
        "pakai_ai": bool(pakai_ai),
        "aktif": True,
        "status": "antre",
        "progres": {"langkah": "Menunggu giliran", "kini": 0, "total": 0, "persen": 0},
        "jumlah_chunk": 0,
        "pengayaan": "",
        "error": "",
        "oleh": oleh or "",
        "asal": (asal or "").strip(),
    }
    with _lock:
        rows = load_dokumen()
        rows.insert(0, d)  # terbaru di atas
        _save_dokumen(rows)
    return d


def get_dokumen(dok_id: str) -> dict | None:
    return next((d for d in dokumen() if d.get("id") == dok_id), None)


def dokumen_nonaktif() -> list[dict]:
    return [d for d in dokumen() if not d.get("aktif", True)]


def update_dokumen(dok_id: str, **fields) -> dict:
    """Perbarui field dokumen. `untuk_pembeli` DIPROPAGASI ke semua chunk-nya
    supaya tak ada chunk yatim yang publik padahal dokumennya internal."""
    # `asal` ikut boleh diperbarui: entri lama yang isinya DITIMPA lewat chat
    # (aksi 'perbarui' tool ajarkan_pengetahuan) harus jujur berlabel 'chat' —
    # kalau tidak, menu Pengetahuan AI menampilkannya sebagai "diketik admin"
    # padahal isinya sudah berasal dari percakapan. Endpoint PATCH admin tidak
    # ikut terbuka: body-nya dibatasi model PengetahuanPatch.
    boleh = {"judul", "deskripsi", "tag", "untuk_pembeli", "aktif", "pakai_ai",
             "status", "progres", "jumlah_chunk", "pengayaan", "error", "berkas",
             "teks_admin", "tabel_admin", "asal"}
    upd = {k: v for k, v in fields.items() if k in boleh}
    with _lock:
        rows = load_dokumen()
        i = next((n for n, d in enumerate(rows) if d.get("id") == dok_id), -1)
        if i < 0:
            raise KeyError("Dokumen tidak ditemukan.")
        if "judul" in upd:
            upd["judul"] = (upd["judul"] or "").strip()
            if not upd["judul"]:
                raise ValueError("Judul wajib diisi.")
        if "tag" in upd:
            upd["tag"] = clean_list(upd["tag"], 12)
        rows[i].update(upd)
        _save_dokumen(rows)
        d = rows[i]
    if "untuk_pembeli" in upd or "judul" in upd:
        _sync_chunks_dari_dokumen(dok_id, d)
    return d


def _sync_chunks_dari_dokumen(dok_id: str, d: dict) -> None:
    with _lock:
        rows = load_chunks()
        ubah = False
        for c in rows:
            if c.get("dok_id") != dok_id:
                continue
            if c.get("untuk_pembeli") != bool(d.get("untuk_pembeli")):
                c["untuk_pembeli"] = bool(d.get("untuk_pembeli"))
                ubah = True
            if c.get("judul") != d.get("judul"):
                c["judul"] = d.get("judul")
                ubah = True
        if ubah:
            _save_chunks(rows)


def set_status(dok_id: str, status: str = "", progres: dict | None = None,
               **fields) -> None:
    """Update ringan dipanggil job saat berjalan — tak melempar bila dokumen
    sudah dihapus di tengah jalan (admin boleh membatalkan)."""
    upd = dict(fields)
    if status:
        upd["status"] = status
    if progres is not None:
        upd["progres"] = progres
    try:
        update_dokumen(dok_id, **upd)
    except KeyError:
        pass


def delete_dokumen(dok_id: str) -> dict:
    """Hapus dokumen + seluruh chunk + media + berkas aslinya."""
    with _lock:
        rows = load_dokumen()
        i = next((n for n, d in enumerate(rows) if d.get("id") == dok_id), -1)
        if i < 0:
            raise KeyError("Dokumen tidak ditemukan.")
        gone = rows.pop(i)
        _save_dokumen(rows)
        sisa = [c for c in load_chunks() if c.get("dok_id") != dok_id]
        _save_chunks(sisa)
    for d in (media_dir(), berkas_dir()):
        try:
            for p in d.glob(f"{dok_id}_*"):
                try:
                    p.unlink()
                except OSError:
                    pass
        except OSError:
            pass
    return gone


# ── CRUD chunk ───────────────────────────────────────────────────────
def chunks_dokumen(dok_id: str) -> list[dict]:
    return [c for c in chunks() if c.get("dok_id") == dok_id]


def replace_chunks(dok_id: str, baru: list[dict]) -> int:
    """Ganti SELURUH chunk sebuah dokumen sekaligus (hasil index/re-index).
    Chunk lama tetap tersaji sampai detik penulisan — tak ada jendela kosong."""
    with _lock:
        rows = [c for c in load_chunks() if c.get("dok_id") != dok_id]
        rows.extend(baru)
        _save_chunks(rows)
    return len(baru)


def sapu_media(dok_id: str) -> int:
    """Hapus berkas media milik dokumen ini yang tak lagi dirujuk chunk mana pun.

    Re-index menomori ulang gambar (`{dok_id}_{n:03d}.png`), jadi tanpa sapuan
    ini file lama menumpuk di bind-mount sampai penuh. Aman dipanggil setelah
    `replace_chunks` karena penulisannya atomik — tak ada jendela referensi
    menggantung.
    """
    dipakai = {f for c in chunks_dokumen(dok_id) for f in (c.get("gambar_ref") or [])}
    n = 0
    try:
        for p in media_dir().glob(f"{dok_id}_*"):
            if p.name in dipakai:
                continue
            try:
                p.unlink()
                n += 1
            except OSError:
                pass
    except OSError:
        pass
    return n


def perlu_reindex(dok_id: str) -> bool:
    """True bila dokumen masih berskema lama — belum punya gambar embedded,
    breadcrumb, atau metadata kolom hasil ekstraksi V2."""
    isi = chunks_dokumen(dok_id)
    return bool(isi) and any(c.get("skema", 1) < 2 for c in isi)


def update_chunk(cid: str, judul_id=None, kata_kunci=None, dicari=None) -> dict:
    """Kurasi manual per-chunk oleh admin — jalur perbaikan kualitas utama
    ketika pengayaan LLM meleset atau tidak dipakai.

    Menandai `kurasi=True` supaya re-index memulihkannya kembali dan pengayaan
    LLM tidak menimpanya (keputusan manusia menang atas tebakan model).
    """
    with _lock:
        rows = load_chunks()
        i = next((n for n, c in enumerate(rows) if c.get("id") == cid), -1)
        if i < 0:
            raise KeyError("Bagian tidak ditemukan.")
        if judul_id is not None:
            rows[i]["judul_id"] = str(judul_id).strip()[:80]
        if kata_kunci is not None:
            rows[i]["kata_kunci"] = clean_list(kata_kunci, 8)
        if dicari is not None:
            rows[i]["dicari"] = bool(dicari)
        rows[i]["kurasi"] = True
        _save_chunks(rows)
        return rows[i]


def kunci_kurasi(c: dict) -> str:
    """Kunci pencocokan chunk lama ↔ baru saat re-index.

    SENGAJA bukan `id`: `dok#seq` bergeser begitu jumlah bagian berubah (mis.
    V2 mengekstrak gambar sehingga bagian bertambah), sehingga kurasi akan
    nyasar ke bagian yang salah. Kombinasi sumber+halaman+awal teks jauh lebih
    stabil terhadap perubahan cara ekstraksi.
    """
    teks = re.sub(r"\s+", " ", (c.get("teks") or "")).strip().lower()[:80]
    return f"{c.get('sumber') or ''}|{c.get('halaman') or 0}|{teks}"


def snapshot_kurasi(dok_id: str) -> dict[str, dict]:
    """Kurasi manual dokumen ini, siap dipulihkan setelah re-index."""
    out: dict[str, dict] = {}
    for c in chunks_dokumen(dok_id):
        if not c.get("kurasi"):
            continue
        out[kunci_kurasi(c)] = {
            "judul_id": c.get("judul_id") or "",
            "kata_kunci": list(c.get("kata_kunci") or []),
            "dicari": bool(c.get("dicari", True)),
        }
    return out


def terapkan_kurasi(baru: list[dict], simpan: dict[str, dict]) -> int:
    """Pulihkan kurasi ke chunk hasil ekstraksi ulang. Return jumlah kurasi
    yang TIDAK bisa dipulihkan (isi berkasnya berubah) — pemanggil wajib
    melaporkannya ke admin, jangan hilang diam-diam."""
    if not simpan:
        return 0
    kena: set[str] = set()
    for c in baru:
        k = kunci_kurasi(c)
        sim = simpan.get(k)
        if not sim:
            continue
        c["judul_id"] = sim["judul_id"] or c.get("judul_id") or ""
        if sim["kata_kunci"]:
            c["kata_kunci"] = list(sim["kata_kunci"])
        c["dicari"] = sim["dicari"]
        c["kurasi"] = True
        kena.add(k)
    return len(set(simpan) - kena)


# ── pencarian ────────────────────────────────────────────────────────
# Aksara CJK (Han + kana). Ditangani terpisah karena TIDAK berspasi: pemecah
# kata Latin menghasilkan nol token untuk kueri Mandarin, dan batas-kata regex
# tak punya arti di sana.
_CJK_RE = re.compile(r"[㐀-䶿一-鿿豈-﫿぀-ヿ]+")
_MAX_TERM_CJK = 12


# ── stemming Indonesia ringan ────────────────────────────────────────
# Dipakai HANYA sebagai fallback per-kata saat kecocokan eksak gagal, dengan
# bobot di bawah kecocokan teks eksak — jadi salah-stem paling banter menambah
# hasil lemah, tak pernah menggeser kecocokan eksak. Kedua sisi (indeks & kueri)
# distem dengan algoritme yang SAMA, sehingga konsistensi lebih penting daripada
# ketepatan linguistik.
_NASAL = {"meng": "k", "meny": "s", "men": "t", "mem": "p",
          "peng": "k", "peny": "s", "pen": "t", "pem": "p"}
_PREFIKS = ("meng", "meny", "men", "mem", "me", "peng", "peny", "pen", "pem",
            "pe", "ber", "ter", "di", "ke", "se")
_SUFIKS = ("lah", "kah", "pun", "nya", "kan", "an", "i")


def _stem_kandidat(w: str) -> tuple[str, ...]:
    """Kandidat akar kata: buang sufiks umum lalu prefiks; prefiks nasal
    (mem-/men-/meng-/meny- dst.) juga melahirkan varian ber-huruf-pulih
    ('memasang' → asang + pasang) supaya ketemu dgn 'pasang' polos."""
    if len(w) < 5 or any(c.isdigit() for c in w):
        return ()
    kandidat: set[str] = set()
    s = w
    for suf in _SUFIKS:
        if s.endswith(suf) and len(s) - len(suf) >= 4:
            s = s[: -len(suf)]
            kandidat.add(s)
            break
    for pre in _PREFIKS:
        if s.startswith(pre) and len(s) - len(pre) >= 3:
            akar = s[len(pre):]
            kandidat.add(akar)
            pulih = _NASAL.get(pre)
            if pulih:
                kandidat.add(pulih + akar)
            break
    kandidat.discard(w)
    return tuple(kandidat)


_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _stem_set(gabung: str) -> frozenset[str]:
    """Token + seluruh kandidat stemnya untuk SATU chunk. Dihitung sekali per
    penulisan store (ikut cache _hay_semua), bukan per kueri."""
    out: set[str] = set()
    for t in _TOKEN_RE.findall(gabung):
        if len(t) < 4:
            continue
        out.add(t)
        out.update(_stem_kandidat(t))
    return frozenset(out)


def _stems_kueri(words: list[str]) -> dict[str, tuple[str, ...]]:
    """Per kata kueri → (kata, *kandidat stem) untuk dicek ke stemset chunk."""
    return {w: (w, *_stem_kandidat(w)) for w in words}


# Token PN/kode di kueri: runtun huruf-angka BERPEMISAH ('WG-9725.190102') yang
# oleh pemecah kata Latin tercerai jadi pecahan pendek tak berarti. Digabung
# ulang tanpa pemisah supaya cocok dgn `kode` chunk yang juga dinormalkan.
_SEP_RE = re.compile(r"[-./]")
_PN_RUN_RE = re.compile(r"[a-z0-9][a-z0-9\-./]{4,}[a-z0-9]")
_MIN_KODE_NORM = 6


def _kode_norm(kode: list) -> str:
    """Kode chunk tanpa pemisah, satu string siap-substring ('wg9725190102')."""
    return " ".join(_SEP_RE.sub("", str(k)).lower() for k in (kode or []))


# Pola batas-kata per term, dikompilasi SEKALI. `search()` memanggil _hit
# ~(jumlah chunk × 5 ladang × jumlah kata) kali; merakit ulang string pola di
# tiap panggilan adalah biaya dominan pencarian (terukur: 368 ms → 34 ms untuk
# kueri 3 kata atas 5.000 chunk).
_PAT_CACHE: dict[str, re.Pattern] = {}


def _pola(term: str) -> re.Pattern:
    p = _PAT_CACHE.get(term)
    if p is None:
        if len(_PAT_CACHE) > 4000:      # pagar memori; kueri unik tak terbatas
            _PAT_CACHE.clear()
        p = re.compile(r"(?<![a-z0-9])" + re.escape(term) + r"(?![a-z0-9])")
        _PAT_CACHE[term] = p
    return p


def _hit(term: str, hay: str) -> bool:
    # CJK: substring polos SUDAH benar (tak ada batas kata) dan lebih murah.
    # Perilaku term Latin sengaja tidak diubah sedikit pun.
    if _CJK_RE.search(term):
        return term in hay
    # Gerbang murah: cocok batas-kata MUSTAHIL tanpa cocok substring, jadi
    # menolak di sini identik hasilnya tapi menghindari mesin regex.
    if term not in hay:
        return False
    return _pola(term).search(hay) is not None


def _hay(r: dict) -> tuple:
    """Ladang jerami siap-cocok untuk satu chunk (semuanya lowercase):
    (idn, judul, ringkas, kode, teks, gabung, stemset, kode_norm).

    Dihitung SEKALI per penulisan store lewat `_hay_semua`, bukan per kueri:
    dulu `_score` merakit ulang gabungan tabel + beberapa .lower() untuk tiap
    chunk × tiap varian sinonim — puluhan ribu pembangunan string per kueri.
    """
    idn = (f"{r.get('judul_id','')} {' '.join(r.get('kata_kunci') or [])}").lower()
    judul = (r.get("judul") or "").lower()
    # Caption gambar & nama kolom tabel = sinyal padat → setara ringkasan, tapi
    # sengaja TIDAK setara judul_id supaya kurasi manusia tetap menang.
    ringkas = (f"{r.get('ringkasan','')} {r.get('gambar_teks','')} "
               f"{' '.join(r.get('kolom') or [])}").lower()
    kode = " ".join(r.get("kode") or []).lower()
    tabel = " ".join(" ".join(str(c) for c in (baris or []))
                     for baris in (r.get("tabel") or []))
    teks = f"{r.get('teks','')} {tabel} {' '.join(r.get('jalur') or [])}".lower()
    # Elemen ke-6 = gabungan semua ladang, dipakai sebagai penyaring murah:
    # `in` (level-C) jauh lebih cepat dari regex, dan kecocokan batas-kata
    # selalu mensyaratkan kecocokan substring — jadi tak ada hasil yang hilang,
    # hanya chunk yang mustahil cocok yang dilewati lebih awal.
    # Elemen ke-7/8 = stemset (fallback morfologi Indonesia) & kode ternormal
    # (PN berpemisah) — keduanya prakomputasi supaya per-kueri tinggal lookup.
    gabung = f"{idn} {judul} {ringkas} {kode} {teks}"
    return (idn, judul, ringkas, kode, teks, gabung,
            _stem_set(gabung), _kode_norm(r.get("kode") or []))


_HAY_CACHE: dict = {"mtime": None, "rows": None}


def _hay_semua(rows: list[dict]) -> list[tuple]:
    """Haystack seluruh store, di-cache dengan sinyal mtime yang sama dengan
    `knowledge_util.load_json` sehingga otomatis basi saat store ditulis."""
    p = _chunk_file()
    try:
        mt = p.stat().st_mtime if p.exists() else None
    except OSError:
        mt = None
    ent = _HAY_CACHE
    if ent["mtime"] != mt or ent["rows"] is None or len(ent["rows"]) != len(rows):
        ent["rows"] = [_hay(r) for r in rows]
        ent["mtime"] = mt
    return ent["rows"]


# Bobot fallback stem: DI BAWAH kecocokan teks eksak (1) supaya morfologi tak
# pernah menggeser kecocokan yang benar-benar diketik user.
_BOBOT_STEM = 0.7


def _score_hay(hay: tuple, ql: str, words: list[str], frasa_ok: bool,
               stems: dict[str, tuple[str, ...]] | None = None) -> float:
    """Peringkat: judul_id/kata_kunci (kurasi Indonesia) > judul dokumen > kode >
    ringkasan > teks/tabel. Token ber-ANGKA (PN, kode error) ×3 karena paling
    diskriminatif; frasa penuh cocok → bonus besar. Pola manual_teks._score.

    Dua fallback per-kata (hanya bila SEMUA kecocokan eksak gagal):
    - kode ternormal: 'wg9725190102' / prefiks ≥6 char cocok substring ke
      `kode` chunk tanpa pemisah — setara bobot ladang kode;
    - stem Indonesia: 'pemasangan' ↔ 'pasang' — bobot di bawah teks eksak.
    """
    idn, judul, ringkas, kode, teks = hay[:5]
    stemset = hay[6] if len(hay) > 6 else frozenset()
    kodenorm = hay[7] if len(hay) > 7 else ""
    s = 0.0
    if ql and (_hit(ql, idn) or _hit(ql, judul)):
        s += 50
    elif ql and frasa_ok and (_hit(ql, ringkas) or _hit(ql, teks)):
        # Frasa utuh yang muncul di BADAN teks — inilah yang membuat dokumen
        # berbahasa asing tetap bisa dicari setelah judulnya dikurasi ke
        # Indonesia. `elif` menjaga hierarki: kurasi judul selalu di atas ini.
        s += 20
    for w in words:
        berangka = any(c.isdigit() for c in w)
        spec = 3 if berangka else 1
        if _hit(w, idn):
            s += 5 * spec
        elif _hit(w, judul):
            s += 3 * spec
        elif _hit(w, kode):
            s += 4 * spec
        elif (berangka and kodenorm and len(w) >= _MIN_KODE_NORM
              and w in kodenorm):
            s += 4 * spec
        elif _hit(w, ringkas):
            s += 2 * spec
        elif _hit(w, teks):
            s += 1 * spec
        elif stems and stemset:
            sk = stems.get(w)
            if sk and any(k in stemset for k in sk):
                s += _BOBOT_STEM * spec
    return s


def _score(r: dict, ql: str, words: list[str]) -> float:
    """Skor satu chunk mentah — dipertahankan untuk test & pemakaian ad-hoc."""
    return _score_hay(_hay(r), ql, words, _frasa_layak(ql), _stems_kueri(words))


def _words(ql: str) -> list[str]:
    """Token pencarian. Jalur Latin SENGAJA tak diubah (nol regresi peringkat).

    Tambahan CJK: tiap runtun aksara dipecah jadi BIGRAM bertumpang-tindih
    (`退货流程` → 退货, 货流, 流程). Bigram, bukan unigram: satu aksara Han
    terlalu ambigu dan akan mencocoki hampir semua dokumen sehingga ambang
    relatif jadi tak berguna.
    """
    out = [w for w in re.findall(r"[a-z0-9]+", ql) if len(w) >= 2]
    # Runtun PN berpemisah ('wg-9725.190102') digabung ulang tanpa pemisah jadi
    # token tambahan — pemecah di atas mencerainya jadi pecahan yang tak akan
    # pernah cocok dengan kode utuh di indeks.
    for run in _PN_RUN_RE.findall(ql):
        if "-" not in run and "." not in run and "/" not in run:
            continue
        norm = _SEP_RE.sub("", run)
        if (len(norm) >= _MIN_KODE_NORM and any(c.isdigit() for c in norm)
                and norm not in out):
            out.append(norm)
    for run in _CJK_RE.findall(ql):
        if len(run) == 1:
            out.append(run)
        else:
            out.extend(run[i:i + 2] for i in range(len(run) - 1))
        if len(out) >= _MAX_TERM_CJK:
            break
    return out[:_MAX_TERM_CJK] if len(out) > _MAX_TERM_CJK else out


def _frasa_layak(ql: str) -> bool:
    """Bonus frasa-di-badan-teks hanya untuk kueri yang cukup spesifik.

    Kueri SATU kata Latin sengaja dikecualikan: kecocokannya di `teks` sudah
    dihitung +1 di loop kata, dan memberinya +20 akan mengacak peringkat lama
    (chunk yang menyebut kata itu sekali bisa melompati chunk yang judulnya
    memang tepat). Dengan pagar ini kueri satu-kata berperilaku identik V1.
    """
    if len(re.findall(r"[a-z0-9]+", ql)) >= 2:
        return True
    return sum(len(r) for r in _CJK_RE.findall(ql)) >= 2


# Skor istilah hasil ekspansi sinonim diredam supaya sinonim tidak pernah
# menggeser kecocokan LANGSUNG ke kata yang benar-benar diketik user.
_BOBOT_SINONIM = 0.6

# Kueri hasil koreksi typo ikut dinilai tapi diredam — kueri asli user (siapa
# tahu memang benar) selalu menang bila dua-duanya cocok.
_BOBOT_TIPO = 0.8


# ── koreksi typo (kueri → kosakata store) ────────────────────────────
# Mekanik mengetik cepat di HP: 'slenoid', 'kompresso'. Kata kueri yang TIDAK
# ada di kosakata store dicarikan tetangga terdekatnya (jarak edit ≤1, kata
# panjang ≤2) lalu seluruh kueri diulang sebagai varian teredam. Kosakata
# di-cache per-mtime seperti haystack. Batasan sadar: huruf PERTAMA dianggap
# benar (bucket per huruf awal) — menekan biaya scan dan salah-koreksi.
_VOCAB_CACHE: dict = {"kunci": None, "ember": None}
_ALPHA_RE = re.compile(r"[a-z]{4,}")


def _vocab_ember(hays: list[tuple]) -> dict[str, set[str]]:
    p = _chunk_file()
    try:
        mt = p.stat().st_mtime if p.exists() else None
    except OSError:
        mt = None
    kunci = (str(p), mt, len(hays))
    ent = _VOCAB_CACHE
    if ent["kunci"] != kunci or ent["ember"] is None:
        ember: dict[str, set[str]] = {}
        for hay in hays:
            for t in _ALPHA_RE.findall(hay[5]):
                ember.setdefault(t[0], set()).add(t)
        ent["ember"] = ember
        ent["kunci"] = kunci
    return ent["ember"]


# Dulu SALINAN byte-identik dari search_boost._jarak_edit. Satu sumber saja
# sekarang (rapidfuzz C++ di balik nama yang sama) — dua salinan berarti dua
# tempat yang harus diperbaiki setiap kali aturan koreksi tipo berubah.
_jarak_edit = search_boost._jarak_edit


def _koreksi_tipo(ql: str, hays: list[tuple]) -> str:
    """Kueri dgn kata-tak-dikenal diganti tetangga terdekat di kosakata store;
    '' bila tak ada yang perlu/bisa dikoreksi."""
    kata = [w for w in re.findall(r"(?<![a-z0-9])[a-z]{5,}(?![a-z0-9])", ql)]
    if not kata or not hays:
        return ""
    ember = _vocab_ember(hays)
    if not ember:
        return ""
    ganti: dict[str, str] = {}
    for w in dict.fromkeys(kata):
        kandidat = ember.get(w[0])
        if not kandidat or w in kandidat:
            continue
        maks = 1 if len(w) < 8 else 2
        best, best_d = "", maks + 1
        for k in kandidat:
            d = _jarak_edit(w, k, maks)
            if d < best_d:
                best, best_d = k, d
                if d == 1:
                    break
        if best:
            ganti[w] = best
    if not ganti:
        return ""
    out = ql
    for a, b in ganti.items():
        out = re.sub(rf"(?<![a-z0-9]){re.escape(a)}(?![a-z0-9])", b, out)
    return out if out != ql else ""

# Ambang relatif: hasil yang skornya jauh di bawah juara BUKAN jawaban, hanya
# kebetulan berbagi satu kata umum. Membuangnya menaikkan akurasi (model tak
# terdistraksi isi yang tak relevan) SEKALIGUS menghemat token — chunk lemah
# tak ikut diserialisasi ke messages.
_AMBANG_RELATIF = 0.30

# Pagar diversitas hasil: maksimal sekian chunk per dokumen di lintasan pertama
# seleksi — dokumen tebal (400 chunk) tak boleh menenggelamkan dokumen lain
# yang relevan. Kursi sisa tetap diisi ulang, jadi `limit` tak pernah dikorbankan.
_MAX_PER_DOK = 2

# Kata fungsi & kata tanya yang cocok di hampir semua chunk — menyumbang skor
# tanpa membedakan apa pun ("bagaimana cara ganti filter": 'bagaimana'/'cara'
# menaikkan chunk yang salah). Dibuang HANYA dari daftar kata skor; frasa kueri
# utuh tetap dinilai, dan `_words()` sendiri tidak diubah. Daftar SENGAJA
# pendek & konservatif: salah-buang kata bermakna lebih mahal daripada
# membiarkan satu-dua kata umum lolos.
_STOP_SKOR = frozenset({
    "yang", "dan", "untuk", "dari", "dengan", "pada", "atau", "ini", "itu",
    "apa", "apakah", "bagaimana", "berapa", "kenapa", "mengapa", "cara",
    "adalah", "bisa", "kapan", "dimana", "saat", "jika", "bila",
})


def kata_kueri(topik: str) -> list[str]:
    """Kata kueri siap-skor/sorot: token `_words` minus stopword skor. Dipakai
    internal `search_skor` dan oleh tool untuk memilih jendela isi & baris
    tabel yang relevan."""
    ql = (topik or "").strip().lower()
    return [w for w in _words(ql) if w not in _STOP_SKOR]


# ── pemilihan isi relevan (dipakai tool penyaji) ─────────────────────
def potong_relevan(teks: str, words: list[str], batas: int) -> str:
    """Jendela ≤`batas` char yang memuat kecocokan kata-kueri BERBEDA terbanyak
    — pengganti `teks[:batas]` yang buta posisi: kalimat penjawab di char 1500
    dulu terpotong senyap padahal 1200 char kepala tetap terbayar.

    Tie → jendela paling awal. Jendela yang tak mulai dari awal diberi prefiks
    '…' dan dirapikan ke batas kata. Nol kecocokan eksak (mis. chunk lolos via
    stem/typo) → kepala teks, perilaku lama."""
    s = (teks or "").strip()
    if len(s) <= batas:
        return s
    hay = s.lower()
    posisi: list[tuple[int, int]] = []          # (pos, idx_kata)
    for wi, w in enumerate(words or []):
        if not w:
            continue
        if _CJK_RE.search(w):
            p = hay.find(w)
            while p >= 0 and len(posisi) <= 200:
                posisi.append((p, wi))
                p = hay.find(w, p + 1)
        else:
            for m in _pola(w).finditer(hay):
                posisi.append((m.start(), wi))
                if len(posisi) > 200:
                    break
    if not posisi:
        return s[:batas]
    posisi.sort()
    # Kandidat jendela: berawal sedikit SEBELUM tiap posisi kecocokan supaya
    # kalimatnya ikut terbawa. n posisi ≤ 200 → kuadratik kecil, murah.
    efektif = batas - 1                          # sisakan 1 char untuk '…'
    terbaik_n, terbaik_mulai = -1, 0
    for p, _ in posisi:
        mulai = max(0, p - 80)
        akhir = mulai + (batas if mulai == 0 else efektif)
        n = len({wi for q, wi in posisi if mulai <= q < akhir})
        if n > terbaik_n:
            terbaik_n, terbaik_mulai = n, mulai
    if terbaik_mulai == 0:
        return s[:batas]
    potong = s[terbaik_mulai:terbaik_mulai + efektif]
    # Rapikan kepala ke batas kata (buang pecahan kata pertama).
    sp = potong.find(" ", 0, 30)
    if sp > 0:
        potong = potong[sp + 1:]
    return "…" + potong


def baris_relevan(tabel: list, words: list[str], maks: int = 8) -> tuple[list, int]:
    """Baris tabel yang memuat kata kueri DIDAHULUKAN (header selalu ikut),
    kursi sisa diisi baris awal — pengganti `tabel[:maks]` yang buta: baris
    penjawab di indeks 25 dulu tak pernah terlihat model.

    Return (baris ≤ maks termasuk header, jumlah baris cocok). Urutan asli
    baris dipertahankan (di-sort ulang) supaya konteks antarbaris tak teracak."""
    rows = tabel or []
    if len(rows) <= maks:
        return rows, 0
    header, body = rows[0], rows[1:]
    kursi = maks - 1
    cocok: list[int] = []
    if words:
        for i, b in enumerate(body):
            hay = " ".join(str(c) for c in b).lower()
            if any(w in hay for w in words):
                cocok.append(i)
                if len(cocok) >= kursi:
                    break
    if not cocok:
        return rows[:maks], 0
    pilih = list(cocok)
    for i in range(len(body)):
        if len(pilih) >= kursi:
            break
        if i not in cocok:
            pilih.append(i)
    pilih.sort()
    return [header, *(body[i] for i in pilih)], len(cocok)


def _mirip(a: str, b: str) -> bool:
    """Dua chunk dianggap kembar bila salah satu memuat 120 char awal yang lain.
    Chunking memakai overlap 120 char, jadi potongan bersebelahan bisa muncul
    berdua untuk kueri yang sama — mengirim keduanya ke model = token terbuang
    untuk kalimat yang sama."""
    a, b = (a or "").strip(), (b or "").strip()
    if not a or not b:
        return False
    pendek, panjang = (a, b) if len(a) <= len(b) else (b, a)
    return pendek[:120] in panjang


def search(topik: str = "", limit: int = 5, untuk_pembeli: bool = False) -> list[dict]:
    """Cari chunk aktif (`dicari=True`, dokumen `aktif`), diranking relevansi.

    `untuk_pembeli=True` (role pembeli) → HANYA chunk yang sengaja dipublikasikan
    admin. Query kosong → []. Query diperluas kamus sinonim istilah lapangan.
    """
    return [r for _, r in search_skor(topik, limit, untuk_pembeli)]


def search_skor(topik: str = "", limit: int = 5,
                untuk_pembeli: bool = False) -> list[tuple[float, dict]]:
    """Seperti `search()` tapi mengembalikan (skor, chunk) — skor TIDAK dibuang
    di ambang modul: tool penyaji memakainya untuk bertingkat (juara kutipan
    penuh, pendukung lemah cukup judul) dan mendeteksi kecocokan lemah."""
    ql = (topik or "").strip().lower()
    if not ql:
        return []
    mati = {d.get("id") for d in dokumen() if not d.get("aktif", True)}
    rows = chunks()
    hays = _hay_semua(rows)
    try:
        terms, _ = sinonim.expand_query(ql)
    except Exception:
        terms = [ql]
    daftar = [(t, 1.0 if i == 0 else _BOBOT_SINONIM)
              for i, t in enumerate(terms) if (t or "").strip()]
    # Koreksi typo terhadap kosakata store — kueri terkoreksi jadi varian
    # teredam TAMBAHAN; kueri asli tetap dinilai penuh.
    try:
        koreksi = _koreksi_tipo(ql, hays)
    except Exception:
        koreksi = ""
    if koreksi:
        daftar.append((koreksi, _BOBOT_TIPO))
    # Token, kelayakan-frasa & stem dihitung SEKALI per varian, bukan per chunk.
    # Stopword skor dibuang dari daftar KATA saja — frasa `tl` utuh tetap
    # dinilai, jadi judul kurasi "Cara mengajukan retur" tetap bisa cocok frasa.
    varian = []
    for t, b in daftar:
        tl = t.strip().lower()
        w = [x for x in _words(tl) if x not in _STOP_SKOR]
        stems = _stems_kueri(w)
        qstem = frozenset(k for sk in stems.values() for k in sk)
        varian.append((tl, w, _frasa_layak(tl), b, stems, qstem))
    scored: list[tuple[float, int, dict]] = []
    for i, r in enumerate(rows):
        if not r.get("dicari", True):
            continue
        if r.get("dok_id") in mati:
            continue
        if untuk_pembeli and not r.get("untuk_pembeli"):
            continue
        hay = hays[i]
        gabung = hay[5]
        stemset = hay[6] if len(hay) > 6 else frozenset()
        sc = 0.0
        for t, w, f, b, stems, qstem in varian:
            # Tolak murah dulu: chunk yang tak memuat satu pun kata kueri
            # sebagai substring — dan tak beririsan stem — mustahil cocok.
            if w and not any(x in gabung for x in w) \
                    and (not stemset or not (qstem & stemset)):
                continue
            v = _score_hay(hay, t, w, f, stems) * b
            if v > sc:
                sc = v
        if sc > 0:
            scored.append((sc, i, r))  # i = tie-break stabil
    if not scored:
        return []
    scored.sort(key=lambda t: (-t[0], t[1]))
    lantai = scored[0][0] * _AMBANG_RELATIF
    # Seleksi dua-lintasan dgn pagar diversitas: satu dokumen besar tak boleh
    # memonopoli seluruh hasil — dokumen lain yang relevan ikut tampil. Kursi
    # yang masih kosong setelah lintasan pertama diisi lagi dari chunk yang
    # tadinya tertahan pagar (limit selalu terpenuhi bila kandidatnya ada).
    kandidat: list[tuple[float, dict]] = []
    for sc, _, r in scored:
        if sc < lantai:
            break                      # sisanya makin lemah — berhenti
        kandidat.append((sc, r))
    out: list[tuple[float, dict]] = []
    per_dok: dict[str, int] = {}
    tertahan: list[tuple[float, dict]] = []
    for sc, r in kandidat:
        if any(_mirip(r.get("teks") or "", p.get("teks") or "") for _, p in out):
            continue                   # kembar akibat overlap chunking
        dok = r.get("dok_id") or ""
        if per_dok.get(dok, 0) >= _MAX_PER_DOK:
            tertahan.append((sc, r))
            continue
        out.append((sc, r))
        per_dok[dok] = per_dok.get(dok, 0) + 1
        if len(out) >= limit:
            return out
    for sc, r in tertahan:
        if any(_mirip(r.get("teks") or "", p.get("teks") or "") for _, p in out):
            continue
        out.append((sc, r))
        if len(out) >= limit:
            break
    return out


# ── pembacaan satu bagian (tool buka_pengetahuan) ────────────────────
def _norm(s: str) -> str:
    """Normalisasi longgar untuk mencocokkan judul yang diketik ulang model."""
    return re.sub(r"[^a-z0-9一-鿿]+", " ", (s or "").lower()).strip()


def cocokkan(kandidat: list[str], nama: str) -> tuple[str, list[str]]:
    """Cocokkan nama manusiawi ke daftar kandidat, bertingkat: persis →
    case-insensitive → normalisasi → substring UNIK.

    Return (terpilih, ambigu). Ambigu (≥2 kandidat setara) TIDAK ditebak —
    pemanggil mengembalikan daftarnya agar model memilih, pola
    `ai_sheet.select_sheet`. Tak ada ID opaque di mana pun.
    """
    n = (nama or "").strip()
    if not n:
        return "", []
    if n in kandidat:
        return n, []
    low = [c for c in kandidat if c.lower() == n.lower()]
    if len(low) == 1:
        return low[0], []
    nn = _norm(n)
    norm = [c for c in kandidat if _norm(c) == nn]
    if len(norm) == 1:
        return norm[0], []
    if norm:
        return "", norm
    sub = [c for c in kandidat if nn and nn in _norm(c)]
    if len(sub) == 1:
        return sub[0], []
    return "", sub


def _bisa_dibaca(c: dict, mati: set, untuk_pembeli: bool) -> bool:
    if c.get("dok_id") in mati or not c.get("dicari", True):
        return False
    return not (untuk_pembeli and not c.get("untuk_pembeli"))


def buka(dokumen: str, bagian: str = "", halaman: int = 0,
         untuk_pembeli: bool = False) -> dict:
    """Satu bagian dokumen secara UTUH + daftar bagian tetangga.

    Filter `untuk_pembeli` diterapkan SEBELUM pencocokan judul supaya judul
    bagian internal tak bocor lewat pesan error sekalipun.
    """
    mati = {d.get("id") for d in dokumen_nonaktif()}
    semua = [c for c in chunks() if _bisa_dibaca(c, mati, untuk_pembeli)]
    if not semua:
        return {"found": False, "dokumen_tersedia": [],
                "catatan": "Belum ada pengetahuan internal yang bisa dibuka."}

    judul_dok = list(dict.fromkeys(c.get("judul") or "" for c in semua if c.get("judul")))
    pilih, ambigu = cocokkan(judul_dok, dokumen)
    if not pilih:
        return {"found": False, "dokumen_tersedia": (ambigu or judul_dok)[:30],
                "catatan": (f"Dokumen '{dokumen}' tidak dikenali. Pilih SATU judul "
                            "PERSIS dari 'dokumen_tersedia' — jangan mengarang judul lain.")}

    isi_dok = [c for c in semua if (c.get("judul") or "") == pilih]
    daftar = [{"judul": c.get("judul_id") or c.get("judul"),
               "halaman": c.get("halaman") or 0} for c in isi_dok][:30]

    target = None
    if halaman and not bagian:
        target = next((c for c in isi_dok if (c.get("halaman") or 0) == int(halaman)), None)
    if target is None and bagian:
        judul_bag = [c.get("judul_id") or c.get("judul") or "" for c in isi_dok]
        pb, amb = cocokkan(judul_bag, bagian)
        if not pb:
            return {"found": False, "dokumen": pilih,
                    "bagian_tersedia": [d for d in daftar
                                        if not amb or d["judul"] in amb],
                    "catatan": (f"Bagian '{bagian}' tidak ada di dokumen '{pilih}'. "
                                "Pilih SATU judul PERSIS dari 'bagian_tersedia' — "
                                "jangan menebak judul lain.")}
        target = next(c for c in isi_dok
                      if (c.get("judul_id") or c.get("judul") or "") == pb)
    if target is None and not bagian and not halaman:
        # Tanpa `bagian` → daftar isi dokumen (murah, dan mengajari model judul
        # yang sah untuk panggilan berikutnya).
        return {"found": True, "dokumen": pilih, "daftar_isi": daftar,
                "jumlah_bagian": len(isi_dok),
                "catatan": ("Daftar isi dokumen. Panggil lagi dengan 'bagian' = SATU "
                            "judul PERSIS dari daftar ini untuk membaca isinya.")}
    if target is None:
        return {"found": False, "dokumen": pilih, "bagian_tersedia": daftar,
                "catatan": (f"Tidak ada bagian pada halaman {halaman} di '{pilih}'. "
                            "Pilih dari 'bagian_tersedia'.")}
    return {"_target": target, "_dok": pilih, "_daftar": daftar, "_isi_dok": isi_dok}


def jahit_tabel(isi_dok: list[dict], target: dict, maks: int = 60) -> tuple[list, int]:
    """Satukan potongan tabel yang bersaudara (sumber+jalur+kolom sama) jadi
    satu tabel utuh. Tabel panjang dipecah 30 baris/chunk saat indexing, jadi
    tanpa penjahitan ini asisten tak pernah bisa membaca tabel besar."""
    kolom = target.get("kolom") or []
    if not target.get("tabel"):
        return [], 0
    if not kolom:
        return target["tabel"][:maks + 1], target.get("baris_total") or 0
    saudara = [c for c in isi_dok
               if (c.get("kolom") or []) == kolom
               and c.get("sumber") == target.get("sumber")
               and (c.get("jalur") or []) == (target.get("jalur") or [])]
    saudara.sort(key=lambda c: c.get("baris_dari") or 0)
    header = target["tabel"][0] if target["tabel"] else kolom
    baris: list = []
    for c in saudara:
        baris.extend((c.get("tabel") or [])[1:])
        if len(baris) >= maks:
            break
    total = target.get("baris_total") or len(baris)
    return [header, *baris[:maks]], total


# ── gambar ───────────────────────────────────────────────────────────
def image_bytes(fname: str) -> bytes | None:
    """Bytes gambar — nama divalidasi ketat + WAJIB terdaftar di gambar_ref
    salah satu chunk (anti path-traversal). Pola manual_media.image_bytes."""
    f = (fname or "").strip()
    if not _FNAME_RE.match(f):
        return None
    if not any(f in (c.get("gambar_ref") or []) for c in chunks()):
        return None
    p = media_dir() / f
    try:
        return p.read_bytes() if p.is_file() else None
    except OSError:
        return None
