"""P2: grounding ANGKA (stok/harga). Model bisa mengarang 'stok 77 pc' / 'Rp
1.500.000' yang tak ada di hasil tool mana pun — guard menangkapnya (koreksi lalu
anotasi). Hanya aktif bila tool BENAR jalan turn ini (follow-up murni-riwayat
dilewati agar tak false-positive). DeepSeek & tool DI-MOCK.
"""
import pytest

from app.services import ai_assistant as ai

USER = {"username": "tester", "role": "user"}


@pytest.fixture(autouse=True)
def _hermetik(monkeypatch):
    monkeypatch.setattr(ai, "_system_prompt", lambda user: "system uji")
    monkeypatch.setattr(ai, "_tool_specs", lambda user, sheet_id="": [])
    monkeypatch.setattr(ai, "_unit_name_tokens", lambda: set())
    monkeypatch.setattr(ai, "_prefetch_epc_rangka", lambda h: None)


def _flow(monkeypatch, tool_result, replies):
    """1 ronde tool (mengembalikan tool_result) → jawaban final berurutan `replies`."""
    seq = [replies] if isinstance(replies, str) else list(replies)
    responses = [
        {"choices": [{"message": {"content": "", "tool_calls": [
            {"id": "t1", "function": {"name": "detail_part", "arguments": "{}"}}]},
            "finish_reason": "tool_calls"}]},
    ] + [{"choices": [{"message": {"content": c}, "finish_reason": "stop"}]} for c in seq]
    calls = {"n": 0}

    def fake(messages, tools, max_tokens=6000):
        r = responses[min(calls["n"], len(responses) - 1)]
        calls["n"] += 1
        return r

    monkeypatch.setattr(ai, "_post_chat", fake)
    monkeypatch.setattr(ai, "_run_tool", lambda n, a, u, sheet_id="": tool_result)
    return calls


# ── Helper murni ────────────────────────────────────────────────────────────
def test_extract_dan_claimed_nums():
    assert ai._extract_nums("stok 12, Rp 1.500.000") == {"12", "1500000"}
    assert ai._claimed_nums("Stok WG9925520270 ada 77 pc.") == {"77"}
    assert ai._claimed_nums("Harganya Rp 1.500.000 per buah.") == {"1500000"}
    assert ai._claimed_nums("Ada 3 unit tersedia.") == {"3"}   # angka+satuan
    # angka insidental (bukan stok+satuan / bukan Rp) tak diklaim
    assert ai._claimed_nums("Tersedia di 3 gudang berbeda.") == set()


# ── Guard end-to-end ────────────────────────────────────────────────────────
def test_stok_karangan_dikoreksi(monkeypatch):
    """Tool found=False (tanpa angka). Model klaim '77 pc' → dikoreksi → jawaban bersih."""
    calls = _flow(monkeypatch, {"found": False},
                  ["Stok WG9925520270 ada 77 pc.",          # karangan → koreksi
                   "Maaf, stok untuk part itu tidak tersedia di data."])  # bersih
    out = ai.chat(USER, [{"role": "user", "content": "stok WG9925520270?"}])
    assert calls["n"] == 3                                  # tool + karangan + koreksi
    assert "77" not in out["reply"]


def test_harga_karangan_membandel_dianotasi(monkeypatch):
    """Model TERUS mengklaim harga karangan → setelah retry habis, jawaban dianotasi."""
    _flow(monkeypatch, {"found": True, "part_number": "WG9925520270"},
          "Harga WG9925520270 adalah Rp 9.999.000.")       # 9999000 tak ada di dump
    out = ai.chat(USER, [{"role": "user", "content": "harga WG9925520270?"}])
    assert "tidak terverifikasi" in out["reply"].lower()   # dianotasi (tak dihapus)


def test_angka_ada_di_hasil_tool_lolos(monkeypatch):
    """Stok & harga yang BENAR ada di hasil tool → lolos apa adanya (tanpa koreksi)."""
    calls = _flow(monkeypatch,
                  {"found": True, "part_number": "WG9925520270", "stok": 5, "harga": 1500000},
                  "Stok WG9925520270 ada 5 pc, harga Rp 1.500.000.")
    out = ai.chat(USER, [{"role": "user", "content": "stok & harga WG9925520270?"}])
    assert calls["n"] == 2                                  # tool + jawaban (tanpa retry)
    assert "5 pc" in out["reply"] and "1.500.000" in out["reply"]


def test_follow_up_tanpa_tool_tak_kena_guard(monkeypatch):
    """Tanpa tool jalan turn ini (follow-up murni-riwayat) → guard angka DILEWATI."""
    def fake(messages, tools, max_tokens=6000):
        return {"choices": [{"message": {"content": "Kira-kira Rp 2.500.000 seperti tadi."},
                             "finish_reason": "stop"}]}
    monkeypatch.setattr(ai, "_post_chat", fake)
    out = ai.chat(USER, [
        {"role": "user", "content": "tadi berapa harganya?"},
    ])
    assert out["reply"].startswith("Kira")                  # tak dikoreksi/dianotasi
    assert out["tools_used"] == []
