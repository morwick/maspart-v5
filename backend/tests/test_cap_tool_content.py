"""_cap_tool_content: potong KEPALA + EKOR, bukan kepala saja.

Tool builder menaruh instruksi penyetir model (catatan/jawaban_wajib/catatan_cakupan)
di UJUNG dict → potong-kepala-saja menghapusnya senyap. Head+tail menjaga aturan itu
tetap sampai ke model.
"""
from app.services import ai_assistant as A


def test_pendek_tak_dipotong():
    s = '{"found":true,"catatan":"jangan mengarang"}'
    assert A._cap_tool_content(s) == s


def test_panjang_pertahankan_kepala_dan_ekor():
    ekor = '"catatan":"⛔ JANGAN mengarang PN di luar daftar"}'
    s = '{"found":true,"parts":[' + ("x" * 40000) + '],' + ekor
    out = A._cap_tool_content(s)
    assert len(out) <= A._MAX_TOOL_CONTENT
    assert out.startswith('{"found":true')          # kepala utuh
    assert out.endswith(ekor)                         # EKOR (instruksi) selamat
    assert "dipotong" in out and "di tengah" in out


def test_ekor_memuat_jawaban_wajib():
    s = ("A" * 30000) + '"jawaban_wajib":"sampaikan jujur tidak ada"'
    out = A._cap_tool_content(s)
    assert "jawaban_wajib" in out                     # tak lagi hilang
