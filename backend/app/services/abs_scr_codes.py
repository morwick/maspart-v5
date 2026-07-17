"""Kode kesalahan (DTC) rem ABS (WABCO/Sinotruk) + SCR gas 国V — Bahasa Indonesia.

Data di-generate `tools/build_abs_scr.py` dari 2 Excel di data/manuals/
(kurasi pemilik, sudah diterjemahkan Indonesia via kamus statik) ke
`abs_scr_codes.json.gz`. Tiap entri:
  sistem   : "ABS" | "SCR"  — dipakai sbg nilai filter `unit`
  kode     : SCR = kode P (P0427, P1D06…); ABS = "" (dicari via SPN/FMI)
  spn,fmi  : ABS (J1939); None utk SCR
  sid,blink: ABS (SID + Blink Code, mis. "3 + 2")
  komponen : ABS = nama komponen SPN; SCR = Part
  deskripsi: arti gangguan
  penyebab : ABS = sifat sinyal (arti FMI); SCR = strategi monitoring
  reaksi   : ABS = reaksi sistem (fungsi yg dinonaktifkan); SCR = batas torsi
  perbaikan: ABS = langkah perbaikan (Repair Guide); SCR = "" (tabel tak memuat)
  lampu    : ABS = lampu peringatan (mis. "Lampu Kuning"); SCR = ""

Pelengkap `fault_codes.py` (tabel Bosch mesin, ber-SPN/FMI) & `eol_dtc.py`
(EOL CNHTC, ber-kode P). Modul ini menutup ABS (SPN/FMI + langkah perbaikan)
yang tak muat di keduanya, dan tabel SCR gas 国V spesifik.
Regenerasi: python backend/tools/build_abs_scr.py
"""
from __future__ import annotations

import gzip
import json
from pathlib import Path

_DATA = Path(__file__).parent / "abs_scr_codes.json.gz"

_CACHE: dict = {"mtime": None, "data": []}


def _load() -> list[dict]:
    try:
        mt = _DATA.stat().st_mtime if _DATA.exists() else None
    except OSError:
        mt = None
    if mt != _CACHE["mtime"]:
        data: list[dict] = []
        try:
            if mt is not None:
                with gzip.open(_DATA, "rt", encoding="utf-8") as f:
                    data = json.load(f) or []
        except Exception:
            data = []
        _CACHE.update(mtime=mt, data=data)
    return _CACHE["data"]


def available() -> bool:
    return bool(_load())


def count() -> int:
    return len(_load())


def units() -> list[str]:
    seen, res = set(), []
    for r in _load():
        u = r.get("sistem") or ""
        if u and u not in seen:
            seen.add(u)
            res.append(u)
    return res


def _hay(r: dict) -> str:
    return (f"{r.get('komponen','')} {r.get('deskripsi','')} {r.get('penyebab','')} "
            f"{r.get('reaksi','')} {r.get('perbaikan','')} {r.get('blink','')}").lower()


def search(spn: int | None = None, fmi: int | None = None, code: str = "",
           query: str = "", unit: str = "", limit: int = 20) -> list[dict]:
    """Cari kode ABS/SCR. `spn`(+`fmi`) → tabel ABS (bila pasangan SPN+FMI tak
    ada, balikan semua FMI utk SPN itu — jujur, tak kosong senyap). `code` →
    kode P SCR (eksak → prefix). `query` → substring Indonesia. `unit` → filter
    "ABS"/"SCR". Semua opsional, bisa dikombinasi."""
    data = _load()
    u = (unit or "").strip().upper()
    if u:
        data = [r for r in data if (r.get("sistem") or "").upper() == u]

    # ── SPN/FMI (ABS) ──
    if spn is not None:
        by_spn = [r for r in data if r.get("spn") == spn]
        res = by_spn
        if fmi is not None:
            exact = [r for r in by_spn if r.get("fmi") == fmi]
            res = exact or by_spn  # pasangan tak ada → tampilkan FMI lain utk SPN itu
        if query:
            q = query.strip().lower()
            res = [r for r in res if q in _hay(r)]
        return res[:limit]

    # ── kode P (SCR) ──
    if code:
        c = code.strip().upper()
        exact = [r for r in data if r.get("kode") and r["kode"].upper() == c]
        res = exact or [r for r in data if r.get("kode") and r["kode"].upper().startswith(c)]
        if query:
            q = query.strip().lower()
            res = [r for r in res if q in _hay(r)]
        return res[:limit]

    # ── kata kunci ──
    if query:
        q = query.strip().lower()
        return [r for r in data if q in _hay(r) or (r.get("kode") and q in r["kode"].lower())][:limit]

    return data[:limit] if u else []
