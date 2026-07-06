"""Indeks Accurate 5-jam = SUMBER STOK TUNGGAL yang dibagi semua fitur.

snapshot() (overlay pencarian) & stock_full() (detail part / tool AI) harus membaca
indeks bersama TANPA tarikan massal sendiri; per_gudang tetap panggilan kecil per-PN.
Tanpa jaringan: _index_cache & _warehouse_retry di-monkeypatch.
"""
from __future__ import annotations

from app.services import accurate


def _seed_index(monkeypatch, by_pn: dict, snap: dict | None = None):
    monkeypatch.setattr(accurate, "_index_cache", {
        "ts": 123.0,
        "items": list(by_pn.values()),
        "by_pn": by_pn,
        "snap": snap if snap is not None else {},
    })
    monkeypatch.setattr(accurate, "_stock_cache", {})


def test_snapshot_adalah_view_indeks_dan_non_blocking(monkeypatch):
    snap = {"WG123": {"stok": 7.0, "harga": 1000.0, "unit": "Pc"}}
    _seed_index(monkeypatch, {}, snap)

    def _boom():
        raise AssertionError("snapshot tak boleh menarik katalog sendiri")

    monkeypatch.setattr(accurate, "fetch_all_items", _boom)
    assert accurate.snapshot() == snap


def test_snapshot_kosong_saat_cold_start(monkeypatch):
    _seed_index(monkeypatch, {}, {})
    assert accurate.snapshot() == {}  # pemanggil fallback Excel


def test_stock_full_dari_indeks_plus_gudang_live(monkeypatch):
    entry = {"no": "000001.WG123", "pn": "WG123", "name": "Bearing",
             "available_to_sell": 7.0, "quantity": 7.0, "unit": "Pc",
             "item_type": "Persediaan", "price": 1000.0, "accurate_id": 42}
    _seed_index(monkeypatch, {"WG123": entry})

    seen = {}

    def _wh(item_id):
        seen["id"] = item_id
        return {"d": {"detailWarehouseData": [
            {"warehouseName": "01.Jakarta", "balance": 5, "id": 1},
            {"warehouseName": "05.Makasar", "balance": 2, "id": 5},
            {"warehouseName": "02.Kosong", "balance": 0, "id": 2},
        ]}}

    monkeypatch.setattr(accurate, "_warehouse_retry", _wh)

    def _boom(**kw):
        raise AssertionError("stock_full tak boleh menembak pencarian Accurate per-PN")

    monkeypatch.setattr(accurate, "_search_retry", _boom)

    hit = accurate.stock_full("wg-123")  # normalisasi PN tetap jalan
    assert hit["available_to_sell"] == 7.0
    assert seen["id"] == 42  # id barang diambil dari indeks, bukan search live
    assert [g["gudang"] for g in hit["per_gudang"]] == ["01.Jakarta", "05.Makasar"]


def test_stock_full_pn_tak_ada_di_indeks(monkeypatch):
    _seed_index(monkeypatch, {})
    assert accurate.stock_full("WG999") is None  # pemanggil fallback Excel


def test_stock_full_gudang_gagal_nonfatal(monkeypatch):
    entry = {"no": "000001.WG123", "pn": "WG123", "name": "Bearing",
             "available_to_sell": 7.0, "quantity": 7.0, "unit": "Pc",
             "item_type": "Persediaan", "price": 1000.0, "accurate_id": 42}
    _seed_index(monkeypatch, {"WG123": entry})

    def _fail(item_id):
        raise accurate.AccurateError("gudang down")

    monkeypatch.setattr(accurate, "_warehouse_retry", _fail)
    hit = accurate.stock_full("WG123")
    assert hit["available_to_sell"] == 7.0
    assert hit["per_gudang"] == []  # agregat tetap tampil
