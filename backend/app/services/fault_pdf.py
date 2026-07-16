"""Lembar diagnosa PDF resmi per-pasangan SPN/FMI — `data/Fault/*.pdf`.

Nama file: `SPN <n> FMI <m>.pdf` (beberapa punya suffix varian, mis.
`SPN 1387 FMI 0 (E5_020E).pdf` — satu pasangan bisa >1 file). Isi tiap PDF =
lembar kesalahan resmi: P-code, klasifikasi, lampu MIL/SVS, part terkait, dan
langkah diagnosa. 216 file / 215 pasangan / 83 SPN — 32 pasangan di antaranya
TIDAK ada di tabel teks (fault_codes) → PDF memperluas cakupan.

Dipakai `cari_kode_kesalahan`/`diagnosa` (ai_assistant): bila pasangan SPN/FMI
yang ditanya punya PDF, file di-stash `ai_export.stash_raw` → tampil sebagai
KARTU di chat (kanal `excel_exports`) dan bisa dibuka langsung oleh user.

Deploy: scp `data/Fault/` ke `/opt/maspart/data/Fault/` (tak di-track git).
Index di-cache per-mtime direktori → admin menambah PDF, termuat tanpa restart.
"""
from __future__ import annotations

import re
from pathlib import Path

from ..core.config import get_settings

_CACHE: dict = {"mtime": None, "rows": []}

_NAME_RE = re.compile(r"^SPN (\d+) FMI (\d+)(?:\s*\([^)]*\))?\.pdf$", re.IGNORECASE)


def _dir() -> Path:
    return get_settings().data_path / "Fault"


def _load() -> list[dict]:
    d = _dir()
    try:
        mt = d.stat().st_mtime if d.is_dir() else None
    except OSError:
        mt = None
    if mt != _CACHE["mtime"]:
        rows: list[dict] = []
        try:
            if mt is not None:
                for p in sorted(d.glob("*.pdf")):
                    m = _NAME_RE.match(p.name)
                    if m:
                        rows.append({"file": p.name,
                                     "spn": int(m.group(1)),
                                     "fmi": int(m.group(2))})
        except OSError:
            rows = []
        _CACHE.update(mtime=mt, rows=rows)
    return _CACHE["rows"]


def available() -> bool:
    return bool(_load())


def count() -> int:
    return len(_load())


def find(spn: int | None, fmi: int | None = None) -> list[dict]:
    """PDF untuk SPN (+FMI). fmi=None → semua FMI utk SPN itu (urut FMI)."""
    if spn is None:
        return []
    rows = [r for r in _load() if r["spn"] == int(spn)]
    if fmi is not None:
        rows = [r for r in rows if r["fmi"] == int(fmi)]
    return sorted(rows, key=lambda r: (r["fmi"], r["file"]))


def pdf_bytes(fname: str) -> bytes | None:
    """Bytes PDF — nama wajib terdaftar di index (anti path-traversal)."""
    f = (fname or "").strip()
    if not any(r["file"] == f for r in _load()):
        return None
    p = _dir() / f
    try:
        return p.read_bytes() if p.is_file() else None
    except OSError:
        return None
