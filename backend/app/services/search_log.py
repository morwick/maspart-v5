"""
Log query pencarian NIHIL (0 hasil) → bahan usulan sinonim untuk admin.

Menyimpan agregat per query ternormalisasi di data/search_misses.json (in-memory
+ flush ke file tiap miss). Volume rendah karena HANYA query gagal yang dicatat,
jadi tulis-per-miss aman. Dipakai halaman admin 'Pencarian Nihil' untuk melihat
istilah lapangan yang belum ada di sinonim.json.
"""
from __future__ import annotations

import json
import threading
import time

from ..core.config import get_settings

_lock = threading.Lock()
_state: dict = {"data": None}
_MAX_ENTRIES = 3000


def _path():
    return get_settings().data_path / "search_misses.json"


def _ensure() -> dict:
    if _state["data"] is None:
        try:
            _state["data"] = json.loads(_path().read_text("utf-8"))
        except Exception:
            _state["data"] = {}
    return _state["data"]


def _flush(d: dict) -> None:
    try:
        _path().write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def record_miss(query: str, mode: str = "", source: str = "search") -> None:
    """Catat satu pencarian nihil. Query terlalu pendek/panjang diabaikan."""
    q = (query or "").strip()
    if len(q) < 2 or len(q) > 80:
        return
    key = q.lower()
    with _lock:
        d = _ensure()
        e = d.get(key) or {"query": q, "count": 0, "modes": [], "sources": []}
        e["query"] = q
        e["count"] = int(e.get("count", 0)) + 1
        e["last"] = int(time.time())
        if mode and mode not in e["modes"]:
            e["modes"].append(mode)
        if source and source not in e["sources"]:
            e["sources"].append(source)
        d[key] = e
        if len(d) > _MAX_ENTRIES:  # jaga ukuran file — simpan yang tersering
            d = dict(sorted(d.items(), key=lambda kv: -kv[1].get("count", 0))[:_MAX_ENTRIES])
            _state["data"] = d
        _flush(d)


def top_misses(limit: int = 300) -> list[dict]:
    """Query nihil tersering (untuk review admin)."""
    with _lock:
        d = _ensure()
        rows = sorted(d.values(),
                      key=lambda e: (-int(e.get("count", 0)), -int(e.get("last", 0))))
        return [dict(r) for r in rows[:limit]]


def resolve_miss(query: str) -> bool:
    """Hapus satu entri (mis. setelah admin menambahkannya ke sinonim.json)."""
    key = (query or "").strip().lower()
    with _lock:
        d = _ensure()
        if key in d:
            del d[key]
            _flush(d)
            return True
    return False


def total_count() -> int:
    with _lock:
        return len(_ensure())
