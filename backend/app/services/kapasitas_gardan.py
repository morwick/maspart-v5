"""
Service: KAPASITAS OLI GARDAN per MODEL + TORSI MUR RODA (dokumen RESMI
en.sinotruk.com) — kapasitas_gardan.json.gz, dibangun tools/build_kapasitas_gardan.py.

Melengkapi `jadwal_servis_truk` yang hanya mencakup gardan MCY13: di sini ada
MCY11/MCY13/AC16/AC26/HW16 — jadi unit dengan gardan lain tak lagi dijawab
dengan angka milik MCY13.

⚠️ 'liter' SUDAH total. Sebagian model ditulis pabrik sebagai penjumlahan
(mis. AC16: 17+2×2=21 L = gardan + 2 hub); rumusnya disimpan di `rumus`.
⛔ TRANSMISI sengaja TIDAK ada di sini — lihat catatan builder (risiko
salah-pasang antar model gearbox).
"""
from __future__ import annotations

import gzip
import json
import re
import threading
from pathlib import Path

_DATA = Path(__file__).parent / "kapasitas_gardan.json.gz"
_CACHE: dict = {"mtime": None, "data": {}}
_lock = threading.Lock()


def _load() -> dict:
    try:
        mt = _DATA.stat().st_mtime if _DATA.exists() else None
    except Exception:
        mt = None
    if mt != _CACHE["mtime"]:
        with _lock:
            if mt != _CACHE["mtime"]:
                d: dict = {}
                if mt is not None:
                    try:
                        with gzip.open(_DATA, "rt", encoding="utf-8") as f:
                            d = json.load(f) or {}
                    except Exception:
                        d = {}
                _CACHE.update(mtime=mt, data=d)
    return _CACHE["data"]


def available() -> bool:
    return bool(_load().get("gardan"))


def _norm(s: str) -> str:
    return re.sub(r"[\s\-_/]", "", (s or "")).upper()


def cari_gardan(model: str = "") -> list[dict]:
    """Kapasitas per model gardan. `model` kosong = semua.

    Pencocokan longgar: 'MCY 13', 'mcy13', dan kode gardan dari cek_kendaraan
    (mis. 'MCP16ZG') sama-sama diterima — yang tak dikenal balik daftar kosong,
    ⛔ BUKAN ditebak ke model terdekat."""
    rows = _load().get("gardan") or []
    q = _norm(model)
    if not q:
        return rows
    tepat = [g for g in rows if _norm(g["model"]) == q]
    if tepat:
        return tepat
    return [g for g in rows if _norm(g["model"]) in q or q in _norm(g["model"])]


def torsi(kata: str = "") -> list[dict]:
    rows = _load().get("torsi") or []
    k = (kata or "").strip().lower()
    if not k:
        return rows
    return [t for t in rows if k in t["item"].lower() or k in (t.get("catatan") or "").lower()]


def meta() -> dict:
    d = _load()
    return {k: d.get(k) for k in ("sumber", "url_tabel", "url_manual",
                                  "diambil", "peringatan")}
