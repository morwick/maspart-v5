"""Test service manual_teks — isi manual teknik (Bosch ECU CN + TFT NanoBCU).

Fokus: (1) available/count/count_dicari, (2) search berperingkat pakai istilah
INDONESIA (kata_kunci/judul_id) walau teks aslinya China; dicari=False tak muncul;
cocok via 'kode'; query kosong → [], (3) for_page, (4) store hilang → aman.
Fixture = store .json.gz sintetis (tmp_path) + monkeypatch _DATA & bersihkan cache.
"""
import gzip
import json

import pytest

from app.services import knowledge_util, manual_teks

_ROWS = [
    {
        "sumber": "Manual_Sinotruk_MC_BOSCHECU_DH_CHINESE.pdf", "halaman": 181,
        "judul": "当有一个巡航控制按键被长按",
        "judul_id": "Gangguan tombol cruise control tertekan lama / macet",
        "kata_kunci": ["cruise control", "tombol macet", "saklar", "kabel", "gangguan"],
        "teks": "当有一个巡航控制按键被长按 故障可能原因 检查巡航开关",
        "tabel": [], "tipe": "gangguan", "dicari": True,
        "gambar_ref": ["boschcn_p181_0.png"],
        "blok": {"penyebab": ["1.按键长按"], "langkah": ["1、检查巡航开关"]},
    },
    {
        "sumber": "manual_tft_nanobcu.pdf", "halaman": 41,
        "judul": "转速、水温、机油压力表",
        "judul_id": "Tabel nilai sensor tachometer, suhu air, tekanan oli",
        "kata_kunci": ["nilai sensor", "tekanan oli", "suhu air", "tachometer", "panel tft"],
        "teks": "转速表传感器频率 仪表转速", "tabel": [["freq", "rpm"], ["0 Hz", "0 rpm"]],
        "tipe": "tabel", "dicari": True, "gambar_ref": ["tft_p041_0.png"],
    },
    {
        "sumber": "Manual_Sinotruk_MC_BOSCHECU_DH_CHINESE.pdf", "halaman": 61,
        "judul": "故障描述 J1939 DTC", "judul_id": "", "kata_kunci": [],
        "teks": "P0130 氧传感器 3056 26", "tabel": [["P0130", "3056", "26"]],
        "tipe": "tabel", "dicari": False, "gambar_ref": [], "kode": ["P0130"],
    },
]


@pytest.fixture()
def dunia(tmp_path, monkeypatch):
    p = tmp_path / "manual_teks.json.gz"
    with gzip.open(p, "wt", encoding="utf-8") as f:
        json.dump(_ROWS, f, ensure_ascii=False)
    monkeypatch.setattr(manual_teks, "_DATA", p)
    knowledge_util._LOAD_CACHE.clear()
    return tmp_path


def test_available_count(dunia):
    assert manual_teks.available() is True
    assert manual_teks.count() == 3
    assert manual_teks.count_dicari() == 2


def test_search_pakai_istilah_indonesia(dunia):
    # kueri Indonesia cocok via kata_kunci/judul_id meski teks asli China.
    r = manual_teks.search("cruise control tombol macet")
    assert r and r[0]["halaman"] == 181
    r = manual_teks.search("nilai sensor tekanan oli")
    assert r and r[0]["halaman"] == 41
    # dicari=False (tabel DTC) TIDAK muncul lewat topik...
    assert all(x["halaman"] != 61 for x in manual_teks.search("oksigen sensor"))
    # ...tapi query kosong → [].
    assert manual_teks.search("") == []
    assert manual_teks.search("   ") == []


def test_for_page(dunia):
    r = manual_teks.for_page("manual_tft_nanobcu.pdf", 41)
    assert r and "tachometer" in (r.get("judul_id") or "").lower()
    assert manual_teks.for_page("x.pdf", 1) is None
    assert manual_teks.for_page("", 0) is None


def test_store_hilang_aman(tmp_path, monkeypatch):
    monkeypatch.setattr(manual_teks, "_DATA", tmp_path / "tak_ada.json.gz")
    knowledge_util._LOAD_CACHE.clear()
    assert manual_teks.available() is False
    assert manual_teks.count() == 0
    assert manual_teks.search("cruise") == []
    assert manual_teks.for_page("x", 1) is None
