"""spek_mesin — SPESIFIKASI mesin Weichai (kapasitas oli, daya, emisi) + PN filter
kurasi pabrik, dipetik dari respons getOrderNumber yang MEMANG SUDAH dipanggil.

Latar: pertanyaan lapangan "berapa liter oli untuk service 40.000?" berbulan-bulan
dijawab "data tidak ada di sistem" / taksiran berlabel 'bukan data resmi', padahal
angkanya ikut terkirim di respons order Weichai lalu dibuang sebelum sampai ke
asisten. Tes ini mengunci ekstraksinya + kejujuran saat field tak dikirim.
"""
import pytest

from app.services import epc_weichai
from app.services import ai_assistant as ai

USER = {"username": "budi", "role": "user"}

# Bentuk respons NYATA getOrderNumber (dipangkas) — serial 1623S027151.
ORDER_NYATA = {
    "dhhNumber": "DHL10Q1061*01", "dhhName": "WP10.380E22", "id": "root-1",
    "oilUpLine": "20.9", "oilQualityGrade": "CF-4及以上",
    "jylqqPartNumbers": "1000424655",
    "ryjlxPartNumbers": "1000442956",
    "rycxPartNumbers": "1000424916",
    "maintenancePartNumbers": ["1013034356"],
    "heartPartNumbers": [["612630010015", "612600030053"]],
    "bagPartNumbers": [["612600030037"], ["612600030045", "612600030121"]],
    "cardPartNumbers": [],
    "ibaLang": {"Model": "WP10.380E22", "Power": "280", "Series": "WP10",
                "sim.emission": "Euro II", "ext.fueltype": "diesel oil",
                "ext.oilLevel": "20.9", "ProductType": "Truck power",
                "sim.modelStruct": "Diesel engine",
                "ext.supportingHostFactory": "中国重汽集团济南动力有限公司",
                "英文名称": "WP10.380E22 Diesel Engine for Truck"},
}


# ── ekstraksi field ─────────────────────────────────────────────────────────
def test_spek_dari_order_ambil_kapasitas_dan_spek():
    s = epc_weichai._spek_dari_order(ORDER_NYATA)
    assert s["kapasitas_oli_liter"] == "20.9"
    assert s["grade_oli"] == "CF-4及以上"
    assert s["model"] == "WP10.380E22"
    assert s["daya_kw"] == "280"
    assert s["emisi"] == "Euro II"
    assert s["seri"] == "WP10"


def test_dua_sumber_kapasitas_saling_cek():
    """oilUpLine & ext.oilLevel membawa angka yang sama — kecocokan itu dicatat."""
    s = epc_weichai._spek_dari_order(ORDER_NYATA)
    assert "cocok" in s["kapasitas_oli_sumber"]
    assert "kapasitas_oli_beda_sumber" not in s


def test_kapasitas_beda_antar_sumber_ditandai_bukan_dipilih_diam_diam():
    od = {**ORDER_NYATA, "oilUpLine": "20.9",
          "ibaLang": {**ORDER_NYATA["ibaLang"], "ext.oilLevel": "18"}}
    s = epc_weichai._spek_dari_order(od)
    assert s["kapasitas_oli_beda_sumber"] == {"oilUpLine": "20.9", "ext.oilLevel": "18"}


def test_field_tak_dikirim_tidak_diisi_nilai_palsu():
    """Field absen HARUS hilang dari hasil — bukan '' atau 0 yang terbaca sbg fakta."""
    s = epc_weichai._spek_dari_order({"dhhNumber": "X", "ibaLang": {"Model": "WPX"}})
    assert s["model"] == "WPX"
    for k in ("kapasitas_oli_liter", "daya_kw", "emisi", "grade_oli", "part_pabrik"):
        assert k not in s


def test_bentuk_grup_pn_tidak_seragam_diratakan():
    """API mengirim string / list / list-of-list untuk grup PN yang berbeda."""
    assert epc_weichai._pn_list("A1") == ["A1"]
    assert epc_weichai._pn_list(["A1", "A2"]) == ["A1", "A2"]
    assert epc_weichai._pn_list([["A1", "A2"], ["A3"]]) == ["A1", "A2", "A3"]
    assert epc_weichai._pn_list("A1, A2;A3") == ["A1", "A2", "A3"]
    assert epc_weichai._pn_list(None) == []
    assert epc_weichai._pn_list([["A1"], ["A1"]]) == ["A1"]      # dedup


def test_grup_pn_pabrik_dipetakan_ke_label():
    s = epc_weichai._spek_dari_order(ORDER_NYATA)
    pp = s["part_pabrik"]
    assert pp["filter_oli"] == ["1000424655"]
    assert pp["filter_solar_halus"] == ["1000442956"]
    assert pp["filter_solar_kasar"] == ["1000424916"]
    assert pp["paket_gasket"] == ["612600030037", "612600030045", "612600030121"]
    assert "part_kartu" not in pp                                 # list kosong dibuang


def test_bukan_dict_aman():
    assert epc_weichai._spek_dari_order(None) == {}
    assert epc_weichai._spek_dari_order("DHH123") == {}


# ── spek_mesin(): jalur serial & rangka ─────────────────────────────────────
def test_spek_mesin_via_nomor_mesin(monkeypatch):
    monkeypatch.setattr(epc_weichai, "resolve_engine_order", lambda s: {
        "found": True, "spek": epc_weichai._spek_dari_order(ORDER_NYATA)})
    r = epc_weichai.spek_mesin(no_mesin="1623S027151")
    assert r["found"] and r["kapasitas_oli_liter"] == "20.9"
    assert r["serial"] == "1623S027151"


def test_spek_mesin_via_rangka_pakai_cache_bridge(monkeypatch):
    dipanggil = []

    def fake_bridge(frame):
        dipanggil.append(frame)
        return {"found": True, "serial": "SER1", "dhhNumber": "D1",
                "spek": epc_weichai._spek_dari_order(ORDER_NYATA)}
    monkeypatch.setattr(epc_weichai, "_bridge", fake_bridge)
    r = epc_weichai.spek_mesin(rangka="rt110063")
    assert r["found"] and r["rangka"] == "RT110063" and r["serial"] == "SER1"
    assert dipanggil == ["RT110063"]


def test_spek_mesin_tanpa_argumen_ditolak():
    r = epc_weichai.spek_mesin()
    assert r["found"] is False and r["reason"] == "input"


def test_unit_non_weichai_diteruskan_apa_adanya(monkeypatch):
    monkeypatch.setattr(epc_weichai, "_bridge", lambda f: {
        "found": False, "reason": "no_link", "message": "Unit ini tidak punya link EPC Weichai"})
    r = epc_weichai.spek_mesin(rangka="ABC12345")
    assert r["found"] is False and r["reason"] == "no_link"


def test_order_tanpa_field_spek_bukan_klaim_tak_berspesifikasi(monkeypatch):
    monkeypatch.setattr(epc_weichai, "_bridge", lambda f: {
        "found": True, "serial": "S1", "spek": {}})
    r = epc_weichai.spek_mesin(rangka="ABC12345")
    assert r["found"] is False and r["reason"] == "no_spec"
    assert "bukan" in r["message"].lower()


# ── tool asisten ────────────────────────────────────────────────────────────
def test_tool_butuh_rangka_atau_no_mesin():
    assert "error" in ai._t_spek_mesin({}, USER)


def test_tool_perkaya_nama_part_pabrik(monkeypatch):
    monkeypatch.setattr(ai.epc_weichai, "spek_mesin", lambda rangka="", no_mesin="": {
        "found": True, "serial": "S1", "kapasitas_oli_liter": "20.9",
        "part_pabrik": {"filter_oli": ["1000424655"]}})
    monkeypatch.setattr(ai.part_index, "rows_for_pns",
                        lambda pns: {"1000424655": {"part_name": "Filter Element"}})
    r = ai._t_spek_mesin({"rangka": "RT110063"}, USER)
    grup = r["part_pabrik"]["Filter oli mesin (kurasi pabrik)"]
    assert grup == [{"pn": "1000424655", "nama": "Filter Element"}]
    assert list(r)[-1] == "catatan"


def test_tool_nama_tak_ketemu_tetap_bawa_pn(monkeypatch):
    monkeypatch.setattr(ai.epc_weichai, "spek_mesin", lambda rangka="", no_mesin="": {
        "found": True, "part_pabrik": {"filter_oli": ["PN-ASING"]}})
    monkeypatch.setattr(ai.part_index, "rows_for_pns", lambda pns: {})
    r = ai._t_spek_mesin({"rangka": "R1"}, USER)
    assert r["part_pabrik"]["Filter oli mesin (kurasi pabrik)"] == [
        {"pn": "PN-ASING", "nama": ""}]


def test_tool_exception_dilaporkan_sbg_gagal_cek(monkeypatch):
    def boom(**kw):
        raise RuntimeError("jaringan mati")
    monkeypatch.setattr(ai.epc_weichai, "spek_mesin", boom)
    r = ai._t_spek_mesin({"rangka": "R1"}, USER)
    assert r["_cek_tak_lengkap"] is True and "BUKAN bukti" in r["error"]


def test_catatan_larang_karang_interval_servis(monkeypatch):
    monkeypatch.setattr(ai.epc_weichai, "spek_mesin", lambda rangka="", no_mesin="": {
        "found": True, "kapasitas_oli_liter": "20.9"})
    r = ai._t_spek_mesin({"rangka": "R1"}, USER)
    assert "interval" in r["catatan"].lower()


def test_tool_terdaftar_di_spec_dan_dispatch():
    assert ai._DISPATCH["spek_mesin"] is ai._t_spek_mesin
    assert "spek_mesin" in [s["function"]["name"] for s in ai._tool_specs(USER)]
