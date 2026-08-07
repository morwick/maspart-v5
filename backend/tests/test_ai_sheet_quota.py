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


# ── TTL dihitung dari akses TERAKHIR (touch-on-read) ────────────────────────
# Umur diuji dgn memundurkan cap waktu di stash (bukan menambal time.monotonic)
# supaya deterministik & tak mengganggu apa pun di luar test.

def _tuakan(sid: str, detik: float) -> None:
    ai_sheet._stash[sid]["at"] -= detik


def test_akses_menyegarkan_umur_stash():
    sid = ai_sheet.put_sheet("budi", _isi(1))
    _tuakan(sid, 5000)                       # ±83 menit lalu
    lama = ai_sheet._stash[sid]["at"]
    assert ai_sheet.get_sheet(sid, "budi") == _isi(1)
    assert ai_sheet._stash[sid]["at"] > lama


def test_lampiran_yang_dipakai_tak_kedaluwarsa_di_tengah_jalan():
    """Percakapan panjang (isi stok → tambah harga → rekap → gambar exploded)
    bisa melampaui TTL 2 jam. Dulu file 'hilang' padahal justru sedang dikerjakan."""
    sid = ai_sheet.put_sheet("budi", _isi(1))
    for _ in range(4):                       # dipakai tiap ±1,5 jam
        _tuakan(sid, 1.5 * 3600)
        assert ai_sheet.get_sheet(sid, "budi") == _isi(1)
    _tuakan(sid, 2.5 * 3600)                 # baru DITINGGAL lebih lama dari TTL
    assert ai_sheet.get_sheet(sid, "budi") is None


def test_akses_user_lain_tak_menahan_lampiran_tetap_hidup():
    """Hanya akses SUKSES yang menyegarkan — kalau tidak, orang lain bisa
    menahan lampiran orang lain di RAM hanya dengan menebak sheet_id."""
    sid = ai_sheet.put_sheet("budi", _isi(1))
    _tuakan(sid, 5000)
    lama = ai_sheet._stash[sid]["at"]
    assert ai_sheet.get_sheet(sid, "siti") is None
    assert ai_sheet._stash[sid]["at"] == lama


def test_eviksi_kuota_memilih_yang_benar_benar_idle(monkeypatch):
    """Efek samping yang diinginkan: min(at) di put_sheet kini berarti 'paling
    lama tak disentuh', bukan 'paling dulu diunggah'."""
    monkeypatch.setattr(ai_sheet, "_STASH_MAX_PER_USER", 2)
    a = ai_sheet.put_sheet("budi", _isi(1))
    b = ai_sheet.put_sheet("budi", _isi(2))
    _tuakan(a, 3600)                         # a paling tua…
    assert ai_sheet.get_sheet(a, "budi") == _isi(1)   # …tapi BARU dipakai
    ai_sheet.put_sheet("budi", _isi(3))      # kuota penuh → satu harus pergi
    assert ai_sheet.get_sheet(a, "budi") == _isi(1)   # yang aktif selamat
    assert ai_sheet.get_sheet(b, "budi") is None      # yang idle tergusur
