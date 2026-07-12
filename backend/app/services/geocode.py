"""Reverse-geocoding (OpenStreetMap Nominatim) — dipakai router geo & auto-isi
kode pos asal gudang dari koordinat yang sudah diatur admin.

Nominatim membatasi laju (~1 permintaan/detik) → pemanggil massal WAJIB memberi
jeda (lihat `fill_missing_gudang_postal`).
"""
from __future__ import annotations

import logging
import threading
import time

import requests

from . import gudang_config

logger = logging.getLogger("maspart.geocode")

_UA = {"User-Agent": "maspart-geocode/1.0 (internal tool)"}
_TIMEOUT = 15
_THROTTLE = 1.2       # detik antar permintaan (kebijakan Nominatim)
_STARTUP_DELAY = 45   # jangan ganggu startup


def reverse(lat: float, lon: float) -> dict:
    """{address, postal, display_name} — {} bila gagal (non-fatal)."""
    try:
        r = requests.get(
            "https://nominatim.openstreetmap.org/reverse",
            params={"lat": lat, "lon": lon, "format": "json", "addressdetails": 1, "zoom": 18},
            headers=_UA, timeout=_TIMEOUT,
        )
        r.raise_for_status()
        d = r.json() or {}
        a = d.get("address", {}) or {}
        return {"address": a, "postal": a.get("postcode", "") or "",
                "display_name": d.get("display_name", "")}
    except Exception as e:
        logger.warning("reverse-geocode gagal (%s, %s): %s", lat, lon, e)
        return {}


def reverse_postal(lat: float, lon: float) -> str:
    """Kode pos (digit saja) dari koordinat; '' bila tak ketemu."""
    p = (reverse(lat, lon) or {}).get("postal", "")
    return "".join(ch for ch in str(p) if ch.isdigit())[:10]


def fill_missing_gudang_postal() -> int:
    """Isi kode pos ASAL untuk gudang yang punya koordinat tapi belum punya kode pos.

    Kenapa perlu: gudang PEMENUH (fallback terdekat) sering bukan lokasi pilihan
    pembeli, dan tanpa kode pos ongkir dari gudang itu tak bisa dihitung sama
    sekali. Koordinat semua gudang sudah diatur admin, jadi kode pos bisa
    diturunkan dari situ; admin tinggal mengoreksi lewat halaman Lokasi Gudang.
    Return jumlah gudang yang terisi.
    """
    coords = gudang_config.coords_map()
    postal = gudang_config.postal_map()
    kurang = [lb for lb, c in coords.items() if c and not postal.get(lb)]
    filled = 0
    for lb in kurang:
        lat, lon = coords[lb]
        code = reverse_postal(lat, lon)
        if code and gudang_config.set_postal(lb, code):
            filled += 1
            logger.info("kode pos gudang %s → %s (dari koordinat)", lb, code)
        time.sleep(_THROTTLE)
    return filled


def start_postal_warmer() -> None:
    """Jalankan pengisian kode pos gudang SEKALI di latar setelah boot (best-effort)."""
    def _run() -> None:
        time.sleep(_STARTUP_DELAY)
        try:
            n = fill_missing_gudang_postal()
            if n:
                logger.info("auto-isi kode pos gudang: %d gudang terisi dari koordinat", n)
        except Exception:
            logger.exception("auto-isi kode pos gudang gagal")

    threading.Thread(target=_run, name="gudang-postal-warmer", daemon=True).start()
