"""Fallback SIMS di cari_part asisten + saran & jadwal usulan sinonim.

SIMS/LLM di-mock — tanpa jaringan.
"""
import pytest

from app.services import ai_assistant as ai
from app.services import ai_sinonim_learn as learn
from app.services import search_log, sims


@pytest.fixture(autouse=True)
def _misses_tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(search_log, "_path", lambda: tmp_path / "misses.json")
    search_log._state["data"] = None
    return tmp_path


# ── cari_part: PN tak ada lokal → nama dari SIMS, bukan "tidak ada" ─────────

def test_cari_part_fallback_sims(monkeypatch):
    monkeypatch.setattr(sims, "available", lambda: True)
    monkeypatch.setattr(sims, "get_part_info",
                        lambda pn, force_refresh=False:
                        {"partName": "Air compressor assembly"}
                        if pn == "1014167092" else {})
    res = ai._t_cari_part({"query": "1014167092"}, {"role": "admin", "username": "t"})
    assert res["jumlah_part_unik"] == 0
    assert res["hasil_sims"] == [{"part_number": "1014167092",
                                  "part_name": "Air compressor assembly",
                                  "sumber": "SIMS (katalog resmi Sinotruk)"}]
    assert "SIMS" in (res["catatan"] or "")
    # SIMS kenal → BUKAN celah kamus → tidak dicatat sbg miss.
    assert search_log.top_misses() == []


def test_cari_part_sims_tak_kenal_tetap_miss(monkeypatch):
    monkeypatch.setattr(sims, "available", lambda: True)
    monkeypatch.setattr(sims, "get_part_info", lambda pn, force_refresh=False: {})
    res = ai._t_cari_part({"query": "9999888877"}, {"role": "admin", "username": "t"})
    assert res["hasil_sims"] == []
    assert [m["query"] for m in search_log.top_misses()] == ["9999888877"]


def test_cari_part_sims_mati_aman(monkeypatch):
    monkeypatch.setattr(sims, "available", lambda: False)
    res = ai._t_cari_part({"query": "1014167092"}, {"role": "admin", "username": "t"})
    assert res["hasil_sims"] == []


# ── Jadwal usulan sinonim: tick hanya memanggil LLM bila miss cukup ──────────

def test_sched_tick_butuh_minimal_kandidat(monkeypatch):
    calls = {"n": 0}
    monkeypatch.setattr(learn, "generate",
                        lambda limit=10, auto_approve=False:
                        calls.__setitem__("n", calls["n"] + 1) or {"dibuat": 0})

    class _S:
        ai_configured = True
    monkeypatch.setattr(learn, "get_settings", lambda: _S())
    monkeypatch.setattr(learn, "_kandidat", lambda limit: [])
    learn._sched_tick()
    assert calls["n"] == 0            # miss kurang → LLM tidak dipanggil

    monkeypatch.setattr(learn, "_kandidat", lambda limit: [{}, {}, {}])
    monkeypatch.setattr(learn, "list_usulan", lambda status=None: [])
    learn._sched_tick()
    assert calls["n"] == 1            # cukup → generate sekali


def test_sched_start_idempoten(monkeypatch):
    monkeypatch.setattr(learn, "_sched_loop", lambda: None)
    learn._sched_started = False
    assert learn.start_scheduled_generate() is True
    assert learn.start_scheduled_generate() is False
    learn._sched_started = False  # bersihkan utk test lain
