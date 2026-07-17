"""Perpustakaan GAMBAR manual (diagram/skema/pinout/foto unit) — PNG di
`data/manual_media/` + `index.json`.

Diekstrak `tools/build_manual_media.py` dari sumber mentah `data/manuals/`
(PDF pin/ECU, skema pneumatik ABS, skema kelistrikan, foto unit Excel). Sumber
selama ini hanya diambil TABEL-nya; builder ini mengeluarkan GAMBAR di dalamnya.

`index.json` record: {file, label, deskripsi, sinonim:[], sumber, halaman, tipe,
dicari, seed_teks}. `dicari=True` = boleh muncul dari pencarian bebas tool
`diagram_wiring` (set kecil terkurasi: pinout/skema/foto_unit + diagram penting
yang dipromosikan kurator). Gambar naratif (boschcn/tft) `dicari=False` tetap
bisa dilayani `image_bytes()` (ditautkan per-halaman oleh `manual_teks`).

Deploy: PNG di-scp (pola `data/wiring` — aset besar tak di-track git); `index.json`
ikut git. Cache index per-mtime. Gambar dibaca on-demand oleh handler, di-stash
`ai_export.stash_raw` supaya tampil INLINE (kanal `exploded_images`).
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from ..core.config import get_settings

_CACHE: dict = {"mtime": None, "rows": []}

_FNAME_RE = re.compile(r"^[A-Za-z0-9_.\-]+\.png$")


def _dir() -> Path:
    return get_settings().data_path / "manual_media"


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


def count_dicari() -> int:
    """Jumlah gambar TERKURASI yang boleh muncul dari pencarian bebas."""
    return sum(1 for r in _load() if r.get("dicari"))


def labels() -> list[str]:
    return [r.get("label") or r.get("file") for r in _load()]


def _hit(term: str, hay: str) -> bool:
    return bool(re.search(r"(?<![a-z0-9])" + re.escape(term) + r"(?![a-z0-9])", hay))


def _score(r: dict, ql: str, words: list[str]) -> int:
    """Skor relevansi — korpus 50 gambar banyak berbagi kata umum ('skema',
    'unit', 'foto'), jadi butuh peringkat: cocok di LABEL/SINONIM > deskripsi >
    sumber/tipe; token ber-ANGKA (kode model 'sd16', 'se210w', 'x1') paling
    diskriminatif → bobot ×3; frasa penuh cocok → bonus."""
    label_syn = (f"{r.get('label','')} {' '.join(r.get('sinonim') or [])}").lower()
    desc = (r.get("deskripsi") or "").lower()
    meta = f"{r.get('sumber','')} {r.get('tipe','')}".lower()
    s = 0
    if ql and _hit(ql, f"{label_syn} {desc}"):
        s += 50
    for w in words:
        spec = 3 if any(c.isdigit() for c in w) else 1
        if _hit(w, label_syn):
            s += 4 * spec
        elif _hit(w, desc):
            s += 2 * spec
        elif _hit(w, meta):
            s += 1 * spec
    return s


def search(query: str = "", limit: int = 6) -> list[dict]:
    """Cari gambar TERKURASI (dicari=True), diranking relevansi (label/sinonim >
    deskripsi > sumber/tipe; kode model berbobot lebih). Query kosong → []."""
    ql = (query or "").strip().lower()
    if not ql:
        return []
    words = [w for w in re.findall(r"[a-z0-9]+", ql) if len(w) >= 2]
    scored: list[tuple[int, int, dict]] = []
    for i, r in enumerate(_load()):
        if not r.get("dicari"):
            continue
        sc = _score(r, ql, words)
        if sc > 0:
            scored.append((sc, i, r))  # i = tie-break stabil (urutan index)
    scored.sort(key=lambda t: (-t[0], t[1]))
    return [r for _, _, r in scored[:limit]]


def for_page(sumber: str, halaman: int) -> list[dict]:
    """Gambar sebuah halaman sumber tertentu — dipakai `manual_teks` untuk
    menautkan gambar ke jawaban teks (kontekstual, tak perlu dicari)."""
    if not sumber or not halaman:
        return []
    return [r for r in _load()
            if r.get("sumber") == sumber and r.get("halaman") == halaman]


def image_bytes(fname: str) -> bytes | None:
    """Bytes PNG sebuah gambar — nama file divalidasi ketat + wajib terdaftar
    di index (anti path-traversal). Melayani semua gambar (termasuk dicari=False
    untuk tautan per-halaman)."""
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
