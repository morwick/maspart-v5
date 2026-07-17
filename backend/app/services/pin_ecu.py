"""Definisi PIN ECU Bosch MC (tabel konektor Manual Sinotruk MC BOSCHECU) —
'sensor X di pin berapa', 'pin K54 untuk apa', nilai uji per pin.

Data: `pin_ecu.json.gz` — di-generate `tools/build_pin_ecu.py` (302 pin).
Baris: {pin, sinyal (kode internal, mis. O_S_RL11), deskripsi (CN/EN),
nilai_uji}. Disajikan lewat tool diagram_wiring (fan-out — tanpa tool baru).
Regenerasi: python backend/tools/build_pin_ecu.py
"""
from __future__ import annotations

from pathlib import Path

from .knowledge_util import load_json

_DATA = Path(__file__).parent / "pin_ecu.json.gz"


def _load() -> list[dict]:
    return load_json(_DATA)


def available() -> bool:
    return bool(_load())


def count() -> int:
    return len(_load())


def search(query: str, limit: int = 12) -> list[dict]:
    """Cari per KODE PIN (K54/A12/101 — eksak, case-insensitive) atau substring
    bebas atas sinyal/deskripsi (CN maupun EN)."""
    q = (query or "").strip()
    if not q:
        return []
    qu = q.upper()
    eksak = [r for r in _load() if r["pin"].upper() == qu]
    if eksak:
        return eksak[:limit]
    ql = q.lower()
    return [r for r in _load()
            if ql in f"{r['sinyal']} {r['deskripsi']}".lower()][:limit]
