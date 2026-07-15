"""Streaming status: chat() memanggil on_progress dgn label langkah (tanpa PN/harga).
DeepSeek DI-MOCK. Kontrak return chat() TAK berubah bila on_progress None."""
import pytest

from app.services import ai_assistant as ai

USER = {"username": "tester", "role": "user"}


@pytest.fixture(autouse=True)
def _hermetik(monkeypatch):
    monkeypatch.setattr(ai, "_system_prompt", lambda user: "sys uji")
    monkeypatch.setattr(ai, "_tool_specs", lambda user, sheet_id="": [])
    monkeypatch.setattr(ai, "_unit_name_tokens", lambda: set())
    monkeypatch.setattr(ai, "_prefetch_epc_rangka", lambda h: None)
    monkeypatch.setattr(ai.ai_chat_log, "log_turn", lambda **kw: True)


def _seq(monkeypatch, seq):
    calls = {"n": 0}

    def fake(messages, tools, max_tokens=6000):
        c = seq[min(calls["n"], len(seq) - 1)]
        calls["n"] += 1
        return c

    monkeypatch.setattr(ai, "_post_chat", fake)
    return calls


def test_on_progress_label_tool_dan_menyusun(monkeypatch):
    _seq(monkeypatch, [
        {"choices": [{"message": {"content": "", "tool_calls": [
            {"id": "a", "function": {"name": "cari_part", "arguments": "{}"}}]},
          "finish_reason": "tool_calls"}]},
        {"choices": [{"message": {"content": "Ini jawabannya."}, "finish_reason": "stop"}]},
    ])
    monkeypatch.setattr(ai, "_run_tool", lambda n, a, u, sheet_id="": {"found": True, "hasil": []})
    labels: list[str] = []
    out = ai.chat(USER, [{"role": "user", "content": "cari part rem"}], on_progress=labels.append)
    assert out["reply"] == "Ini jawabannya."
    assert "Memproses pertanyaan…" in labels
    assert "Mencari part di katalog" in labels       # label ramah cari_part
    assert "Menyusun jawaban…" in labels
    # Label TIDAK memuat PN/harga (aman di-stream).
    assert all("Rp" not in x for x in labels)


def test_tanpa_on_progress_tetap_jalan(monkeypatch):
    """Kontrak lama: on_progress None → chat() normal, tak error."""
    _seq(monkeypatch, [{"choices": [{"message": {"content": "Halo."}, "finish_reason": "stop"}]}])
    out = ai.chat(USER, [{"role": "user", "content": "halo"}])
    assert out["reply"] == "Halo."


def test_on_progress_error_ditelan(monkeypatch):
    """Callback yang melempar tak boleh merusak giliran (best-effort)."""
    _seq(monkeypatch, [{"choices": [{"message": {"content": "Halo."}, "finish_reason": "stop"}]}])

    def _boom(_label):
        raise RuntimeError("klien putus")

    out = ai.chat(USER, [{"role": "user", "content": "halo"}], on_progress=_boom)
    assert out["reply"] == "Halo."


def test_label_map_fallback():
    assert ai._tool_label("cari_part_di_unit") == "Mencari part di unit (EPC)"
    assert ai._tool_label("tool_tak_dikenal_xyz") == "Memproses data"
