"""Persist build indeks item unit yang PARSIAL (ada node gagal dibuka).

Sebelumnya build parsial cuma di RAM → restart/redeploy = rebuild 56-84 dtk lagi
untuk unit besar. Kini parsial dipersist ke disk TAPI dgn TTL pendek (1 jam) +
flag incomplete, jadi: (a) tak rebuild berulang dalam 1 jam, (b) tetap dibangun
ulang setelah TTL agar cakupan menyusul lengkap, (c) items_index_ready TETAP False
untuk parsial (jalur teliti-instan hanya untuk indeks yang benar-benar lengkap).

⚠️ conftest melarang test membangun indeks EPC nyata — semua node/HTTP di-mock.
"""
import json

import pytest

from app.services import epc_bom as E

# Referensi ASLI (diambil sebelum fixture conftest mem-patch-nya jadi False).
_REAL_READY = E.items_index_ready


def _fake_disk(monkeypatch, tmp_path):
    monkeypatch.setattr(E, "_items_disk_path", lambda fr: tmp_path / f"{fr}.json")


# ── round-trip disk: flag incomplete & TTL ───────────────────────────────────
def test_disk_save_load_lengkap(monkeypatch, tmp_path):
    _fake_disk(monkeypatch, tmp_path)
    E._items_disk_save("FR1", [{"pn": "A", "nama": "x", "nama_cn": ""}], incomplete=False)
    d = E._items_disk_load("FR1")
    assert d is not None and d["incomplete"] is False and d["rows"][0]["pn"] == "A"


def test_disk_save_load_parsial(monkeypatch, tmp_path):
    _fake_disk(monkeypatch, tmp_path)
    E._items_disk_save("FR2", [{"pn": "A", "nama": "x", "nama_cn": ""}], incomplete=True)
    d = E._items_disk_load("FR2")
    assert d is not None and d["incomplete"] is True


def test_file_lama_tanpa_flag_dianggap_lengkap(monkeypatch, tmp_path):
    """Kompatibel mundur: file yang ditulis versi lama (tanpa key 'incomplete')
    dibaca sebagai LENGKAP, bukan parsial."""
    _fake_disk(monkeypatch, tmp_path)
    p = tmp_path / "FR3.json"
    p.write_text(json.dumps({"ts": E.time.time(),
                             "rows": [{"pn": "A", "nama": "x", "nama_cn": ""}]}),
                 encoding="utf-8")
    d = E._items_disk_load("FR3")
    assert d is not None and d["incomplete"] is False


def test_parsial_kedaluwarsa_1_jam(monkeypatch, tmp_path):
    """Parsial berumur > _ITEMS_PARTIAL_TTL → None (dibangun ulang di akses berikut)."""
    _fake_disk(monkeypatch, tmp_path)
    p = tmp_path / "FR4.json"
    p.write_text(json.dumps({"ts": E.time.time() - E._ITEMS_PARTIAL_TTL - 10,
                             "rows": [{"pn": "A", "nama": "x", "nama_cn": ""}],
                             "incomplete": True}), encoding="utf-8")
    assert E._items_disk_load("FR4") is None


def test_lengkap_masih_valid_lewat_1_jam(monkeypatch, tmp_path):
    """Build LENGKAP tetap dipercaya jauh melewati 1 jam (TTL 7 hari)."""
    _fake_disk(monkeypatch, tmp_path)
    p = tmp_path / "FR5.json"
    p.write_text(json.dumps({"ts": E.time.time() - E._ITEMS_PARTIAL_TTL - 10,
                             "rows": [{"pn": "A", "nama": "x", "nama_cn": ""}],
                             "incomplete": False}), encoding="utf-8")
    assert E._items_disk_load("FR5") is not None


# ── items_index_ready: parsial ≠ siap ────────────────────────────────────────
def test_index_ready_hanya_untuk_lengkap(monkeypatch, tmp_path):
    _fake_disk(monkeypatch, tmp_path)
    monkeypatch.setattr(E, "_frame", lambda r: r)
    E._items_all_cache.pop("FR6", None)

    E._items_disk_save("FR6", [{"pn": "A", "nama": "x", "nama_cn": ""}], incomplete=True)
    assert _REAL_READY("FR6") is False       # parsial

    E._items_disk_save("FR6", [{"pn": "A", "nama": "x", "nama_cn": ""}], incomplete=False)
    assert _REAL_READY("FR6") is True         # lengkap
    E._items_all_cache.pop("FR6", None)
