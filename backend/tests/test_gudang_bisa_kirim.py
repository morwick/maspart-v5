"""Centang 'Bisa Kirim' per gudang: gudang internal (mis. B80) tak boleh memenuhi
pesanan online.

Kenapa perlu: kandidat gudang PEMENUH datang dari INDEKS STOK (semua gudang ber-stok
di Accurate), bukan dari konfigurasi admin — jadi gudang yang tak pernah diatur admin
pun ikut menawarkan barangnya, dan B80 H1/H2 malah hampir selalu menang karena
koordinat bawaannya sama persis dengan Jakarta (jarak 0).

Default: BOLEH mengirim (perilaku lama). Hanya gudang yang sengaja dimatikan admin
yang disaring — dan hanya di jalur PEMBELI; staf/admin tetap melihat stok apa adanya.
"""
import pytest

from app.routers import admin as admin_router
from app.routers import parts as parts_router
from app.routers.admin import GudangItem, SaveGudangRequest, save_gudang
from app.services import buyer_catalog as BC, gudang, gudang_config, orders as OS

BUYER = {"jakarta": {"label": "01.Jakarta", "origin_postal": "14250"}}
COORDS = {"01.Jakarta": (-6.14, 106.91), "06.B80 H1": (-6.21, 106.85),
          "03.Balikpapan": (-1.27, 116.83)}
STOK_B80 = {"06.B80 H1": 9}          # part yang HANYA ada di gudang internal


@pytest.fixture
def b80_dimatikan(monkeypatch):
    monkeypatch.setattr(gudang_config, "buyer_locations", lambda: BUYER)
    monkeypatch.setattr(gudang_config, "coords_map", lambda: COORDS)
    monkeypatch.setattr(gudang_config, "no_ship_labels", lambda: {"06.B80 H1"})


@pytest.fixture
def semua_boleh(monkeypatch):
    monkeypatch.setattr(gudang_config, "buyer_locations", lambda: BUYER)
    monkeypatch.setattr(gudang_config, "coords_map", lambda: COORDS)
    monkeypatch.setattr(gudang_config, "no_ship_labels", lambda: set())


def _produk():
    return {"part_number": "PN-B80", "name": "Brake lining", "gudang": dict(STOK_B80),
            "_hay": "PN-B80 BRAKE LINING", "_flat": "PNB80", "kategori": ["rem"],
            "foto": None, "harga": 500_000, "harga_display": "Rp 500.000",
            "berat": 2000, "stok_total": 9}


def _stok_etalase(monkeypatch):
    monkeypatch.setattr(BC.reservations, "reserved_map", lambda force=False: {})
    return BC._scoped_stock(_produk(), "roni", "01.Jakarta", list(COORDS), {})


def test_default_gudang_b80_tetap_mengirim(semua_boleh, monkeypatch):
    """Tanpa dimatikan admin, perilaku lama dipertahankan (fallback ke B80)."""
    stok, label = _stok_etalase(monkeypatch)
    assert stok == 9 and label == "B80 H1"


def test_etalase_tak_menawarkan_stok_gudang_non_kirim(b80_dimatikan, monkeypatch):
    stok, label = _stok_etalase(monkeypatch)
    assert stok == 0 and label == ""      # tampil HABIS, bukan 'READY · B80 H1'


def test_gudang_pemenuh_order_tak_memilih_gudang_non_kirim(b80_dimatikan, monkeypatch):
    monkeypatch.setattr(OS.reservations, "reserved_map", lambda force=False: {})
    monkeypatch.setattr("app.services.part_index.gudang_names", lambda: list(COORDS))
    monkeypatch.setattr("app.services.part_index.gudang_breakdown",
                        lambda pn: dict(STOK_B80))
    monkeypatch.setattr("app.services.supabase_client.get_user_gudang", lambda u: "jakarta")
    assert OS.fulfillment_gudang("roni", [{"part_number": "PN-B80", "qty": 1}]) == ""


def test_staf_internal_tetap_melihat_stok_b80(b80_dimatikan, monkeypatch):
    """Saringan hanya untuk pembeli — gudang & admin harus tetap melihat stok apa adanya."""
    monkeypatch.setattr(parts_router.part_index, "gudang_names", lambda: list(COORDS))
    hasil = parts_router._scope_gudang(
        [{"part_number": "PN-B80", "gudang": dict(STOK_B80)}],
        {"username": "mas", "role": "admin"},
    )
    assert hasil[0]["gudang"] == STOK_B80


def test_admin_menyimpan_gudang_non_kirim(monkeypatch):
    keluar = {}
    monkeypatch.setattr(admin_router.gudang_config, "buyer_locations", lambda: {})
    monkeypatch.setattr(admin_router.gudang_config, "postal_map", lambda: {})
    monkeypatch.setattr(admin_router.gudang_config, "save",
                        lambda c, b, p=None, po=None, ns=None: keluar.update(no_ship=ns) or (True, ""))
    save_gudang(SaveGudangRequest(items=[
        GudangItem(label="06.B80 H1", lat=-6.21, lon=106.85, can_ship=False),
        GudangItem(label="01.Jakarta", lat=-6.14, lon=106.91, can_ship=True),
    ]), _admin={})
    assert keluar["no_ship"] == ["06.B80 H1"]      # hanya yang dimatikan yang disimpan
