"""Lembar diagnosa terstruktur (dtc_diagnosa — isi 216 kartu PDF data/Fault)
kini DIBACA model, bukan cuma dilampirkan sbg kartu. Fase 2.5-K2 rombakan.
"""
from __future__ import annotations

from app.services import ai_assistant as ai
from app.services import dtc_codes, dtc_diagnosa

ADMIN = {"username": "admin", "role": "admin"}


def test_dataset_lengkap_216_kartu():
    assert dtc_diagnosa.available()
    assert dtc_diagnosa.count() == 216
    # tiap kartu wajib punya penyebab & langkah (fail-loud builder menjaminnya)
    for r in dtc_diagnosa._load():
        assert r["penyebab"] and r["langkah"], r["file"]
        assert r["kode"].startswith(("P", "U"))


def test_for_pair_dan_for_code():
    rows = dtc_diagnosa.for_pair(100, 15)
    assert rows and rows[0]["kode"] == "P0523"
    assert "oil" in rows[0]["judul"].lower() or "oli" in rows[0]["judul"].lower()
    assert dtc_diagnosa.for_pair(None, None) == []
    assert dtc_diagnosa.for_code("P0523")
    # varian (E5_020E / E5_0211) → 2 kartu utk pasangan yang sama
    assert len(dtc_diagnosa.for_pair(1387, 0)) == 2


def test_store_kanonik_flag_kartu_dan_sumber_kartu():
    ber_kartu = [r for r in dtc_codes.rows() if r.get("kartu")]
    assert ber_kartu
    # pasangan PDF-only masuk sbg sumber='kartu' dgn deskripsi judul kartu
    for r in dtc_codes.rows("kartu"):
        assert r["kartu"] is True and r["spn"] is not None and r["deskripsi"]


def test_handler_cari_kode_sertakan_diagnosa_rinci():
    r = ai._t_cari_kode_kesalahan({"spn": 100, "fmi": 15}, ADMIN)
    assert r["found"]
    assert r["diagnosa_rinci"] and r["diagnosa_rinci"][0]["langkah"]
    assert "diagnosa_rinci" in r["catatan"]


def test_handler_pasangan_pdf_only_tetap_ditemukan():
    # salah satu dari 32 pasangan tanpa baris tabel Bosch — dulu model buta isinya
    pdf_only = dtc_codes.rows("kartu")[0]
    r = ai._t_cari_kode_kesalahan({"spn": pdf_only["spn"], "fmi": pdf_only["fmi"]}, ADMIN)
    assert r["found"]
    assert r["diagnosa_rinci"]
