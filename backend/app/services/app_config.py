"""
Metadata aplikasi mobile yang bisa diatur admin TANPA rebuild/deploy APK:

  - `version`: versi APK terbaru → aplikasi membandingkannya dengan versi
    terpasang saat dibuka, lalu memunculkan notifikasi update. `force` + `min_name`
    untuk memaksa update (mis. setelah perbaikan keamanan).
  - `config` : feature-flag bebas yang dibaca aplikasi (chip saran Asisten,
    default Cari-by-Foto, limit Cari Part). Aplikasi SELALU punya nilai bawaan
    hardcoded, jadi kunci yang absen bukan error — hanya "pakai bawaan".

Disimpan sebagai JSON di <DATA_DIR>/app_config.json, pola sama dgn
`gudang_config`. Dibaca publik lewat GET /api/app/meta; diubah admin lewat
GET/PUT /api/admin/app-config.

⚠️ `latest_name` di bawah adalah SEED awal, bukan pembacaan otomatis dari APK —
setiap kali merilis APK baru, admin WAJIB memperbaruinya di panel (sama seperti
APP_VERSION di halaman /download). Kalau tidak, pengguna lama tak akan diberi
tahu ada versi baru.
"""
from __future__ import annotations

import json
import threading

from ..core.config import get_settings

_lock = threading.Lock()
_cache: dict | None = None

_DOWNLOAD_URL = "https://maspart.tech/download"


def _defaults() -> dict:
    return {
        "version": {
            "latest_code": 0,       # legacy: aplikasi ≤2.1.3 membandingkan versionCode
            "latest_name": "2.1.4",  # versi APK yang sedang dilayani /download
            "min_code": 0,
            "min_name": "",         # kosong = tak ada paksaan update
            "download_url": _DOWNLOAD_URL,
            "force": False,
        },
        # Kosong = aplikasi memakai bawaan hardcoded tiap layar.
        "config": {},
    }


def _path():
    return get_settings().data_path / "app_config.json"


def _bersihkan_version(raw: dict, dasar: dict) -> dict:
    """Ambil hanya kunci yang dikenal & paksa tipenya — panel admin mengirim
    JSON mentah, jangan sampai tipe ngawur bocor ke aplikasi."""
    out = dict(dasar)
    if not isinstance(raw, dict):
        return out
    for k in ("latest_code", "min_code"):
        if k in raw:
            try:
                out[k] = int(raw[k] or 0)
            except (TypeError, ValueError):
                pass
    for k in ("latest_name", "min_name"):
        if k in raw:
            out[k] = str(raw[k] or "").strip()
    if "download_url" in raw:
        # URL kosong → jangan simpan string kosong; aplikasi lama memang
        # mem-fallback sendiri, tapi panel admin jadi tampak "hilang isinya".
        out["download_url"] = str(raw["download_url"] or "").strip() or _DOWNLOAD_URL
    if "force" in raw:
        out["force"] = bool(raw["force"])
    return out


def load() -> dict:
    """{'version': {...}, 'config': {...}} — default ditimpa file bila ada."""
    global _cache
    with _lock:
        if _cache is not None:
            return _cache
        cfg = _defaults()
        try:
            p = _path()
            if p.exists():
                saved = json.loads(p.read_text(encoding="utf-8"))
                if isinstance(saved, dict):
                    cfg["version"] = _bersihkan_version(saved.get("version"), cfg["version"])
                    if isinstance(saved.get("config"), dict):
                        cfg["config"] = saved["config"]
        except Exception:
            # File rusak/tak terbaca → pakai default. ⛔ JANGAN melempar: endpoint
            # ini dipanggil aplikasi tiap kali dibuka.
            pass
        _cache = cfg
        return cfg


def save(version: dict | None, config: dict | None) -> dict:
    """Simpan lalu kembalikan config aktif yang baru. Argumen None = tak diubah."""
    global _cache
    cur = load()
    baru = {
        "version": _bersihkan_version(version or {}, cur["version"]),
        "config": config if isinstance(config, dict) else cur["config"],
    }
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(baru, ensure_ascii=False, indent=2), encoding="utf-8")
    with _lock:
        _cache = baru
    return baru


def reset_cache() -> None:
    """Untuk test — buang cache proses agar file dibaca ulang."""
    global _cache
    with _lock:
        _cache = None
