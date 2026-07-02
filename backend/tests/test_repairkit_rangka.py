"""Repair kit per-VIN: resolve gearbox PERSIS unit via EPC (arg `rangka`).

EPC di-mock (tanpa network); data repair kit memakai data/repairkit nyata.
gearboxModelCode EPC = string deskriptif China, mis.
'HW25712XST变速箱+HW50直联式取力器(带液力缓速器)' → kode model = token Latin awal.
"""
import pytest

from app.services import ai_assistant as ai

RANGKA = "LZZ5DMSD5RT108966"


def _mock_epc(monkeypatch, lookup_result: dict):
    monkeypatch.setattr(ai.epc, "lookup", lambda rangka: lookup_result)


# ── _gearbox_from_rangka: ekstraksi kode dari string EPC ────────────────────

def test_ekstrak_kode_dari_string_epc(monkeypatch):
    _mock_epc(monkeypatch, {"found": True, "frame_number": "RT108966",
                            "gearbox": "HW25712XST变速箱+HW50直联式取力器(带液力缓速器)"})
    kode, info = ai._gearbox_from_rangka(RANGKA)
    assert kode == "HW25712XST"          # bagian '+HW50…取力器' (PTO) tak ikut
    assert info["model_gearbox"] == "HW25712XST"
    assert info["rangka"] == "RT108966"
    assert "EPC" in info["sumber"]


def test_rangka_tak_dikenal_epc(monkeypatch):
    _mock_epc(monkeypatch, {"found": False})
    kode, info = ai._gearbox_from_rangka("XX000000")
    assert kode == ""
    assert info["gearbox_epc"] is None
    assert "catatan_epc" in info


def test_epc_found_tapi_tanpa_gearbox(monkeypatch):
    _mock_epc(monkeypatch, {"found": True, "frame_number": "RT108966", "gearbox": ""})
    kode, info = ai._gearbox_from_rangka(RANGKA)
    assert kode == "" and "catatan_epc" in info


# ── Handler _t_repair_kit_transmisi dengan rangka ────────────────────────────

def test_rangka_meresolve_kit_persis_per_unit(monkeypatch):
    _mock_epc(monkeypatch, {"found": True, "frame_number": "RT108966",
                            "gearbox": "HW19709XST变速箱+HW50直联式取力器"})
    out = ai._t_repair_kit_transmisi({"rangka": RANGKA}, {"role": "user"})
    assert out["jumlah_model_cocok"] == 1
    assert out["hasil"][0]["model"] == "HW19709XST"
    assert out["resolusi_epc"]["model_gearbox"] == "HW19709XST"
    assert "EPC per-VIN" in out["catatan"]


def test_rangka_mengalahkan_teks_user(monkeypatch):
    # User salah sebut model — data pabrik (EPC) yang menang.
    _mock_epc(monkeypatch, {"found": True, "frame_number": "RT108966",
                            "gearbox": "HW25712XST变速箱+HW50直联式取力器"})
    out = ai._t_repair_kit_transmisi({"rangka": RANGKA, "transmisi": "HW19710"},
                                     {"role": "user"})
    assert out["hasil"][0]["model"] == "HW25712XST"


def test_epc_gagal_fallback_ke_teks_user(monkeypatch):
    _mock_epc(monkeypatch, {"found": False})
    out = ai._t_repair_kit_transmisi({"rangka": RANGKA, "transmisi": "HW19710"},
                                     {"role": "user"})
    assert out["jumlah_model_cocok"] >= 1
    assert out["hasil"][0]["model"] == "HW19710"
    assert "catatan_epc" in out["resolusi_epc"]      # jujur: ini perkiraan per-model


def test_epc_gagal_tanpa_model_jangan_menebak(monkeypatch):
    _mock_epc(monkeypatch, {"found": False})
    out = ai._t_repair_kit_transmisi({"rangka": "XX000000"}, {"role": "user"})
    assert out["jumlah_model_cocok"] == 0
    assert "JANGAN menebak" in out["catatan"]
    assert "hasil" not in out


def test_gearbox_epc_tanpa_data_kit_jujur(monkeypatch):
    # EPC menyebut gearbox yang TIDAK punya data repair kit → jangan tawarkan kit lain.
    _mock_epc(monkeypatch, {"found": True, "frame_number": "RT108966",
                            "gearbox": "ZZ9999XYZ变速箱"})
    out = ai._t_repair_kit_transmisi({"rangka": RANGKA}, {"role": "user"})
    assert out["jumlah_model_cocok"] == 0
    assert out["resolusi_epc"]["model_gearbox"] == "ZZ9999XYZ"
    assert "JANGAN menawarkan kit model lain" in out["catatan"]


def test_tanpa_rangka_perilaku_lama_utuh():
    out = ai._t_repair_kit_transmisi({"transmisi": "HW19710"}, {"role": "user"})
    assert out["jumlah_model_cocok"] >= 1
    assert out["hasil"][0]["model"] == "HW19710"
    assert "resolusi_epc" not in out
