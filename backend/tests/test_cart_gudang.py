"""Keranjang menyebutkan gudang PENGIRIM tiap item (dulu checkout sama sekali tak
memberi keterangan barang berangkat dari mana), dan menandai keranjang yang terpecah
ke lebih dari satu gudang — karena itu berarti lebih dari satu paket.

Gudang yang dilaporkan WAJIB sama dengan yang dipakai create_order (fulfillment_map
adalah sumber tunggalnya), supaya keterangan di checkout tidak berbohong.
"""
import pytest

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
