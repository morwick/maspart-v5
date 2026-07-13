"""Penjaga HARGA TERPUSAT asisten — mengikuti Menu Control 'Kolom Harga'.

Bug nyata: staf 'rizal' dengan izin kolom harga DIMATIKAN tetap melihat harga di
asisten AI. Sebabnya asisten memakai gerbang terpisah (can_see_price: admin/'mas'
saja) yang hanya dicek 2-3 dari ~20 tool; sisanya membocorkan harga_lokal tanpa
cek izin. Kini: satu helper _boleh_harga (admin/pembeli selalu; staf ikut izin
col_harga) + strip TERPUSAT di _run_tool membuang semua field harga bila tak berhak.
"""
import pytest

from app.services import ai_assistant as ai

ADMIN = {"username": "admin", "role": "admin"}
RIZAL = {"username": "rizal", "role": "user"}      # col_harga OFF
AGUS = {"username": "agustiono", "role": "user"}   # col_harga ON
PEMBELI = {"username": "roni", "role": "pembeli"}


@pytest.fixture
def perms(monkeypatch):
    """Menu Control: agustiono punya col_harga, rizal tidak."""
    monkeypatch.setattr("app.services.permissions.effective",
                        lambda kind, u, r: ["col_stok", "col_harga"] if u == "agustiono"
                        else ["col_stok"])


# ── _boleh_harga (gerbang tunggal) ───────────────────────────────────────────
def test_boleh_harga_per_peran(perms):
    assert ai._boleh_harga(ADMIN) is True
    assert ai._boleh_harga(PEMBELI) is True          # harga jual = yang dibayar
    assert ai._boleh_harga(AGUS) is True             # col_harga ON
    assert ai._boleh_harga(RIZAL) is False           # col_harga OFF


# ── _strip_harga (rekursif) ──────────────────────────────────────────────────
def test_strip_harga_dict_dan_list_bersarang():
    r = {"parts": [{"pn": "X", "harga_lokal": "Rp 5", "stok_total": 3},
                   {"pn": "Y", "harga": 10}],
         "harga_sims": "Rp 9", "nama": "z"}
    ai._strip_harga(r)
    assert r["parts"][0] == {"pn": "X", "stok_total": 3}
    assert "harga" not in r["parts"][1] and "harga_sims" not in r
    assert r["nama"] == "z"                            # non-harga utuh


# ── Penjaga terpusat di _run_tool ────────────────────────────────────────────
def test_run_tool_strip_harga_utk_staf_tanpa_izin(perms, monkeypatch):
    monkeypatch.setattr(ai, "_DISPATCH", {"dummy": lambda a, u: {
        "found": True, "parts": [{"pn": "X", "harga_lokal": "Rp 100", "stok_total": 5}]}})
    monkeypatch.setattr(ai, "_allowed_tool_names", lambda u, sid="": {"dummy"})
    out = ai._run_tool("dummy", {}, RIZAL)
    assert "harga_lokal" not in out["parts"][0]        # ⛔ tak bocor
    assert out["parts"][0]["stok_total"] == 5          # stok tetap


def test_run_tool_pertahankan_harga_utk_yang_berhak(perms, monkeypatch):
    monkeypatch.setattr(ai, "_DISPATCH", {"dummy": lambda a, u: {
        "parts": [{"pn": "X", "harga_lokal": "Rp 100"}]}})
    monkeypatch.setattr(ai, "_allowed_tool_names", lambda u, sid="": {"dummy"})
    assert ai._run_tool("dummy", {}, AGUS)["parts"][0]["harga_lokal"] == "Rp 100"
    assert ai._run_tool("dummy", {}, ADMIN)["parts"][0]["harga_lokal"] == "Rp 100"


def test_pembeli_tetap_lihat_harga_jual(perms, monkeypatch):
    """Pembeli membayar harga jual — tampil di /toko & di asisten (bukan rahasia)."""
    monkeypatch.setattr(ai, "_DISPATCH", {"dummy": lambda a, u: {"parts": [{"harga_lokal": "Rp 5"}]}})
    monkeypatch.setattr(ai, "_allowed_tool_names", lambda u, sid="": {"dummy"})
    assert ai._run_tool("dummy", {}, PEMBELI)["parts"][0]["harga_lokal"] == "Rp 5"
