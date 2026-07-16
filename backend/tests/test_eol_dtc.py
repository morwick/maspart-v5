"""Test eol_dtc + wiring_ref + integrasi tool — sumber pengetahuan EOL CNHTC.

Fokus: (1) loader gzip data NYATA yang di-commit (eol_dtc.json.gz), (2) search
kode eksak/prefix (P0100 → keluarga P0100F7) + query Indonesia + filter unit,
(3) jembatan SPN/FMI Bosch → langkah perbaikan EOL (repair_for), (4) handler
cari_kode_kesalahan memuat hasil_eol/perbaikan_eol, (5) wiring_ref: index +
sinonim + batas kata + validasi nama file (anti-traversal), (6) tool
diagram_wiring men-stash jpg & masuk kanal gambar.
"""
import pytest

from app.services import ai_assistant as ai
from app.services import eol_dtc, wiring_ref

USER = {"username": "budi", "role": "user"}


# ── eol_dtc (data nyata ter-commit) ─────────────────────────────────────
def test_data_tersedia_dan_cakupan():
    assert eol_dtc.available()
    assert eol_dtc.count() > 5000
    assert len(eol_dtc.units()) >= 40


def test_search_kode_prefix_keluarga():
    res = eol_dtc.search(code="P0100")
    assert res and all(r["kode"].startswith("P0100") for r in res)
    assert any(r["kode"] == "P0100F7" for r in res)


def test_search_kode_eksak_menang():
    res = eol_dtc.search(code="P0100F7")
    assert res and res[0]["kode"] == "P0100F7"


def test_search_query_indonesia_dan_unit():
    res = eol_dtc.search(query="radar terhalang")
    assert res and any(r["unit"].startswith("SRR") for r in res)
    ems = eol_dtc.search(query="rail", unit="EMS")
    assert ems and all(r["unit"] == "EMS" for r in ems)


def test_repair_for_jembatan_bosch():
    r = eol_dtc.repair_for("P0645")
    assert r and r["kode"].startswith("P0645") and r["perbaikan"]


# ── handler cari_kode_kesalahan (gabung Bosch + EOL) ────────────────────
def test_handler_spn_fmi_dapat_perbaikan_eol():
    r = ai._t_cari_kode_kesalahan({"spn": 1551, "fmi": 5}, USER)
    assert r["jumlah_cocok"] >= 1
    h = r["hasil"][0]
    assert h["kode"] == "P0645"
    assert h.get("perbaikan_eol", {}).get("perbaikan")


def test_handler_code_memuat_hasil_eol():
    r = ai._t_cari_kode_kesalahan({"code": "P0100"}, USER)
    assert r["jumlah_cocok_eol"] > 0
    assert all(x["kode"].startswith("P0100") for x in r["hasil_eol"])


def test_handler_unit_saja():
    r = ai._t_cari_kode_kesalahan({"unit": "ESP", "query": "tegangan"}, USER)
    assert r["hasil_eol"] and all(x["unit_kontrol"] == "ESP" for x in r["hasil_eol"])


# ── wiring_ref + tool diagram_wiring ────────────────────────────────────
def test_wiring_index_dan_sinonim():
    assert wiring_ref.available()
    assert wiring_ref.count() >= 50
    res = wiring_ref.search("pedal gas")
    assert res and res[0]["file"] == "accp.jpg"
    # batas kata: 'pedal gas' TIDAK boleh menyeret SCR_rapps.jpg (substring 'app')
    assert all("SCR_rapps" not in r["file"] for r in res)


def test_wiring_image_bytes_validasi():
    assert wiring_ref.image_bytes("accp.jpg")[:3] == b"\xff\xd8\xff"
    assert wiring_ref.image_bytes("../secrets.toml") is None
    assert wiring_ref.image_bytes("tidak-terdaftar.jpg") is None


def test_tool_diagram_wiring_stash_inline():
    from app.services import ai_export
    r = ai._t_diagram_wiring({"komponen": "tekanan rail"}, USER)
    assert r.get("found") and r["gambar"]
    g = r["gambar"][0]
    assert g["filename"].endswith(".jpg")
    data, fname = ai_export.generic_excel(g["image_id"])
    assert data[:3] == b"\xff\xd8\xff" and fname == g["filename"]


def test_tool_diagram_wiring_miss_daftar():
    r = ai._t_diagram_wiring({"komponen": "komponen tak dikenal xyz"}, USER)
    assert r.get("jumlah") == 0 and "Diagram yang tersedia" in r["catatan"]


def test_spec_dan_dispatch():
    names = [s["function"]["name"] for s in ai._tool_specs(USER, "")]
    assert "diagram_wiring" in names and "cari_kode_kesalahan" in names
    assert "diagram_wiring" in ai._DISPATCH
