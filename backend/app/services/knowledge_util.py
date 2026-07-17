"""Util bersama untuk dataset pengetahuan asisten AI (skema kanonik).

Menyatukan pola yang selama ini di-copy antar service/builder:
  norm_pn()       : normalisasi part number kanonik (union 2 varian lama).
  load_json()     : loader dataset dgn cache per-mtime; auto-deteksi .gz.
  write_json_gz() : writer deterministik byte-stable (regenerasi tanpa
                    perubahan data = byte identik; aman utk git diff & cache).
  apply_i18n()    : terapkan kamus terjemahan statik {teks asli → Indonesia}
                    pada kolom terpilih; fallback = teks asli (build tak gagal
                    saat kamus belum lengkap).
  dump_strings()  : kumpulkan string unik dari kolom terpilih — bahan mengisi
                    kamus i18n (pola build_abs_scr --dump-strings).

Konvensi dataset kanonik (lihat plan rombakan 2026-07-17):
  - payload = list-of-dict FLAT, kolom Indonesia snake_case;
  - teks panjang = string ATAU list-of-string (langkah bernomor), jangan campur;
  - >100 KB → .json.gz, kecil → .json; TANPA timestamp di payload.
"""
from __future__ import annotations

import gzip
import json
import re
import threading
from pathlib import Path

# ── normalisasi PN ───────────────────────────────────────────────────
# Union dua varian lama: catalog_bom/repairkit buang [spasi _ - /],
# filter_ref/maintenance_ref buang [spasi _ - ( ) （ ）]. Kanonik = gabungan.
_PN_STRIP_RE = re.compile(r"[\s_\-/()（）]")


def norm_pn(s: str | None) -> str:
    """Part number kanonik: buang spasi/_/-///kurung, uppercase."""
    return _PN_STRIP_RE.sub("", str(s or "")).upper()


# ── loader cache per-mtime ───────────────────────────────────────────
_LOAD_CACHE: dict[str, dict] = {}
_LOAD_LOCK = threading.Lock()


def load_json(path: Path | str, default=None):
    """Baca file JSON (auto-gunzip bila berakhiran .gz) dgn cache per-mtime.

    File hilang/rusak → `default` (default: list kosong) — jangan crash
    produksi. Thread-safe; satu entri cache per path absolut.
    """
    p = Path(path)
    key = str(p.resolve()) if p.is_absolute() else str(p)
    try:
        mt = p.stat().st_mtime if p.exists() else None
    except OSError:
        mt = None
    with _LOAD_LOCK:
        ent = _LOAD_CACHE.get(key)
        if ent is not None and ent["mtime"] == mt:
            return ent["data"]
    data = [] if default is None else default
    try:
        if mt is not None:
            if p.suffix == ".gz":
                with gzip.open(p, "rt", encoding="utf-8") as f:
                    data = json.load(f)
            else:
                data = json.loads(p.read_text(encoding="utf-8"))
            if data is None:
                data = [] if default is None else default
    except Exception:
        data = [] if default is None else default
    with _LOAD_LOCK:
        _LOAD_CACHE[key] = {"mtime": mt, "data": data}
    return data


# ── writer deterministik ─────────────────────────────────────────────
def write_json_gz(path: Path | str, rows) -> None:
    """Tulis dataset ke .json.gz byte-stable: kunci di-sort, tanpa timestamp
    gzip (mtime=0), kompak. Dua build dgn data sama → byte identik."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(rows, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":")).encode("utf-8")
    with open(p, "wb") as raw:
        # filename="" + mtime=0 → header gzip identik apa pun nama/waktu file
        with gzip.GzipFile(filename="", fileobj=raw, mode="wb", mtime=0) as gz:
            gz.write(payload)


# ── i18n statik (pola build_abs_scr) ─────────────────────────────────
def _iter_texts(v):
    """String tunggal ATAU list-of-string → iterasi (idx, teks)."""
    if isinstance(v, list):
        for i, t in enumerate(v):
            if isinstance(t, str):
                yield i, t
    elif isinstance(v, str):
        yield None, v


def apply_i18n(rows: list[dict], kamus: dict, fields: tuple[str, ...]) -> int:
    """Terapkan kamus {asli → Indonesia} pada kolom `fields` tiap baris.
    Mendukung nilai string maupun list-of-string. String tak ada di kamus
    dibiarkan apa adanya (fallback aman). Return: jumlah string miss."""
    miss = 0
    for r in rows:
        for f in fields:
            v = r.get(f)
            for i, t in _iter_texts(v):
                t2 = t.strip()
                if not t2:
                    continue
                tr = kamus.get(t2)
                if tr:
                    if i is None:
                        r[f] = tr
                    else:
                        v[i] = tr
                else:
                    miss += 1
    return miss


def dump_strings(rows: list[dict], fields: tuple[str, ...]) -> list[str]:
    """String unik (terurut) dari kolom `fields` — bahan kamus i18n."""
    vals: set[str] = set()
    for r in rows:
        for f in fields:
            for _, t in _iter_texts(r.get(f)):
                t2 = t.strip()
                if t2:
                    vals.add(t2)
    return sorted(vals)
