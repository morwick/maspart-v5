"""Banding part antar unit SATU CUSTOMER (banding_part_armada) — logika murni
tanpa network: pencocokan customer di populasi (units_for_customer), pemilihan
kolom konfigurasi per domain part, pengelompokan unit per konfigurasi EPC
(epc.lookup di-mock), cek part per unit wakil (_t_part_aus_dari_rangka di-mock),
dan verdict SAMA/BEDA yang dihitung sistem.
"""
import pandas as pd
import pytest

from app.services import ai_assistant as ai
from app.services import populasi

ADMIN = {"username": "tester", "role": "admin"}
BIASA = {"username": "budi", "role": "user"}


@pytest.fixture
def pop_df(monkeypatch):
    df = pd.DataFrame([
        {"NO": "1", "CUSTOMER": "PT.ARGCIO JAYA", "MODEL": "ZZ4255N3446E1X",
         "JENIS": "HOWO NX 6X4", "TIPE UNIT": "TRACTOR HEAD", "TAHUN": "2022",
         "Euro": "Euro V", "NOMOR RANGKA": "LZZPCLSC5NJ250001"},
        {"NO": "2", "CUSTOMER": "PT ARGCIO JAYA ", "MODEL": "ZZ4255N3446E1X",
         "JENIS": "HOWO NX 6X4", "TIPE UNIT": "TRACTOR HEAD", "TAHUN": "2023",
         "Euro": "Euro V", "NOMOR RANGKA": "LZZPCLSC5NJ250002"},
        {"NO": "3", "CUSTOMER": "PT.ARGCIO JAYA", "MODEL": "ZZ1315N4666E1",
         "JENIS": "HOWO NX 8x4", "TIPE UNIT": "CARGO", "TAHUN": "2022",
         "Euro": "Euro V", "NOMOR RANGKA": "LZZPBXSF5NJ250003"},
        {"NO": "4", "CUSTOMER": "PT LAIN SENTOSA", "MODEL": "ZZ4256V395MF1",
         "JENIS": "SITRAK C7H", "TIPE UNIT": "TRACTOR HEAD", "TAHUN": "2022",
         "Euro": "Euro V", "NOMOR RANGKA": "LZZ8CVWD9NB250004"},
    ])
    monkeypatch.setitem(populasi._state, "df", df)
    return df


# ── populasi.units_for_customer ──────────────────────────────────────────────
def test_units_for_customer_normalisasi_tanda_baca(pop_df):
    # 'PT ARGCIO' harus kena baik 'PT.ARGCIO JAYA' maupun 'PT ARGCIO JAYA '.
    r = populasi.units_for_customer("PT ARGCIO")
    assert r["jumlah_unit"] == 3
    assert {u["rangka"] for u in r["units"]} == {
        "LZZPCLSC5NJ250001", "LZZPCLSC5NJ250002", "LZZPBXSF5NJ250003"}


def test_units_for_customer_kata_pt_tak_wajib(pop_df):
    assert populasi.units_for_customer("argcio jaya")["jumlah_unit"] == 3


def test_units_for_customer_nihil_beri_kandidat(pop_df):
    # Sebagian kata cocok ('ARGCIO') tapi tidak semua → nihil + kandidat ejaan.
    r = populasi.units_for_customer("PT ARGCIO SENTOSA")
    assert r["jumlah_unit"] == 0 and not r["units"]
    assert any("ARGCIO" in k for k in r["kandidat"])


# ── pemilihan kolom konfigurasi per domain ───────────────────────────────────
def test_banding_armada_akses_ditolak_user_biasa(pop_df):
    r = ai._t_banding_part_armada({"customer": "PT ARGCIO", "part": "kampas kopling"}, BIASA)
    assert r.get("denied")


def _mock_epc_lookup(monkeypatch, cfg_by_rangka: dict):
    def fake_lookup(rangka: str) -> dict:
        d = cfg_by_rangka.get(rangka)
        return {"found": True, **d} if d else {"found": False}
    monkeypatch.setattr(ai.epc, "lookup", fake_lookup)


def _mock_part_aus(monkeypatch, pns_by_rangka: dict):
    def fake_aus(args: dict, user: dict) -> dict:
        pns = pns_by_rangka.get(args.get("rangka"))
        if pns is None:
            return {"found": False, "error": "EPC gagal"}
        return {"found": True,
                "parts": [{"part_number": pn, "nama": f"Part {pn}"} for pn in pns]}
    monkeypatch.setattr(ai, "_t_part_aus_dari_rangka", fake_aus)


def test_banding_armada_sama_semua(pop_df, monkeypatch):
    # Semua unit ARGCIO: engine+gearbox identik → 1 kelompok → verdict SAMA.
    cfg = {"engine": "MC11.36-50", "gearbox": "HW25712XST"}
    _mock_epc_lookup(monkeypatch, {
        "LZZPCLSC5NJ250001": cfg, "LZZPCLSC5NJ250002": cfg, "LZZPBXSF5NJ250003": cfg})
    _mock_part_aus(monkeypatch, {"LZZPCLSC5NJ250001": ["WG9114160020"]})
    r = ai._t_banding_part_armada({"customer": "PT ARGCIO", "part": "kampas kopling"}, ADMIN)
    assert r["found"] and r["jumlah_kelompok_konfigurasi"] == 1
    assert r["perbandingan"]["sama_semua"] is True
    # kopling → kolom penentu engine+gearbox (bukan poros)
    assert "engine" in r["dasar_pengelompokan"] and "gearbox" in r["dasar_pengelompokan"]


def test_banding_armada_beda_kelompok(pop_df, monkeypatch):
    # Unit ke-3 gearbox beda → 2 kelompok, PN beda → verdict BEDA + irisan PN.
    a = {"engine": "MC11.36-50", "gearbox": "HW25712XST"}
    b = {"engine": "MC11.36-50", "gearbox": "HW19712XS"}
    _mock_epc_lookup(monkeypatch, {
        "LZZPCLSC5NJ250001": a, "LZZPCLSC5NJ250002": a, "LZZPBXSF5NJ250003": b})
    _mock_part_aus(monkeypatch, {
        "LZZPCLSC5NJ250001": ["WG9114160020", "WG9725160390"],
        "LZZPBXSF5NJ250003": ["WG9114160021", "WG9725160390"]})
    r = ai._t_banding_part_armada({"customer": "PT ARGCIO", "part": "kampas kopling"}, ADMIN)
    assert r["jumlah_kelompok_konfigurasi"] == 2
    assert r["perbandingan"]["sama_semua"] is False
    assert r["perbandingan"]["pn_sama_semua_kelompok"] == ["WG9725160390"]
    # Kelompok terbesar (2 unit) diurutkan lebih dulu.
    assert r["kelompok"][0]["jumlah_unit"] == 2


def test_banding_armada_rangka_tak_dikenal_epc_tidak_disimpulkan(pop_df, monkeypatch):
    # Satu unit tak dikenali EPC → walau kelompok tercek identik, verdict None
    # (jangan klaim pasti semua sama).
    cfg = {"engine": "MC11.36-50", "gearbox": "HW25712XST"}
    _mock_epc_lookup(monkeypatch, {
        "LZZPCLSC5NJ250001": cfg, "LZZPCLSC5NJ250002": cfg})  # unit ke-3 miss
    _mock_part_aus(monkeypatch, {"LZZPCLSC5NJ250001": ["WG9114160020"]})
    r = ai._t_banding_part_armada({"customer": "PT ARGCIO", "part": "kampas kopling"}, ADMIN)
    assert r["perbandingan"]["sama_semua"] is None
    assert r["jumlah_tak_dikenal_epc"] == 1


def test_banding_armada_customer_tak_ada_beri_kandidat(pop_df):
    r = ai._t_banding_part_armada({"customer": "PT ARGCIO SENTOSA", "part": "kampas kopling"}, ADMIN)
    assert r["found"] is False
    assert any("ARGCIO" in k for k in r["kandidat_customer"])


def test_banding_armada_cek_part_gagal_belum_tuntas(pop_df, monkeypatch):
    cfg = {"engine": "MC11.36-50", "gearbox": "HW25712XST"}
    _mock_epc_lookup(monkeypatch, {
        "LZZPCLSC5NJ250001": cfg, "LZZPCLSC5NJ250002": cfg, "LZZPBXSF5NJ250003": cfg})
    _mock_part_aus(monkeypatch, {})  # semua rep gagal
    r = ai._t_banding_part_armada({"customer": "PT ARGCIO", "part": "kampas kopling"}, ADMIN)
    assert r["perbandingan"]["sama_semua"] is None
    assert "error_cek_part" in r["kelompok"][0]
