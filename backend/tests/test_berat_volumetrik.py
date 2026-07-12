"""Ongkir memakai berat TERTAGIH = max(berat asli, berat volumetrik).

Kurir domestik menagih yang lebih besar antara berat asli dan volumetrik
(p×l×t ÷ 6000). Dulu aplikasi hanya mengirim berat asli ke RajaOngkir, jadi barang
besar-ringan ditagih terlalu murah ke pembeli dan selisihnya ditanggung penjual di
konter. Contoh nyata (dimensi resmi SIMS): filter WG9725190102 berat 3,7 kg tapi
46,5 × 30,5 × 30,2 cm = 7,1 kg volumetrik — hampir dua kali lipat.
"""
import pytest

from app.services import harga, sims, sims_weights


def test_volumetrik_dihitung_dari_dimensi_sims():
    info = {"lengthCm": 46.5, "widthCm": 30.5, "heightCm": 30.2, "roughWeightKg": 3.7}
    assert sims._vol_grams(info) == 7139        # 46,5×30,5×30,2 / 6000 = 7,139 kg


def test_dimensi_tak_lengkap_volumetrik_nol():
    assert sims._vol_grams({"lengthCm": 46.5, "widthCm": 0, "heightCm": 30.2}) == 0
    assert sims._vol_grams({}) == 0


@pytest.fixture
def indeks(monkeypatch):
    """Indeks berat tiruan: filter (besar-ringan) & baut (kecil-berat)."""
    data = {
        "FILTER-BESAR": {"g": 3_700, "vol": 7_139},
        "BAUT-KECIL": {"g": 2_000, "vol": 300},
        "TANPA-DIMENSI": {"g": 1_500, "vol": 0},
    }
    monkeypatch.setattr(sims_weights, "get", lambda pn: data.get(pn.upper(), {}).get("g", 0))
    monkeypatch.setattr(sims_weights, "get_volumetric",
                        lambda pn: data.get(pn.upper(), {}).get("vol", 0))
    monkeypatch.setattr(sims_weights, "dim_known", lambda pn: pn.upper() in data)
    monkeypatch.setattr(harga, "weight_for",
                        lambda pn, allow_remote=False: data.get(pn.upper(), {}).get("g", 0))


def test_barang_besar_ringan_ditagih_volumetriknya(indeks):
    assert harga.shipping_weight_for("FILTER-BESAR") == 7_139     # bukan 3.700


def test_barang_kecil_berat_tetap_pakai_berat_asli(indeks):
    assert harga.shipping_weight_for("BAUT-KECIL") == 2_000       # volumetrik lebih kecil


def test_tanpa_dimensi_jatuh_ke_berat_asli(indeks):
    assert harga.shipping_weight_for("TANPA-DIMENSI") == 1_500


def test_total_ongkir_memakai_berat_tertagih(indeks):
    total = harga.total_weight_grams([("FILTER-BESAR", 2), ("BAUT-KECIL", 1)], 1000)
    assert total == 7_139 * 2 + 2_000


# ── Indeks persisten: format lama tetap terbaca, dimensi dilengkapi belakangan ──
def test_format_lama_angka_polos_terbaca(monkeypatch):
    monkeypatch.setattr(sims_weights, "_map", {"PN-1": sims_weights._norm(2500)})
    assert sims_weights.get("PN-1") == 2500
    assert sims_weights.get_volumetric("PN-1") == 0
    assert sims_weights.dim_known("PN-1") is False   # → warmer akan melengkapinya


def test_dimensi_nol_dianggap_sudah_dicek(monkeypatch):
    """0 = SIMS memang tak punya dimensi (jangan di-fetch ulang tiap siklus);
    None = belum pernah dicek."""
    monkeypatch.setattr(sims_weights, "_map", {"PN-2": sims_weights._norm({"g": 900, "vol": 0})})
    assert sims_weights.dim_known("PN-2") is True
    assert sims_weights.get_volumetric("PN-2") == 0
