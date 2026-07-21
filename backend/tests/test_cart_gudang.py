"""Keranjang menyebutkan gudang PENGIRIM tiap item (dulu checkout sama sekali tak
memberi keterangan barang berangkat dari mana), dan menandai keranjang yang terpecah
ke lebih dari satu gudang — karena itu berarti lebih dari satu paket.

Gudang yang dilaporkan WAJIB sama dengan yang dipakai create_order (fulfillment_map
adalah sumber tunggalnya), supaya keterangan di checkout tidak berbohong.
"""
import pytest
from fastapi import HTTPException

from app.routers import orders as R
from app.services import gudang_config, orders as OS

BUYER = {"pekanbaru": {"label": "02.Pekanbaru", "origin_postal": "28291"}}
COORDS = {"02.Pekanbaru": (0.48, 101.39), "01.Jakarta": (-6.14, 106.91),
          "03.Balikpapan": (-1.27, 116.83)}
STOK = {
    "P-PKU": {"02.Pekanbaru": 5},      # gudang pembeli sendiri
    "P-BPN": {"03.Balikpapan": 2},     # gudang lain (fallback)
}
USER = {"username": "roni", "role": "pembeli", "gudang": "pekanbaru"}


HARGA = {"P-PKU": 100_000, "P-BPN": 250_000}   # P-TANPA-HARGA sengaja tak ada


@pytest.fixture(autouse=True)
def dunia(monkeypatch):
    monkeypatch.setattr(gudang_config, "buyer_locations", lambda: BUYER)
    monkeypatch.setattr(gudang_config, "coords_map", lambda: COORDS)
    monkeypatch.setattr(gudang_config, "no_ship_labels", lambda: set())
    monkeypatch.setattr("app.services.part_index.gudang_names", lambda: list(COORDS))
    monkeypatch.setattr("app.services.part_index.gudang_breakdown",
                        lambda pn: dict(STOK.get(pn.upper(), {})))
    monkeypatch.setattr("app.services.supabase_client.get_user_gudang", lambda u: "pekanbaru")
    monkeypatch.setattr(OS.reservations, "reserved_map", lambda force=False: {})
    monkeypatch.setattr(R.harga, "price_for_buyer",
                        lambda pn: (HARGA.get(pn.upper(), 0), "Part uji"))
    monkeypatch.setattr(R.harga, "weight_for", lambda pn, allow_remote=False: 1000)


def _minta(*pns):
    body = R.CartGudangRequest(items=[R.OrderItemIn(part_number=p, qty=1) for p in pns])
    return R.cart_gudang(body, USER)


def test_satu_gudang_tak_ditandai_multi():
    out = _minta("P-PKU")
    assert out["utama"] == "Pekanbaru" and out["multi"] is False
    (it,) = out["items"]
    assert it["part_number"] == "P-PKU" and it["gudang"] == "Pekanbaru"
    assert it["harga"] == 100_000 and it["bisa_dibeli"] is True


def test_item_dari_gudang_lain_disebut_gudangnya():
    out = _minta("P-BPN")
    (it,) = out["items"]
    assert it["gudang"] == "Balikpapan" and it["harga"] == 250_000
    assert out["utama"] == "Balikpapan"


def test_harga_dari_server_bukan_dari_keranjang_browser():
    """Harga yang dilaporkan = harga yang akan DITAGIH (price_for_buyer), bukan
    harga yang tersimpan di localStorage pembeli (bisa basi)."""
    out = _minta("P-PKU")
    assert out["items"][0]["harga_display"] == "Rp 100.000"


def test_part_tanpa_harga_ditandai_tak_bisa_dibeli():
    out = _minta("P-TANPA-HARGA")
    (it,) = out["items"]
    assert it["bisa_dibeli"] is False and it["alasan"] == "harga belum tersedia"
    assert it["harga"] == 0 and out["utama"] == ""


def test_keranjang_terpecah_ditandai_multi():
    out = _minta("P-PKU", "P-BPN")
    assert out["multi"] is True
    per_pn = {i["part_number"]: i["gudang"] for i in out["items"]}
    assert per_pn == {"P-PKU": "Pekanbaru", "P-BPN": "Balikpapan"}


def test_part_habis_ditandai_stok_habis():
    out = _minta("P-TIDAK-ADA")
    (it,) = out["items"]
    assert it["gudang"] == "" and it["bisa_dibeli"] is False
    assert out["utama"] == "" and out["multi"] is False


def test_gudang_yang_dilaporkan_sama_dengan_yang_dipakai_order():
    """Keterangan di checkout harus memakai sumber yang sama dengan create_order."""
    items = [{"part_number": "P-BPN", "qty": 1}]
    assert OS.fulfillment_map("roni", items) == {"P-BPN": "03.Balikpapan"}
    assert OS.fulfillment_gudang("roni", items) == "03.Balikpapan"


# ── SATU pesanan = SATU gudang (aturan pemilik) ─────────────────────────────
def test_ongkir_ditolak_untuk_keranjang_lintas_gudang():
    """Kurir tak bisa mengirim satu paket dari dua kota. Menghitung ongkir dari satu
    gudang dominan (perilaku lama) membuat paket gudang kedua TAK tertagih."""
    body = R.RatesRequest(weight_grams=2000, dest_postal="40111", items=[
        R.OrderItemIn(part_number="P-PKU", qty=1),
        R.OrderItemIn(part_number="P-BPN", qty=1),
    ])
    out = R.shipping_rates(body, USER)
    assert out["rates"] == []
    assert "2 gudang" in out["error"] and "Pekanbaru" in out["error"] and "Balikpapan" in out["error"]


def test_create_order_menolak_keranjang_lintas_gudang(monkeypatch):
    dibuat = []
    monkeypatch.setattr(R.payments, "available", lambda: True)
    monkeypatch.setattr(R.orders, "create_order", lambda *a, **kw: dibuat.append(a) or (None, "x"))
    body = R.CreateOrderRequest(
        items=[R.OrderItemIn(part_number="P-PKU", qty=1), R.OrderItemIn(part_number="P-BPN", qty=1)],
        payment_method="gateway", recipient_name="Roni", recipient_phone="0811",
        recipient_address="Jl. X", recipient_postal="40111")
    with pytest.raises(HTTPException) as e:
        R.create_order(body, USER)
    assert e.value.status_code == 400
    assert "SATU gudang" in str(e.value.detail)
    assert dibuat == []          # order tak pernah dibuat / stok tak direservasi


def test_create_order_tolak_qty_negatif(monkeypatch):
    """M4: qty<1 ditolak EKSPLISIT (400). Dulu router men-clamp ke 1 sedangkan
    create_order men-skip → item hantu direservasi tapi tak masuk pesanan."""
    monkeypatch.setattr(R.payments, "available", lambda: True)
    dibuat = []
    monkeypatch.setattr(R.orders, "create_order", lambda *a, **kw: dibuat.append(a) or ({}, None))
    body = R.CreateOrderRequest(
        items=[R.OrderItemIn(part_number="P-PKU", qty=-1)],
        payment_method="gateway", recipient_name="Roni", recipient_phone="0811",
        recipient_address="Jl. X", recipient_postal="40111")
    with pytest.raises(HTTPException) as e:
        R.create_order(body, USER)
    assert e.value.status_code == 400
    assert "tidak valid" in str(e.value.detail).lower()
    assert dibuat == []          # order & reservasi tak pernah dibuat


def test_create_order_diblokir_saat_indeks_basi(monkeypatch):
    """H3: indeks Accurate terlalu tua → checkout ditolak 503 (jangan jual harga
    basi / oversell vs ERP)."""
    monkeypatch.setattr(R.accurate, "index_too_old_for_checkout", lambda: True)
    body = R.CreateOrderRequest(
        items=[R.OrderItemIn(part_number="P-PKU", qty=1)],
        payment_method="gateway", recipient_name="Roni", recipient_phone="0811",
        recipient_address="Jl. X", recipient_postal="40111")
    with pytest.raises(HTTPException) as e:
        R.create_order(body, USER)
    assert e.value.status_code == 503


def test_keranjang_satu_gudang_tetap_lolos(monkeypatch):
    """Aturan ini tak boleh mengganggu keranjang normal (satu gudang)."""
    body = R.RatesRequest(weight_grams=2000, dest_postal="40111",
                          items=[R.OrderItemIn(part_number="P-PKU", qty=2)])
    monkeypatch.setattr(R.shipping, "available", lambda: True)
    monkeypatch.setattr(R.shipping, "get_rates",
                        lambda *a, **kw: ([{"courier": "jnt", "service": "EZ", "price": 12_000}], None))
    out = R.shipping_rates(body, USER)
    assert out["error"] is None and out["rates"][0]["price"] == 12_000
