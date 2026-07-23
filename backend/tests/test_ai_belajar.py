"""Miner belajar-sendiri harian (ai_belajar — Fase B program "nyambung"
2026-07-23). Invarian: BEBAS jaringan (edges dibaca dari disk), watermark
anti-dobel, gap kelompok ≥3, tick no-change tidak menulis links."""
import json

import pytest

from app.services import ai_belajar as belajar
from app.services import search_log


@pytest.fixture
def dunia(tmp_path, monkeypatch):
    class _S:
        data_path = tmp_path

    monkeypatch.setattr(belajar, "get_settings", lambda: _S())
    monkeypatch.setattr(search_log, "_path", lambda: tmp_path / "misses.json")
    search_log._state["data"] = None
    # Cache loader per-mtime global — bersihkan agar tulis+baca file gap/state
    # dalam satu test tak tertutup entri basi dari test lain (granularity mtime).
    from app.services import knowledge_util
    knowledge_util._LOAD_CACHE.clear()
    # ⛔ ZERO-NETWORK: panggilan requests apa pun = bug
    import requests as _rq

    def _boom(*a, **k):
        raise AssertionError("miner tidak boleh menyentuh jaringan!")

    monkeypatch.setattr(_rq, "get", _boom)
    monkeypatch.setattr(_rq, "post", _boom)
    return tmp_path


def _logs(monkeypatch, rows):
    from app.services import ai_chat_log
    monkeypatch.setattr(ai_chat_log, "list_logs", lambda limit=200: rows)


def test_nf_istilah_masuk_misses(dunia, monkeypatch):
    _logs(monkeypatch, [
        {"created_at": "2026-07-23T01:00:00Z", "question": "karet stabil howo",
         "tools_failed": "cari_part:nf", "outcome": "ok"},
        {"created_at": "2026-07-23T01:01:00Z",
         "question": "kenapa jawaban kemarin salah total soal harga part yang "
                     "saya tanyakan itu ya tolong dicek lagi",          # panjang
         "tools_failed": "cari_part:nf", "outcome": "ok"},
        {"created_at": "2026-07-23T01:02:00Z", "question": "WG9100443050",
         "tools_failed": "cari_part:nf", "outcome": "ok"},               # PN
        {"created_at": "2026-07-23T01:03:00Z", "question": "cek RT108966",
         "tools_failed": "cari_part_di_unit:nf", "outcome": "ok"},       # rangka
        {"created_at": "2026-07-23T01:04:00Z", "question": "filter oli",
         "tools_failed": "diagnosa:err", "outcome": "ok"},               # err bukan nf
    ])
    res = belajar.mine_chat_logs()
    assert res["miss_terekam"] == 1
    assert [m["query"] for m in search_log.top_misses()] == ["karet stabil howo"]


def test_watermark_anti_dobel(dunia, monkeypatch):
    _logs(monkeypatch, [
        {"created_at": "2026-07-23T01:00:00Z", "question": "karet stabil",
         "tools_failed": "cari_part:nf", "outcome": "ok"},
    ])
    belajar.mine_chat_logs()
    res2 = belajar.mine_chat_logs()          # baris sama → tidak diproses ulang
    assert res2["baris_baru"] == 0
    assert search_log.top_misses()[0]["count"] == 1


def test_gap_kelompok_minimal_3(dunia, monkeypatch):
    rows = [{"created_at": f"2026-07-23T02:0{i}:00Z",
             "question": f"cara setel klep mesin howo unit {i}",
             "tools_failed": "", "outcome": "not_found"} for i in range(3)]
    rows.append({"created_at": "2026-07-23T02:09:00Z",
                 "question": "pertanyaan lain sekali saja",
                 "tools_failed": "", "outcome": "empty"})
    _logs(monkeypatch, rows)
    belajar.mine_chat_logs()
    g = belajar.gaps()
    assert len(g) == 1 and g[0]["jumlah"] == 3
    assert "klep" in g[0]["topik"]
    # resolve menghapus
    assert belajar.resolve_gap(g[0]["topik"]) is True
    assert belajar.gaps() == []


def test_edges_dari_disk_tanpa_jaringan(dunia, monkeypatch):
    d = dunia / "epc_unit_items"
    d.mkdir()
    (d / "RT108966.json").write_text(json.dumps({
        "ts": 1, "rows": [{"pn": "WG9100443050", "nama": "Brake pad"},
                          {"pn": "AZ9925520217", "nama": "Spring"}]}),
        encoding="utf-8")
    (d / "SJ346500.json").write_text(json.dumps({
        "ts": 1, "rows": [{"pn": "WG9100443050", "nama": "Brake pad"}]}),
        encoding="utf-8")
    # populasi best-effort — paksa gagal (edges tetap terbentuk tanpa model)
    monkeypatch.setattr(belajar, "_peta_rangka_model", lambda: {})
    assert belajar.mine_epc_edges() is True
    from app.services.knowledge_util import load_json, _LOAD_CACHE
    _LOAD_CACHE.clear()
    edges = load_json(belajar._edges_path(), default={})
    assert edges["WG9100443050"]["frames_n"] == 2
    assert edges["AZ9925520217"]["nama"] == "Spring"
    # jalankan lagi tanpa perubahan → byte sama → False
    assert belajar.mine_epc_edges() is False


def test_edges_dengan_model_populasi(dunia, monkeypatch):
    d = dunia / "epc_unit_items"
    d.mkdir()
    (d / "RT108966.json").write_text(json.dumps({
        "ts": 1, "rows": [{"pn": "WG9100443050", "nama": "Brake pad"}]}),
        encoding="utf-8")
    monkeypatch.setattr(belajar, "_peta_rangka_model",
                        lambda: {"RT108966": "HOWO A7 380"})
    belajar.mine_epc_edges()
    from app.services.knowledge_util import load_json, _LOAD_CACHE
    _LOAD_CACHE.clear()
    edges = load_json(belajar._edges_path(), default={})
    assert edges["WG9100443050"]["models"] == ["HOWO A7 380"]


def test_tick_no_change_tak_menulis_links(dunia, monkeypatch):
    _logs(monkeypatch, [])
    ditulis = {"n": 0}
    from app.services import knowledge_links as kl
    monkeypatch.setattr(kl, "build", lambda: ditulis.__setitem__("n", ditulis["n"] + 1) or {})
    res = belajar._tick()
    assert res["edges_berubah"] is False
    assert ditulis["n"] == 0                 # links TIDAK di-rebuild


def test_start_scheduled_idempoten(monkeypatch):
    monkeypatch.setattr(belajar, "_sched_loop", lambda: None)
    belajar._sched_started = False
    assert belajar.start_scheduled() is True
    assert belajar.start_scheduled() is False
    belajar._sched_started = False           # bersihkan utk test lain
