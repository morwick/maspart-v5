"""Refresh indeks Accurate TERJADWAL: jam WIB tetap 3×/hari + persistensi disk.

⛔ Aturan pemilik: indeks ditarik dari Accurate HANYA 07:00/12:00/19:00 WIB;
deploy/restart TIDAK menarik ulang (muat dari disk). Tanpa jaringan: di-monkeypatch.
"""
from __future__ import annotations

import datetime as dt

from app.services import accurate as a


# ── Jadwal jam WIB ───────────────────────────────────────────────────────────

def test_jam_terjadwal_hanya_3():
    assert a._REFRESH_HOURS_WIB == (7, 12, 19)


def test_seconds_until_next_refresh_pilih_jam_wib_berikutnya(monkeypatch):
    # Bekukan "sekarang" = 08:00 WIB → berikutnya 12:00 WIB (4 jam lagi).
    fixed = dt.datetime(2026, 7, 10, 8, 0, 0, tzinfo=a._WIB)

    class _DT(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed if tz is None else fixed.astimezone(tz)

    monkeypatch.setattr(a._dt, "datetime", _DT)
    assert a._seconds_until_next_refresh() == 4 * 3600


def test_seconds_until_next_refresh_lewat_tengah_malam(monkeypatch):
    # 21:00 WIB → berikutnya 07:00 WIB besok (10 jam lagi).
    fixed = dt.datetime(2026, 7, 10, 21, 0, 0, tzinfo=a._WIB)

    class _DT(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed if tz is None else fixed.astimezone(tz)

    monkeypatch.setattr(a._dt, "datetime", _DT)
    assert a._seconds_until_next_refresh() == 10 * 3600


# ── refresh(force=False) TIDAK PERNAH menarik on-demand ──────────────────────

def test_refresh_force_false_tak_pernah_pull(monkeypatch):
    monkeypatch.setattr(a, "_index_cache", {"items": [{"pn": "X"}], "ts": 0.0})  # cache tua
    monkeypatch.setattr(a, "fetch_all_items",
                        lambda: (_ for _ in ()).throw(AssertionError("force=False DILARANG pull")))
    assert a.refresh(force=False)["items"] == [{"pn": "X"}]   # pakai cache apa adanya


def test_refresh_force_true_menarik(monkeypatch):
    monkeypatch.setattr(a, "_index_cache", {"items": [], "by_pn": {}, "snap": {}, "ts": 0.0})
    raw = [{"no": "000001.WG1", "name": "Bearing", "availableToSell": 5, "id": 1, "unit1": {"name": "Pc"}}]
    monkeypatch.setattr(a, "fetch_all_items", lambda: raw)
    idx = a.refresh(force=True)
    assert len(idx["items"]) == 1 and "WG1" in idx["by_pn"]


# ── Persistensi disk: muat saat start, TANPA login ──────────────────────────

def test_save_dan_load_index(monkeypatch, tmp_path):
    fp = tmp_path / "accurate_index.json"
    monkeypatch.setattr(a, "_index_file", lambda: fp)
    monkeypatch.setattr(a, "_index_cache", {
        "items": [{"pn": "WG1"}], "by_pn": {"WG1": {"pn": "WG1"}},
        "by_gudang": {"WG1": {"01.Jkt": 3}}, "snap": {"WG1": {"stok": 3}},
        "ts": 111.0, "gudang_ts": 111.0,
    })
    a._save_index()
    assert fp.exists()
    # kosongkan lalu muat dari disk
    monkeypatch.setattr(a, "_index_cache", {"items": [], "by_pn": {}, "by_gudang": {}, "snap": {}})
    assert a._load_index() is True
    assert a._index_cache["by_gudang"] == {"WG1": {"01.Jkt": 3}}


def test_load_index_tak_ada_file(monkeypatch, tmp_path):
    monkeypatch.setattr(a, "_index_file", lambda: tmp_path / "nihil.json")
    assert a._load_index() is False


def test_start_restart_muat_disk_tak_bootstrap(monkeypatch):
    """Restart dgn indeks di disk → muat dari disk, TIDAK menarik/login."""
    monkeypatch.setattr(a, "_sched_started", False)
    monkeypatch.setattr(a, "_load_index", lambda: True)          # disk ada
    monkeypatch.setattr(a, "available", lambda: True)
    started = []
    monkeypatch.setattr(a.threading, "Thread",
                        lambda *ar, **k: type("T", (), {"start": lambda self: started.append(k.get("name"))})())
    assert a.start_scheduled_refresh() is True
    assert "accurate-index-bootstrap" not in started            # TAK bootstrap (ada disk)
    assert "accurate-index-refresh" in started                  # loop jadwal tetap jalan


def test_start_disk_kosong_bootstrap_sekali(monkeypatch):
    monkeypatch.setattr(a, "_sched_started", False)
    monkeypatch.setattr(a, "_load_index", lambda: False)         # disk kosong
    monkeypatch.setattr(a, "available", lambda: True)
    started = []
    monkeypatch.setattr(a.threading, "Thread",
                        lambda *ar, **k: type("T", (), {"start": lambda self: started.append(k.get("name"))})())
    a.start_scheduled_refresh()
    assert "accurate-index-bootstrap" in started                # bootstrap sekali


def test_start_idempoten(monkeypatch):
    monkeypatch.setattr(a, "_load_index", lambda: True)
    monkeypatch.setattr(a, "_scheduled_refresh_loop", lambda: None)
    monkeypatch.setattr(a, "available", lambda: True)
    monkeypatch.setattr(a.threading, "Thread",
                        lambda *ar, **k: type("T", (), {"start": lambda self: None})())
    monkeypatch.setattr(a, "_sched_started", False)
    assert a.start_scheduled_refresh() is True
    assert a.start_scheduled_refresh() is False


# ── refresh terjadwal melepas sesi (logout) setelah selesai ──────────────────

def test_do_scheduled_refresh_logout_di_akhir(monkeypatch):
    monkeypatch.setattr(a, "available", lambda: True)
    monkeypatch.setattr(a, "refresh", lambda force=False: {"items": []})
    monkeypatch.setattr(a, "enrich_warehouses", lambda: 0)
    monkeypatch.setattr(a, "_save_index", lambda: None)
    monkeypatch.setitem(a._index_cache, "items", [])
    calls = {"logout": 0}
    monkeypatch.setattr(a, "logout", lambda: calls.__setitem__("logout", calls["logout"] + 1))
    a._do_scheduled_refresh()
    assert calls["logout"] == 1        # sesi dilepas setelah window
