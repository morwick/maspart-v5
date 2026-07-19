"""Telemetri split jenis kegagalan tool: nf (lookup jujur nihil) vs err
(error/ditolak/infra) — _tool_fail_kind (p7) + suffix `nama:kind` di
tools_failed (p9) + rincian di ai_chat_log.summary().

Dulu statistik "tool gagal" menyatukan keduanya → cari_kode_kesalahan tampak
"23% rusak" padahal mayoritas not-found jujur (kode memang tak ada di dataset).
"""
import pytest

from app.services import ai_assistant as ai
from app.services import ai_chat_log

USER = {"username": "admin", "role": "admin"}


# ── matriks _tool_fail_kind ──────────────────────────────────────────────────
@pytest.mark.parametrize("result,kind", [
    ({"found": True, "parts": [1]}, ""),
    ("bukan dict", ""),
    ({}, ""),
    # nf: lookup jujur nihil — walau membawa string `error` PENJELAS.
    ({"found": False}, "nf"),
    ({"ditemukan": False}, "nf"),
    ({"tersedia": False}, "nf"),
    ({"found": False, "error": "BOM unit ini tidak ditemukan di EPC."}, "nf"),
    # err: ditolak / exception / infra.
    ({"denied": True, "error": "Tool tidak tersedia untuk peran Anda."}, "err"),
    ({"error": "tool 'x' gagal dijalankan (gangguan internal — sampaikan jujur "
               "ke user, jangan mengarang data)."}, "err"),
    ({"error": "tool tidak dikenal: abc"}, "err"),
    # infra menang atas found:False.
    ({"found": False, "_token_issue": True, "error": "Token EPC bermasalah."}, "err"),
    ({"found": False, "error": "Gagal menghubungi server EPC (jaringan). "
                               "Coba lagi."}, "err"),
])
def test_tool_fail_kind_matrix(result, kind):
    assert ai._tool_fail_kind(result) == kind
    assert ai._tool_failed(result) is bool(kind)   # kesetaraan dgn guard lama


# ── _catat_tool_gagal: dedupe per nama + upgrade nf→err ──────────────────────
def test_catat_tool_gagal_dedupe_dan_upgrade():
    daftar = []
    ai._catat_tool_gagal(daftar, "cari_part", "nf")
    ai._catat_tool_gagal(daftar, "cari_part", "nf")
    assert daftar == ["cari_part:nf"]
    ai._catat_tool_gagal(daftar, "cari_part", "err")     # upgrade
    assert daftar == ["cari_part:err"]
    ai._catat_tool_gagal(daftar, "cari_part", "nf")      # err TIDAK turun ke nf
    assert daftar == ["cari_part:err"]
    ai._catat_tool_gagal(daftar, "stok_gudang", "nf")
    assert daftar == ["cari_part:err", "stok_gudang:nf"]


# ── summary(): parse suffix, denominator nama polos, rincian nf/err/legacy ───
def test_summary_membelah_nf_err_legacy(monkeypatch):
    rows = [
        {"tools": "cari_part", "tools_failed": "cari_part:nf",
         "tool_failed": True, "latency_ms": 1, "outcome": "ok"},
        {"tools": "cari_part, stok_gudang", "tools_failed": "cari_part:err, stok_gudang:err",
         "tool_failed": True, "latency_ms": 1, "outcome": "ok"},
        # baris LAMA pra-split: nama polos tanpa suffix → "legacy".
        {"tools": "cari_part", "tools_failed": "cari_part",
         "tool_failed": True, "latency_ms": 1, "outcome": "ok"},
    ]
    monkeypatch.setattr(ai_chat_log, "list_logs", lambda limit=1000: rows)
    s = ai_chat_log.summary()
    top = s["tool_gagal_tersering"]
    # nama POLOS (bukan "cari_part:nf") agar rasio gagal/pakai tetap benar.
    assert top[0][0] == "cari_part" and top[0][1] == 3
    assert top[0][2] == 100.0
    assert top[0][3] == 1 and top[0][4] == 1        # [.., nf, err]
    assert s["tool_gagal_rincian"] == {"nf": 1, "err": 2, "legacy": 1}


# ── integrasi chat(): miss tool → log_turn menerima suffix :nf ───────────────
def test_chat_mencatat_tools_failed_bersuffix(monkeypatch):
    monkeypatch.setattr(ai, "_system_prompt", lambda user: "system uji")
    monkeypatch.setattr(ai, "_tool_specs", lambda user, sheet_id="": [])
    monkeypatch.setattr(ai, "_allowed_tool_names", lambda user, sheet_id="": {"cari_part"})
    monkeypatch.setattr(ai, "_unit_name_tokens", lambda: set())
    monkeypatch.setattr(ai, "_prefetch_epc_rangka", lambda history: None)
    monkeypatch.setattr(ai, "_run_tool",
                        lambda name, args, user, sheet_id="": {"found": False,
                                                               "jumlah_part_unik": 0})
    seq = [
        {"choices": [{"message": {"content": "", "tool_calls": [
            {"id": "c1", "function": {"name": "cari_part",
                                      "arguments": '{"query":"zzz"}'}},
        ]}, "finish_reason": "tool_calls"}]},
        {"choices": [{"message": {"content": "Part itu tidak ditemukan di katalog."},
                      "finish_reason": "stop"}]},
    ]
    calls = {"n": 0}

    def fake(messages, tools, max_tokens=6000):
        c = seq[min(calls["n"], len(seq) - 1)]
        calls["n"] += 1
        return c

    monkeypatch.setattr(ai, "_post_chat", fake)
    captured = {}
    monkeypatch.setattr(ai.ai_chat_log, "log_turn",
                        lambda **kw: captured.update(kw) or True)
    ai.chat(USER, [{"role": "user", "content": "ada part zzz?"}])
    assert captured["tools_failed"] == ["cari_part:nf"]
    assert captured["tool_failed"] is True
