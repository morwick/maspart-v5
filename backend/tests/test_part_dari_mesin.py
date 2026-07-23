"""Tool `part_dari_mesin` (2026-07-23): cari part di EPC Weichai LANGSUNG dari
NOMOR MESIN (serial engine), tanpa VIN. Untuk 'carikan starter untuk no engine
4P24B000713'. Jalur service (find_parts_by_no / engine_bom_by_no) di-MOCK."""
import pytest

from app.services import ai_assistant as ai

ADMIN = {"username": "admin", "role": "admin"}
STAF = {"username": "budi", "role": "user"}
PEMBELI = {"username": "roni", "role": "pembeli"}

_ENGINE = {"model": "WP4G130E22", "nomor_mesin": "4P24B000713",
           "nama": "WP4G130E22 Diesel", "order": "DHB04G0070*01"}
_FIND = {
    "found": True, "engine": _ENGINE, "jumlah_group": 20, "jumlah_part": 400,
    "cocok": 1, "hasil": [
        {"pn": "612600090401", "nama": "Starter motor", "group": "Electrical"}],
}
_BOM = {
    "found": True, "engine": _ENGINE, "jumlah_group": 2, "jumlah_part": 5,
    "groups": [{"nama": "Cylinder block", "jumlah_part": 3, "parts": []},
               {"nama": "Electrical", "jumlah_part": 2, "parts": []}],
    "_ctx": {},
}


@pytest.fixture
def dunia(monkeypatch):
    monkeypatch.setattr(ai.epc_weichai, "find_parts_by_no",
                        lambda no, terms: dict(_FIND))
    monkeypatch.setattr(ai.epc_weichai, "engine_bom_by_no",
                        lambda no: dict(_BOM))
    monkeypatch.setattr(ai.part_index, "search_exact_pns", lambda pns: [
        {"part_number": "612600090401", "part_name": "Starter motor",
         "stok": "3", "harga": "Rp 4.500.000", "gudang": {"01.Jakarta": 3}}])
    monkeypatch.setattr(ai, "_expand_query",
                        lambda q: ([q, "starter", "starting motor"], []))
    # _auto_exploded_gambar dead-code → [], [], ""
    return {}


def test_cari_starter_dari_no_mesin(dunia):
    r = ai._t_part_dari_mesin({"no_mesin": "4P24B000713", "part": "starter"}, ADMIN)
    assert r["found"] and r["jumlah_cocok"] == 1
    assert r["mesin"]["nomor_mesin"] == "4P24B000713"
    assert r["mesin"]["kode_model"] == "WP4G130E22"
    k = r["komponen"][0]
    assert k["part_number"] == "612600090401" and k["stok_total"] == "3"
    assert k["harga_lokal"] == "Rp 4.500.000"


def test_tanpa_part_daftar_group(dunia):
    r = ai._t_part_dari_mesin({"no_mesin": "4P24B000713"}, ADMIN)
    assert r["found"] and r["jumlah_group"] == 2
    assert [g["nama"] for g in r["group"]] == ["Cylinder block", "Electrical"]


def test_harga_disembunyikan_dari_staf(dunia):
    # Penjaga harga TERPUSAT di _run_tool (bukan di handler) — uji jalur nyata.
    r = ai._run_tool("part_dari_mesin",
                     {"no_mesin": "4P24B000713", "part": "starter"}, STAF)
    assert "harga_lokal" not in r["komponen"][0]
    assert r["komponen"][0]["stok_total"] == "3"


def test_butuh_no_mesin(dunia):
    assert "error" in ai._t_part_dari_mesin({}, ADMIN)
    assert "NOMOR MESIN" in ai._t_part_dari_mesin({"part": "starter"}, ADMIN)["error"]


def test_no_mesin_tak_ada_jujur(monkeypatch):
    monkeypatch.setattr(ai.epc_weichai, "find_parts_by_no",
                        lambda no, terms: {"found": False, "reason": "no_order",
                                           "message": "Nomor mesin 'X' tidak ditemukan di EPC Weichai."})
    monkeypatch.setattr(ai, "_expand_query", lambda q: ([q], []))
    r = ai._t_part_dari_mesin({"no_mesin": "X", "part": "starter"}, ADMIN)
    assert r["found"] is False and "tidak ditemukan" in r["error"]


def test_sesi_belum_aktif(monkeypatch):
    monkeypatch.setattr(ai.epc_weichai, "engine_bom_by_no",
                        lambda no: {"found": False, "reason": "no_session",
                                    "message": "Sesi EPC Weichai belum aktif."})
    r = ai._t_part_dari_mesin({"no_mesin": "4P24B000713"}, ADMIN)
    assert r["found"] is False and "Weichai" in r["error"]


def test_terdaftar_dispatch_dan_spec():
    assert ai._DISPATCH["part_dari_mesin"] is ai._t_part_dari_mesin
    names = {s["function"]["name"] for s in ai._tool_specs(ADMIN)}
    assert "part_dari_mesin" in names
