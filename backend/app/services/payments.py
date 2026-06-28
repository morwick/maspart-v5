"""
Service Pembayaran — Payment API RajaOngkir/Komerce (Virtual Account / QRIS).

Spec resmi (terverifikasi live, sandbox):
  Base   : {host}/user           host = api-sandbox / api .collaborator.komerce.id
  Auth   : header  x-api-key: <PAYMENT_API_KEY>
  Methods: GET  /api/v1/user/methods
  Create : POST /api/v1/user/payment/create
  Status : GET  /api/v1/user/payment/status/{payment_id}   (payment_id di-URL-encode)
  Cancel : POST /api/v1/user/payment/cancel
  Callback: gateway POST ke callback_url kita; verifikasi via callback_API_KEY
            yang kita kirim saat create (dicocokkan dgn PAYMENT_CALLBACK_SECRET).

Status dinormalisasi → 'pending' | 'paid' | 'failed'.
"""
from __future__ import annotations

from urllib.parse import quote

import requests

from ..core.config import get_settings

_TIMEOUT = 20
_PATH = "/user/api/v1/user"  # prefix path setelah host

_PAID = {"paid", "success", "settled", "settlement", "completed", "berhasil", "lunas"}
_FAILED = {"expired", "failed", "cancel", "cancelled", "canceled", "deny", "gagal", "kadaluarsa"}


def available() -> bool:
    return get_settings().payment_configured


def _headers() -> dict:
    return {"x-api-key": get_settings().payment_api_key, "Content-Type": "application/json"}


def _url(path: str) -> str:
    return f"{get_settings().payment_api_base}{_PATH}{path}"


def _norm_status(raw_status: str) -> str:
    v = (raw_status or "").strip().lower()
    if v in _PAID:
        return "paid"
    if v in _FAILED:
        return "failed"
    return "pending"


def _channel_to_payload(channel: str) -> dict:
    """Map kode channel internal → field payment_type/channel_code Komerce.
    'qris' → QRIS; 'va_bca' / 'va_bni' / ... → bank_transfer + bank_code.
    """
    c = (channel or "qris").strip().lower()
    if c in ("qris", "qr"):
        return {"payment_type": "qris"}
    if c.startswith("va_"):
        return {"payment_type": "bank_transfer", "channel_code": c[3:].upper()}
    # fallback: anggap kode bank langsung
    return {"payment_type": "bank_transfer", "channel_code": c.upper()}


# ─────────────────────────────────────────────────────────────
# API publik service (dipakai router)
# ─────────────────────────────────────────────────────────────
def list_methods() -> tuple[list[dict], str | None]:
    """Daftar metode bayar dari gateway → [{code, label, payment_type, bank_code}]."""
    if not available():
        return [], "Pembayaran otomatis belum diaktifkan."
    try:
        r = requests.get(_url("/methods"), headers=_headers(), timeout=_TIMEOUT)
        if r.status_code != 200:
            return [], f"Gagal ambil metode: {r.status_code}"
        out = [{"code": "qris", "label": "QRIS (scan QR)", "payment_type": "qris", "bank_code": ""}]
        for m in (r.json() or {}).get("data", []) or []:
            if m.get("payment_type") == "va" and m.get("bank_code"):
                out.append({
                    "code": f"va_{m['bank_code'].lower()}",
                    "label": f"Virtual Account {m.get('display_name') or m['bank_code']}",
                    "payment_type": "bank_transfer",
                    "bank_code": m["bank_code"],
                })
        return out, None
    except Exception as e:
        return [], str(e)


def create_payment(order_code: str, amount: int, channel: str, customer: dict | None = None) -> tuple[dict | None, str | None]:
    if not available():
        return None, "Pembayaran otomatis belum diaktifkan (PAYMENT_API_KEY kosong)."
    if amount <= 0:
        return None, "Nominal pembayaran tidak valid."
    s = get_settings()
    cust = customer or {}
    phone = "".join(ch for ch in str(cust.get("phone") or "") if ch.isdigit())
    if len(phone) < 8:
        phone = "08000000000"  # default valid (≥8 digit) — gateway mewajibkan
    body = {
        "order_id": order_code,
        "amount": int(amount),
        "customer": {
            "name": cust.get("name") or order_code,
            "email": cust.get("email") or "noreply@example.com",
            "phone": phone,
        },
        "items": [{"name": f"Pesanan {order_code}", "quantity": 1, "price": int(amount)}],
        "expiry_duration": 86400,
        **_channel_to_payload(channel),
    }
    # Webhook hanya kalau secret diisi (gateway mewajibkan callback_api_key non-kosong).
    if s.payment_callback_secret:
        body["callback_url"] = f"{s.public_base_url.rstrip('/')}/api/payments/webhook"
        body["callback_api_key"] = s.payment_callback_secret
        body["callback_API_KEY"] = s.payment_callback_secret
    try:
        r = requests.post(_url("/payment/create"), headers=_headers(), json=body, timeout=_TIMEOUT)
        if r.status_code not in (200, 201):
            return None, f"Gagal buat pembayaran: {r.status_code} {r.text[:200]}"
        d = (r.json() or {}).get("data") or {}
        return {
            "ref": d.get("payment_id"),
            "channel": channel,
            "va": d.get("va_number") or "",
            "qr": d.get("qr_string") or "",
            "url": d.get("payment_url") or "",
            "expiry": d.get("expired_at"),
            "status": _norm_status(d.get("status")),
            "raw": r.json(),
        }, None
    except Exception as e:
        return None, str(e)


def get_status(payment_ref: str) -> tuple[dict | None, str | None]:
    if not available():
        return None, "Pembayaran otomatis belum diaktifkan."
    if not payment_ref:
        return None, "payment_ref kosong."
    try:
        r = requests.get(_url(f"/payment/status/{quote(payment_ref, safe='')}"), headers=_headers(), timeout=_TIMEOUT)
        if r.status_code != 200:
            return None, f"Gagal cek status: {r.status_code} {r.text[:160]}"
        d = (r.json() or {}).get("data") or {}
        return {
            "status": _norm_status(d.get("status")),
            "amount": int(d.get("amount") or 0),
            "order_id": d.get("order_id"),
            "raw": r.json(),
        }, None
    except Exception as e:
        return None, str(e)


def _verify_callback(headers: dict, payload: dict) -> bool:
    """Cocokkan callback_API_KEY (yang kita kirim saat create) dgn secret kita.
    Secret kosong: bila gateway pembayaran AKTIF (PAYMENT_API_KEY terisi) → TOLAK
    (fail-closed) apa pun lingkungannya; hanya diizinkan saat pembayaran belum
    dikonfigurasi sama sekali (uji lokal tanpa gateway)."""
    settings = get_settings()
    secret = settings.payment_callback_secret
    if not secret:
        # Tanpa secret, keaslian callback tak bisa dijamin. Jika gateway sungguhan
        # dipakai, tolak — jangan andalkan APP_ENV yang mudah lupa di-set.
        return not settings.payment_configured
    h = {str(k).lower(): v for k, v in (headers or {}).items()}
    candidates = [
        payload.get("callback_API_KEY"), payload.get("callback_api_key"),
        (payload.get("data") or {}).get("callback_API_KEY") if isinstance(payload.get("data"), dict) else None,
        h.get("x-api-key"), h.get("x-callback-key"), h.get("callback-api-key"),
    ]
    return any(c == secret for c in candidates if c is not None)


def parse_webhook(headers: dict, payload: dict) -> tuple[dict | None, str | None]:
    """Verifikasi keaslian callback lalu kembalikan {order_id, ref, status, amount}."""
    if not _verify_callback(headers, payload):
        return None, "Callback key tidak valid."
    d = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    return {
        "order_id": d.get("order_id"),
        "ref": d.get("payment_id"),
        "status": _norm_status(d.get("status")),
        "amount": int(d.get("amount") or 0),
        "raw": payload,
    }, None
