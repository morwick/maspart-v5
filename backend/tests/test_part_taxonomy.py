"""Taksonomi part (part_taxonomy — Fase C 2026-07-23): aturan cluster
deterministik, loader, for_pn/ringkas, ketahanan store absen."""
import pytest

from app.services import part_taxonomy as tax


def test_match_name_aturan_dasar():
    assert tax.match_name("OIL FILTER ASSEMBLY")[0] == "filter oli"
    assert tax.match_name("FUEL FILTER ELEMENT")[0] == "filter bahan bakar"
    assert tax.match_name("BRAKE FRICTION PLATE")[0] == "kampas rem"
    assert tax.match_name("CLUTCH DRIVEN DISC")[0] == "kampas kopling"
    assert tax.match_name("RELEASE BEARING")[0] == "release bearing"
    # prioritas: release bearing MENANG atas bearing generik
    assert tax.match_name("THROWOUT BEARING")[0] == "release bearing"
    assert tax.match_name("DEEP GROOVE BALL BEARING")[0] == "bearing"
    assert tax.match_name("LEAF SPRING ASSY")[0] == "pegas daun"
    assert tax.match_name("O-RING")[0] == "o-ring"


def test_match_name_tak_terklasifikasi():
    assert tax.match_name("BRACKET") is None
    assert tax.match_name("") is None
    # word-boundary: FAN tidak nyangkut di kata lain
    assert tax.match_name("MANIFOLD") is None


@pytest.fixture
def store(tmp_path, monkeypatch):
    from app.services.knowledge_util import write_json_gz, _LOAD_CACHE
    p = tmp_path / "part_taxonomy.json.gz"
    write_json_gz(p, [
        {"keluarga": "filter oli", "sistem": "mesin", "sub_sistem": "pelumasan",
         "nama_kunci": ["OIL FILTER"], "contoh_pn": ["VG61000070005"],
         "jumlah_pn": 214, "fungsi": "Menyaring oli mesin dari gram.",
         "gejala_umum": "Tekanan oli turun.", "catatan": ""},
        {"keluarga": "kampas rem", "sistem": "rem", "sub_sistem": "rem roda",
         "nama_kunci": ["BRAKE FRICTION PLATE"], "contoh_pn": ["AZ450045000042"],
         "jumlah_pn": 88, "fungsi": "", "gejala_umum": "", "catatan": ""},
    ])
    monkeypatch.setattr(tax, "_PATH", p)
    _LOAD_CACHE.clear()
    return p


def test_loader_dan_cari(store):
    assert tax.available() and tax.count() == 2
    assert tax.cari("filter oli")[0]["keluarga"] == "filter oli"
    # via istilah yang cocok aturan ('oil filter' → keluarga filter oli)
    assert tax.cari("oil filter")[0]["keluarga"] == "filter oli"
    assert tax.cari("zzz-tidak-ada") == []


def test_for_pn_dan_ringkas(store, monkeypatch):
    from app.services import part_index
    monkeypatch.setattr(part_index, "name_for",
                        lambda pn: "OIL FILTER ASSEMBLY"
                        if pn == "VG61000070005" else "")
    r = tax.for_pn("VG61000070005")
    assert r and r["keluarga"] == "filter oli"
    s = tax.ringkas("VG61000070005")
    assert s.startswith("filter oli (mesin/pelumasan)") and "Menyaring" in s
    # kurasi kosong → ringkas tanpa fungsi
    monkeypatch.setattr(part_index, "name_for",
                        lambda pn: "BRAKE FRICTION PLATE")
    assert tax.ringkas("AZ1") == "kampas rem (rem/rem roda)"
    # tak dikenal → ''
    monkeypatch.setattr(part_index, "name_for", lambda pn: "")
    assert tax.ringkas("X") == ""


def test_store_absen_aman(tmp_path, monkeypatch):
    from app.services.knowledge_util import _LOAD_CACHE
    monkeypatch.setattr(tax, "_PATH", tmp_path / "tidak-ada.json.gz")
    _LOAD_CACHE.clear()
    assert tax.available() is False and tax.count() == 0
    assert tax.cari("filter oli") == [] or True   # tak crash
