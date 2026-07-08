"""Gambar exploded view MESIN Weichai inline (padanan gambar_exploded Sinotruk):
epc_weichai.exploded_figures + renderer via token + tool + capture ke metadata.
Jaringan/render di-mock (tanpa EPC/resvg).
"""
import pytest

from app.services import ai_assistant as ai
from app.services import ai_export
from app.services import epc_weichai as wc

U = {"username": "mas", "role": "admin"}


# ── exploded_figures: saring figure yang memuat PN + balon (orderNo) ─────────
def test_exploded_figures_temukan_pn(monkeypatch):
    monkeypatch.setattr(wc, "catalog_walk", lambda r, k: {
        "found": True, "frame_number": "FR1", "figures": [
            {"nama": "Engine Block Group", "kategori": "WP12", "svg": "SVG1",
             "items": [{"pn": "612630010055", "balon": 5, "nama": "Cylinder Liner"},
                       {"pn": "1013955889", "balon": 10, "nama": "Block"}]},
            {"nama": "Fuel Group", "kategori": "WP12", "svg": "SVG2",
             "items": [{"pn": "1000076563", "balon": 2, "nama": "Injector"}]},
        ]})
    d = wc.exploded_figures("RJ1", "612630010055", "lengkap")
    assert d["found"] and len(d["figures"]) == 1
    assert d["figures"][0]["svg"] == "SVG1" and d["figures"][0]["balon"] == 5


def test_exploded_figures_pn_tak_ada(monkeypatch):
    monkeypatch.setattr(wc, "catalog_walk", lambda r, k: {
        "found": True, "frame_number": "FR1",
        "figures": [{"nama": "X", "svg": "S", "items": [{"pn": "AAA", "balon": 1}]}]})
    d = wc.exploded_figures("RJ1", "ZZZ", "lengkap")
    assert not d["found"] and d["_err"] == "not_found"


def test_exploded_figures_walk_gagal(monkeypatch):
    monkeypatch.setattr(wc, "catalog_walk", lambda r, k: {"found": False, "_err": "no_link"})
    d = wc.exploded_figures("X", "PN", "lengkap")
    assert not d["found"] and d["_err"] == "no_link"


# ── renderer: token di-mint ulang dari rangka, fetch_svg, highlight, PNG ──────
def test_exploded_png_weichai(monkeypatch):
    monkeypatch.setattr(wc, "_ensure_token", lambda r: "TOK" if r else "")
    monkeypatch.setattr(wc, "fetch_svg", lambda fid, tok: b"<svg><text x='1' y='1'>5</text></svg>")
    monkeypatch.setattr(ai_export, "_svg_to_png", lambda svg, width=1400: b"\x89PNG\r\n\x1a\nOK")
    out = ai_export.exploded_png_weichai("SVGID", "RJ1", 5)
    assert out and out.startswith(b"\x89PNG")
    # token gagal → None (aman)
    monkeypatch.setattr(wc, "_ensure_token", lambda r: "")
    assert ai_export.exploded_png_weichai("SVGID", "RJ1", 5) is None


def test_generic_excel_exploded_weichai_dispatch(monkeypatch):
    seen = {}
    monkeypatch.setattr(ai_export, "exploded_png_weichai",
                        lambda svg, rangka, ball: seen.update(svg=svg, rangka=rangka, ball=ball) or b"PNGW")
    eid, _ = ai_export.stash_builder(
        "Exploded", {"kind": "exploded", "source": "weichai", "svg": "S1", "balon": 5, "rangka": "RJ1"}, ext="png")
    data, _ = ai_export.generic_excel(eid)
    assert data == b"PNGW" and seen == {"svg": "S1", "rangka": "RJ1", "ball": 5}


# ── tool _t_gambar_exploded_mesin ────────────────────────────────────────────
def test_tool_butuh_rangka_dan_pn():
    assert "error" in ai._t_gambar_exploded_mesin({"pn": "X"}, U)      # tanpa rangka
    assert "error" in ai._t_gambar_exploded_mesin({"rangka": "R"}, U)  # tanpa pn


def test_tool_sukses_dan_metadata(monkeypatch):
    monkeypatch.setattr(ai.epc_weichai, "exploded_figures", lambda r, p, k: {
        "found": True, "frame_number": "FR1", "pn": p,
        "figures": [{"svg": "SVG1", "balon": 5, "nama": "Engine Block Group",
                     "kategori": "WP12", "jumlah_item": 15}]})
    r = ai._t_gambar_exploded_mesin({"rangka": "RJ1", "pn": "612630010055"}, U)
    assert r["found"] and r["gambar"] and r["gambar"][0]["balon"] == 5
    assert r["gambar"][0]["image_id"]


def test_tool_non_weichai(monkeypatch):
    monkeypatch.setattr(ai.epc_weichai, "exploded_figures", lambda r, p, k: {
        "found": False, "_err": "no_link"})
    r = ai._t_gambar_exploded_mesin({"rangka": "X", "pn": "PN"}, U)
    assert not r["found"] and "Weichai" in r["error"]


def test_tool_terdaftar_dan_allowlist():
    assert "gambar_exploded_mesin" in ai._DISPATCH
    assert "gambar_exploded_mesin" in ai._allowed_tool_names(U)
