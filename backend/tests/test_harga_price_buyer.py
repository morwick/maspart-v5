"""Harga jual = ACCURATE SAJA. harga.xlsx TIDAK PERNAH dipakai (aturan pemilik).

Dulu alur order memakai harga.xlsx sementara etalase memajang harga Accurate, sehingga
part yang harganya hanya ada di Accurate (mis. WG9725584002 Rp 80.000) tampil berharga
di toko tapi ditolak saat checkout, dan part yang harganya beda di dua sumber ditagih
beda dari yang dipajang. Sekarang satu sumber saja — dan bila Accurate tak punya
harganya, part itu memang TIDAK bisa dibeli (bukan jatuh ke Excel).
"""
import pytest

from app.services import harga


@pytest.fixture(autouse=True)
def excel_punya_harga(monkeypatch):
    """harga.xlsx SELALU punya harga di semua test ini — untuk membuktikan bahwa
    harga itu tak pernah dipakai."""
    monkeypatch.setattr(harga, "price_for", lambda pn: (50_000, "Nama Excel"))


def test_harga_dari_accurate(monkeypatch):
    monkeypatch.setattr("app.services.accurate.stock_full",
                        lambda pn: {"price": 80_000, "name": "Nama Accurate"})
    assert harga.price_for_buyer("WG9725584002") == (80_000, "Nama Accurate")


def test_accurate_tak_punya_harga_TIDAK_jatuh_ke_excel(monkeypatch):
    monkeypatch.setattr("app.services.accurate.stock_full", lambda pn: None)
    assert harga.price_for_buyer("PN-X") == (0, "")      # ⛔ bukan (50_000, ...)


def test_accurate_berharga_nol_tak_bisa_dibeli(monkeypatch):
    monkeypatch.setattr("app.services.accurate.stock_full",
                        lambda pn: {"price": 0, "name": "Nama Accurate"})
    assert harga.price_for_buyer("PN-X") == (0, "")


def test_indeks_accurate_error_tak_membocorkan_harga_excel(monkeypatch):
    """Accurate bermasalah → part tak bisa dibeli, BUKAN dijual dengan harga Excel."""
    def _meledak(pn):
        raise RuntimeError("indeks Accurate belum siap")

    monkeypatch.setattr("app.services.accurate.stock_full", _meledak)
    assert harga.price_for_buyer("PN-X") == (0, "")
