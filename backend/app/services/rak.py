"""
Rak & Kartu Stok — lokasi FISIK part per gudang (tabel `rak_gudang`, migrasi 028)
plus kewenangan menulisnya (kolom `users.gudang_kelola`, migrasi 027).

Kenapa ada: indeks Accurate menjawab BERAPA stoknya, tapi tak pernah menjawab DI
MANA barangnya — staf tetap keliling gudang mencari. Di sini staf gudang menulis
kode rak + memotret kartu stok; halaman detail part & asisten AI tinggal
menyebutkannya ("stok 4 di 01.Jakarta, rak A-12").

⛔ BUKAN sumber kebenaran ANGKA stok. Angka tetap milik Accurate; foto kartu
cuma bukti visual untuk mata manusia (tanpa OCR — keputusan pemilik).

Dua jebakan yang membentuk modul ini:
  1. PN. Katalog & EPC memakai PN ber-suffix varian ('WG9525160004/2'),
     Accurate menyimpan PN DASAR ('WG9525160004'). Rak yang diisi lewat satu
     jalur tak akan pernah ketemu lewat jalur lain kalau kuncinya mentah — maka
     tulis pakai `pn_key()` (kanonik pemaaf-suffix) dan BACA pakai `in.(norm,
     base)` lewat `_kunci_baca()`.
  2. Nama gudang. Yang disimpan LABEL PENUH ('01.Jakarta') karena seluruh
     aplikasi (accurate.gudang_breakdown/per_gudang, gudang_config) memakai
     `warehouseName` apa adanya. `locName` pendek hanya kosmetik pembeli.

Tahan pra-migrasi: `gudang_kelola_for` menelan 42703 (kolom belum ada) dan
mengembalikan [] — fitur DORMAN, tak ada yang meledak (pola customer_map).
"""
from __future__ import annotations

import io
import logging
import re
import threading
import time

import requests

from ..core.config import get_settings
from . import accurate
from .supabase_client import (PHOTO_BUCKET, _rest_url, _service_headers,
                              delete_storage_object)

logger = logging.getLogger("maspart.rak")

_TABLE = "rak_gudang"
_TIMEOUT = 15

# Kolom yang di-SELECT dari DB. `foto_path` ikut dibaca (dibutuhkan jalur foto/
# hapus untuk membuang objek Storage lama) tapi TIDAK pernah sampai ke klien —
# `_bersih()` membuangnya sebelum baris keluar dari modul ini.
_KOLOM = "pn_key,pn_input,gudang,rak,catatan,foto_url,foto_path,updated_by,updated_at"


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# ── Kunci PN (pemaaf-suffix) ─────────────────────────────────────────
def _norm(pn: str) -> str:
    """Normalisasi PN — sama persis dengan accurate.norm_pn (uppercase, buang
    spasi/-/_//)."""
    return re.sub(r"[\s\-_/]", "", (pn or "").upper())


def _dasar(pn: str) -> str:
    """PN DASAR: potong suffix varian di '/' atau '+' ('WG9525160004/2' →
    'WG9525160004'), lalu dinormalisasi."""
    return _norm(re.split(r"[/+]", (pn or "").strip().upper())[0])


def pn_key(pn: str) -> str:
    """Kunci SIMPAN kanonik untuk sebuah PN.

    Utamanya `accurate.index_key` — ia yang tahu bentuk mana ('apa adanya' vs
    'PN dasar') yang benar-benar dipakai Accurate. Jebakan: saat indeks belum
    dimuat (cold start / test), index_key balik ke kunci APA ADANYA sehingga
    'WG9525160004/2' tersimpan sebagai 'WG95251600042' dan besok — setelah
    indeks hangat — jadi baris kedua yang yatim. Karena itu indeks kosong →
    langsung pakai PN DASAR, bentuk yang deterministik dan paling sering benar.
    """
    s = (pn or "").strip()
    if not s:
        return ""
    try:
        if accurate.snapshot():          # indeks hangat → percayai index_key
            k = accurate.index_key(s)
            if k:
                return k
    except Exception:                    # pragma: no cover — indeks tak boleh menjatuhkan input rak
        logger.debug("index_key gagal untuk %s", s, exc_info=True)
    return _dasar(s) or _norm(s)


def _kunci_baca(pn: str) -> list[str]:
    """Kunci-kunci yang dicoba saat MEMBACA: (norm apa adanya, PN dasar).

    Baris bisa saja tertulis dengan salah satu bentuk (diisi saat indeks dingin,
    atau di-upload dengan suffix). Membaca dengan `in.(...)` menutup dua-duanya
    tanpa perlu migrasi data."""
    out: list[str] = []
    for k in (_norm(pn), _dasar(pn), pn_key(pn)):
        if k and k not in out:
            out.append(k)
    return out


def _in_filter(nilai: list[str]) -> str:
    """Nilai untuk operator PostgREST `in.` — dikutip agar aman bila mengandung koma."""
    return "(" + ",".join('"' + v.replace('"', '') + '"' for v in nilai) + ")"


def _bersih(row: dict) -> dict:
    """Baris DB → bentuk yang dipakai API/UI (tanpa foto_path internal)."""
    return {
        "part_number": row.get("pn_input") or row.get("pn_key") or "",
        "pn_key": row.get("pn_key") or "",
        "gudang": row.get("gudang") or "",
        "rak": row.get("rak") or "",
        "catatan": row.get("catatan") or "",
        "foto_url": row.get("foto_url") or "",
        "updated_by": row.get("updated_by") or "",
        "updated_at": row.get("updated_at") or "",
    }


# ── Baca ─────────────────────────────────────────────────────────────
def get_for_pn(pn: str) -> dict[str, dict]:
    """{label gudang → baris rak} untuk satu PN. Kosong bila tak ada/DB mati.

    ⛔ TAK PERNAH raise: dipanggil dari halaman detail part & tool asisten —
    rak yang gagal dibaca tak boleh menjatuhkan info stok/harga."""
    kunci = _kunci_baca(pn)
    if not kunci or not get_settings().supabase_configured:
        return {}
    try:
        r = requests.get(_rest_url(_TABLE),
                         headers={**_service_headers(), "Accept": "application/json"},
                         params={"select": _KOLOM, "pn_key": f"in.{_in_filter(kunci)}"},
                         timeout=_TIMEOUT)
        if r.status_code != 200:
            return {}
        rows = r.json() or []
    except Exception:
        logger.debug("baca rak gagal untuk %s", pn, exc_info=True)
        return {}
    out: dict[str, dict] = {}
    for row in rows:
        g = (row.get("gudang") or "").strip()
        if g:
            out[g] = _bersih(row)
    return out


def get_for_gudang(label: str, q: str = "", limit: int = 300) -> list[dict]:
    """Lookup TERBALIK: isi satu gudang (menu Rak & Kartu Stok).

    `q` = substring pada PN maupun kode rak — staf lebih sering bertanya "rak
    A-12 isinya apa" daripada mencari PN tertentu."""
    label = (label or "").strip()
    if not label or not get_settings().supabase_configured:
        return []
    params = {"select": _KOLOM, "gudang": f"eq.{label}",
              "order": "rak.asc", "limit": str(max(1, min(int(limit or 300), 2000)))}
    q = (q or "").strip()
    if q:
        # PN dicari dengan bentuk ter-normalisasi (kolom pn_key sudah normal),
        # rak & catatan dicari apa adanya (huruf-besar-kecil diabaikan).
        params["or"] = (f"(pn_key.ilike.*{_norm(q)}*,pn_input.ilike.*{q}*,"
                        f"rak.ilike.*{q}*,catatan.ilike.*{q}*)")
    try:
        r = requests.get(_rest_url(_TABLE),
                         headers={**_service_headers(), "Accept": "application/json"},
                         params=params, timeout=_TIMEOUT)
        if r.status_code != 200:
            return []
        return [_bersih(x) for x in (r.json() or [])]
    except Exception:
        logger.debug("lookup rak per gudang gagal (%s)", label, exc_info=True)
        return []


def _row_mentah(pn: str, gudang: str) -> dict | None:
    """Baris DB apa adanya (termasuk `foto_path`) — dipakai jalur foto & hapus."""
    kunci = _kunci_baca(pn)
    if not kunci or not (gudang or "").strip() or not get_settings().supabase_configured:
        return None
    try:
        r = requests.get(_rest_url(_TABLE),
                         headers={**_service_headers(), "Accept": "application/json"},
                         params={"select": _KOLOM, "pn_key": f"in.{_in_filter(kunci)}",
                                 "gudang": f"eq.{gudang.strip()}", "limit": "1"},
                         timeout=_TIMEOUT)
        if r.status_code != 200:
            return None
        rows = r.json() or []
        return rows[0] if rows else None
    except Exception:
        return None


# ── Tulis ────────────────────────────────────────────────────────────
def upsert(pn: str, gudang: str, rak: str, catatan: str = "",
           username: str = "") -> tuple[bool, str]:
    """Simpan/ubah rak satu pasangan (pn × gudang). (ok, pesan).

    Upsert PostgREST `on_conflict=pn_key,gudang` (pola insert_part_photo) — hanya
    kolom yang DIKIRIM yang ditimpa, jadi `foto_url`/`foto_path` yang sudah ada
    tidak ikut terhapus saat staf sekadar membetulkan kode raknya."""
    pk, g, rk = pn_key(pn), (gudang or "").strip(), (rak or "").strip()
    if not pk:
        return False, "Part Number kosong."
    if not g:
        return False, "Gudang kosong."
    if not rk:
        return False, "Kode rak kosong."
    if not get_settings().supabase_configured:
        return False, "Supabase belum dikonfigurasi"
    row = {
        "pn_key": pk,
        "pn_input": (pn or "").strip().upper(),
        "gudang": g,
        "rak": rk,
        "catatan": (catatan or "").strip() or None,
        "updated_by": (username or "").strip().lower() or None,
        "updated_at": _now(),
    }
    try:
        r = requests.post(_rest_url(_TABLE),
                          headers=_service_headers("resolution=merge-duplicates,return=minimal"),
                          params={"on_conflict": "pn_key,gudang"},
                          json=row, timeout=_TIMEOUT)
    except Exception as e:
        return False, str(e)
    if r.status_code in (200, 201, 204):
        return True, "ok"
    if _tabel_hilang(r):
        return False, ("Tabel rak_gudang belum ada — jalankan "
                       "migrations/028_rak_gudang.sql di Supabase.")
    return False, f"{r.status_code}: {(r.text or '')[:200]}"


def hapus(pn: str, gudang: str) -> tuple[bool, str]:
    """Hapus satu baris rak. Foto yang menempel ikut dibuang dari Storage —
    kalau tidak, objeknya jadi sampah permanen yang tak bisa ditemukan lagi."""
    pk, g = pn_key(pn), (gudang or "").strip()
    if not pk or not g:
        return False, "Part Number / gudang kosong."
    if not get_settings().supabase_configured:
        return False, "Supabase belum dikonfigurasi"
    lama = _row_mentah(pn, g)
    try:
        r = requests.delete(_rest_url(_TABLE),
                            headers=_service_headers("return=minimal"),
                            params={"pn_key": f"in.{_in_filter(_kunci_baca(pn))}",
                                    "gudang": f"eq.{g}"},
                            timeout=_TIMEOUT)
    except Exception as e:
        return False, str(e)
    if r.status_code not in (200, 204):
        return False, f"{r.status_code}: {(r.text or '')[:200]}"
    if lama and lama.get("foto_path"):
        _buang_objek(lama["foto_path"])
    return True, "ok"


def set_foto(pn: str, gudang: str, foto_url: str, foto_path: str,
             username: str = "") -> tuple[bool, str]:
    """Pasang foto kartu stok TERBARU (tanpa riwayat).

    Objek LAMA dihapus lebih dulu: pemilik memutuskan cukup foto terakhir, jadi
    tiap penggantian tanpa pembersihan = satu file yatim selamanya di bucket."""
    g = (gudang or "").strip()
    lama = _row_mentah(pn, g)
    if not lama:
        # Baris rak belum ada → tak ada tempat menempelkan foto. Sengaja BUKAN
        # membuat baris kosong: kolom `rak` not null, dan foto tanpa lokasi rak
        # tak menjawab pertanyaan siapa pun.
        return False, "Isi kode rak dulu sebelum mengunggah foto kartu stok."
    lama_path = lama.get("foto_path") or ""
    ok, msg = _patch(pn, g, {
        "foto_url": (foto_url or "").strip() or None,
        "foto_path": (foto_path or "").strip() or None,
        "updated_by": (username or "").strip().lower() or None,
        "updated_at": _now(),
    })
    if ok and lama_path and lama_path != (foto_path or "").strip():
        _buang_objek(lama_path)
    return ok, msg


def hapus_foto(pn: str, gudang: str, username: str = "") -> tuple[bool, str]:
    """Lepas foto kartu stok (baris raknya tetap ada)."""
    g = (gudang or "").strip()
    lama = _row_mentah(pn, g)
    if not lama:
        return False, "Data rak tidak ditemukan."
    ok, msg = _patch(pn, g, {"foto_url": None, "foto_path": None,
                             "updated_by": (username or "").strip().lower() or None,
                             "updated_at": _now()})
    if ok and lama.get("foto_path"):
        _buang_objek(lama["foto_path"])
    return ok, msg


def _patch(pn: str, gudang: str, data: dict) -> tuple[bool, str]:
    if not get_settings().supabase_configured:
        return False, "Supabase belum dikonfigurasi"
    try:
        r = requests.patch(_rest_url(_TABLE),
                           headers=_service_headers("return=minimal"),
                           params={"pn_key": f"in.{_in_filter(_kunci_baca(pn))}",
                                   "gudang": f"eq.{(gudang or '').strip()}"},
                           json=data, timeout=_TIMEOUT)
    except Exception as e:
        return False, str(e)
    if r.status_code in (200, 204):
        return True, "ok"
    return False, f"{r.status_code}: {(r.text or '')[:200]}"


def _buang_objek(path: str) -> None:
    """Hapus objek Storage — best-effort, gagalnya tak boleh membatalkan
    penyimpanan data rak (barangnya sudah pindah rak, itu yang penting)."""
    try:
        delete_storage_object(PHOTO_BUCKET, path)
    except Exception:
        logger.debug("hapus objek foto rak gagal (%s)", path, exc_info=True)


def _tabel_hilang(resp) -> bool:
    """PostgREST 42P01 / PGRST205 = tabel belum dibuat (migrasi 028 belum jalan)."""
    if resp.status_code not in (400, 404):
        return False
    txt = (resp.text or "").lower()
    return "rak_gudang" in txt and ("42p01" in txt or "pgrst205" in txt
                                   or "does not exist" in txt or "not find" in txt)


# ── Kewenangan tulis (users.gudang_kelola) ───────────────────────────
# Cache: gerbang `boleh_tulis` dipanggil di SETIAP endpoint tulis dan saat
# menyusun payload izin; tanpa cache, satu sesi impor Excel memukul Supabase
# sekali per baris. TTL pendek supaya pencabutan hak oleh admin cepat berlaku;
# admin.py juga memanggil invalidate() begitu penugasan disimpan.
_CACHE_TTL = 60.0
_cache_lock = threading.Lock()
_cache: dict[str, tuple[float, list[str]]] = {}


def invalidate(username: str = "") -> None:
    """Buang cache gudang_kelola satu akun (kosong = semua)."""
    with _cache_lock:
        if username:
            _cache.pop((username or "").strip().lower(), None)
        else:
            _cache.clear()


def _kolom_hilang(resp) -> bool:
    """PostgREST 42703 = kolom tak dikenal (migrasi 027 belum jalan)."""
    if resp.status_code not in (400, 404):
        return False
    txt = (resp.text or "").lower()
    return "gudang_kelola" in txt and ("column" in txt or "42703" in txt)


def parse_kelola(nilai: str | None) -> list[str]:
    """'01.Jakarta, 06.B80 H1' → ['01.Jakarta', '06.B80 H1'] (urut & tanpa duplikat)."""
    if not nilai:
        return []
    out: list[str] = []
    for bagian in str(nilai).split(","):
        lb = bagian.strip()
        if lb and lb not in out:
            out.append(lb)
    return sorted(out)


def format_kelola(labels: list[str] | None) -> str | None:
    """Kebalikan parse_kelola — bentuk simpan koma-spasi. Kosong → None (NULL)."""
    bersih = parse_kelola(", ".join(str(x) for x in (labels or [])))
    return ", ".join(bersih) or None


def gudang_kelola_for(username: str) -> list[str]:
    """Label gudang yang boleh DITULIS akun ini. [] = staf biasa.

    ⛔ TAK PERNAH raise: ikut dipanggil dari payload izin yang dimuat setiap
    halaman. Kolom belum ada (42703, migrasi 027 belum jalan) → [] alias fitur
    DORMAN, bukan error (pola customer_map._kolom_hilang)."""
    uname = (username or "").strip().lower()
    if not uname:
        return []
    now = time.monotonic()
    with _cache_lock:
        hit = _cache.get(uname)
        if hit and (now - hit[0]) < _CACHE_TTL:
            return list(hit[1])
    labels: list[str] = []
    s = get_settings()
    if s.supabase_configured:
        try:
            r = requests.get(_rest_url(s.supabase_table),
                             headers={**_service_headers(), "Accept": "application/json"},
                             params={"select": "gudang_kelola",
                                     "username": f"eq.{uname}", "limit": "1"},
                             timeout=10)
            if _kolom_hilang(r):
                labels = []
            elif r.status_code == 200:
                rows = r.json() or []
                labels = parse_kelola(rows[0].get("gudang_kelola") if rows else "")
        except Exception:
            logger.debug("baca gudang_kelola gagal (%s)", uname, exc_info=True)
            labels = []
    with _cache_lock:
        _cache[uname] = (time.monotonic(), list(labels))
    return list(labels)


def boleh_tulis(user: dict, gudang: str) -> bool:
    """Boleh MENGUBAH rak gudang ini? Admin selalu; pembeli tak pernah (data
    internal); staf hanya untuk gudang yang ditugaskan admin.

    Fail-CLOSED seperti permissions.boleh_ai: memberi hak tulis ke gudang orang
    lain karena Supabase sedang ngadat jelas lebih buruk daripada menolak."""
    role = (user.get("role") or "").lower()
    if role == "admin":
        return True
    if role == "pembeli":
        return False
    g = (gudang or "").strip()
    if not g:
        return False
    try:
        return g in gudang_kelola_for(user.get("username", ""))
    except Exception:
        return False


# ── Impor massal (Excel/CSV) ─────────────────────────────────────────
def parse_import(data: bytes, filename: str = "") -> tuple[list[dict], list[dict]]:
    """Baca file unggahan → (baris valid, baris dilewati).

    Meniru opname.parse_upload: pandas `dtype=str` + pencarian header berbasis
    SUBSTRING, karena file gudang nyata menulis judul kolom sesuka hati
    ('Part Number', 'No Part', 'KODE BARANG'). Baris tanpa kode rak DILEWATI
    dan dilaporkan — diam-diam menghapus rak yang sudah benar jauh lebih mahal
    daripada memberi tahu barisnya kosong.

    Valid   : [{'pn', 'rak', 'catatan'}]
    Dilewati: [{'pn', 'alasan'}]
    """
    import pandas as pd

    bio = io.BytesIO(data or b"")
    if (filename or "").lower().endswith(".csv"):
        df = pd.read_csv(bio, dtype=str)
    else:
        df = pd.read_excel(bio, dtype=str)
    if df.empty:
        return [], []

    def _find(keys: list[str]) -> str | None:
        for c in df.columns:
            cl = str(c).strip().lower()
            if any(k in cl for k in keys):
                return c
        return None

    pn_col = _find(["part number", "partnumber", "kode", "no part"]) or df.columns[0]
    rak_col = _find(["rak", "lokasi"])
    cat_col = _find(["catatan", "note", "keterangan"])

    valid: list[dict] = []
    dilewati: list[dict] = []
    if rak_col is None:
        # Tanpa kolom rak file ini tak punya isi sama sekali → jangan diam-diam
        # menyimpan 0 baris dan melaporkan "berhasil".
        return [], [{"pn": "", "alasan": "Kolom RAK tidak ditemukan (butuh judul kolom 'Rak' atau 'Lokasi')."}]

    def _teks(v) -> str:
        s = str(v).strip()
        return "" if s.lower() in ("nan", "none", "nat", "-", "—") else s

    seen: set[str] = set()
    for _, row in df.iterrows():
        pn = _teks(row[pn_col]).upper()
        if not pn:
            continue
        rak = _teks(row[rak_col])
        if not rak:
            dilewati.append({"pn": pn, "alasan": "kode rak kosong"})
            continue
        k = pn_key(pn) or pn
        if k in seen:
            dilewati.append({"pn": pn, "alasan": "PN ganda dalam file — baris pertama dipakai"})
            continue
        seen.add(k)
        valid.append({"pn": pn, "rak": rak,
                      "catatan": _teks(row[cat_col]) if cat_col else ""})
    return valid, dilewati
