"""Runner eval: retry jaringan + mode percakapan `steps`.

Latar: run 2026-07-20 tercatat 24 lolos / 12 gagal — tapi 10 dari 12 kegagalan
itu "Gagal menghubungi DeepSeek (jaringan)" karena koneksi putus di tengah run.
Angka pass/fail jadi tak bisa dipakai sebagai metrik kualitas sama sekali.
Retry HANYA untuk kegagalan transport; kegagalan perilaku harus tetap FAIL.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parents[1] / "evals" / "run_evals.py"


@pytest.fixture(scope="module")
def re_mod():
    spec = importlib.util.spec_from_file_location("run_evals_uji", _PATH)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


# ── Klasifikasi error ───────────────────────────────────────────────────────

def test_error_jaringan_dikenali(re_mod):
    for pesan in ("Gagal menghubungi DeepSeek (jaringan)",
                  "Connection reset by peer",
                  "Request timed out"):
        assert re_mod._is_network_error(RuntimeError(pesan)) is True


def test_error_lain_bukan_jaringan(re_mod):
    """Kegagalan perilaku/tool tak boleh diulang — itu justru sinyal yang dicari."""
    assert re_mod._is_network_error(RuntimeError("tool tidak dikenal")) is False
    assert re_mod._is_network_error(ValueError("jaringan")) is False
    assert re_mod._is_network_error(KeyError("timeout")) is False


# ── Retry ───────────────────────────────────────────────────────────────────

def test_retry_lalu_berhasil(re_mod, monkeypatch):
    monkeypatch.setattr(re_mod.time, "sleep", lambda s: None)
    n = {"i": 0}

    def flaky(user, turns, **kw):
        n["i"] += 1
        if n["i"] < 3:
            raise RuntimeError("Gagal menghubungi DeepSeek (jaringan)")
        return {"reply": "ok", "tools_used": []}

    monkeypatch.setattr(re_mod.ai_assistant, "chat", flaky)
    assert re_mod._chat_once([{"role": "user", "content": "x"}])["reply"] == "ok"
    assert n["i"] == 3


def test_retry_habis_tetap_melempar(re_mod, monkeypatch):
    monkeypatch.setattr(re_mod.time, "sleep", lambda s: None)

    def selalu_gagal(user, turns, **kw):
        raise RuntimeError("Gagal menghubungi DeepSeek (jaringan)")

    monkeypatch.setattr(re_mod.ai_assistant, "chat", selalu_gagal)
    with pytest.raises(RuntimeError):
        re_mod._chat_once([{"role": "user", "content": "x"}])


def test_error_perilaku_tak_diulang(re_mod, monkeypatch):
    monkeypatch.setattr(re_mod.time, "sleep", lambda s: None)
    n = {"i": 0}

    def gagal(user, turns, **kw):
        n["i"] += 1
        raise RuntimeError("tool 'cari_part' tidak dikenal")

    monkeypatch.setattr(re_mod.ai_assistant, "chat", gagal)
    with pytest.raises(RuntimeError):
        re_mod._chat_once([{"role": "user", "content": "x"}])
    assert n["i"] == 1


# ── Mode `steps` (percakapan bersesi) ───────────────────────────────────────

def test_steps_mengakumulasi_riwayat_nyata(re_mod, monkeypatch):
    """Assertion berlaku pada jawaban TERAKHIR; riwayat dibangun dari jawaban
    NYATA, bukan yang ditulis di golden — itulah gunanya menguji follow-up."""
    dilihat = []

    def chat(user, turns, **kw):
        dilihat.append(([dict(t) for t in turns], kw.get("conversation_id")))
        return {"reply": f"jawab-{len(dilihat)}", "tools_used": [f"tool{len(dilihat)}"]}

    monkeypatch.setattr(re_mod.ai_assistant, "chat", chat)
    reply, tools = re_mod._jalankan_kasus(
        {"id": "x", "steps": ["pertama", "kedua"]}, "conv-uji-1234")

    assert reply == "jawab-2"
    assert tools == ["tool1", "tool2"]          # gabungan seluruh langkah
    assert len(dilihat[1][0]) == 3              # user, assistant(nyata), user
    assert dilihat[1][0][1]["content"] == "jawab-1"
    assert dilihat[0][1] == dilihat[1][1] == "conv-uji-1234"   # sesi yang sama


def test_kasus_biasa_tanpa_conversation_id(re_mod, monkeypatch):
    """Kasus lama (question/turns) TIDAK boleh mendadak berbagi memori sesi —
    eval harus tetap menguji jawaban dari nol."""
    dilihat = {}

    def chat(user, turns, **kw):
        dilihat.update(kw)
        return {"reply": "ok", "tools_used": []}

    monkeypatch.setattr(re_mod.ai_assistant, "chat", chat)
    re_mod._jalankan_kasus({"id": "x", "question": "halo"}, "conv-uji-1234")
    assert dilihat.get("conversation_id") in (None, "")


def test_conv_id_selalu_sah(re_mod):
    """Harus lolos pola ^[A-Za-z0-9-]{8,64}$ yang divalidasi ai_session."""
    from app.services import ai_session
    for cid in ("a", "isi-assy-gearbox-nx400", "x" * 200, "aneh_/.:"):
        assert ai_session._CONV_RE.match(re_mod._conv_id(cid))
