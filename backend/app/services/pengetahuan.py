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
from . import sinonim
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
                teks_admin: str = "", tabel_admin=None) -> dict:
    """Daftarkan dokumen baru berstatus 'antre'. Chunk diisi belakangan oleh job.

    `teks_admin`/`tabel_admin` = isi yang diketik langsung di form (bukan berkas);
    disimpan di sini supaya re-index bisa mengulang tanpa admin mengetik lagi.
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
    }
    with _lock:
        rows = load_dokumen()
        rows.insert(0, d)  # terbaru di atas
        _save_dokumen(rows)
    return d


def get_dokumen(dok_id: str) -> dict | None:
    return next((d for d in dokumen() if d.get("id") == dok_id), None)


def update_dokumen(dok_id: str, **fields) -> dict:
    """Perbarui field dokumen. `untuk_pembeli` DIPROPAGASI ke semua chunk-nya
    supaya tak ada chunk yatim yang publik padahal dokumennya internal."""
    boleh = {"judul", "deskripsi", "tag", "untuk_pembeli", "aktif", "pakai_ai",
             "status", "progres", "jumlah_chunk", "pengayaan", "error", "berkas",
             "teks_admin", "tabel_admin"}
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


def update_chunk(cid: str, judul_id=None, kata_kunci=None, dicari=None) -> dict:
    """Kurasi manual per-chunk oleh admin — jalur perbaikan kualitas utama
    ketika pengayaan LLM meleset atau tidak dipakai."""
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
        _save_chunks(rows)
        return rows[i]


# ── pencarian ────────────────────────────────────────────────────────
def _hit(term: str, hay: str) -> bool:
    return bool(re.search(r"(?<![a-z0-9])" + re.escape(term) + r"(?![a-z0-9])", hay))


def _score(r: dict, ql: str, words: list[str]) -> float:
    """Peringkat: judul_id/kata_kunci (kurasi Indonesia) > judul dokumen > kode >
    ringkasan > teks/tabel. Token ber-ANGKA (PN, kode error) ×3 karena paling
    diskriminatif; frasa penuh cocok → bonus besar. Pola manual_teks._score."""
    idn = (f"{r.get('judul_id','')} {' '.join(r.get('kata_kunci') or [])}").lower()
    judul = (r.get("judul") or "").lower()
    ringkas = (r.get("ringkasan") or "").lower()
    kode = " ".join(r.get("kode") or []).lower()
    tabel = " ".join(" ".join(str(c) for c in (baris or []))
                     for baris in (r.get("tabel") or []))
    teks = f"{r.get('teks','')} {tabel}".lower()
    s = 0.0
    if ql and (_hit(ql, idn) or _hit(ql, judul)):
        s += 50
    for w in words:
        spec = 3 if any(c.isdigit() for c in w) else 1
        if _hit(w, idn):
            s += 5 * spec
        elif _hit(w, judul):
            s += 3 * spec
        elif _hit(w, kode):
            s += 4 * spec
        elif _hit(w, ringkas):
            s += 2 * spec
        elif _hit(w, teks):
            s += 1 * spec
    return s


def _words(ql: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9]+", ql) if len(w) >= 2]


# Skor istilah hasil ekspansi sinonim diredam supaya sinonim tidak pernah
# menggeser kecocokan LANGSUNG ke kata yang benar-benar diketik user.
_BOBOT_SINONIM = 0.6

# Ambang relatif: hasil yang skornya jauh di bawah juara BUKAN jawaban, hanya
# kebetulan berbagi satu kata umum. Membuangnya menaikkan akurasi (model tak
# terdistraksi isi yang tak relevan) SEKALIGUS menghemat token — chunk lemah
# tak ikut diserialisasi ke messages.
_AMBANG_RELATIF = 0.30


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
    ql = (topik or "").strip().lower()
    if not ql:
        return []
    try:
        terms, _ = sinonim.expand_query(ql)
    except Exception:
        terms = [ql]
    varian = [(t.strip().lower(), 1.0 if i == 0 else _BOBOT_SINONIM)
              for i, t in enumerate(terms) if (t or "").strip()]
    mati = {d.get("id") for d in dokumen() if not d.get("aktif", True)}
    scored: list[tuple[float, int, dict]] = []
    for i, r in enumerate(chunks()):
        if not r.get("dicari", True):
            continue
        if r.get("dok_id") in mati:
            continue
        if untuk_pembeli and not r.get("untuk_pembeli"):
            continue
        sc = max(_score(r, t, _words(t)) * b for t, b in varian)
        if sc > 0:
            scored.append((sc, i, r))  # i = tie-break stabil
    if not scored:
        return []
    scored.sort(key=lambda t: (-t[0], t[1]))
    lantai = scored[0][0] * _AMBANG_RELATIF
    out: list[dict] = []
    for sc, _, r in scored:
        if sc < lantai:
            break                      # sisanya makin lemah — berhenti
        if any(_mirip(r.get("teks") or "", p.get("teks") or "") for p in out):
            continue                   # kembar akibat overlap chunking
        out.append(r)
        if len(out) >= limit:
            break
    return out


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
