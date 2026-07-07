"""
Kamus sinonim istilah lapangan — CRUD untuk data/sinonim/sinonim.json.

File JSON ini SATU-SATUNYA sumber kamus. Asisten AI membacanya per-mtime
(ai_assistant._load_sinonim_entries) sehingga perubahan dari halaman admin
langsung terpakai TANPA restart: ekspansi query cari_part & kamus istilah di
prompt ikut segar pada permintaan berikutnya.

Format entri: {"grup": str, "triggers": [istilah lapangan/Indonesia...],
"keywords": [kata kunci nama part katalog/Inggris...]}.

Tulis ATOMIK (tmp + os.replace) agar pembaca tidak pernah melihat file
setengah jadi; serialisasi lewat lock proses (uvicorn kita single-process).
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path

from ..core.config import get_settings

_lock = threading.Lock()


def _file() -> Path:
    return get_settings().data_path / "sinonim" / "sinonim.json"


def _clean_list(vals) -> list[str]:
    """Rapikan daftar string: strip, buang kosong & duplikat (case-insensitive,
    urutan asli dipertahankan)."""
    out: list[str] = []
    seen: set[str] = set()
    for v in vals or []:
        s = str(v).strip()
        key = s.lower()
        if s and key not in seen:
            seen.add(key)
            out.append(s)
    return out


def _normalize(grup: str, triggers, keywords) -> dict:
    g = (grup or "").strip().lower() or "umum"
    trig = _clean_list(triggers)
    kw = _clean_list(keywords)
    if not trig:
        raise ValueError("Minimal satu istilah lapangan (trigger).")
    if not kw:
        raise ValueError("Minimal satu kata kunci katalog (keyword).")
    return {"grup": g, "triggers": trig, "keywords": kw}


def load() -> list[dict]:
    """Seluruh entri kamus (list kosong bila file belum ada/korup)."""
    p = _file()
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8")) or []
    except Exception:
        return []
    return [e for e in data if isinstance(e, dict)]


def _save(entries: list[dict]) -> None:
    p = _file()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, p)  # atomik: pembaca melihat file lama ATAU baru, tak pernah separuh


def add(grup: str, triggers, keywords) -> dict:
    """Tambah entri baru. Tolak bila trigger-set persis sama sudah ada (hindari
    duplikat tak sengaja — admin sebaiknya mengedit entri yang ada)."""
    e = _normalize(grup, triggers, keywords)
    with _lock:
        entries = load()
        new_set = {t.lower() for t in e["triggers"]}
        for old in entries:
            if {str(t).lower() for t in (old.get("triggers") or [])} == new_set:
                raise ValueError(
                    "Entri dengan istilah lapangan yang sama sudah ada "
                    f"(grup '{old.get('grup', '')}') — edit entri itu saja."
                )
        entries.append(e)
        _save(entries)
    return e


def update(index: int, grup: str, triggers, keywords) -> dict:
    e = _normalize(grup, triggers, keywords)
    with _lock:
        entries = load()
        if not 0 <= index < len(entries):
            raise IndexError("Entri tidak ditemukan — data mungkin berubah, muat ulang halaman.")
        entries[index] = e
        _save(entries)
    return e


def delete(index: int) -> dict:
    with _lock:
        entries = load()
        if not 0 <= index < len(entries):
            raise IndexError("Entri tidak ditemukan — data mungkin berubah, muat ulang halaman.")
        gone = entries.pop(index)
        _save(entries)
    return gone
