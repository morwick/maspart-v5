"""
Jadwal perawatan berkala (periodic maintenance table) alat berat SHANTUI.

Sejak rombakan 2026-07-17 modul ini LOADER TIPIS atas store kanonik
`jadwal_perawatan.json.gz` (di-generate `tools/build_maintenance.py` dari 4 xlsx
data/manuals — parser 3-template pensiun dari runtime; kesalahan parsing kini
ketahuan saat build, fail-loud). Ganti/tambah Excel = jalankan builder + deploy.

Baris: {jenis, model, varian, sistem, nama, part_number, qty, ganti_jam[]}
(+ sistem_asli/nama_asli bila kamus i18n menerjemahkan — pencarian tetap
memaafkan istilah asli ES/EN/CN).
"""
from __future__ import annotations

import re
from pathlib import Path

from .knowledge_util import load_json

_DATA = Path(__file__).parent / "jadwal_perawatan.json.gz"

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


def _load() -> list[dict]:
    return load_json(_DATA)


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
            hay = " ".join([r["nama"], r["sistem"], r["part_number"],
                            r.get("nama_asli") or "", r.get("sistem_asli") or ""]).lower()
            if not any(t in hay for t in terms):
                continue
        out.append(r)
    return out
