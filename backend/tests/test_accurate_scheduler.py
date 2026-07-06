"""Refresh indeks Accurate TERJADWAL (menu Stok) — logika satu-iterasi & idempotensi.

Tanpa jaringan: available/refresh di-monkeypatch.
"""
from __future__ import annotations

from app.services import accurate


def test_once_refresh_dipanggil_force(monkeypatch):
    calls = {}
    monkeypatch.setattr(accurate, "available", lambda: True)
    monkeypatch.setattr(accurate, "refresh", lambda force=False: calls.setdefault("force", force))
    wait = accurate._scheduled_refresh_once()
    assert calls["force"] is True
    assert wait == accurate._INDEX_TTL


def test_once_skip_bila_tak_dikonfigurasi(monkeypatch):
    monkeypatch.setattr(accurate, "available", lambda: False)

    def _boom(force=False):
        raise AssertionError("refresh tak boleh dipanggil saat Accurate tak dikonfigurasi")

    monkeypatch.setattr(accurate, "refresh", _boom)
    assert accurate._scheduled_refresh_once() == accurate._INDEX_TTL


def test_once_gagal_return_interval_retry(monkeypatch):
    monkeypatch.setattr(accurate, "available", lambda: True)

    def _fail(force=False):
        raise RuntimeError("sesi mati")

    monkeypatch.setattr(accurate, "refresh", _fail)
    wait = accurate._scheduled_refresh_once()
    assert wait == accurate._SCHED_RETRY
    assert wait < accurate._INDEX_TTL  # gagal harus dicoba ulang LEBIH CEPAT dari TTL


def test_start_idempoten(monkeypatch):
    # Jangan jalankan loop nyata (refresh sungguhan) — cukup thread no-op.
    monkeypatch.setattr(accurate, "_scheduled_refresh_loop", lambda: None)
    monkeypatch.setattr(accurate, "_sched_started", False)
    assert accurate.start_scheduled_refresh() is True
    assert accurate.start_scheduled_refresh() is False  # kedua kali: sudah jalan
