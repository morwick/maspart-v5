"""Catalog BOM (§3.5.5b) — resolve kategori/unit, verdict Jaccard, banding unit.

Data di-mock (monkeypatch _load) agar test murni & cepat, tidak bergantung
data/catalog_bom.json 7.5MB.
"""
import pytest

from app.services import catalog_bom as cb

FIXTURE = {
    "kategori": {
        "01": "Kabin (Driver's cab)", "02": "Mesin / Powertrain",
        "04": "Kopling (Clutch)", "05": "Transmisi (Gearbox)",
        "06": "Gardan depan", "07": "Gardan belakang",
        "08": "Kelistrikan", "09": "Rem (Brake)",
    },
    "units": {
        "NX400 6X4 (LZZ5EXSF)": {"file": "x.xlsx", "kategori": {
            "09": {"assy_pn": None, "jumlah": 3, "parts": [
                {"pn": "WG9000360100", "nama": "Air dryer", "qty": "1"},
                {"pn": "WG9000360520", "nama": "Relay valve", "qty": "1"},
                {"pn": "WG9925360003", "nama": "Brake chamber", "qty": "2"},
            ]},
        }},
        "V7X400 8X4 (LZZ1EXSF)": {"file": "y.xlsx", "kategori": {
            "09": {"assy_pn": None, "jumlah": 3, "parts": [
                {"pn": "WG9000360100", "nama": "Air dryer", "qty": "1"},
                {"pn": "WG9000360520", "nama": "Relay valve", "qty": "1"},
                {"pn": "AZ9925360999", "nama": "Brake chamber V7X", "qty": "2"},
            ]},
        }},
        "V7X440 8X4 (LZZ5BXVH)": {"file": "z.xlsx", "kategori": {}},
    },
}


@pytest.fixture(autouse=True)
def _mock_data(monkeypatch):
    monkeypatch.setattr(cb, "_load", lambda: FIXTURE)


# ── resolve_kategori: sinonim lapangan → kode 01..12 ────────────────────────

@pytest.mark.parametrize("query,expected", [
    ("rem", "09"),
    ("brake", "09"),
    ("kopling", "04"),
    ("gardan depan", "06"),          # frasa spesifik menang atas 'gardan' umum
    ("gardan belakang", "07"),
    ("gardan", "07"),                # gardan polos = penggerak (belakang)
    ("transmisi", "05"),
    ("girboks", "05"),
    ("kelistrikan", "08"),
    ("kabin", "01"),
    ("05", "05"),                    # kode langsung
    ("mesin", "02"),
])
def test_resolve_kategori_sinonim(query, expected):
    assert cb.resolve_kategori(query) == expected


def test_resolve_kategori_tak_dikenal():
    assert cb.resolve_kategori("kategori ngawur xyz") is None
    assert cb.resolve_kategori("") is None


# ── resolve_unit ─────────────────────────────────────────────────────────────

def test_resolve_unit_substring():
    assert cb.resolve_unit("nx400") == ["NX400 6X4 (LZZ5EXSF)"]


def test_resolve_unit_persis_menang():
    assert cb.resolve_unit("NX400 6X4 (LZZ5EXSF)") == ["NX400 6X4 (LZZ5EXSF)"]


def test_resolve_unit_ambigu_kembalikan_semua():
    assert cb.resolve_unit("v7x") == ["V7X400 8X4 (LZZ1EXSF)", "V7X440 8X4 (LZZ5BXVH)"]


def test_resolve_unit_tak_ada():
    assert cb.resolve_unit("fuso canter") == []


# ── _verdict: kalibrasi ambang Jaccard ───────────────────────────────────────

@pytest.mark.parametrize("jac,only1,only2,expected", [
    (1.00, 0, 0, "identik"),
    (0.97, 3, 2, "praktis_identik"),
    (0.80, 20, 25, "sangat_mirip"),
    (0.67, 50, 60, "mirip_satu_keluarga"),
    (0.45, 80, 90, "mirip_satu_keluarga"),   # batas bawah keluarga
    (0.116, 200, 220, "berbeda"),            # kasus nyata rem NX400 vs V7X400
])
def test_verdict_ambang(jac, only1, only2, expected):
    kode, _ = cb._verdict(jac, only1, only2)
    assert kode == expected


def test_verdict_identik_hanya_bila_nol_selisih():
    # Jaccard 1.0 tapi ada selisih (secara teori tak terjadi) tetap bukan 'identik'.
    kode, _ = cb._verdict(1.0, 1, 0)
    assert kode == "praktis_identik"


# ── _diff & compare_units end-to-end (data mock) ─────────────────────────────

def test_diff_kosong_tidak_mengklaim_identik():
    res = cb._diff({}, {}, cap=10)
    assert res["verdict"] == "tak_dapat_dibandingkan"
    assert res["persen_kesamaan"] == 0.0


def test_compare_units_rem():
    res = cb.compare_units("nx400", "v7x400", "rem")
    assert res["mode"] == "antar_unit"
    assert res["kategori"] == "09"
    assert res["jumlah_part_sama"] == 2          # air dryer + relay valve
    assert res["jumlah_hanya_di_1"] == 1 and res["jumlah_hanya_di_2"] == 1
    assert res["persen_kesamaan"] == 50.0        # 2 sama / 4 union


def test_compare_units_ambigu_minta_perjelas():
    res = cb.compare_units("v7x", "nx400", "rem")
    assert res.get("ambigu") == 1
    assert len(res["kandidat"]) == 2


def test_compare_units_kategori_tak_dikenal():
    assert "error" in cb.compare_units("nx400", "v7x400", "kategori ngawur")


# ── _norm & _pnmap ───────────────────────────────────────────────────────────

def test_norm_buang_spasi_strip_dan_uppercase():
    assert cb._norm("wg 2210-040_097/a") == "WG2210040097A"


def test_pnmap_dedup_pn_pertama_menang():
    parts = [{"pn": "A1234567", "nama": "Nama pertama"},
             {"pn": "a 1234567", "nama": "Duplikat beda format"}]
    assert cb._pnmap(parts) == {"A1234567": "Nama pertama"}
