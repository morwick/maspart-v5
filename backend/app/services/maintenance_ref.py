"""
Jadwal perawatan berkala (periodic maintenance table) alat berat SHANTUI —
dibaca dari `data/manuals/shantui Loader maintenance project periodic table.xlsx`.

Layout file: tiap SHEET = 1 model unit (dozer SD16/SD22/SD32; loader L36-B5,
L36-B3, L39-B3, L55-B5, L56-B5, L58K-B5, L68K-B5). Tiap sheet berisi item servis
(filter, oli, coolant, gemuk) dikelompokkan per SISTEM (MOTOR, Gearbox &
konverter, SISTEMA HIDRAULICO, Drive axle, Braking system), dengan kolom:

    Servicio | N/P SHANTUI | N/P EQUIVALENTE | N/P FLEETGUARD | CANTIDAD |
    Intervalo en horas | 50 | 100 | 250 | 500 | 750 | 1000 | ... | 2000

Kolom interval BERVARIASI antar sheet (ada yang mulai 50h, ada 100h) → dibaca
dinamis. Tanda `X` pada kolom interval = part diganti pada servis jam itu.

Di-cache di memori berdasarkan mtime file → bila admin mengganti Excel-nya,
otomatis dibaca ulang tanpa restart (sama pola dengan `filter_ref`).
"""
from __future__ import annotations

import re
import threading
from pathlib import Path

from ..core.config import get_settings

_FILE_NAME = "shantui Loader maintenance project periodic table.xlsx"

_lock = threading.Lock()
_cache: dict = {"mtime": None, "rows": []}

# Istilah lapangan (Indonesia/Inggris) → daftar kata kunci padanan di tabel.
# Nama servis di file CAMPUR: loader = Inggris ('Engine oil filter'), dozer =
# Spanyol + Mandarin ('Filtro de aceite 机油滤') → tiap sinonim memetakan ke
# padanan Inggris + Spanyol + Mandarin sekaligus agar pencarian memaafkan.
_SVC_SYN: dict[str, list[str]] = {
    "oli": ["oil", "aceite", "机油"], "minyak": ["oil", "aceite", "机油"],
    "pelumas": ["oil", "aceite", "机油"],
    "solar": ["fuel", "combustible", "燃油", "柴滤"],
    "bbm": ["fuel", "燃油", "柴滤"], "bahan bakar": ["fuel", "燃油", "柴滤"],
    "udara": ["air", "aire", "空滤", "空气"], "hawa": ["air", "aire", "空气"],
    "hidrolik": ["hydraulic", "hidraulic", "液压"],
    "hidraulik": ["hydraulic", "hidraulic", "液压"],
    "transmisi": ["transmission", "变速", "convertidor", "converter", "变矩"],
    "perseneling": ["transmission", "变速"],
    "gearbox": ["gearbox", "transmission", "变速"],
    "konverter": ["converter", "convertidor", "变矩"],
    "coolant": ["coolant", "antifreeze", "refrigerante", "冷却"],
    "pendingin": ["coolant", "refrigerante", "冷却"],
    "radiator": ["coolant", "refrigerante", "冷却"],
    "antibeku": ["antifreeze", "冷却"],
    "gardan": ["axle", "eje", "桥"], "as roda": ["axle", "eje", "桥"],
    "rem": ["brake", "freno", "制动"], "brake": ["brake", "freno", "制动"],
    "gemuk": ["grease", "grasa", "润滑脂"], "grease": ["grease", "grasa", "润滑脂"],
    "filter": ["filter", "filtro", "滤"], "saringan": ["filter", "filtro", "滤"],
    "water separator": ["water", "agua", "水"],
    "pemisah air": ["water", "agua", "水"],
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


def _model_from(sheet: str, modelo_row: str) -> str:
    """Kode model bersih (mis. 'SD22', 'L36-B5') dari baris 'MODELO : ...' atau
    nama sheet. Buang embel-embel Mandarin & tanda kurung."""
    src = ""
    m = re.search(r"MODELO\s*[:：]?\s*(.+)", modelo_row or "", re.IGNORECASE)
    if m:
        src = m.group(1)
    if not src:
        src = sheet or ""
    # Ambil rangkaian awal alfanumerik/dash (kode model) sebelum karakter non-ASCII.
    m2 = re.match(r"\s*([A-Za-z0-9][A-Za-z0-9\-]*)", src)
    return (m2.group(1) if m2 else src).strip().upper()


def _hours(label: str) -> int | None:
    """'50' / '50h' / '1000' → int; sel non-jam → None."""
    m = re.fullmatch(r"\s*(\d+)\s*[hH]?\s*", label or "")
    return int(m.group(1)) if m else None


def _parse(path: Path) -> list[dict]:
    """Ekstrak semua item servis → list dict
    {model, unit, sistem, nama, part_number, qty, ganti_jam[list int]}."""
    import pandas as pd

    xls = pd.ExcelFile(path, engine="openpyxl")
    rows: list[dict] = []
    for sheet in xls.sheet_names:
        df = pd.read_excel(xls, sheet_name=sheet, header=None, dtype=str)
        nrow, ncol = df.shape

        # Metadata unit (baris atas).
        unit_desc = ""
        modelo_row = ""
        hdr = None
        for r in range(min(nrow, 15)):
            first = _cell(df, r, 0)
            up = first.upper()
            if up.startswith("UNIDAD"):
                unit_desc = re.sub(r"^UNIDAD\s*[:：]\s*", "", first).strip()
            elif up.startswith("MODELO"):
                modelo_row = first
            elif "SERVICIOS" in up:
                hdr = r
        if hdr is None:
            continue
        model = _model_from(sheet, modelo_row)

        # Kolom interval jam: kolom setelah 'Intervalo' yang header-nya angka.
        interval_cols: list[tuple[int, int]] = []  # (col_idx, jam)
        for c in range(ncol):
            j = _hours(_cell(df, hdr, c))
            if j is not None:
                interval_cols.append((c, j))

        sistem = ""
        for r in range(hdr + 1, nrow):
            nama = _cell(df, r, 0)
            if not nama:
                continue
            # Baris catatan kaki → berhenti.
            if nama.lower().startswith(("note:", "nota:", "备注")):
                continue
            pn = _cell(df, r, 1)
            qty = _cell(df, r, 4)
            # Baris SISTEM = hanya sel pertama terisi (PN & qty kosong).
            if not pn and not qty:
                sistem = nama
                continue
            ganti = [j for c, j in interval_cols if _cell(df, r, c)]
            rows.append({
                "model": model,
                "unit": unit_desc,
                "sistem": sistem,
                "nama": nama,
                "part_number": pn,
                "qty": qty,
                "ganti_jam": ganti,
            })
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
    terms = [ql] if ql else []
    for k, vs in _SVC_SYN.items():
        # Batas kata (hindari 'oli' cocok di dalam 'hidr-oli-k'). Karakter Mandarin
        # bukan [a-z] → batas otomatis terpenuhi.
        if re.search(r"(?<![a-z])" + re.escape(k) + r"(?![a-z])", ql):
            for v in vs:
                if v not in terms:
                    terms.append(v)
    return [t for t in terms if t]


def models() -> list[str]:
    """Daftar kode model unit yang punya jadwal perawatan (untuk kesadaran)."""
    seen, res = set(), []
    for r in _load():
        m = (r.get("model") or "").strip()
        if m and m not in seen:
            seen.add(m)
            res.append(m)
    return res


def search(model: str = "", query: str = "", jam: int | None = None) -> list[dict]:
    """Item perawatan tersaring: per model (opsional), kata kunci part (opsional),
    dan interval jam servis (opsional — hanya item yang diganti pada jam itu)."""
    rows = _load()
    mn = _norm(model)
    terms = _expand_terms(query) if query else []
    out: list[dict] = []
    for r in rows:
        if mn and mn not in _norm(r["model"]):
            continue
        if jam is not None and jam not in (r.get("ganti_jam") or []):
            continue
        if terms:
            hay = " ".join([r["nama"], r["sistem"], r["part_number"]]).lower()
            if not any(t in hay for t in terms):
                continue
        out.append(r)
    return out
