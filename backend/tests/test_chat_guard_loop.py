"""Loop guard anti-halusinasi di chat() end-to-end — DeepSeek DI-MOCK
(tanpa network): model 'bandel' yang terus mengarang PN harus berujung
pesan jujur 'tidak ditemukan', bukan tabel palsu.
"""
import pytest

from app.services import ai_assistant as ai

USER = {"username": "tester", "role": "user"}


@pytest.fixture(autouse=True)
def _hermetik(monkeypatch):
    # Hindari membangun system prompt besar / daftar tool nyata — bukan fokus test.
    monkeypatch.setattr(ai, "_system_prompt", lambda user: "system uji")
    monkeypatch.setattr(ai, "_tool_specs", lambda user: [])


def _stub_model(monkeypatch, content: str):
    """_post_chat palsu: selalu jawab teks yang sama, tanpa tool_calls."""
    calls = {"n": 0}

    def fake(messages, tools):
        calls["n"] += 1
        return {"choices": [{"message": {"content": content}, "finish_reason": "stop"}]}

    monkeypatch.setattr(ai, "_post_chat", fake)
    return calls


def test_pn_karangan_membandel_diganti_pesan_jujur(monkeypatch):
    calls = _stub_model(monkeypatch, "Part yang cocok: AZ9998887776, stok 5, Rp 1.200.000.")
    out = ai.chat(USER, [{"role": "user", "content": "cari tierod dong"}])
    # 1 jawaban awal + 2 retry koreksi = 3 panggilan model, lalu jaring terakhir.
    assert calls["n"] == 1 + ai._MAX_GUARD_RETRIES
    assert out["reply"] == ai._NOT_FOUND_REPLY
    assert "AZ9998887776" not in out["reply"]


def test_pn_yang_user_sebut_dianggap_sah(monkeypatch):
    calls = _stub_model(monkeypatch, "Stok WG2210040097 saat ini 5 pcs.")
    out = ai.chat(USER, [{"role": "user", "content": "stok WG2210040097 berapa?"}])
    assert calls["n"] == 1                       # tanpa retry — langsung lolos
    assert out["reply"] == "Stok WG2210040097 saat ini 5 pcs."


def test_pn_dari_jawaban_asisten_sebelumnya_dianggap_sah(monkeypatch):
    # Follow-up tanpa tool: PN dari turn asisten sebelumnya (sudah lolos guard) sah.
    _stub_model(monkeypatch, "Ya, HW19709XST237036 itu transmisi assy NX400.")
    history = [
        {"role": "user", "content": "transmisi NX400 apa?"},
        {"role": "assistant", "content": "Transmisi NX400 adalah HW19709XST237036."},
        {"role": "user", "content": "yang tadi itu assy ya?"},
    ]
    out = ai.chat(USER, history)
    assert "HW19709XST237036" in out["reply"]
    assert "tak terverifikasi" not in out["reply"]


def test_jawaban_tanpa_pn_lolos_apa_adanya(monkeypatch):
    _stub_model(monkeypatch, "Halo! Ada yang bisa saya bantu soal spare part?")
    out = ai.chat(USER, [{"role": "user", "content": "halo"}])
    assert out["reply"].startswith("Halo!")
    assert out["tools_used"] == []
