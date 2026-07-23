"""Tool `turunan_assembly` (2026-07-23): telusuri KOMPONEN sebuah assembly PN
dari MODEL LAIN yang punya breakdown-nya (jalur global EPC) — untuk kasus
assembly yang di VIN target hanya muncul utuh tanpa rincian.

Alur service (assembly_components_global) di-MOCK — tanpa jaringan."""
import pytest

from app.services import ai_assistant as ai

ADMIN = {"username": "admin", "role": "admin"}
STAF = {"username": "budi", "role": "user"}
PEMBELI = {"username": "roni", "role": "pembeli"}

_HASIL = {
    "found": True,
    "assembly": {"pn": "WG9925477132", "nama": "Power steering gear assembly"},
    "sumber_model": "19th edition ZZ4257N3247D1",
    "figure_pn": "AC97254780217", "figure_nama": "Steering device",
    "svg": "I000.svg",
    "jumlah_model_pemakai": 1357, "figure_unik": 173, "figure_dicoba": 1,
    "komponen": [
        {"pn": "WG9725479295", "nama": "Steering gear bracket", "nama_cn": "转向器支架",
         "qty": 1, "balon": 1, "pengganti": []},
        {"pn": "WG9725476016", "nama": "Steering vane pump", "nama_cn": "",
         "qty": 1, "balon": 6, "pengganti": [{"pn": "AZ9725476016", "nama": "Vane pump"}]},
    ],
}


@pytest.fixture
def dunia(monkeypatch):
    monkeypatch.setattr(ai.epc_bom, "assembly_components_global",
                        lambda pn, **kw: dict(_HASIL))
    monkeypatch.setattr(ai.part_index, "rows_for_pns", lambda pns: {
        "WG9725479295": {"part_number": "WG9725479295",
                         "part_name": "Steering gear bracket",
                         "stok": "5", "harga": "Rp 1.200.000",
                         "gudang": {"01.Jakarta": 5}},
    })
    return {}


def test_turunan_ketemu_lintas_model(dunia):
    r = ai._t_turunan_assembly({"pn": "WG9925477132"}, ADMIN)
    assert r["found"] and r["jumlah_komponen"] == 2
    assert r["sumber_model"].startswith("19th edition")
    assert r["jumlah_model_pemakai"] == 1357
    pns = [k["part_number"] for k in r["komponen"]]
    assert pns == ["WG9725479295", "WG9725476016"]
    # silang inventori
    k0 = r["komponen"][0]
    assert k0["ada_di_inventori"] and k0["stok_total"] == "5"
    assert k0["harga_lokal"] == "Rp 1.200.000" and k0["balon"] == 1
    # pengganti dari partAlternates
    assert r["komponen"][1]["part_pengganti"][0]["pn"] == "AZ9725476016"
    assert "model lain" in r["catatan"]
    assert list(r).index("catatan") == len(r) - 1   # catatan key terakhir


def test_harga_disembunyikan_dari_staf(dunia):
    r = ai._t_turunan_assembly({"pn": "WG9925477132"}, STAF)
    assert "harga_lokal" not in r["komponen"][0]
    assert r["komponen"][0]["stok_total"] == "5"


def test_pembeli_tak_lihat_per_gudang(dunia):
    r = ai._t_turunan_assembly({"pn": "WG9925477132"}, PEMBELI)
    assert "stok_per_gudang" not in r["komponen"][0]


def test_butuh_pn_bukan_nama(dunia):
    assert "error" in ai._t_turunan_assembly({}, ADMIN)
    assert "PN assembly" in ai._t_turunan_assembly({"pn": "v stay"}, ADMIN)["error"]


def test_tak_beranak_jujur(monkeypatch):
    monkeypatch.setattr(ai.epc_bom, "assembly_components_global",
                        lambda pn, **kw: {"found": False, "assembly": {"pn": pn},
                                          "jumlah_model_pemakai": 40,
                                          "figure_unik": 5, "figure_dicoba": 5})
    r = ai._t_turunan_assembly({"pn": "WG9999999999"}, ADMIN)
    assert r["found"] is False and r["jumlah_model_pemakai"] == 40
    assert "JANGAN mengarang" in r["error"]


def test_token_issue_diteruskan(monkeypatch):
    monkeypatch.setattr(ai.epc_bom, "assembly_components_global",
                        lambda pn, **kw: {"found": False, "_err": "token_expired"})
    r = ai._t_turunan_assembly({"pn": "WG9925477132"}, ADMIN)
    assert r.get("_token_issue") is True


def test_terdaftar_dispatch_dan_spec():
    assert ai._DISPATCH["turunan_assembly"] is ai._t_turunan_assembly
    names = {s["function"]["name"] for s in ai._tool_specs(ADMIN)}
    assert "turunan_assembly" in names
