"""Biaya token DeepSeek per giliran chat → ai_chat_log (menu Observabilitas AI).

Satu giliran = beberapa panggilan API (ronde tool + retry + final); biaya yang
jujur = JUMLAH `usage` seluruh panggilan itu. Skema lama (migrasi 021 belum
jalan) tak boleh membuat log hilang: baris diulang tanpa kolom token.
"""
from app.services import ai_assistant as A
from app.services import ai_chat_log as L

ADMIN = {"username": "mas", "role": "admin"}


def _usage(inp, out, cache=0):
    return {"prompt_tokens": inp, "completion_tokens": out,
            "prompt_cache_hit_tokens": cache}


# ── Akumulasi lintas panggilan dalam SATU giliran ────────────────────────────
def test_token_diakumulasi_dari_semua_panggilan_giliran(monkeypatch):
    """Ronde tool (panggilan 1) + jawaban final (panggilan 2) → log berisi jumlahnya."""
    responses = [
        {"choices": [{"message": {"content": "", "tool_calls": [
            {"id": "t1", "function": {"name": "cari_part", "arguments": "{}"}}]},
            "finish_reason": "tool_calls"}],
         "usage": _usage(40_000, 300, cache=38_000)},
        {"choices": [{"message": {"content": "Ini jawabannya."},
                      "finish_reason": "stop"}],
         "usage": _usage(45_000, 900, cache=41_000)},
    ]
    monkeypatch.setattr(A, "_post_chat", lambda m, t: responses.pop(0))
    monkeypatch.setattr(A, "_run_tool", lambda n, a, u, s="": {"found": True, "hasil": []})
    monkeypatch.setattr(A, "_prefetch_epc_rangka", lambda h: None)
    logged: dict = {}
    monkeypatch.setattr(A.ai_chat_log, "log_turn",
                        lambda **kw: logged.update(kw) or True)

    A.chat(ADMIN, [{"role": "user", "content": "cek stok kampas rem"}])

    assert logged["tokens_in"] == 85_000        # 40rb + 45rb
    assert logged["tokens_out"] == 1_200
    assert logged["tokens_cache_hit"] == 79_000
    assert logged["api_calls"] == 2


def test_respons_tanpa_usage_tidak_meledak(monkeypatch):
    monkeypatch.setattr(A, "_post_chat", lambda m, t: {
        "choices": [{"message": {"content": "Halo."}, "finish_reason": "stop"}]})
    monkeypatch.setattr(A, "_prefetch_epc_rangka", lambda h: None)
    logged: dict = {}
    monkeypatch.setattr(A.ai_chat_log, "log_turn",
                        lambda **kw: logged.update(kw) or True)

    A.chat(ADMIN, [{"role": "user", "content": "halo"}])

    assert logged["tokens_in"] == 0 and logged["tokens_out"] == 0
    assert logged["api_calls"] == 1             # panggilan tetap terhitung


# NB: tes fallback log_turn (skema lama tanpa kolom token) ada di
# test_ai_chat_log.py — satu-satunya modul yang TIDAK di-noop conftest.


# ── summary: rata-rata HANYA dari baris yang punya data token ────────────────
def test_summary_rata2_hanya_dari_baris_terukur(monkeypatch):
    rows = [
        # 2 baris lama (pra-migrasi 021): token 0 → tak boleh menyeret rata-rata.
        {"latency_ms": 1000, "outcome": "ok"},
        {"latency_ms": 1000, "outcome": "ok"},
        # 2 baris baru dgn token.
        {"latency_ms": 1000, "outcome": "ok",
         "tokens_in": 50_000, "tokens_out": 1_000, "tokens_cache_hit": 45_000},
        {"latency_ms": 1000, "outcome": "ok",
         "tokens_in": 30_000, "tokens_out": 3_000, "tokens_cache_hit": 27_000},
    ]
    monkeypatch.setattr(L, "list_logs", lambda limit=1000: rows)

    t = L.summary()["token"]

    assert t["giliran_terukur"] == 2
    assert t["total_in"] == 80_000 and t["total_out"] == 4_000
    assert t["rata2_in"] == 40_000 and t["rata2_out"] == 2_000
    assert t["cache_hit_persen"] == 90.0


def test_summary_tanpa_data_token_tetap_utuh(monkeypatch):
    monkeypatch.setattr(L, "list_logs",
                        lambda limit=1000: [{"latency_ms": 5, "outcome": "ok"}])

    s = L.summary()

    assert s["token"]["giliran_terukur"] == 0
    assert s["token"]["cache_hit_persen"] == 0.0
    assert s["total"] == 1                      # metrik lama tak terganggu
