"""
Service: Repair Kit Transmisi — baca data/repairkit/transmisi.json (disusun dari
sheet '05变速箱 Gearbox' tiap unit). Memetakan MODEL gearbox (HOWO HW…, ZF, Fast JS)
→ daftar komponen repair kit, BERTINGKAT: seal_kit (oil seal+gasket+O-ring) &
overhaul_tambahan (bearing+synchronizer+snap ring).

Dibaca segar tiap panggil agar editan JSON langsung terpakai tanpa restart
(seperti sinonim.json).
"""
from __future__ import annotations

import io
import json
import re

import pandas as pd

from ..core.config import get_settings

_SEAL_LABEL = {"oil_seal": "Oil seal (油封)", "gasket": "Gasket (垫片)", "o_ring": "O-ring (密封圈)"}
_OVER_LABEL = {"bearing": "Bearing (轴承)", "synchronizer": "Synchronizer (同步器)",
               "snap_ring": "Snap ring/circlip (卡簧)"}


def _load() -> dict:
    try:
        p = get_settings().data_path / "repairkit" / "transmisi.json"
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8")) or {}
    except Exception:
        pass
    return {}


def available() -> bool:
    return bool(_load())


def _norm(s: str) -> str:
    return re.sub(r"[\s_\-/]", "", (s or "")).upper()


# Cache set PN gearbox-assy (dinormalkan), di-refresh saat file transmisi.json berubah.
_ASSY_CACHE: dict = {"mtime": None, "set": set()}


def all_assy_pns() -> set[str]:
    """Set Part Number gearbox/transmisi assy (dinormalkan) dari transmisi.json —
    sumber kebenaran kurasi untuk mengenali PN assy yang TIDAK tertangkap heuristik
    pola (mis. Fast `FZ…`, ZF `WG…`, atau HOWO `HW19710…` tanpa huruf). Di-cache
    per-mtime agar tetap 'segar tiap query' tanpa baca-parse JSON tiap baris."""
    try:
        p = get_settings().data_path / "repairkit" / "transmisi.json"
        mt = p.stat().st_mtime if p.exists() else None
    except Exception:
        mt = None
    if mt != _ASSY_CACHE["mtime"]:
        s: set[str] = set()
        for v in _load().values():
            for pn in v.get("assy_pn", []):
                n = _norm(pn)
                if n:
                    s.add(n)
        _ASSY_CACHE["mtime"] = mt
        _ASSY_CACHE["set"] = s
    return _ASSY_CACHE["set"]


def list_models() -> list[dict]:
    out = []
    for k, v in _load().items():
        out.append({
            "model": k,
            "tipe": v.get("tipe"),
            "jumlah_seal_kit": v.get("jumlah_seal_kit"),
            "jumlah_overhaul_tambahan": v.get("jumlah_overhaul_tambahan"),
            "unit": v.get("unit", []),
        })
    return out


def find(query: str) -> list[tuple[str, dict]]:
    """Resolve query → [(model_key, entry)]. Cocok via: kode model persis,
    assy PN, awalan model (HW19709 → HW19709XST/XSTL), atau nama unit."""
    data = _load()
    if not data or not (query or "").strip():
        return []
    qn = _norm(query)
    # 1) kode model persis
    for k, v in data.items():
        if _norm(k) == qn:
            return [(k, v)]
    # 2) assy PN (persis atau substring)
    hits = [(k, v) for k, v in data.items()
            if any(qn == _norm(pn) or qn in _norm(pn) for pn in v.get("assy_pn", []))]
    if hits:
        return hits
    # 3) awalan/irisan kode model (HW19709 → semua variannya)
    hits = [(k, v) for k, v in data.items() if _norm(k).startswith(qn) or qn in _norm(k)]
    if hits:
        return hits
    # 4) nama unit
    hits = [(k, v) for k, v in data.items()
            if any(qn in _norm(u) for u in v.get("unit", []))]
    return hits


def kit(entry: dict, tingkat: str = "seal_kit", per_cat_cap: int = 60) -> dict:
    """Susun repair kit utk satu model. tingkat: 'seal_kit' | 'overhaul' | 'semua'."""
    tingkat = (tingkat or "seal_kit").lower()
    tiers = []
    if tingkat in ("seal_kit", "semua", "perpak", "seal"):
        tiers.append(("seal_kit", _SEAL_LABEL))
    if tingkat in ("overhaul", "semua", "lengkap"):
        tiers.append(("overhaul_tambahan", _OVER_LABEL))
    if not tiers:  # fallback
        tiers = [("seal_kit", _SEAL_LABEL)]

    out_cats: dict[str, dict] = {}
    total = 0
    for tier_key, labels in tiers:
        for cat, items in (entry.get(tier_key) or {}).items():
            shown = items[:per_cat_cap]
            out_cats[labels.get(cat, cat)] = {
                "jumlah": len(items),
                "komponen": [{"pn": it["pn"], "nama": it["nama"]} for it in shown],
                "catatan_potong": (f"+{len(items) - len(shown)} lagi tidak ditampilkan"
                                   if len(items) > len(shown) else None),
            }
            total += len(items)
    return {"jumlah_komponen": total, "kategori": out_cats}


_ALL_LABEL = {**_SEAL_LABEL, **_OVER_LABEL}
_TIER_LABEL = {"seal_kit": "SEAL KIT (perpak)", "overhaul_tambahan": "OVERHAUL"}


def _component_rows(mk: str, entry: dict) -> list[dict]:
    rows = []
    for tier_key in ("seal_kit", "overhaul_tambahan"):
        for cat, items in (entry.get(tier_key) or {}).items():
            for it in items:
                rows.append({
                    "Tingkat": _TIER_LABEL.get(tier_key, tier_key),
                    "Kategori": _ALL_LABEL.get(cat, cat),
                    "Part Number": it.get("pn", ""),
                    "Nama": it.get("nama", ""),
                })
    return rows


def to_excel_bytes(model: str | None = None) -> bytes:
    """Workbook: sheet 'Ringkasan' + 1 sheet per model transmisi (komponen kit).
    Bila `model` diisi, hanya model yang cocok."""
    data = _load()
    if model:
        items = find(model)
    else:
        items = sorted(data.items())

    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        summary = [{
            "Model": mk,
            "Tipe": v.get("tipe", ""),
            "Seal Kit (perpak)": v.get("jumlah_seal_kit", 0),
            "Overhaul tambahan": v.get("jumlah_overhaul_tambahan", 0),
            "Unit pemakai": ", ".join(v.get("unit", [])),
            "Gearbox assy PN": ", ".join(v.get("assy_pn", [])),
        } for mk, v in items]
        pd.DataFrame(summary or [{"Model": "(kosong)"}]).to_excel(
            xw, sheet_name="Ringkasan", index=False)

        used = set()
        for mk, entry in items:
            name = re.sub(r"[\[\]\:\*\?/\\]", "", str(mk))[:31] or "Model"
            base = name
            i = 1
            while name.lower() in used:
                name = f"{base[:28]}_{i}"; i += 1
            used.add(name.lower())
            rows = _component_rows(mk, entry)
            pd.DataFrame(rows or [{"Part Number": "(tidak ada komponen)", "Nama": ""}]).to_excel(
                xw, sheet_name=name, index=False)
    return buf.getvalue()
