"""Fitur pembeli 'Cocok di unit saya?': verifikasi part ke BOM per-VIN EPC.

Aturan keras pemilik: part per-unit SELALU dicek EPC — fitur ini membawanya ke
halaman detail part pembeli. Cocok → penjelasan ramah (nama EN + istilah lapangan
+ kategori + figure/balon) + gambar exploded view; tidak cocok → jawaban JUJUR,
bukan tebakan. Gambar dilayani endpoint sendiri (bukan /api/ai/excel yang digembok
izin menu Asisten AI).
"""
import pytest

from app.routers import parts as R

USER = {"username": "roni", "role": "pembeli"}
_LL = {"found": True, "frame_number": "LZZTEST123", "jumlah_part": 3, "parts": [
    {"pn": "WG9100443050", "nama_cn": "制动摩擦片", "qty": 4},
    {"pn": "VG1560080012", "nama_cn": "燃油滤清器", "qty": 1},
]}


@pytest.fixture
def dunia(monkeypatch):
    from app.services import ai_export, catalog_bom, epc_bom, part_index
    monkeypatch.setattr(epc_bom, "loading_list", lambda r: dict(_LL))
    monkeypatch.setattr(part_index, "name_for",
                        lambda pn: "Brake friction plate" if pn.startswith("WG9100") else "")
    monkeypatch.setattr(catalog_bom, "pn_category_map",
                        lambda: {catalog_bom._norm("WG9100443050"): {"kategori": "09"}})
    monkeypatch.setattr(epc_bom, "exploded_figures",
                        lambda r, pn, k: {"found": True, "figures": [
                            {"svg": "fig.svg", "balon": 7, "nama": "FRONT AXLE BRAKE",
                             "kategori": k, "jumlah_item": 12, "items_ringkas": []}]})
    stashed = {}
    monkeypatch.setattr(ai_export, "stash_builder",
                        lambda judul, builder, ext="png": (stashed.update(builder=builder) or ("IMG1", "x.png")))
    monkeypatch.setattr(R, "_istilah_lapangan", lambda nama: "kampas rem" if "friction" in nama.lower() else "")
    return stashed


def test_cocok_lengkap_dengan_gambar(dunia):
    r = R.cek_part_di_unit(R.CekUnitRequest(part_number="WG9100443050", rangka="LZZTEST123"), USER)
    assert r["checked"] and r["cocok"] is True
    assert r["nama"] == "Brake friction plate" and r["istilah_lapangan"] == "kampas rem"
    assert r["qty"] == 4 and r["balon"] == 7 and r["image_id"] == "IMG1"
    assert "kampas rem" in r["penjelasan"] and "FRONT AXLE BRAKE" in r["penjelasan"]
    assert dunia["builder"]["balon"] == 7          # balon part DISOROT di PNG


def test_cocok_tanpa_kategori_tetap_dikirim(dunia, monkeypatch):
    """Part tanpa peta kategori → tak ada gambar, tapi hasil cocok TETAP tampil."""
    from app.services import catalog_bom
    monkeypatch.setattr(catalog_bom, "pn_category_map", lambda: {})
    r = R.cek_part_di_unit(R.CekUnitRequest(part_number="VG1560080012", rangka="LZZTEST123"), USER)
    assert r["cocok"] is True and r["image_id"] is None and r["lokasi"] is None


def test_tidak_cocok_jawab_jujur(dunia):
    r = R.cek_part_di_unit(R.CekUnitRequest(part_number="AZ9999999999", rangka="LZZTEST123"), USER)
    assert r["checked"] and r["cocok"] is False
    assert "TIDAK terdaftar" in r["pesan"]          # jujur — jangan menebak cocok


def test_rangka_tak_dikenal(dunia, monkeypatch):
    from app.services import epc_bom
    monkeypatch.setattr(epc_bom, "loading_list", lambda r: {"found": False, "_err": "empty"})
    r = R.cek_part_di_unit(R.CekUnitRequest(part_number="WG9100443050", rangka="LZZSALAH1"), USER)
    assert r["checked"] is False and "tidak ditemukan" in r["error"]


def test_gambar_gagal_tak_menggagalkan_hasil(dunia, monkeypatch):
    """exploded_figures error → hasil cocok tetap dikirim tanpa gambar (best-effort)."""
    from app.services import epc_bom
    def _boom(r, pn, k):
        raise RuntimeError("EPC figure error")
    monkeypatch.setattr(epc_bom, "exploded_figures", _boom)
    r = R.cek_part_di_unit(R.CekUnitRequest(part_number="WG9100443050", rangka="LZZTEST123"), USER)
    assert r["cocok"] is True and r["image_id"] is None


def test_endpoint_gambar_terpisah_dari_gembok_ai(monkeypatch):
    from app.services import ai_export
    monkeypatch.setattr(ai_export, "generic_excel", lambda i: (b"PNGDATA", "x.png"))
    resp = R.part_exploded_png("abc", {"username": "roni", "role": "pembeli"})
    assert resp.body == b"PNGDATA" and resp.media_type == "image/png"


def test_istilah_lapangan_keyword_terpanjang_menang(monkeypatch):
    entries = [
        {"triggers": ["kampas rem"], "keywords": ["brake friction plate", "friction plate"]},
        {"triggers": ["plat kopling"], "keywords": ["friction"]},
    ]
    monkeypatch.setattr("app.services.ai_assistant._load_sinonim_entries", lambda: entries)
    assert R._istilah_lapangan("Brake friction plate assembly") == "kampas rem"
    assert R._istilah_lapangan("Oil filter") == ""
