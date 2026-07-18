"""
Referensi filter SHANTUI (alat berat).

Sejak rombakan 2026-07-17 modul ini LOADER TIPIS atas store kanonik
`filter_shantui.json.gz` (di-generate `tools/build_filter_ref.py` dari
data/manuals/PART FILTER SHANTUI.xlsx — parser blok HYDRAULIC/ENGINE pensiun
dari runtime; fail-loud saat build). Ganti Excel = jalankan builder + deploy.

Baris: {alat, jenis, model, part_name, part_number, cross_reference[]}
(cross-reference merek: Fleetguard, Donaldson, Weichai, HIFI, Sakura, dll).
"""
from __future__ import annotations

import re
from pathlib import Path

from .knowledge_util import load_json

_DATA = Path(__file__).parent / "filter_shantui.json.gz"

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


def _load() -> list[dict]:
    return load_json(_DATA)


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
                r.get("part_name_asli") or "",
            ]).lower()
            if not any(t in hay for t in terms):
                continue
        out.append(r)
    return out


_BY_PN_CACHE: dict = {"n": -1, "map": {}}


def by_pn(pn: str) -> dict | None:
    """Cari 1 baris filter yang PN-nya = `pn` ATAU `pn` ada di cross_reference-nya
    (normalisasi PN). Untuk mengisi kolom cross-reference Excel. None bila tak ada."""
    rows = _load()
    if _BY_PN_CACHE["n"] != len(rows):
        m: dict[str, dict] = {}
        for r in rows:
            for key in [r.get("part_number", "")] + list(r.get("cross_reference") or []):
                k = _norm(key)
                if k and k not in m:
                    m[k] = r
        _BY_PN_CACHE.update(n=len(rows), map=m)
    return _BY_PN_CACHE["map"].get(_norm(pn))


def units() -> list[str]:
    """Daftar model unit yang punya data filter (untuk konteks/awareness)."""
    seen, res = set(), []
    for r in _load():
        m = (r.get("model") or "").strip()
        if m and m not in seen:
            seen.add(m)
            res.append(m)
    return res
