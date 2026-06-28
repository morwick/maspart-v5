"""
Referensi filter SHANTUI (alat berat) — dibaca dari
`data/manuals/PART FILTER SHANTUI.xlsx`.

Layout file: 4 sheet = jenis alat (EXCAVATOR, BULDOZER, ROLLER, GRADER). Tiap
sheet dibagi 2 blok BERDAMPINGAN: HYDRAULIC FILTER (kiri) & ENGINE FILTER (kanan).
Tiap blok dikelompokkan per MODEL unit (mis. SD22, SE215, DH08, SR10, SG15-B6),
dengan kolom No / Part Name / Part Number + beberapa kolom cross-reference merek
lain (Fleetguard, Donaldson, Weichai, HIFI, Sakura, Baldwin, Cummins, dll).

Di-cache di memori berdasarkan mtime file → bila admin mengganti Excel-nya,
otomatis dibaca ulang tanpa restart.
"""
from __future__ import annotations

import re
import threading
from pathlib import Path

from ..core.config import get_settings

_FILE_NAME = "PART FILTER SHANTUI.xlsx"

_lock = threading.Lock()
_cache: dict = {"mtime": None, "rows": []}

# Istilah lapangan Indonesia → kata kunci nama filter (Inggris) di katalog.
_FILTER_SYN = {
    "oli": "oil", "minyak": "oil", "pelumas": "oil",
    "solar": "fuel", "bbm": "fuel", "bahan bakar": "fuel", "bensin": "fuel",
    "udara": "air", "hawa": "air",
    "hidrolik": "hydraulic", "hidraulik": "hydraulic",
    "transmisi": "transmission", "perseneling": "transmission",
    "ac": "air conditioning", "kabin": "air conditioning",
    "pemisah air": "water", "water separator": "water",
}


def _file() -> Path | None:
    p = get_settings().data_path / "manuals" / _FILE_NAME
    try:
        return p if p.is_file() else None
    except OSError:
        return None


def _cell(df, r: int, c: int) -> str:
    import pandas as pd
    try:
        v = df.iat[r, c]
        return "" if pd.isna(v) else str(v).strip()
    except Exception:
        return ""


def _parse(path: Path) -> list[dict]:
    """Ekstrak semua baris filter → list dict {alat, jenis, model, part_name,
    part_number, cross_reference[]}."""
    import pandas as pd

    xls = pd.ExcelFile(path, engine="openpyxl")
    rows: list[dict] = []
    for sheet in xls.sheet_names:
        df = pd.read_excel(xls, sheet_name=sheet, header=None, dtype=str)
        nrow, ncol = df.shape
        # Kolom awal blok ENGINE (pemisah kiri-hidrolik / kanan-mesin).
        eng_col = next((c for c in range(ncol) if "ENGINE" in _cell(df, 0, c).upper()), None)
        blocks = [("hydraulic", 0, eng_col if eng_col is not None else ncol)]
        if eng_col is not None:
            blocks.append(("engine", eng_col, ncol))
        for ftype, c0, c1 in blocks:
            current_model = ""
            name_c = pn_c = None
            cross_cs: list[int] = []
            mode = None
            for r in range(nrow):
                first = _cell(df, r, c0)
                up = first.upper()
                if up in ("HYDRAULIC FILTER", "ENGINE FILTER", ""):
                    continue
                if up == "NO":  # header sub-tabel
                    name_c, pn_c = c0 + 1, c0 + 2
                    cross_cs = [
                        c for c in range(c0 + 3, c1)
                        if any(_cell(df, rr, c) for rr in range(r + 1, min(r + 12, nrow)))
                    ]
                    mode = "data"
                    continue
                if re.fullmatch(r"\d+", first):  # baris data (No = angka)
                    if mode != "data":
                        continue
                    name = _cell(df, r, name_c)
                    pn = _cell(df, r, pn_c)
                    if not (name or pn):
                        continue
                    cross = [_cell(df, r, c) for c in cross_cs if _cell(df, r, c)]
                    rows.append({
                        "alat": sheet,
                        "jenis": ftype,
                        "model": current_model,
                        "part_name": name,
                        "part_number": pn,
                        "cross_reference": cross,
                    })
                elif up not in ("PART NAME", "PART NUMBER"):
                    current_model = first  # label model unit
                    mode = "expect"
    return rows


def _load() -> list[dict]:
    f = _file()
    if not f:
        return []
    try:
        mt = f.stat().st_mtime
    except OSError:
        return []
    with _lock:
        if _cache["mtime"] == mt and _cache["rows"]:
            return _cache["rows"]
    try:
        rows = _parse(f)
    except Exception:
        rows = []
    with _lock:
        _cache["mtime"] = mt
        _cache["rows"] = rows
    return rows


def available() -> bool:
    return bool(_load())


def _norm(s: str) -> str:
    return re.sub(r"[\s_\-()（）]", "", (s or "")).upper()


def _expand_terms(q: str) -> list[str]:
    ql = (q or "").lower()
    terms = [ql]
    for k, v in _FILTER_SYN.items():
        if k in ql and v not in terms:
            terms.append(v)
    return [t for t in terms if t]


def search(unit: str = "", query: str = "") -> list[dict]:
    """Cari filter berdasar unit/model/jenis-alat (opsional) + kata kunci (opsional)."""
    rows = _load()
    u = _norm(unit)
    terms = _expand_terms(query) if query else []
    out: list[dict] = []
    for r in rows:
        if u and u not in _norm(r["model"]) and u not in _norm(r["alat"]):
            continue
        if terms:
            hay = " ".join([
                r["part_name"], r["jenis"], r["part_number"], " ".join(r["cross_reference"]),
            ]).lower()
            if not any(t in hay for t in terms):
                continue
        out.append(r)
    return out


def units() -> list[str]:
    """Daftar model unit yang punya data filter (untuk konteks/awareness)."""
    seen, res = set(), []
    for r in _load():
        m = (r.get("model") or "").strip()
        if m and m not in seen:
            seen.add(m)
            res.append(m)
    return res
