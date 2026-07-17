"""Test service skema_ref — index skema/manual PDF (data/manuals/skema).

Fokus: (1) index dari index.json (available/count/labels), (2) search sinonim
batas-kata ('rem angin' → ABS; 'tft' → manual; 'kabin' TIDAK nyangkut 'kb'),
(3) pdf_bytes valid + anti path-traversal (nama aneh/tak terdaftar → None),
(4) index hilang → available False & search []. Fixture pakai direktori
sintetis (tmp_path) supaya deterministik.
"""
import json

import pytest

from app.services import skema_ref

_PDF = b"%PDF-1.4 uji skema"

_ROWS = [
    {
        "file": "abs_kb_6x4_traktor.pdf",
        "label": "Skema pneumatik ABS 6x4 — traktor (tipe KB)",
        "deskripsi": "Skema jalur angin sistem rem ABS traktor 6x4 tipe KB.",
        "sinonim": ["abs", "angin", "pneumatik", "rem angin", "traktor", "kb", "trailer"],
    },
    {
        "file": "manual_tft_nanobcu.pdf",
        "label": "Manual pelatihan servis instrumen TFT NanoBCU (Bahasa Indonesia)",
        "deskripsi": "Manual servis panel instrumen TFT NanoBCU dalam Bahasa Indonesia.",
        "sinonim": ["tft", "instrumen", "cluster", "dashboard", "nanobcu", "spidometer"],
    },
]


@pytest.fixture()
def dunia(tmp_path, monkeypatch):
    d = tmp_path / "manuals" / "skema"
    d.mkdir(parents=True)
    (d / "index.json").write_text(
        json.dumps(_ROWS, ensure_ascii=False, indent=1) + "\n",
        encoding="utf-8", newline="\n")
    for r in _ROWS:
        (d / r["file"]).write_bytes(_PDF)

    class _S:
        data_path = tmp_path

    monkeypatch.setattr(skema_ref, "get_settings", lambda: _S())
    monkeypatch.setattr(skema_ref, "_CACHE", {"mtime": None, "rows": []})
    return tmp_path


def test_available_dan_labels(dunia):
    assert skema_ref.available() is True
    assert skema_ref.count() == 2
    labs = skema_ref.labels()
    assert any("ABS 6x4" in x for x in labs)
    assert any("TFT NanoBCU" in x for x in labs)


def test_search_sinonim_dan_batas_kata(dunia):
    # 'rem angin' (frasa sinonim) → hanya ABS.
    r = skema_ref.search("rem angin")
    assert [x["file"] for x in r] == ["abs_kb_6x4_traktor.pdf"]
    # 'tft' → hanya manual instrumen.
    r = skema_ref.search("tft")
    assert [x["file"] for x in r] == ["manual_tft_nanobcu.pdf"]
    # 'kabin' mengandung substring 'kb' TAPI batas kata → TIDAK kena ABS (kb).
    assert skema_ref.search("kabin") == []
    # Query kosong → [].
    assert skema_ref.search("") == []
    assert skema_ref.search("   ") == []


def test_pdf_bytes_validasi(dunia):
    assert skema_ref.pdf_bytes("abs_kb_6x4_traktor.pdf") == _PDF
    assert skema_ref.pdf_bytes("../secrets.toml") is None    # nama aneh
    assert skema_ref.pdf_bytes("../../etc/passwd") is None    # traversal
    assert skema_ref.pdf_bytes("tak_terdaftar.pdf") is None   # valid tapi tak di index


def test_index_hilang_kosong(tmp_path, monkeypatch):
    class _S:
        data_path = tmp_path  # tak ada manuals/skema/index.json

    monkeypatch.setattr(skema_ref, "get_settings", lambda: _S())
    monkeypatch.setattr(skema_ref, "_CACHE", {"mtime": None, "rows": []})
    assert skema_ref.available() is False
    assert skema_ref.count() == 0
    assert skema_ref.labels() == []
    assert skema_ref.search("abs") == []
    assert skema_ref.pdf_bytes("abs_kb_6x4_traktor.pdf") is None
