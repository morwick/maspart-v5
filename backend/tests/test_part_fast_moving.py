"""Tool part_fast_moving: daftar part aus per MODEL dari dataset fast_moving.

Invarian: label pasaran terurai (NX400 = NX + 400 HP), ambigu → kandidat +
instruksi tanya (bukan memilih diam-diam), tak ketemu → daftar 'tersedia'
(bukan mengarang), ko_eksis & porsi n_unit diteruskan, harga tergerbang
_boleh_harga, stok disilang part_index. Semua sumber di-mock (offline).
"""
import pytest

from app.services import ai_assistant as ai
from app.services import fast_moving

ADMIN = {"username": "admin", "role": "admin"}

_DATA = {"model": {
    "ZZ3257V404JF1": {
        "jenis": "HOWO-NX 6X4", "hp": 400, "unit_populasi": 51,
        "unit_sampel": ["SJ000001", "SJ000002", "SJ000003"], "n_sampel": 3,
        "slot": [
            {"kategori": "filter", "slot": "oil filter", "varian": [
                {"pn": "VG61000070005", "nama": "Oil filter", "qty": 1,
                 "n_unit": 3, "tahun": ["2023", "2024"], "pn_sub": [],
                 "pengganti": []}]},
            {"kategori": "rem", "slot": "brake shoe — drum brake",
             "ko_eksis": True, "varian": [
                {"pn": "AZ450045001160", "nama": "Brake shoe", "qty": 2,
                 "n_unit": 3, "tahun": [], "pn_sub": [], "pengganti": []},
                {"pn": "AZ450045001161", "nama": "Brake shoe", "qty": 2,
                 "n_unit": 3, "tahun": [], "pn_sub": [], "pengganti": []}]},
            {"kategori": "karet", "slot": "suspension rubber mount", "varian": [
                {"pn": "AZ9T3152200011", "nama": "Suspension rubber mount",
                 "qty": 4, "n_unit": 2, "tahun": ["2024"], "pn_sub": [],
                 "pengganti": []},
                {"pn": "AZ9725520683", "nama": "Suspension rubber mount",
                 "qty": 4, "n_unit": 1, "tahun": ["2022"], "pn_sub": [],
                 "pengganti": ["AZ9725520683TP0003"]}]},
        ]},
    "ZZ3312V404JB1": {
        "jenis": "HOWO-NX 8X4", "hp": 400, "unit_populasi": 12,
        "unit_sampel": ["SJ000009"], "n_sampel": 1, "slot": []},
    "ZZ4256V324HF1B": {
        "jenis": "SITRAK C7H 6X4", "hp": 320, "unit_populasi": 30,
        "unit_sampel": ["SJ000005"], "n_sampel": 1, "slot": []},
}}

_LOKAL = {"VG61000070005": {"part_name": "Oil Filter", "stok": 120,
                            "harga": "Rp 55.000", "gudang": {"01.Jakarta": 100}}}


@pytest.fixture
def patched(monkeypatch):
    monkeypatch.setattr(fast_moving, "data", lambda: dict(_DATA))
    monkeypatch.setattr(ai.part_index, "rows_for_pns",
                        lambda pns: {p: dict(_LOKAL[p]) for p in pns if p in _LOKAL})
    monkeypatch.setattr(ai, "_boleh_harga", lambda u: True)


def test_fm_match_variasi_label():
    m64 = _DATA["model"]["ZZ3257V404JF1"]
    assert ai._fm_match("NX400", "ZZ3257V404JF1", m64)
    assert ai._fm_match("nx 400", "ZZ3257V404JF1", m64)          # _fm_norm meng-upper
    assert ai._fm_match("HOWO NX 6X4", "ZZ3257V404JF1", m64)
    assert ai._fm_match("ZZ3257V404JF1", "ZZ3257V404JF1", m64)
    assert not ai._fm_match("NX460", "ZZ3257V404JF1", m64)       # HP beda
    m_c7h = _DATA["model"]["ZZ4256V324HF1B"]
    assert ai._fm_match("SITRAK C7H", "ZZ4256V324HF1B", m_c7h)
    assert not ai._fm_match("SITRAK C9H", "ZZ4256V324HF1B", m_c7h)


def test_nx400_ambigu_tawarkan_kandidat(patched):
    r = ai._t_part_fast_moving({"model": "NX400"}, ADMIN)
    assert r["found"] and r["ambigu"]
    # urut populasi terbanyak; tak memilih diam-diam
    assert [k["model"] for k in r["kandidat"]] == ["ZZ3257V404JF1", "ZZ3312V404JB1"]
    assert "TANYAKAN" in r["catatan"]


def test_detail_model_lengkap(patched):
    r = ai._t_part_fast_moving({"model": "NX400 6X4"}, ADMIN)
    assert r["found"] and r["model"] == "ZZ3257V404JF1" and r["hp"] == 400
    assert r["unit_sampel_epc"] == 3 and r["unit_populasi"] == 51
    slots = {s["slot"]: s for s in r["slot"]}
    # porsi + stok + harga tersilang
    of = slots["oil filter"]["varian"][0]
    assert of["dipakai_di_unit"] == "3/3" and of["stok_total"] == 120
    assert of["harga"] == "Rp 55.000"
    # ko_eksis diteruskan
    assert slots["brake shoe — drum brake"]["ko_eksis"] is True
    # varian terbelah bawa tahun + pengganti; PN di luar inventori ditandai
    rm = slots["suspension rubber mount"]["varian"]
    assert [v["dipakai_di_unit"] for v in rm] == ["2/3", "1/3"]
    assert rm[0]["tahun_unit"] == ["2024"]
    assert rm[1]["pengganti"] == ["AZ9725520683TP0003"]
    assert rm[1]["ada_di_inventori"] is False


def test_harga_tergerbang(patched, monkeypatch):
    monkeypatch.setattr(ai, "_boleh_harga", lambda u: False)
    r = ai._t_part_fast_moving({"model": "ZZ3257V404JF1"}, ADMIN)
    of = [s for s in r["slot"] if s["slot"] == "oil filter"][0]["varian"][0]
    assert of["stok_total"] == 120 and "harga" not in of


def test_filter_kategori(patched):
    r = ai._t_part_fast_moving({"model": "ZZ3257V404JF1", "kategori": "rem"}, ADMIN)
    assert [s["kategori"] for s in r["slot"]] == ["rem"]
    assert r["kategori_difilter"] == "rem"


def test_tak_ketemu_daftarkan_tersedia(patched):
    r = ai._t_part_fast_moving({"model": "NX999"}, ADMIN)
    assert r["found"] is False
    jenis = {t["jenis"] for t in r["tersedia"]}
    assert "HOWO-NX 6X4" in jenis and "SITRAK C7H 6X4" in jenis
    assert "JANGAN mengarang" in r["catatan"]


def test_dataset_kosong(monkeypatch):
    monkeypatch.setattr(fast_moving, "data", lambda: {})
    r = ai._t_part_fast_moving({"model": "NX400"}, ADMIN)
    assert "belum terbangun" in r["error"]
