"""Laporan mingguan SINYAL PEMBELIAN — dari kanal TARIKAN jadi DORONGAN.

Kenapa ada (audit 1.189 giliran, 17 Jul–16 Agu 2026): modul
permintaan_tak_terlayani sudah matang sejak 2026-08-09 — ia memisahkan 45%
sinyal beli yang ASLI dari salah ketik & miss basi. Tapi tool-nya dipanggil NOL
kali dalam 30 hari, sementara `pengganti_part:nf` justru kegagalan tool NOMOR
SATU (50 kejadian). Datanya menumpuk dan tak pernah dilihat siapa pun, karena
informasi yang sifatnya DORONGAN dipasang di kanal TARIKAN.
"""
import pytest

from app.services import permintaan_tak_terlayani as P


@pytest.fixture
def analisa_palsu(monkeypatch):
    """Pasang analisa() palsu; `dipakai` merekam argumen yang BENAR-BENAR dikirim
    teks_laporan (bukan default fungsi)."""
    dipakai: dict = {}

    def pasang(**d):
        def palsu(**kw):
            dipakai.clear()
            dipakai.update(kw)
            return dict(d)
        monkeypatch.setattr(P, "analisa", palsu)
        return dipakai
    return pasang


def test_tak_ada_sinyal_TIDAK_mengirim_pesan(analisa_palsu):
    """⛔ Pesan kosong tiap minggu melatih orang mengabaikan kanalnya — dan kanal
    yang diabaikan persis penyakit yang sedang kita obati."""
    analisa_palsu(permintaan_tak_terlayani=[], jumlah_kemungkinan_salah_ketik=0)
    assert P.teks_laporan() == ""


def test_laporan_memuat_barang_dan_berapa_kali(analisa_palsu):
    analisa_palsu(
        permintaan_tak_terlayani=[
            {"dicari": "seal roda depan", "berapa_kali": 7, "terakhir_dicari_hari_lalu": 2.0},
            {"dicari": "WG9100360501", "berapa_kali": 3, "terakhir_dicari_hari_lalu": 11.4},
        ],
        total_kejadian_tak_terlayani=10,
        jumlah_kemungkinan_salah_ketik=0)
    t = P.teks_laporan()
    assert "seal roda depan" in t and "7×" in t
    assert "WG9100360501" in t
    assert "10 pencarian" in t


def test_salah_ketik_DISEBUT_agar_daftar_tak_terbaca_sbg_peluang_semua(analisa_palsu):
    """35% pencarian nihil adalah salah ketik. Tanpa angka ini, daftar di atas
    terbaca seolah seluruhnya peluang jualan — dan membelinya justru keliru."""
    analisa_palsu(
        permintaan_tak_terlayani=[{"dicari": "x", "berapa_kali": 2,
                                   "terakhir_dicari_hari_lalu": 1.0}],
        total_kejadian_tak_terlayani=2,
        jumlah_kemungkinan_salah_ketik=9)
    t = P.teks_laporan()
    assert "9 pencarian nihil lain" in t
    assert "JANGAN dibeli" in t


def test_ambang_minimal_diteruskan_ke_analisa(analisa_palsu):
    """Dicari SEKALI belum tentu sinyal — ambangnya harus benar-benar dipakai,
    bukan sekadar tertulis di konstanta."""
    dipakai = analisa_palsu(permintaan_tak_terlayani=[],
                            jumlah_kemungkinan_salah_ketik=0)
    P.teks_laporan()
    assert dipakai["min_kejadian"] == P._LAPORAN_MIN_KEJADIAN
    assert P._LAPORAN_MIN_KEJADIAN >= 2


def test_analisa_meledak_tidak_menjatuhkan_apa_pun(monkeypatch):
    def rusak(**kw):
        raise RuntimeError("indeks part rusak")
    monkeypatch.setattr(P, "analisa", rusak)
    assert P.teks_laporan() == ""


def test_tanpa_telegram_scheduler_tidak_jalan(monkeypatch):
    from app.services import notify
    monkeypatch.setattr(notify, "available", lambda: False)
    monkeypatch.setattr(P, "_laporan_started", False)
    assert P.start_laporan_mingguan() is False


def test_scheduler_idempoten(monkeypatch):
    from app.services import notify
    monkeypatch.setattr(notify, "available", lambda: True)
    monkeypatch.setattr(P, "_laporan_started", False)
    jalan = []
    monkeypatch.setattr(P._threading, "Thread",
                        lambda **kw: type("T", (), {"start": lambda s: jalan.append(1)})())
    assert P.start_laporan_mingguan() is True
    assert P.start_laporan_mingguan() is False   # dipanggil dua kali = satu thread
    assert len(jalan) == 1
