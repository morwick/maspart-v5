"""Indeks berat SIMS persisten + warmer + integrasi weight_for (SIMS utama)."""
import json

import pytest

from app.services import sims_weights, harga


@pytest.fixture(autouse=True)
def tmp_index(tmp_path, monkeypatch):
    f = tmp_path / "sims_weights.json"
    monkeypatch.setattr(sims_weights, "_file", lambda: f)
    monkeypatch.setattr(sims_weights, "_map", None)   # reset cache tiap test
    monkeypatch.setattr(sims_weights, "_THROTTLE", 0)  # tanpa jeda di test
    return f


def test_put_get_dan_persist(tmp_index):
    sims_weights.put("wg123", 8800)
    assert sims_weights.get("WG123") == 8800                 # case-insensitif
    assert json.loads(tmp_index.read_text())["WG123"] == 8800  # tersimpan ke disk
    sims_weights._map = None                                  # reload dari file
    assert sims_weights.get("wg123") == 8800


def test_put_abaikan_nol_dan_kosong(tmp_index):
    sims_weights.put("x", 0)
    sims_weights.put("", 500)
    assert sims_weights.get("x") == 0 and sims_weights.get("") == 0


def test_fetch_and_store(monkeypatch, tmp_index):
    monkeypatch.setattr(sims_weights.sims, "get_part_weight_grams",
                        lambda pn: 7430 if pn == "VG1" else 0)
    assert sims_weights.fetch_and_store("VG1") == 7430
    assert sims_weights.get("VG1") == 7430
    assert sims_weights.fetch_and_store("NOPE") == 0
    assert sims_weights.get("NOPE") == 0                      # 0 tak disimpan


def test_warm_once_lewati_yang_sudah_ada(monkeypatch, tmp_index):
    monkeypatch.setattr(sims_weights.sims, "_SIMS_OK", True)
    monkeypatch.setattr(sims_weights, "_priced_pns", lambda: {"A", "B", "C"})
    sims_weights.put("A", 1000)                               # sudah ada → skip
    fetched = []
    monkeypatch.setattr(sims_weights, "fetch_and_store",
                        lambda pn: fetched.append(pn) or 500)
    r = sims_weights.warm_once()
    assert set(fetched) == {"B", "C"}                         # A dilewati (cached)
    assert r["fetched"] == 2 and r["with_weight"] == 2


def test_warm_once_sims_mati(monkeypatch, tmp_index):
    monkeypatch.setattr(sims_weights.sims, "_SIMS_OK", False)
    assert sims_weights.warm_once().get("skipped") == "sims off"


def test_weight_for_sims_utama_lalu_fallback(monkeypatch, tmp_index):
    monkeypatch.setattr(harga, "_ensure", lambda: None)       # jangan baca harga.xlsx
    # (a) SIMS punya → menang atas berat manual harga.xlsx
    sims_weights.put("PN1", 5000)
    monkeypatch.setattr(harga, "_weight_map", {"PN1": 9999, "PN2": 3000})
    assert harga.weight_for("PN1") == 5000                    # SIMS utama
    # (b) SIMS kosong → fallback berat manual
    assert harga.weight_for("PN2") == 3000
    # (c) dua-duanya kosong → 0
    assert harga.weight_for("PN3") == 0
