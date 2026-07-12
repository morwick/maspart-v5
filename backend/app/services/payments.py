"""
Service Pembayaran — Midtrans Snap.

Alur:
  Create : POST {app_base}/snap/v1/transactions  (Basic auth Server Key)
           → {token, redirect_url}. User diarahkan ke redirect_url (halaman Snap
           berisi semua metode: VA, QRIS, e-wallet, kartu).
  Status : GET  {api_base}/v2/{order_id}/status   (Basic auth Server Key)
  Webhook: Midtrans POST notification ke URL yang diset di DASHBOARD Midtrans
           (Settings → Payment Notification URL = {public}/api/payments/webhook).
           Keaslian diverifikasi via signature_key = SHA512(order_id + status_code
           + gross_amount + ServerKey).

Bentuk fungsi & return DIJAGA SAMA dengan gateway sebelumnya agar router tak berubah:
  create_payment → {ref, channel, va, qr, url, expiry, status, raw}
  get_status     → {status, amount, order_id, raw}
  parse_webhook  → {order_id, ref, status, amount, raw}

Status Midtrans (transaction_status) dinormalisasi → 'pending' | 'paid' | 'failed'.
"""
from __future__ import annotations

import base64
import hashlib
import time

import requests

from ..core.config import get_settings

_TIMEOUT = 20
_EXPIRY_HOURS = 24   # batas bayar (selaras dgn TTL reservasi stok 24 jam)

# transaction_status Midtrans → status internal.
_PAID = {"settlement", "capture"}          # 'capture' butuh fraud_status 'accept' (dicek terpisah)
_FAILED = {"deny", "cancel", "expire", "expired", "failure", "refund", "partial_refund", "chargeback"}


def available() -> bool:
    return get_settings().payment_configured


def _auth_header() -> dict:
    # Basic auth: base64(ServerKey + ':') — password kosong.
    key = get_settings().midtrans_server_key
    token = base64.b64encode(f"{key}:".encode()).decode()
    return {
        "Authorization": f"Basic {token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


def _norm_status(transaction_status: str, fraud_status: str = "") -> str:
    v = (transaction_status or "").strip().lower()
    if v == "capture":
        # capture (kartu): lunas hanya bila fraud_status 'accept'; 'challenge' = pending.
        return "paid" if (fraud_status or "").strip().lower() == "accept" else "pending"
    if v in _PAID:
        return "paid"
    if v in _FAILED:
        return "failed"
    return "pending"


def _amount_int(gross_amount) -> int:
    """gross_amount Midtrans kadang string '10000.00' → int rupiah."""
    try:
        return int(float(gross_amount))
    except (TypeError, ValueError):
        return 0


# ─────────────────────────────────────────────────────────────
# API publik service (dipakai router) — bentuk return dijaga
# ─────────────────────────────────────────────────────────────
def list_methods() -> tuple[list[dict], str | None]:
    """Snap menampilkan SEMUA metode di halamannya sendiri → satu 'channel'.
    (Bentuk dijaga agar UI pemilih channel tetap berfungsi.)"""
    if not available():
        return [], "Pembayaran otomatis belum diaktifkan."
    return [{
        "code": "snap",
        "label": "VA / QRIS / e-wallet / kartu — pilih di halaman pembayaran",
        "payment_type": "snap",
        "bank_code": "",
    }], None


def create_payment(order_code: str, amount: int, channel: str, customer: dict | None = None) -> tuple[dict | None, str | None]:
    if not available():
        return None, "Pembayaran otomatis belum diaktifkan (MIDTRANS_SERVER_KEY kosong)."
    if amount <= 0:
        return None, "Nominal pembayaran tidak valid."
    s = get_settings()
    cust = customer or {}
    phone = "".join(ch for ch in str(cust.get("phone") or "") if ch.isdigit())
    finish = f"{s.public_base_url.rstrip('/')}/pesanan/{order_code}"
    # Batas bayar 24 jam. Midtrans tak mengembalikan waktu kedaluwarsa di respons Snap,
    # jadi kita hitung sendiri (sekarang+24j) → disimpan sbg payment_expiry agar order
    # auto-batal saat lewat batas (orders.is_expired/sweep_expired) & tampil "Batas bayar".
    expiry_epoch = time.time() + _EXPIRY_HOURS * 3600
    expiry_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(expiry_epoch))
    body = {
        "transaction_details": {"order_id": order_code, "gross_amount": int(amount)},
        "item_details": [{"id": order_code, "price": int(amount), "quantity": 1,
                          "name": f"Pesanan {order_code}"[:50]}],
        "customer_details": {
            "first_name": (cust.get("name") or order_code)[:50],
            "email": cust.get("email") or "noreply@maspart.tech",
            **({"phone": phone} if len(phone) >= 8 else {}),
        },
        "callbacks": {"finish": finish, "error": finish, "pending": finish},
        "expiry": {"unit": "hours", "duration": _EXPIRY_HOURS},
    }
    try:
        r = requests.post(f"{s.midtrans_app_base}/snap/v1/transactions",
                          headers=_auth_header(), json=body, timeout=_TIMEOUT)
        if r.status_code not in (200, 201):
            return None, f"Gagal buat pembayaran Midtrans: {r.status_code} {r.text[:200]}"
        d = r.json() or {}
        url = d.get("redirect_url") or ""
        if not url:
            return None, "Midtrans tidak mengembalikan redirect_url."
        return {
            "ref": order_code,          # Midtrans mengacu transaksi via order_id (= order_code)
            "channel": "snap",
            "va": "",
            "qr": "",
            "url": url,
            "expiry": expiry_iso,
            "status": "pending",
            "raw": d,
        }, None
    except Exception as e:
        return None, str(e)


def get_status(payment_ref: str) -> tuple[dict | None, str | None]:
    if not available():
        return None, "Pembayaran otomatis belum diaktifkan."
    if not payment_ref:
        return None, "payment_ref kosong."
    s = get_settings()
    try:
        r = requests.get(f"{s.midtrans_api_base}/v2/{payment_ref}/status",
                         headers=_auth_header(), timeout=_TIMEOUT)
        if r.status_code == 404:
            # Transaksi belum pernah dibayar/dibuat di Midtrans → anggap pending.
            return {"status": "pending", "amount": 0, "order_id": payment_ref, "raw": _safe_json(r)}, None
        if r.status_code != 200:
            return None, f"Gagal cek status Midtrans: {r.status_code} {r.text[:160]}"
        d = r.json() or {}
        return {
            "status": _norm_status(d.get("transaction_status"), d.get("fraud_status")),
            "amount": _amount_int(d.get("gross_amount")),
            "order_id": d.get("order_id"),
            "raw": d,
        }, None
    except Exception as e:
        return None, str(e)


def _safe_json(r) -> dict:
    try:
        return r.json() or {}
    except Exception:
        return {}


def _verify_signature(payload: dict) -> bool:
    """signature_key = SHA512(order_id + status_code + gross_amount + ServerKey).
    Fail-closed: gateway aktif tapi signature salah/absen → TOLAK."""
    server_key = get_settings().midtrans_server_key
    if not server_key:
        return False
    sig = str(payload.get("signature_key") or "")
    order_id = str(payload.get("order_id") or "")
    status_code = str(payload.get("status_code") or "")
    gross_amount = str(payload.get("gross_amount") or "")
    if not (sig and order_id and status_code and gross_amount):
        return False
    calc = hashlib.sha512(f"{order_id}{status_code}{gross_amount}{server_key}".encode()).hexdigest()
    # Perbandingan tahan-waktu.
    import hmac
    return hmac.compare_digest(calc, sig)


def parse_webhook(headers: dict, payload: dict) -> tuple[dict | None, str | None]:
    """Verifikasi signature Midtrans lalu kembalikan {order_id, ref, status, amount}."""
    if not _verify_signature(payload):
        return None, "Signature Midtrans tidak valid."
    order_id = payload.get("order_id")
    return {
        "order_id": order_id,
        "ref": order_id,             # ref = order_id (get_status memakai order_id)
        "status": _norm_status(payload.get("transaction_status"), payload.get("fraud_status")),
        "amount": _amount_int(payload.get("gross_amount")),
        "raw": payload,
    }, None
