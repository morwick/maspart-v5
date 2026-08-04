"""Builder fast_moving (dataset part aus per kode MODEL dari cache EPC).

Invarian: offline murni (disk saja), varian intra-model tampil semua dengan
porsi n_unit, build parsial & unit di luar populasi tak meracuni data, kamus
negatif menyaring hardware pendukung, HP terparse dari kode model.
"""
import json

import pandas as pd
import pytest

from app.services import fast_moving


@pytest.fixture
def dunia(tmp_path, monkeypatch):
    class _S:
        data_path = tmp_path

    monkeypatch.setattr(fast_moving, "get_settings", lambda: _S())
    from app.services import knowledge_util
    knowledge_util._LOAD_CACHE.clear()

    df = pd.DataFrame([
        {"NOMOR RANGKA": "LZZ111111PJ295852", "MODEL": "ZZ3257V404JF1",
         "JENIS": "HOWO NX 6X4", "TAHUN": "2023"},
        {"NOMOR RANGKA": "LZZ111111PJ295853", "MODEL": "ZZ3257V404JF1",
         "JENIS": "HOWO NX 6X4", "TAHUN": "2024"},
        {"NOMOR RANGKA": "LZZ111111PJ295854", "MODEL": "ZZ3257V404JF1",
         "JENIS": "HOWO NX 6X4", "TAHUN": "2024"},   # tanpa cache (populasi saja)
    ])
    from app.services import populasi
    monkeypatch.setattr(populasi, "_ensure", lambda: df)
    return tmp_path


def _tulis_unit(tmp_path, frame, rows, incomplete=False):
    d = tmp_path / "epc_unit_items"
    d.mkdir(exist_ok=True)
    (d / f"{frame}.json").write_text(
        json.dumps({"ts": 1, "rows": rows, "incomplete": incomplete}),
        encoding="utf-8")


_F1 = [
    {"pn": "VG61000070005", "nama": "Oil filter assembly", "nama_cn": "机油滤清器",
     "qty": 1, "pasok": None, "pengganti": []},
    {"pn": "WG9725520278", "nama": "Rubber support assembly", "nama_cn": "橡胶支座",
     "qty": 4, "pasok": "stop", "pengganti": [{"pn": "WG9725520683"}]},
    {"pn": "AZ9100443050", "nama": "Filter bracket", "nama_cn": "滤清器支架",
     "qty": 1, "pasok": None, "pengganti": []},          # negatif: bracket
    {"pn": "WG9000360600", "nama": "Air pipe", "nama_cn": "气管",
     "qty": 2, "pasok": None, "pengganti": []},          # bukan kategori
]
_F2 = [
    {"pn": "VG61000070005A", "nama": "Oil filter assembly", "nama_cn": "机油滤清器",
     "qty": 1, "pasok": None, "pengganti": []},          # varian beda unit
    {"pn": "WG9725520278", "nama": "Rubber support assembly", "nama_cn": "橡胶支座",
     "qty": 4, "pasok": None, "pengganti": []},
]


def test_build_konsensus_varian(dunia):
    _tulis_unit(dunia, "PJ295852", _F1)
    _tulis_unit(dunia, "PJ295853", _F2)
    r = fast_moving.build()
    assert r["model_n"] == 1 and r["unit_dipakai"] == 2

    d = fast_moving.data()["model"]["ZZ3257V404JF1"]
    assert d["jenis"] == "HOWO NX 6X4" and d["hp"] == 400
    assert d["n_sampel"] == 2 and d["unit_populasi"] == 3

    slots = {(s["kategori"], s["slot"]): s for s in d["slot"]}
    # bracket & pipe tersaring
    assert all("bracket" not in k[1] and "pipe" not in k[1] for k in slots)
    # slot oil filter: DUA varian dgn porsi masing-masing (assembly dibuang dari slot)
    of = slots[("filter", "oil filter")]
    assert [(v["pn"], v["n_unit"]) for v in of["varian"]] == [
        ("VG61000070005", 1), ("VG61000070005A", 1)]
    # rubber support: 1 PN di 2 unit, tahun terkumpul, pengganti terbawa;
    # flag pasok TIDAK dibawa (marketability tak bisa dipercaya — lihat modul)
    rs = slots[("karet", "rubber support")]
    v = rs["varian"][0]
    assert v["n_unit"] == 2 and v["tahun"] == ["2023", "2024"]
    assert v["pengganti"] == ["WG9725520683"] and "pasok" not in v


def test_parsial_dan_luar_populasi_tak_dihitung(dunia):
    _tulis_unit(dunia, "PJ295852", _F1)
    _tulis_unit(dunia, "PJ295853", _F2, incomplete=True)   # bolong → skip
    _tulis_unit(dunia, "XX999999", _F2)                    # di luar populasi
    r = fast_moving.build()
    assert r["unit_dipakai"] == 1 and r["unit_tanpa_populasi"] == 1
    d = fast_moving.data()["model"]["ZZ3257V404JF1"]
    assert d["n_sampel"] == 1


def test_suffix_pn_dilebur_ke_pn_dasar(dunia):
    """WG.../1 di unit A dan WG.../2 di unit B = part SAMA → satu varian
    ber-PN dasar dgn n_unit 2 (bukan dua varian palsu)."""
    _tulis_unit(dunia, "PJ295852", [
        {"pn": "WG9525195010/1", "nama": "Air filter assembly",
         "nama_cn": "空气滤清器", "qty": 1, "pengganti": []}])
    _tulis_unit(dunia, "PJ295853", [
        {"pn": "WG9525195010/2", "nama": "Air filter assembly",
         "nama_cn": "空气滤清器", "qty": 1, "pengganti": []}])
    fast_moving.build()
    d = fast_moving.data()["model"]["ZZ3257V404JF1"]
    af = [s for s in d["slot"] if s["slot"] == "air filter"][0]
    assert len(af["varian"]) == 1
    v = af["varian"][0]
    assert v["pn"] == "WG9525195010" and v["n_unit"] == 2
    assert v["pn_sub"] == ["WG9525195010/1", "WG9525195010/2"]


def test_nama_generik_dipecah_per_assembly(dunia):
    """SATU unit memuat 2 PN 'oil seal' berbeda = dua POSISI (bukan varian) →
    slot dipecah per assembly induk agar porsi n_unit jujur."""
    rows = [
        {"pn": "WG7117329002", "nama": "Oil seal", "nama_cn": "油封", "qty": 1,
         "pengganti": [], "dari_assembly": {"pn": "A1", "nama": "Gearbox"}},
        {"pn": "AZ4071410051", "nama": "Oil seal", "nama_cn": "油封", "qty": 4,
         "pengganti": [], "dari_assembly": {"pn": "A2", "nama": "Wheel hub"}},
    ]
    _tulis_unit(dunia, "PJ295852", rows)
    _tulis_unit(dunia, "PJ295853", rows)
    fast_moving.build()
    d = fast_moving.data()["model"]["ZZ3257V404JF1"]
    nama_slot = sorted(s["slot"] for s in d["slot"])
    assert nama_slot == ["oil seal — gearbox", "oil seal — wheel hub"]
    for s in d["slot"]:
        assert len(s["varian"]) == 1 and s["varian"][0]["n_unit"] == 2


def test_kamus_file_menimpa_default(dunia):
    fm = dunia / "fast_moving"
    fm.mkdir()
    (fm / "kamus_kategori.json").write_text(json.dumps({
        "negatif": [], "kategori": {"pneumatik": ["air pipe"]}}), encoding="utf-8")
    _tulis_unit(dunia, "PJ295852", _F1)
    fast_moving.build()
    d = fast_moving.data()["model"]["ZZ3257V404JF1"]
    assert [s["kategori"] for s in d["slot"]] == ["pneumatik"]


def test_hp_dari_model():
    assert fast_moving.hp_dari_model("ZZ3257V404JF1") == 400
    assert fast_moving.hp_dari_model("ZZ4257V324HE1B") == 320
    assert fast_moving.hp_dari_model("ZZ3257N3847B2R") == 380
    assert fast_moving.hp_dari_model("TAZ5466TYT") is None
