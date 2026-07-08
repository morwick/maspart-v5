"""Katalog: kolom Stok & Harga default KOSONG; hanya ADMIN yang minta bisa isi;
user non-admin tak pernah bisa (walau flag dikirim). Builder di-mock ke EPC.
"""
import pytest

from app.services import ai_assistant as ai
from app.services import ai_export

ADMIN = {"username": "admin", "role": "admin"}
USER = {"username": "u", "role": "user"}
PEMBELI = {"username": "b", "role": "pembeli"}


# ── gate peran+permintaan ────────────────────────────────────────────────────
def test_boleh_isi_hanya_admin_dan_minta():
    assert ai._boleh_isi_stok_harga({"sertakan_stok_harga": True}, ADMIN) is True
    assert ai._boleh_isi_stok_harga({"sertakan_stok_harga": False}, ADMIN) is False
    assert ai._boleh_isi_stok_harga({}, ADMIN) is False              # admin tak minta → kosong
    assert ai._boleh_isi_stok_harga({"sertakan_stok_harga": True}, USER) is False   # user minta → tetap kosong
    assert ai._boleh_isi_stok_harga({"sertakan_stok_harga": True}, PEMBELI) is False


# ── flag benar disimpan di builder (per peran+permintaan) ────────────────────
@pytest.fixture
def _mock_walk(monkeypatch):
    monkeypatch.setattr(ai.epc_bom, "catalog_walk", lambda r, k: {
        "found": True, "frame_number": "FR1", "kategori_kode": "01",
        "lengkap": False, "jumlah_figure": 1, "jumlah_part": 3,
        "kategori_cocok": ["Kabin"], "figures": [{}], "incomplete": False})
    monkeypatch.setattr(ai.epc_weichai, "catalog_walk", lambda r, k: {
        "found": True, "frame_number": "FR1", "engine_model": "WP12",
        "lengkap": False, "jumlah_figure": 1, "jumlah_part": 3,
        "kategori_cocok": ["Blok"], "figures": [{}], "incomplete": False})


def _stashed(monkeypatch):
    box = {}
    real = ai_export.stash_builder
    monkeypatch.setattr(ai_export, "stash_builder",
                        lambda judul, builder, ext="xlsx": box.update(builder) or ("EID", "f." + ext))
    return box


def test_kategori_admin_minta_isi(monkeypatch, _mock_walk):
    box = _stashed(monkeypatch)
    r = ai._t_katalog_kategori(
        {"rangka": "R", "kategori": "kabin", "format": "excel", "sertakan_stok_harga": True}, ADMIN)
    assert r["found"] and box["isi_stok_harga"] is True
    assert r["stok_harga_diisi"] is True


def test_kategori_admin_tak_minta_kosong(monkeypatch, _mock_walk):
    box = _stashed(monkeypatch)
    r = ai._t_katalog_kategori({"rangka": "R", "kategori": "kabin", "format": "excel"}, ADMIN)
    assert box["isi_stok_harga"] is False and r["stok_harga_diisi"] is False


def test_kategori_user_minta_tetap_kosong(monkeypatch, _mock_walk):
    box = _stashed(monkeypatch)
    r = ai._t_katalog_kategori(
        {"rangka": "R", "kategori": "kabin", "format": "excel", "sertakan_stok_harga": True}, USER)
    assert box["isi_stok_harga"] is False and r["stok_harga_diisi"] is False


def test_mesin_user_minta_tetap_kosong(monkeypatch, _mock_walk):
    box = _stashed(monkeypatch)
    r = ai._t_katalog_mesin(
        {"rangka": "R", "kategori": "blok", "format": "excel", "sertakan_stok_harga": True}, USER)
    assert box["isi_stok_harga"] is False and r["stok_harga_diisi"] is False


def test_mesin_admin_minta_isi(monkeypatch, _mock_walk):
    box = _stashed(monkeypatch)
    r = ai._t_katalog_mesin(
        {"rangka": "R", "kategori": "blok", "format": "excel", "sertakan_stok_harga": True}, ADMIN)
    assert box["isi_stok_harga"] is True and r["stok_harga_diisi"] is True


# ── builder Excel: sel stok/harga kosong saat flag False, terisi saat True ────
def test_excel_builder_menahan_stok_harga(monkeypatch):
    figures = [{"nama": "Kabin", "kode": "1", "kategori": "Kabin", "svg": "",
                "items": [{"balon": 1, "pn": "PN1", "nama": "Bracket", "nama_cn": "",
                           "qty": 2, "pengganti": []}]}]
    monkeypatch.setattr(ai_export, "_katalog_source",
                        lambda r, k, s: ({"found": True, "frame_number": "FR1",
                                          "lengkap": False, "figures": figures}, lambda ref: None))
    monkeypatch.setattr(ai_export.part_index, "search_exact_pns",
                        lambda pns: [{"part_number": "PN1", "part_name": "Bracket",
                                      "stok": 9, "harga": "Rp 100"}])
    import openpyxl, io

    def _read(isi):
        data, _ = ai_export.katalog_excel("R", "kabin", "sinotruk", isi_stok_harga=isi)
        wb = openpyxl.load_workbook(io.BytesIO(data))
        ws = wb["Katalog"]
        # cari baris data PN1: kolom 2=PN, 5=Stok, 6=Harga
        for row in ws.iter_rows(values_only=True):
            if row and row[1] == "PN1":
                return row[4], row[5]
        return "NOTFOUND", None

    stok0, harga0 = _read(False)
    assert stok0 is None and harga0 is None      # default: KOSONG
    stok1, harga1 = _read(True)
    assert stok1 == 9 and harga1 == "Rp 100"     # admin minta: TERISI
