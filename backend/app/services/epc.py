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
import time

import requests
import urllib3

from .cache_util import CacheTTL

# EPC pakai sertifikat yang tak terverifikasi requests → kita verify=False;
# redam warning-nya agar tak membanjiri log tiap panggilan.
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

EPC_BASE = "https://epc.sinotruk.com:18080"
_CONFIG_URL = f"{EPC_BASE}/api/rest/serviceVehicle/getVehicleConfig"

# TANPA TTL — disengaja (alasannya di get_config: hanya hit ASLI yang di-cache,
# jadi tak ada risiko blip jaringan jadi permanen). Yang ditambahkan cuma PAGAR
# JUMLAH: kuncinya frame dari input user, dan dulu dict ini hanya tumbuh selama
# proses hidup. 2000 > populasi armada (1.335) → praktis tak pernah membuang.
_cache = CacheTTL("epc.config_vin", None, 2000)
_lock = threading.Lock()

# Terjemahan ringan enum China yang sering muncul (sisanya biar AI terjemahkan).
_DISCHARGE = {"欧II": "Euro II", "欧III": "Euro III", "欧IV": "Euro IV",
              "欧V": "Euro V", "欧VI": "Euro VI", "国V": "China V", "国VI": "China VI"}
_BRAND = {"豪沃": "HOWO", "汕德卡": "SITRAK", "斯太尔": "STEYR", "黄河": "Yellow River"}


def _frame(rangka: str) -> str:
    """Normalisasi → frame number (8 char terakhir bila itu VIN penuh)."""
    n = re.sub(r"[^A-Z0-9]", "", (rangka or "").upper())
    return n[-8:] if len(n) >= 11 else n


# Check digit VIN (ISO 3779 / GB 16735) — salinan kecil dari vin_ocr (modul OCR
# berat, sengaja tak diimpor di jalur asisten).
_CD_TRANS = {c: i for i, c in enumerate("0123456789")}
_CD_TRANS.update({"A": 1, "B": 2, "C": 3, "D": 4, "E": 5, "F": 6, "G": 7, "H": 8,
                  "J": 1, "K": 2, "L": 3, "M": 4, "N": 5, "P": 7, "R": 9,
                  "S": 2, "T": 3, "U": 4, "V": 5, "W": 6, "X": 7, "Y": 8, "Z": 9})
_CD_BOBOT = (8, 7, 6, 5, 4, 3, 2, 10, 0, 9, 8, 7, 6, 5, 4, 3, 2)


def check_digit(vin: str) -> str:
    total = sum(_CD_TRANS.get(c, 0) * _CD_BOBOT[i] for i, c in enumerate(vin[:17]))
    sisa = total % 11
    return "X" if sisa == 10 else str(sisa)


def periksa_bentuk_rangka(rangka: str) -> dict | None:
    """Deteksi VIN yang BENTUKNYA janggal — supaya jawaban "tidak terbaca di EPC"
    bisa menyebut sebabnya. Audit ai_chat_log 2026-08-28: 'LZZ5EXSF9RJ38044'
    (16 char) dicoba 3× & 'LZZ5BBFE1SE592853' 2×; `_frame` diam-diam memotong
    8 char terakhir sehingga EPC ditanya frame yang salah dan user cuma disuruh
    "cek ejaan". None = bentuk wajar (17 char cd cocok, atau frame 8 char)."""
    n = re.sub(r"[^A-Z0-9]", "", (rangka or "").upper())
    if not n or len(n) <= 8:
        return None
    if len(n) != 17:
        if 9 <= len(n) <= 16:
            sel = 17 - len(n)
            pesan = (f"Nomor rangka yang diberikan {len(n)} karakter — VIN Sinotruk "
                     f"17 karakter, kemungkinan KURANG {sel} karakter (salah ketik/terlewat). "
                     "Sampaikan jumlah karakternya ke user & minta cek ulang/foto plat rangka.")
        else:
            sel = len(n) - 17
            pesan = (f"Nomor rangka yang diberikan {len(n)} karakter — VIN 17 karakter, "
                     f"kemungkinan KELEBIHAN {sel} karakter. Sampaikan ke user & minta cek ulang.")
        return {"panjang": len(n), "harus": 17, "selisih": len(n) - 17, "pesan": pesan}
    if any(c in "IOQ" for c in n):
        return {"panjang": 17, "harus": 17, "selisih": 0,
                "pesan": "VIN memuat huruf I/O/Q yang tak pernah dipakai di VIN — kemungkinan "
                         "salah baca 1/0/O. Sampaikan ke user & minta cek ulang."}
    cd = check_digit(n)
    if n[8] != cd:
        return {"panjang": 17, "harus": 17, "selisih": 0, "check_digit_salah": True,
                "pesan": ("VIN 17 karakter tapi check digit (karakter ke-9) TIDAK cocok — "
                          "kemungkinan salah ketik 1 karakter. Sampaikan ke user & minta cek ulang.")}
    return None


def available() -> bool:
    return True  # endpoint publik, tanpa auth


def get_config(rangka: str) -> dict:
    """Ambil config mentah dari EPC (cache in-memory).

    TIGA kembalian yang WAJIB dibedakan pemanggil — dulu ketiganya sama-sama `{}`,
    sehingga EPC yang MATI dilaporkan ke user sebagai "nomor rangka Anda salah":
      - dict berisi field  → unit dikenal EPC;
      - `{}`               → EPC menjawab, unit TIDAK dikenal (jawaban sah);
      - `{"_err": "network"}` → EPC gagal dihubungi/dibaca — status unit BELUM
        diketahui. ⛔ JANGAN diperlakukan sebagai 'tak dikenal'.
    Sentinel dipilih berkunci '_err' (bukan exception) agar pemanggil lama yang
    hanya memeriksa kebenaran-nilai tetap jalan, cuma tak lagi menyalahkan user.
    """
    cjh = _frame(rangka)
    if not cjh:
        return {}
    with _lock:
        if cjh in _cache:
            return _cache[cjh]
    data: dict = {}
    err: str | None = None
    # Retry 1x utk blip jaringan — dulu satu ConnectionError = 'rangka tidak
    # ditemukan' palsu di giliran itu (miss memang tak di-cache, tapi jawaban
    # asisten giliran tsb sudah terlanjur salah).
    for _attempt in (1, 2):
        try:
            r = requests.get(_CONFIG_URL, params={"chassisNo": cjh},
                             timeout=20, verify=False)
            j = r.json()
            if isinstance(j, dict) and j.get("success") and isinstance(j.get("data"), dict):
                data = j["data"]
            err = None       # server MENJAWAB (walau 'tak dikenal') → bukan error
            break
        except Exception:
            err = "network"
            if _attempt == 1:
                time.sleep(0.8)
                continue
            data = {}
    # Cache HANYA hit asli. Jangan cache {} dari error jaringan / respons gagal —
    # tanpa TTL, satu blip akan permanen jadi 'rangka tidak ditemukan' sepanjang
    # proses hidup. Miss dibiarkan tak ter-cache → panggilan berikut coba lagi.
    if data:
        with _lock:
            _cache[cjh] = data
        return data
    return {"_err": err} if err else {}


def lookup(rangka: str) -> dict:
    """Ringkasan config kendaraan (field bersih + sebagian enum diterjemahkan)."""
    d = get_config(rangka)
    if d.get("_err"):
        # EPC tak terjangkau ≠ nomor rangka salah. Pisahkan, supaya asisten tak
        # menyuruh user "cek ejaan" padahal servernya yang sedang mati.
        return {"found": False, "input": (rangka or "").strip(),
                "frame_number": _frame(rangka), "_err": d["_err"],
                "catatan": "EPC Sinotruk GAGAL DIHUBUNGI (jaringan) — status unit ini "
                           "BELUM bisa dipastikan. ⛔ JANGAN bilang nomor rangkanya "
                           "salah/tak terdaftar; minta user coba lagi sebentar."}
    if not d:
        out = {"found": False, "input": (rangka or "").strip(),
               "frame_number": _frame(rangka),
               "catatan": "Nomor rangka tidak ditemukan di EPC Sinotruk (cek ejaan; "
                          "EPC hanya memuat unit Sinotruk/HOWO/SITRAK)."}
        fb = periksa_bentuk_rangka(rangka)
        if fb:
            out["format_rangka"] = fb
            out["catatan"] = fb["pesan"] + " " + out["catatan"]
        return out
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
