"""Hemat token in-turn: pangkas isi HASIL TOOL ronde lama saat messages dikirim
ulang tiap panggilan API. role:tool + tool_call_id WAJIB dipertahankan (validitas
pasangan API); ronde terbaru tetap utuh. Grounding tak terpengaruh (ditangkap ke
state samping saat append, bukan dibaca ulang dari messages)."""
from app.services import ai_assistant as A


def _msgs():
    return [
        {"role": "system", "content": "sys"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "a"}]},
        {"role": "tool", "tool_call_id": "a", "content": "HASIL BESAR RONDE 1 " + "x" * 500},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "b"}]},
        {"role": "tool", "tool_call_id": "b", "content": "HASIL BESAR RONDE 2 " + "y" * 500},
        {"role": "tool", "tool_call_id": "c", "content": "HASIL BESAR RONDE 3 " + "z" * 500},
    ]


def _idx():
    return [
        {"i": 2, "round": 1, "name": "bom_dari_rangka"},
        {"i": 4, "round": 2, "name": "stok_gudang"},
        {"i": 5, "round": 3, "name": "detail_part"},
    ]


def test_trim_ronde_lama_jadi_stub_terbaru_utuh():
    m, idx = _msgs(), _idx()
    A._trim_old_tool_messages(m, idx, cur_round=3, keep_last=2)
    # batas = 3-2 = 1 → hanya ronde 1 diciutkan.
    assert "diringkas" in m[2]["content"] and "bom_dari_rangka" in m[2]["content"]
    assert m[2]["tool_call_id"] == "a"                 # tool_call_id DIPERTAHANKAN
    assert m[2]["role"] == "tool"
    assert "HASIL BESAR RONDE 2" in m[4]["content"]    # ronde terbaru utuh
    assert "HASIL BESAR RONDE 3" in m[5]["content"]
    assert idx[0]["stubbed"] is True and "stubbed" not in idx[1]


def test_trim_idempoten():
    m, idx = _msgs(), _idx()
    A._trim_old_tool_messages(m, idx, cur_round=3)
    first = m[2]["content"]
    A._trim_old_tool_messages(m, idx, cur_round=3)     # panggil lagi
    assert m[2]["content"] == first                    # tak dobel-stub / error


def test_trim_noop_saat_ronde_sedikit():
    """Turn 1-2 ronde (batas<1) → tak ada yang diciutkan (aman, hasil terkini utuh)."""
    m, idx = _msgs(), _idx()
    A._trim_old_tool_messages(m, idx, cur_round=2, keep_last=2)   # batas = 0
    assert "HASIL BESAR RONDE 1" in m[2]["content"]


def test_trim_banyak_ronde():
    m, idx = _msgs(), _idx()
    A._trim_old_tool_messages(m, idx, cur_round=4, keep_last=2)   # batas = 2
    assert "diringkas" in m[2]["content"]              # ronde 1
    assert "diringkas" in m[4]["content"]              # ronde 2
    assert "HASIL BESAR RONDE 3" in m[5]["content"]    # ronde 3 utuh
