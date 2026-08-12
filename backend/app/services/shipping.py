"""
Service Ongkir — RajaOngkir (Komerce API v1): JNE, J&T, SiCepat, POS.

Order internal: tujuan = kota cabang pemesan (dari akun), asal = gudang pusat.
RajaOngkir (Komerce) memakai ID lokasi, bukan kode pos langsung — jadi kode pos
cabang/asal dicari dulu jadi ID lokasi (di-cache), lalu dihitung ongkirnya.

Perlu RAJAONGKIR_API_KEY di .env (key dari https://rajaongkir.komerce.id).
"""
from __future__ import annotations

import time

import requests

from ..core.config import get_settings
from .cache_util import CacheTTL

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


# ── Lacak resi (waybill) ────────────────────────────────────────────────────
# Paket RajaOngkir yang dipakai MEMANG punya endpoint ini (diprobe 2026-07-21:
# nomor karangan dijawab {"meta":{"message":"Invalid Awb"}} — amplop JSON milik
# API, bukan '404 page not found' seperti rute yang tak ada). ⛔ Ini BUKAN
# pemesanan pengiriman: resi tetap dibuat gerai ekspedisi & diketik admin.
_TRACK_URL = f"{_BASE}/track/waybill"
_TRACK_TTL = 600.0          # 10 mnt — paket tak berpindah lebih cepat dari ini
# Berkunci (resi, kurir) → tiap kiriman baru menambah satu entri PERMANEN sebelum
# ini diberi plafon; TTL-nya sendiri sudah diperiksa pemanggil (lihat cache_util).
_track_cache = CacheTTL("shipping.track", _TRACK_TTL, 512)


def _teks(v) -> str:
    return str(v).strip() if v is not None else ""


def track(awb: str, courier: str) -> tuple[dict | None, str | None]:
    """(hasil, error). hasil = {delivered, status, ringkas, riwayat[]}.

    Di-cache 10 menit per (resi, kurir): halaman pesanan pembeli & aplikasi
    memanggilnya tiap dibuka, sedangkan paket tak berpindah secepat itu — tanpa
    cache satu pembeli yang menyegarkan berulang kali menghabiskan kuota API.
    """
    no = _teks(awb).upper()
    kur = _teks(courier).lower()
    if not no or not kur:
        return None, "Nomor resi atau kurir belum ada."
    if not available():
        return None, "Layanan lacak resi belum aktif."

    kunci = (no, kur)
    now = time.time()
    hit = _track_cache.get(kunci)
    if hit and now - hit[0] < _TRACK_TTL:
        return hit[1], None

    try:
        r = requests.post(_TRACK_URL, data={"awb": no, "courier": kur},
                          headers=_headers(), timeout=20)
    except Exception as e:
        return None, f"Gagal menghubungi layanan lacak: {e}"
    try:
        body = r.json() or {}
    except ValueError:
        return None, f"Balasan lacak tak terbaca ({r.status_code})."
    if r.status_code != 200:
        # Pesan dari kurir/API diteruskan apa adanya — 'Invalid Awb' jauh lebih
        # berguna bagi admin daripada 'gagal'.
        pesan = _teks(((body.get("meta") or {}).get("message"))) or f"HTTP {r.status_code}"
        return None, pesan

    d = body.get("data") or {}
    ringkas = d.get("summary") or {}
    status = d.get("delivery_status") or {}
    manifest = d.get("manifest") or []
    hasil = {
        "resi": no,
        "kurir": kur,
        "delivered": bool(d.get("delivered")),
        # Nama field berbeda antar-kurir → ambil yang pertama tersedia.
        "status": _teks(status.get("status") or ringkas.get("status")),
        "penerima": _teks(status.get("pod_receiver") or status.get("pod_receiver_name")),
        "waktu_terima": _teks(status.get("pod_date") or status.get("pod_time")),
        "layanan": _teks(ringkas.get("service_code") or ringkas.get("service")),
        "riwayat": [
            {
                "waktu": f"{_teks(m.get('manifest_date'))} {_teks(m.get('manifest_time'))}".strip(),
                "keterangan": _teks(m.get("manifest_description") or m.get("manifest_code")),
                "lokasi": _teks(m.get("city_name") or m.get("city")),
            }
            for m in manifest
            if isinstance(m, dict)
        ],
    }
    # Kurir mengirim manifest dari yang TERLAMA; pembeli ingin yang terbaru dulu.
    hasil["riwayat"].reverse()
    _track_cache[kunci] = (now, hasil)
    return hasil, None


def bersihkan_cache_lacak() -> None:
    """Untuk test."""
    _track_cache.clear()
