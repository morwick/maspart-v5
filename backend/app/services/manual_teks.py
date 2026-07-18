"""Teks & tabel MANUAL teknik (Bosch ECU China + TFT NanoBCU) — bisa dicari &
dijawab asisten. Store `manual_teks.json.gz` (builder `tools/build_manual_teks.py`).

Record page-level: {sumber, halaman, judul, judul_id, kata_kunci:[], teks, tabel,
tipe, dicari, gambar_ref:[], blok?, kode?}. `teks` = teks halaman China APA ADANYA
(model menerjemahkan runtime, pola pin_ecu). `judul_id`+`kata_kunci` = kurasi
Indonesia (supaya bisa dicari pakai istilah Indonesia — teks aslinya China).
`blok` = struktur kartu gangguan {kondisi_trigger,reaksi,penyebab[],langkah[],
tes_setelah,catatan}. `gambar_ref` = PNG halaman dari `manual_media` (tampil inline).

`dicari=True` = kartu gangguan Bosch + semua halaman TFT (tabel DTC/pin Bosch
tidak — sudah dilayani cari_kode_kesalahan/pin_ecu, tetap findable via 'kode').

Disajikan tool `cari_manual`: teks/tabel/blok + gambar halaman inline + kartu PDF
(TFT via skema_ref). Loader tipis pola `manual_media`. `.json.gz` ikut git.
"""
from __future__ import annotations

import re
from pathlib import Path

from .knowledge_util import load_json

_DATA = Path(__file__).parent / "manual_teks.json.gz"


def _load() -> list[dict]:
    return load_json(_DATA)


def available() -> bool:
    return bool(_load())


def count() -> int:
    return len(_load())


def count_dicari() -> int:
    """Jumlah record yang boleh muncul dari cari_manual (kartu gangguan + TFT)."""
    return sum(1 for r in _load() if r.get("dicari"))


def _hit(term: str, hay: str) -> bool:
    return bool(re.search(r"(?<![a-z0-9])" + re.escape(term) + r"(?![a-z0-9])", hay))


def _score(r: dict, ql: str, words: list[str]) -> int:
    """Peringkat: judul_id/kata_kunci (Indonesia kurasi) > judul > teks (China) >
    kode. Token ber-ANGKA (kode P/SPN) ×3; frasa penuh cocok → bonus. Karena teks
    aslinya China, kueri Indonesia terutama cocok di judul_id/kata_kunci."""
    idn = (f"{r.get('judul_id','')} {' '.join(r.get('kata_kunci') or [])}").lower()
    judul = (r.get("judul") or "").lower()
    teks = (r.get("teks") or "").lower()
    kode = " ".join(r.get("kode") or []).lower()
    s = 0
    if ql and (_hit(ql, idn) or _hit(ql, judul)):
        s += 50
    for w in words:
        spec = 3 if any(c.isdigit() for c in w) else 1
        if _hit(w, idn):
            s += 5 * spec
        elif _hit(w, judul):
            s += 3 * spec
        elif _hit(w, kode):
            s += 4 * spec
        elif _hit(w, teks):
            s += 1 * spec
    return s


def search(topik: str = "", limit: int = 5) -> list[dict]:
    """Cari record TERKURASI (dicari=True), diranking relevansi. Query kosong → []."""
    ql = (topik or "").strip().lower()
    if not ql:
        return []
    words = [w for w in re.findall(r"[a-z0-9]+", ql) if len(w) >= 2]
    scored: list[tuple[int, int, dict]] = []
    for i, r in enumerate(_load()):
        if not r.get("dicari"):
            continue
        sc = _score(r, ql, words)
        if sc > 0:
            scored.append((sc, i, r))
    scored.sort(key=lambda t: (-t[0], t[1]))
    return [r for _, _, r in scored[:limit]]


def for_page(sumber: str, halaman: int) -> dict | None:
    if not sumber or not halaman:
        return None
    return next((r for r in _load()
                 if r.get("sumber") == sumber and r.get("halaman") == halaman), None)
