"""Gerbang kolom STOK server-side di router non-AI — mengikuti Menu Control.

Kembaran: tests/test_gate_router_harga.py (kolom Harga); pola aktor dari
tests/test_stok_gate_asisten.py. Celah yang ditutup sama: centang 'Kolom Stok'
hanya dipatuhi asisten, endpoint non-AI mengirim stok mentah ke akun staf yang
centangnya dimatikan.
"""
import pytest
from fastapi import HTTPException

from app.routers import parts as parts_router
from app.routers import stok as stok_router

ADMIN = {"username": "admin", "role": "admin"}
WAWAN = {"username": "wawan", "role": "user"}        # col_stok OFF
AGUS = {"username": "agustiono", "role": "user"}     # col_stok ON
PEMBELI = {"username": "roni", "role": "pembeli"}


@pytest.fixture
def perms(monkeypatch):
    """Menu Control: agustiono punya col_stok, wawan tidak (col_harga dua-duanya)."""
    monkeypatch.setattr("app.services.permissions.effective",
                        lambda kind, u, r: ["col_stok", "col_harga"] if u == "agustiono"
                        else ["col_harga"])


@pytest.fixture
def parts_data(monkeypatch):
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
def test_parts_search_mask_stok_tanpa_izin(perms, parts_data):
    r = parts_router.search(q="X", page=1, page_size=20, user=WAWAN).results[0]
    assert r.stok == "—" and r.gudang == {}          # ⛔ tak bocor
    assert r.harga == "Rp 100.000"                   # col_harga ON → harga tetap
    assert r.quantity == "2"                         # qty BOM bukan stok — utuh


def test_parts_search_stok_utuh_utk_yang_berhak(perms, parts_data):
    for u in (ADMIN, AGUS, PEMBELI):
        r = parts_router.search(q="X", page=1, page_size=20, user=u).results[0]
        assert r.stok == "5"
        assert r.gudang != {}


# ── /api/parts/accurate-stock ────────────────────────────────────────────────
@pytest.fixture
def accurate_full(monkeypatch):
    monkeypatch.setattr("app.services.accurate.available", lambda: True)
    monkeypatch.setattr("app.services.accurate.stock_full", lambda pn: {
        "available_to_sell": 5, "quantity": 6, "unit": "PCS", "name": "N",
        "no": "X", "item_type": "Persediaan", "price": 100000,
        "per_gudang": [{"gudang": "01.Jakarta", "qty": 5}]})


def test_accurate_stock_strip_stok_tanpa_izin(perms, accurate_full):
    out = parts_router.accurate_stock(pn="X", user=WAWAN)
    stock = out["stock"]
    for k in ("available_to_sell", "quantity", "per_gudang"):
        assert k not in stock
    assert stock["harga"] == 100000                  # col_harga ON → harga tetap
    assert stock["name"] == "N"                      # non-stok utuh


def test_accurate_stock_stok_utuh_utk_admin_dan_staf(perms, accurate_full):
    for u in (ADMIN, AGUS):
        stock = parts_router.accurate_stock(pn="X", user=u)["stock"]
        assert stock["available_to_sell"] == 5
        assert stock["per_gudang"]              # rincian antar-gudang utk internal


def test_accurate_stock_pembeli_lihat_ketersediaan_tanpa_per_gudang(perms, accurate_full):
    """Pembeli perlu tahu ADA stok (available_to_sell) tapi TIDAK boleh melihat
    distribusi antar-gudang — aturan 'gudang disembunyikan dari pembeli'."""
    stock = parts_router.accurate_stock(pn="X", user=PEMBELI)["stock"]
    assert stock["available_to_sell"] == 5      # ketersediaan tetap
    assert stock["harga"] == 100000             # harga jual tetap
    assert "per_gudang" not in stock            # rincian gudang DIBUANG


# ── /api/stok/list ───────────────────────────────────────────────────────────
@pytest.fixture
def stok_rows(monkeypatch):
    monkeypatch.setattr("app.services.accurate.available", lambda: True)
    monkeypatch.setattr("app.services.stok.stock_rows", lambda q, s: [
        {"pn": "X", "name": "N", "available_to_sell": 5, "unit": "PCS"}])
    monkeypatch.setattr("app.services.stok.total_count", lambda: 1)


def test_stok_list_mask_tanpa_izin(perms, stok_rows):
    out = stok_router.list_stok(q="", sort="pn", page=1, page_size=50, _user=WAWAN)
    assert out["rows"][0]["Stok"] == "—"
    assert out["rows"][0]["Part Number"] == "X"      # kolom lain utuh


def test_stok_list_utuh_utk_yang_berhak(perms, stok_rows):
    for u in (ADMIN, AGUS):
        out = stok_router.list_stok(q="", sort="pn", page=1, page_size=50, _user=u)
        assert out["rows"][0]["Stok"] != "—"


# ── /api/stok/list/export = 403 (Excel isi 0 semua menyesatkan) ──────────────
def test_stok_export_403_tanpa_izin(perms, stok_rows, monkeypatch):
    monkeypatch.setattr("app.services.stok.to_excel_bytes", lambda rows: b"xlsx")
    with pytest.raises(HTTPException) as e:
        stok_router.list_export(q="", sort="pn", _user=WAWAN)
    assert e.value.status_code == 403
    assert stok_router.list_export(q="", sort="pn", _user=AGUS).body == b"xlsx"


# ── /api/parts/batch-catalog: kolom stok dibuang dari pilihan ────────────────
def test_batch_catalog_buang_kolom_stok_tanpa_izin(perms, monkeypatch):
    """Handler async + Form — cukup uji unit keputusan kolomnya lewat helper
    permissions (perilaku handler = filter col_list bila not boleh_stok)."""
    from app.services import permissions
    assert permissions.boleh_stok(WAWAN) is False
    assert permissions.boleh_stok(AGUS) is True
    assert permissions.boleh_stok(PEMBELI) is True
