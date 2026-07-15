"""ASAL ONGKIR = gudang PEMENUH (yang benar-benar mengirim), dengan kode pos yang
diatur admin untuk SETIAP gudang.

Regresi yang dijaga: gudang pemenuh sering BUKAN gudang pilihan pembeli (fallback
terdekat). Dulu, gudang pemenuh tanpa kode pos membuat sistem diam-diam memakai
kode pos gudang PEMBELI → ongkir Balikpapan→tujuan ditagih sebagai Jakarta→tujuan.
Sekarang: kode pos semua gudang tersimpan; sub-gudang ikut cabang pemiliknya; dan
bila tetap tak ada kode pos → ongkir DITOLAK dengan pesan jelas (tak menebak).
"""
import pytest
from fastapi import HTTPException

from app.routers import orders as R
from app.services import gudang, gudang_config, orders as OS

BUYER = {"jakarta": {"label": "01.Jakarta", "origin_postal": "14250"}}
COORDS = {
    "01.Jakarta": (-6.14, 106.91),
    "03.Balikpapan": (-1.27, 116.83),
    "06.B80 H1": (-6.21, 106.85),      # sub-gudang Jakarta (bukan cabang sendiri)
    "18.Pontianak": (-0.02, 109.34),   # cabang, sengaja TANPA kode pos
}
POSTAL = {"01.Jakarta": "14250", "03.Balikpapan": "76114"}
STOK = {
    "P-JKT": {"01.Jakarta": 5},
    "P-BPN": {"03.Balikpapan": 4},     # cuma di gudang non-pilihan pembeli
    "P-B80": {"06.B80 H1": 7},         # cuma di sub-gudang (kode pos ikut cabang)
    "P-PTK": {"18.Pontianak": 2},      # gudang tanpa kode pos → harus DITOLAK
}
USER = {"username": "budi", "role": "pembeli"}


@pytest.fixture
def dunia(monkeypatch):
    """Gudang, stok, dan tarif ongkir tiruan; rekam origin_postal yang dipakai."""
    monkeypatch.setattr(gudang_config, "buyer_locations", lambda: BUYER)
    monkeypatch.setattr(gudang_config, "coords_map", lambda: COORDS)
    monkeypatch.setattr(gudang_config, "postal_map", lambda: POSTAL)
    monkeypatch.setattr(R.part_index, "gudang_names", lambda: list(COORDS))
    monkeypatch.setattr(OS, "reservations", type("_R", (), {
        "reserved_map": staticmethod(lambda force=False: {}),
    }))
    monkeypatch.setattr(R.reservations, "reserved_map", lambda force=False: {})
    monkeypatch.setattr(R.part_index, "gudang_breakdown", lambda pn: dict(STOK.get(pn.upper(), {})))
    monkeypatch.setattr(R.sb, "get_user_gudang", lambda u: "jakarta")
    monkeypatch.setattr(R.shipping, "available", lambda: True)
    monkeypatch.setattr(R.harga, "total_weight_grams", lambda items, d, **kw: 2000)
    monkeypatch.setattr(R.payments, "available", lambda: True)

    seen = {}

    def _rates(username, w, v, dest_postal="", origin_postal=""):
        seen["origin_postal"] = origin_postal
        return [{"courier": "jne", "service": "REG", "price": 25000}], None

    monkeypatch.setattr(R.shipping, "get_rates", _rates)
    return seen


def _rates_for(pn):
    return R.RatesRequest(weight_grams=2000, dest_postal="40111",
                          items=[R.OrderItemIn(part_number=pn, qty=1)])


def test_asal_ongkir_gudang_pembeli_sendiri(dunia):
    R.shipping_rates(_rates_for("P-JKT"), USER)
    assert dunia["origin_postal"] == "14250"


def test_asal_ongkir_dari_gudang_pemenuh_bukan_gudang_pembeli(dunia):
    """Stok cuma di Balikpapan → ongkir dari 76114, BUKAN 14250 (gudang pembeli)."""
    R.shipping_rates(_rates_for("P-BPN"), USER)
    assert dunia["origin_postal"] == "76114"


def test_sub_gudang_memakai_kode_pos_cabang_pemiliknya(dunia):
    """'06.B80 H1' tak punya kode pos sendiri → ikut cabang pengelolanya (Jakarta)."""
    assert gudang.origin_postal_for_label("06.B80 H1") == "14250"
    R.shipping_rates(_rates_for("P-B80"), USER)
    assert dunia["origin_postal"] == "14250"


def test_gudang_tanpa_kode_pos_ditolak_bukan_pakai_kode_pos_pembeli(dunia):
    """Pontianak (cabang sendiri, kode pos belum diisi admin) → ongkir ditolak."""
    out = R.shipping_rates(_rates_for("P-PTK"), USER)
    assert out["rates"] == []
    assert "Pontianak" in out["error"] and "kode pos" in out["error"].lower()
    assert "origin_postal" not in dunia          # tarif tak pernah diminta


def test_create_order_menolak_gudang_tanpa_kode_pos(dunia):
    body = R.CreateOrderRequest(
        items=[R.OrderItemIn(part_number="P-PTK", qty=1)], courier="jne",
        courier_service="REG", shipping_cost=25000, weight_grams=2000,
        payment_method="gateway", recipient_name="Budi", recipient_phone="0811",
        recipient_address="Jl. X", recipient_postal="40111")
    with pytest.raises(HTTPException) as e:
        R.create_order(body, USER)
    assert e.value.status_code == 400
    assert "kode pos" in str(e.value.detail).lower()


def _order_body(pn, shipping_cost=0):
    return R.CreateOrderRequest(
        items=[R.OrderItemIn(part_number=pn, qty=1)], courier="jne",
        courier_service="REG", shipping_cost=shipping_cost, weight_grams=2000,
        payment_method="gateway", recipient_name="Budi", recipient_phone="0811",
        recipient_address="Jl. X", recipient_postal="99999")


def test_create_order_ongkir_kosong_ditolak_bukan_dipercaya_klien(dunia, monkeypatch):
    """Celah ONGKIR GRATIS: kode pos tujuan tak resolve → rates kosong. Klien kirim
    shipping_cost=0 → HARUS ditolak 400, BUKAN dipercaya (ongkir Rp 0)."""
    monkeypatch.setattr(R.shipping, "get_rates",
                        lambda u, w, v, dest_postal="", origin_postal="": ([], None))
    with pytest.raises(HTTPException) as e:
        R.create_order(_order_body("P-JKT", shipping_cost=0), USER)
    assert e.value.status_code == 400
    assert "ongkir" in str(e.value.detail).lower()


def test_create_order_shipping_unavailable_ditolak(dunia, monkeypatch):
    """Layanan ongkir mati → order gateway ditolak 503, klien tak dipercaya."""
    monkeypatch.setattr(R.shipping, "available", lambda: False)
    with pytest.raises(HTTPException) as e:
        R.create_order(_order_body("P-JKT", shipping_cost=0), USER)
    assert e.value.status_code == 503


def test_create_order_kurir_tak_ada_di_tarif_ditolak(dunia, monkeypatch):
    """Kurir/servis pilihan tak ada di tarif resmi → 400 (bukan pakai nilai klien)."""
    monkeypatch.setattr(R.shipping, "get_rates",
                        lambda u, w, v, dest_postal="", origin_postal="":
                        ([{"courier": "tiki", "service": "ECO", "price": 30000}], None))
    with pytest.raises(HTTPException) as e:
        R.create_order(_order_body("P-JKT", shipping_cost=0), USER)
    assert e.value.status_code == 400
