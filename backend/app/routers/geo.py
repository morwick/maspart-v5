"""Router Geo — proxy reverse/forward geocoding (OpenStreetMap Nominatim).

Dipakai pemilih lokasi peta saat mengisi alamat penerima. Diproxy lewat backend
agar bebas masalah CORS / User-Agent policy Nominatim.
"""
from __future__ import annotations

import requests
from fastapi import APIRouter, Depends, Query

from ..deps import get_current_user

router = APIRouter(prefix="/api/geo", tags=["geo"])

_UA = {"User-Agent": "maspart-geocode/1.0 (internal tool)"}
_TIMEOUT = 15


def _compose_address(a: dict) -> str:
    parts = [
        a.get("road"),
        a.get("neighbourhood") or a.get("hamlet"),
        a.get("village") or a.get("suburb"),
        a.get("city_district"),
        a.get("city") or a.get("town") or a.get("county"),
        a.get("state"),
    ]
    seen, out = set(), []
    for p in parts:
        if p and p not in seen:
            seen.add(p)
            out.append(p)
    return ", ".join(out)


@router.get("/reverse")
def reverse(lat: float = Query(...), lon: float = Query(...), _u: dict = Depends(get_current_user)):
    try:
        r = requests.get(
            "https://nominatim.openstreetmap.org/reverse",
            params={"lat": lat, "lon": lon, "format": "json", "addressdetails": 1, "zoom": 18},
            headers=_UA, timeout=_TIMEOUT,
        )
        r.raise_for_status()
        d = r.json() or {}
        a = d.get("address", {}) or {}
        return {
            "lat": lat, "lon": lon,
            "address": _compose_address(a),
            "postal": a.get("postcode", ""),
            "display_name": d.get("display_name", ""),
        }
    except Exception as e:
        return {"lat": lat, "lon": lon, "address": "", "postal": "", "display_name": "", "error": str(e)}


@router.get("/search")
def search(q: str = Query(..., min_length=3), _u: dict = Depends(get_current_user)):
    try:
        r = requests.get(
            "https://nominatim.openstreetmap.org/search",
            params={"q": q, "format": "json", "addressdetails": 1, "limit": 6, "countrycodes": "id"},
            headers=_UA, timeout=_TIMEOUT,
        )
        r.raise_for_status()
        out = []
        for d in r.json() or []:
            a = d.get("address", {}) or {}
            out.append({
                "label": d.get("display_name", ""),
                "lat": float(d.get("lat")),
                "lon": float(d.get("lon")),
                "postal": a.get("postcode", ""),
                "address": _compose_address(a),
            })
        return {"results": out}
    except Exception as e:
        return {"results": [], "error": str(e)}
