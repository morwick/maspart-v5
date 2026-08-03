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
    ok = ai_chat_log.log_turn(username="u", role="admin", question="q" * 1400,
                              tools_used=["cari_part", "detail_part"], rounds=2,
                              latency_ms=1234, guard_hit=True, tool_failed=False,
                              reply_len=50, outcome="ok")
    assert ok is True
    assert sent["json"]["tools"] == "cari_part, detail_part"
    assert sent["json"]["tools_count"] == 2
    assert sent["json"]["guard_hit"] is True
    # Cap dinaikkan 500 → 1000 (_QUESTION_CAP): 500 char memotong justru bagian
    # yang menjelaskan MAKSUD user, dan kolomnya `text` jadi tak menambah biaya.
    assert len(sent["json"]["question"]) == ai_chat_log._QUESTION_CAP == 1000


def test_log_turn_kirim_reply_dicap(monkeypatch):
    """Teks jawaban ikut disimpan, di-cap _REPLY_CAP; ada di payload tingkat teratas."""
    sent = {}

    class _R:
        status_code = 201

    def fake_post(url, headers=None, json=None, timeout=None):
        sent["json"] = json
        return _R()

    monkeypatch.setattr(ai_chat_log.requests, "post", fake_post)
    ai_chat_log.log_turn(username="u", role="admin", question="q",
                         tools_used=[], rounds=0, latency_ms=1, guard_hit=False,
                         tool_failed=False, reply_len=9000, outcome="ok",
                         reply="J" * 9000)
    assert len(sent["json"]["reply"]) == ai_chat_log._REPLY_CAP  # 4000
    assert sent["json"]["reply_len"] == 9000                     # panjang asli tetap


def test_log_turn_kirim_tools_failed(monkeypatch):
    """Nama tool yang gagal disimpan (comma-space, seperti `tools`) di tingkat teratas."""
    sent = {}

    class _R:
        status_code = 201

    monkeypatch.setattr(ai_chat_log.requests, "post",
                        lambda url, headers=None, json=None, timeout=None: sent.update(json=json) or _R())
    ai_chat_log.log_turn(username="u", role="admin", question="q", tools_used=["cari_part_di_unit", "stok_gudang"],
                         rounds=1, latency_ms=1, guard_hit=False, tool_failed=True, reply_len=5,
                         outcome="ok", reply="halo", tools_failed=["cari_part_di_unit"])
    assert sent["json"]["tools_failed"] == "cari_part_di_unit"


def test_summary_tool_gagal_tersering(monkeypatch):
    rows = [
        {"tools": "cari_part_di_unit, stok_gudang", "tools_failed": "cari_part_di_unit",
         "tool_failed": True, "latency_ms": 1, "outcome": "ok"},
        {"tools": "cari_part_di_unit", "tools_failed": "cari_part_di_unit",
         "tool_failed": True, "latency_ms": 1, "outcome": "ok"},
        {"tools": "cari_part_di_unit", "tools_failed": "", "tool_failed": False,
         "latency_ms": 1, "outcome": "ok"},
    ]
    monkeypatch.setattr(ai_chat_log, "list_logs", lambda limit=1000: rows)
    s = ai_chat_log.summary()
    top = s["tool_gagal_tersering"]
    assert top[0][0] == "cari_part_di_unit" and top[0][1] == 2   # 2 gagal
    assert top[0][2] == 66.7                                     # 2 gagal / 3 pakai


def test_log_turn_reply_fallback_bila_kolom_absen(monkeypatch):
    """Migrasi 022 belum jalan (PostgREST 400 utk kolom reply) → jatuh ke payload
    tanpa reply; log TETAP tercatat."""
    seen = []

    def fake_post(url, headers=None, json=None, timeout=None):
        seen.append(json)

        class _R:
            status_code = 400 if "reply" in json else 201
        return _R()

    monkeypatch.setattr(ai_chat_log.requests, "post", fake_post)
    ok = ai_chat_log.log_turn(username="u", role="admin", question="q",
                              tools_used=[], rounds=0, latency_ms=1, guard_hit=False,
                              tool_failed=False, reply_len=5, outcome="ok", reply="halo")
    assert ok is True
    assert any("reply" not in p for p in seen)   # tingkat fallback dipakai


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


# ── hook di chat() memanggil log_turn_async dgn metadata benar ──────────────
# chat() mencatat lewat log_turn_async (thread daemon) agar jalur jawaban tak
# menunggu Supabase; stub di bawah memanggilnya SINKRON supaya tak ada balapan
# antara assertion dan thread.

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
    monkeypatch.setattr(ai.ai_chat_log, "log_turn_async",
                        lambda **kw: captured.update(kw) or True)
    out = ai.chat(USER, [{"role": "user", "content": "halo asisten"}])
    assert out["reply"].startswith("Halo")
    assert captured["question"] == "halo asisten"
    assert captured["reply"] == out["reply"]      # teks jawaban ikut dicatat
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
    monkeypatch.setattr(ai.ai_chat_log, "log_turn_async",
                        lambda **kw: captured.update(kw) or True)
    out = ai.chat(USER, [{"role": "user", "content": "cari part"}])
    assert "AZ9998887776" not in out["reply"]
    assert captured["outcome"] == "not_found"
    assert captured["guard_hit"] is True


# ── Kolom token (migrasi 021): skema lama → fallback tanpa kolom token ───────

def test_log_turn_fallback_tanpa_kolom_token(monkeypatch):
    """Migrasi 021 & 022 belum jalan → insert dgn kolom reply/token ditolak
    PostgREST; baris WAJIB diulang berjenjang sampai base agar log tak hilang."""
    payloads = []

    class _R:
        def __init__(self, code):
            self.status_code = code

    def fake_post(url, headers=None, json=None, timeout=None):
        payloads.append(json)
        # kolom reply (022) & token (021) belum ada → hanya base yang diterima.
        extra = ("reply" in json) or ("tokens_in" in json)
        return _R(400 if extra else 201)

    monkeypatch.setattr(ai_chat_log.requests, "post", fake_post)

    ok = ai_chat_log.log_turn(username="a", role="admin", question="q",
                              tools_used=[], rounds=1, latency_ms=10,
                              guard_hit=False, tool_failed=False, reply_len=5,
                              outcome="ok", tokens_in=100, tokens_out=20,
                              tokens_cache_hit=90, api_calls=2, reply="halo")

    assert ok is True
    assert "reply" in payloads[0]                        # tingkat terkaya dulu
    assert "tokens_in" not in payloads[-1]               # base: tanpa token
    assert "reply" not in payloads[-1]                   # base: tanpa reply
    assert payloads[-1]["outcome"] == "ok"


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
