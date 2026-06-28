"""Service chat pesanan (tabel order_chats). Resilient bila tabel belum ada."""
from __future__ import annotations

import time

import requests

from .supabase_client import _rest_url, _service_headers

_TIMEOUT = 15


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def list_messages(order_code: str) -> list[dict]:
    try:
        r = requests.get(
            _rest_url("order_chats"),
            headers={**_service_headers(), "Accept": "application/json"},
            params={
                "select": "sender_username,sender_role,body,created_at",
                "order_code": f"eq.{order_code}",
                "order": "created_at.asc",
                "limit": "500",
            },
            timeout=_TIMEOUT,
        )
        r.raise_for_status()
        return r.json() or []
    except Exception:
        return []


def add_message(order_code: str, sender_username: str, sender_role: str, body: str) -> bool:
    try:
        r = requests.post(
            _rest_url("order_chats"),
            headers=_service_headers("return=minimal"),
            json={
                "order_code": order_code,
                "sender_username": sender_username,
                "sender_role": sender_role,
                "body": body,
                "created_at": _now(),
            },
            timeout=_TIMEOUT,
        )
        return r.status_code in (200, 201, 204)
    except Exception:
        return False


# ── Chat pra-pesanan (pembeli ↔ gudang), tabel gudang_chats ─────────
def list_gudang_messages(gudang_key: str, buyer_username: str) -> list[dict]:
    try:
        r = requests.get(
            _rest_url("gudang_chats"),
            headers={**_service_headers(), "Accept": "application/json"},
            params={
                "select": "sender_username,sender_role,body,created_at",
                "gudang_key": f"eq.{gudang_key}",
                "buyer_username": f"eq.{buyer_username}",
                "order": "created_at.asc",
                "limit": "500",
            },
            timeout=_TIMEOUT,
        )
        r.raise_for_status()
        return r.json() or []
    except Exception:
        return []


def add_gudang_message(gudang_key: str, buyer_username: str, sender_role: str, sender_username: str, body: str) -> bool:
    try:
        r = requests.post(
            _rest_url("gudang_chats"),
            headers=_service_headers("return=minimal"),
            json={
                "gudang_key": gudang_key,
                "buyer_username": buyer_username,
                "sender_role": sender_role,
                "sender_username": sender_username,
                "body": body,
                "created_at": _now(),
            },
            timeout=_TIMEOUT,
        )
        return r.status_code in (200, 201, 204)
    except Exception:
        return False


def buyer_threads(buyer_username: str) -> list[dict]:
    """Daftar gudang yang pernah dichat pembeli ini + pesan terakhir (terbaru dulu)."""
    try:
        r = requests.get(
            _rest_url("gudang_chats"),
            headers={**_service_headers(), "Accept": "application/json"},
            params={
                "select": "gudang_key,body,created_at",
                "buyer_username": f"eq.{buyer_username}",
                "order": "created_at.desc",
                "limit": "500",
            },
            timeout=_TIMEOUT,
        )
        r.raise_for_status()
        rows = r.json() or []
        seen, out = set(), []
        for m in rows:
            g = m.get("gudang_key")
            if g and g not in seen:
                seen.add(g)
                out.append({"gudang_key": g, "last": m.get("body", ""), "created_at": m.get("created_at")})
        return out
    except Exception:
        return []


def branch_threads(gudang_key: str) -> list[dict]:
    """Daftar pembeli yang chat ke gudang ini + pesan terakhir (terbaru dulu)."""
    try:
        r = requests.get(
            _rest_url("gudang_chats"),
            headers={**_service_headers(), "Accept": "application/json"},
            params={
                "select": "buyer_username,body,created_at",
                "gudang_key": f"eq.{gudang_key}",
                "order": "created_at.desc",
                "limit": "500",
            },
            timeout=_TIMEOUT,
        )
        r.raise_for_status()
        rows = r.json() or []
        seen, out = set(), []
        for m in rows:
            b = m.get("buyer_username")
            if b and b not in seen:
                seen.add(b)
                out.append({"buyer_username": b, "last": m.get("body", ""), "created_at": m.get("created_at")})
        return out
    except Exception:
        return []
