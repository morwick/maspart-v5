"""Sisa temuan audit jual-beli (L1, L2, L3, L5) — uang & stok tak boleh ganda:

L5. Dobel-klik "Bayar" / retry jaringan dgn keranjang IDENTIK → SATU order & SATU
    VA. Dulu tiap klik membuat order + reservasi + transaksi gateway baru: stok
    tertahan dua kali dan pembeli menerima dua tagihan.
L2. Uang masuk dengan nominal BEDA (webhook maupun polling) ditandai
    `flag_amount_mismatch` → naik ke radar admin. Dulu webhook hanya menjawab
    'ignored' → uang lenyap dari pantauan.
L1. Gateway bilang LUNAS tapi tak menyertakan nominal → verifikasi underpay tak
    bisa dijalankan; wajib tercatat di log, jangan lolos senyap.
L3. GET /payment/status memicu HTTP ke Midtrans + berpotensi mutasi status →
    dibatasi per-IP.
"""
import asyncio
import logging

import pytest
from fastapi import HTTPException

from app.core import ratelimit as RL
from app.routers import orders as R

USER = {"username": "budi", "role": "pembeli"}


class _Req:
    """Request palsu secukupnya untuk dependency rate-limit / webhook."""

    def __init__(self, ip: str = "9.9.9.9", body: dict | None = None):
        self.headers: dict[str, str] = {}
        self.client = type("C", (), {"host": ip})()
        self._body = body or {}

    async def json(self):
        return self._body


def _checkout_lancar(monkeypatch) -> dict:
    """Pasang seluruh jalur checkout ke keadaan SUKSES; kembalikan pencacah
    berapa kali order & transaksi gateway benar-benar dibuat."""
    hitung = {"order": 0, "payment": 0, "reserve": 0}
    monkeypatch.setattr(R.orders, "fulfillment_map", lambda u, items: {"P-1": "01.Jakarta"})
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
    monkeypatch.setattr(R.gudang, "shippable", lambda bd: bd)
    monkeypatch.setattr(R.gudang, "scope_breakdown",
                        lambda bd, u, role, names, own="": bd)
    monkeypatch.setattr(R.part_index, "gudang_names", lambda: ["01.Jakarta"])
    monkeypatch.setattr(R.part_index, "gudang_breakdown", lambda pn: {"01.Jakarta": 5})
    monkeypatch.setattr(R.reservations, "reserved_map", lambda force=False: {})
    monkeypatch.setattr(R.reservations, "reserve",
                        lambda code, entries: hitung.__setitem__("reserve", hitung["reserve"] + 1) or True)
    monkeypatch.setattr(R.orders, "set_fulfill_gudang", lambda code, lb: True)
    monkeypatch.setattr(R.orders, "attach_payment", lambda code, pay: True)
    monkeypatch.setattr(R.orders, "get_order", lambda code, username=None: None)
    monkeypatch.setattr(R.notify, "notify_new_order", lambda o, items=None: None)

    def _buat(*a, **kw):
        hitung["order"] += 1
        return {"order_code": f"PO-{hitung['order']}", "total": 500_000,
                "status": "menunggu_pembayaran"}, None

    def _bayar(code, amt, ch, customer=None):
        hitung["payment"] += 1
        return {"va_number": f"VA-{hitung['payment']}", "payment_url": "x"}, None

    monkeypatch.setattr(R.orders, "create_order", _buat)
    monkeypatch.setattr(R.payments, "create_payment", _bayar)
    return hitung


def _body(qty: int = 1) -> "R.CreateOrderRequest":
    return R.CreateOrderRequest(
        items=[R.OrderItemIn(part_number="P-1", qty=qty)], courier="jne",
        courier_service="REG", payment_method="gateway", payment_channel="qris",
        recipient_name="Budi", recipient_phone="0811", recipient_address="Jl. X",
        recipient_postal="40111")


# ── L5. Idempotensi checkout ────────────────────────────────────────────────
def test_dobel_klik_checkout_hanya_membuat_satu_order(monkeypatch):
    hitung = _checkout_lancar(monkeypatch)
    a = R.create_order(_body(), USER)
    b = R.create_order(_body(), USER)          # klik kedua, keranjang identik
    assert a["order_code"] == b["order_code"] == "PO-1"
    assert a["payment"] == b["payment"]        # satu VA, bukan dua tagihan
    assert hitung == {"order": 1, "payment": 1, "reserve": 1}


def test_keranjang_berbeda_tetap_membuat_order_baru(monkeypatch):
    """Idempotensi hanya untuk maksud pesan yang SAMA — jangan sampai pembeli
    yang benar-benar memesan lagi (qty beda) malah menerima order lama."""
    hitung = _checkout_lancar(monkeypatch)
    a = R.create_order(_body(qty=1), USER)
    b = R.create_order(_body(qty=2), USER)
    assert a["order_code"] == "PO-1" and b["order_code"] == "PO-2"
    assert hitung["order"] == 2


def test_pembeli_lain_tidak_pernah_menerima_order_pembeli_lain(monkeypatch):
    """Sidik jari menyertakan username — dua pembeli dgn keranjang & penerima
    persis sama tetap dapat ordernya masing-masing."""
    hitung = _checkout_lancar(monkeypatch)
    a = R.create_order(_body(), USER)
    b = R.create_order(_body(), {"username": "siti", "role": "pembeli"})
    assert a["order_code"] != b["order_code"]
    assert hitung["order"] == 2


def test_order_yang_sudah_dibatalkan_tidak_dipakai_ulang(monkeypatch):
    """Batal → checkout ulang keranjang yang sama dalam rentang TTL harus dapat
    order + VA BARU; memakai ulang order batal berarti pembeli menatap VA mati."""
    hitung = _checkout_lancar(monkeypatch)
    a = R.create_order(_body(), USER)
    monkeypatch.setattr(R.orders, "get_order",
                        lambda code, username=None: {"order_code": code, "status": "batal"})
    b = R.create_order(_body(), USER)
    assert a["order_code"] == "PO-1" and b["order_code"] == "PO-2"
    assert hitung["order"] == 2 and hitung["payment"] == 2


def test_order_masih_menunggu_pembayaran_tetap_dipakai_ulang(monkeypatch):
    hitung = _checkout_lancar(monkeypatch)
    monkeypatch.setattr(R.orders, "get_order", lambda code, username=None: {
        "order_code": code, "status": "menunggu_pembayaran"})
    a = R.create_order(_body(), USER)
    b = R.create_order(_body(), USER)
    assert a["order_code"] == b["order_code"] and hitung["order"] == 1


def test_checkout_gagal_tidak_ikut_di_cache(monkeypatch):
    """Kegagalan TIDAK boleh tersimpan — pembeli harus bisa langsung ulangi
    checkout setelah stok/gateway pulih, bukan dibalas error yang sama 90 detik."""
    hitung = _checkout_lancar(monkeypatch)
    monkeypatch.setattr(R.part_index, "gudang_breakdown", lambda pn: {})   # stok habis
    with pytest.raises(HTTPException) as e:
        R.create_order(_body(), USER)
    assert e.value.status_code == 400
    monkeypatch.setattr(R.part_index, "gudang_breakdown", lambda pn: {"01.Jakarta": 5})
    out = R.create_order(_body(), USER)         # percobaan ulang langsung jalan
    assert out["order_code"] == "PO-1" and hitung["order"] == 1


# ── L2. Nominal tak cocok → ditandai ────────────────────────────────────────
def test_polling_nominal_beda_ditandai_untuk_admin(monkeypatch):
    ditandai = {}
    monkeypatch.setattr(R.orders, "get_order", lambda code, username=None: {
        "order_code": code, "status": "menunggu_pembayaran", "total": 500_000,
        "payment_ref": code})
    monkeypatch.setattr(R.payments, "get_status",
                        lambda ref: ({"status": "paid", "amount": 100_000, "raw": {}}, None))
    monkeypatch.setattr(R.orders, "flag_amount_mismatch",
                        lambda code, paid, total: ditandai.update(code=code, paid=paid, total=total) or True)
    marked = []
    monkeypatch.setattr(R.orders, "mark_paid", lambda code, raw=None: marked.append(code) or True)

    out = R.payment_status("PO-1", USER)
    assert out["paid"] is False and "tidak sama" in out["error"]
    assert ditandai == {"code": "PO-1", "paid": 100_000, "total": 500_000}
    assert marked == []                         # kurang bayar tak melunasi order


def test_webhook_nominal_beda_ditandai_untuk_admin(monkeypatch):
    ditandai = {}
    monkeypatch.setattr(R.payments, "parse_webhook",
                        lambda h, p: ({"order_id": "PO-7", "ref": "PO-7", "status": "paid",
                                       "amount": 100_000, "raw": p}, None))
    monkeypatch.setattr(R.orders, "find_by_payment", lambda ref: {
        "order_code": "PO-7", "status": "menunggu_pembayaran", "total": 500_000,
        "payment_ref": "PO-7"})
    monkeypatch.setattr(R.payments, "get_status",
                        lambda ref: ({"status": "paid", "amount": 100_000, "raw": {}}, None))
    monkeypatch.setattr(R.orders, "flag_amount_mismatch",
                        lambda code, paid, total: ditandai.update(code=code, paid=paid, total=total) or True)
    marked = []
    monkeypatch.setattr(R.orders, "mark_paid", lambda code, raw=None: marked.append(code) or True)

    out = asyncio.run(R.payment_webhook(_Req(body={"order_id": "PO-7"})))
    assert "ignored" in out
    assert ditandai == {"code": "PO-7", "paid": 100_000, "total": 500_000}
    assert marked == []


# ── L1. Lunas tanpa nominal → tercatat, tak senyap ──────────────────────────
def test_lunas_tanpa_nominal_tercatat_di_log(monkeypatch, caplog):
    """Nominal tak disertakan → underpay tak terverifikasi. Order tetap dilunasi
    (Snap mengunci nominal saat pembuatan), tapi wajib meninggalkan jejak."""
    monkeypatch.setattr(R.orders, "get_order", lambda code, username=None: {
        "order_code": code, "status": "menunggu_pembayaran", "total": 500_000,
        "payment_ref": code})
    monkeypatch.setattr(R.payments, "get_status",
                        lambda ref: ({"status": "paid", "amount": 0, "raw": {}}, None))
    monkeypatch.setattr(R.orders, "mark_paid", lambda code, raw=None: True)
    monkeypatch.setattr(R, "_after_paid", lambda code: None)

    with caplog.at_level(logging.WARNING, logger="maspart.orders"):
        out = R.payment_status("PO-1", USER)
    assert out["paid"] is True
    assert any("TANPA nominal" in r.getMessage() and "PO-1" in r.getMessage()
               for r in caplog.records)


# ── L3. Rate limit endpoint status pembayaran ───────────────────────────────
def test_payment_status_dibatasi_per_akun(monkeypatch):
    """Batas ditekan lewat endpoint sungguhan. Order sengaja 'tak ditemukan' →
    tiap panggilan berhenti di 404, membuktikan gerbang limit berjalan LEBIH DULU
    (sebelum get_status ke Midtrans & mutasi status)."""
    monkeypatch.setattr(R.orders, "get_order", lambda code, username=None: None)
    RL._hits.clear()
    for _ in range(30):                          # polling 8 dtk = 7,5/menit → longgar
        with pytest.raises(HTTPException) as e:
            R.payment_status("PO-1", USER)
        assert e.value.status_code == 404
    with pytest.raises(HTTPException) as e:
        R.payment_status("PO-1", USER)
    assert e.value.status_code == 429
    RL._hits.clear()


def test_batas_payment_status_tidak_menular_antar_akun(monkeypatch):
    """⛔ Per AKUN, bukan per IP: pembeli seluler berbagi IP publik lewat CGNAT
    operator — batas per-IP akan menendang pembeli sah yang satu jaringan."""
    monkeypatch.setattr(R.orders, "get_order", lambda code, username=None: None)
    RL._hits.clear()
    for _ in range(31):
        try:
            R.payment_status("PO-1", USER)
        except HTTPException:
            pass
    with pytest.raises(HTTPException) as e:      # akun lain masih dilayani
        R.payment_status("PO-1", {"username": "siti", "role": "pembeli"})
    assert e.value.status_code == 404
    RL._hits.clear()
