"""Kamus Sinonim admin — CRUD sinonim.json + auto-reload asisten (per-mtime).

File dialihkan ke tmp_path agar test tidak menyentuh data/sinonim/sinonim.json
asli. Skenario kunci: entri baru dari admin LANGSUNG dipakai _expand_query
asisten tanpa restart (cache per-mtime).
"""
import json

import pytest

from app.services import ai_assistant as ai
from app.services import sinonim


@pytest.fixture(autouse=True)
def _tmp_file(tmp_path, monkeypatch):
    f = tmp_path / "sinonim.json"
    monkeypatch.setattr(sinonim, "_file", lambda: f)
    return f


def test_add_normalisasi_dan_tersimpan(_tmp_file):
    e = sinonim.add("Undercarriage", ["  Tapak Shoe ", "tapak shoe", ""], ["track plate", "Track Plate"])
    assert e == {"grup": "undercarriage", "triggers": ["Tapak Shoe"], "keywords": ["track plate"]}
    on_disk = json.loads(_tmp_file.read_text(encoding="utf-8"))
    assert on_disk == [e]


def test_add_tolak_trigger_duplikat():
    sinonim.add("a", ["tapak shoe"], ["track plate"])
    with pytest.raises(ValueError, match="sudah ada"):
        sinonim.add("b", ["TAPAK SHOE"], ["track shoe"])


def test_add_wajib_trigger_dan_keyword():
    with pytest.raises(ValueError):
        sinonim.add("x", [], ["track plate"])
    with pytest.raises(ValueError):
        sinonim.add("x", ["tapak shoe"], ["  "])


def test_update_dan_delete():
    sinonim.add("a", ["tapak shoe"], ["track plate"])
    sinonim.add("b", ["laher"], ["bearing"])

    sinonim.update(0, "a", ["tapak shoe", "tapak sepatu"], ["track plate", "track shoe"])
    assert sinonim.load()[0]["triggers"] == ["tapak shoe", "tapak sepatu"]

    gone = sinonim.delete(0)
    assert gone["triggers"][0] == "tapak shoe"
    assert [e["triggers"] for e in sinonim.load()] == [["laher"]]

    with pytest.raises(IndexError):
        sinonim.update(5, "x", ["a"], ["b"])
    with pytest.raises(IndexError):
        sinonim.delete(5)


def test_load_file_hilang_atau_korup(_tmp_file):
    assert sinonim.load() == []          # belum ada file
    _tmp_file.write_text("{rusak", encoding="utf-8")
    assert sinonim.load() == []          # korup → aman, list kosong


def test_asisten_langsung_pakai_entri_baru(tmp_path, monkeypatch):
    """Inti fitur: admin tambah 'tapak shoe' → _expand_query asisten langsung
    mengembalikan 'track plate' TANPA restart (reload per-mtime).
    Sejak rombakan 2026-07-17 lookup terpusat di services/sinonim (cache mtime
    per-path di knowledge_util) — asisten & CRUD tetap membaca FILE YANG SAMA."""
    real = tmp_path / "sinonim" / "sinonim.json"
    monkeypatch.setattr(sinonim, "_file", lambda: real)

    # Sebelum ditambah: tak ada ekspansi.
    terms, matched = ai._expand_query("harga tapak shoe SD16")
    assert matched == []

    # Admin menambah lewat service (persis alur endpoint).
    sinonim.add("undercarriage", ["tapak shoe"], ["track plate", "track shoe"])

    # Tanpa restart: query berikut langsung terekspansi.
    terms, matched = ai._expand_query("harga tapak shoe SD16")
    assert matched == ["tapak shoe"]
    assert "track plate" in terms and "track shoe" in terms
