"""
Service SIMS — reuse modul `sims_fetcher.py` milik project Streamlit untuk
mengambil URL gambar part dari SIMS (cache di images/image_links.json,
fallback login RSA bila cache miss).

Tidak menulis ulang logika SIMS: kita import modul root apa adanya, lalu
arahkan path cache-nya ke folder `images/` di root project (karena backend
berjalan dari folder backend/).
"""
from __future__ import annotations

import sys
from pathlib import Path

from ..core.config import get_settings

# Root project = parent dari folder backend/ (sibling: data/, images/).
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
# Modul SIMS/compare yang di-reuse ada di backend/shared/ (di-import top-level).
_SHARED_DIR = Path(__file__).resolve().parents[2] / "shared"
if str(_SHARED_DIR) not in sys.path:
    sys.path.insert(0, str(_SHARED_DIR))

_images_dir = _PROJECT_ROOT / "images"

_SIMS_OK = False
try:
    import sims_fetcher as _sf  # type: ignore

    # Arahkan path cache JSON ke root project (absolut).
    _sf.IMAGES_JSON = _images_dir / "image_links.json"
    _sf.PART_INFO_JSON = _images_dir / "part_info.json"
    _SIMS_OK = True
except Exception as _e:  # pragma: no cover
    _sf = None
    _SIMS_IMPORT_ERR = str(_e)

# Harga (CNY) dari SIMS — modul terpisah, cache JSON sendiri.
_PRICE_OK = False
try:
    import sims_price_fetcher as _spf  # type: ignore

    _spf.PRICE_CACHE_FILE = _images_dir / "part_price_cache.json"
    _PRICE_OK = True
except Exception:  # pragma: no cover
    _spf = None


def available() -> bool:
    return _SIMS_OK


def get_images(part_number: str, force_refresh: bool = False) -> list[str]:
    """
    Return list URL gambar SIMS untuk part_number (bisa kosong).
    Non-fatal: error apa pun → list kosong.
    """
    pn = (part_number or "").strip()
    if not _SIMS_OK or not pn:
        return []
    try:
        urls, _err = _sf.get_sims_images(pn, force_refresh=force_refresh)
        return list(urls or [])
    except Exception:
        return []


def get_part_info(part_number: str) -> dict:
    """Return info part dari SIMS (mis. {'partName': ...}) atau {} bila gagal."""
    pn = (part_number or "").strip()
    if not _SIMS_OK or not pn:
        return {}
    try:
        info, _err = _sf.get_sims_part_info(pn)
        return info or {}
    except Exception:
        return {}


def price_available() -> bool:
    return _PRICE_OK


def get_price(part_number: str, force_refresh: bool = False) -> tuple[float | None, str | None]:
    """
    Harga part (CNY) dari SIMS, dengan fallback PN tanpa suffix '/<digit>'.
    Return (harga_cny_atau_None, info_error_atau_None). Field error ke-2 juga
    dipakai sebagai catatan 'via PN lain' (mirror batch_harga_engine._fetch_one).
    """
    import re

    pn = (part_number or "").strip()
    if not _PRICE_OK or not pn:
        return None, "price fetcher tidak tersedia" if not _PRICE_OK else None
    try:
        price, err = _spf.get_sims_part_price(pn, force_refresh=force_refresh)
        if price is None and re.search(r"/\d+$", pn):
            fallback = re.sub(r"/\d+$", "", pn)
            price2, _err2 = _spf.get_sims_part_price(fallback, force_refresh=force_refresh)
            if price2 is not None:
                return price2, f"(via {fallback})"
        return price, err
    except Exception as e:
        return None, str(e)
