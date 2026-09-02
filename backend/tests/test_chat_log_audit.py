"""Telemetri PER PANGGILAN (migrations/031) + diet nalar ronde tool (2026-09-02).

Latar: output p50 1.354 token/giliran, ±86% di antaranya blok [PIKIR] + JSON
tool; angka per giliran menyembunyikan panggilan mana yang cache-miss; dan hanya
NAMA tool yang tercatat sehingga "follow-up memanggil ulang tool yang sama" tak
bisa dibedakan dari panggilan dengan argumen berbeda. DeepSeek DI-MOCK.
"""
from __future__ import annotations

import json

import pytest

from app.services import ai_assistant as ai
from app.services import ai_chat_log as L

USER = {"username": "tester", "role": "user"}
# conftest me-no-op L.log_turn untuk modul di luar prefiks test_ai_chat_log —
# fungsi ASLI ditangkap saat import (sebelum fixture berjalan), pola yang sama
# dengan test_chat_log_phase.
_LOG_TURN_ASLI = L.log_turn


@pytest.fixture(autouse=True)
def _hermetik(monkeypatch):
    monkeypatch.setattr(ai, "_system_prompt", lambda user: "sys")
    monkeypatch.setattr(ai, "_tool_specs", lambda user, sheet_id="": [])
    monkeypatch.setattr(ai, "_unit_name_tokens", lambda: set())
    monkeypatch.setattr(ai, "_prefetch_epc_rangka", lambda h: None)
    monkeypatch.setattr(L, "_rest_url", lambda t: "http://x/" + t)
    monkeypatch.setattr(L, "_service_headers", lambda *a, **k: {})


# ── helper murni ─────────────────────────────────────────────────────────────

def test_pikir_chars_menghitung_blok_tertutup_dan_terbuka():
    assert ai._pikir_chars("[PIKIR]abc[/PIKIR]jawaban") == len("[PIKIR]abc[/PIKIR]")
    assert ai._pikir_chars("[pikir]tak ditutup sampai akhir") == len("[pikir]tak ditutup sampai akhir")
    assert ai._pikir_chars("[PIKIR]a[/PIKIR]x[PIKIR]bc[/PIKIR]") == 16 + 17   # dua blok, 'x' tidak
    assert ai._pikir_chars("") == 0 and ai._pikir_chars(None) == 0
    assert ai._pikir_chars("jawaban polos tanpa nalar") == 0


def test_add_usage_mencatat_rincian_per_panggilan_dan_nalar():
    tot = {"in": 0, "out": 0, "cache": 0, "calls": 0}
    ai._add_usage(tot, {"usage": {"prompt_tokens": 100, "completion_tokens": 20,
                                  "prompt_cache_hit_tokens": 64},
                        "choices": [{"message": {"content": "[PIKIR]xyz[/PIKIR]ok"}}]})
    ai._add_usage(tot, {"usage": {"prompt_tokens": 200, "completion_tokens": 5,
                                  "prompt_cache_hit_tokens": 192},
                        "choices": [{"message": {"content": None, "tool_calls": []}}]})
    ai._add_usage(tot, {})                       # respons kosong/gagal tak melempar
    assert tot["calls"] == 3 and tot["in"] == 300 and tot["cache"] == 256
    assert tot["detail"] == ["100/64", "200/192", "0/0"]
    assert tot["pikir"] == len("[PIKIR]xyz[/PIKIR]")


def test_tool_arg_digest_stabil_dan_bebas_kunci_server():
    a = ai._tool_arg_digest("cari_part", {"query": "kampas rem", "unit": "NX360"})
    b = ai._tool_arg_digest("cari_part", {"unit": "NX360", "query": "kampas rem",
                                          "_grounded": {"X"}, "_q_user": "…"})
    c = ai._tool_arg_digest("cari_part", {"query": "kampas kopling", "unit": "NX360"})
    assert a == b                                  # urutan & kunci '_' tak berpengaruh
    assert a != c and a.startswith("cari_part#") and len(a) == len("cari_part#") + 8
    melingkar: dict = {}
    melingkar["diri"] = melingkar
    assert ai._tool_arg_digest("x", melingkar) == "x#?"      # tak ter-JSON → '?'


# ── chat() meneruskan ketiga kolom ke log ────────────────────────────────────

def test_chat_meneruskan_telemetri_per_panggilan(monkeypatch):
    responses = [
        {"choices": [{"message": {"content": "[PIKIR]cek stok[/PIKIR]", "tool_calls": [
            {"id": "t1", "type": "function",
             "function": {"name": "cari_part", "arguments": json.dumps({"query": "kampas rem"})}}]},
            "finish_reason": "tool_calls"}],
         "usage": {"prompt_tokens": 1000, "completion_tokens": 30, "prompt_cache_hit_tokens": 960}},
        {"choices": [{"message": {"content": "[PIKIR]satu varian[/PIKIR]Ada 1 varian."},
                      "finish_reason": "stop"}],
         "usage": {"prompt_tokens": 1300, "completion_tokens": 40, "prompt_cache_hit_tokens": 1024}},
    ]
    monkeypatch.setattr(ai, "_post_chat",
                        lambda messages, tools, max_tokens=6000: responses.pop(0))
    monkeypatch.setattr(ai, "_run_tool", lambda n, a, u, sheet_id="": {"found": True, "hasil": []})
    logged: dict = {}
    monkeypatch.setattr(ai.ai_chat_log, "log_turn_async", lambda **kw: logged.update(kw) or True)

    ai.chat(USER, [{"role": "user", "content": "cek stok kampas rem"}])

    assert logged["calls_detail"] == "1000/960;1300/1024"
    assert logged["pikir_chars"] == len("[PIKIR]cek stok[/PIKIR]") + len("[PIKIR]satu varian[/PIKIR]")
    assert logged["tools_args"] == [ai._tool_arg_digest("cari_part", {"query": "kampas rem"})]
    assert logged["tools_used"] == ["cari_part"]          # sejajar


def test_giliran_tanpa_tool_tetap_kirim_kunci(monkeypatch):
    monkeypatch.setattr(ai, "_post_chat", lambda messages, tools, max_tokens=6000: {
        "choices": [{"message": {"content": "Halo."}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 500, "completion_tokens": 3, "prompt_cache_hit_tokens": 448}})
    logged: dict = {}
    monkeypatch.setattr(ai.ai_chat_log, "log_turn_async", lambda **kw: logged.update(kw) or True)
    ai.chat(USER, [{"role": "user", "content": "halo"}])
    assert logged["pikir_chars"] == 0
    assert logged["calls_detail"] == "500/448"
    assert logged["tools_args"] == []


# ── tangga insert: tingkat 031 ditolak → turun ke 030 tanpa kehilangan baris ─

class _R:
    def __init__(self, code):
        self.status_code = code


def _payload_dasar():
    return dict(username="u", role="user", question="q", tools_used=["cari_part"],
                rounds=1, latency_ms=10, guard_hit=False, tool_failed=False,
                reply_len=5, outcome="ok", tokens_in=1, tokens_out=1,
                tokens_cache_hit=1, api_calls=1, reply="r", session_id="s",
                pikir_chars=42, calls_detail="1000/960;1300/1024",
                tools_args=["cari_part#deadbeef"])


def test_log_turn_tingkat_audit_dikirim_dulu(monkeypatch):
    monkeypatch.setattr(L, "_tier_memo", 0)
    payloads = []
    monkeypatch.setattr(L.requests, "post",
                        lambda url, headers=None, json=None, timeout=None:
                        (payloads.append(json) or _R(201)))
    assert _LOG_TURN_ASLI(**_payload_dasar()) is True
    assert len(payloads) == 1
    p = payloads[0]
    assert p["pikir_chars"] == 42 and p["calls_detail"] == "1000/960;1300/1024"
    assert p["tools_args"] == "cari_part#deadbeef" and p["diulang"] is False


def test_log_turn_turun_ke_030_bila_kolom_031_belum_ada(monkeypatch):
    monkeypatch.setattr(L, "_tier_memo", 0)
    payloads = []

    def fake_post(url, headers=None, json=None, timeout=None):
        payloads.append(json)
        return _R(400 if "pikir_chars" in json else 201)

    monkeypatch.setattr(L.requests, "post", fake_post)
    assert _LOG_TURN_ASLI(**_payload_dasar()) is True
    assert "pikir_chars" in payloads[0] and "pikir_chars" not in payloads[1]
    assert "diulang" in payloads[1]                        # tingkat 030 utuh


def test_kolom_audit_dipotong_plafon(monkeypatch):
    monkeypatch.setattr(L, "_tier_memo", 0)
    payloads = []
    monkeypatch.setattr(L.requests, "post",
                        lambda url, headers=None, json=None, timeout=None:
                        (payloads.append(json) or _R(201)))
    _LOG_TURN_ASLI(**{**_payload_dasar(), "tools_args": [f"t#{i:08d}" for i in range(500)],
                  "calls_detail": "9/9;" * 500})
    assert len(payloads[0]["tools_args"]) <= L._TOOLS_ARGS_CAP
    assert len(payloads[0]["calls_detail"]) <= L._CALLS_DETAIL_CAP


def test_list_logs_mencoba_select_audit_dulu(monkeypatch):
    seen = []

    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return [{"id": 1}]

    def fake_get(url, headers=None, params=None, timeout=None):
        seen.append(params["select"])
        return _Resp()

    monkeypatch.setattr(L.requests, "get", fake_get)
    assert L.list_logs(5) == [{"id": 1}]
    assert seen[0] == L._SELECT_AUDIT and "tools_args" in seen[0]


# ── prompt: diet nalar ronde tool ────────────────────────────────────────────

def test_prompt_punya_bentuk_a_maks_3_baris(monkeypatch):
    monkeypatch.undo()
    for role in ("admin", "user", "pembeli"):
        sp = ai._system_prompt({"username": "t", "role": role})
        assert "RESPONS YANG MEMANGGIL TOOL" in sp and "MAKS 3 BARIS" in sp
        assert "JAWABAN FINAL (tanpa tool_calls)" in sp
        assert "CEK AKHIR" in sp and "plafon ±150 kata" in sp      # bentuk B tetap utuh
        assert len(sp) <= 60_000
