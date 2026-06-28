"""
Service: Populasi Unit — tabel dari populasi.xlsx (semua sheet digabung),
dengan pencarian kata kunci + filter kolom. Decoupled dari Streamlit.

Sumber data: Supabase Storage `data/populasi.xlsx` (fallback folder lokal
data/populasi/). Mirror app.py::_load_populasi_data + render_populasi_tab.
"""
from __future__ import annotations

import io
import threading

import pandas as pd

from ..core.config import get_settings
from .supabase_client import download_storage_object

_EXCEL_EXT = (".xlsx", ".xls", ".xlsm")
# Kolom kandidat filter dropdown (yang ada saja, maks 4) — sama dengan Streamlit.
_CANDIDATE_FILTERS = ["MODEL", "JENIS", "TIPE UNIT", "LOKASI KERJA", "TAHUN", "Euro"]

_lock = threading.Lock()
_state: dict = {"df": None}


def _read_sheets(file_bytes: bytes) -> list[pd.DataFrame]:
    frames = []
    xl = pd.ExcelFile(io.BytesIO(file_bytes), engine="openpyxl")
    for sheet in xl.sheet_names:
        df = pd.read_excel(xl, sheet_name=sheet, dtype=str)
        df.columns = [str(c).strip() for c in df.columns]
        frames.append(df)
    return frames


def refresh() -> pd.DataFrame:
    frames: list[pd.DataFrame] = []

    # 1. Supabase Storage
    b = download_storage_object("populasi.xlsx")
    if b:
        try:
            frames.extend(_read_sheets(b))
        except Exception:
            pass

    # 2. Fallback folder lokal
    if not frames:
        folder = get_settings().data_path / "populasi"
        if folder.exists():
            for fp in sorted(folder.iterdir()):
                if fp.suffix.lower() in _EXCEL_EXT:
                    try:
                        frames.extend(_read_sheets(fp.read_bytes()))
                    except Exception:
                        continue

    combined = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    with _lock:
        _state["df"] = combined
    return combined


def _ensure() -> pd.DataFrame:
    if _state["df"] is None:
        refresh()
    return _state["df"]


def columns() -> list[str]:
    return [str(c) for c in _ensure().columns]


def filter_options() -> dict[str, list[str]]:
    df = _ensure()
    present = [c for c in _CANDIDATE_FILTERS if c in df.columns][:4]
    out: dict[str, list[str]] = {}
    for c in present:
        vals = sorted({v.strip() for v in df[c].dropna().astype(str) if v.strip()})
        out[c] = vals
    return out


def query(q: str = "", filters: dict | None = None) -> pd.DataFrame:
    df = _ensure()
    if df.empty:
        return df
    mask = pd.Series([True] * len(df), index=df.index)

    if q and q.strip():
        kw = q.strip().upper()
        m = pd.Series([False] * len(df), index=df.index)
        for col in df.columns:
            m |= df[col].astype(str).str.upper().str.contains(kw, na=False, regex=False)
        mask &= m

    for col, val in (filters or {}).items():
        if col in df.columns and val and val != "Semua":
            mask &= (df[col].astype(str) == str(val))

    return df[mask].reset_index(drop=True)


def sort_df(df: pd.DataFrame, sort: str, direction: str = "asc") -> pd.DataFrame:
    """Urutkan DataFrame berdasarkan kolom `sort`. Kolom yang seluruhnya angka
    (mis. NO, TAHUN) diurutkan numerik; selain itu sebagai teks (tidak
    case-sensitive). Stabil, sehingga urutan asli dipertahankan untuk nilai sama."""
    if not sort or sort not in df.columns or df.empty:
        return df
    ascending = (direction or "asc").lower() != "desc"
    col = df[sort]
    numeric = pd.to_numeric(col, errors="coerce")
    if numeric.notna().all():
        order = numeric.sort_values(ascending=ascending, kind="stable").index
    else:
        order = (
            col.astype(str).str.strip().str.lower()
            .sort_values(ascending=ascending, kind="stable").index
        )
    return df.loc[order].reset_index(drop=True)


def to_excel_bytes(df: pd.DataFrame) -> bytes:
    buf = io.BytesIO()
    df.to_excel(buf, index=False, engine="openpyxl")
    return buf.getvalue()


def total_count() -> int:
    return len(_ensure())


def _multi_query(df: pd.DataFrame, q: str) -> pd.DataFrame:
    """Seperti query() tetapi q boleh berisi BEBERAPA kata — SEMUA kata harus
    muncul (di kolom mana pun). Mis. 'NX360 2022' → unit NX360 tahun 2022."""
    if not q or not q.strip() or df.empty:
        return df
    mask = pd.Series([True] * len(df), index=df.index)
    for w in q.upper().split():
        m = pd.Series([False] * len(df), index=df.index)
        for col in df.columns:
            m |= df[col].astype(str).str.upper().str.contains(w, na=False, regex=False)
        mask &= m
    return df[mask].reset_index(drop=True)


def search_summary(q: str = "", limit: int = 15) -> dict:
    """Ringkasan populasi unit untuk Asisten AI: total, jumlah cocok, rincian
    jumlah per MODEL/TIPE, dan beberapa contoh baris. Return plain dict (hemat
    token, tidak membocorkan seluruh tabel)."""
    df = _ensure()
    cols = [str(c) for c in df.columns]
    if df is None or df.empty:
        return {"available": False, "kolom": cols, "total_semua_unit": 0}

    res = _multi_query(df, q)

    # Rincian jumlah per nilai pada kolom pengelompokan pertama yang tersedia.
    breakdown: dict[str, int] = {}
    breakdown_col = None
    for key in ("MODEL", "TIPE UNIT", "JENIS", "TIPE", "Euro"):
        if key in res.columns:
            vc = (
                res[key].astype(str).str.strip().replace("", pd.NA).dropna().value_counts()
            )
            breakdown = {str(k): int(v) for k, v in vc.head(25).items()}
            breakdown_col = key
            break

    rows = res.head(limit).fillna("").astype(str).to_dict("records")
    return {
        "available": True,
        "kolom": cols,
        "total_semua_unit": len(df),
        "jumlah_cocok": len(res),
        "ditampilkan": len(rows),
        "ringkasan_kolom": breakdown_col,
        "jumlah_per_nilai": breakdown,
        "contoh_baris": rows,
        "filter_tersedia": filter_options(),
    }
