"""Anggaran WALL-CLOCK satu giliran chat.

Pagar per-tool (`_TOOL_TIMEOUT_*`) dan per-ronde (`_MAX_TOOL_ROUNDS`) sudah lama
ada, tapi HASIL KALINYA tak pernah dibatasi: 8 ronde × 180 dtk = 24 menit tanpa
satu pun pagar menyala, dan user hanya menatap angka detik berjalan (giliran
nyata terburuk yang tercatat: 351 dtk, 2026-09-03).

Dua perilaku yang dikunci di sini:
1. Anggaran habis → ronde tool DITUTUP, model dipaksa menjawab dari data yang
   sudah ada, dan user DIBERI TAHU bahwa jawabannya mungkin sebagian.
2. Tool yang start saat anggaran hampir habis hanya ditunggu selama SISA
   anggaran — kalau tidak, satu tool 180 dtk membuat anggaran tak membatasi apa
   pun.

DeepSeek di-mock; nol jaringan, nol panggilan model.
"""
from __future__ import annotations

import threading
import time

import pytest

from app.services import ai_assistant as ai

USER = {"username": "tester", "role": "user"}
PESAN = [{"role": "user", "content": "cek part ini dong"}]   # sengaja TANPA rangka


@pytest.fixture(autouse=True)
def _hermetik(monkeypatch):
    monkeypatch.setattr(ai, "_system_prompt", lambda user: "system uji")
    monkeypatch.setattr(ai, "_unit_name_tokens", lambda: set())
    monkeypatch.setattr(ai.part_index, "search_exact_pns", lambda pns: [])
    monkeypatch.setattr(ai, "_tool_specs", lambda user, sheet_id="": [{"x": 1}])


def _pasang_model(monkeypatch, nama="detail_part"):
    """Model yang SELALU minta tool selama tool masih ditawarkan, dan menjawab
    begitu daftar tool kosong (persis perilaku jalur `tools_habis`)."""
    ronde = {"n": 0}

    def _post(messages, tools, max_tokens=6000, **kw):
        if not tools:
            return {"choices": [{"message": {"content": "Jawaban seadanya."},
                                 "finish_reason": "stop"}]}
        ronde["n"] += 1
        return {"choices": [{"message": {"content": None, "tool_calls": [{
            "id": f"c{ronde['n']}", "type": "function",
            "function": {"name": nama,
                         "arguments": '{"part_number": "PN%03d"}' % ronde["n"]},
        }]}, "finish_reason": "tool_calls"}]}

    monkeypatch.setattr(ai, "_post_chat", _post)
    return ronde


def test_anggaran_utuh_tak_menyentuh_giliran_normal(monkeypatch):
    """Pagar ini HANYA untuk giliran patologis — jalur normal tak boleh berubah."""
    _pasang_model(monkeypatch)
    monkeypatch.setattr(ai, "_run_tool", lambda n, a, u, sid="": {"found": True})

    out = ai.chat(USER, PESAN)

    assert "Jawaban seadanya." in out["reply"]
    assert "batas waktu satu giliran" not in out["reply"]


def test_anggaran_habis_menutup_ronde_tool_dan_memberi_tahu_user(monkeypatch):
    jalan = {"n": 0}

    def _tool(n, a, u, sid=""):
        jalan["n"] += 1
        return {"found": True}

    _pasang_model(monkeypatch)
    monkeypatch.setattr(ai, "_run_tool", _tool)
    # Anggaran sudah lewat sebelum ronde pertama → tool tak dijalankan sama sekali.
    monkeypatch.setattr(ai, "_TURN_WALL_BUDGET_S", -1.0)

    out = ai.chat(USER, PESAN)

    assert jalan["n"] == 0, "anggaran habis → tak boleh ada ronde tool baru"
    assert "Jawaban seadanya." in out["reply"]
    # Jujur ke user: jawaban ini mungkin sebagian, dan 'tidak ada' belum final.
    assert "batas waktu satu giliran" in out["reply"]
    assert "jangan dianggap final" in out["reply"]


def test_tool_hanya_ditunggu_selama_sisa_anggaran(monkeypatch):
    """Tanpa clamp: tool ini ditunggu `_tool_timeout` penuh (90-180 dtk), jadi
    anggaran 240 dtk tak pernah benar-benar membatasi giliran."""
    lepas = threading.Event()

    def _tool_lambat(n, a, u, sid=""):
        lepas.wait(30)                      # jauh di atas anggaran uji
        return {"found": True}

    _pasang_model(monkeypatch)
    monkeypatch.setattr(ai, "_run_tool", _tool_lambat)
    monkeypatch.setattr(ai, "_tool_timeout", lambda name: 180.0)
    monkeypatch.setattr(ai, "_TURN_WALL_BUDGET_S", 2.0)
    monkeypatch.setattr(ai, "_TOOL_WAIT_MIN_S", 0.2)

    t0 = time.monotonic()
    try:
        out = ai.chat(USER, PESAN)
    finally:
        lepas.set()                         # jangan tinggalkan thread menggantung
    lama = time.monotonic() - t0

    assert lama < 10, f"tool ditunggu terlalu lama ({lama:.1f} dtk) — clamp tak jalan"
    assert "batas waktu satu giliran" in out["reply"]


# ── aturan lama tunggu per tool (fungsi murni — bebas timing) ───────────────
# Sengaja diuji langsung, bukan lewat chat(): versi lewat chat() bergantung pada
# berapa lama giliran butuh untuk sampai ke ronde tool, dan itu berubah-ubah di
# bawah beban suite penuh (tesnya lulus sendirian, gagal saat suite ramai).

def test_batas_tunggu_normal_sama_dgn_timeout_tool():
    """Anggaran masih longgar → perilaku lama, persis `_tool_timeout`."""
    assert ai._batas_tunggu_tool("detail_part", 200.0) == ai._TOOL_TIMEOUT_S
    assert ai._batas_tunggu_tool("filter_unit", 200.0) == ai._TOOL_TIMEOUT_BERAT_S


def test_batas_tunggu_dipotong_sisa_anggaran():
    """Inti pagarnya: tool tak boleh menahan giliran melewati anggaran."""
    assert ai._batas_tunggu_tool("filter_unit", 40.0) == 40.0


def test_lantai_menjaga_tool_murah_saat_sisa_tinggal_remah():
    """`min(batas, sisa)` polos bisa jadi 0,3 dtk dan memvonis gagal-cek SEMUA
    tool murah yang normalnya 1-2 dtk."""
    assert ai._batas_tunggu_tool("detail_part", 0.3) == ai._TOOL_WAIT_MIN_S
    assert ai._batas_tunggu_tool("detail_part", -50.0) == ai._TOOL_WAIT_MIN_S


def test_lantai_TIDAK_boleh_melebihi_timeout_tool(monkeypatch):
    """⛔ Bug yang tertangkap `test_tool_menggantung_dijawab_gagal_cek`:
    `max(lantai, min(...))` membuat lantai mengalahkan batas waktu tool, jadi
    tool yang sengaja dibatasi 0,2 dtk malah ditunggu 15 dtk."""
    monkeypatch.setattr(ai, "_TOOL_TIMEOUT_S", 0.2)
    assert ai._batas_tunggu_tool("detail_part", 200.0) == 0.2
    assert ai._batas_tunggu_tool("detail_part", 0.05) == 0.2   # lantai pun kalah
