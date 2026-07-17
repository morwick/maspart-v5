"""Lembar diagnosa per-SPN/FMI (isi 216 kartu PDF data/Fault) — TERSTRUKTUR,
dibaca MODEL (bukan cuma kartu unduhan user).

Data: `dtc_diagnosa.json.gz` — di-generate `tools/build_fault_cards.py`
(pdfplumber atas data/Fault/*.pdf; English, kamus i18n bertahap). Tiap baris:
  {spn, fmi, kode, no_kartu, varian, file, judul, part_terkait,
   kondisi_trigger, reaksi, penyebab:[...], langkah:[...], tes_setelah}

Kartu PDF tetap disajikan via fault_pdf.py — modul ini pelengkap teksnya.
Regenerasi: python backend/tools/build_fault_cards.py
"""
from __future__ import annotations

from pathlib import Path

from .knowledge_util import load_json

_DATA = Path(__file__).parent / "dtc_diagnosa.json.gz"


def _load() -> list[dict]:
    return load_json(_DATA)


def available() -> bool:
    return bool(_load())


def count() -> int:
    return len(_load())


def pairs() -> set[tuple[int, int]]:
    """Semua pasangan (spn, fmi) yang punya lembar diagnosa."""
    return {(r["spn"], r["fmi"]) for r in _load()}


def for_pair(spn: int | None, fmi: int | None) -> list[dict]:
    """Lembar diagnosa utk pasangan SPN+FMI persis (bisa >1: varian E5_020E dst).
    fmi None → semua kartu utk SPN itu."""
    if spn is None:
        return []
    return [r for r in _load()
            if r["spn"] == spn and (fmi is None or r["fmi"] == fmi)]


def for_code(code: str) -> list[dict]:
    """Lembar diagnosa utk kode P (eksak, uppercase)."""
    c = (code or "").strip().upper()
    if not c:
        return []
    return [r for r in _load() if r.get("kode") == c]
