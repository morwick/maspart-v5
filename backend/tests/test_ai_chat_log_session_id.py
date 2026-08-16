"""session_id di ai_chat_log (migrations/025) + degradasi bila migrasi belum jalan.

Migrasi dijalankan MANUAL oleh pemilik di Supabase, jadi kode wajib tetap
mencatat walau kolomnya belum ada — kalau tidak, observabilitas mati diam-diam
persis saat fitur baru dirilis.
"""
from __future__ import annotations

import pytest

from app.services import ai_chat_log


class _Resp:
    def __init__(self, code):
        self.status_code = code
        self.headers = {}
        self.text = ""


@pytest.fixture
def kirim(monkeypatch):
    """Rekam payload tiap percobaan POST; `tolak` = set kolom yang 'belum ada'."""
    rekam: list[dict] = []
    state = {"tolak": set()}

    def fake_post(url, headers=None, json=None, timeout=None):
        rekam.append(dict(json or {}))
        if state["tolak"] & set((json or {}).keys()):
            return _Resp(400)          # PostgREST: kolom tak dikenal
        return _Resp(201)

    monkeypatch.setattr(ai_chat_log.requests, "post", fake_post)
    monkeypatch.setattr(ai_chat_log, "_rest_url", lambda t: "http://x/" + t)
    monkeypatch.setattr(ai_chat_log, "_service_headers", lambda *a, **k: {})
    return rekam, state


def _log(**kw):
    dasar = dict(username="budi", role="user", question="q", tools_used=["cari_part"],
                 rounds=1, latency_ms=10, guard_hit=False, tool_failed=False,
                 reply_len=5, outcome="ok")
    dasar.update(kw)
    return ai_chat_log.log_turn(**dasar)


def test_session_id_ikut_tercatat(kirim):
    rekam, _ = kirim
    assert _log(session_id="conv-123-abc") is True
    assert rekam[0]["session_id"] == "conv-123-abc"


def test_kolom_session_belum_ada_tetap_tercatat(kirim):
    """Migrasi 025 belum dijalankan → turun tingkat sampai lolos, log TIDAK hilang.
    Sejak migrations/026 (guard_kinds), 029 (fase ms) & 030 (diulang) tangganya
    bertambah tiga anak, jadi EMPAT tingkat teratas sama-sama membawa session_id
    dan sama-sama ditolak."""
    rekam, state = kirim
    state["tolak"] = {"session_id"}
    assert _log(session_id="conv-123-abc") is True
    assert len(rekam) == 5
    assert "session_id" in rekam[0] and "diulang" in rekam[0]
    assert "session_id" in rekam[1] and "diulang" not in rekam[1] and "model_ms" in rekam[1]
    assert "session_id" in rekam[2] and "model_ms" not in rekam[2]
    assert "session_id" in rekam[3] and "guard_kinds" not in rekam[3]
    assert "session_id" not in rekam[4]          # tingkat yang akhirnya lolos
    assert rekam[4]["tools_failed"] is None or "tools_failed" in rekam[4]


def test_kolom_guard_kinds_belum_ada_tetap_tercatat(kirim):
    """Migrasi 026 belum dijalankan → turun TIGA tingkat (030 & 029 di atasnya
    ikut membawa guard_kinds); session_id tetap ikut."""
    rekam, state = kirim
    state["tolak"] = {"guard_kinds"}
    assert _log(session_id="c-1", guard_kinds=["dtc"]) is True
    assert len(rekam) == 4
    assert rekam[0]["guard_kinds"] == "dtc"
    assert "guard_kinds" not in rekam[3]
    assert rekam[3]["session_id"] == "c-1"       # kolom lain TIDAK ikut hilang


def test_guard_kinds_tercatat_sebagai_daftar_koma(kirim):
    rekam, _ = kirim
    _log(guard_kinds=["dtc", "pn"])
    assert rekam[0]["guard_kinds"] == "dtc, pn"


def test_tanpa_guard_kolomnya_null(kirim):
    rekam, _ = kirim
    _log()
    assert rekam[0]["guard_kinds"] is None


def test_degradasi_berjenjang_sampai_dasar(kirim):
    """Skema paling lama (016) pun masih menerima baris dasar."""
    rekam, state = kirim
    state["tolak"] = {"guard_kinds", "session_id", "tools_failed", "reply", "tokens_in"}
    assert _log(session_id="c-1", reply="halo", guard_kinds=["pn"]) is True
    terakhir = rekam[-1]
    for kolom in ("guard_kinds", "session_id", "tools_failed", "reply", "tokens_in"):
        assert kolom not in terakhir
    assert terakhir["question"] == "q"


def test_tanpa_session_id_kolomnya_null(kirim):
    rekam, _ = kirim
    _log()
    assert rekam[0]["session_id"] is None


def test_pertanyaan_panjang_tak_lagi_dipotong_500(kirim):
    """500 char memotong justru bagian yang menjelaskan MAKSUD user."""
    rekam, _ = kirim
    _log(question="x" * 900)
    assert len(rekam[0]["question"]) == 900
    assert ai_chat_log._QUESTION_CAP == 1000


def test_pertanyaan_sangat_panjang_tetap_di_cap(kirim):
    rekam, _ = kirim
    _log(question="x" * 5000)
    assert len(rekam[0]["question"]) == 1000
