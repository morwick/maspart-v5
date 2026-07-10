"""Indeks Accurate 5-jam = SUMBER STOK TUNGGAL yang dibagi semua fitur.

⛔ ATURAN KERAS PEMILIK: baca stok/harga/per-gudang HANYA dari indeks (tarikan
sekali per 5 jam) — TIDAK PERNAH panggilan live per-PN. snapshot() (overlay
pencarian) & stock_full() (detail part / tool AI) membaca indeks bersama tanpa
tarikan sendiri. Login live HANYA untuk membuat penawaran.
Tanpa jaringan: _index_cache di-monkeypatch.
"""
from __future__ import annotations

from app.services import accurate


def _seed_index(monkeypatch, by_pn: dict, snap: dict | None = None, by_gudang: dict | None = None):
    monkeypatch.setattr(accurate, "_index_cache", {
        "ts": 123.0,
        "items": list(by_pn.values()),
        "by_pn": by_pn,
        "by_gudang": by_gudang or {},
        "snap": snap if snap is not None else {},
    })
    # Jaring pengaman: STOK BACA tak boleh menembak Accurate live sama sekali.
    def _no_live(*a, **k):
        raise AssertionError("baca stok DILARANG login/panggilan live per-PN")
    monkeypatch.setattr(accurate, "_warehouse_retry", _no_live)
    monkeypatch.setattr(accurate, "_search_retry", _no_live)
    monkeypatch.setattr(accurate, "_ensure_session", _no_live)


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


def test_stock_full_gudang_dari_indeks_tanpa_live(monkeypatch):
    entry = {"no": "000001.WG123", "pn": "WG123", "name": "Bearing",
             "available_to_sell": 7.0, "quantity": 7.0, "unit": "Pc",
             "item_type": "Persediaan", "price": 1000.0, "accurate_id": 42}
    # per-gudang dari INDEKS (enrichment 5-jam) — bukan panggilan live.
    _seed_index(monkeypatch, {"WG123": entry},
                by_gudang={"WG123": {"01.Jakarta": 5, "05.Makasar": 2}})

    hit = accurate.stock_full("wg-123")  # normalisasi PN tetap jalan
    assert hit["available_to_sell"] == 7.0
    # urut qty menurun; _warehouse_retry TAK dipanggil (dijaga _no_live)
    assert [g["gudang"] for g in hit["per_gudang"]] == ["01.Jakarta", "05.Makasar"]


def test_stock_full_belum_ter_enrich_per_gudang_kosong(monkeypatch):
    entry = {"no": "000001.WG123", "pn": "WG123", "name": "Bearing",
             "available_to_sell": 7.0, "quantity": 7.0, "unit": "Pc",
             "item_type": "Persediaan", "price": 1000.0, "accurate_id": 42}
    _seed_index(monkeypatch, {"WG123": entry}, by_gudang={})  # belum ter-enrich

    hit = accurate.stock_full("WG123")
    assert hit["available_to_sell"] == 7.0     # agregat tetap tampil (dari indeks)
    assert hit["per_gudang"] == []             # kosong — TANPA panggilan live


def test_stock_full_pn_tak_ada_di_indeks(monkeypatch):
    _seed_index(monkeypatch, {})
    assert accurate.stock_full("WG999") is None  # pemanggil fallback Excel
