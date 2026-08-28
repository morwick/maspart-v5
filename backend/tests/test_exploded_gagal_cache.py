"""gambar_exploded: render yang baru saja GAGAL (infra) tidak dipanggil ulang
selama jendela cache negatif — audit ai_chat_log 2026-08-10 (3× beruntun)."""
import pytest

from app.services import ai_assistant as ai

U = {"username": "admin", "role": "admin"}


@pytest.fixture(autouse=True)
def bersih():
    ai._exploded_gagal._d.clear()
    yield
    ai._exploded_gagal._d.clear()


def test_gagal_infra_tidak_diulang(monkeypatch):
    n = {"k": 0}

    def atlas(args, user):
        n["k"] += 1
        return {"found": False, "error": "Gagal menghubungi server EPC (jaringan). Coba lagi."}

    monkeypatch.setattr(ai, "_gambar_exploded_atlas_impl", atlas)
    monkeypatch.setattr(ai, "_gambar_exploded_mesin_impl", lambda a, u: {"found": False, "error": "x"})
    a = {"pn": "WG9100032311", "rangka": "RJ380449", "kategori": "Kabin"}
    r1 = ai._t_gambar_exploded(a, U)
    assert n["k"] == 1 and ai._tool_fail_kind(r1) == "err"
    r2 = ai._t_gambar_exploded(a, U)
    assert n["k"] == 1                       # TIDAK memanggil impl lagi
    assert "SUDAH GAGAL" in r2["catatan"] and ai._tool_fail_kind(r2) == "err"
    # argumen beda → dicoba (kunci berbeda)
    ai._t_gambar_exploded({**a, "pn": "WG9100032314"}, U)
    assert n["k"] == 2


def test_nf_dan_sukses_tidak_dicache(monkeypatch):
    n = {"k": 0}

    def atlas(args, user):
        n["k"] += 1
        return {"found": False, "error": "PN tak ditemukan di Atlas untuk unit ini."}

    monkeypatch.setattr(ai, "_gambar_exploded_atlas_impl", atlas)
    monkeypatch.setattr(ai, "_gambar_exploded_mesin_impl", lambda a, u: {"found": False})
    a = {"pn": "WG9100032311", "rangka": "RJ380449", "kategori": "Kabin"}
    ai._t_gambar_exploded(a, U); ai._t_gambar_exploded(a, U)
    assert n["k"] == 2 and len(ai._exploded_gagal) == 0
