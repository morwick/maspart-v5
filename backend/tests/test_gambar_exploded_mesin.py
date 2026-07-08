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


def test_tool_sukses_info_teks(monkeypatch):
    # Kartu gambar DIMATIKAN → tool balas INFO TEKS (figure + daftar balon), tanpa 'gambar'.
    monkeypatch.setattr(ai.epc_weichai, "exploded_figures", lambda r, p, k: {
        "found": True, "frame_number": "FR1", "pn": p,
        "figures": [{"svg": "SVG1", "balon": 5, "nama": "Engine Block Group",
                     "kategori": "WP12", "jumlah_item": 2,
                     "items_ringkas": [{"balon": 5, "pn": "612630010055", "nama": "Cylinder Liner"}]}]})
    r = ai._t_gambar_exploded_mesin({"rangka": "RJ1", "pn": "612630010055"}, U)
    assert r["found"] and not r.get("gambar")                # tak ada kartu gambar
    assert r["daftar_balon_gambar"]                          # info balon (teks) tetap ada
    assert "Engine Block Group" in r["catatan"]


def test_tool_non_weichai(monkeypatch):
    monkeypatch.setattr(ai.epc_weichai, "exploded_figures", lambda r, p, k: {
        "found": False, "_err": "no_link"})
    r = ai._t_gambar_exploded_mesin({"rangka": "X", "pn": "PN"}, U)
    assert not r["found"] and "Weichai" in r["error"]


def test_tool_sorot_balon_tertentu(monkeypatch):
    # User minta balon 3 → figure ditemukan via PN assembly, HIGHLIGHT balon 3
    # (bukan balon PN=2), + laporkan part di balon 3.
    monkeypatch.setattr(ai.epc_weichai, "exploded_figures", lambda r, p, k: {
        "found": True, "frame_number": "FR1", "pn": p,
        "figures": [{"svg": "SVG1", "balon": 2, "nama": "Turbocharger Group",
                     "kategori": "WP12", "jumlah_item": 7,
                     "items_ringkas": [{"balon": 2, "pn": "1014276820", "nama": "Turbocharger"},
                                       {"balon": 3, "pn": "1002177442", "nama": "Exhaust Manifold Bolt"}]}]})
    r = ai._t_gambar_exploded_mesin({"rangka": "RJ1", "pn": "1014276820", "balon": 3}, U)
    assert r["found"]
    assert r["balon_disorot"] == 3
    assert not r.get("gambar")                              # tak ada kartu gambar
    assert r["part_di_balon"]["part_number"] == "1002177442"  # info balon 3 (teks)
    assert "Exhaust Manifold Bolt" in (r["part_di_balon"]["nama"] or "")


def test_tool_sorot_balon_tak_ada(monkeypatch):
    monkeypatch.setattr(ai.epc_weichai, "exploded_figures", lambda r, p, k: {
        "found": True, "pn": p,
        "figures": [{"svg": "S", "balon": 2, "nama": "Turbo", "kategori": "", "jumlah_item": 2,
                     "items_ringkas": [{"balon": 2, "pn": "X", "nama": "Turbo"}]}]})
    r = ai._t_gambar_exploded_mesin({"rangka": "RJ1", "pn": "X", "balon": 9}, U)
    assert r["found"] and r["balon_disorot"] == 9
    assert r["part_di_balon"] is None                        # balon 9 tak ada
    assert not r.get("gambar")                               # tak ada kartu gambar


def test_tool_terdaftar_dan_allowlist():
    assert "gambar_exploded_mesin" in ai._DISPATCH
    assert "gambar_exploded_mesin" in ai._allowed_tool_names(U)


# ── AUTO exploded DIMATIKAN (pemilik) — _auto_exploded_gambar selalu kosong ────
def test_sino_exploded_kat_mapping():
    assert ai._sino_exploded_kat(("FDJ", "FDJFJ"), None) == "mesin"
    assert ai._sino_exploded_kat(("LHQ",), None) == "kopling"
    assert ai._sino_exploded_kat(("BSX",), None) == "transmisi"
    assert ai._sino_exploded_kat(("CDQ", "QDQ"), "depan") == "gardan depan"
    assert ai._sino_exploded_kat(("CDQ", "QDQ"), "belakang") == "gardan belakang"
    assert ai._sino_exploded_kat(("CDQ", "QDQ"), None) == ""       # poros tanpa posisi → skip
    assert ai._sino_exploded_kat(("FDJ", "FDJFJ", "CDQ", "QDQ"), None) == ""  # filter multi → skip


def test_auto_exploded_gambar_dimatikan():
    # Kartu thumbnail dimatikan (pemilik) → helper SELALU kosong (tanpa walk EPC).
    assert ai._auto_exploded_gambar("RJ1", "PN1", "weichai", "blok") == ([], [], "")
    assert ai._auto_exploded_gambar("RJ1", "PN1", "sinotruk", "rem") == ([], [], "")


def test_uraikan_mesin_tanpa_kartu_gambar(monkeypatch):
    # uraikan_mesin tetap kasih daftar komponen (teks), TANPA kartu gambar.
    monkeypatch.setattr(ai.epc_weichai, "find_parts", lambda r, t: {
        "found": True, "engine": {"nama": "WP12", "model": "X", "order": "O"},
        "hasil": [{"pn": "1014276820", "nama": "Turbocharger", "group": "Turbo"}]})
    monkeypatch.setattr(ai.part_index, "search_exact_pns", lambda pns: [])
    r = ai._t_uraikan_mesin({"rangka": "SJ346500", "part": "turbo"}, U)
    assert r["found"] and r["komponen"]        # info komponen (teks) ada
    assert not r.get("gambar")                 # tak ada kartu gambar


def test_capture_meta_ambil_gambar_dari_uraikan_mesin():
    # _capture_meta harus menangkap 'gambar' dari uraikan_mesin & part_aus (bukan
    # hanya gambar_exploded*). Diuji via alur chat singkat lebih berat; di sini
    # cukup pastikan nama tool masuk daftar yang di-handle.
    import inspect
    src = inspect.getsource(ai.chat)
    assert '"uraikan_mesin", "part_aus_dari_rangka"' in src
