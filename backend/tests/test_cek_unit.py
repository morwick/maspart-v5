"""Fitur pembeli 'Cocok di unit saya?' — kini lewat PENCARIAN TERBALIK EPC.

Satu panggilan home/reverse/part memvonis cocok/tidak untuk SELURUH katalog unit
(termasuk part yang tersembunyi di dalam assembly — kampas rem AZ450045000042 di
SJ346500 terbukti ketemu sebagai bagian 'Brake shoe assembly'), menggantikan alur
lama: fetch Loading List + sisir 10 kategori Atlas (1-2 menit). Gambar exploded
view tinggal pelengkap best-effort. Vonis 'tidak cocok' tetap jujur; EPC bermasalah
= 'gagal mengecek', bukan vonis palsu.
"""
import pytest

from app.routers import parts as R

USER = {"username": "roni", "role": "pembeli"}

# Hasil reverse per-PN (unit uji). Kampas = kasus nyata: 2 posisi assembly.
_REV = {
    "AZ450045000042": [
        {"parent_pn": "AZ450045001161", "parent_nama": "Brake shoe assembly",
         "part_id": 1482857, "part_list_id": 432614, "root_id": 1278889},
        {"parent_pn": "AZ450045001160", "parent_nama": "Brake shoe assembly",
         "part_id": 1482856, "part_list_id": 432613, "root_id": 1278889},
    ],
    "WG9100443050": [
        {"parent_pn": "WG9100443001", "parent_nama": "Front brake assembly",
         "part_id": 1, "part_list_id": 2, "root_id": 3},
    ],
}


@pytest.fixture
def dunia(monkeypatch):
    from app.services import ai_export, catalog_bom, epc_bom, part_index

    def _reverse(rangka, pn):
        inst = _REV.get(pn.upper())
        return {"found": bool(inst), "frame_number": rangka.upper(),
                "instances": list(inst or [])}

    monkeypatch.setattr(epc_bom, "reverse_find_in_unit", _reverse)
    # Loading List TIDAK boleh disentuh lagi (alur lama yang lambat).
    monkeypatch.setattr(epc_bom, "loading_list",
                        lambda r: (_ for _ in ()).throw(AssertionError("loading_list dipanggil")))
    monkeypatch.setattr(part_index, "name_for",
                        lambda pn: "Brake friction plate" if pn.startswith(("WG9100", "AZ4500")) else "")
    monkeypatch.setattr(catalog_bom, "pn_category_map",
                        lambda: {catalog_bom._norm("WG9100443050"): {"kategori": "09"},
                                 catalog_bom._norm("AZ450045000042"): {"kategori": "06"}})
    fig_calls: list[str] = []

    def _figures(r, pn, k):
        fig_calls.append(k)
        if pn.upper() in _REV:
            return {"found": True, "figures": [
                {"svg": "fig.svg", "balon": 2, "qty": 2, "nama_item": "Brake friction plate",
                 "nama": "brake shoe assembly", "kategori": k, "jumlah_item": 9,
                 "items_ringkas": []}]}
        return {"found": False, "_err": "not_in_category"}

    monkeypatch.setattr(epc_bom, "exploded_figures", _figures)
    stashed = {}
    monkeypatch.setattr(ai_export, "stash_builder",
                        lambda judul, builder, ext="png": (stashed.update(builder=builder) or ("IMG1", "x.png")))
    monkeypatch.setattr(R, "_istilah_lapangan", lambda nama: "kampas rem" if "friction" in nama.lower() else "")
    stashed["fig_calls"] = fig_calls
    return stashed


def test_kampas_tersembunyi_di_assembly_ketemu_satu_panggilan(dunia):
    """Kasus nyata SJ346500: kampas tak ada di Loading List — reverse menemukannya
    sebagai bagian 'Brake shoe assembly' TANPA sisir kategori."""
    r = R.cek_part_di_unit(R.CekUnitRequest(part_number="AZ450045000042", rangka="sj346500"), USER)
    assert r["checked"] and r["cocok"] is True and r["sumber"] == "epc_reverse"
    assert r["assembly_induk"] == "Brake shoe assembly" and r["jumlah_posisi"] == 2
    assert r["qty"] == 2 and r["balon"] == 2 and r["image_id"] == "IMG1"
    assert "kampas rem" in r["penjelasan"] and "Brake shoe assembly" in r["penjelasan"]
    assert dunia["fig_calls"] == ["gardan depan"]     # gambar: 1 kategori terpetakan saja


def test_tidak_cocok_cepat_tanpa_sisir_kategori(dunia):
    r = R.cek_part_di_unit(R.CekUnitRequest(part_number="ZZ0000000001", rangka="LZZTEST123"), USER)
    assert r["checked"] and r["cocok"] is False
    assert "menyeluruh" in r["pesan"]
    assert dunia["fig_calls"] == []                   # nol walk kategori — vonis dari reverse


def test_epc_error_bukan_vonis_palsu(dunia, monkeypatch):
    from app.services import epc_bom
    monkeypatch.setattr(epc_bom, "reverse_find_in_unit",
                        lambda r, pn: {"found": False, "_err": "token_expired"})
    r = R.cek_part_di_unit(R.CekUnitRequest(part_number="AZ450045000042", rangka="SJ346500"), USER)
    assert r["checked"] is False and "token" in r["error"].lower()


def test_rangka_tak_dikenal(dunia, monkeypatch):
    from app.services import epc_bom
    monkeypatch.setattr(epc_bom, "reverse_find_in_unit",
                        lambda r, pn: {"found": False, "_err": "not_found"})
    r = R.cek_part_di_unit(R.CekUnitRequest(part_number="AZ450045000042", rangka="LZZSALAH1"), USER)
    assert r["checked"] is False and "tidak ditemukan" in r["error"]


def test_gambar_gagal_tak_menggagalkan_hasil(dunia, monkeypatch):
    """Reverse sudah memvonis cocok; figure error → hasil tetap dikirim, lokasi
    jatuh ke assembly induk dari reverse."""
    from app.services import epc_bom

    def _boom(r, pn, k):
        raise RuntimeError("EPC figure error")

    monkeypatch.setattr(epc_bom, "exploded_figures", _boom)
    r = R.cek_part_di_unit(R.CekUnitRequest(part_number="AZ450045000042", rangka="SJ346500"), USER)
    assert r["cocok"] is True and r["image_id"] is None
    assert r["lokasi"] == "Brake shoe assembly"


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
