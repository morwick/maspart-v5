"""Kode kesalahan (DTC) EOL CNHTC — SEMUA unit kontrol truk Sinotruk,
Bahasa Indonesia + penyebab + LANGKAH PERBAIKAN + komponen terkait.

Data di-generate `tools/build_eol_dtc.py` dari artifact "Panduan Teknisi
Sinotruk" (sumber aplikasi EOL CNHTC, arsip D:/CNHTC_Data/) ke `eol_dtc.json.gz`
(±5.254 entri, 52 unit kontrol: EMS, TCU/TCUZF, ESP/ABS, BMS/VCU/MCU (EV),
BCM, ACU airbag, IFC/radar ADAS, SCR/AdBlue, dst). Tiap entri:
  unit      : unit kontrol (mis. "EMS", "ESP", "TCUZF", "BMS")
  kode      : kode DTC — P/B/U-code (EMS pakai UDS 7-char, mis. "P0100F7"
              = P0100 + suffix FMI-hex) atau kode hex/desimal EV
  deskripsi : arti gangguan (Bahasa Indonesia)
  penyebab  : kemungkinan penyebab (Indonesia)
  perbaikan : langkah perbaikan resmi EOL (Indonesia; bisa kosong)
  part      : komponen/sparepart terkait (bisa kosong)

Pelengkap `fault_codes.py` (tabel Bosch MC ber-SPN/FMI, mesin saja): EOL tak
punya SPN/FMI tapi jauh lebih luas + sudah Indonesia + ada cara perbaikan.
Regenerasi: python backend/tools/build_eol_dtc.py
(lalu python backend/tools/build_dtc_store.py — sejak rombakan 2026-07-17 modul
ini SHIM atas store kanonik `dtc_codes.json.gz`; eol_dtc.json.gz tinggal tabel
antara.)
"""
from __future__ import annotations

from . import dtc_codes


def _load() -> list[dict]:
    return dtc_codes.legacy_eol()


def available() -> bool:
    return bool(_load())


def count() -> int:
    return len(_load())


def units() -> list[str]:
    seen, res = set(), []
    for r in _load():
        u = r.get("unit") or ""
        if u and u not in seen:
            seen.add(u)
            res.append(u)
    return res


def search(code: str = "", query: str = "", unit: str = "",
           limit: int = 20) -> list[dict]:
    """Cari DTC EOL. `code`: eksak dulu, lalu PREFIX (P0100 → P0100F7…).
    `query`: substring Indonesia atas deskripsi+penyebab+part. `unit`: filter
    unit kontrol (EMS/ESP/TCU/BMS/…). Semua opsional, bisa dikombinasi."""
    data = _load()
    u = (unit or "").strip().upper()
    if u:
        data = [r for r in data if (r.get("unit") or "").upper() == u]

    if code:
        c = code.strip().upper()
        exact = [r for r in data if r["kode"] == c]
        res = exact or [r for r in data if r["kode"].startswith(c)]
        if query:
            q = query.strip().lower()
            res = [r for r in res
                   if q in f"{r['deskripsi']} {r['penyebab']} {r['part']}".lower()]
        return res[:limit]

    if query:
        q = query.strip().lower()
        return [r for r in data
                if q in f"{r['deskripsi']} {r['penyebab']} {r['part']}".lower()
                or q in r["kode"].lower()][:limit]

    return data[:limit] if u else []


def repair_for(code: str) -> dict | None:
    """Entri EOL ber-perbaikan untuk sebuah kode (eksak → prefix). Untuk
    menjembatani hasil SPN/FMI Bosch (P0645) ke langkah perbaikan EOL."""
    c = (code or "").strip().upper()
    if not c:
        return None
    best = None
    for r in _load():
        k = r["kode"]
        if k == c or k.startswith(c):
            if r.get("perbaikan"):
                if k == c:
                    return r
                if best is None:
                    best = r
    return best
