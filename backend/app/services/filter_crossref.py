"""
Service: PADANAN AFTERMARKET filter (PN OEM → PN merek lain) — data/filter_crossref.json.gz
dipanen oleh backend/tools/build_filter_crossref.py.

⚠️ STATUS SUMBER: PIHAK KETIGA (oilfilter-crossreference.com), anonim, TANPA
lisensi data eksplisit. Karena itu seluruh keluaran modul ini membawa
`peringatan` dan pemanggil WAJIB menyajikannya sebagai REFERENSI, ⛔ bukan
"padanan resmi pabrik" seperti supersession SIMS/Weichai.

⚠️ CAKUPAN RENDAH: hanya ~8-17% PN filter kita ada di sana (diukur, bukan
ditaksir). `found: False` di sini karena itu SANGAT sering, dan artinya
"tidak ada di basis pihak ketiga" — ⛔ BUKAN "tidak ada padanan aftermarket".

⚠️ ENTRI GENERIK: sebagian PN membalas ratusan "padanan" lintas merek yang tak
berhubungan (mis. merek kompresor udara untuk sebuah "Filter Element" generik).
Baris seperti itu ditandai `generik: True` saat panen — perlakukan sebagai
sinyal LEMAH dan jangan disodorkan sebagai rekomendasi.
"""
from __future__ import annotations

import gzip
import json
import re
import threading

from ..core.config import get_settings

_CACHE: dict = {"mtime": None, "data": {}}
_lock = threading.Lock()

_MAKS_PADANAN = 25          # cukup untuk jawaban chat; sisanya dilaporkan jumlahnya


def _path():
    return get_settings().data_path / "filter_crossref.json.gz"


def _norm(pn: str) -> str:
    return re.sub(r"[\s_\-/]", "", (pn or "")).upper()


def _load() -> dict:
    try:
        p = _path()
        mt = p.stat().st_mtime if p.exists() else None
    except Exception:
        mt = None
    if mt != _CACHE["mtime"]:
        with _lock:
            if mt != _CACHE["mtime"]:
                data: dict = {}
                if mt is not None:
                    try:
                        with gzip.open(_path(), "rt", encoding="utf-8") as f:
                            data = json.load(f) or {}
                    except Exception:
                        data = {}
                idx = {}
                for pn, v in (data.get("data") or {}).items():
                    idx[_norm(pn)] = {**v, "pn_oem": pn}
                data["_idx"] = idx
                _CACHE.update(mtime=mt, data=data)
    return _CACHE["data"]


def available() -> bool:
    return bool(_load().get("_idx"))


def status() -> dict:
    d = _load()
    return {"tersedia": bool(d.get("_idx")),
            "pn_berpadanan": len(d.get("_idx") or {}),
            "pn_dicek": d.get("pn_dicek") or 0,
            "sumber": d.get("sumber") or "",
            "diambil": d.get("diambil") or ""}


def cari(part_number: str) -> dict:
    """Padanan aftermarket untuk satu PN filter.

    {found, pn_oem, padanan:[{merek, pn}], jumlah, generik?, peringatan} atau
    {found: False, alasan}. ⛔ found=False BUKAN bukti tak ada padanan — cakupan
    sumbernya memang rendah."""
    d = _load()
    pn = (part_number or "").strip().upper()
    if not pn:
        return {"found": False, "alasan": "input"}
    if not d.get("_idx"):
        return {"found": False, "alasan": "no_data",
                "pesan": "Data padanan aftermarket belum tersedia di server."}
    e = d["_idx"].get(_norm(pn))
    if not e:
        return {"found": False, "part_number": pn, "alasan": "tak_ada_di_sumber",
                "pesan": ("PN ini tidak ada di basis padanan pihak ketiga "
                          "(cakupannya memang rendah) — BUKAN berarti tak ada "
                          "padanan aftermarket.")}
    padanan = e.get("padanan") or []
    out = {
        "found": True,
        "part_number": pn,
        "pn_oem": e.get("pn_oem") or pn,
        "nama": e.get("nama") or "",
        "jumlah": e.get("jumlah") or len(padanan),
        "padanan": padanan[:_MAKS_PADANAN],
        "sumber": d.get("sumber") or "",
        "peringatan": ("Referensi PIHAK KETIGA, bukan padanan resmi pabrik. "
                       "Cocokkan spesifikasi/fisik sebelum dipakai."),
    }
    if len(padanan) > _MAKS_PADANAN:
        out["terpotong"] = len(padanan) - _MAKS_PADANAN
    if e.get("generik"):
        out["generik"] = True
        out["peringatan"] = (
            "⚠️ SINYAL LEMAH: PN ini membalas ratusan 'padanan' lintas merek yang "
            "belum tentu berhubungan (entri catch-all di sumber pihak ketiga). "
            "JANGAN direkomendasikan; sebut sebagai petunjuk kasar saja.")
    return out
