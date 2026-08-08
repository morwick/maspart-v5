"""Konfigurasi PABRIK per unit dari SIMS `configDesc` — sumber KAPASITAS TANGKI BBM.

Latar: kapasitas tangki berkali-kali dijawab "tidak tercatat di sistem" ke user
lapangan (log 2026-08-08 id 1659/1660), dan audit sempat menyimpulkan datanya
memang tak ada — SALAH. `getVehicleInfoAndMaintNum` mengembalikan 61 field dan
`info_unit` membuang `configDesc`, tempat pabrik menulis konfigurasi unit:
  SJ346500 → "…12.00R24轮胎(…);400L油箱;…"   RT110063 → "…300L油箱;…"
Terverifikasi 14 dari 16 unit uji membawa angkanya (120L–600L).
"""
import pytest

from app.services import sims_warranty as sw
from app.services import ai_assistant as ai

USER = {"username": "budi", "role": "user"}

CD_NYATA = ("豪沃  NX;印度尼西亚;自卸车;WP12.400E201发动机;HW19709XST变速箱;"
            "速比6.73;12.00R24轮胎(超强型钢圈/工程块状/20PR);400L油箱;"
            "驾驶室双侧顶置警灯;电子风扇;单冷空调;不带ABS;")


# ── parser kapasitas ────────────────────────────────────────────────────────
def test_ambil_kapasitas_tangki_dari_config_nyata():
    k = sw.konfigurasi_pabrik(CD_NYATA)
    assert k["kapasitas_tangki_liter"] == 400.0
    assert k["tangki_teks"] == ["400L油箱"]
    assert "tangki_rincian_liter" not in k


def test_ruas_dipecah_dan_dipertahankan_apa_adanya():
    """Ruas lain (ban, rasio gardan, ABS) ikut supaya pertanyaan konfigurasi
    lain bisa dijawab dari sumber, bukan ingatan model."""
    k = sw.konfigurasi_pabrik(CD_NYATA)
    assert "12.00R24轮胎(超强型钢圈/工程块状/20PR)" in k["ruas"]
    assert "速比6.73" in k["ruas"]
    assert "不带ABS" in k["ruas"]
    assert len(k["ruas"]) == 12


def test_tangki_dengan_material_tetap_terbaca():
    k = sw.konfigurasi_pabrik("A;120L铁油箱;B")
    assert k["kapasitas_tangki_liter"] == 120.0


def test_tangki_ganda_total_plus_rincian():
    """Total sah, tapi rinciannya WAJIB ikut — user tak boleh mengira itu satu
    tangki 1000 L."""
    k = sw.konfigurasi_pabrik("X;600L+400L油箱;Y")
    assert k["kapasitas_tangki_liter"] == 1000.0
    assert k["tangki_rincian_liter"] == [600.0, 400.0]


def test_liter_tidak_tertipu_kode_part_berawalan_L():
    """'L' yang diikuti huruf latin (mis. PN 'LG9704…') bukan satuan liter."""
    assert sw._liter("LG9704580101") == []
    assert sw._liter("12.00R20(工程横向)") == []
    assert sw._liter("400L油箱") == [400.0]


def test_batas_kata_cjk_tidak_menggagalkan_angka():
    """Regresi: '\\b' sesudah L tak pernah cocok karena CJK termasuk \\w →
    ruas tangki terdeteksi tapi liternya selalu kosong."""
    assert sw._liter("300L油箱") == [300.0]


def test_config_kosong_bukan_nol():
    assert sw.konfigurasi_pabrik("") == {}
    assert sw.konfigurasi_pabrik(None) == {}


def test_tanpa_ruas_tangki_tak_mengarang_angka():
    k = sw.konfigurasi_pabrik("豪沃 NX;单冷空调;不带ABS;")
    assert k["ruas"]
    assert "kapasitas_tangki_liter" not in k
    assert "tangki_teks" not in k


# ── info_unit menyertakan konfigurasi ───────────────────────────────────────
def test_info_unit_bawa_konfigurasi_pabrik(monkeypatch):
    monkeypatch.setattr(sw, "_cached", lambda key, ttl, fn: {
        "chassisNo": "SJ346500", "brandName": "HOWO", "configDesc": CD_NYATA})
    r = sw.info_unit("SJ346500")
    assert r["konfigurasi_pabrik"]["kapasitas_tangki_liter"] == 400.0


def test_info_unit_tanpa_config_tak_menambah_kunci(monkeypatch):
    monkeypatch.setattr(sw, "_cached", lambda key, ttl, fn: {
        "chassisNo": "PJ264967", "brandName": "HOWO", "configDesc": ""})
    assert "konfigurasi_pabrik" not in sw.info_unit("PJ264967")


# ── cek_kendaraan memperkaya dengan konfigurasi ─────────────────────────────
def test_cek_kendaraan_sertakan_kapasitas_tangki(monkeypatch):
    monkeypatch.setattr(ai.epc, "lookup", lambda r: {"found": True, "model_code": "ZZ"})
    monkeypatch.setattr(ai.epc_bom, "assembly_list", lambda r: {"found": False})
    monkeypatch.setattr(ai.sims_warranty, "available", lambda: True)
    monkeypatch.setattr(ai.sims_warranty, "info_unit", lambda r: {
        "konfigurasi_pabrik": {"ruas": ["400L油箱"], "tangki_teks": ["400L油箱"],
                               "kapasitas_tangki_liter": 400.0}})
    r = ai._t_cek_kendaraan({"rangka": "SJ346500"}, USER)
    assert r["konfigurasi_pabrik"]["kapasitas_tangki_liter"] == 400.0
    assert "tangki solarnya berapa liter" in r["catatan"]


def test_sims_mati_tak_menjatuhkan_spesifikasi_epc(monkeypatch):
    """Perkayaan ini best-effort: SIMS tumbang → jawaban EPC tetap utuh."""
    monkeypatch.setattr(ai.epc, "lookup", lambda r: {"found": True, "model_code": "ZZ"})
    monkeypatch.setattr(ai.epc_bom, "assembly_list", lambda r: {"found": False})
    monkeypatch.setattr(ai.sims_warranty, "available", lambda: True)

    def boom(r):
        raise RuntimeError("SIMS mati")
    monkeypatch.setattr(ai.sims_warranty, "info_unit", boom)
    r = ai._t_cek_kendaraan({"rangka": "SJ346500"}, USER)
    assert r["found"] is True
    assert "konfigurasi_pabrik" not in r


def test_unit_tanpa_config_tak_memunculkan_kunci_kosong(monkeypatch):
    monkeypatch.setattr(ai.epc, "lookup", lambda r: {"found": True, "model_code": "ZZ"})
    monkeypatch.setattr(ai.epc_bom, "assembly_list", lambda r: {"found": False})
    monkeypatch.setattr(ai.sims_warranty, "available", lambda: True)
    monkeypatch.setattr(ai.sims_warranty, "info_unit", lambda r: {})
    r = ai._t_cek_kendaraan({"rangka": "PJ264967"}, USER)
    assert "konfigurasi_pabrik" not in r
