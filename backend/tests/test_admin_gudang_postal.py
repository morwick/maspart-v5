"""Kode pos ASAL ongkir di konfigurasi gudang admin: dikirim dari UI (auto-isi
dari koordinat), disanitasi (digit saja), dan back-compat (None → nilai lama)."""
import pytest

from app.routers import admin as admin_router
from app.routers.admin import GudangItem, SaveGudangRequest, save_gudang


@pytest.fixture
def captured(monkeypatch):
    out = {}
    monkeypatch.setattr(admin_router.gudang_config, "buyer_locations",
                        lambda: {"jakarta": {"label": "01.Jakarta", "origin_postal": "14250"}})

    def _save(coords, buyer, pic):
        out.update({"coords": coords, "buyer": buyer, "pic": pic})
        return True, ""

    monkeypatch.setattr(admin_router.gudang_config, "save", _save)
    return out


def _item(**kw):
    base = dict(label="02.Pekanbaru", lat=0.48, lon=101.44, selectable=True, key="pekanbaru")
    base.update(kw)
    return GudangItem(**base)


def test_postal_dari_ui_tersimpan(captured):
    save_gudang(SaveGudangRequest(items=[_item(origin_postal="28292")]), _admin={})
    assert captured["buyer"]["pekanbaru"]["origin_postal"] == "28292"


def test_postal_disanitasi_digit_saja(captured):
    save_gudang(SaveGudangRequest(items=[_item(origin_postal=" 28292-ID ")]), _admin={})
    assert captured["buyer"]["pekanbaru"]["origin_postal"] == "28292"


def test_postal_none_pertahankan_nilai_lama(captured):
    # UI lama tak mengirim origin_postal → nilai lama label itu dipertahankan.
    save_gudang(SaveGudangRequest(items=[
        GudangItem(label="01.Jakarta", lat=-6.21, lon=106.85, selectable=True,
                   key="jakarta", origin_postal=None),
    ]), _admin={})
    assert captured["buyer"]["jakarta"]["origin_postal"] == "14250"


def test_postal_kosong_eksplisit_mengosongkan(captured):
    save_gudang(SaveGudangRequest(items=[_item(origin_postal="")]), _admin={})
    assert captured["buyer"]["pekanbaru"]["origin_postal"] == ""
