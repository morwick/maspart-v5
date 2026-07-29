"""Memori sesi asisten (services/ai_session.py).

Fokus: batas-batas yang menjaga ingatan TIDAK berubah jadi kebocoran atau
kebohongan — scoping antar-user, kedaluwarsa, kuota, dan validasi id yang datang
dari klien.
"""
from __future__ import annotations

import pytest

from app.services import ai_session


@pytest.fixture(autouse=True)
def _bersih():
    ai_session._reset()
    yield
    ai_session._reset()


CONV = "abc12345-abcd-4321"


def test_merge_lalu_get():
    assert ai_session.merge("budi", CONV, rangka=["RT108966"],
                            pn={"WG9100443050": "brake pad"}, nums=["12"],
                            fakta=["[RT108966] brake pad"]) is True
    memo = ai_session.get("budi", CONV)
    assert memo["rangka"] == ["RT108966"]
    assert "WG9100443050" in memo["pn"]
    assert memo["nums"] == ["12"]
    assert memo["fakta"] == ["[RT108966] brake pad"]


def test_username_tak_peka_huruf_besar():
    ai_session.merge("Budi", CONV, pn=["AAA111"])
    assert ai_session.get("budi", CONV) is not None
    assert ai_session.get("BUDI", CONV) is not None


def test_percakapan_user_lain_tak_terlihat():
    """Ingatan HARUS discoped per-user: kalau tidak, PN/rangka satu pelanggan
    bisa di-ground di percakapan pelanggan lain."""
    ai_session.merge("budi", CONV, pn=["AAA111"])
    assert ai_session.get("siti", CONV) is None


def test_conversation_id_tak_sah_diabaikan():
    """conversation_id datang dari klien → input tak tepercaya. Bentuk aneh
    tidak boleh error, cukup mematikan memori sesi (turun ke perilaku lama)."""
    for buruk in ("", "pendek", "a" * 65, "ada spasi", "titik.koma;", "../../etc"):
        assert ai_session.merge("budi", buruk, pn=["AAA111"]) is False
        assert ai_session.get("budi", buruk) is None


def test_kedaluwarsa_dibuang(monkeypatch):
    ai_session.merge("budi", CONV, pn=["AAA111"])
    monkeypatch.setattr(ai_session, "_TTL_SEC", -1)
    assert ai_session.get("budi", CONV) is None


def test_get_mengembalikan_salinan():
    """Pemanggil di jalur chat memutasi apa yang ia terima; stash tak boleh ikut."""
    ai_session.merge("budi", CONV, rangka=["RT108966"], pn={"AAA111": ""})
    memo = ai_session.get("budi", CONV)
    memo["rangka"].append("PALSU")
    memo["pn"]["BBB222"] = ""
    lagi = ai_session.get("budi", CONV)
    assert lagi["rangka"] == ["RT108966"]
    assert "BBB222" not in lagi["pn"]


def test_pn_lru_dan_plafon(monkeypatch):
    monkeypatch.setattr(ai_session, "_CAP_PN", 3)
    ai_session.merge("budi", CONV, pn=["A1", "B2", "C3"])
    ai_session.merge("budi", CONV, pn=["D4"])
    memo = ai_session.get("budi", CONV)
    assert list(memo["pn"]) == ["D4", "A1", "B2"]      # terbaru di depan, ekor terpotong


def test_slot_terbaru_menang():
    ai_session.merge("budi", CONV, rangka=["LAMA0001"])
    ai_session.merge("budi", CONV, rangka=["BARU0002"])
    assert ai_session.get("budi", CONV)["rangka"][0] == "BARU0002"


def test_plafon_rangka_dan_fakta(monkeypatch):
    monkeypatch.setattr(ai_session, "_CAP_FAKTA", 2)
    ai_session.merge("budi", CONV, rangka=["A", "B", "C"], fakta=["f1", "f2", "f3"])
    memo = ai_session.get("budi", CONV)
    assert len(memo["rangka"]) == ai_session._CAP_RANGKA
    assert memo["fakta"] == ["f1", "f2"]


def test_kuota_per_user_menggusur_milik_sendiri(monkeypatch):
    """Kuota per-user DULU — pelajaran ai_sheet: pagar global saja membuat
    percakapan user lain menggusur ingatan user ini."""
    monkeypatch.setattr(ai_session, "_MAX_PER_USER", 2)
    for i in range(3):
        ai_session.merge("budi", f"conv-000{i}-xxxx", pn=[f"P{i}"])
    ai_session.merge("siti", "conv-siti-9999", pn=["S0"])
    assert ai_session.get("budi", "conv-0000-xxxx") is None      # tertua tergusur
    assert ai_session.get("budi", "conv-0002-xxxx") is not None
    assert ai_session.get("siti", "conv-siti-9999") is not None  # user lain aman


def test_clear():
    ai_session.merge("budi", CONV, pn=["AAA111"])
    assert ai_session.clear("budi", CONV) is True
    assert ai_session.get("budi", CONV) is None
    assert ai_session.clear("budi", CONV) is False


def test_pn_dinormalkan_huruf_besar():
    ai_session.merge("budi", CONV, pn=["wg9100443050"])
    assert "WG9100443050" in ai_session.get("budi", CONV)["pn"]
