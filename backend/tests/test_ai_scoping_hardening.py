"""Audit-fix asisten AI (2026-07-07): scoping peran + integritas jawaban.

Menutup temuan audit:
  #1 grounding tak percaya riwayat assistant kiriman klien
  #2 error tool → tak boleh mengarang angka (catatan sistem disuntik)
  #3 stok per-gudang tak bocor ke pembeli (detail_part/cari_part/stok_accurate)
  #4 allow-list tool terpusat di _run_tool
  #5 pesanan_saya/detail_pesanan tolak username kosong
  #6 anggaran retry terpisah dari ronde tool
  #7 hasil tool besar dipotong sebelum masuk riwayat
"""
import pytest

from app.services import ai_assistant as ai


ADMIN = {"username": "admin", "role": "admin"}
PEMBELI = {"username": "beli", "role": "pembeli"}


# ── #3 Kebocoran stok per-gudang ke pembeli ─────────────────────────────────

def test_hide_gudang_helper():
    r = {"stok_per_gudang": {"01.Jakarta": 5}}
    assert "stok_per_gudang" not in ai._hide_gudang_for_buyer(dict(r), PEMBELI)
    assert "stok_per_gudang" in ai._hide_gudang_for_buyer(dict(r), ADMIN)


def test_detail_part_pembeli_tak_lihat_gudang(monkeypatch):
    monkeypatch.setattr(ai.part_index, "search_part_number",
                        lambda pn: [{"part_number": "WG123", "part_name": "X",
                                     "stok": "10", "gudang": {"01.Jakarta": 6, "05.Makasar": 4},
                                     "harga": "Rp 100", "file": "NX.xlsx", "path": ""}])
    monkeypatch.setattr(ai.accurate, "available", lambda: False)
    monkeypatch.setattr(ai.sims, "get_part_spec", lambda pn: {})
    adm = ai._t_detail_part({"part_number": "WG123"}, ADMIN)
    beli = ai._t_detail_part({"part_number": "WG123"}, PEMBELI)
    assert adm.get("stok_per_gudang") == {"01.Jakarta": 6, "05.Makasar": 4}
    assert "stok_per_gudang" not in beli


def test_stok_accurate_pembeli_tak_enumerasi_cabang(monkeypatch):
    monkeypatch.setattr(ai.accurate, "available", lambda: True)
    monkeypatch.setattr(ai.accurate, "stock_full", lambda pn: {
        "name": "X", "no": "K1", "available_to_sell": 9, "quantity": 9,
        "unit": "PCS", "item_type": "Inventory", "price": 1000,
        "per_gudang": [{"gudang": "01.Jakarta", "qty": 9}]})
    beli = ai._t_stok_accurate({"part_number": "WG123"}, PEMBELI)
    assert beli["ditemukan"] is True
    assert "stok_per_gudang" not in beli
    adm = ai._t_stok_accurate({"part_number": "WG123"}, ADMIN)
    assert adm["stok_per_gudang"] == [{"gudang": "01.Jakarta", "qty": 9}]


# ── #4 Allow-list tool terpusat ─────────────────────────────────────────────

def test_run_tool_tolak_tool_di_luar_peran(monkeypatch):
    # harga_sims tidak ditawarkan ke pembeli → _run_tool harus menolak sebelum
    # handler dijalankan (defense in depth).
    allowed = ai._allowed_tool_names(PEMBELI)
    assert "harga_sims" not in allowed
    called = {"n": 0}
    monkeypatch.setitem(ai._DISPATCH, "harga_sims",
                        lambda a, u: called.__setitem__("n", called["n"] + 1) or {})
    out = ai._run_tool("harga_sims", {}, PEMBELI)
    assert out.get("denied") is True
    assert called["n"] == 0
    # admin: diizinkan (allow-list memuatnya).
    assert "harga_sims" in ai._allowed_tool_names(ADMIN)


# ── #5 Username kosong ditolak ──────────────────────────────────────────────

def test_pesanan_username_kosong_ditolak(monkeypatch):
    hit = {"n": 0}
    monkeypatch.setattr(ai.orders, "list_orders",
                        lambda username=None: hit.__setitem__("n", hit["n"] + 1) or [])
    out = ai._t_pesanan_saya({}, {"username": "", "role": "pembeli"})
    assert "error" in out
    assert hit["n"] == 0  # tak pernah query tanpa filter


def test_detail_pesanan_username_kosong_ditolak(monkeypatch):
    hit = {"n": 0}
    monkeypatch.setattr(ai.orders, "get_order",
                        lambda code, username=None: hit.__setitem__("n", hit["n"] + 1) or None)
    out = ai._t_detail_pesanan({"order_code": "INV1"}, {"username": "", "role": "pembeli"})
    assert "error" in out
    assert hit["n"] == 0


# ── #2 Deteksi kegagalan tool ───────────────────────────────────────────────

def test_tool_failed_deteksi():
    assert ai._tool_failed({"error": "x"})
    assert ai._tool_failed({"denied": True})
    assert ai._tool_failed({"found": False})
    assert ai._tool_failed({"ditemukan": False})
    assert not ai._tool_failed({"found": True, "stok": 5})
    assert not ai._tool_failed({"stok": 5})


# ── #7 Cap ukuran hasil tool ────────────────────────────────────────────────

def test_cap_tool_content():
    kecil = '{"a": 1}'
    assert ai._cap_tool_content(kecil) == kecil
    besar = "x" * (ai._MAX_TOOL_CONTENT + 5000)
    out = ai._cap_tool_content(besar)
    assert len(out) < len(besar)
    assert "dipotong" in out
