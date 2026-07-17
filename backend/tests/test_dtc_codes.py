"""Store DTC kanonik (dtc_codes) + util bersama (knowledge_util) — Fase 1 rombakan.

Menjaga: skema union bertanda sumber, proyeksi legacy identik dgn bentuk lama,
deskripsi Bosch Indonesia terisi, jembatan repair_for, dan determinisme writer.
"""
from __future__ import annotations

import gzip
import json

from app.services import abs_scr_codes, dtc_codes, eol_dtc, fault_codes
from app.services.knowledge_util import (apply_i18n, dump_strings, load_json,
                                         norm_pn, write_json_gz)


# ── knowledge_util ────────────────────────────────────────────────────
def test_norm_pn_union_dua_varian():
    assert norm_pn(" wg9725160004/1 ") == "WG9725160004/1".replace("/", "")
    assert norm_pn("AZ9725-160004") == "AZ9725160004"
    assert norm_pn("612600（A）") == "612600A"
    assert norm_pn(None) == ""


def test_write_json_gz_deterministik(tmp_path):
    rows = [{"b": 1, "a": "x"}, {"a": "y", "b": None}]
    p1 = tmp_path / "a.json.gz"
    p2 = tmp_path / "b.json.gz"
    write_json_gz(p1, rows)
    write_json_gz(p2, list(rows))
    assert p1.read_bytes() == p2.read_bytes()  # byte-stable (mtime gzip = 0)
    assert load_json(p1) == json.loads(gzip.decompress(p1.read_bytes()))


def test_load_json_cache_mtime(tmp_path):
    p = tmp_path / "d.json"
    p.write_text('[{"x":1}]', encoding="utf-8")
    assert load_json(p) == [{"x": 1}]
    assert load_json(tmp_path / "tidak_ada.json") == []
    assert load_json(tmp_path / "tidak_ada2.json", default={}) == {}


def test_apply_i18n_string_dan_list():
    rows = [{"deskripsi": "engine oil", "langkah": ["check oil", "unknown step"]}]
    kamus = {"engine oil": "oli mesin", "check oil": "cek oli"}
    miss = apply_i18n(rows, kamus, ("deskripsi", "langkah"))
    assert rows[0]["deskripsi"] == "oli mesin"
    assert rows[0]["langkah"] == ["cek oli", "unknown step"]  # fallback aman
    assert miss == 1
    assert dump_strings([{"a": "x", "b": ["y", "x"]}], ("a", "b")) == ["x", "y"]


# ── store kanonik ─────────────────────────────────────────────────────
def test_union_jumlah_per_sumber():
    assert dtc_codes.available()
    assert dtc_codes.count() == 7778
    assert dtc_codes.count("bosch") == 2276
    assert dtc_codes.count("eol") == 5254
    assert dtc_codes.count("abs") + dtc_codes.count("scr") == 216
    assert dtc_codes.count("kartu") == 32  # pasangan PDF-only (K2)


def test_semua_baris_punya_sumber_dan_unit():
    for r in dtc_codes.rows():
        assert r["sumber"] in ("bosch", "eol", "abs", "scr", "kartu")
        assert r["unit"]


def test_deskripsi_bosch_indonesia_terisi():
    bosch = dtc_codes.rows("bosch")
    terisi = sum(1 for r in bosch if r["deskripsi"])
    assert terisi / len(bosch) >= 0.90  # gate Fase 1
    # teks asli China tetap dibawa sebagai fallback/audit
    assert all("deskripsi_cn" in r for r in bosch)


def test_proyeksi_legacy_identik_dgn_shim():
    # shim service lama = proyeksi store kanonik (satu sumber kebenaran)
    assert fault_codes._load() is dtc_codes.legacy_bosch()
    assert eol_dtc._load() is dtc_codes.legacy_eol()
    assert abs_scr_codes._load() is dtc_codes.legacy_abs_scr()
    assert len(fault_codes._load()) == 2276
    assert len(eol_dtc._load()) == 5254
    assert len(abs_scr_codes._load()) == 216


def test_legacy_bosch_bawa_deskripsi_indonesia():
    hits = fault_codes.search(code="P0645")
    assert hits
    assert "deskripsi" in hits[0]        # kolom baru (aditif)
    assert "desc_cn" in hits[0]          # kolom lama tetap ada


def test_repair_for_jembatan_konsisten():
    r1 = dtc_codes.repair_for("P0645")
    r2 = eol_dtc.repair_for("P0645")
    assert r1 == r2
    if r1 is not None:
        assert r1.get("perbaikan")


def test_units_per_sumber():
    assert dtc_codes.units("bosch") == ["EMS"]
    assert dtc_codes.units("abs") == ["ABS"]
    assert len(dtc_codes.units("eol")) >= 40  # 52 unit ECU EOL
