"""Skema/manual referensi teknik (PDF) — `data/manuals/skema/` + `index.json`.

Sumber: 5 PDF pengetahuan dari pabrikan (dikumpulkan pemilik di
`data/manuals/`), dipindah & diberi nama pendek-aman ke `data/manuals/skema/`:
  - `abs_kb_6x4_traktor.pdf`      — skema pneumatik ABS traktor 6x4 tipe KB
  - `abs_wabco_6x4_traktor.pdf`   — skema pneumatik ABS WABCO traktor 6x4
  - `abs_6x4_nontraktor.pdf`      — skema pneumatik ABS 6x4 non-traktor (rigid)
  - `listrik_hohan_howo_n_mc_a12.pdf` — skema kelistrikan HOHAN N/HOWO N mesin MC
  - `manual_tft_nanobcu.pdf`      — manual servis instrumen TFT NanoBCU (Indonesia)

`index.json` = list of dict {file, label, deskripsi, sinonim:[..]} — sumber
tunggal metadata pencarian. Regenerasi/penambahan skema = EDIT `index.json`
(tambah entri), tak perlu ubah kode.

Deploy: file besar (PDF) TIDAK di-track git — dikirim via scp pola
`data/manuals` (`index.json` + PDF di `data/` ikut scp). Cache index per-mtime →
admin menambah PDF + entri, termuat tanpa restart. Dibaca on-demand: PDF di-stash
`ai_export.stash_raw` supaya tampil sebagai kartu di chat (pola `fault_pdf`).
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from ..core.config import get_settings

_CACHE: dict = {"mtime": None, "rows": []}

_FNAME_RE = re.compile(r"^[A-Za-z0-9_.\-]+\.pdf$")


def _dir() -> Path:
    return get_settings().data_path / "manuals" / "skema"


def _load() -> list[dict]:
    p = _dir() / "index.json"
    try:
        mt = p.stat().st_mtime if p.is_file() else None
    except OSError:
        mt = None
    if mt != _CACHE["mtime"]:
        rows: list[dict] = []
        try:
            if mt is not None:
                rows = json.loads(p.read_text(encoding="utf-8")) or []
        except Exception:
            rows = []
        _CACHE.update(mtime=mt, rows=rows)
    return _CACHE["rows"]


def available() -> bool:
    return bool(_load())


def count() -> int:
    return len(_load())


def labels() -> list[str]:
    return [r.get("label") or r.get("file") for r in _load()]


def _terms(q: str) -> list[str]:
    """Query penuh + tiap kata (≥2 huruf) — supaya frasa sinonim multi-kata
    ('rem angin', 'non traktor') tetap cocok saat ditanya per-kata atau utuh."""
    ql = (q or "").strip().lower()
    if not ql:
        return []
    terms = [ql]
    for w in re.findall(r"[a-z0-9]+", ql):
        if len(w) >= 2 and w not in terms:
            terms.append(w)
    return terms


def search(query: str = "", limit: int = 4) -> list[dict]:
    """Cari skema/manual by label + deskripsi + sinonim + nama file (case-
    insensitive, batas kata). Query kosong → []."""
    terms = _terms(query)
    if not terms:
        return []
    out = []
    for r in _load():
        syn = " ".join(r.get("sinonim") or [])
        hay = f"{r.get('label','')} {r.get('deskripsi','')} {syn} {r.get('file','')}".lower()
        # Batas kata (angka/huruf) — cegah 'kabin' menyeret 'kb', dsb.
        if any(re.search(r"(?<![a-z0-9])" + re.escape(t) + r"(?![a-z0-9])", hay)
               for t in terms):
            out.append(r)
    return out[:limit]


def pdf_bytes(fname: str) -> bytes | None:
    """Bytes PDF — nama file divalidasi ketat + wajib terdaftar di index
    (anti path-traversal)."""
    f = (fname or "").strip()
    if not _FNAME_RE.match(f):
        return None
    if not any(r.get("file") == f for r in _load()):
        return None
    p = _dir() / f
    try:
        return p.read_bytes() if p.is_file() else None
    except OSError:
        return None
