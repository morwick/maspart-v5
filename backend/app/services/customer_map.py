"""
Tautan AKUN → PELANGGAN ACCURATE (kolom `users.accurate_customer_*`, migrasi 024).

Kenapa ada: penawaran Accurate otomatis dulu mencocokkan pelanggan dari
`orders.recipient_name` — teks BEBAS yang diketik pembeli di form pengiriman.
Akun yang sama menulis 'rizki' hari ini dan 'PT ARGCIO JAYA ABADI' besok, jadi
penawaran kadang jadi, kadang di-skip diam-diam, dan berisiko menempel ke
pelanggan yang SALAH bila namanya mirip. Sekarang admin menautkannya SEKALI dan
penawaran memakai `customer_id` langsung.

⛔ `accurate_customer_id` adalah SUMBER KEBENARAN. `name`/`no` hanya disimpan
untuk tampilan & audit — nama bisa berubah di Accurate, jadi JANGAN dipakai
mencocokkan apa pun.

Tahan pra-migrasi: bila kolomnya belum ada, pembacaan mengembalikan kosong dan
penulisan melapor gagal dengan pesan jelas — alur pesanan & pembayaran tidak
terganggu (penawaran otomatis cuma di-skip).
"""
from __future__ import annotations

import logging

import requests

from ..core.config import get_settings
from .supabase_client import _rest_url, _service_headers

logger = logging.getLogger("maspart.customer_map")

_KOLOM = "username,role,is_active,accurate_customer_id,accurate_customer_name,accurate_customer_no"
_TIMEOUT = 10


class KolomBelumAda(RuntimeError):
    """Migrasi 024 belum dijalankan di database ini."""


def _kolom_hilang(resp) -> bool:
    """PostgREST 42703 = kolom tak dikenal (migrasi belum jalan)."""
    if resp.status_code not in (400, 404):
        return False
    txt = (resp.text or "").lower()
    return "accurate_customer" in txt and ("column" in txt or "42703" in txt)


def tersedia() -> bool:
    """True bila kolom tautan sudah ada di database."""
    s = get_settings()
    if not s.supabase_configured:
        return False
    try:
        r = requests.get(_rest_url(s.supabase_table),
                         headers=_service_headers(),
                         params={"select": "accurate_customer_id", "limit": "1"},
                         timeout=_TIMEOUT)
        return r.status_code == 200
    except Exception:
        return False


def daftar() -> list[dict]:
    """Semua akun + tautannya (untuk panel admin). Pra-migrasi → KolomBelumAda."""
    s = get_settings()
    if not s.supabase_configured:
        return []
    r = requests.get(_rest_url(s.supabase_table),
                     headers=_service_headers(),
                     params={"select": _KOLOM, "order": "username.asc"},
                     timeout=_TIMEOUT)
    if _kolom_hilang(r):
        raise KolomBelumAda("kolom accurate_customer_* belum ada (jalankan migrations/024)")
    r.raise_for_status()
    out = []
    for u in (r.json() or []):
        cid = u.get("accurate_customer_id")
        out.append({
            "username": u.get("username") or "",
            "role": u.get("role") or "user",
            "is_active": bool(u.get("is_active", True)),
            "customer_id": int(cid) if cid is not None else None,
            "customer_name": u.get("accurate_customer_name") or "",
            "customer_no": u.get("accurate_customer_no") or "",
        })
    return out


def untuk(username: str) -> dict | None:
    """Tautan satu akun → {'id','name','no'}; None bila belum ditautkan / kolom
    belum ada. ⛔ TAK PERNAH raise: dipanggil dari jalur pembayaran."""
    uname = (username or "").strip().lower()
    if not uname:
        return None
    s = get_settings()
    if not s.supabase_configured:
        return None
    try:
        r = requests.get(_rest_url(s.supabase_table),
                         headers=_service_headers(),
                         params={"select": _KOLOM, "username": f"eq.{uname}", "limit": "1"},
                         timeout=_TIMEOUT)
        if r.status_code != 200:
            return None
        rows = r.json() or []
    except Exception:
        logger.exception("baca tautan pelanggan gagal (%s)", username)
        return None
    if not rows:
        return None
    cid = rows[0].get("accurate_customer_id")
    if cid is None:
        return None
    try:
        cid = int(cid)
    except (TypeError, ValueError):
        return None
    return {"id": cid, "name": rows[0].get("accurate_customer_name") or "",
            "no": rows[0].get("accurate_customer_no") or ""}


def tautkan(username: str, customer_id: int | None,
            customer_name: str = "", customer_no: str = "") -> tuple[bool, str]:
    """Set/hapus tautan. `customer_id=None` → lepas tautan. (ok, pesan)."""
    uname = (username or "").strip().lower()
    if not uname:
        return False, "username kosong"
    s = get_settings()
    if not s.supabase_configured:
        return False, "Supabase belum dikonfigurasi"
    data = {
        "accurate_customer_id": int(customer_id) if customer_id is not None else None,
        "accurate_customer_name": (customer_name or "").strip() or None,
        "accurate_customer_no": (customer_no or "").strip() or None,
    }
    try:
        r = requests.patch(_rest_url(s.supabase_table),
                           headers=_service_headers("return=minimal"),
                           params={"username": f"eq.{uname}"},
                           json=data, timeout=_TIMEOUT)
    except Exception as e:
        return False, str(e)
    if _kolom_hilang(r):
        return False, ("Kolom tautan belum ada di database — jalankan "
                       "migrations/024_users_accurate_customer.sql di Supabase.")
    if r.status_code not in (200, 204):
        return False, f"{r.status_code}: {(r.text or '')[:200]}"
    return True, "ok"
