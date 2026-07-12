"""
Konfigurasi lokasi gudang yang bisa diatur admin (persisten):
  - `coords`: koordinat tiap gudang (label → [lat, lon]) → dipakai untuk
    menghitung gudang TERDEKAT (fallback stok) secara otomatis.
  - `buyer` : lokasi yang bisa DIPILIH pembeli (key → {label, origin_postal}).
  - `postal`: kode pos ASAL ongkir SETIAP gudang (label → kode pos), termasuk
    gudang yang TIDAK bisa dipilih pembeli. Wajib ada karena gudang pemenuh
    (fallback terdekat) sering bukan gudang pilihan pembeli — tanpa ini ongkir
    akan dihitung dari kota yang salah.
  - `no_ship`: gudang yang TIDAK boleh mengirim pesanan online (daftar label).
    Kandidat gudang pemenuh berasal dari INDEKS STOK (semua gudang ber-stok di
    Accurate), bukan dari config ini — jadi tanpa daftar ini gudang internal
    (mis. B80) ikut menawarkan barangnya ke pembeli. Tidak terdaftar = BOLEH
    mengirim (default aman: perilaku lama tak berubah).

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
        "pic": {},      # label gudang → nomor PIC (kontak), diatur admin
        "postal": {},   # label gudang → kode pos asal ongkir (SEMUA gudang)
        "no_ship": [],  # label gudang yang TIDAK boleh mengirim pesanan online
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
                if isinstance(saved.get("postal"), dict):
                    cfg["postal"] = {str(k): str(v) for k, v in saved["postal"].items() if v}
                if isinstance(saved.get("no_ship"), list):
                    cfg["no_ship"] = [str(v) for v in saved["no_ship"] if str(v).strip()]
        except Exception:
            pass
        # Config lama (sebelum ada map `postal`) hanya menyimpan kode pos di dalam
        # entri pembeli → angkat ke map postal agar tak hilang saat upgrade.
        for b in cfg["buyer"].values():
            lb, p = b.get("label", ""), b.get("origin_postal", "")
            if lb and p and lb not in cfg["postal"]:
                cfg["postal"][lb] = p
        _cache = cfg
        return cfg


def save(coords: dict, buyer: dict, pic: dict | None = None,
         postal: dict | None = None, no_ship: list | None = None) -> tuple[bool, str]:
    """Tulis config ke disk & invalidasi cache.

    `postal` (label → kode pos) berlaku untuk SEMUA gudang. Kode pos gudang yang
    juga lokasi pembeli ikut ditulis ke entri `buyer` agar pembaca lama tetap jalan.
    `no_ship` = daftar gudang yang tak boleh mengirim.

    postal/no_ship = None → nilai lama DIPERTAHANKAN (penting: penulis parsial
    seperti auto-isi kode pos tak boleh menghapus daftar no_ship, dan sebaliknya).
    """
    global _cache
    lama = load()
    if postal is None:
        postal = dict(lama.get("postal", {}))
    if no_ship is None:
        no_ship = list(lama.get("no_ship", []))
    postal_clean = {
        str(k): "".join(ch for ch in str(v) if ch.isdigit())[:10]
        for k, v in (postal or {}).items()
        if str(k).strip() and str(v).strip()
    }
    data = {
        "coords": {
            str(k): [float(v[0]), float(v[1])]
            for k, v in (coords or {}).items()
            if v is not None and v[0] is not None and v[1] is not None
        },
        "buyer": {
            str(k): {
                "label": str(b.get("label", "")),
                "origin_postal": postal_clean.get(str(b.get("label", "")),
                                                  str(b.get("origin_postal", ""))),
            }
            for k, b in (buyer or {}).items()
            if str(k).strip()
        },
        "pic": {str(k): str(v).strip() for k, v in (pic or {}).items() if str(v).strip()},
        "postal": postal_clean,
        "no_ship": sorted({str(v) for v in (no_ship or []) if str(v).strip()}),
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


def postal_map() -> dict[str, str]:
    """label gudang → kode pos ASAL ongkir (semua gudang, bukan hanya pilihan pembeli)."""
    return load().get("postal", {})


def no_ship_labels() -> set[str]:
    """Gudang yang TIDAK boleh mengirim pesanan online (dimatikan admin)."""
    return set(load().get("no_ship", []))


def set_postal(label: str, code: str) -> bool:
    """Isi kode pos SATU gudang (dipakai auto-isi dari koordinat). Tulis ke disk."""
    code = "".join(ch for ch in str(code or "") if ch.isdigit())[:10]
    if not label or not code:
        return False
    cfg = load()
    if cfg.get("postal", {}).get(label) == code:
        return False
    postal = dict(cfg.get("postal", {}))
    postal[label] = code
    ok, _msg = save(cfg["coords"], cfg["buyer"], cfg.get("pic"), postal)
    return ok
