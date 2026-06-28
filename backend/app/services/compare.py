"""
Service: Bandingkan 2 Part (Interchange Analyzer) — decoupled dari Streamlit.

Reuse modul root `part_compare.py` (analisis kemiripan gambar via perceptual
hash + nama, murni numpy/PIL). Untuk tiap PN: ambil URL gambar SIMS + nama
(index lokal → fallback SIMS), unduh bytes, lalu best_match.
"""
from __future__ import annotations

import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests

from . import part_index, sims

# Modul `part_compare.py` yang di-reuse ada di backend/shared/.
_SHARED_DIR = Path(__file__).resolve().parents[2] / "shared"
if str(_SHARED_DIR) not in sys.path:
    sys.path.insert(0, str(_SHARED_DIR))

_PC_OK = False
try:
    import part_compare as _pc  # type: ignore
    _PC_OK = True
except Exception:  # pragma: no cover
    _pc = None

# Batasi jumlah foto yang dianalisis per part (N×M kombinasi bisa lambat).
_MAX_ANALYZE = 8


def available() -> bool:
    return _PC_OK


def _name_for(pn: str) -> str:
    # Nama dari index lokal (exact) → fallback SIMS partName.
    hits = part_index.search_part_number(pn)
    exact = [h for h in hits if h["part_number"].upper() == pn.upper()]
    if exact and exact[0]["part_name"] not in ("", "N/A"):
        return exact[0]["part_name"]
    info = sims.get_part_info(pn)
    return (info.get("partName") or "").strip()


def _fetch_bytes(url: str) -> bytes | None:
    try:
        r = requests.get(url, timeout=20)
        if r.status_code == 200 and r.content:
            return r.content
    except Exception:
        pass
    return None


def _download_all(urls: list[str]) -> list[bytes | None]:
    out: list[bytes | None] = [None] * len(urls)
    with ThreadPoolExecutor(max_workers=6) as ex:
        futs = {ex.submit(_fetch_bytes, u): i for i, u in enumerate(urls)}
        for f in futs:
            i = futs[f]
            try:
                out[i] = f.result()
            except Exception:
                out[i] = None
    return out


def compare(pn1: str, pn2: str) -> dict:
    pn1 = (pn1 or "").strip()
    pn2 = (pn2 or "").strip()

    urls1 = sims.get_images(pn1)
    urls2 = sims.get_images(pn2)
    name1 = _name_for(pn1)
    name2 = _name_for(pn2)

    result: dict = {
        "pn1": pn1, "pn2": pn2,
        "name1": name1, "name2": name2,
        "urls1": urls1, "urls2": urls2,
        "best": None,
        "error": None,
    }

    if not _PC_OK:
        result["error"] = "Modul analisis (part_compare) tidak tersedia."
        return result
    if not urls1:
        result["error"] = f"Tidak ada gambar SIMS untuk {pn1}."
        return result
    if not urls2:
        result["error"] = f"Tidak ada gambar SIMS untuk {pn2}."
        return result

    bytes1 = _download_all(urls1[:_MAX_ANALYZE])
    bytes2 = _download_all(urls2[:_MAX_ANALYZE])
    if not any(bytes1) or not any(bytes2):
        result["error"] = "Gagal mengunduh gambar dari SIMS."
        return result

    match = _pc.best_match(bytes1, bytes2, name1=name1, name2=name2)
    best = match.get("best")
    if not best:
        result["error"] = "Tidak dapat menganalisis pasangan gambar."
        return result

    result["best"] = {
        "shape_score": best["shape_score"],
        "color_score": best["color_score"],
        "name_score": best["name_score"],
        "overall": best["overall"],
        "verdict": best["verdict"],
        "color": best["color"],
        "i": best.get("i", 0),
        "j": best.get("j", 0),
    }
    return result
