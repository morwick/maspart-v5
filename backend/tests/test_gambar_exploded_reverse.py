"""gambar_exploded: PN + rangka sudah cukup, kategori tak lagi wajib.

Log produksi: "berikan gambar teknis 612600070465 dari no rangka SJ383550" →
8 ronde, KOSONG. Penyebabnya berlapis: handler MEWAJIBKAN `kategori` padahal
spec tool-nya sendiri menyuruh mengosongkannya, jadi ronde pertama terbuang
hanya untuk bertanya "kategori apa?" — lalu tebakan kategorinya sering meleset
('not_in_category').

Jalur terbalik (reverse_find_in_unit → figure_for_instance) sudah lama dipakai
UI web dan tak butuh kategori sama sekali. Nol jaringan di test ini.
"""
from __future__ import annotations

import pytest

from app.services import ai_assistant as ai

ADMIN = {"username": "admin", "role": "admin"}
ARGS = {"rangka": "SJ383550", "pn": "612600070465"}


@pytest.fixture(autouse=True)
def _stash(monkeypatch):
    monkeypatch.setattr(ai.ai_export, "stash_builder",
                        lambda judul, spec, ext="png": ("img1", "gambar.png"))


def _pasang_reverse(monkeypatch, instances, figure):
    monkeypatch.setattr(ai.epc_bom, "reverse_find_in_unit",
                        lambda r, p: {"found": bool(instances), "frame_number": "SJ383550",
                                      "instances": instances})
    monkeypatch.setattr(ai.epc_bom, "figure_for_instance",
                        lambda r, inst, p: figure)


_INST = [{"parent_pn": "AZ111", "parent_nama": "Fuel system",
          "part_id": 9, "part_list_id": 8, "root_id": 7}]
_FIG = {"found": True, "svg": "fig.svg", "balon": 12, "qty": 1,
        "nama": "Common rail assembly",
        "items_ringkas": [{"balon": 12, "pn": "612600070465", "nama": "Rail"}]}


def test_tanpa_kategori_langsung_dapat_gambar(monkeypatch):
    _pasang_reverse(monkeypatch, _INST, _FIG)
    out = ai._gambar_exploded_atlas_impl(dict(ARGS), ADMIN)
    assert out["found"] is True
    assert out["sumber"] == "reverse"
    assert out["gambar"][0]["image_id"] == "img1"
    assert "pilihan_kategori" not in out          # tak lagi bertanya kategori


def test_daftar_balon_ikut(monkeypatch):
    """Konteks follow-up 'no N itu apa' harus tetap tersedia seperti jalur lama."""
    _pasang_reverse(monkeypatch, _INST, _FIG)
    out = ai._gambar_exploded_atlas_impl(dict(ARGS), ADMIN)
    assert out["daftar_balon_gambar"][0]["pn"] == "612600070465"


def test_balon_diminta_disorot(monkeypatch):
    _pasang_reverse(monkeypatch, _INST, _FIG)
    out = ai._gambar_exploded_atlas_impl({**ARGS, "balon": 5}, ADMIN)
    assert out["balon_disorot"] == 5
    assert out["gambar"][0]["balon"] == 5


def test_jatuh_ke_jalur_lama_bila_reverse_nihil(monkeypatch):
    """Jalur terbalik MENAMBAH peluang, bukan menggantikan — bila ia tak
    menemukan apa pun, jalur kategori lama harus tetap dicoba."""
    _pasang_reverse(monkeypatch, [], {"found": False})
    dipanggil = {}

    def _lama(rangka, pn, kategori):
        dipanggil["kategori"] = kategori
        return {"found": False, "_err": "not_found"}

    monkeypatch.setattr(ai.epc_bom, "exploded_figures", _lama)
    ai._gambar_exploded_atlas_impl({**ARGS, "kategori": "mesin"}, ADMIN)
    assert dipanggil["kategori"] == "mesin"


def test_tanpa_kategori_dan_reverse_gagal_baru_bertanya(monkeypatch):
    _pasang_reverse(monkeypatch, [], {"found": False})
    out = ai._gambar_exploded_atlas_impl(dict(ARGS), ADMIN)
    assert out["found"] is False
    assert "pilihan_kategori" in out


def test_masalah_token_dilaporkan_jujur(monkeypatch):
    """Token EPC bermasalah tak boleh disamarkan jadi 'kategori apa?' — pesan
    yang menyesatkan membuat user & model mengejar hal yang salah."""
    monkeypatch.setattr(ai.epc_bom, "reverse_find_in_unit",
                        lambda r, p: {"found": False, "_err": "token_expired"})
    out = ai._gambar_exploded_atlas_impl(dict(ARGS), ADMIN)
    assert out["found"] is False
    assert out.get("_token_issue") is True


def test_flag_mati_kembali_ke_jalur_lama(monkeypatch):
    mati = ai.get_settings().model_copy(update={"epc_exploded_reverse": False})
    monkeypatch.setattr(ai, "get_settings", lambda: mati)

    def _jangan(*a, **k):
        pytest.fail("jalur terbalik harus MATI saat flag off")

    monkeypatch.setattr(ai.epc_bom, "reverse_find_in_unit", _jangan)
    out = ai._gambar_exploded_atlas_impl(dict(ARGS), ADMIN)
    assert "pilihan_kategori" in out       # kembali menanyakan kategori


def test_rangka_atau_pn_kosong_tetap_ditolak():
    assert "error" in ai._gambar_exploded_atlas_impl({"pn": "X"}, ADMIN)
    assert "error" in ai._gambar_exploded_atlas_impl({"rangka": "SJ383550"}, ADMIN)
