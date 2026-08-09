"""
Service: JADWAL SERVIS BERBASIS KM truk Sinotruk — jadwal_servis_truk.json.gz
(dibangun tools/build_jadwal_servis_truk.py dari PDF RESMI cnhtcgroup.com).

Beda dari `maintenance_ref` (jadwal_perawatan): itu ALAT BERAT Shantui berbasis
JAM. Yang ini TRUK berbasis KILOMETER — dua semantik berbeda, jangan dicampur.

⚠️ CAKUPAN: dokumennya berjudul "HOWO 371HP engine, axle type trucks" (gardan
MCY13). ⛔ Angka di sini TIDAK boleh disodorkan sebagai spesifikasi NX/SITRAK/
V7X/HOMAN — dokumen OEM lain memberi angka gardan berbeda untuk varian lain.
Setiap keluaran modul ini membawa `cakupan` supaya batas itu ikut terbaca.
"""
from __future__ import annotations

import gzip
import json
import threading
from pathlib import Path

_DATA = Path(__file__).parent / "jadwal_servis_truk.json.gz"
_CACHE: dict = {"mtime": None, "data": {}}
_lock = threading.Lock()


def _load() -> dict:
    try:
        mt = _DATA.stat().st_mtime if _DATA.exists() else None
    except Exception:
        mt = None
    if mt != _CACHE["mtime"]:
        with _lock:
            if mt != _CACHE["mtime"]:
                d: dict = {}
                if mt is not None:
                    try:
                        with gzip.open(_DATA, "rt", encoding="utf-8") as f:
                            d = json.load(f) or {}
                    except Exception:
                        d = {}
                _CACHE.update(mtime=mt, data=d)
    return _CACHE["data"]


def available() -> bool:
    return bool(_load().get("item"))


def meta() -> dict:
    d = _load()
    return {k: d.get(k) for k in ("sumber", "url", "cakupan", "diambil",
                                  "interval_km", "arti_kode", "peringatan")}


def cairan(nama: str = "") -> list[dict]:
    """Kapasitas & spesifikasi cairan. `nama` menyaring (mis. 'coolant', 'gardan')."""
    q = " ".join((nama or "").split()).lower()
    out = _load().get("cairan") or []
    if not q:
        return out
    # Istilah lapangan Indonesia → kata di dokumen (dokumennya Bahasa Inggris).
    sinonim = {
        "oli mesin": "engine oil", "oli gardan": "differential", "gardan": "differential",
        "oli transmisi": "transmission", "transmisi": "transmission", "persneling": "transmission",
        "radiator": "coolant", "air radiator": "coolant", "pendingin": "coolant",
        "kopling": "clutch", "minyak kopling": "clutch", "power steering": "steering",
        "setir": "steering", "kemudi": "steering",
    }
    q = sinonim.get(q, q)
    return [c for c in out
            if q in c["item"].lower() or q in (c.get("kategori") or "").lower()
            or q in (c.get("spesifikasi") or "").lower()]


def _terdekat(km: int, interval: list[int]) -> int | None:
    """Interval terdekat yang <= km bila persis tak ada; None bila di bawah semua."""
    if km in interval:
        return km
    lebih_kecil = [x for x in interval if x <= km]
    return max(lebih_kecil) if lebih_kecil else None


def pada_km(km: int) -> dict:
    """Pekerjaan servis pada jarak tempuh tertentu.

    ⛔ Bila `km` bukan salah satu interval resmi, TIDAK dikira-kira diam-diam:
    interval terdekat dikembalikan bersama penanda `interval_persis=False`."""
    d = _load()
    interval = d.get("interval_km") or []
    if not interval:
        return {"found": False, "alasan": "no_data"}
    try:
        km = int(km)
    except (TypeError, ValueError):
        return {"found": False, "alasan": "input"}
    dipakai = _terdekat(km, interval)
    if dipakai is None:
        return {"found": False, "km_diminta": km, "interval_km": interval,
                "alasan": "di_bawah_interval_pertama",
                "pesan": (f"{km:,} km berada di bawah servis pertama "
                          f"({interval[0]:,} km).").replace(",", ".")}
    i = interval.index(dipakai)
    kerja = []
    for it in (d.get("item") or []):
        kode = (it.get("kode") or [])
        if i < len(kode) and kode[i] and kode[i] != "I":
            kerja.append({"kategori": it["kategori"], "item": it["item"],
                          "kode": kode[i], "arti": (d.get("arti_kode") or {}).get(kode[i], ""),
                          **({"catatan": it["catatan"]} if it.get("catatan") else {})})
    periksa = sum(1 for it in (d.get("item") or [])
                  if i < len(it.get("kode") or []) and (it["kode"][i] == "I"))
    return {"found": True, "km_diminta": km, "km_interval": dipakai,
            "interval_persis": dipakai == km, "interval_km": interval,
            "pekerjaan": kerja, "jumlah_item_periksa_rutin": periksa,
            "cakupan": d.get("cakupan"), "sumber": d.get("sumber")}
