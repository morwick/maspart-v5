"""Penjaga istilah lapangan: kapan argumen model DITIMPA, kapan TIDAK.

Guard ini lahir dari kasus "cucuk per" (model dua kali mengarang terjemahannya
sendiri). Tapi apa adanya ia juga menghukum model yang BENAR: pada pertanyaan
multi-part, satu trigger kamus yang kebetulan cocok sudah cukup untuk mengganti
kata kunci permintaan LAIN di kalimat yang sama.

Pembedanya BUKAN "kena part di katalog": "cross joint" itu nama part yang
sungguh ada — persis sebabnya insiden asli lolos pagar semacam itu. Pembeda yang
benar: apakah istilah model berakar pada kata yang DIKETIK user.
"""
from __future__ import annotations

import pytest

from app.services import ai_assistant as ai

KAMUS = [
    {"grup": "suspensi", "triggers": ["cucuk per", "per"],
     "keywords": ["spring pin", "leaf spring"]},
]


@pytest.fixture(autouse=True)
def _kamus(monkeypatch):
    monkeypatch.setattr(ai, "_load_sinonim_entries", lambda: list(KAMUS))


def test_istilah_karangan_tetap_ditimpa():
    """Insiden asli: user bilang 'cucuk per', model menulis 'cross joint' —
    tak satu katanya pun ada di pertanyaan → tafsiran sendiri → ditimpa."""
    args = {"query": "cross joint"}
    catatan = ai._paksa_istilah_kamus("cari_part", args, "carikan cucuk per dong")
    assert args["query"] == "cucuk per"
    assert "MENGGANTINYA" in catatan


def test_karangan_kedua_juga_ditimpa():
    args = {"query": "fuel injector"}
    ai._paksa_istilah_kamus("cari_part", args, "ada cucuk per?")
    assert args["query"] == "cucuk per"


def test_permintaan_kedua_tidak_dibajak():
    """Regresi 'cek per daun dan bearing roda': 'bearing' ADA di pertanyaan, jadi
    model sedang melayani permintaan KEDUA — bukan salah menerjemahkan 'per'."""
    args = {"query": "wheel bearing"}
    catatan = ai._paksa_istilah_kamus(
        "cari_part", args, "cek per daun dan bearing roda")
    assert args["query"] == "wheel bearing"          # TIDAK diganti
    assert "berakar pada kata user" in catatan
    assert "terpisah" in catatan.lower()             # model diberi tahu ada 2 hal


def test_kata_umum_tak_dianggap_akar():
    """'part'/'cari' muncul di mana-mana — tak membuktikan apa pun."""
    args = {"query": "cari part joint"}
    ai._paksa_istilah_kamus("cari_part", args, "cari part cucuk per")
    assert args["query"] == "cucuk per"


def test_model_selaras_kamus_tak_diganggu():
    args = {"query": "spring pin"}
    assert ai._paksa_istilah_kamus("cari_part", args, "cucuk per ada?") == ""
    assert args["query"] == "spring pin"


def test_array_tetap_ditambahkan_bukan_diganti():
    """Nilai array: istilah lain di daftar bisa saja sah → append, jangan ganti."""
    args = {"kata_kunci": ["cross joint", "bearing"]}
    ai._paksa_istilah_kamus("cari_part_di_unit", args, "cucuk per unit ini")
    assert args["kata_kunci"] == ["cross joint", "bearing", "cucuk per"]


def test_kata_kunci_ber_digit_tak_diganggu():
    """Kemungkinan Part Number — jangan disentuh sama sekali."""
    args = {"query": "WG9100443050"}
    assert ai._paksa_istilah_kamus("cari_part", args, "cucuk per") == ""
    assert args["query"] == "WG9100443050"


def test_tool_di_luar_daftar_tak_terpengaruh():
    args = {"query": "cross joint"}
    assert ai._paksa_istilah_kamus("detail_part", args, "cucuk per") == ""
    assert args["query"] == "cross joint"
