"""
Reservasi stok (anti-oversell). Stok tersedia = stok Excel − reservasi aktif
yang BELUM kedaluwarsa.

Tabel `stock_reservations`. Jalur atomik (anti-oversell sejati) lewat RPC
`reserve_order` (migrasi 014). Bila RPC/kolom belum ada, fungsi fallback ke
jalur lama (best-effort) sehingga alur tetap jalan — namun jalankan migrasi 014
untuk jaminan penuh.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone

import requests

from .supabase_client import _rest_url, _service_headers

_TIMEOUT = 15
_TTL = 5.0
_DEFAULT_RESERVE_TTL = 86400  # detik (selaras dengan masa berlaku pembayaran gateway)
_cache: dict = {"ts": 0.0, "map": {}}


def _epoch(s: str | None) -> float | None:
    if not s:
        return None
    try:
        t = s.strip()
        if t.endswith("Z"):
            t = t[:-1] + "+00:00"
        dt = datetime.fromisoformat(t)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except Exception:
        return None


def _fetch_active() -> list[dict]:
    """Ambil reservasi aktif. Coba sertakan expires_at; fallback bila kolom
    belum ada (migrasi 014 belum dijalankan) agar tidak malah mengosongkan map."""
    base = {"active": "eq.true", "limit": "10000"}
    for sel in ("part_number,gudang_label,qty,expires_at", "part_number,gudang_label,qty"):
        try:
            r = requests.get(
                _rest_url("stock_reservations"),
                headers={**_service_headers(), "Accept": "application/json"},
                params={**base, "select": sel},
                timeout=_TIMEOUT,
            )
            if r.status_code == 200:
                return r.json() or []
        except Exception:
            continue
    return []


def reserved_map(force: bool = False) -> dict[tuple[str, str], int]:
    """{(PART_UPPER, gudang_label): qty} dari reservasi aktif & belum kedaluwarsa."""
    now = time.time()
    if not force and (now - _cache["ts"]) < _TTL:
        return _cache["map"]
    m: dict[tuple[str, str], int] = {}
    for row in _fetch_active():
        exp = _epoch(row.get("expires_at"))
        if exp is not None and exp <= now:
            continue  # reservasi kedaluwarsa → tidak dihitung (stok kembali tersedia)
        k = (str(row.get("part_number", "")).upper(), str(row.get("gudang_label", "")))
        m[k] = m.get(k, 0) + int(row.get("qty") or 0)
    _cache.update(ts=now, map=m)
    return m


def reserved_for(pn: str, gudang_label: str) -> int:
    return reserved_map().get(((pn or "").upper(), gudang_label or ""), 0)


def reserve(order_code: str, entries: list[tuple[str, str, int, int]], ttl_seconds: int = _DEFAULT_RESERVE_TTL):
    """Reservasi ATOMIK all-or-nothing lewat RPC `reserve_order`.

    entries: [(part_number, gudang_label, qty, stock_excel)].
    Return: True (berhasil) | False (oversell, ditolak) | None (RPC belum ada →
    pemanggil sebaiknya fallback ke add() + cek pasca-reservasi).
    """
    rows = [
        {"part": pn.upper(), "gudang": g, "qty": int(q), "stock": int(s)}
        for pn, g, q, s in entries
        if q and int(q) > 0
    ]
    if not rows:
        return True
    try:
        r = requests.post(
            _rest_url("rpc/reserve_order"),
            headers=_service_headers(),
            json={"p_order_code": order_code, "p_items": rows, "p_ttl_seconds": int(ttl_seconds)},
            timeout=_TIMEOUT,
        )
        if r.status_code in (200, 201):
            _cache["ts"] = 0.0
            val = r.json()
            return bool(val)
        # Fungsi belum ada (migrasi 014 belum dijalankan) → minta caller fallback.
        txt = (r.text or "").lower()
        if r.status_code == 404 or "pgrst202" in txt or "could not find" in txt or "function" in txt:
            return None
        return False
    except Exception:
        return None


def add(order_code: str, items: list[tuple[str, str, int]], ttl_seconds: int = _DEFAULT_RESERVE_TTL) -> bool:
    """items: [(part_number, gudang_label, qty)]. Jalur fallback non-atomik."""
    expiry = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + ttl_seconds))
    rows = [
        {"order_code": order_code, "part_number": pn.upper(), "gudang_label": g,
         "qty": int(q), "active": True, "expires_at": expiry}
        for pn, g, q in items
        if q and int(q) > 0
    ]
    if not rows:
        return True
    try:
        r = requests.post(_rest_url("stock_reservations"), headers=_service_headers("return=minimal"), json=rows, timeout=_TIMEOUT)
        if r.status_code not in (200, 201, 204):
            # Skema lama tanpa expires_at → ulangi tanpa kolom itu.
            rows2 = [{k: v for k, v in row.items() if k != "expires_at"} for row in rows]
            r = requests.post(_rest_url("stock_reservations"), headers=_service_headers("return=minimal"), json=rows2, timeout=_TIMEOUT)
        _cache["ts"] = 0.0
        return r.status_code in (200, 201, 204)
    except Exception:
        return False


def commit(order_code: str) -> bool:
    """Jadikan reservasi order ini permanen (expires_at = null) — dipakai saat
    order LUNAS, agar stok tetap tertahan dan tidak ikut kedaluwarsa."""
    try:
        r = requests.patch(
            _rest_url("stock_reservations"),
            headers=_service_headers("return=minimal"),
            params={"order_code": f"eq.{order_code}", "active": "eq.true"},
            json={"expires_at": None},
            timeout=_TIMEOUT,
        )
        _cache["ts"] = 0.0
        return r.status_code in (200, 204)
    except Exception:
        return False


def release(order_code: str) -> bool:
    """Lepas reservasi sebuah order (mis. saat dibatalkan)."""
    try:
        r = requests.patch(
            _rest_url("stock_reservations"),
            headers=_service_headers("return=minimal"),
            params={"order_code": f"eq.{order_code}", "active": "eq.true"},
            json={"active": False},
            timeout=_TIMEOUT,
        )
        _cache["ts"] = 0.0
        return r.status_code in (200, 204)
    except Exception:
        return False


def clear_all() -> bool:
    """Reset semua reservasi aktif (dipakai saat stok di-upload ulang)."""
    try:
        r = requests.patch(
            _rest_url("stock_reservations"),
            headers=_service_headers("return=minimal"),
            params={"active": "eq.true"},
            json={"active": False},
            timeout=_TIMEOUT,
        )
        _cache["ts"] = 0.0
        return r.status_code in (200, 204)
    except Exception:
        return False
