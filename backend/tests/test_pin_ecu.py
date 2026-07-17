"""Definisi PIN ECU Bosch MC (pin_ecu, K4 rombakan) — dataset + fan-out
lewat tool diagram_wiring tanpa tool baru.
"""
from __future__ import annotations

from app.services import ai_assistant as ai
from app.services import pin_ecu

ADMIN = {"username": "admin", "role": "admin"}


def test_dataset_pin_ecu_multi_ecu():
    assert pin_ecu.available()
    assert pin_ecu.count() >= 500  # 302 manual Bosch CN + 4 PDF English (2026-07-18)
    ecus = {r["ecu"] for r in pin_ecu._load()}
    assert {"MC (manual Bosch CN)", "Mesin MC National V (Bosch ECU)",
            "NanoBCU (body control unit)", "NBCU (body control unit)",
            "ZF-AMT (transmisi otomatis)"} <= ecus
    for r in pin_ecu._load():
        assert r["pin"] and (r["sinyal"] or r["deskripsi"])


def test_search_kode_pin_eksak_dan_keyword():
    eksak = pin_ecu.search("K54")
    assert eksak and all(r["pin"] == "K54" for r in eksak)
    kw = pin_ecu.search("SCR")
    assert kw and any("SCR" in f"{r['sinyal']} {r['deskripsi']}" for r in kw)
    assert pin_ecu.search("") == []
    # pin NBCU format X<konektor>.<pin> + keyword lintas-ECU (haystack ecu ikut)
    nbcu = pin_ecu.search("X1.4")
    assert nbcu and nbcu[0]["ecu"].startswith("NBCU")
    amt = pin_ecu.search("ZF-AMT")
    assert amt and all("ZF-AMT" in r["ecu"] for r in amt)


def test_diagram_wiring_sertakan_pin_ecu(monkeypatch):
    # diagram tak ketemu TAPI pin cocok → found=True + payload pin_ecu.
    monkeypatch.setattr(ai.wiring_ref, "available", lambda: True)
    monkeypatch.setattr(ai.wiring_ref, "search", lambda q, limit=6: [])
    monkeypatch.setattr(ai.wiring_ref, "labels", lambda: ["ECU"])
    r = ai._t_diagram_wiring({"komponen": "K54"}, ADMIN)
    assert r["found"] is True
    assert r["pin_ecu"] and r["pin_ecu"][0]["pin"] == "K54"
    assert "pin_ecu" in r["catatan"]


def test_diagram_wiring_sertakan_kartu_skema(monkeypatch):
    # diagram & pin tak ketemu TAPI skema PDF cocok → kartu pdf_skema + found.
    monkeypatch.setattr(ai.wiring_ref, "available", lambda: True)
    monkeypatch.setattr(ai.wiring_ref, "search", lambda q, limit=6: [])
    monkeypatch.setattr(ai.wiring_ref, "labels", lambda: ["ECU"])
    monkeypatch.setattr(ai.skema_ref, "search", lambda q, limit=3: [
        {"file": "abs_wabco_6x4_traktor.pdf",
         "label": "Skema pneumatik ABS 6x4 — traktor (WABCO)", "deskripsi": "d"}])
    monkeypatch.setattr(ai.skema_ref, "pdf_bytes", lambda f: b"%PDF-dummy")
    monkeypatch.setattr(ai.ai_export, "stash_raw",
                        lambda judul, data, fname: ("EXP1", fname))
    r = ai._t_diagram_wiring({"komponen": "rem angin wabco"}, ADMIN)
    assert r["found"] is True
    assert r["pdf_skema"][0]["export_id"] == "EXP1"
    assert "pdf_skema" in r["catatan"]
