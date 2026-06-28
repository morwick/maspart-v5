"""
Konfigurasi lokasi gudang yang bisa diatur admin (persisten):
  - `coords`: koordinat tiap gudang (label → [lat, lon]) → dipakai untuk
    menghitung gudang TERDEKAT (fallback stok) secara otomatis.
  - `buyer` : lokasi yang bisa DIPILIH pembeli (key → {label, origin_postal}).

Disimpan sebagai JSON di <DATA_DIR>/gudang_config.json. Default di-seed dari
nilai bawaan; admin dapat mengubah via /api/admin/gudang.
"""
from __future__ import annotations

import json
import threading

from ..core.config import get_settings

# ── Default (seed) ───────────────────────────────────────────────────
_DEFAULT_COORDS: dict[str, tuple] = {
    "01.Jakarta": (-6.21, 106.85),
    "06.B80 H1": (-6.21, 106.85),
    "07.B80 H2": (-6.21, 106.85),
    "28.Ruko Stadion": (-6.21, 106.85),
    "02.Pekanbaru": (0.51, 101.45),
    "09.Kerinci pku": (0.51, 101.45),
    "04.Palembang": (-2.99, 104.76),
    "08.TJP Jambi": (-1.61, 103.61),
    "23.Medan": (3.59, 98.67),
    "03.Balikpapan": (-1.27, 116.83),
    "10.Banjarbaru": (-3.45, 114.84),
    "25. PT BJM": (-3.32, 114.59),
    "11.Muara Teweh": (-0.95, 114.89),
    "18.Pontianak": (-0.02, 109.34),
    "05.Makasar": (-5.13, 119.42),
    "26. BELOPA": (-3.38, 120.36),
}

_DEFAULT_BUYER: dict[str, dict] = {
    "jakarta":     {"label": "01.Jakarta",     "origin_postal": "14250"},
    "pekanbaru":   {"label": "02.Pekanbaru",   "origin_postal": ""},
    "balikpapan":  {"label": "03.Balikpapan",  "origin_postal": ""},
    "palembang":   {"label": "04.Palembang",   "origin_postal": ""},
    "makassar":    {"label": "05.Makasar",     "origin_postal": ""},
    "jambi":       {"label": "08.TJP Jambi",   "origin_postal": ""},
    "banjarmasin": {"label": "10.Banjarbaru",  "origin_postal": ""},
    "muarateweh":  {"label": "11.Muara Teweh", "origin_postal": ""},
    "pontianak":   {"label": "18.Pontianak",   "origin_postal": ""},
    "medan":       {"label": "23.Medan",       "origin_postal": ""},
}

_lock = threading.Lock()
_cache: dict | None = None


def _path():
    return get_settings().data_path / "gudang_config.json"


def _defaults() -> dict:
    return {
        "coords": {k: [v[0], v[1]] for k, v in _DEFAULT_COORDS.items()},
        "buyer": {k: dict(v) for k, v in _DEFAULT_BUYER.items()},
        "pic": {},  # label gudang → nomor PIC (kontak), diatur admin
    }


def load() -> dict:
    """Config aktif (default ditimpa file JSON bila ada). Di-cache."""
    global _cache
    with _lock:
        if _cache is not None:
            return _cache
        cfg = _defaults()
        try:
            p = _path()
            if p.exists():
                saved = json.loads(p.read_text(encoding="utf-8"))
                if isinstance(saved.get("coords"), dict):
                    cfg["coords"].update(
                        {str(k): [float(v[0]), float(v[1])] for k, v in saved["coords"].items() if v}
                    )
                if isinstance(saved.get("buyer"), dict):
                    cfg["buyer"] = {
                        str(k): {
                            "label": str(b.get("label", "")),
                            "origin_postal": str(b.get("origin_postal", "")),
                        }
                        for k, b in saved["buyer"].items()
                    }
                if isinstance(saved.get("pic"), dict):
                    cfg["pic"] = {str(k): str(v) for k, v in saved["pic"].items() if v}
        except Exception:
            pass
        _cache = cfg
        return cfg


def save(coords: dict, buyer: dict, pic: dict | None = None) -> tuple[bool, str]:
    """Tulis config ke disk & invalidasi cache."""
    global _cache
    data = {
        "coords": {
            str(k): [float(v[0]), float(v[1])]
            for k, v in (coords or {}).items()
            if v is not None and v[0] is not None and v[1] is not None
        },
        "buyer": {
            str(k): {
                "label": str(b.get("label", "")),
                "origin_postal": str(b.get("origin_postal", "")),
            }
            for k, b in (buyer or {}).items()
            if str(k).strip()
        },
        "pic": {str(k): str(v).strip() for k, v in (pic or {}).items() if str(v).strip()},
    }
    try:
        p = _path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        with _lock:
            _cache = None
        return True, "ok"
    except Exception as e:
        return False, str(e)


def coords_map() -> dict[str, tuple]:
    return {k: (v[0], v[1]) for k, v in load()["coords"].items()}


def buyer_locations() -> dict[str, dict]:
    return load()["buyer"]


def pic_map() -> dict[str, str]:
    return load().get("pic", {})
