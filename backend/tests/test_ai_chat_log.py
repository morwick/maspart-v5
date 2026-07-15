"""Observabilitas asisten (ai_chat_log): pencatatan best-effort + agregasi +
hook di chat(). Supabase REST & _post_chat di-mock (tanpa jaringan)."""
import pytest

from app.services import ai_assistant as ai
from app.services import ai_chat_log

USER = {"username": "obs", "role": "user"}


# ── log_turn best-effort ─────────────────────────────────────────────────────

def test_log_turn_kirim_payload(monkeypatch):
    sent = {}

    class _R:
        status_code = 201

    def fake_post(url, headers=None, json=None, timeout=None):
        sent["url"] = url
        sent["json"] = json
        return _R()

    monkeypatch.setattr(ai_chat_log.requests, "post", fake_post)
    ok = ai_chat_log.log_turn(username="u", role="admin", question="q" * 800,
                              tools_used=["cari_part", "detail_part"], rounds=2,
                              latency_ms=1234, guard_hit=True, tool_failed=False,
                              reply_len=50, outcome="ok")
    assert ok is True
    assert sent["json"]["tools"] == "cari_part, detail_part"
    assert sent["json"]["tools_count"] == 2
    assert sent["json"]["guard_hit"] is True
    assert len(sent["json"]["question"]) == 500  # dipotong


def test_log_turn_gagal_tak_melempar(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("supabase down")
    monkeypatch.setattr(ai_chat_log.requests, "post", boom)
    assert ai_chat_log.log_turn(username="u", role="user", question="q",
                                tools_used=[], rounds=0, latency_ms=1,
                                guard_hit=False, tool_failed=False,
                                reply_len=1, outcome="ok") is False


def test_summary_agregasi(monkeypatch):
    rows = [
        {"latency_ms": 1000, "guard_hit": True, "tool_failed": False,
         "tools": "cari_part", "outcome": "ok"},
        {"latency_ms": 3000, "guard_hit": False, "tool_failed": True,
         "tools": "cari_part, detail_part", "outcome": "not_found"},
    ]
    monkeypatch.setattr(ai_chat_log, "list_logs", lambda limit=1000: rows)
    s = ai_chat_log.summary()
    assert s["total"] == 2
    assert s["guard_menyala"] == 1
    assert s["tool_gagal"] == 1
    assert dict(s["tool_tersering"])["cari_part"] == 2
    assert s["outcome"]["not_found"] == 1


def test_summary_kosong(monkeypatch):
    monkeypatch.setattr(ai_chat_log, "list_logs", lambda limit=1000: [])
    assert ai_chat_log.summary() == {"total": 0}


# ── hook di chat() memanggil log_turn dgn metadata benar ────────────────────

def _hermetik(monkeypatch):
    monkeypatch.setattr(ai, "_system_prompt", lambda user: "sys")
    monkeypatch.setattr(ai, "_tool_specs", lambda user, sheet_id="": [])
    monkeypatch.setattr(ai, "_unit_name_tokens", lambda: set())


def test_chat_mencatat_giliran(monkeypatch):
    _hermetik(monkeypatch)
    monkeypatch.setattr(ai, "_post_chat",
                        lambda messages, tools, max_tokens=6000: {"choices": [{"message": {"content": "Halo, siap membantu."},
                                                              "finish_reason": "stop"}]})
    captured = {}
    monkeypatch.setattr(ai.ai_chat_log, "log_turn",
                        lambda **kw: captured.update(kw) or True)
    out = ai.chat(USER, [{"role": "user", "content": "halo asisten"}])
    assert out["reply"].startswith("Halo")
    assert captured["question"] == "halo asisten"
    assert captured["outcome"] == "ok"
    assert captured["guard_hit"] is False
    assert captured["rounds"] == 0
    assert captured["latency_ms"] >= 0


def test_chat_catat_outcome_not_found_saat_karangan(monkeypatch):
    _hermetik(monkeypatch)
    monkeypatch.setattr(ai.part_index, "search_exact_pns", lambda pns: [])
    monkeypatch.setattr(ai, "_post_chat",
                        lambda messages, tools, max_tokens=6000: {"choices": [{"message": {"content": "PN AZ9998887776 stok 5."},
                                                              "finish_reason": "stop"}]})
    captured = {}
    monkeypatch.setattr(ai.ai_chat_log, "log_turn",
                        lambda **kw: captured.update(kw) or True)
    out = ai.chat(USER, [{"role": "user", "content": "cari part"}])
    assert "AZ9998887776" not in out["reply"]
    assert captured["outcome"] == "not_found"
    assert captured["guard_hit"] is True


# ── Kolom token (migrasi 021): skema lama → fallback tanpa kolom token ───────

def test_log_turn_fallback_tanpa_kolom_token(monkeypatch):
    """Migrasi 021 belum jalan → insert dgn kolom token ditolak PostgREST;
    baris WAJIB diulang tanpa kolom token agar log tidak hilang."""
    payloads = []

    class _R:
        def __init__(self, code):
            self.status_code = code

    def fake_post(url, headers=None, json=None, timeout=None):
        payloads.append(json)
        return _R(400 if len(payloads) == 1 else 201)

    monkeypatch.setattr(ai_chat_log.requests, "post", fake_post)

    ok = ai_chat_log.log_turn(username="a", role="admin", question="q",
                              tools_used=[], rounds=1, latency_ms=10,
                              guard_hit=False, tool_failed=False, reply_len=5,
                              outcome="ok", tokens_in=100, tokens_out=20,
                              tokens_cache_hit=90, api_calls=2)

    assert ok is True
    assert "tokens_in" in payloads[0]           # dicoba lengkap dulu
    assert "tokens_in" not in payloads[1]       # fallback: tanpa kolom token
    assert payloads[1]["outcome"] == "ok"


def test_log_turn_skema_baru_sekali_kirim(monkeypatch):
    payloads = []

    class _R:
        status_code = 201

    def fake_post(url, headers=None, json=None, timeout=None):
        payloads.append(json)
        return _R()

    monkeypatch.setattr(ai_chat_log.requests, "post", fake_post)

    ok = ai_chat_log.log_turn(username="a", role="admin", question="q",
                              tools_used=[], rounds=1, latency_ms=10,
                              guard_hit=False, tool_failed=False, reply_len=5,
                              outcome="ok", tokens_in=85_000, tokens_out=1_200,
                              tokens_cache_hit=79_000, api_calls=2)

    assert ok is True and len(payloads) == 1
    assert payloads[0]["tokens_in"] == 85_000
    assert payloads[0]["api_calls"] == 2
