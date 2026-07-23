"""Rem panggilan tool identik per-giliran (H3 paket handal 2026-07-23).

Log produksi 30 hari: 82,6% giliran ber-ronde ≥4 berisi tool gagal — model
membakar ronde memanggil tool yang sama berulang. Panggilan IDENTIK kini
di-cache (tak dieksekusi ulang + instruksi berhenti); tiap tool maksimal 3
eksekusi BERBEDA per giliran (payload penolakan TANPA field error → telemetri
nf, bukan err)."""
import json

import pytest

from app.services import ai_assistant as ai

USER = {"username": "tester", "role": "user"}


@pytest.fixture(autouse=True)
def _hermetik(monkeypatch):
    monkeypatch.setattr(ai, "_system_prompt", lambda user: "system uji")
    monkeypatch.setattr(ai, "_tool_specs", lambda user, sheet_id="": [])
    monkeypatch.setattr(ai, "_unit_name_tokens", lambda: set())
    monkeypatch.setattr(ai, "_prefetch_epc_rangka", lambda history: None)


def _tc(name, args, cid="c1"):
    return {"id": cid, "function": {"name": name, "arguments": json.dumps(args)}}


def _stub_model(monkeypatch, seq):
    calls = {"n": 0, "messages": []}

    def fake(messages, tools, max_tokens=6000):
        calls["messages"].append(messages)
        c = seq[min(calls["n"], len(seq) - 1)]
        calls["n"] += 1
        if isinstance(c, str):
            return {"choices": [{"message": {"content": c}, "finish_reason": "stop"}]}
        return c

    monkeypatch.setattr(ai, "_post_chat", fake)
    return calls


def _tool_round(*tcs):
    return {"choices": [{"message": {"content": "", "tool_calls": list(tcs)},
                         "finish_reason": "tool_calls"}]}


def test_panggilan_identik_lintas_ronde_dieksekusi_sekali(monkeypatch):
    hitung = {"n": 0}

    def run_tool(name, args, user, sheet_id=""):
        hitung["n"] += 1
        return {"found": True, "data": "isi"}

    monkeypatch.setattr(ai, "_run_tool", run_tool)
    seq = [
        _tool_round(_tc("cari_part", {"query": "kampas"}, "a")),
        _tool_round(_tc("cari_part", {"query": "kampas"}, "b")),   # identik!
        "Hasil: kampas ada.",
    ]
    calls = _stub_model(monkeypatch, seq)
    out = ai.chat(USER, [{"role": "user", "content": "kampas ada?"}])
    assert hitung["n"] == 1                       # eksekusi nyata cuma sekali
    assert out["reply"] == "Hasil: kampas ada."
    # pesan tool ronde-2 (yang dilihat model panggilan ke-3) memuat instruksi stop
    m3 = calls["messages"][2]
    assert any("PANGGILAN IDENTIK" in str(m.get("content") or "") for m in m3)


def test_batas_3_eksekusi_berbeda_per_tool(monkeypatch):
    hitung = {"n": 0}

    def run_tool(name, args, user, sheet_id=""):
        hitung["n"] += 1
        return {"found": False}                    # nf jujur tiap kali

    monkeypatch.setattr(ai, "_run_tool", run_tool)
    seq = [
        _tool_round(_tc("cari_part", {"query": "a"}, "1")),
        _tool_round(_tc("cari_part", {"query": "b"}, "2")),
        _tool_round(_tc("cari_part", {"query": "c"}, "3")),
        _tool_round(_tc("cari_part", {"query": "d"}, "4")),   # ke-4 → ditolak
        "Tidak ketemu di semua pencarian.",
    ]
    calls = _stub_model(monkeypatch, seq)
    ai.chat(USER, [{"role": "user", "content": "cari sesuatu"}])
    assert hitung["n"] == 3                        # eksekusi berhenti di 3
    m5 = calls["messages"][4]
    assert any("BATAS" in str(m.get("content") or "") for m in m5)


def test_tool_call_key_abaikan_kunci_server():
    k1 = ai._tool_call_key("hitung_part", {"pn": "X", "_grounded": {"A", "B"}})
    k2 = ai._tool_call_key("hitung_part", {"pn": "X", "_grounded": {"C"},
                                           "_q_user": "beda"})
    assert k1 == k2
    # urutan arg tak relevan
    assert (ai._tool_call_key("t", {"a": 1, "b": 2})
            == ai._tool_call_key("t", {"b": 2, "a": 1}))
    # args beda → key beda
    assert ai._tool_call_key("t", {"a": 1}) != ai._tool_call_key("t", {"a": 2})


def test_duplikat_identik_dalam_satu_batch(monkeypatch):
    hitung = {"n": 0}

    def run_tool(name, args, user, sheet_id=""):
        hitung["n"] += 1
        return {"found": True, "data": "isi"}

    monkeypatch.setattr(ai, "_run_tool", run_tool)
    seq = [
        _tool_round(_tc("detail_part", {"pn": "WG1"}, "a"),
                    _tc("detail_part", {"pn": "WG1"}, "b"),    # kembar dlm batch
                    _tc("detail_part", {"pn": "WG2"}, "c")),
        "Selesai.",
    ]
    calls = _stub_model(monkeypatch, seq)
    ai.chat(USER, [{"role": "user", "content": "cek dua part"}])
    assert hitung["n"] == 2                        # WG1 sekali + WG2 sekali
    # ketiga tool message tetap ada (kontrak tool_call_id → jawaban model)
    m2 = calls["messages"][1]
    assert sum(1 for m in m2 if m.get("role") == "tool") == 3


def test_args_beda_dan_tool_beda_tak_terpengaruh(monkeypatch):
    hitung = {"n": 0}

    def run_tool(name, args, user, sheet_id=""):
        hitung["n"] += 1
        return {"found": True}

    monkeypatch.setattr(ai, "_run_tool", run_tool)
    seq = [
        _tool_round(_tc("cari_part", {"query": "a"}, "1")),
        _tool_round(_tc("detail_part", {"pn": "b"}, "2")),
        "Beres.",
    ]
    _stub_model(monkeypatch, seq)
    ai.chat(USER, [{"role": "user", "content": "dua hal"}])
    assert hitung["n"] == 2


def test_batas_tak_mencemari_telemetri_err(monkeypatch):
    """Payload penolakan batas TANPA 'error' → _tool_fail_kind = nf, bukan err."""
    res = None
    monkeypatch.setattr(ai, "_run_tool", lambda n, a, u, s="": {"found": False})
    seq = [
        _tool_round(_tc("cari_part", {"query": "a"}, "1")),
        _tool_round(_tc("cari_part", {"query": "b"}, "2")),
        _tool_round(_tc("cari_part", {"query": "c"}, "3")),
        _tool_round(_tc("cari_part", {"query": "d"}, "4")),
        "Tidak ada.",
    ]
    _stub_model(monkeypatch, seq)
    logged = {}
    monkeypatch.setattr(ai.ai_chat_log, "log_turn",
                        lambda **kw: logged.update(kw) or True)
    ai.chat(USER, [{"role": "user", "content": "cari"}])
    assert "cari_part:err" not in (logged.get("tools_failed") or [])
