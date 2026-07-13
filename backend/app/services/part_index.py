"""
Service: index part + pencarian Part Number — DECOUPLED dari Streamlit.

Logika di sini diekstrak dari app.py (process_single_file, parse_stok_file,
search_part_number, _load_harga_data) TANPA satupun `st.*`. Tujuannya: baik
backend FastAPI maupun (nanti) app Streamlit bisa memanggil service yang sama
sehingga tidak ada duplikasi perilaku.

Index dibangun sekali lalu di-cache di memori; panggil refresh_index() untuk
membangun ulang (mis. setelah admin upload data baru).
"""
from __future__ import annotations

import difflib
import hashlib
import io
import os
import pickle
import re
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd

from ..core.config import get_settings
from . import harga
from .supabase_client import download_storage_object

# ── Regex parser stok (disalin dari app.py) ──────────────────────────
_GUDANG_HEADER_RE = re.compile(r"^\s*\d+\s*\.")   # "01.Jakarta", "25. PT BJM"
_KODE_PREFIX_RE = re.compile(r"^\d{6}\.")          # "000001." pada Kode Barang

_EXCEL_EXT = (".xlsx", ".xls", ".xlsm")
# Subfolder data yang BUKAN database part (di-load terpisah sebagai lookup).
# 'manuals' berisi PDF/Excel referensi (mis. filter Shantui) — jangan diparse
# sebagai katalog part.
_NON_PART_DIRS = {"stok", "harga", "populasi", "manuals", "sinonim", "repairkit"}

# Teks baris JUDUL kolom yang kerap ikut terbaca sebagai "part" (kolom B atau D).
# Dicocokkan PERSIS (UPPER, stripped) agar tidak menyentuh Part Number asli.
_HEADER_TOKENS = {
    "图号", "序号", "零件图号", "PART NO.", "PART NO", "PART NUMBER", "PARTS NO.",
    "P/N", "NO.", "ITEM", "ITEM NO.", "PART NAME", "PARTS NAME", "名称", "零件名称",
}

# Singkatan umum katalog part → bentuk panjang, agar 'kabin assy' = 'kabin
# assembly', 'cyl' = 'cylinder', dst. Dicocokkan per-token (kata utuh).
_PART_ABBR = {
    "assy": "assembly", "assy.": "assembly", "ass'y": "assembly",
    "assly": "assembly", "asmbly": "assembly", "asm": "assembly",
    "cyl": "cylinder", "brkt": "bracket", "brg": "bearing",
    "hsg": "housing", "gskt": "gasket",
}


def _normalize_abbr(q: str) -> str:
    """Ganti token singkatan umum dengan bentuk panjangnya (lihat _PART_ABBR)."""
    return " ".join(_PART_ABBR.get(w.lower().strip(".,;:"), w) for w in q.split())


def _phrase_or_allwords(haystack_up: str, kw_up: str) -> bool:
    """Cocok bila frasa utuh ada, ATAU semua kata penting (>2 huruf) ada di
    haystack (urutan bebas). Mis. 'kabin assembly' tetap cocok ke teks
    '... cab assembly ... kabin assembly ...' walau katanya tak berurutan."""
    if kw_up in haystack_up:
        return True
    words = [w for w in kw_up.split() if len(w) > 2]
    return bool(words) and all(w in haystack_up for w in words)

# ── Disk cache per-file (mirror app.py: hash size+mtime → pickle) ─────
# Versi schema cache — naikkan kalau struktur entri _process_file berubah,
# supaya cache lama otomatis diabaikan.
_CACHE_VERSION = "v3"  # v3: saring baris header (图号/PART NO.) agar tak jadi part palsu
_CACHE_DIR = Path(__file__).resolve().parents[2] / ".cache"


def _file_hash(fp: Path) -> Optional[str]:
    try:
        s = fp.stat()
        raw = f"{_CACHE_VERSION}_{fp}_{s.st_size}_{s.st_mtime}"
        return hashlib.md5(raw.encode("utf-8")).hexdigest()
    except Exception:
        return None


def _cache_load(fh: str) -> Optional[list]:
    cf = _CACHE_DIR / f"{fh}.pkl"
    if cf.exists():
        try:
            with open(cf, "rb") as f:
                return pickle.load(f)
        except Exception:
            return None
    return None


def _cache_save(fh: str, data: list) -> None:
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        with open(_CACHE_DIR / f"{fh}.pkl", "wb") as f:
            pickle.dump(data, f, protocol=pickle.HIGHEST_PROTOCOL)
    except Exception:
        pass

# ── State index (in-memory, thread-safe) ─────────────────────────────
_lock = threading.Lock()
_state: dict = {
    "excel_files": [],     # list[dict] entri per sheet (lihat _process_file)
    "stok_cache": {},      # {PN_UPPER: "total"}
    "gudang_cache": {},    # {PN_UPPER: {nama_gudang: qty}}
    "gudang_names": [],
    "harga_lookup": {},    # {PN_UPPER: "Rp x"}
    "indexed_at": None,
    "file_count": 0,
    "part_count": 0,
    "name_vocab": set(),       # {WORD_UPPER} semua kata nama part — utk koreksi typo
    "name_vocab_list": [],     # list(name_vocab) — cache utk difflib
}


# ═══════════════════════════════════════════════════════════════════════
#  PARSER PART (per file Excel)  — mirror app.py::process_single_file
# ═══════════════════════════════════════════════════════════════════════
def extract_simple_filename(filename: str) -> str:
    name = os.path.splitext(filename)[0]
    return name.split(" - ")[-1] if " - " in name else name


def _process_file(file_path: Path, relative_path: Path) -> list[dict]:
    simple_name = extract_simple_filename(file_path.name)

    # Disk cache: kalau file (size+mtime) tak berubah, muat dari pickle.
    fh = _file_hash(file_path)
    if fh:
        cached = _cache_load(fh)
        if cached is not None:
            return cached

    results: list[dict] = []
    try:
        xls = pd.ExcelFile(file_path, engine="openpyxl")
    except Exception:
        return results

    for sheet_name in xls.sheet_names:
        try:
            # Kolom (0-based): B=Part No.(1), D=Part Name(3), E=Qty(4), F=备注/Remark(5).
            # Kolom F dibaca sbg 'remark' (keterangan tambahan dari admin) lalu IKUT
            # diindeks untuk pencarian nama — TIDAK menggantikan part_name yang tampil.
            # Baca semua kolom lalu pilih posisional agar aman pada sheet yang kolomnya
            # lebih sedikit (mis. tanpa kolom F).
            raw = pd.read_excel(xls, sheet_name=sheet_name, dtype=str)
            ncol = raw.shape[1]

            def _col(i: int):
                return raw.iloc[:, i].reset_index(drop=True) if ncol > i else pd.Series([""] * len(raw))

            df = pd.DataFrame({
                "part_number": _col(1),
                "part_name": _col(3),
                "quantity": _col(4),
                "remark": _col(5),
            })

            # Buang baris JUDUL/HEADER yang ikut terbaca sebagai part: tiap sheet
            # punya baris judul kolom (mis. B='图号'/'PART NO.' dgn D='Part Name')
            # yang sebelumnya terindeks jadi "part palsu" di 73 unit. Saring agar
            # tak mengotori hasil (detail_part('PART NO.') dll).
            _pn_u = df["part_number"].fillna("").astype(str).str.strip().str.upper()
            _nm_u = df["part_name"].fillna("").astype(str).str.strip().str.upper()
            _hdr = _pn_u.isin(_HEADER_TOKENS) | _nm_u.isin(_HEADER_TOKENS)
            if _hdr.any():
                df = df[~_hdr].reset_index(drop=True)

            pn_series = df["part_number"].fillna("").astype(str).str.strip().str.upper()
            pn_valid = pn_series[pn_series != ""]
            if len(pn_valid):
                pn_idx = (
                    pn_valid.reset_index()
                    .groupby("part_number", sort=False)["index"]
                    .apply(list)
                    .to_dict()
                )
            else:
                pn_idx = {}

            # nm_idx: {WORD_UPPER: [row_idx]} — untuk search by part name.
            # Sumber kata kunci = Part Name (kolom D) + Remark (kolom F) digabung, agar
            # keterangan yang ditulis admin di kolom Remark ikut bisa dicari.
            name_series = df["part_name"].fillna("").astype(str).str.strip()
            remark_series = df["remark"].fillna("").astype(str).str.strip()
            searchable = (name_series + " " + remark_series).str.upper()
            nm_idx: dict[str, list[int]] = {}
            for idx, txt in searchable.items():
                seen_words: set[str] = set()
                for word in txt.split():
                    if len(word) > 2 and word not in seen_words:
                        seen_words.add(word)
                        nm_idx.setdefault(word, []).append(idx)

            results.append({
                "full_path": str(file_path),
                "relative_path": str(relative_path),
                "simple_name": simple_name,
                "sheet": sheet_name,
                "dataframe": df,
                "part_number_index": pn_idx,
                "part_name_index": nm_idx,
            })
        except Exception:
            continue

    if fh and results:
        _cache_save(fh, results)
    return results


# ═══════════════════════════════════════════════════════════════════════
#  PARSER STOK  — mirror app.py::parse_stok_file
# ═══════════════════════════════════════════════════════════════════════
def _stok_to_int(v) -> int:
    if v is None:
        return 0
    s = str(v).strip()
    if not s or s.lower() in ("nan", "none", "—", "-"):
        return 0
    s = s.replace(",", "").replace(".", "")
    try:
        return int(float(s))
    except Exception:
        return 0


def _parse_stok_file(file_bytes: bytes) -> tuple[dict, dict, list]:
    try:
        raw = pd.read_excel(io.BytesIO(file_bytes), header=None, dtype=str)
    except Exception:
        return {}, {}, []
    if raw.empty:
        return {}, {}, []

    header_idx = None
    for i in range(min(15, len(raw))):
        c0 = str(raw.iloc[i, 0]).strip().lower()
        if c0 in ("kode barang", "kode barang "):
            header_idx = i
            break

    # FORMAT LAMA (single-total)
    if header_idx is None:
        df = raw
        if len(df) > 0 and any(
            str(x).lower() in ["part number", "kode", "no part"] for x in df.iloc[0]
        ):
            df = df.iloc[1:]
        ncol = df.shape[1]
        stk_i = 3 if ncol > 3 else (ncol - 1)
        stok_cache = {}
        for _, row in df.iterrows():
            pn = str(row.iloc[0]).strip().upper()
            if not pn or pn in ("NAN", "NONE"):
                continue
            val = row.iloc[stk_i]
            stok_cache[pn] = "—" if pd.isna(val) else str(val).strip()
        return stok_cache, {}, []

    # FORMAT MULTI-GUDANG
    headers = [str(x).strip() if not pd.isna(x) else "" for x in raw.iloc[header_idx]]
    total_i = None
    gudang_cols = []
    for ci, h in enumerate(headers):
        hl = h.lower()
        if hl.startswith("total"):
            total_i = ci
        elif _GUDANG_HEADER_RE.match(h):
            gudang_cols.append((ci, h))

    gudang_names = [name for _, name in gudang_cols]
    stok_cache, gudang_cache = {}, {}
    for ri in range(header_idx + 1, len(raw)):
        row = raw.iloc[ri]
        kode = str(row.iloc[0]).strip()
        if not kode or kode.lower() in ("nan", "none"):
            continue
        pn = _KODE_PREFIX_RE.sub("", kode).strip().upper()
        if not pn:
            continue
        if total_i is not None:
            total_val = _stok_to_int(row.iloc[total_i])
        else:
            total_val = sum(_stok_to_int(row.iloc[ci]) for ci, _ in gudang_cols)
        breakdown = {name: q for ci, name in gudang_cols if (q := _stok_to_int(row.iloc[ci])) != 0}
        stok_cache[pn] = str(total_val)
        gudang_cache[pn] = breakdown
    return stok_cache, gudang_cache, gudang_names


# ═══════════════════════════════════════════════════════════════════════
#  PARSER HARGA  — mirror app.py::_load_harga_data
# ═══════════════════════════════════════════════════════════════════════
def _parse_harga_file(file_bytes: bytes) -> dict:
    try:
        df = pd.read_excel(io.BytesIO(file_bytes), dtype=str)
    except Exception:
        return {}
    if df.empty:
        return {}

    rename = {}
    for c in df.columns:
        cl = str(c).strip().lower()
        if cl in ("part number", "partnumber", "no part", "kode"):
            rename[c] = "Part Number"
        elif cl in ("part name", "nama", "deskripsi"):
            rename[c] = "Part Name"
        elif cl in ("harga", "price"):
            rename[c] = "Harga"
    df = df.rename(columns=rename)
    if "Part Number" not in df.columns or "Harga" not in df.columns:
        return {}

    lookup = {}
    for _, row in df.iterrows():
        pn = str(row["Part Number"]).strip().upper()
        if not pn or pn in ("NAN", "NONE"):
            continue
        harga_raw = row["Harga"]
        if pd.isna(harga_raw):
            continue
        try:
            harga_int = int(float(str(harga_raw).replace(",", "").replace(".", "").strip()))
            lookup[pn] = f"Rp {harga_int:,}".replace(",", ".")
        except Exception:
            lookup[pn] = str(harga_raw).strip()
    return lookup


# ═══════════════════════════════════════════════════════════════════════
#  BUILD INDEX
# ═══════════════════════════════════════════════════════════════════════
def _walk_part_files(data_dir: Path) -> list[tuple[Path, Path]]:
    out = []
    for root, _, files in os.walk(data_dir):
        rel_root = Path(root).relative_to(data_dir)
        top = rel_root.parts[0].lower() if rel_root.parts else ""
        if top in _NON_PART_DIRS:
            continue
        for f in files:
            if f.lower().endswith(_EXCEL_EXT):
                fp = Path(root) / f
                out.append((fp, fp.relative_to(data_dir)))
    return out


def _read_optional(path: Path) -> Optional[bytes]:
    try:
        return path.read_bytes() if path.exists() else None
    except Exception:
        return None


def _load_dataset_bytes(local_path: Path, remote_name: str) -> Optional[bytes]:
    """
    Ambil file dataset (stok/harga): utamakan file lokal, kalau tidak ada
    download dari Supabase Storage (seperti Streamlit). Return bytes/None.
    """
    data = _read_optional(local_path)
    if data is not None:
        return data
    return download_storage_object(remote_name)


def refresh_index() -> dict:
    """(Re)build index dari DATA_DIR. Return ringkasan status."""
    settings = get_settings()
    data_dir = settings.data_path

    excel_files: list[dict] = []
    file_count = 0
    if data_dir.exists():
        for fp, rp in _walk_part_files(data_dir):
            file_count += 1
            excel_files.extend(_process_file(fp, rp))

    stok_bytes = _load_dataset_bytes(data_dir / "stok" / "stok.xlsx", "stok.xlsx")
    stok_cache, gudang_cache, gudang_names = (
        _parse_stok_file(stok_bytes) if stok_bytes else ({}, {}, [])
    )
    harga_bytes = _load_dataset_bytes(data_dir / "harga" / "harga.xlsx", "harga.xlsx")
    harga_lookup = _parse_harga_file(harga_bytes) if harga_bytes else {}

    part_count = sum(len(fi.get("part_number_index", {})) for fi in excel_files)

    # Kosakata nama part (semua kata, sudah UPPER & >2 huruf) untuk koreksi salah
    # ketik (fuzzy) dan saran "mungkin maksud Anda".
    name_vocab: set[str] = set()
    for fi in excel_files:
        name_vocab.update(fi.get("part_name_index", {}).keys())

    with _lock:
        _state.update({
            "excel_files": excel_files,
            "stok_cache": stok_cache,
            "gudang_cache": gudang_cache,
            "gudang_names": gudang_names,
            "harga_lookup": harga_lookup,
            "indexed_at": datetime.now(),
            "file_count": file_count,
            "part_count": part_count,
            "name_vocab": name_vocab,
            "name_vocab_list": list(name_vocab),
        })
    return status()


def ensure_index() -> None:
    """Build index sekali kalau belum pernah (lazy)."""
    if _state["indexed_at"] is None:
        refresh_index()


def gudang_names() -> list[str]:
    """Daftar nama gudang dari INDEKS ACCURATE (tarikan terjadwal 3×/hari).
    ⛔ Aturan pemilik 2026-07-12: stok/harga/rincian gudang HANYA dari indeks
    Accurate — stok.xlsx tak lagi jadi sumber. Nama dari stok.xlsx hanya dipakai
    bila indeks Accurate benar-benar kosong (cold start tanpa file persist),
    supaya fallback-gudang-terdekat tidak kehilangan kandidat."""
    from . import accurate
    names = accurate.warehouse_names()
    if names:
        return names
    ensure_index()
    return list(_state["gudang_names"])


def unit_models() -> list[dict]:
    """Daftar unit/model truk. Tiap NAMA FILE Excel = satu tipe unit
    (simple_name), dikelompokkan per kategori folder.
    Mis. {'unit': 'NX360 6X4 (LZZ1BLSG)', 'kategori': 'Sinotruk/NX360HP'}."""
    ensure_index()
    seen: dict[str, dict] = {}
    for fi in _state["excel_files"]:
        sn = str(fi.get("simple_name") or "").strip()
        if not sn or sn in seen:
            continue
        rp = str(fi.get("relative_path") or "")
        parent = str(Path(rp).parent).replace("\\", "/")
        seen[sn] = {"unit": sn, "kategori": "" if parent == "." else parent}
    return sorted(seen.values(), key=lambda x: (x["kategori"], x["unit"]))


def gudang_breakdown(pn: str) -> dict:
    """Rincian stok {gudang: qty} untuk satu Part Number (hanya qty != 0) —
    SATU-SATUNYA sumber = INDEKS ACCURATE (tarikan 3×/hari, persist disk, enrichment
    per-gudang). ⛔ Aturan pemilik 2026-07-12: stok.xlsx TAK PERNAH lagi dipakai
    untuk stok — indeks Accurate dibagi ke semua fitur (etalase, order, search, AI)."""
    from . import accurate
    out: dict[str, int] = {}
    for g, q in (accurate.gudang_breakdown(pn) or {}).items():
        try:
            qi = int(q)
        except (TypeError, ValueError):
            continue
        if qi != 0:
            out[g] = qi
    return out


def _acc_stok_harga(snap: dict, pn_key: str) -> tuple[str, str]:
    """(stok_str, harga_str) baris hasil pencarian, dari snapshot indeks Accurate.
    '—' bila PN tak ada di Accurate (perusahaan tak menstok/menjualnya).
    ⛔ Dulu diisi dari stok.xlsx & harga.xlsx — dua-duanya kini dilarang pemilik."""
    from . import accurate
    e = snap.get(accurate.norm_pn(pn_key)) if snap else None
    if not e:
        return "—", "—"
    stok = e.get("stok")
    hg = e.get("harga")
    s = f"{int(stok):,}".replace(",", ".") if stok is not None else "—"
    h = "Rp " + f"{int(hg):,}".replace(",", ".") if hg else "—"
    return s, h


def harga_map() -> dict[str, str]:
    """Lookup harga Excel {PN_UPPER: 'Rp x'} apa adanya — dipakai etalase pembeli
    sebagai fallback bila PN tak ada di indeks Accurate. Jangan dimutasi."""
    ensure_index()
    return _state["harga_lookup"]


def name_for(pn: str) -> str:
    """Nama part untuk satu Part Number (exact, uppercase). '' bila tak ada."""
    ensure_index()
    key = (pn or "").strip().upper()
    if not key:
        return ""
    for fi in _state["excel_files"]:
        indices = fi.get("part_number_index", {}).get(key)
        if indices:
            row = fi["dataframe"].iloc[indices[0]]
            return str(row["part_name"]) if pd.notna(row["part_name"]) else ""
    return ""


_ASM_LASTRUN_RE = re.compile(r"(\d{3,})(\D*)$")


def _assembly_base(pn: str) -> Optional[str]:
    """PN INDUK assembly ala penomoran Shantui/Komatsu: nolkan 3 digit terakhir dari
    run angka TERAKHIR. '146-56-15200' → '146-56-15000'; '16Y-56E-26400' →
    '16Y-56E-26000'. None bila sudah level assembly (…000) / tak ada run angka."""
    m = _ASM_LASTRUN_RE.search(pn or "")
    if not m:
        return None
    run = m.group(1)
    base = run[:-3] + "000"
    if base == run:
        return None
    return (pn or "")[:m.start(1)] + base + m.group(2)


def _group_key(pn: str) -> Optional[str]:
    """Kunci GRUP assembly = PN tanpa 3 digit terakhir dari run angka TERAKHIR
    ('146-56-15200' → '146-56-15'; '16Y-56E-26400' → '16Y-56E-26'). Part yang
    berbagi kunci ini = satu cluster/assembly di katalog. None bila run < 4 digit."""
    m = _ASM_LASTRUN_RE.search(pn or "")
    if not m or len(m.group(1)) <= 3:
        return None
    return (pn or "")[:m.start(1)] + m.group(1)[:-3]


def assembly_context(pn: str, file_simple: str = "") -> dict:
    """KONTEKS GRUP sebuah part — meniru cara teknisi membaca katalog: lihat
    TETANGGA fungsional, bukan cuma nama baris. Mengumpulkan part se-cluster (PN
    berbagi kunci grup, lihat _group_key) di UNIT yang sama.
    Return {induk, anggota:[nama unik…]} — `induk` = nama part head (…000) grup itu
    (mis. 'LOCK(L.H.)'), `anggota` = nama tetangga se-grup (mis. LOCK CATCH, LOCK
    BODY…) untuk memaknai part bernama ambigu ('HANDLE'). {} bila tak ada cluster."""
    ensure_index()
    key = _group_key(pn)
    if not key:
        return {}
    ku = key.upper()
    self_u = (pn or "").strip().upper()
    fs = (file_simple or "").strip()
    files = _state["excel_files"]
    ordered = ([fi for fi in files if fs and fs in (fi.get("simple_name") or "")] + files
               if fs else files)
    for fi in ordered:
        pidx = fi.get("part_number_index", {})
        members: list[tuple[str, str]] = []
        for k, idxs in pidx.items():
            if k.startswith(ku) and idxs:
                row = fi["dataframe"].iloc[idxs[0]]
                nm = " ".join(str(row["part_name"]).split()) if pd.notna(row["part_name"]) else ""
                if nm:
                    members.append((k, nm))
        if len(members) < 2:
            continue
        members.sort(key=lambda x: x[0])
        induk = next((nm for k, nm in members if k.endswith("000")), "")
        self_name = next((nm for k, nm in members if k == self_u), "")
        anggota: list[str] = []
        seen: set[str] = set()
        for k, nm in members:
            nu = nm.upper()
            if nm == induk or nm == self_name or nu in seen:
                continue
            seen.add(nu)
            anggota.append(nm)
        return {"induk": induk, "anggota": anggota[:6]}
    return {}


def assembly_parent(pn: str, file_simple: str = "") -> str:
    """Nama part INDUK ASSEMBLY (head grup, mis. 'LOCK(L.H.)') — ringkas dari
    assembly_context. '' bila tak ada. Dipertahankan untuk pemakai lama."""
    return assembly_context(pn, file_simple).get("induk", "")


def status() -> dict:
    return {
        "indexed": _state["indexed_at"] is not None,
        "indexed_at": _state["indexed_at"].isoformat() if _state["indexed_at"] else None,
        "file_count": _state["file_count"],
        "sheet_count": len(_state["excel_files"]),
        "part_count": _state["part_count"],
        "stok_entries": len(_state["stok_cache"]),
        "harga_entries": len(_state["harga_lookup"]),
        "gudang_names": _state["gudang_names"],
        "data_dir": str(get_settings().data_path),
    }


# ═══════════════════════════════════════════════════════════════════════
#  SEARCH  — mirror app.py::search_part_number
# ═══════════════════════════════════════════════════════════════════════
def is_exact_match_found(term: str) -> bool:
    """Cek apakah ada Part Number yang PERSIS sama (exact) di index lokal."""
    ensure_index()
    term_up = (term or "").strip().upper()
    if not term_up:
        return False
    for fi in _state["excel_files"]:
        if term_up in fi.get("part_number_index", {}):
            return True
    return False


def search_part_number(term: str) -> list[dict]:
    ensure_index()
    term_up = (term or "").strip().upper()
    if not term_up:
        return []

    excel_files = _state["excel_files"]
    # Stok & harga baris HANYA dari indeks Accurate (aturan pemilik 2026-07-12) —
    # snapshot di-hoist sekali per pencarian (dict in-memory, murah).
    from . import accurate as _acc
    _snap = _acc.snapshot()

    results, seen = [], set()
    for fi in excel_files:
        sn = fi["simple_name"]
        if sn in seen:
            continue
        df = fi["dataframe"]
        for indexed_pn, indices in fi.get("part_number_index", {}).items():
            if term_up in indexed_pn:
                row = df.iloc[indices[0]]
                pn_value = str(row["part_number"]).strip() if pd.notna(row["part_number"]) else "N/A"
                pn_key = pn_value.upper()
                results.append({
                    "file": sn,
                    "path": fi["relative_path"],
                    "sheet": fi["sheet"],
                    "part_number": pn_value,
                    "part_name": str(row["part_name"]) if pd.notna(row["part_name"]) else "N/A",
                    "keterangan": str(row["remark"]).strip() if pd.notna(row.get("remark")) else "",
                    "quantity": str(row["quantity"]) if pd.notna(row["quantity"]) else "N/A",
                    "stok": _acc_stok_harga(_snap, pn_key)[0],
                    "harga": _acc_stok_harga(_snap, pn_key)[1],
                    "berat": harga.weight_for(pn_key),
                    "gudang": gudang_breakdown(pn_key),
                    "excel_row": int(indices[0]) + 2,
                })
                seen.add(sn)
                break
    return results


def search_exact_pns(pns) -> list[dict]:
    """Ambil baris untuk Part Number PERSIS (exact match) dari sekumpulan PN.
    CEPAT: lookup dict O(1) per sheet (bukan scan substring spt search_part_number).
    Satu baris per (PN, unit). Format baris sama dengan search_part_number."""
    ensure_index()
    want = {(p or "").strip().upper() for p in pns}
    want.discard("")
    if not want:
        return []

    excel_files = _state["excel_files"]
    # Stok & harga baris HANYA dari indeks Accurate (aturan pemilik 2026-07-12) —
    # snapshot di-hoist sekali per pencarian (dict in-memory, murah).
    from . import accurate as _acc
    _snap = _acc.snapshot()

    results, seen = [], set()
    for fi in excel_files:
        sn = fi["simple_name"]
        pidx = fi.get("part_number_index", {})
        if not pidx:
            continue
        df = fi["dataframe"]
        for au in want:
            indices = pidx.get(au)
            if not indices:
                continue
            key = (au, sn)
            if key in seen:
                continue
            seen.add(key)
            row = df.iloc[indices[0]]
            pn_value = str(row["part_number"]).strip() if pd.notna(row["part_number"]) else "N/A"
            pn_key = pn_value.upper()
            results.append({
                "file": sn,
                "path": fi["relative_path"],
                "sheet": fi["sheet"],
                "part_number": pn_value,
                "part_name": str(row["part_name"]) if pd.notna(row["part_name"]) else "N/A",
                "keterangan": str(row["remark"]).strip() if pd.notna(row.get("remark")) else "",
                "quantity": str(row["quantity"]) if pd.notna(row["quantity"]) else "N/A",
                "stok": _acc_stok_harga(_snap, pn_key)[0],
                "harga": _acc_stok_harga(_snap, pn_key)[1],
                "berat": harga.weight_for(pn_key),
                "gudang": gudang_breakdown(pn_key),
                "excel_row": int(indices[0]) + 2,
            })
    return results


# Cache (pn, name) seluruh part — utk scan kelas part tertentu (mis. transmisi
# assy) tanpa men-scan ulang tiap query. Di-refresh saat index dibangun ulang.
_ALLPARTS_CACHE: dict = {"at": None, "rows": []}


def all_parts_min() -> list[tuple[str, str]]:
    """Daftar (PART_NUMBER_UPPER, nama_part) UNIK utk SELURUH katalog — satu entri
    per PN (nama dari kemunculan pertama). Ringan & di-cache per build index, dipakai
    untuk menyaring kelas part tertentu (mis. semua transmisi/gearbox assy) tanpa
    cap hasil pencarian biasa."""
    ensure_index()
    at = _state["indexed_at"]
    c = _ALLPARTS_CACHE
    if c["at"] != at:
        out: dict[str, str] = {}
        for fi in _state["excel_files"]:
            df = fi.get("dataframe")
            if df is None:
                continue
            for pn_up, idxs in fi.get("part_number_index", {}).items():
                if pn_up in out or not idxs:
                    continue
                try:
                    nm = df.iloc[idxs[0]]["part_name"]
                    out[pn_up] = "" if pd.isna(nm) else str(nm)
                except Exception:
                    out[pn_up] = ""
        c["at"] = at
        c["rows"] = list(out.items())
    return c["rows"]


def search_part_name(term: str) -> list[dict]:
    """Cari berdasarkan Part Name — mirror app.py::search_part_name."""
    ensure_index()
    term_clean = (term or "").strip()
    term_up = term_clean.upper()
    if not term_up:
        return []

    excel_files = _state["excel_files"]
    # Stok & harga baris HANYA dari indeks Accurate (aturan pemilik 2026-07-12) —
    # snapshot di-hoist sekali per pencarian (dict in-memory, murah).
    from . import accurate as _acc
    _snap = _acc.snapshot()
    search_keywords = [term_clean.lower()]
    # Tambah varian dgn singkatan dinormalkan (mis. 'kabin assy' → 'kabin assembly').
    norm = _normalize_abbr(term_clean).lower()
    if norm and norm != term_clean.lower():
        search_keywords.append(norm)

    results: list[dict] = []
    for fi in excel_files:
        df = fi["dataframe"]
        pni = fi.get("part_name_index", {})
        matching_indices: set[int] = set()

        for keyword in search_keywords:
            kw_up = keyword.upper()
            search_words = kw_up.split()
            for word in pni.keys():
                for sw in search_words:
                    if sw in word or word in sw:
                        matching_indices.update(pni[word])
            # Fallback keyword SANGAT pendek (1-2 huruf) yang TIDAK terindeks (index
            # hanya menyimpan kata >2 huruf). Kata ≥3 huruf — termasuk istilah China
            # 3-karakter spt '变速器'/'变速箱' — sudah tertangani word-loop di atas, jadi
            # JANGAN jalankan fallback untuknya. Pakai str.contains (vektor) BUKAN
            # df.iterrows() yang O(baris) & sangat lambat (penyebab query transmisi ~40s).
            if not matching_indices and len(kw_up) < 3:
                pn_up = df["part_name"].fillna("").astype(str).str.upper()
                rm_up = df["remark"].fillna("").astype(str).str.upper()
                mask = pn_up.str.contains(kw_up, regex=False) | rm_up.str.contains(kw_up, regex=False)
                matching_indices.update(int(i) for i in df.index[mask])

        if not matching_indices:
            continue
        # Ambil kolom SEKALI per file (batch) — df.iloc[idx] per baris memicu
        # fast_xs pandas per baris (terukur ±37 dtk utk 61 ribu baris pada satu
        # pencarian 'lampu'); tolist() kolom sub-frame memangkasnya drastis.
        idxs = sorted(matching_indices)
        sub = df.iloc[idxs]
        col_pn = sub["part_number"].tolist()
        col_nm = sub["part_name"].tolist()
        col_qty = sub["quantity"].tolist()
        col_rm = sub["remark"].tolist()
        for pos, idx in enumerate(idxs):
            pname = str(col_nm[pos]) if pd.notna(col_nm[pos]) else ""
            remark = str(col_rm[pos]).strip() if pd.notna(col_rm[pos]) else ""
            # Cocokkan ke nama ATAU keterangan (kata kunci bisa berasal dari remark).
            haystack = f"{pname} {remark}".upper()
            if not any(_phrase_or_allwords(haystack, kw.upper()) for kw in search_keywords):
                continue
            pn_value = str(col_pn[pos]).strip() if pd.notna(col_pn[pos]) else "N/A"
            pn_key = pn_value.upper()
            results.append({
                "file": fi["simple_name"],
                "path": fi["relative_path"],
                "sheet": fi["sheet"],
                "part_number": pn_value,
                "part_name": pname if pname else "N/A",
                "keterangan": remark,
                "quantity": str(col_qty[pos]) if pd.notna(col_qty[pos]) else "N/A",
                "stok": _acc_stok_harga(_snap, pn_key)[0],
                "harga": _acc_stok_harga(_snap, pn_key)[1],
                "berat": harga.weight_for(pn_key),
                "gudang": gudang_breakdown(pn_key),
                "excel_row": int(idx) + 2,
            })
    return results


# ═══════════════════════════════════════════════════════════════════════
#  KOREKSI SALAH KETIK (fuzzy) + SARAN "MUNGKIN MAKSUD ANDA"
# ═══════════════════════════════════════════════════════════════════════
def _word_known(word_up: str, vocab: set) -> bool:
    """Kata dianggap dikenal bila persis ada di kosakata, atau menjadi substring
    kata katalog mana pun (pencarian nama memang berbasis substring)."""
    if word_up in vocab:
        return True
    return any(word_up in vw for vw in vocab)


def correct_typos(term: str) -> tuple[str, list[tuple[str, str]]]:
    """Perbaiki salah ketik tiap KATA pada `term` terhadap kosakata nama part.
    Hanya mengganti kata yang TIDAK dikenal dan punya padanan sangat mirip
    (mis. 'injektor'→'injector', 'radiato'→'radiator'). Kata pendek (≤3 huruf)
    dan yang mengandung angka (Part Number) dilewati.
    Return (term_terkoreksi, [(asli, koreksi), ...])."""
    ensure_index()
    vocab = _state.get("name_vocab") or set()
    vocab_list = _state.get("name_vocab_list") or []
    if not vocab or not (term or "").strip():
        return term, []
    out_words: list[str] = []
    corrections: list[tuple[str, str]] = []
    for w in term.split():
        wu = w.upper()
        if len(wu) <= 3 or any(ch.isdigit() for ch in wu) or _word_known(wu, vocab):
            out_words.append(w)
            continue
        match = difflib.get_close_matches(wu, vocab_list, n=1, cutoff=0.82)
        if match and match[0] != wu:
            out_words.append(match[0])
            corrections.append((w, match[0].title()))
        else:
            out_words.append(w)
    return " ".join(out_words), corrections


def suggest_names(term: str, limit: int = 6) -> list[dict]:
    """Saran 'mungkin maksud Anda' saat pencarian 0 hasil: cari kata katalog yang
    mirip dengan kata pada `term`, lalu kembalikan beberapa part yang mengandung
    kata itu. Return [{'part_number','part_name'}]."""
    ensure_index()
    vocab_list = _state.get("name_vocab_list") or []
    if not vocab_list or not (term or "").strip():
        return []
    close_words: set[str] = set()
    for w in term.upper().split():
        if len(w) <= 3 or any(ch.isdigit() for ch in w):
            continue
        for m in difflib.get_close_matches(w, vocab_list, n=3, cutoff=0.7):
            close_words.add(m)
    if not close_words:
        return []
    out: list[dict] = []
    seen: set = set()
    for fi in _state["excel_files"]:
        df = fi["dataframe"]
        pni = fi.get("part_name_index", {})
        idxs: set[int] = set()
        for cw in close_words:
            idxs.update(pni.get(cw, []))
        for idx in idxs:
            row = df.iloc[idx]
            nm = str(row["part_name"]).strip() if pd.notna(row["part_name"]) else ""
            pn = str(row["part_number"]).strip() if pd.notna(row["part_number"]) else ""
            key = (pn.upper(), nm.upper())
            if nm and key not in seen:
                seen.add(key)
                out.append({"part_number": pn, "part_name": nm})
                if len(out) >= limit:
                    return out
    return out


# ═══════════════════════════════════════════════════════════════════════
#  PENCARIAN PN "PEMAAF" — varian bersih, basis-PN, bebas-pemisah, fuzzy
#  (dipetakan dari pola nyata log Pencarian Nihil: user menempel PN dengan
#  suffix qty/halaman 'WG…0011/7', PN varian panjang 'WG…0223TQF717' yang
#  basisnya ada di katalog, atau PN salah satu digit yang dicoba berulang.)
# ═══════════════════════════════════════════════════════════════════════
_PN_TOKEN_RE = re.compile(r"[A-Z0-9][A-Z0-9.\-]{4,}", re.IGNORECASE)


def looks_like_pn(token: str) -> bool:
    """Heuristik token Part Number: cukup panjang, mayoritas alfanumerik,
    dan memuat ≥3 digit (nama part hampir tak pernah begitu)."""
    t = (token or "").strip().upper()
    return (len(t) >= 6 and " " not in t
            and sum(ch.isdigit() for ch in t) >= 3
            and all(ch.isalnum() or ch in ".-/" for ch in t))


def pn_tokens(query: str) -> list[str]:
    """Token PN-like pada query (untuk deteksi multi-PN dalam satu pertanyaan)."""
    out: list[str] = []
    for m in _PN_TOKEN_RE.findall((query or "").upper()):
        if looks_like_pn(m) and m not in out:
            out.append(m)
    return out


def pn_query_variants(term: str) -> list[str]:
    """Token PN BERSIH dari query, urut kemunculan — tanpa term asli utuh.
    Suffix qty/halaman otomatis lepas krn '/' dan '+' bukan karakter token:
    'WG9925680011/7' → ['WG9925680011']; 'WG90003601 1/3' → ['WG90003601'];
    'cari WG9925470433+01 dong' → ['WG9925470433']. '-' TIDAK memotong
    (banyak PN katalog memang mengandung '-')."""
    up = (term or "").strip().upper()
    return [t for t in pn_tokens(up) if t != up]


# flat(PN tanpa pemisah) → daftar PN mentah — di-cache per build index.
_PN_FLAT_CACHE: dict = {"at": None, "map": {}}


def _pn_flat(s: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", (s or "").upper())


def _pn_flat_map() -> dict[str, list[str]]:
    ensure_index()
    at = _state["indexed_at"]
    c = _PN_FLAT_CACHE
    if c["at"] != at:
        m: dict[str, list[str]] = {}
        for pn, _nm in all_parts_min():
            m.setdefault(_pn_flat(pn), []).append(pn)
        c["at"] = at
        c["map"] = m
    return c["map"]


def rows_for_pns(pns) -> dict[str, dict]:
    """{PN_ASLI: baris inventori} untuk daftar PN — PEMAAF terhadap SUFFIX VARIAN EPC.

    EPC kerap memberi PN berakhiran varian pemasok/halaman ('WG9525160004/2',
    'WG9525470121+003/1') sementara indeks stok/harga kita menyimpan PN dasarnya
    ('WG9525160004'). Pencocokan PERSIS meleset → part tampil 'stok —' padahal
    barangnya ADA (kasus nyata: kampas kopling WG9525160004/2, stok 11 pc).
    Urutan: cocok PERSIS → varian bersih (suffix dibuang) → tanpa tanda pemisah.
    Kunci hasil = PN ASLI yang diminta (pemanggil tak perlu tahu bentuk dasarnya)."""
    want = [str(p or "").strip().upper() for p in (pns or [])]
    want = [p for p in want if p]
    if not want:
        return {}

    # 1) Cocok PERSIS.
    out: dict[str, dict] = {}
    exact: dict[str, dict] = {}
    for r in search_exact_pns(want):
        pn = (r.get("part_number") or "").upper()
        if pn and pn not in exact:
            exact[pn] = r
    sisa = []
    for p in want:
        if p in exact:
            out[p] = exact[p]
        else:
            sisa.append(p)
    if not sisa:
        return out

    # 2) Varian bersih: suffix qty/halaman/varian dibuang ('WG…/2' → 'WG…').
    kandidat: dict[str, list[str]] = {}   # PN asli → varian yang dicoba
    for p in sisa:
        kandidat[p] = [v for v in pn_query_variants(p) if v and v != p]
    lookup = {v for vs in kandidat.values() for v in vs}
    if lookup:
        base: dict[str, dict] = {}
        for r in search_exact_pns(sorted(lookup)):
            pn = (r.get("part_number") or "").upper()
            if pn and pn not in base:
                base[pn] = r
        for p, vs in list(kandidat.items()):
            hit = next((base[v] for v in vs if v in base), None)
            if hit:
                out[p] = hit
                sisa.remove(p)
    if not sisa:
        return out

    # 3) Tanpa tanda pemisah ('202V091007926' = '202V09100-7926').
    fmap = _pn_flat_map()
    for p in sisa:
        tok = (pn_query_variants(p) or [p])[0]
        raws = fmap.get(_pn_flat(tok))
        if not raws:
            continue
        rows = search_exact_pns(raws[:1])
        if rows:
            out[p] = rows[0]
    return out


def smart_pn_search(term: str) -> tuple[list[dict], Optional[str]]:
    """Fallback pintar saat pencarian PN biasa nihil. Urutan ikhtiar:
      1) varian bersih (suffix qty/halaman dibuang) → substring biasa;
      2) padanan BEBAS-PEMISAH persis ('202V091007926' = '202V09100-7926');
      3) BASIS-PN: PN katalog terpanjang yang menjadi AWALAN token query
         (PN varian panjang, mis. 'WG9525930223TQF717' → 'WG9525930223').
    Return (rows, catatan utk user) — ([], None) bila tetap nihil."""
    up = (term or "").strip().upper()
    if not up:
        return [], None
    toks = pn_query_variants(up)
    for v in toks:
        rows = search_part_number(v)
        if rows:
            return rows, f"PN pada query dibersihkan/diekstrak menjadi '{v}' (suffix qty/halaman dibuang)."
    tok = toks[0] if toks else re.split(r"[\s/+]+", up)[0]
    flat = _pn_flat(tok)
    if not looks_like_pn(flat):
        return [], None
    fmap = _pn_flat_map()
    raws = fmap.get(flat)
    if raws:
        rows = search_exact_pns(raws)
        if rows:
            return rows, f"'{up}' dicocokkan tanpa memedulikan tanda pemisah ke PN katalog '{raws[0]}'."
    # Basis-PN: prefix terpanjang yang terdaftar (min 8 char agar tak asal cocok).
    best: list[str] = []
    best_len = 7
    for f, raws_ in fmap.items():
        if len(f) > best_len and flat.startswith(f):
            best, best_len = raws_, len(f)
    if best:
        rows = search_exact_pns(best)
        if rows:
            return rows, (
                f"'{up}' tidak terdaftar persis, tetapi BASIS PN-nya '{best[0]}' ada di "
                "katalog (sisanya kemungkinan kode varian/suffix). Sampaikan asumsi ini ke user."
            )
    return [], None


def suggest_pns(term: str, limit: int = 5) -> list[dict]:
    """Saran 'mungkin maksud Anda' utk PN yang 0 hasil: PN katalog paling mirip
    (selisih 1-2 karakter — kasus nyata: user kurang/tertukar satu digit).
    Return [{'part_number','part_name'}]."""
    up = (term or "").strip().upper()
    toks = pn_tokens(up)
    tok = toks[0] if toks else up
    flat = _pn_flat(tok)
    if not looks_like_pn(flat):
        return []
    rows = all_parts_min()
    # Bucket dulu (prefix-4 sama ATAU panjang ±1) supaya difflib murah & fokus.
    pool: dict[str, tuple[str, str]] = {}
    for pn, nm in rows:
        f = _pn_flat(pn)
        if f[:4] == flat[:4] or abs(len(f) - len(flat)) <= 1:
            pool.setdefault(f, (pn, nm))
    close = difflib.get_close_matches(flat, list(pool.keys()), n=limit, cutoff=0.85)
    return [{"part_number": pool[f][0], "part_name": pool[f][1]} for f in close if f != flat]