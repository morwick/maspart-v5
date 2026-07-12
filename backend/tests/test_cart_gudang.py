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


def _minta(*pns):
    body = R.CartGudangRequest(items=[R.OrderItemIn(part_number=p, qty=1) for p in pns])
    return R.cart_gudang(body, USER)


def test_satu_gudang_tak_ditandai_multi():
    out = _minta("P-PKU")
    assert out["utama"] == "Pekanbaru" and out["multi"] is False
    assert out["items"] == [{"part_number": "P-PKU", "gudang": "Pekanbaru"}]


def test_item_dari_gudang_lain_disebut_gudangnya():
    out = _minta("P-BPN")
    assert out["items"] == [{"part_number": "P-BPN", "gudang": "Balikpapan"}]
    assert out["utama"] == "Balikpapan"


def test_keranjang_terpecah_ditandai_multi():
    out = _minta("P-PKU", "P-BPN")
    assert out["multi"] is True
    per_pn = {i["part_number"]: i["gudang"] for i in out["items"]}
    assert per_pn == {"P-PKU": "Pekanbaru", "P-BPN": "Balikpapan"}


def test_part_habis_tak_muncul():
    out = _minta("P-TIDAK-ADA")
    assert out["items"] == [] and out["utama"] == "" and out["multi"] is False


def test_gudang_yang_dilaporkan_sama_dengan_yang_dipakai_order():
    """Keterangan di checkout harus memakai sumber yang sama dengan create_order."""
    items = [{"part_number": "P-BPN", "qty": 1}]
    assert OS.fulfillment_map("roni", items) == {"P-BPN": "03.Balikpapan"}
    assert OS.fulfillment_gudang("roni", items) == "03.Balikpapan"
