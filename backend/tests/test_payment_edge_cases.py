"""Kasus tepi alur bayar gateway (Midtrans) yang bisa menelan uang / stok:

1. Bukti transfer manual DITOLAK untuk order gateway — set_proof memindahkan status
   ke 'menunggu_verifikasi' dan dulu membuat pembayaran lunas terabaikan selamanya.
   Sebagai jaring pengaman, order 'menunggu_verifikasi' tetap boleh dilunasi gateway.
2. Pembayaran yang masuk untuk order BATAL ditandai (perlu refund), tak lagi ditelan
   diam-diam — order tidak dihidupkan karena stoknya sudah dilepas.
3. Gagal buat transaksi gateway → order DIBATALKAN + reservasi dilepas; kalau tidak,
   order tanpa payment_expiry tak akan pernah kedaluwarsa (order zombie penahan stok).
"""
import asyncio

import pytest
from fastapi import HTTPException

from app.routers import orders as R

USER = {"username": "budi", "role": "pembeli"}
ADMIN = {"username": "mas", "role": "admin"}


# ── 1. Bukti manual pada order gateway ──────────────────────────────────────
def test_upload_bukti_ditolak_untuk_order_gateway(monkeypatch):
    monkeypatch.setattr(R.orders, "get_order",
                        lambda code, username=None: {"order_code": code, "payment_method": "gateway"})
    with pytest.raises(HTTPException) as e:
        asyncio.run(R.upload_proof("PO-1", file=None, user=USER))
    assert e.value.status_code == 400
    assert "otomatis" in str(e.value.detail).lower()


def test_order_menunggu_verifikasi_masih_bisa_dilunasi_gateway(monkeypatch):
    """Jaring pengaman: kalau order gateway terlanjur di status verifikasi manual,
    konfirmasi lunas dari gateway tetap dieksekusi (bukan diabaikan)."""
    paid = {}
    monkeypatch.setattr(R.orders, "get_order", lambda code, username=None: {
        "order_code": code, "status": "menunggu_verifikasi", "total": 100_000, "payment_ref": code})
    monkeypatch.setattr(R.payments, "get_status",
                        lambda ref: ({"status": "paid", "amount": 100_000, "order_id": ref, "raw": {}}, None))
    monkeypatch.setattr(R.orders, "mark_paid",
                        lambda code, raw=None: paid.setdefault("code", code) is None or True)
    monkeypatch.setattr(R, "_after_paid", lambda code: paid.setdefault("after", code))
    out = R.payment_status("PO-1", ADMIN)
    assert out["paid"] is True and out["status"] == "diproses"
    assert paid["code"] == "PO-1" and paid["after"] == "PO-1"


# ── 2. Bayar setelah order batal ────────────────────────────────────────────
def test_webhook_bayar_setelah_batal_ditandai_bukan_ditelan(monkeypatch):
    flagged = {}
    monkeypatch.setattr(R.payments, "parse_webhook",
                        lambda h, p: ({"order_id": "PO-9", "ref": "PO-9", "status": "paid",
                                       "amount": 250_000, "raw": p}, None))
    monkeypatch.setattr(R.orders, "find_by_payment", lambda ref: {
        "order_code": "PO-9", "status": "batal", "total": 250_000, "payment_ref": "PO-9"})
    monkeypatch.setattr(R.payments, "get_status",
                        lambda ref: ({"status": "paid", "amount": 250_000, "order_id": "PO-9", "raw": {}}, None))
    monkeypatch.setattr(R.orders, "flag_late_payment",
                        lambda code, amount=0: flagged.update(code=code, amount=amount) or True)
    marked = []
    monkeypatch.setattr(R.orders, "mark_paid", lambda code, raw=None: marked.append(code) or True)

    class _Req:
        headers = {}

        async def json(self):
            return {"order_id": "PO-9"}

    out = asyncio.run(R.payment_webhook(_Req()))
    assert "flagged" in out                       # admin diberi tahu
    assert flagged == {"code": "PO-9", "amount": 250_000}
    assert marked == []                           # order batal TIDAK dihidupkan


# ── 3. Gateway gagal saat checkout ──────────────────────────────────────────
def test_gagal_buat_pembayaran_membatalkan_order_dan_lepas_reservasi(monkeypatch):
    dibatalkan, dilepas = [], []
    monkeypatch.setattr(R.harga, "total_weight_grams", lambda items, d, **kw: 2000)
    monkeypatch.setattr(R.payments, "available", lambda: True)
    monkeypatch.setattr(R.shipping, "available", lambda: True)
    monkeypatch.setattr(R.shipping, "get_rates",
                        lambda u, w, v, dest_postal="", origin_postal="":
                        ([{"courier": "jne", "service": "REG", "price": 25000}], None))
    monkeypatch.setattr(R.gudang, "origin_postal_for_label", lambda lb: "14250")
    monkeypatch.setattr(R.sb, "get_user_gudang", lambda u: "jakarta")
    monkeypatch.setattr(R.gudang, "buyer_label", lambda k: "01.Jakarta")
    monkeypatch.setattr(R.gudang, "owning_branch_label", lambda lb: lb)
    monkeypatch.setattr(R.gudang, "gudang_label", lambda lb: "Jakarta")
    monkeypatch.setattr(R.part_index, "gudang_names", lambda: ["01.Jakarta"])
    monkeypatch.setattr(R.part_index, "gudang_breakdown", lambda pn: {"01.Jakarta": 5})
    monkeypatch.setattr(R.reservations, "reserved_map", lambda force=False: {})
    monkeypatch.setattr(R.reservations, "reserve", lambda code, entries: True)
    monkeypatch.setattr(R.reservations, "release", lambda code: dilepas.append(code) or True)
    monkeypatch.setattr(R.orders, "create_order", lambda *a, **kw: (
        {"order_code": "PO-Z", "total": 500_000, "status": "menunggu_pembayaran"}, None))
    monkeypatch.setattr(R.orders, "set_status",
                        lambda code, st: dibatalkan.append((code, st)) or True)
    monkeypatch.setattr(R.orders, "set_fulfill_gudang", lambda code, lb: True)
    monkeypatch.setattr(R.payments, "create_payment",
                        lambda code, amt, ch, customer=None: (None, "Midtrans 503"))

    body = R.CreateOrderRequest(
        items=[R.OrderItemIn(part_number="P-1", qty=1)], courier="jne",
        courier_service="REG", payment_method="gateway",
        recipient_name="Budi", recipient_phone="0811", recipient_address="Jl. X",
        recipient_postal="40111")
    with pytest.raises(HTTPException) as e:
        R.create_order(body, USER)
    assert e.value.status_code == 502
    assert "dibatalkan" in str(e.value.detail).lower()
    assert dibatalkan == [("PO-Z", "batal")]      # tak ada order zombie
    assert dilepas == ["PO-Z"]                    # stok tak tertahan
