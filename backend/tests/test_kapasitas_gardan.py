"""Kapasitas oli gardan per MODEL + torsi mur roda (dokumen RESMI en.sinotruk.com).

Menghapus batas dataset `jadwal_servis_truk` yang hanya mencakup gardan MCY13.

Yang dikunci di sini adalah hal-hal yang, bila salah, MERUSAK UNIT:
 • notasi penjumlahan '17+2×2=21L' harus jadi 21 L (gardan + 2 hub), bukan 17;
 • label poros bisa terpotong spasi di PDF ('后 桥') — AC16 sempat kehilangan
   penanda poros belakangnya;
 • model gardan yang TIDAK terdaftar tak boleh dijawab dengan angka model lain;
 • TRANSMISI sengaja TIDAK ada di dataset ini: percobaan pertama memberi
   HW16709XST = 9,5 L padahal itu milik HW13709XST (salah-pasang senyap).
"""
import pytest

from app.services import kapasitas_gardan as kg
from app.services import ai_assistant as ai

USER = {"username": "budi", "role": "user"}


# ── dataset nyata ───────────────────────────────────────────────────────────
def test_dataset_tersedia():
    assert kg.available()
    m = kg.meta()
    assert "en.sinotruk.com" in (m.get("url_tabel") or "")


def test_mcy13_cocok_dengan_dokumen_cnhtc_lain():
    """Validasi silang: angka yang sama muncul di dua dokumen resmi BERBEDA."""
    g = kg.cari_gardan("MCY13")
    assert len(g) == 1
    peta = {k["poros"]: k["liter"] for k in g[0]["kapasitas"]}
    assert peta == {"poros tengah": 18.0, "poros belakang": 14.5}


def test_notasi_penjumlahan_jadi_total_bukan_angka_pertama():
    """AC16 '17+2×2=21L' = gardan 17 L + 2 hub × 2 L. Menyimpan 17 = kurang 4 L."""
    g = kg.cari_gardan("AC16")[0]
    tengah = next(k for k in g["kapasitas"] if k["poros"] == "poros tengah")
    assert tengah["liter"] == 21.0
    assert tengah["rumus"] == "17+2×2=21L"


def test_label_poros_belakang_tidak_hilang_karena_spasi():
    """Regresi: '后 桥' terpotong pemenggalan baris PDF → poros belakang AC16
    sempat tak berlabel sama sekali."""
    g = kg.cari_gardan("AC16")[0]
    poros = {k.get("poros") for k in g["kapasitas"]}
    assert poros == {"poros tengah", "poros belakang"}
    belakang = next(k for k in g["kapasitas"] if k["poros"] == "poros belakang")
    assert belakang["liter"] == 17.5


def test_beberapa_model_gardan_tercakup():
    model = {g["model"] for g in kg.cari_gardan()}
    assert {"MCY11", "MCY13", "AC16", "AC26", "HW16"} <= model


def test_transmisi_tidak_ada_di_dataset_ini():
    """Sengaja dikeluarkan — risiko salah-pasang antar model gearbox."""
    for g in kg.cari_gardan():
        assert g["jenis"] == "gardan"
        assert not g["model"].startswith("HW1") or g["model"] == "HW16"


def test_torsi_mur_roda_resmi():
    t = kg.torsi()
    assert t and t[0]["nm_min"] == 550 and t[0]["nm_maks"] == 600
    assert "50km" in (t[0]["catatan"] or "").replace(" ", "")   # kencangkan ulang


def test_model_tak_dikenal_kembalikan_kosong_bukan_tebakan():
    assert kg.cari_gardan("MODEL-KARANGAN-XYZ") == []


def test_pencocokan_longgar():
    assert kg.cari_gardan("mcy 13")[0]["model"] == "MCY13"
    assert kg.cari_gardan("MCY-13")[0]["model"] == "MCY13"


# ── tool ────────────────────────────────────────────────────────────────────
def test_tool_gardan_dan_torsi():
    r = ai._t_jadwal_servis_truk({"gardan": "AC16"}, USER)
    assert r["gardan"][0]["model"] == "AC16"
    assert r["torsi"][0]["nm_min"] == 550


def test_tool_gardan_asing_tak_disamarkan(monkeypatch):
    """MCP16ZG = gardan nyata unit SJ346500 tapi tak ada di dokumen — asisten
    HARUS diberi tahu, bukan diberi angka MCY13."""
    r = ai._t_jadwal_servis_truk({"gardan": "MCP16ZG"}, USER)
    assert r["gardan_tak_dikenal"] == "MCP16ZG"
    assert "MCY13" in r["gardan_tersedia"]
    assert r["gardan"] == []


def test_catatan_larang_pakai_angka_model_lain():
    r = ai._t_jadwal_servis_truk({"gardan": "AC16"}, USER)
    c = r["catatan"]
    assert "JANGAN memakai angka model lain" in c
    assert "COCOKKAN dengan model gardan unit" in c
    assert "50 km" in c                     # pengencangan ulang mur roda


def test_sumber_gardan_disebut():
    r = ai._t_jadwal_servis_truk({"gardan": "MCY13"}, USER)
    assert "sinotruk" in (r.get("sumber_gardan") or "").lower()


def test_dataset_absen_tak_menjatuhkan_tool(monkeypatch):
    monkeypatch.setattr(ai.kapasitas_gardan, "available", lambda: False)
    r = ai._t_jadwal_servis_truk({"km": 34000}, USER)
    assert r["servis"]["found"] is True and "gardan" not in r


# ── parser builder ──────────────────────────────────────────────────────────
def _builder():
    import importlib.util
    from pathlib import Path
    src = Path(__file__).resolve().parents[1] / "tools" / "build_kapasitas_gardan.py"
    spec = importlib.util.spec_from_file_location("build_kapasitas_gardan", src)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_parser_penjumlahan_dan_poros_terpotong():
    m = _builder()
    hal = ["普通 AC16 齿轮油 GL-5 85W-90 17+2×2=21L （中桥） 13.5+2× 2=17.5L（后 桥） **sisa**"]
    out = m.parse_gardan(hal)
    assert len(out) == 1 and out[0]["model"] == "AC16"
    kap = out[0]["kapasitas"]
    assert [k["liter"] for k in kap] == [21.0, 17.5]
    assert [k["poros"] for k in kap] == ["poros tengah", "poros belakang"]


def test_parser_abaikan_kode_model_jauh_di_badan_teks():
    """Kode yang menyempil jauh dari awal halaman pernah membuat kapasitas
    model LAIN tercatat atas nama model itu."""
    m = _builder()
    hal = ["x" * 200 + " MCY13 齿轮油 18L（中桥）"]
    assert m.parse_gardan(hal) == []


def test_parser_butuh_penanda_oli_roda_gigi():
    m = _builder()
    assert m.parse_gardan(["MCY13 sesuatu 18L（中桥）"]) == []
