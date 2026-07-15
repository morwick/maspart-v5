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


# ── P9.3: hasil tool BOCOR disuntik sebagai role:system (bukan user) ──────────

def test_hasil_tool_bocor_disuntik_sebagai_system(monkeypatch):
    monkeypatch.setattr(ai, "_system_prompt", lambda user: "sys")
    monkeypatch.setattr(ai, "_tool_specs", lambda user, sheet_id="": [])
    monkeypatch.setattr(ai, "_unit_name_tokens", lambda: set())
    monkeypatch.setattr(ai, "_prefetch_epc_rangka", lambda h: None)
    seen = {"msgs": None}
    seq = [
        {"choices": [{"message": {"content": '<invoke name="daftar_unit"></invoke>'},
                      "finish_reason": "stop"}]},                    # model bocorkan tool sbg teks
        {"choices": [{"message": {"content": "Ada beberapa unit."},
                      "finish_reason": "stop"}]},
    ]
    calls = {"n": 0}

    def fake(messages, tools, max_tokens=6000):
        seen["msgs"] = [dict(m) for m in messages]
        c = seq[min(calls["n"], len(seq) - 1)]
        calls["n"] += 1
        return c

    monkeypatch.setattr(ai, "_post_chat", fake)
    monkeypatch.setattr(ai, "_run_tool", lambda n, a, u, sheet_id="": {"found": True, "units": ["A"]})

    ai.chat({"username": "t", "role": "user"}, [{"role": "user", "content": "unit apa saja?"}])

    hasil = [m for m in seen["msgs"] if "[HASIL TOOL" in (m.get("content") or "")]
    assert hasil and all(m["role"] == "system" for m in hasil)       # disuntik sbg system
    assert not any(m["role"] == "user" and "[HASIL TOOL" in (m.get("content") or "")
                   for m in seen["msgs"])
