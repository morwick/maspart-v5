"""Anti-bocor tool-call (§3.5.5c) — model kadang MENULIS pemanggilan tool
sebagai teks (markup invoke/parameter) alih-alih field tool_calls API.

Fungsi yang diuji: _parse_leaked_tool_calls (parse → jalankan) dan
_strip_tool_markup (buang markup dari jawaban ke layar).
"""
from app.services import ai_assistant as ai

LEAKED = (
    'Saya carikan dulu.'
    '<tool_calls><invoke name="cari_part">'
    '<parameter name="q">kampas kopling</parameter>'
    '<parameter name="unit">NX400</parameter>'
    '</invoke>'
)


def test_parse_satu_invoke_dengan_argumen():
    calls = ai._parse_leaked_tool_calls(LEAKED)
    assert calls == [{"name": "cari_part",
                      "arguments": {"q": "kampas kopling", "unit": "NX400"}}]


def test_parse_dua_invoke_berurutan():
    txt = ('<invoke name="detail_part"><parameter name="part_number">WG2210040097</parameter></invoke>'
           '<invoke name="daftar_unit"></invoke>')
    calls = ai._parse_leaked_tool_calls(txt)
    assert [c["name"] for c in calls] == ["detail_part", "daftar_unit"]
    assert calls[0]["arguments"] == {"part_number": "WG2210040097"}
    assert calls[1]["arguments"] == {}


def test_teks_biasa_tanpa_markup_tidak_diparse():
    assert ai._parse_leaked_tool_calls("Stok part itu 5 pcs di Jakarta.") == []
    assert ai._parse_leaked_tool_calls("") == []


def test_strip_membuang_seluruh_rentang_markup():
    out = ai._strip_tool_markup(LEAKED)
    assert "invoke" not in out and "parameter" not in out
    assert "kampas kopling" not in out          # nilai parameter ikut dibuang
    assert out == "Saya carikan dulu."


def test_strip_mempertahankan_teks_sebelum_dan_sesudah():
    txt = 'Halo.<invoke name="x"><parameter name="a">1</parameter></invoke> Selesai.'
    assert ai._strip_tool_markup(txt) == "Halo. Selesai."


def test_strip_teks_bersih_tidak_berubah():
    s = "Jawaban normal dengan <b>markup html biasa</b> tetap utuh."
    assert ai._strip_tool_markup(s) == s
