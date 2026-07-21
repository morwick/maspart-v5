"""Enrichment stok PER-GUDANG ke indeks Accurate: view-itemstock-bywarehouse ditarik
SEKALI per siklus 5-jam (serial) → _index_cache['by_gudang'] = {norm_pn:{gudang:qty}},
dibagi ke semua fitur (stock_full & tool stok_gudang) tanpa panggilan live per-PN.
Jaringan di-mock (tanpa HTTP Accurate).
"""
import pytest

from app.services import accurate as ac


def _wh(rows):
    """Bentuk respons view-itemstock-bywarehouse.do."""
    return {"d": {"detailWarehouseData": rows}}


# ── _warehouse_map: parse detailWarehouseData → {gudang: qty>0} ──────────────
def test_warehouse_map(monkeypatch):
    monkeypatch.setattr(ac, "_warehouse_retry", lambda i: _wh([
        {"warehouseName": "04.Palembang", "balance": 8.0},
        {"warehouseName": "01.Jakarta", "balance": 55.0},
        {"warehouseName": "02.Kosong", "balance": 0.0},        # qty 0 → dibuang
        {"warehouseName": "", "balance": 3.0},                 # nama kosong → dibuang
    ]))
    m = ac._warehouse_map(123)
    assert m == {"04.Palembang": 8.0, "01.Jakarta": 55.0}


def test_warehouse_map_error(monkeypatch):
    def _boom(i):
        raise ac.AccurateError("gagal")
    monkeypatch.setattr(ac, "_warehouse_retry", _boom)
    assert ac._warehouse_map(1) == {}                          # non-fatal → {}


# ── enrich_warehouses: bangun by_gudang utk SEMUA barang berstok>0 ───────────
def test_enrich_warehouses(monkeypatch):
    by_pn = {
        "AZ1": {"available_to_sell": 5.0, "accurate_id": 11},
        "AZ2": {"available_to_sell": 0.0, "accurate_id": 12},   # stok 0 → dilewati
        "AZ3": {"available_to_sell": 3.0, "accurate_id": None}, # tanpa id → dilewati
        "AZ4": {"available_to_sell": 7.0, "accurate_id": 14},
    }
    monkeypatch.setattr(ac, "available", lambda: True)
    monkeypatch.setattr(ac, "refresh", lambda force=False: {"by_pn": by_pn})
    monkeypatch.setitem(ac._index_cache, "by_gudang", {})
    wh = {11: _wh([{"warehouseName": "04.Palembang", "balance": 5.0}]),
          14: _wh([{"warehouseName": "01.Jakarta", "balance": 7.0},
                   {"warehouseName": "04.Palembang", "balance": 2.0}])}
    monkeypatch.setattr(ac, "_warehouse_retry", lambda i: wh[i])

    n = ac.enrich_warehouses()
    assert n == 2                                              # AZ1 & AZ4 saja
    assert ac.gudang_breakdown("AZ1") == {"04.Palembang": 5.0}
    assert ac.gudang_breakdown("AZ4") == {"01.Jakarta": 7.0, "04.Palembang": 2.0}
    assert ac.gudang_breakdown("AZ2") == {}                    # tak ter-enrich
    assert ac.gudang_enriched_count() == 2


# ── H2: guard-kosong — jangan timpa by_gudang bagus dengan {} ────────────────
def test_enrich_per_pn_kosong_tak_menimpa_indeks_bagus(monkeypatch):
    """Nol PN ter-enrich (mis. semua stok 0) TAK boleh menimpa indeks lama →
    kalau tidak, etalase & checkout jadi 'habis' semua sampai window berikut."""
    bagus = {"AZ1": {"04.Palembang": 5.0}}
    monkeypatch.setitem(ac._index_cache, "by_gudang", dict(bagus))
    monkeypatch.setattr(ac, "available", lambda: True)
    monkeypatch.setattr(ac, "refresh", lambda force=False: {"by_pn": {}})  # 0 in-stock
    n = ac.enrich_warehouses()
    assert n == 1                                              # jumlah lama
    assert ac.gudang_breakdown("AZ1") == {"04.Palembang": 5.0}  # DIPERTAHANKAN


def test_enrich_report_kosong_tak_menimpa_indeks_bagus(monkeypatch):
    bagus = {"AZ1": {"01.Jakarta": 9.0}}
    monkeypatch.setitem(ac._index_cache, "by_gudang", dict(bagus))
    monkeypatch.setattr(ac, "_ensure_session", lambda: object())
    monkeypatch.setattr(ac, "_stock_report_xls", lambda sess: b"")
    monkeypatch.setattr(ac, "_parse_stock_report", lambda xls: {})   # parse 0 PN
    ac.enrich_warehouses_via_report()
    assert ac.gudang_breakdown("AZ1") == {"01.Jakarta": 9.0}   # tak ditimpa {}


# ── H3: umur indeks untuk checkout ──────────────────────────────────────────
def test_index_too_old_for_checkout(monkeypatch):
    import time
    monkeypatch.setitem(ac._index_cache, "ts", 0.0)
    assert ac.index_too_old_for_checkout() is True            # belum pernah refresh
    monkeypatch.setitem(ac._index_cache, "ts", time.time())
    assert ac.index_too_old_for_checkout() is False           # baru
    monkeypatch.setitem(ac._index_cache, "ts", time.time() - 25 * 3600)
    assert ac.index_too_old_for_checkout() is True            # 25 jam → basi


def test_gudang_breakdown_kosong(monkeypatch):
    monkeypatch.setitem(ac._index_cache, "by_gudang", {})
    assert ac.gudang_breakdown("XYZ") == {}
    assert ac.gudang_breakdown("") == {}


# ── items_matching: pindai indeks utk nama/PN yang cocok kata kunci ──────────
def test_items_matching(monkeypatch):
    items = [
        {"pn": "A1", "name": "Clutch Driven Disc"},
        {"pn": "A2", "name": "Brake Lining"},
        {"pn": "DRIVENX", "name": "Random Part"},          # cocok via PN
        {"pn": "A4", "name": "Oil Filter"},
    ]
    monkeypatch.setitem(ac._index_cache, "items", items)
    got = {it["pn"] for it in ac.items_matching(["driven disc", "brake lining"])}
    assert got == {"A1", "A2"}                             # word-boundary di nama
    # term terlalu pendek (<3) diabaikan; limit dihormati
    assert ac.items_matching(["ab"]) == []
    assert len(ac.items_matching(["a"], limit=1)) == 0     # 'a' <3 → tak ada pola


# ── stock_full: per-gudang dari INDEKS (tanpa live) + fallback live ──────────
def test_stock_full_pakai_indeks(monkeypatch):
    # PN ter-enrich → per_gudang dari by_gudang; _warehouse_retry TAK dipanggil.
    monkeypatch.setitem(ac._index_cache, "by_pn",
                        {"PN1": {"pn": "PN1", "name": "X", "available_to_sell": 11.0, "accurate_id": 1}})
    monkeypatch.setitem(ac._index_cache, "by_gudang",
                        {"PN1": {"01.Jakarta": 9.0, "04.Palembang": 2.0}})

    def _fail(i):
        raise AssertionError("stock_full TIDAK boleh panggil live saat sudah ter-enrich")
    monkeypatch.setattr(ac, "_warehouse_retry", _fail)

    sf = ac.stock_full("PN1")
    per = {g["gudang"]: g["qty"] for g in sf["per_gudang"]}
    assert per == {"01.Jakarta": 9.0, "04.Palembang": 2.0}
    assert sf["per_gudang"][0]["qty"] == 9.0                   # urut qty menurun


def test_stock_full_belum_enrich_tanpa_live(monkeypatch):
    # ⛔ ATURAN KERAS: PN belum ter-enrich → per_gudang KOSONG, TANPA panggilan
    # live (dulu ada fallback live per-PN — kini dihapus, login hanya utk penawaran).
    monkeypatch.setitem(ac._index_cache, "by_pn",
                        {"PN2": {"pn": "PN2", "name": "Y", "available_to_sell": 4.0, "accurate_id": 2}})
    monkeypatch.setitem(ac._index_cache, "by_gudang", {})      # kosong
    monkeypatch.setattr(ac, "_warehouse_retry",
                        lambda i: (_ for _ in ()).throw(AssertionError("dilarang login live")))
    sf = ac.stock_full("PN2")
    assert sf["available_to_sell"] == 4.0     # agregat tetap dari indeks
    assert sf["per_gudang"] == []             # kosong sampai enrichment 5-jam berikutnya
