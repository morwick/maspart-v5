"""Kuota stash lampiran Excel (ai_sheet).

Dulu hanya ada pagar GLOBAL `_STASH_MAX`, sehingga unggahan user lain menggusur
lampiran yang sedang dipakai user ini sebelum TTL habis. Dari sisi user, asisten
mendadak "lupa" ada file — tanpa sebab yang terlihat.
"""
from __future__ import annotations

import pytest

from app.services import ai_sheet


@pytest.fixture(autouse=True)
def _bersih():
    ai_sheet._stash.clear()
    yield
    ai_sheet._stash.clear()


def _isi(n: int) -> dict:
    return {"ok": True, "n": n}


def test_kuota_per_user_menggusur_milik_sendiri(monkeypatch):
    monkeypatch.setattr(ai_sheet, "_STASH_MAX_PER_USER", 2)
    a = ai_sheet.put_sheet("budi", _isi(1))
    b = ai_sheet.put_sheet("budi", _isi(2))
    c = ai_sheet.put_sheet("budi", _isi(3))
    assert ai_sheet.get_sheet(a, "budi") is None      # tertua milik budi tergusur
    assert ai_sheet.get_sheet(b, "budi") == _isi(2)
    assert ai_sheet.get_sheet(c, "budi") == _isi(3)


def test_unggahan_user_lain_tak_menggusur(monkeypatch):
    """Inti perbaikan: kerugian ditanggung pengunggahnya sendiri, bukan orang lain."""
    monkeypatch.setattr(ai_sheet, "_STASH_MAX_PER_USER", 2)
    monkeypatch.setattr(ai_sheet, "_STASH_MAX", 40)
    punya_budi = ai_sheet.put_sheet("budi", _isi(1))
    for i in range(10):
        ai_sheet.put_sheet(f"orang{i}", _isi(100 + i))
    assert ai_sheet.get_sheet(punya_budi, "budi") == _isi(1)


def test_pagar_global_tetap_ada(monkeypatch):
    """Jaring terakhir RAM: banyak user aktif tak boleh membuat stash tumbuh terus."""
    monkeypatch.setattr(ai_sheet, "_STASH_MAX_PER_USER", 5)
    monkeypatch.setattr(ai_sheet, "_STASH_MAX", 4)
    for i in range(10):
        ai_sheet.put_sheet(f"orang{i}", _isi(i))
    assert len(ai_sheet._stash) <= 4


def test_sheet_user_lain_tetap_tak_terbaca():
    sid = ai_sheet.put_sheet("budi", _isi(1))
    assert ai_sheet.get_sheet(sid, "siti") is None


def test_kedaluwarsa_dibuang(monkeypatch):
    sid = ai_sheet.put_sheet("budi", _isi(1))
    monkeypatch.setattr(ai_sheet, "_STASH_TTL_SEC", -1)
    assert ai_sheet.get_sheet(sid, "budi") is None
