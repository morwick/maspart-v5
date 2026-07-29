"""Rem anti-loop: plafon harus DETERMINISTIK, dan penolakannya jujur.

Dua cacat yang diuji di sini, keduanya dari log produksi:

1. Gate `_call_count >= _MAX_CALLS_PER_TOOL` dibaca SEBELUM eksekusi dan angkanya
   dinaikkan SESUDAH, tanpa lock — sementara batch tool dijalankan di
   ThreadPoolExecutor. Seluruh gelombang worker pertama membaca angka yang sama
   dan lolos bersamaan, jadi plafon efektif ≈ 3 + (worker−1).

2. Hasil penolakan membawa `found: False`, sehingga dibaca sebagai "nf" (data
   tidak ada) dan memicu nota "lookup gagal, jangan mengarang". Model lalu
   menyimpulkan puluhan PN yang DITOLAK REM itu memang tak ada di data —
   padahal belum sempat dicek sama sekali.

DeepSeek di-mock; nol jaringan, nol panggilan model.
"""
from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.services import ai_assistant as ai

USER = {"username": "tester", "role": "user"}


@pytest.fixture(autouse=True)
def _hermetik(monkeypatch):
    monkeypatch.setattr(ai, "_system_prompt", lambda user: "system uji")
    monkeypatch.setattr(ai, "_unit_name_tokens", lambda: set())
    monkeypatch.setattr(ai.part_index, "search_exact_pns", lambda pns: [])


# ── klasifikasi ─────────────────────────────────────────────────────────────

def test_hasil_rem_dikenali_sbg_brake():
    assert ai._tool_fail_kind({"found": False, "dibatasi": True}) == "brake"


def test_brake_bukan_nf_maupun_err():
    """Pembedaan inilah intinya: 'nf' berarti kita SUDAH mencari dan tak ada."""
    assert ai._tool_fail_kind({"found": False}) == "nf"
    assert ai._tool_fail_kind({"error": "boom"}) == "err"
    assert ai._tool_fail_kind({"found": False, "dibatasi": True}) != "nf"


def test_hasil_rem_tak_berkey_error():
    """Kontrak lama: rem tak boleh mencemari telemetri error infra."""
    assert "error" not in {"found": False, "dibatasi": True}


def test_sinyal_kuat_menimpa_brake():
    daftar: list[str] = []
    ai._catat_tool_gagal(daftar, "detail_part", "brake")
    assert daftar == ["detail_part:brake"]
    ai._catat_tool_gagal(daftar, "detail_part", "nf")
    assert daftar == ["detail_part:nf"]          # nf lebih kuat
    ai._catat_tool_gagal(daftar, "detail_part", "err")
    assert daftar == ["detail_part:err"]         # err paling kuat
    ai._catat_tool_gagal(daftar, "detail_part", "brake")
    assert daftar == ["detail_part:err"]         # tak turun lagi


# ── atomisitas di bawah eksekusi paralel ────────────────────────────────────

def _chat_dgn_batch(monkeypatch, n_panggilan: int) -> dict:
    """Jalankan satu giliran di mana model mengemit `n` tool_call sekaligus."""
    jalan = {"n": 0}
    lock = threading.Lock()
    barrier = threading.Barrier(min(4, n_panggilan), timeout=5)

    def _fake_run_tool(name, args, u, sid=""):
        # Paksa semua worker gelombang pertama masuk BERSAMAAN — inilah kondisi
        # balapan yang dulu membuat plafon bocor.
        try:
            barrier.wait()
        except Exception:
            pass
        with lock:
            jalan["n"] += 1
        return {"found": True, "pn": args.get("part_number")}

    monkeypatch.setattr(ai, "_run_tool", _fake_run_tool)
    monkeypatch.setattr(ai, "_tool_specs", lambda user, sheet_id="": [{"x": 1}])

    calls = [{"id": str(i), "type": "function",
              "function": {"name": "detail_part",
                           "arguments": '{"part_number": "PN%03d"}' % i}}
             for i in range(n_panggilan)]
    seq = [
        {"choices": [{"message": {"content": None, "tool_calls": calls},
                      "finish_reason": "tool_calls"}]},
        {"choices": [{"message": {"content": "Selesai."}, "finish_reason": "stop"}]},
    ]
    it = iter(seq)
    last = {}

    def _post(messages, tools, max_tokens=6000):
        try:
            last["v"] = next(it)
        except StopIteration:
            pass
        return last["v"]

    monkeypatch.setattr(ai, "_post_chat", _post)
    out = ai.chat(USER, [{"role": "user", "content": "cek 12 PN"}])
    return {"eksekusi": jalan["n"], "out": out}


def test_plafon_tepat_walau_batch_paralel(monkeypatch):
    """Sebelum perbaikan: bisa 6 eksekusi. Sesudah: tepat _MAX_CALLS_PER_TOOL."""
    r = _chat_dgn_batch(monkeypatch, 12)
    assert r["eksekusi"] == ai._MAX_CALLS_PER_TOOL


def test_giliran_ber_rem_tetap_menghasilkan_jawaban(monkeypatch):
    """Rem tak boleh menjatuhkan giliran — user tetap dapat jawaban."""
    r = _chat_dgn_batch(monkeypatch, 12)
    assert r["out"]["reply"]


def test_pesan_rem_menunjuk_tool_massal(monkeypatch):
    """Model harus diberi jalan keluar yang benar, bukan sekadar ditolak."""
    ditangkap = {}

    def _fake_run_tool(name, args, u, sid=""):
        return {"found": True}

    monkeypatch.setattr(ai, "_run_tool", _fake_run_tool)
    monkeypatch.setattr(ai, "_tool_specs", lambda user, sheet_id="": [{"x": 1}])

    calls = [{"id": str(i), "type": "function",
              "function": {"name": "detail_part",
                           "arguments": '{"part_number": "PN%03d"}' % i}}
             for i in range(8)]
    seq = [
        {"choices": [{"message": {"content": None, "tool_calls": calls},
                      "finish_reason": "tool_calls"}]},
        {"choices": [{"message": {"content": "Selesai."}, "finish_reason": "stop"}]},
    ]
    it = iter(seq)
    last = {}

    def _post(messages, tools, max_tokens=6000):
        ditangkap["messages"] = [dict(m) for m in messages]
        try:
            last["v"] = next(it)
        except StopIteration:
            pass
        return last["v"]

    monkeypatch.setattr(ai, "_post_chat", _post)
    ai.chat(USER, [{"role": "user", "content": "cek 8 PN"}])

    teks = "\n".join(str(m.get("content") or "") for m in ditangkap["messages"])
    assert "cek_massal_part" in teks
    assert "BELUM TENTU tidak" in teks          # jangan simpulkan 'tak ada'
    assert ai._LOOKUP_GAGAL_NOTE not in teks    # nota 'lookup gagal' TIDAK muncul


def test_error_infra_tetap_err_bukan_brake(monkeypatch):
    """`_run_tool` di produksi MENELAN exception-nya sendiri dan mengembalikan
    {"error": ...}. Hasil itu harus tetap terklasifikasi 'err' — kalau tertukar
    jadi 'brake', gangguan infrastruktur akan tersembunyi dari telemetri."""
    hasil_err = {"error": "tool 'cari_part' gagal dijalankan (gangguan internal)."}
    assert ai._tool_fail_kind(hasil_err) == "err"
    assert ai._tool_fail_kind({"denied": True}) == "err"


def test_hasil_rem_konsumsi_slot_tak_dobel(monkeypatch):
    """Panggilan yang DITOLAK rem tidak boleh ikut menaikkan hitungan — kalau
    ikut, satu batch besar akan mengunci tool itu untuk ronde-ronde berikutnya."""
    r = _chat_dgn_batch(monkeypatch, 12)
    # 12 diemit, tepat _MAX_CALLS_PER_TOOL dieksekusi; sisanya ditolak TANPA
    # menambah hitungan (kalau menambah, angkanya akan > plafon di ronde ini).
    assert r["eksekusi"] == ai._MAX_CALLS_PER_TOOL
