"""VIN bentuk janggal (16 char / check digit salah) dijelaskan, bukan cuma
'tidak terbaca' — audit ai_chat_log 2026-08-28."""
from app.services import epc
from app.services import ai_assistant as ai

VIN_OK = "LZZ5EXSF9RJ380449"   # 17 char


def _cd_benar(v):
    return v[:8] + epc.check_digit(v) + v[9:]


def test_16_karakter_kurang_satu():
    fb = epc.periksa_bentuk_rangka("LZZ5EXSF9RJ38044")
    assert fb and fb["panjang"] == 16 and fb["selisih"] == -1
    assert "16 karakter" in fb["pesan"] and "KURANG 1" in fb["pesan"]


def test_17_karakter_check_digit_salah_dan_benar():
    v = _cd_benar(VIN_OK)
    assert epc.periksa_bentuk_rangka(v) is None
    salah = v[:8] + ("0" if v[8] != "0" else "1") + v[9:]
    fb = epc.periksa_bentuk_rangka(salah)
    assert fb and fb.get("check_digit_salah") and "check digit" in fb["pesan"]


def test_frame_8_char_dan_kosong_wajar():
    assert epc.periksa_bentuk_rangka("RJ380449") is None
    assert epc.periksa_bentuk_rangka("") is None
    assert epc.periksa_bentuk_rangka("LZZ5EXSF9RJ380449X") and \
        "KELEBIHAN 1" in epc.periksa_bentuk_rangka("LZZ5EXSF9RJ380449X")["pesan"]


def test_lookup_dan_cek_kendaraan_membawa_format_rangka(monkeypatch):
    monkeypatch.setattr(epc, "get_config", lambda r: {})
    r = epc.lookup("LZZ5EXSF9RJ38044")
    assert r["found"] is False and r["format_rangka"]["panjang"] == 16
    assert "16 karakter" in r["catatan"]
    t = ai._t_cek_kendaraan({"rangka": "LZZ5EXSF9RJ38044"}, {"username": "mas", "role": "user"})
    assert t["catatan"].startswith("⚠️") and "16 karakter" in t["catatan"]
    # jaringan gagal → tidak menuduh bentuk (biar tak dobel sebab)
    monkeypatch.setattr(epc, "get_config", lambda r: {"_err": "timeout"})
    assert "format_rangka" not in epc.lookup("LZZ5EXSF9RJ38044")
