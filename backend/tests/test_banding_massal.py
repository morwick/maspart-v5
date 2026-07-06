"""Banding BANYAK unit sekaligus (banding_rangka_massal) — logika murni tanpa
network. Loading List per-VIN (epc_bom.loading_list) di-mock; kategorisasi PN
(catalog_bom.pn_category_map) di-mock; pengelompokan unit ber-set-PN identik &
verdict SERAGAM/BEDA dihitung sistem; mode daftar-VIN vs customer (gated).
"""
import pandas as pd
import pytest

from app.services import ai_assistant as ai
from app.services import catalog_bom, populasi

ADMIN = {"username": "tester", "role": "admin"}
BIASA = {"username": "budi", "role": "user"}


# ── mock helpers ─────────────────────────────────────────────────────────────
def _mock_loading(monkeypatch, pns_by_rangka: dict):
    """pns_by_rangka: {rangka: [pn,...]} → Loading List sukses; nilai None = gagal."""
    def fake_ll(rangka: str) -> dict:
        pns = pns_by_rangka.get(rangka)
        if pns is None:
            return {"found": False, "frame_number": rangka, "_err": "not_found"}
        return {"found": True, "frame_number": rangka, "jumlah_part": len(pns),
                "parts": [{"pn": pn, "qty": 1, "nama_cn": f"零件{pn}"} for pn in pns]}
    monkeypatch.setattr(ai.epc_bom, "loading_list", fake_ll)


def _mock_catalog(monkeypatch, cat_by_pn: dict):
    """cat_by_pn: {pn: '01'|'09'|...}. Sisanya '00' (tak terkategori)."""
    pncat = {catalog_bom._norm(pn): {"kategori": c} for pn, c in cat_by_pn.items()}
    monkeypatch.setattr(ai.catalog_bom, "available", lambda: True)
    monkeypatch.setattr(ai.catalog_bom, "pn_category_map", lambda: pncat)

    def fake_resolve(q: str):
        ql = (q or "").lower()
        return ("01" if "kabin" in ql else "09" if "rem" in ql
                else "02" if "mesin" in ql else None)
    monkeypatch.setattr(ai.catalog_bom, "resolve_kategori", fake_resolve)
    monkeypatch.setattr(ai.part_index, "search_exact_pns",
                        lambda pns: [{"part_number": p, "part_name": f"Name {p}"} for p in pns])
    monkeypatch.setattr(ai.epc_bom, "translate_cn", lambda cn: None)


# ── mode DAFTAR VIN ──────────────────────────────────────────────────────────
def test_massal_butuh_minimal_2(monkeypatch):
    _mock_catalog(monkeypatch, {})
    r = ai._t_banding_rangka_massal({"rangka_list": ["R1"], "kategori": "kabin"}, BIASA)
    assert "error" in r and "MINIMAL 2" in r["error"].upper() or "minimal 2" in r["error"].lower()


def test_massal_tanpa_input(monkeypatch):
    _mock_catalog(monkeypatch, {})
    r = ai._t_banding_rangka_massal({"kategori": "kabin"}, BIASA)
    assert "error" in r


def test_massal_satu_kategori_seragam(monkeypatch):
    _mock_loading(monkeypatch, {
        "R1": ["A", "B", "X"], "R2": ["A", "B", "Y"], "R3": ["A", "B", "Z"]})
    _mock_catalog(monkeypatch, {"A": "01", "B": "01", "X": "09", "Y": "09", "Z": "09"})
    r = ai._t_banding_rangka_massal({"rangka_list": ["R1", "R2", "R3"], "kategori": "kabin"}, BIASA)
    assert r["found"] and r["mode"] == "satu_kategori"
    assert r["seragam"] is True and r["jumlah_kelompok"] == 1
    assert r["jumlah_unit_dibanding"] == 3
    assert r["export_id"] and r["jumlah_baris"] == 2  # kabin = 2 PN (A,B)


def test_massal_satu_kategori_beda(monkeypatch):
    # R3 kabin = {A,C} (bukan {A,B}) → 2 kelompok; part beda = B,C.
    _mock_loading(monkeypatch, {
        "R1": ["A", "B"], "R2": ["A", "B"], "R3": ["A", "C"]})
    _mock_catalog(monkeypatch, {"A": "01", "B": "01", "C": "01"})
    r = ai._t_banding_rangka_massal({"rangka_list": ["R1", "R2", "R3"], "kategori": "kabin"}, BIASA)
    assert r["seragam"] is False and r["jumlah_kelompok"] == 2
    beda = {p["part_number"] for p in r["part_beda"]}
    assert beda == {"B", "C"}
    assert r["kelompok"][0]["jumlah_unit"] == 2  # kelompok terbesar dulu


def test_massal_dedup_vin(monkeypatch):
    _mock_loading(monkeypatch, {"R1": ["A"], "R2": ["A"]})
    _mock_catalog(monkeypatch, {"A": "01"})
    r = ai._t_banding_rangka_massal({"rangka_list": ["R1", "r1", "R2"], "kategori": "kabin"}, BIASA)
    assert r["jumlah_unit_dibanding"] == 2  # R1 & r1 dianggap sama


def test_massal_string_dipisah_koma(monkeypatch):
    _mock_loading(monkeypatch, {"R1": ["A"], "R2": ["A"]})
    _mock_catalog(monkeypatch, {"A": "01"})
    r = ai._t_banding_rangka_massal({"rangka_list": "R1, R2", "kategori": "kabin"}, BIASA)
    assert r["found"] and r["jumlah_unit_dibanding"] == 2


def test_massal_unit_gagal_dikecualikan(monkeypatch):
    _mock_loading(monkeypatch, {"R1": ["A", "B"], "R2": ["A", "B"], "R3": None})
    _mock_catalog(monkeypatch, {"A": "01", "B": "01"})
    r = ai._t_banding_rangka_massal({"rangka_list": ["R1", "R2", "R3"], "kategori": "kabin"}, BIASA)
    assert r["found"] and r["jumlah_unit_dibanding"] == 2
    assert any(g["rangka"] == "R3" for g in r["unit_gagal"])


def test_massal_kurang_dari_2_sukses_error(monkeypatch):
    _mock_loading(monkeypatch, {"R1": ["A"], "R2": None, "R3": None})
    _mock_catalog(monkeypatch, {"A": "01"})
    r = ai._t_banding_rangka_massal({"rangka_list": ["R1", "R2", "R3"], "kategori": "kabin"}, BIASA)
    assert r["found"] is False and "Kurang dari 2" in r["error"]


def test_massal_kategori_tak_dikenal(monkeypatch):
    _mock_loading(monkeypatch, {"R1": ["A"], "R2": ["A"]})
    _mock_catalog(monkeypatch, {"A": "01"})
    r = ai._t_banding_rangka_massal({"rangka_list": ["R1", "R2"], "kategori": "zzz"}, BIASA)
    assert r["found"] is False and "tak dikenal" in r["error"]


# ── mode SEMUA kategori ──────────────────────────────────────────────────────
def test_massal_semua_kategori_ringkasan(monkeypatch):
    # Kabin (01) sama semua; rem (09) R3 beda → kategori_beda memuat rem.
    _mock_loading(monkeypatch, {
        "R1": ["A", "B", "P", "Q"], "R2": ["A", "B", "P", "Q"], "R3": ["A", "B", "P", "R"]})
    _mock_catalog(monkeypatch, {"A": "01", "B": "01", "P": "09", "Q": "09", "R": "09"})
    r = ai._t_banding_rangka_massal({"rangka_list": ["R1", "R2", "R3"], "kategori": "semua"}, BIASA)
    assert r["found"] and r["mode"] == "semua_kategori"
    assert r["seragam_semua"] is False
    beda = {x["kategori_kode"] for x in r["kategori_beda"]}
    seragam = {x["kategori_kode"] for x in r["kategori_seragam"]}
    assert "09" in beda and "01" in seragam
    assert r["export_id"] and r["jumlah_baris"] == 3  # matriks: 1 baris per unit


def test_massal_kategori_kosong_default_semua(monkeypatch):
    _mock_loading(monkeypatch, {"R1": ["A"], "R2": ["A"]})
    _mock_catalog(monkeypatch, {"A": "01"})
    r = ai._t_banding_rangka_massal({"rangka_list": ["R1", "R2"]}, BIASA)
    assert r["found"] and r["mode"] == "semua_kategori"


# ── mode CUSTOMER (gated) ────────────────────────────────────────────────────
@pytest.fixture
def pop_df(monkeypatch):
    df = pd.DataFrame([
        {"CUSTOMER": "PT.ARGCIO JAYA", "MODEL": "ZZ4255N3446E1X", "TAHUN": "2022",
         "NOMOR RANGKA": "LZZ0001"},
        {"CUSTOMER": "PT ARGCIO JAYA ", "MODEL": "ZZ4255N3446E1X", "TAHUN": "2023",
         "NOMOR RANGKA": "LZZ0002"},
        {"CUSTOMER": "PT.ARGCIO JAYA", "MODEL": "ZZ1315N4666E1", "TAHUN": "2022",
         "NOMOR RANGKA": "LZZ0003"},
    ])
    monkeypatch.setitem(populasi._state, "df", df)
    return df


def test_massal_customer_user_biasa_ditolak(pop_df, monkeypatch):
    _mock_catalog(monkeypatch, {})
    r = ai._t_banding_rangka_massal({"customer": "PT ARGCIO", "kategori": "kabin"}, BIASA)
    assert r.get("denied")


def test_massal_customer_admin(pop_df, monkeypatch):
    _mock_loading(monkeypatch, {
        "LZZ0001": ["A", "B"], "LZZ0002": ["A", "B"], "LZZ0003": ["A", "B"]})
    _mock_catalog(monkeypatch, {"A": "01", "B": "01"})
    r = ai._t_banding_rangka_massal({"customer": "PT ARGCIO", "kategori": "kabin"}, ADMIN)
    assert r["found"] and r["jumlah_unit_dibanding"] == 3
    assert r["seragam"] is True
    assert r["customer_cocok"]


def test_massal_customer_tak_ada_beri_kandidat(pop_df, monkeypatch):
    _mock_catalog(monkeypatch, {})
    r = ai._t_banding_rangka_massal({"customer": "PT ARGCIO SENTOSA", "kategori": "kabin"}, ADMIN)
    assert r["found"] is False
    assert any("ARGCIO" in k for k in r["kandidat_customer"])
