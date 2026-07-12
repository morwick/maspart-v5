"""Stok, harga, dan rincian per-gudang HANYA dari INDEKS ACCURATE (3×/hari).

Aturan pemilik 2026-07-12: stok.xlsx & harga.xlsx TAK PERNAH lagi jadi sumber —
indeks Accurate dibagi ke semua fitur (etalase, order, search, AI). part_index
mendelegasikan, jadi SEMUA konsumen lamanya (orders, buyer_catalog, parts, ai_sheet)
otomatis pindah sumber lewat satu titik.
"""
import pytest

from app.services import accurate, part_index


@pytest.fixture
def dua_sumber_beda(monkeypatch):
    """Excel & Accurate sengaja diisi BERBEDA — untuk membuktikan Excel tak terbaca."""
    monkeypatch.setitem(part_index._state, "gudang_cache",
                        {"PN-1": {"01.Jakarta": 999}})           # jebakan Excel
    monkeypatch.setitem(part_index._state, "stok_cache", {"PN-1": "999"})
    monkeypatch.setitem(part_index._state, "harga_lookup", {"PN-1": "Rp 999.999"})
    monkeypatch.setitem(part_index._state, "gudang_names", ["99.EXCEL-SAJA"])
    monkeypatch.setitem(part_index._state, "indexed_at", "T")    # ensure_index no-op
    monkeypatch.setattr(accurate, "gudang_breakdown",
                        lambda pn: {"02.Pekanbaru": 7.0} if pn.upper() == "PN-1" else {})
    monkeypatch.setattr(accurate, "warehouse_names", lambda: ["01.Jakarta", "02.Pekanbaru"])
    monkeypatch.setattr(accurate, "snapshot",
                        lambda: {accurate.norm_pn("PN-1"): {"stok": 7, "harga": 80_000}})


def test_breakdown_dari_accurate_bukan_excel(dua_sumber_beda):
    assert part_index.gudang_breakdown("pn-1") == {"02.Pekanbaru": 7}   # bukan 999 Jakarta


def test_pn_tak_ada_di_accurate_berarti_kosong(dua_sumber_beda, monkeypatch):
    """PN yang cuma ada di stok.xlsx = TIDAK berstok (Accurate satu-satunya sumber)."""
    monkeypatch.setitem(part_index._state, "gudang_cache", {"PN-2": {"01.Jakarta": 5}})
    assert part_index.gudang_breakdown("PN-2") == {}


def test_gudang_names_dari_accurate(dua_sumber_beda):
    assert part_index.gudang_names() == ["01.Jakarta", "02.Pekanbaru"]


def test_gudang_names_fallback_hanya_saat_indeks_kosong(dua_sumber_beda, monkeypatch):
    monkeypatch.setattr(accurate, "warehouse_names", lambda: [])
    assert part_index.gudang_names() == ["99.EXCEL-SAJA"]   # cold start saja


def test_baris_pencarian_stok_harga_dari_accurate(dua_sumber_beda):
    snap = accurate.snapshot()
    stok, hg = part_index._acc_stok_harga(snap, "PN-1")
    assert stok == "7" and hg == "Rp 80.000"                 # bukan 999 / Rp 999.999
    assert part_index._acc_stok_harga(snap, "PN-ASING") == ("—", "—")


def test_warehouse_names_dibaca_dari_by_gudang(monkeypatch):
    monkeypatch.setitem(accurate._index_cache, "by_gudang",
                        {"x": {"01.Jakarta": 2}, "y": {"23.Medan": 1, "01.Jakarta": 3}})
    assert accurate.warehouse_names() == ["01.Jakarta", "23.Medan"]
    monkeypatch.setitem(accurate._index_cache, "by_gudang", {})
    assert accurate.warehouse_names() == []
