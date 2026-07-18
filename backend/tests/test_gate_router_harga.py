"""Gerbang kolom HARGA server-side di router non-AI — mengikuti Menu Control.

Celah nyata (2026-07-19): centang 'Kolom Harga' hanya dipatuhi jalur Asisten AI;
endpoint non-AI (/api/parts/search, /api/harga/*) tetap mengembalikan harga
mentah ke semua akun login — penyembunyian cuma di React, tembus lewat network
tab / curl. Kini router memakai gerbang yang SAMA (permissions.boleh_harga).

Kembaran: tests/test_gate_router_stok.py (kolom Stok) dan pola aktor dari
tests/test_harga_gate_asisten.py.
"""
import pandas as pd
import pytest
from fastapi import HTTPException

from app.routers import harga as harga_router
from app.routers import parts as parts_router

ADMIN = {"username": "admin", "role": "admin"}
RIZAL = {"username": "rizal", "role": "user"}        # col_harga OFF
AGUS = {"username": "agustiono", "role": "user"}     # col_harga ON
PEMBELI = {"username": "roni", "role": "pembeli"}


@pytest.fixture
def perms(monkeypatch):
    """Menu Control: agustiono punya col_harga, rizal tidak (col_stok dua-duanya)."""
    monkeypatch.setattr("app.services.permissions.effective",
                        lambda kind, u, r: ["col_stok", "col_harga"] if u == "agustiono"
                        else ["col_stok"])


@pytest.fixture
def parts_data(monkeypatch):
    """Hasil pencarian deterministik: 1 baris + overlay harga/stok dari Accurate."""
    def _rows():
        return [{"file": "f.xlsx", "path": "", "sheet": "s", "part_number": "X",
                 "part_name": "N", "quantity": "2", "stok": "1", "harga": "Rp 1",
                 "gudang": {"01.Jakarta": 7}, "excel_row": 1, "source": ""}]
    monkeypatch.setattr("app.services.part_index.search_part_number", lambda q: _rows())
    monkeypatch.setattr("app.services.part_index.is_exact_match_found", lambda q: True)
    monkeypatch.setattr("app.services.part_index.gudang_names", lambda: ["01.Jakarta"])
    monkeypatch.setattr("app.services.gudang.gudang_for_user", lambda u, r: None)
    monkeypatch.setattr("app.services.gudang.shippable", lambda bd: bd)
    monkeypatch.setattr("app.services.reservations.reserved_map", lambda: {})
    monkeypatch.setattr(parts_router, "get_user_gudang", lambda u: None)
    monkeypatch.setattr("app.services.accurate.available", lambda: True)
    monkeypatch.setattr("app.services.accurate.index_key", lambda pn: pn)
    monkeypatch.setattr("app.services.accurate.snapshot",
                        lambda: {"X": {"stok": 5, "harga": 100000}})


# ── /api/parts/search ────────────────────────────────────────────────────────
def test_parts_search_mask_harga_tanpa_izin(perms, parts_data):
    r = parts_router.search(q="X", page=1, page_size=20, user=RIZAL).results[0]
    assert r.harga == "—"                        # ⛔ tak bocor
    assert r.stok == "5"                         # col_stok ON → stok tetap
    assert r.quantity == "2"                     # qty BOM bukan stok — utuh


def test_parts_search_harga_utuh_utk_yang_berhak(perms, parts_data):
    for u in (ADMIN, AGUS, PEMBELI):
        r = parts_router.search(q="X", page=1, page_size=20, user=u).results[0]
        assert r.harga == "Rp 100.000"


# ── /api/parts/accurate-stock ────────────────────────────────────────────────
@pytest.fixture
def accurate_full(monkeypatch):
    monkeypatch.setattr("app.services.accurate.available", lambda: True)
    monkeypatch.setattr("app.services.accurate.stock_full", lambda pn: {
        "available_to_sell": 5, "quantity": 6, "unit": "PCS", "name": "N",
        "no": "X", "item_type": "Persediaan", "price": 100000,
        "per_gudang": [{"gudang": "01.Jakarta", "qty": 5}]})


def test_accurate_stock_strip_harga_tanpa_izin(perms, accurate_full):
    out = parts_router.accurate_stock(pn="X", user=RIZAL)
    assert "harga" not in out["stock"]
    assert out["stock"]["available_to_sell"] == 5    # col_stok ON → stok tetap


def test_accurate_stock_harga_utuh_utk_yang_berhak(perms, accurate_full):
    for u in (ADMIN, AGUS, PEMBELI):
        assert parts_router.accurate_stock(pn="X", user=u)["stock"]["harga"] == 100000


# ── /api/harga/list ──────────────────────────────────────────────────────────
@pytest.fixture
def harga_list(monkeypatch):
    df = pd.DataFrame([{"Part Number": "X", "Part Name": "N", "Harga": 100000}])
    monkeypatch.setattr("app.services.harga.list_harga", lambda q, s: df)
    monkeypatch.setattr("app.services.harga.total_count", lambda: 1)


def test_harga_list_mask_tanpa_izin(perms, harga_list):
    out = harga_router.list_harga(q="", sort="pn", page=1, page_size=50, _user=RIZAL)
    assert out["rows"][0]["Harga (Rp)"] == "—"
    assert out["rows"][0]["Part Number"] == "X"      # kolom lain utuh


def test_harga_list_utuh_utk_yang_berhak(perms, harga_list):
    for u in (ADMIN, AGUS):
        out = harga_router.list_harga(q="", sort="pn", page=1, page_size=50, _user=u)
        assert out["rows"][0]["Harga (Rp)"] != "—"


# ── /api/harga/cari & /batch ─────────────────────────────────────────────────
def test_harga_cari_mask_tanpa_izin(perms, monkeypatch):
    monkeypatch.setattr("app.services.harga.cari_harga",
                        lambda pn, force_refresh=False:
                        {"pn": pn, "cny": 10.0, "idr": 22000, "rate": 2200.0, "note": ""})
    out = harga_router.cari(pn="X", refresh=False, _user=RIZAL)
    assert out["cny"] is None and out["idr"] is None
    out = harga_router.cari(pn="X", refresh=False, _user=AGUS)
    assert out["idr"] == 22000


def test_harga_batch_mask_tanpa_izin(perms, monkeypatch):
    monkeypatch.setattr("app.services.harga.batch_harga", lambda pns: {
        "rate": 2200.0, "count": 1, "found": 1,
        "results": [{"pn": "X", "cny": 10.0, "idr": 22000, "note": "", "status": "ok"}]})
    body = harga_router.BatchHargaRequest(text="X")
    out = harga_router.batch(body, _user=RIZAL)
    assert out["results"][0]["cny"] is None and out["results"][0]["idr"] is None
    out = harga_router.batch(body, _user=AGUS)
    assert out["results"][0]["idr"] == 22000


# ── Export = 403 (file harga tanpa harga menyesatkan) ────────────────────────
def test_harga_export_403_tanpa_izin(perms, harga_list, monkeypatch):
    monkeypatch.setattr("app.services.harga.to_excel_bytes", lambda df: b"xlsx")
    with pytest.raises(HTTPException) as e:
        harga_router.list_export(q="", sort="pn", _user=RIZAL)
    assert e.value.status_code == 403
    assert harga_router.list_export(q="", sort="pn", _user=AGUS).body == b"xlsx"


def test_harga_batch_export_403_tanpa_izin(perms, monkeypatch):
    monkeypatch.setattr("app.services.harga.batch_to_excel", lambda rate, rows: b"xlsx")
    body = harga_router.BatchExportRequest(rate=2200.0, rows=[{"pn": "X"}])
    with pytest.raises(HTTPException) as e:
        harga_router.batch_export(body, _user=RIZAL)
    assert e.value.status_code == 403
    assert harga_router.batch_export(body, _user=AGUS).body == b"xlsx"
