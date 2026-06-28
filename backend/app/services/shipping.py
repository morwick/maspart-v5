"""
Service Ongkir — RajaOngkir (Komerce API v1): JNE, J&T, SiCepat, POS.

Order internal: tujuan = kota cabang pemesan (dari akun), asal = gudang pusat.
RajaOngkir (Komerce) memakai ID lokasi, bukan kode pos langsung — jadi kode pos
cabang/asal dicari dulu jadi ID lokasi (di-cache), lalu dihitung ongkirnya.

Perlu RAJAONGKIR_API_KEY di .env (key dari https://rajaongkir.komerce.id).
"""
from __future__ import annotations

import requests

from ..core.config import get_settings

COURIERS = "jne:sicepat:jnt:pos"  # kurir yang diminta (dipisah ':')
_BASE = "https://rajaongkir.komerce.id/api/v1"
_DEST_URL = f"{_BASE}/destination/domestic-destination"
_COST_URL = f"{_BASE}/calculate/domestic-cost"

# Kode pos kota tiap akun cabang (tujuan kirim). Sesuaikan bila perlu.
BRANCH_POSTAL: dict[str, str] = {
    "jakarta": "10110",
    "balikpapan": "76111",
    "palembang": "30111",
    "makassar": "90111",
    "jambi": "36111",
    "banjarmasin": "70711",
    "muarateweh": "73811",
    "pontianak": "78111",
    "medan": "20111",
    "pekanbaru": "28112",
}

# Titik ASAL kirim (gudang) per akun cabang → kode pos. Ongkir dihitung dari sini.
# lat/lon disimpan sebagai referensi lokasi gudang fisik.
BRANCH_ORIGIN: dict[str, dict] = {
    "jakarta": {"postal": "14250", "lat": -6.141004327200295, "lon": 106.91444803654574,
                "label": "Gudang Jakarta — Pegangsaan Dua, Kelapa Gading, Jakarta Utara"},
}

# Cache: kode pos → ID lokasi RajaOngkir (agar tidak query berulang).
_ID_CACHE: dict[str, int] = {}


def origin_postal_for(username: str) -> str:
    """Kode pos gudang ASAL sesuai akun cabang; fallback ke SHIP_ORIGIN_POSTAL."""
    b = BRANCH_ORIGIN.get((username or "").strip().lower())
    if b and b.get("postal"):
        return b["postal"]
    return get_settings().ship_origin_postal or "10110"


def available() -> bool:
    return bool(get_settings().rajaongkir_api_key)


def dest_postal_for(username: str) -> str | None:
    return BRANCH_POSTAL.get((username or "").strip().lower())


def _headers() -> dict:
    return {"key": get_settings().rajaongkir_api_key}


def _resolve_location_id(query: str) -> int | None:
    """Cari ID lokasi RajaOngkir dari kode pos (atau nama). Hasil di-cache."""
    query = (query or "").strip()
    if not query:
        return None
    if query in _ID_CACHE:
        return _ID_CACHE[query]
    try:
        r = requests.get(
            _DEST_URL,
            params={"search": query, "limit": 1, "offset": 0},
            headers=_headers(),
            timeout=20,
        )
        if r.status_code != 200:
            return None
        rows = (r.json() or {}).get("data") or []
        if not rows:
            return None
        loc_id = int(rows[0].get("id"))
        _ID_CACHE[query] = loc_id
        return loc_id
    except Exception:
        return None


def get_rates(username: str, weight_grams: int, item_value: int = 0, dest_postal: str = "", origin_postal: str = "") -> tuple[list[dict], str | None]:
    s = get_settings()
    if not s.rajaongkir_api_key:
        return [], "Ongkir belum diaktifkan (RAJAONGKIR_API_KEY kosong)."

    # Prioritas: kode pos penerima yang diisi; fallback ke kode pos kota cabang.
    dest_pc = (dest_postal or "").strip() or dest_postal_for(username)
    if not dest_pc:
        return [], "Kode pos tujuan belum diisi / cabang belum terdaftar."

    # Asal: kode pos gudang terpilih (pembeli) bila diberikan; jika tidak,
    # gudang cabang sesuai akun, lalu fallback SHIP_ORIGIN_ID/POSTAL.
    branch_origin = BRANCH_ORIGIN.get((username or "").strip().lower())
    if (origin_postal or "").strip():
        origin_id = _resolve_location_id(origin_postal.strip())
    elif branch_origin and branch_origin.get("postal"):
        origin_id = _resolve_location_id(branch_origin["postal"])
    elif s.ship_origin_id:
        origin_id = int(s.ship_origin_id)
    else:
        origin_id = _resolve_location_id(s.ship_origin_postal or "10110")
    dest_id = _resolve_location_id(dest_pc)
    if not origin_id:
        return [], "Lokasi asal gudang tidak ditemukan di RajaOngkir."
    if not dest_id:
        return [], "Lokasi tujuan cabang tidak ditemukan di RajaOngkir."

    body = {
        "origin": origin_id,
        "destination": dest_id,
        "weight": max(int(weight_grams), 100),  # gram
        "courier": COURIERS,
        "price": "lowest",
    }
    try:
        r = requests.post(_COST_URL, data=body, headers=_headers(), timeout=20)
        if r.status_code != 200:
            return [], f"Gagal ambil ongkir: {r.status_code} {r.text[:160]}"
        rows = (r.json() or {}).get("data") or []
        out = []
        for p in rows:
            out.append({
                "courier": (p.get("code") or "").lower(),
                "courier_name": p.get("name") or "",
                "service": p.get("service") or "",
                "price": int(p.get("cost") or 0),
                "etd": p.get("etd") or "",
            })
        out.sort(key=lambda x: x["price"])
        return out, None
    except Exception as e:
        return [], str(e)
