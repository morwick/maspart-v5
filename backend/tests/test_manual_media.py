"""Test service manual_media — perpustakaan gambar manual (data/manual_media).

Fokus: (1) index dari index.json (available/count/labels), (2) search HANYA
gambar dicari=True (label+deskripsi+sinonim+sumber+tipe, batas kata),
(3) for_page menautkan gambar per-halaman sumber (termasuk dicari=False),
(4) image_bytes valid + anti path-traversal (nama aneh/tak terdaftar → None),
(5) index hilang → available False & search []. Fixture direktori sintetis.
"""
import json

import pytest

from app.services import manual_media

_PNG = b"\x89PNG\r\n\x1a\n uji gambar"

_ROWS = [
    {
        "file": "mcnat5_p003_1.png",
        "label": "Skema/pinout konektor ECU Bosch mesin MC National V",
        "deskripsi": "Gambar konektor ECU-A dan ECU-K mesin MC National V.",
        "sinonim": ["pinout", "konektor ecu", "ecu bosch", "mc national v"],
        "sumber": "MC NATIONAL V.pdf", "halaman": 3, "tipe": "pinout",
        "dicari": True,
    },
    {
        "file": "xl_shantui_bulldoze_sd16_0.png",
        "label": "Foto unit SD16",
        "deskripsi": "Foto bulldozer Shantui SD16.",
        "sinonim": ["foto unit", "bulldozer", "sd16"],
        "sumber": "Shantui Bulldozer.xlsx", "halaman": None, "tipe": "foto_unit",
        "dicari": True,
    },
    {
        "file": "boschcn_p021_0.png",
        "label": "Diagram manual Bosch ECU mesin MC — hal 21",
        "deskripsi": "", "sinonim": [],
        "sumber": "Manual_Sinotruk_MC_BOSCHECU_DH_CHINESE.pdf",
        "halaman": 21, "tipe": "diagram", "dicari": False,  # naratif, tak dicari
    },
]


@pytest.fixture()
def dunia(tmp_path, monkeypatch):
    d = tmp_path / "manual_media"
    d.mkdir(parents=True)
    (d / "index.json").write_text(
        json.dumps(_ROWS, ensure_ascii=False, indent=1) + "\n",
        encoding="utf-8", newline="\n")
    for r in _ROWS:
        (d / r["file"]).write_bytes(_PNG)

    class _S:
        data_path = tmp_path

    monkeypatch.setattr(manual_media, "get_settings", lambda: _S())
    monkeypatch.setattr(manual_media, "_CACHE", {"mtime": None, "rows": []})
    return tmp_path


def test_available_dan_labels(dunia):
    assert manual_media.available() is True
    assert manual_media.count() == 3
    labs = manual_media.labels()
    assert any("MC National V" in x for x in labs)
    assert any("SD16" in x for x in labs)


def test_search_hanya_dicari(dunia):
    # 'pinout' → gambar ECU (dicari=True).
    r = manual_media.search("pinout")
    assert [x["file"] for x in r] == ["mcnat5_p003_1.png"]
    # 'foto unit SD16' (frasa multi-kata) → foto unit.
    r = manual_media.search("foto unit SD16")
    assert [x["file"] for x in r] == ["xl_shantui_bulldoze_sd16_0.png"]
    # gambar naratif dicari=False TIDAK muncul walau query cocok isinya.
    assert manual_media.search("boschcn") == []
    assert "boschcn_p021_0.png" not in \
        [x["file"] for x in manual_media.search("diagram bosch ecu")]
    # Query kosong → [].
    assert manual_media.search("") == []
    assert manual_media.search("   ") == []


def test_for_page_termasuk_dicari_false(dunia):
    r = manual_media.for_page(
        "Manual_Sinotruk_MC_BOSCHECU_DH_CHINESE.pdf", 21)
    assert [x["file"] for x in r] == ["boschcn_p021_0.png"]
    assert manual_media.for_page("MC NATIONAL V.pdf", 3)[0]["file"] == \
        "mcnat5_p003_1.png"
    assert manual_media.for_page("tak_ada.pdf", 1) == []
    assert manual_media.for_page("", 0) == []


def test_image_bytes_validasi(dunia):
    assert manual_media.image_bytes("mcnat5_p003_1.png") == _PNG
    # dicari=False tetap bisa dilayani (untuk tautan per-halaman).
    assert manual_media.image_bytes("boschcn_p021_0.png") == _PNG
    assert manual_media.image_bytes("../secrets.toml") is None    # nama aneh
    assert manual_media.image_bytes("../../etc/passwd") is None    # traversal
    assert manual_media.image_bytes("tak_terdaftar.png") is None   # tak di index


def test_index_hilang_kosong(tmp_path, monkeypatch):
    class _S:
        data_path = tmp_path  # tak ada manual_media/index.json

    monkeypatch.setattr(manual_media, "get_settings", lambda: _S())
    monkeypatch.setattr(manual_media, "_CACHE", {"mtime": None, "rows": []})
    assert manual_media.available() is False
    assert manual_media.count() == 0
    assert manual_media.labels() == []
    assert manual_media.search("pinout") == []
    assert manual_media.image_bytes("mcnat5_p003_1.png") is None
