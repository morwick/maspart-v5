"""Penjaga istilah lapangan di _run_tool (2026-07-22).

Kasus produksi "cucuk per" (kamus: spring pin, grup suspensi): model DUA KALI
mengarang terjemahannya sendiri — "cross joint" lalu "fuel injector" — meski
aturan prompt + [KAMUS ISTILAH GILIRAN INI] melarang. Soft guard kalah, maka
ditegakkan deterministik: kata kunci model yang tak memuaskan satu grup kamus
pun DITIMPA istilah mentah user, dan model diberi tahu lewat `catatan_istilah`.

Pertanyaan user dititipkan chat loop lewat kunci server `_q_user` (pola
_sheet_id) — arity _run_tool tetap 4 agar test lama yang mem-patch tak pecah.
"""
import pytest

from app.services import ai_assistant as ai

ADMIN = {"username": "admin", "role": "admin"}
Q = "cekkan part cucuk per RT110061"

_ENTRIES = [
    {"grup": "suspensi", "triggers": ["pen per", "cucuk per", "pin per"],
     "keywords": ["spring pin"]},
    {"grup": "per", "triggers": ["per"], "keywords": ["leaf spring", "spring"]},
    {"grup": "rem", "triggers": ["kampas rem"],
     "keywords": ["brake lining", "friction plate"]},
]


@pytest.fixture
def dunia(monkeypatch):
    """Kamus dipatok + handler cari_part_di_unit dipalsukan (tangkap args)."""
    monkeypatch.setattr(ai, "_load_sinonim_entries", lambda: _ENTRIES)
    tangkap = {}

    def fake(args, user):
        tangkap["args"] = dict(args)
        return {"found": True, "hasil": ["x"], "catatan": "ok"}
    monkeypatch.setitem(ai._DISPATCH, "cari_part_di_unit", fake)
    return tangkap


def _panggil(kata, q=Q):
    return ai._run_tool("cari_part_di_unit",
                        {"rangka": "RT110061", "kata_kunci": kata, "_q_user": q},
                        ADMIN)


def test_karangan_model_ditimpa_istilah_user(dunia):
    r = _panggil("fuel injector")
    assert dunia["args"]["kata_kunci"] == "cucuk per"
    assert "MENGGANTINYA" in r["catatan_istilah"]
    assert "spring pin" in r["catatan_istilah"]
    assert list(r.keys())[-1] == "catatan_istilah"   # ekor — selamat dari cap


def test_q_user_tak_pernah_sampai_ke_handler(dunia):
    """Kunci server _q_user WAJIB di-pop sebelum handler melihat args."""
    _panggil("fuel injector")
    assert "_q_user" not in dunia["args"]


def test_model_selaras_keyword_kamus_tak_disentuh(dunia):
    _panggil("spring pin")
    assert dunia["args"]["kata_kunci"] == "spring pin"


def test_istilah_mentah_diteruskan_tak_disentuh(dunia):
    r = _panggil("cucuk per")
    assert dunia["args"]["kata_kunci"] == "cucuk per"
    assert "catatan_istilah" not in r


def test_kata_kunci_ber_digit_dianggap_pn(dunia):
    """PN eksplisit dari model tak boleh diganggu penjaga."""
    _panggil("WG9100520065")
    assert dunia["args"]["kata_kunci"] == "WG9100520065"


def test_question_tanpa_trigger_tak_disentuh(dunia):
    _panggil("fuel injector", q="cek injector unit itu")
    assert dunia["args"]["kata_kunci"] == "fuel injector"


def test_tanpa_q_user_tak_disentuh(dunia):
    """Pemanggil lama (test/skrip) tanpa _q_user: perilaku persis seperti dulu."""
    r = ai._run_tool("cari_part_di_unit",
                     {"rangka": "R", "kata_kunci": "fuel injector"}, ADMIN)
    assert dunia["args"]["kata_kunci"] == "fuel injector"
    assert "catatan_istilah" not in r


def test_trigger_terpanjang_yang_dipakai(dunia):
    """'cucuk per' (spring pin) menang atas 'per' (leaf spring)."""
    r = _panggil("cross joint")
    assert dunia["args"]["kata_kunci"] == "cucuk per"
    assert "spring pin" in r["catatan_istilah"]


def test_satu_grup_terpuaskan_grup_lain_dibiarkan(dunia):
    """User sebut 2 part; model sedang mencari salah satunya → jangan menimpa."""
    _panggil("brake lining", q="cek kampas rem dan cucuk per RT110061")
    assert dunia["args"]["kata_kunci"] == "brake lining"


def test_tool_di_luar_peta_tak_disentuh(monkeypatch):
    monkeypatch.setattr(ai, "_load_sinonim_entries", lambda: _ENTRIES)
    tangkap = {}

    def fake(args, user):
        tangkap["args"] = dict(args)
        return {"found": True}
    monkeypatch.setitem(ai._DISPATCH, "diagnosa", fake)
    r = ai._run_tool("diagnosa", {"keluhan": "mesin pincang", "_q_user": Q}, ADMIN)
    assert tangkap["args"]["keluhan"] == "mesin pincang"
    assert "_q_user" not in tangkap["args"]
    assert "catatan_istilah" not in r


def test_kamus_rusak_tak_mematikan_tool(dunia, monkeypatch):
    def boom():
        raise RuntimeError("kamus rusak")
    monkeypatch.setattr(ai, "_load_sinonim_entries", boom)
    r = _panggil("fuel injector")
    assert r["found"] is True                      # tool tetap jalan
    assert dunia["args"]["kata_kunci"] == "fuel injector"
