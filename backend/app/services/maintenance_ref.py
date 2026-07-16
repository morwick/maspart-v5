"""
Jadwal perawatan berkala (periodic maintenance table) alat berat SHANTUI —
dibaca dari SEMUA file maintenance di `data/manuals/*.xlsx` (nama memuat
"maintenance"/"periodic"/"cycle table"; `PART FILTER SHANTUI.xlsx` dikecualikan,
itu milik `filter_ref`).

Tiap SHEET = 1 model unit. Jenis alat: dozer, loader, excavator, grader, roller.
Tiap sheet berisi item servis (filter, oli, coolant, gemuk) dikelompokkan per
SISTEM (mesin, transmisi/konverter, hidrolik, gardan, rem) + Nomor Part Shantui,
kuantitas, dan tanda (X/√) pada kolom interval jam kerja (50–3000).

⚠️ Data CAMPUR 3 template sheet (semua punya sel header 'N/P SHANTUI' → jangkar):
  A. Spanyol : UNIDAD/MODELO, header 'SERVICIOS | N/P SHANTUI | ... | Intervalo
     | 50 250 ...', tanda X.
  B. Inggris : Machine type/Model, header 'Item | N/P SHANTUI | Quantity', ANGKA
     JAM di baris BERIKUTNYA (sekaligus baris SISTEM pertama), tanda √.
  C. Bilingual '中英文' : terjemahan model yang sudah ada di sheet lain.
Kolom bisa bergeser (excavator punya kolom 'Part Name' ekstra → PN di kolom 2).
Sel interval kadang berisi CATATAN panjang (bukan tanda ganti) → dipfilter
`_is_mark`. Model muncul di banyak file → dedup; sheet '中英文' digabung bila
model dasarnya sudah tercakup.

Di-cache di memori berdasarkan gabungan mtime semua file → admin ganti/menambah
Excel, otomatis dibaca ulang tanpa restart (pola `filter_ref`).
"""
from __future__ import annotations

import re
import threading
from pathlib import Path

from ..core.config import get_settings

_lock = threading.Lock()
_cache: dict = {"key": None, "rows": []}

# Istilah lapangan (Indonesia/Inggris) → daftar kata kunci padanan di tabel.
# Nama servis CAMPUR Inggris/Spanyol/Mandarin → tiap sinonim memetakan ke padanan
# ketiga bahasa agar pencarian memaafkan.
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

# Sinonim jenis alat (ID/EN) → jenis kanonik.
_JENIS_SYN: dict[str, str] = {
    "dozer": "dozer", "buldoser": "dozer", "bulldozer": "dozer", "buldozer": "dozer",
    "loader": "loader", "pemuat": "loader", "wheel loader": "loader",
    "excavator": "excavator", "eskavator": "excavator", "ekskavator": "excavator",
    "grader": "grader", "motor grader": "grader",
    "roller": "roller", "road roller": "roller", "vibro": "roller",
}

# Kata kunci di baris meta → jenis kanonik (label sumber sering tak konsisten).
_JENIS_HINT = [
    ("wheel loader", "loader"), ("loader", "loader"),
    ("excavator", "excavator"),
    ("motor grader", "grader"), ("mitor grader", "grader"), ("grader", "grader"),
    ("road roller", "roller"), ("roller", "roller"),
    ("bulldozer", "dozer"), ("buldozer", "dozer"), ("dozer", "dozer"),
]

# Tanda "ganti pada jam ini" (bukan catatan / teks lain).
_MARKS = {"x", "√", "✓", "✔", "●", "◯", "o", "*", "×"}


def _files() -> list[Path]:
    """Semua file maintenance di data/manuals (bukan PART FILTER)."""
    try:
        d = get_settings().data_path / "manuals"
        if not d.is_dir():
            return []
        out = []
        for p in sorted(d.glob("*.xlsx")):
            n = p.name.lower()
            if n.startswith("~$"):
                continue
            if "part filter" in n:
                continue
            if any(k in n for k in ("maintenance", "periodic", "cycle table")):
                out.append(p)
        return out
    except OSError:
        return []


def _cell(df, r: int, c: int) -> str:
    import pandas as pd
    try:
        v = df.iat[r, c]
        return "" if pd.isna(v) else str(v).strip()
    except Exception:
        return ""


def _is_mark(v: str) -> bool:
    return v.strip().lower() in _MARKS


_EMISI = {"国四": "Euro IV", "国三": "Euro III", "国二": "Euro II", "国一": "Euro I",
          "国Ⅱ": "Euro II", "国Ⅲ": "Euro III"}


def _jenis_of(meta: str) -> str:
    m = (meta or "").lower()
    for kw, jn in _JENIS_HINT:
        if kw in m:
            return jn
    return ""


def _clean_model(src: str) -> str:
    """Kode model = token pertama yang punya HURUF **dan** ANGKA (mis. SD32,
    L36-B5, SE215W, SG15-B6). Lewati ':', tanggal (2022), & kata jenis alat
    (Bulldozer/Cummins/Wheel...) yang tak ber-pola model."""
    for tok in re.findall(r"[A-Za-z0-9][A-Za-z0-9\-]*", src or ""):
        if re.search(r"[A-Za-z]", tok) and re.search(r"\d", tok):
            return tok.upper()
    return ""


def _model_of(model_row: str, unit_line: str, sheet: str) -> str:
    for src in (model_row, unit_line, sheet):
        mv = _clean_model(src)
        if mv:
            return mv
    return ""


_ENGINE_RE = re.compile(
    r"\b(WP\d[\w.\-]*|WD\d[\w.\-]*|NT\d[\w.\-]*|6CT[\w.\-]*|6BT[\w.\-]*|"
    r"QS[BC][\w.\-]*|\dM\d\d[\w.\-]*)\b", re.IGNORECASE)


def _varian_of(*texts: str) -> str:
    """Emisi (国二/国三→Euro) + mesin (dalam kurung / kode WP/NT/6CT), dari baris
    meta/sheet — untuk membedakan varian model."""
    parts: list[str] = []
    blob = " ".join(t for t in texts if t)
    for zh, en in _EMISI.items():
        if zh in blob and en not in parts:
            parts.append(en)
    for mm in re.findall(r"[（(]\s*([^）)]+?)\s*[）)]", blob):
        tok = mm.strip()
        if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.\-]{1,14}", tok) and tok not in parts:
            parts.append(tok)
    for mm in _ENGINE_RE.findall(blob):
        tok = mm.strip().upper()
        if tok not in [p.upper() for p in parts]:
            parts.append(tok)
    return " / ".join(parts)


def _hours(label: str) -> int | None:
    """'50' / '50h' / '2,250h' → int; sel non-jam → None."""
    m = re.fullmatch(r"\s*([\d,]+)\s*[hH]?\s*", label or "")
    if not m:
        return None
    try:
        return int(m.group(1).replace(",", ""))
    except ValueError:
        return None


def _hour_cols(df, hdr: int, ncol: int) -> list[tuple[int, int]]:
    """Kolom interval jam. Coba baris header & header+1; pilih yang jam-nya
    terbanyak (template Inggris menaruh jam di baris SETELAH header)."""
    best: list[tuple[int, int]] = []
    for r in (hdr, hdr + 1):
        cols = [(c, j) for c in range(ncol) if (j := _hours(_cell(df, r, c))) is not None]
        if len(cols) > len(best):
            best = cols
    return best


def _find_col(df, hdr: int, ncol: int, *keys: str) -> int | None:
    for c in range(ncol):
        up = _cell(df, hdr, c).upper()
        if any(k in up for k in keys):
            return c
    return None


def _parse_file(path: Path) -> list[dict]:
    import pandas as pd

    xls = pd.ExcelFile(path, engine="openpyxl")
    rows: list[dict] = []
    for sheet in xls.sheet_names:
        df = pd.read_excel(xls, sheet_name=sheet, header=None, dtype=str)
        nrow, ncol = df.shape
        if nrow == 0 or ncol == 0:
            continue

        # Baris meta (atas) + baris header (memuat 'N/P SHANTUI').
        meta_lines: list[str] = []
        modelo_row = ""
        unit_line = ""
        hdr = None
        for r in range(min(nrow, 16)):
            first = _cell(df, r, 0)
            if first:
                meta_lines.append(first)
            up = first.upper()
            if "MODEL" in up and not modelo_row:
                modelo_row = first
            if up.startswith(("UNIDAD", "UNIT", "MACHINE")) and not unit_line:
                unit_line = first
            if hdr is None and any("N/P SHANTUI" in _cell(df, r, c).upper()
                                   for c in range(ncol)):
                hdr = r
        if hdr is None:
            continue

        meta_blob = " ".join(meta_lines)
        jenis = _jenis_of(meta_blob)
        model = _model_of(modelo_row, unit_line, sheet)
        varian = _varian_of(meta_blob, sheet)
        is_bilingual = ("中英文" in sheet) or ("中英" in sheet)

        pn_c = _find_col(df, hdr, ncol, "N/P SHANTUI")
        qty_c = _find_col(df, hdr, ncol, "CANTIDAD", "QUANTITY", "QTY", "数量")
        name2_c = _find_col(df, hdr, ncol, "PART NAME", "零件名称")
        interval = _hour_cols(df, hdr, ncol)
        if pn_c is None or not interval:
            continue

        sistem = ""
        for r in range(hdr + 1, nrow):
            nama = _cell(df, r, 0)
            if not nama:
                continue
            low = nama.lower()
            if low.startswith(("note:", "nota:", "nota ", "note ", "备注", "observ")):
                continue
            pn = _cell(df, r, pn_c)
            qty = _cell(df, r, qty_c) if qty_c is not None else ""
            if not pn and not qty:
                sistem = nama
                continue
            if name2_c is not None:
                n2 = _cell(df, r, name2_c)
                if n2 and n2.lower() not in low:
                    nama = f"{nama} · {n2}"
            ganti = [j for c, j in interval if _is_mark(_cell(df, r, c))]
            rows.append({
                "jenis": jenis,
                "model": model,
                "varian": varian,
                "sistem": sistem,
                "nama": nama,
                "part_number": pn,
                "qty": qty,
                "ganti_jam": ganti,
                "_bilingual": is_bilingual,
                "_base": _norm(model),
            })
    return rows


def _loose(base: str) -> str:
    """Kode model tanpa suffix huruf-mesin di ekor (SD99E→SD99, SD22S→SD22) —
    dipakai mencocokkan sheet terjemahan '中英文' (sering pakai kode ber-E/S) ke
    model dasar non-bilingual."""
    return re.sub(r"[A-Z]+$", "", base or "") or base


def _dedup(rows: list[dict]) -> list[dict]:
    # 1) buang baris sheet '中英文' bila model dasarnya (longgar) sudah tercakup
    #    sheet non-bilingual; model yang HANYA ada sebagai '中英文' tetap disimpan.
    covered = {_loose(r["_base"]) for r in rows if not r["_bilingual"] and r["_base"]}
    kept = [r for r in rows if not (r["_bilingual"] and _loose(r["_base"]) in covered)]
    # 2) buang baris identik (mis. PN dobel).
    seen, out = set(), []
    for r in kept:
        key = (r["jenis"], r["model"], r["varian"], r["sistem"], r["nama"],
               r["part_number"], tuple(r["ganti_jam"]))
        if key in seen:
            continue
        seen.add(key)
        out.append({k: v for k, v in r.items() if not k.startswith("_")})
    return out


def _load() -> list[dict]:
    files = _files()
    if not files:
        return []
    try:
        key = tuple(sorted((p.name, p.stat().st_mtime) for p in files))
    except OSError:
        return []
    with _lock:
        if _cache["key"] == key and _cache["rows"]:
            return _cache["rows"]
    rows: list[dict] = []
    for p in files:
        try:
            rows.extend(_parse_file(p))
        except Exception:
            continue
    rows = _dedup(rows)
    with _lock:
        _cache["key"] = key
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


def _jenis_norm(s: str) -> str:
    sl = (s or "").strip().lower()
    return _JENIS_SYN.get(sl, sl)


def models() -> list[str]:
    """Kode model unik (untuk kesadaran / pesan miss)."""
    seen, res = set(), []
    for r in _load():
        m = (r.get("model") or "").strip()
        if m and m not in seen:
            seen.add(m)
            res.append(m)
    return res


def models_by_jenis() -> dict[str, list[str]]:
    """{jenis: [model unik...]} untuk blok kesadaran."""
    out: dict[str, list[str]] = {}
    for r in _load():
        jn = r.get("jenis") or "lainnya"
        m = (r.get("model") or "").strip()
        if not m:
            continue
        lst = out.setdefault(jn, [])
        if m not in lst:
            lst.append(m)
    return out


def jenis_list() -> list[str]:
    seen, res = set(), []
    for r in _load():
        j = r.get("jenis") or ""
        if j and j not in seen:
            seen.add(j)
            res.append(j)
    return res


def search(model: str = "", query: str = "", jam: int | None = None,
           jenis: str = "") -> list[dict]:
    """Item perawatan tersaring: per model, jenis alat, kata kunci part, dan
    interval jam servis — semua opsional."""
    rows = _load()
    mn = _norm(model)
    jn = _jenis_norm(jenis) if jenis else ""
    terms = _expand_terms(query) if query else []
    out: list[dict] = []
    for r in rows:
        if mn and mn not in _norm(r["model"]):
            continue
        if jn and jn != (r.get("jenis") or ""):
            continue
        if jam is not None and jam not in (r.get("ganti_jam") or []):
            continue
        if terms:
            hay = " ".join([r["nama"], r["sistem"], r["part_number"]]).lower()
            if not any(t in hay for t in terms):
                continue
        out.append(r)
    return out
