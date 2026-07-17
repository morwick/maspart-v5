"""Definisi PIN ECU Bosch MC (pin_ecu, K4 rombakan) — dataset + fan-out
lewat tool diagram_wiring tanpa tool baru.
"""
from __future__ import annotations

from app.services import ai_assistant as ai
from app.services import pin_ecu

ADMIN = {"username": "admin", "role": "admin"}


def test_dataset_pin_ecu():
    assert pin_ecu.available()
    assert pin_ecu.count() >= 300
    for r in pin_ecu._load():
        assert r["pin"] and (r["sinyal"] or r["deskripsi"])


def test_search_kode_pin_eksak_dan_keyword():
    eksak = pin_ecu.search("K54")
    assert eksak and all(r["pin"] == "K54" for r in eksak)
    kw = pin_ecu.search("SCR")
    assert kw and any("SCR" in f"{r['sinyal']} {r['deskripsi']}" for r in kw)
    assert pin_ecu.search("") == []


def test_diagram_wiring_sertakan_pin_ecu(monkeypatch):
    # diagram tak ketemu TAPI pin cocok → found=True + payload pin_ecu.
    monkeypatch.setattr(ai.wiring_ref, "available", lambda: True)
    monkeypatch.setattr(ai.wiring_ref, "search", lambda q, limit=6: [])
    monkeypatch.setattr(ai.wiring_ref, "labels", lambda: ["ECU"])
    r = ai._t_diagram_wiring({"komponen": "K54"}, ADMIN)
    assert r["found"] is True
    assert r["pin_ecu"] and r["pin_ecu"][0]["pin"] == "K54"
    assert "pin_ecu" in r["catatan"]
