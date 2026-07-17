"""Lookup kode kesalahan (DTC) mesin Sinotruk/HOWO — ECU Bosch (MC).

Data di-generate oleh `tools/build_fault_codes.py` dari PDF sumber
`data/manuals/Manual_Sinotruk_MC_BOSCHECU_DH_CHINESE.pdf` ke `fault_codes.json`.
Tiap entri:
  code     : kode P/U (mis. "P0410")
  english  : label internal Bosch (mis. "DFC_AirCtlGovrDeMilTqLimrMax")
  desc_cn  : deskripsi gangguan (Bahasa China — Asisten AI menerjemahkan)
  spn      : Suspect Parameter Number (J1939) — bisa null bila tak tercantum
  fmi      : Failure Mode Identifier (J1939) — bisa null bila tak tercantum
  mil/svs  : status lampu indikator (ON/OFF)

Regenerasi bila PDF diperbarui:  python backend/tools/build_fault_codes.py
(lalu python backend/tools/build_dtc_store.py — sejak rombakan 2026-07-17 modul
ini SHIM atas store kanonik `dtc_codes.json.gz`; fault_codes.json tinggal tabel
antara. Baris kini juga membawa `deskripsi` (Indonesia, kamus statik) di samping
`desc_cn`.)
"""
from __future__ import annotations

import re

from . import dtc_codes


def _load() -> list[dict]:
    return dtc_codes.legacy_bosch()


def count() -> int:
    return len(_load())


def search(
    spn: int | None = None,
    fmi: int | None = None,
    code: str | None = None,
    query: str | None = None,
    limit: int = 20,
) -> list[dict]:
    """Cari kode kesalahan. Prioritas: SPN(+FMI) → kode P/U → kata kunci bebas."""
    data = _load()
    res = data

    if spn is not None:
        res = [r for r in res if r["spn"] == int(spn)]
        if fmi is not None:
            res = [r for r in res if r["fmi"] == int(fmi)]
        return res[:limit]

    if code:
        c = code.strip().upper()
        res = [r for r in res if r["code"].upper() == c]
        if not res:  # cocokkan awalan kalau tak ada yang persis
            res = [r for r in _load() if r["code"].upper().startswith(c)]
        return res[:limit]

    if query:
        q = query.strip().lower()
        res = [
            r for r in data
            if q in r["english"].lower() or q in r["desc_cn"].lower() or q in r["code"].lower()
        ]
        return res[:limit]

    return []


def parse_query(text: str) -> dict:
    """Ekstrak spn / fmi / kode dari teks bebas.
    Mis. 'kode kesalahan spn 1241 fmi 21' → {'spn':1241,'fmi':21}.
    Mendukung salah ketik umum 'fm1' untuk FMI."""
    t = (text or "").lower()
    out: dict = {}
    m = re.search(r"spn\s*[:#=]?\s*(\d+)", t)
    if m:
        out["spn"] = int(m.group(1))
    m = re.search(r"fm[i1l]\s*[:#=]?\s*(\d+)", t)
    if m:
        out["fmi"] = int(m.group(1))
    m = re.search(r"\b([pu]\d{4})\b", t)
    if m:
        out["code"] = m.group(1).upper()
    return out
