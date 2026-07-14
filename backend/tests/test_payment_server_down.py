"""Server/DB down di tengah alur bayar — uang sudah masuk di gateway, jangan sampai
pesanannya nyangkut atau stoknya tersandera.

1. Webhook: gateway bilang LUNAS tapi simpan status GAGAL (Supabase down) → balas 5xx
   supaya Midtrans mengirim ulang notifikasi. Balasan 200 = notifikasi hangus & order
   nanti auto-batal padahal uangnya sudah masuk.
2. Polling: kasus yang sama pada /payment/status → jangan laporkan 'diproses' (UI
   berhenti menanya) padahal status gagal disimpan.
3. Order gateway TANPA payment_expiry (proses mati antara create_payment &
   attach_payment) tetap bisa kedaluwarsa lewat created_at → stok kembali, tak ada
   order zombie. Order manual tetap tanpa batas waktu.
"""
import asyncio
import time

import pytest
from fastapi import HTTPException

from app.routers import orders as R
from app.services import orders as S

ADMIN = {"username": "mas", "role": "admin"}


class _Req:
    headers = {}

    async def json(self):
        return {"order_id": "PO-1"}


def _gateway_paid(monkeypatch, status_awal="menunggu_pembayaran"):
    """Gateway mengonfirmasi LUNAS untuk order PO-1 senilai 100.000."""
    monkeypatch.setattr(R.payments, "parse_webhook",
                        lambda h, p: ({"order_id": "PO-1", "ref": "PO-1", "status": "paid",
                                       "amount": 100_000, "raw": p}, None))
    monkeypatch.setattr(R.payments, "get_status",
                        lambda ref: ({"status": "paid", "amount": 100_000, "order_id": "PO-1", "raw": {}}, None))
    o = {"order_code": "PO-1", "status": status_awal, "total": 100_000, "payment_ref": "PO-1"}
    monkeypatch.setattr(R.orders, "find_by_payment", lambda ref: o)
    monkeypatch.setattr(R.orders, "get_order", lambda code, username=None: o)


# ── 1. Webhook: gagal simpan → minta gateway kirim ulang ─────────────────────
def test_webhook_gagal_simpan_balas_5xx_agar_diretry(monkeypatch):
    _gateway_paid(monkeypatch)
    monkeypatch.setattr(R.orders, "mark_paid", lambda code, raw=None: False)  # DB down
    after = []
    monkeypatch.setattr(R, "_after_paid", lambda code: after.append(code))

    with pytest.raises(HTTPException) as e:
        asyncio.run(R.payment_webhook(_Req()))
    assert e.value.status_code >= 500        # bukan 200 → Midtrans me-retry
    assert after == []                       # penawaran Accurate tak dibuat separuh jalan


def test_webhook_sukses_tetap_200(monkeypatch):
    _gateway_paid(monkeypatch)
    monkeypatch.setattr(R.orders, "mark_paid", lambda code, raw=None: True)
    after = []
    monkeypatch.setattr(R, "_after_paid", lambda code: after.append(code))

    assert asyncio.run(R.payment_webhook(_Req())) == {"ok": True}
    assert after == ["PO-1"]


# ── 2. Polling: gagal simpan → jangan bilang 'diproses' ──────────────────────
def test_polling_gagal_simpan_tidak_melaporkan_lunas(monkeypatch):
    _gateway_paid(monkeypatch)
    monkeypatch.setattr(R.orders, "mark_paid", lambda code, raw=None: False)  # DB down
    monkeypatch.setattr(R, "_after_paid", lambda code: pytest.fail("tak boleh dipanggil"))

    out = R.payment_status("PO-1", ADMIN)
    assert out["paid"] is False
    assert out["status"] == "menunggu_pembayaran"   # bukan 'diproses'
    assert out["error"]                             # pembeli diberi tahu & UI terus menanya


# ── 3. Order gateway tanpa payment_expiry tetap bisa kedaluwarsa ─────────────
def _iso(epoch: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch))


def test_order_gateway_tanpa_expiry_kedaluwarsa_lewat_created_at():
    tua = {"status": "menunggu_pembayaran", "payment_method": "gateway",
           "created_at": _iso(time.time() - 25 * 3600)}   # 25 jam lalu, batas 24 jam
    assert S.is_expired(tua) is True


def test_order_gateway_tanpa_expiry_masih_baru_belum_kedaluwarsa():
    baru = {"status": "menunggu_pembayaran", "payment_method": "gateway",
            "created_at": _iso(time.time() - 600)}        # 10 menit lalu (masih checkout)
    assert S.is_expired(baru) is False


def test_order_manual_tanpa_expiry_tidak_pernah_kedaluwarsa():
    manual = {"status": "menunggu_pembayaran", "payment_method": "manual",
              "created_at": _iso(time.time() - 90 * 24 * 3600)}
    assert S.is_expired(manual) is False


def test_payment_expiry_tetap_menang_bila_ada():
    o = {"status": "menunggu_pembayaran", "payment_method": "gateway",
         "created_at": _iso(time.time() - 90 * 24 * 3600),   # created_at sudah lama…
         "payment_expiry": _iso(time.time() + 3600)}         # …tapi gateway beri tenggat
    assert S.is_expired(o) is False
