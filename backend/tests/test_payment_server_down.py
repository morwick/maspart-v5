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
4. REKONSILIASI: down lebih lama dari jendela retry webhook Midtrans (± 5–6 jam) →
   notifikasi lunas hangus PERMANEN. Server harus mengejar sendiri ke gateway:
   jangan pernah membatalkan order tanpa bertanya dulu, dan jangan bergantung pada
   pembeli membuka halamannya.
"""
import asyncio
import time

import pytest
from fastapi import HTTPException

from app.routers import orders as R
from app.services import orders as S
from app.services import payments as P

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


# ── 4. Rekonsiliasi: server bertanya SENDIRI ke gateway ──────────────────────
def _order(expired: bool = True, total: int = 100_000) -> dict:
    """Order gateway yang masih 'menunggu_pembayaran' (kedaluwarsa atau belum)."""
    return {
        "order_code": "PO-1", "status": "menunggu_pembayaran", "payment_method": "gateway",
        "total": total, "payment_ref": "PO-1",
        "created_at": _iso(time.time() - (25 * 3600 if expired else 600)),
        "payment_expiry": _iso(time.time() + (-3600 if expired else 3600)),
    }


@pytest.fixture
def spy(monkeypatch):
    """Rekam aksi yang diambil rekonsiliasi (tanpa menyentuh Supabase)."""
    calls: dict[str, list] = {"paid": [], "expired": [], "after": [], "flag": []}
    monkeypatch.setattr(S, "mark_paid", lambda code, raw=None: bool(calls["paid"].append(code)) or True)
    monkeypatch.setattr(S, "expire_order", lambda code: bool(calls["expired"].append(code)) or True)
    monkeypatch.setattr(S, "after_paid", lambda code: calls["after"].append(code))
    # _flag_payment menulis lewat _patch → tangkap di sini (kolom payment_note).
    monkeypatch.setattr(S, "_patch", lambda code, data, **kw: bool(calls["flag"].append(data)) or True)
    return calls


def _gateway(monkeypatch, status: str = "paid", amount: int = 100_000):
    monkeypatch.setattr(P, "available", lambda: True)
    monkeypatch.setattr(P, "get_status", lambda ref: (
        {"status": status, "amount": amount, "order_id": ref, "raw": {"ref": ref}}, None))


def _gateway_bisu(monkeypatch):
    """Gateway tak bisa ditanya (jaringan/Midtrans ikut down)."""
    monkeypatch.setattr(P, "available", lambda: True)
    monkeypatch.setattr(P, "get_status", lambda ref: (None, "connection refused"))


def test_kedaluwarsa_tapi_ternyata_sudah_lunas_dilunasi_bukan_dibatalkan(monkeypatch, spy):
    """INTI: webhook hangus saat server down, pembeli bayar tepat waktu tapi tak
    membuka lagi halamannya. Jangan sampai pesanannya dibatalkan diam-diam."""
    _gateway(monkeypatch, "paid")

    assert S.reconcile_order(_order(expired=True)) == "diproses"
    assert spy["paid"] == ["PO-1"]
    assert spy["expired"] == []          # TIDAK dibatalkan
    assert spy["after"] == ["PO-1"]      # Penawaran Accurate tetap terpicu


def test_kedaluwarsa_dan_gateway_pastikan_belum_bayar_baru_dibatalkan(monkeypatch, spy):
    _gateway(monkeypatch, "pending")

    assert S.reconcile_order(_order(expired=True)) == "batal"
    assert spy["expired"] == ["PO-1"]
    assert spy["paid"] == []


def test_gateway_bisu_jangan_batalkan_apa_pun(monkeypatch, spy):
    """Tak tahu = tak bertindak. Membatalkan tanpa jawaban gateway bisa membuang
    pesanan yang uangnya sudah masuk."""
    _gateway_bisu(monkeypatch)

    assert S.reconcile_order(_order(expired=True)) is None
    assert spy["expired"] == []
    assert spy["paid"] == []


def test_lunas_sebelum_tenggat_langsung_disinkronkan_tanpa_menunggu_24_jam(monkeypatch, spy):
    """Webhook hangus tapi order masih jauh dari tenggat → jangan biarkan pembeli
    menunggu; penyapu melunasinya begitu gateway bilang lunas."""
    _gateway(monkeypatch, "paid")

    assert S.reconcile_order(_order(expired=False)) == "diproses"
    assert spy["paid"] == ["PO-1"]


def test_belum_lunas_dan_belum_kedaluwarsa_dibiarkan(monkeypatch, spy):
    _gateway(monkeypatch, "pending")

    assert S.reconcile_order(_order(expired=False)) is None
    assert spy["expired"] == [] and spy["paid"] == []


def test_nominal_beda_tidak_dilunasi_otomatis_tapi_ditandai(monkeypatch, spy):
    _gateway(monkeypatch, "paid", amount=50_000)     # tagihan 100.000

    assert S.reconcile_order(_order(expired=True)) is None
    assert spy["paid"] == [] and spy["expired"] == []
    assert "payment_note" in spy["flag"][0]           # tercatat untuk admin


def test_lunas_tapi_db_down_jangan_batalkan_order(monkeypatch, spy):
    """mark_paid gagal (Supabase down) pada order kedaluwarsa: uang sudah masuk →
    biarkan 'menunggu_pembayaran', sapuan berikutnya mencoba lagi."""
    _gateway(monkeypatch, "paid")
    monkeypatch.setattr(S, "mark_paid", lambda code, raw=None: False)

    assert S.reconcile_order(_order(expired=True)) is None
    assert spy["expired"] == []          # yang penting: TIDAK dibatalkan
    assert spy["after"] == []            # penawaran tak dibuat separuh jalan


def test_order_manual_tak_pernah_ditanyakan_ke_gateway(monkeypatch, spy):
    monkeypatch.setattr(P, "get_status", lambda ref: pytest.fail("gateway tak boleh ditanya"))
    o = _order(expired=True) | {"payment_method": "manual"}

    assert S.reconcile_order(o) is None
    assert spy["expired"] == []


def test_sweep_expired_melunasi_bukan_membatalkan_bila_sudah_dibayar(monkeypatch, spy):
    """Halaman 'Pesanan Saya' dulu membatalkan order kedaluwarsa tanpa bertanya."""
    _gateway(monkeypatch, "paid")
    rows = [_order(expired=True)]

    assert S.sweep_expired(rows)[0]["status"] == "diproses"
    assert spy["expired"] == []


def test_reconcile_pending_menyapu_semua_order_menunggu(monkeypatch, spy):
    """Penyapu latar tak bergantung pada pembeli membuka halaman."""
    _gateway(monkeypatch, "paid")
    o1 = _order(expired=False)
    o2 = _order(expired=True) | {"order_code": "PO-2", "payment_ref": "PO-2"}
    monkeypatch.setattr(S, "_pending_gateway", lambda limit=200: [o1, o2])

    st = S.reconcile_pending()
    assert st == {"checked": 2, "lunas": 2, "batal": 0}
    assert spy["paid"] == ["PO-1", "PO-2"]
