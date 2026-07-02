"""
Service EPC (Sinotruk) — resolver KONFIGURASI kendaraan dari nomor rangka.

Memakai endpoint PUBLIK (tanpa token) EPC Sinotruk:
    GET https://epc.sinotruk.com:18080/api/rest/serviceVehicle/getVehicleConfig?chassisNo=<frame>

Mengembalikan model, gearbox, axle, mesin, seri, Euro, dll untuk SATU kendaraan.
'frame number' = 8 karakter terakhir VIN (mis. VIN LZZ5DMSD5RT108966 → RT108966).
Hasil di-cache in-memory (config kendaraan ~statis). Endpoint tree/part EPC TIDAK
dipakai di sini (butuh token & lebih rapuh — lihat catatan integrasi).
"""
from __future__ import annotations

import re
import threading

import requests
import urllib3

# EPC pakai sertifikat yang tak terverifikasi requests → kita verify=False;
# redam warning-nya agar tak membanjiri log tiap panggilan.
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

EPC_BASE = "https://epc.sinotruk.com:18080"
_CONFIG_URL = f"{EPC_BASE}/api/rest/serviceVehicle/getVehicleConfig"

_cache: dict[str, dict] = {}
_lock = threading.Lock()

# Terjemahan ringan enum China yang sering muncul (sisanya biar AI terjemahkan).
_DISCHARGE = {"欧II": "Euro II", "欧III": "Euro III", "欧IV": "Euro IV",
              "欧V": "Euro V", "欧VI": "Euro VI", "国V": "China V", "国VI": "China VI"}
_BRAND = {"豪沃": "HOWO", "汕德卡": "SITRAK", "斯太尔": "STEYR", "黄河": "Yellow River"}


def _frame(rangka: str) -> str:
    """Normalisasi → frame number (8 char terakhir bila itu VIN penuh)."""
    n = re.sub(r"[^A-Z0-9]", "", (rangka or "").upper())
    return n[-8:] if len(n) >= 11 else n


def available() -> bool:
    return True  # endpoint publik, tanpa auth


def get_config(rangka: str) -> dict:
    """Ambil config mentah dari EPC (cache in-memory). {} bila gagal/tak ada."""
    cjh = _frame(rangka)
    if not cjh:
        return {}
    with _lock:
        if cjh in _cache:
            return _cache[cjh]
    data: dict = {}
    try:
        r = requests.get(_CONFIG_URL, params={"chassisNo": cjh}, timeout=20, verify=False)
        j = r.json()
        if isinstance(j, dict) and j.get("success") and isinstance(j.get("data"), dict):
            data = j["data"]
    except Exception:
        data = {}
    # Cache HANYA hit asli. Jangan cache {} dari error jaringan / respons gagal —
    # tanpa TTL, satu blip akan permanen jadi 'rangka tidak ditemukan' sepanjang
    # proses hidup. Miss dibiarkan tak ter-cache → panggilan berikut coba lagi.
    if data:
        with _lock:
            _cache[cjh] = data
    return data


def lookup(rangka: str) -> dict:
    """Ringkasan config kendaraan (field bersih + sebagian enum diterjemahkan)."""
    d = get_config(rangka)
    if not d:
        return {"found": False, "input": (rangka or "").strip(),
                "frame_number": _frame(rangka),
                "catatan": "Nomor rangka tidak ditemukan di EPC Sinotruk (cek ejaan; "
                           "EPC hanya memuat unit Sinotruk/HOWO/SITRAK)."}
    brand = d.get("brandName") or ""
    return {
        "found": True,
        "frame_number": d.get("chassisNo"),
        "vin": d.get("vin"),
        "model_code": d.get("modelCode"),
        "brand": _BRAND.get(brand, brand),
        "seri": d.get("subSeriesName") or d.get("seriesName"),
        "drive_mode": d.get("driveMode"),
        "emisi": _DISCHARGE.get(d.get("discharge") or "", d.get("discharge")),
        "jenis_pemakaian": d.get("useType"),
        "engine": d.get("engineModelCode"),
        "gearbox": d.get("gearboxModelCode"),
        "axle_depan": d.get("axleFrontModelCode"),
        "axle_tengah": d.get("axleMidModelCode"),
        "axle_belakang": d.get("axlxAftModelCode"),
        "transfer_case": d.get("transferboxModelCode"),
        "order_no": d.get("orderNo"),
        "dealer": d.get("dealerName"),
        "negara": d.get("countryName"),
        "tanggal_keluar_pabrik": d.get("departureDate"),
        "tanggal_jual": d.get("saleDate"),
    }
