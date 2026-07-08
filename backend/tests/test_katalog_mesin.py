"""Katalog bergambar MESIN Weichai — walk (figure=group, balon=orderNo),
pemetaan kategori, dePreview fetch, dan flow tool. Jaringan di-mock (tanpa EPC).
"""
import pytest

from app.services import ai_assistant as ai
from app.services import ai_export
from app.services import epc_weichai as wc

U = {"username": "mas", "role": "admin"}


# ── pemetaan kategori istilah → substring nama group ─────────────────────────
def test_mesin_kat_subs():
    assert "block" in wc._mesin_kat_subs("blok")
    assert "piston" in wc._mesin_kat_subs("seher")
    assert "fuel" in wc._mesin_kat_subs("bahan bakar")
    assert "injector" in wc._mesin_kat_subs("injektor")
    assert "compressor" in wc._mesin_kat_subs("kompresor angin")
    # tak dikenal → fallback kata term itu sendiri (semua kata ≥3 huruf)
    assert wc._mesin_kat_subs("oddterm") == ["oddterm"]


# ── fetch_svg: hanya balas bytes bila benar-benar SVG ────────────────────────
class _Resp:
    def __init__(self, content, ok=True):
        self.content, self.ok = content, ok


def test_fetch_svg_valid(monkeypatch):
    monkeypatch.setattr(wc.requests, "get", lambda *a, **k: _Resp(b"<?xml ?><svg>...</svg>"))
    assert wc.fetch_svg("123", "tok").startswith(b"<?xml")


def test_fetch_svg_json_gagal(monkeypatch):
    monkeypatch.setattr(wc.requests, "get", lambda *a, **k: _Resp(b'{"code":401,"msg":"x"}'))
    assert wc.fetch_svg("123", "tok") is None
    assert wc.fetch_svg("", "tok") is None       # id kosong
    assert wc.fetch_svg("123", "") is None        # token kosong


# ── catalog_walk: struktur figure = group, item ber-balon ────────────────────
_TREE = {"data": [{"partName": "WP12 Engine", "children": [
    {"id": "G1", "partName": "Engine Block Group", "svgFileId": "SVG1", "orderNo": 1},
    {"id": "G2", "partName": "Fuel Injector Group", "svgFileId": "SVG2", "orderNo": 2},
    {"id": "G3", "partName": "Water Pump Group", "svgFileId": "SVG3", "orderNo": 3},
]}]}
_LISTS = {
    "G1": {"data": [
        # orderNo = nomor balon di gambar; lineNumber = kunci urut sparse (diabaikan).
        {"partNumber": "612630010055", "partName": "Cylinder Liner", "lineNumber": 110,
         "orderNo": 5, "iba": {"IsRepidWear": "Y"}},
        {"partNumber": "1013955889", "partName": "Engine Block Assembly", "lineNumber": 10,
         "orderNo": 10, "link": {"showRule": "绘制示意图"}, "iba": {}},
    ]},
    "G2": {"data": [{"partNumber": "1000076563", "partName": "Injector", "lineNumber": 20,
                     "orderNo": 1, "iba": {}}]},
    "G3": {"data": [{"partNumber": "1234567890", "partName": "Water Pump", "lineNumber": 30,
                     "orderNo": 1, "iba": {}}]},
}


@pytest.fixture
def mock_wc(monkeypatch):
    monkeypatch.setattr(wc, "_bridge", lambda f: {
        "found": True, "token": "TOK", "dhhNumber": "DHH1", "dhhDate": "", "serial": "WP12S400E201"})
    monkeypatch.setattr(wc.epc_bom, "_frame", lambda r: (r or "").strip().upper())

    def fake_get(url, params, token):
        if url == wc._TREE_URL:
            return _TREE
        if url == wc._LIST_URL:
            return _LISTS.get(params.get("dhhId"), {"data": []})
        return {"_err": "api"}
    monkeypatch.setattr(wc, "_get", fake_get)
    wc._katalog_cache.clear()


def test_walk_kategori_blok(mock_wc):
    d = wc.catalog_walk("RJ345233", "blok")
    assert d["found"] and d["jumlah_figure"] == 1
    fig = d["figures"][0]
    assert fig["nama"] == "Engine Block Group"
    assert fig["svg"] == "SVG1"                     # svgFileId GROUP
    balon = {it["balon"]: it["pn"] for it in fig["items"]}
    # BALON = orderNo (nomor di gambar), BUKAN lineNumber: Cylinder Liner
    # lineNumber 110 tapi orderNo 5; Engine Block lineNumber 10 tapi orderNo 10.
    assert balon[5] == "612630010055"               # orderNo 5  (kasus nyata user)
    assert balon[10] == "1013955889"                # orderNo 10
    assert any(it.get("aus") for it in fig["items"])  # IsRepidWear=Y → aus
    assert d["_token"] == "TOK"


def test_walk_balon_dari_orderNo_bukan_lineNumber(monkeypatch):
    """Regresi bug nyata (2026-07-08): balon = orderNo (nomor tergambar di SVG),
    BUKAN lineNumber (kunci urut sparse). Kasus user: Cylinder Liner lineNumber
    110 → balon 5 (orderNo). Fallback ke urutan kemunculan bila orderNo tak valid."""
    monkeypatch.setattr(wc, "_bridge", lambda f: {
        "found": True, "token": "TOK", "dhhNumber": "DHH1", "dhhDate": "", "serial": "WP12"})
    monkeypatch.setattr(wc.epc_bom, "_frame", lambda r: (r or "").strip().upper())
    tree = {"data": [{"partName": "E", "children": [
        {"id": "G", "partName": "Flywheel Housing Group", "svgFileId": "S", "orderNo": 1}]}]}
    # lineNumber besar & acak; orderNo = balon sebenarnya; satu part orderNo hilang → fallback.
    lst = {"data": [
        {"partNumber": "A", "partName": "A", "lineNumber": 110, "orderNo": 5, "iba": {}},
        {"partNumber": "B", "partName": "B", "lineNumber": 10, "orderNo": 10, "iba": {}},
        {"partNumber": "C", "partName": "C", "lineNumber": 999, "iba": {}},  # orderNo tak ada
    ]}

    def fake_get(url, params, token):
        return tree if url == wc._TREE_URL else (lst if url == wc._LIST_URL else {"_err": "x"})
    monkeypatch.setattr(wc, "_get", fake_get)
    wc._katalog_cache.clear()

    d = wc.catalog_walk("RJ345233", "lengkap")
    bymap = {it["pn"]: it["balon"] for it in d["figures"][0]["items"]}
    assert bymap["A"] == 5                           # orderNo 5 (bukan lineNumber 110)
    assert bymap["B"] == 10                          # orderNo 10 (bukan lineNumber 10)
    assert bymap["C"] == 3                           # tanpa orderNo → urutan kemunculan (ke-3)


def test_walk_lengkap_semua_group(mock_wc):
    d = wc.catalog_walk("RJ345233", "lengkap")
    assert d["found"] and d["jumlah_figure"] == 3 and d["lengkap"] is True


def test_walk_kategori_tak_cocok(mock_wc):
    d = wc.catalog_walk("RJ345233", "gardan belakang")
    assert not d["found"] and d["_err"] == "no_category"
    assert "Engine Block Group" in (d.get("tersedia") or [])


def test_walk_non_weichai(monkeypatch):
    monkeypatch.setattr(wc.epc_bom, "_frame", lambda r: "X")
    monkeypatch.setattr(wc, "_bridge", lambda f: {
        "found": False, "reason": "no_link", "message": "bukan Weichai"})
    wc._katalog_cache.clear()
    d = wc.catalog_walk("X", "blok")
    assert not d["found"] and d["_err"] == "no_link"


# ── tool _t_katalog_mesin: flow bertahap ─────────────────────────────────────
def test_tool_butuh_rangka():
    assert "error" in ai._t_katalog_mesin({}, U)


def test_tool_tanya_kategori_lalu_format_lalu_jadi(monkeypatch):
    monkeypatch.setattr(ai.epc_weichai, "catalog_walk", lambda r, k: {
        "found": True, "frame_number": "FRAME1", "engine_model": "WP12",
        "lengkap": False, "kategori_cocok": ["Engine Block Group"],
        "jumlah_figure": 1, "jumlah_part": 15, "figures": [{}], "incomplete": False})
    # tanpa kategori → tawarkan pilihan
    r0 = ai._t_katalog_mesin({"rangka": "RJ345233"}, U)
    assert r0["found"] is False and r0["pilihan_kategori"]
    # kategori ada, format belum → tanya format
    r1 = ai._t_katalog_mesin({"rangka": "RJ345233", "kategori": "blok"}, U)
    assert r1["found"] is False and r1["pilihan_format"]
    # lengkap → export siap + kartu unduh
    r2 = ai._t_katalog_mesin({"rangka": "RJ345233", "kategori": "blok", "format": "excel"}, U)
    assert r2["found"] and r2["export_id"] and r2["jumlah_figure"] == 1


def test_tool_non_weichai(monkeypatch):
    monkeypatch.setattr(ai.epc_weichai, "catalog_walk", lambda r, k: {
        "found": False, "_err": "no_link", "reason": "no_link", "message": "bukan Weichai"})
    r = ai._t_katalog_mesin({"rangka": "X", "kategori": "blok", "format": "excel"}, U)
    assert not r["found"] and "Weichai" in r["error"]


def test_tool_terdaftar_di_dispatch_dan_capture():
    assert "katalog_mesin" in ai._DISPATCH
