"""Dataset gejala → part (data/sinonim/gejala_map.json) menumpang jalur sinonim.

Kontraknya: skema SAMA dengan sinonim.json, digabung hanya di jalur BACA
(entries), tak pernah ikut tertulis saat admin menyimpan kamus, dan file yang
belum ada = fitur mati, bukan error.
"""
from __future__ import annotations

import json

import pytest

from app.services import sinonim


@pytest.fixture
def kamus(tmp_path, monkeypatch):
    """Arahkan sinonim ke folder sementara; kembalikan (tulis_kurasi, tulis_gejala)."""
    d = tmp_path / "sinonim"
    d.mkdir()
    monkeypatch.setattr(sinonim, "_file", lambda: d / "sinonim.json")
    monkeypatch.setattr(sinonim, "_file_gejala", lambda: d / "gejala_map.json")

    def tulis(nama, data):
        (d / nama).write_text(json.dumps(data), encoding="utf-8")
    return tulis


KURASI = [{"grup": "rem", "triggers": ["kampas rem"], "keywords": ["brake pad"]}]
GEJALA = [{"grup": "gejala:kampas-rem", "triggers": ["rem blong", "rem berdecit"],
           "keywords": ["brake pad", "brake lining"]}]


def test_file_gejala_absen_tak_mengubah_apa_pun(kamus):
    kamus("sinonim.json", KURASI)
    assert sinonim.entries() == KURASI


def test_gejala_digabung_di_jalur_baca(kamus):
    kamus("sinonim.json", KURASI)
    kamus("gejala_map.json", GEJALA)
    out = sinonim.entries()
    assert len(out) == 2
    assert out[0]["grup"] == "rem"                 # kurasi manusia tetap di depan
    assert out[1]["grup"] == "gejala:kampas-rem"


def test_gejala_tak_ikut_di_load_crud(kamus):
    """`load()` melayani CRUD admin — kalau dataset turunan ikut, penyimpanan
    berikutnya akan menuliskannya balik ke sinonim.json."""
    kamus("sinonim.json", KURASI)
    kamus("gejala_map.json", GEJALA)
    assert sinonim.load() == KURASI


def test_grup_bentrok_dimenangkan_kamus_kurasi(kamus):
    kamus("sinonim.json", [{"grup": "rem", "triggers": ["kampas rem"],
                            "keywords": ["brake pad"]}])
    kamus("gejala_map.json", [{"grup": "rem", "triggers": ["rem blong"],
                               "keywords": ["salah"]}])
    out = sinonim.entries()
    assert len(out) == 1
    assert out[0]["keywords"] == ["brake pad"]


def test_gejala_ikut_ekspansi_query(kamus):
    """Manfaat sesungguhnya: keluhan lapangan kini mendarat di istilah katalog
    tanpa satu baris kode runtime baru."""
    kamus("sinonim.json", [])
    kamus("gejala_map.json", GEJALA)
    terms, matched = sinonim.expand_query("rem blong terus")
    assert "brake pad" in terms
    assert "rem blong" in matched


def test_file_gejala_korup_diabaikan(kamus, tmp_path):
    kamus("sinonim.json", KURASI)
    (tmp_path / "sinonim" / "gejala_map.json").write_text("{rusak", encoding="utf-8")
    assert sinonim.entries() == KURASI


# ── Mutu trigger (aturan di tools/build_gejala_map.py) ──────────────────────

@pytest.fixture(scope="module")
def builder():
    import importlib.util
    from pathlib import Path
    p = Path(__file__).resolve().parents[1] / "tools" / "build_gejala_map.py"
    spec = importlib.util.spec_from_file_location("build_gejala_uji", p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_kata_sifat_tunggal_ditolak(builder):
    """expand_query menyuntikkan keyword SETIAP grup yang cocok, jadi satu
    trigger generik mencemari pencarian. Uji nyata: 'bunyi' menarik
    'Right door', 'berat' menarik 'fuel filter'."""
    for buruk in ("bunyi", "berat", "bocor", "keras", "mesin", "asap", "rusak"):
        assert builder._trigger_layak(buruk) is False, buruk


def test_gejala_khas_tunggal_diterima(builder):
    """Yang paling sering diketik montir justru pendek — aturan panjang minimum
    yang tumpul (>=5 huruf) sempat membuang 'loyo'."""
    for baik in ("loyo", "ngelitik", "ngebul", "brebet", "selip", "ngempos"):
        assert builder._trigger_layak(baik) is True, baik


def test_frasa_dua_kata_selalu_diterima(builder):
    """Konteksnya sudah membatasi sendiri — 'bunyi gluduk' tak seperti 'bunyi'."""
    for baik in ("bunyi gluduk", "asap hitam", "setir berat", "rem blong"):
        assert builder._trigger_layak(baik) is True, baik
