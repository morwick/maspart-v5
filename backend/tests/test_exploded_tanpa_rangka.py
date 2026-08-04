"""Exploded view TANPA nomor rangka — `epc_bom.figure_global` + /api/parts/exploded-figure.

Terverifikasi live 2026-07-30: jalur global EPC (home/reverse/part?t=global +
part/tree/item?type=model) memang tak butuh rangka. Untuk EZ923136000037 hasilnya
IDENTIK dengan jalur per-VIN (figure EC9231360000148 / I00301138_C.3.svg, balon 15).

Yang dijaga di sini:
  • figure yang dipilih adalah yang MEMUAT PN itu, dan balonnya ketemu. Ini beda
    tujuan dari `assembly_components_global` yang justru MEMBUANG PN yang dicari
    dari daftar anak (jawabannya "apa isi assembly ini") — dulu itu membuat
    balon selalu None.
  • suffix varian PN ('…402/1' vs '…402') dianggap SAMA saat mencocokkan balon.
  • figure ber-SVG tapi PN tak terdeteksi dipakai sebagai CADANGAN, bukan dibuang.
  • endpoint: cache per PN, kegagalan TIDAK di-cache, dan `catatan` lintas-model
    selalu ikut supaya UI tak menyajikannya seolah gambar unit tertentu.
Tanpa jaringan: `_get_auto` di-stub.
"""
from __future__ import annotations

import pytest

from app.routers import parts as parts_router
from app.services import epc_bom

USER = {"username": "budi", "role": "pembeli"}
PN = "WG9000361402"

# Dua figure: yang pertama TAK memuat PN (hanya cadangan), yang kedua memuat.
_REVERSE = {"data": [
    {"partCode": "FIG-TANPA-PN", "rootId": 1, "partId": 11, "partListId": 111,
     "partName": "Figure lain", "model": "Model A"},
    {"partCode": "FIG-ADA-PN", "rootId": 2, "partId": 22, "partListId": 222,
     "partName": "Auxiliary gas energy storage device", "model": "Model B"},
]}
_ITEMS = {
    "FIG-TANPA-PN": {"data": {"d2s": ["LAIN_A.1.svg"], "items": [
        {"code": "WG111", "name": "Bracket", "ballNum": 1}]}},
    "FIG-ADA-PN": {"data": {"d2s": ["I00050726_C.3.svg"], "items": [
        {"code": "WG9100360108", "name": "Air reservoir bracket", "ballNum": 1},
        # suffix varian: harus tetap dikenali sebagai PN yang dicari
        {"code": PN + "/1", "name": "Drain valve", "ballNum": 9}]}},
}


@pytest.fixture
def epc_stub(monkeypatch):
    panggilan = []

    # **kw: _get_auto kini menerima timeout/retries (jalur GLOBAL pakai yang panjang
    # — respons t=global bisa belasan MB; lihat test_epc_timeout_global.py).
    def fake_get_auto(url, params, **kw):
        panggilan.append((url, dict(params)))
        if url == epc_bom._REVERSE_URL:
            return _REVERSE
        if url == epc_bom._ATLAS_ITEM_URL:
            return _ITEMS.get(params.get("partCode"), {"_err": "api"})
        return {"_err": "api"}

    monkeypatch.setattr(epc_bom, "_get_auto", fake_get_auto)
    return panggilan


# ── figure_global ───────────────────────────────────────────────────────────

def test_figure_global_pilih_figure_yang_memuat_pn(epc_stub):
    d = epc_bom.figure_global(PN)
    assert d["found"] is True
    assert d["svg"] == "I00050726_C.3.svg"
    assert d["balon"] == 9, "balon PN yang dicari WAJIB ketemu (dulu selalu None)"
    assert d["figure_pn"] == "FIG-ADA-PN"
    assert d["jumlah_model_pemakai"] == 2 and d["jumlah_figure"] == 2
    # jalur global: tak boleh ada parameter rangka/cjh sama sekali
    for _url, params in epc_stub:
        assert "cjh" not in params
        assert params.get("type") in (None, "model", "global") or params.get("t") == "global"


def test_figure_global_tanpa_rangka_sama_sekali(epc_stub):
    """Tak ada argumen rangka — kontrak intinya."""
    import inspect
    sig = inspect.signature(epc_bom.figure_global)
    assert "rangka" not in sig.parameters and "vin" not in sig.parameters


def test_figure_global_cadangan_bila_pn_tak_terdeteksi(monkeypatch):
    """Figure ber-SVG tapi PN tak terlihat di items → tetap dipakai (balon None)."""
    def fake(url, params, **kw):
        if url == epc_bom._REVERSE_URL:
            return {"data": [_REVERSE["data"][0]]}
        return _ITEMS["FIG-TANPA-PN"]
    monkeypatch.setattr(epc_bom, "_get_auto", fake)
    d = epc_bom.figure_global(PN)
    assert d["found"] is True and d["balon"] is None
    assert d["svg"] == "LAIN_A.1.svg"


def test_figure_global_pn_kosong():
    assert epc_bom.figure_global("")["found"] is False


def test_pn_sama_suffix_varian():
    assert epc_bom._pn_sama("WG9000361402/1", "WG9000361402")
    assert epc_bom._pn_sama("wg 9000361402", "WG9000361402")
    assert not epc_bom._pn_sama("WG9000361403", "WG9000361402")
    assert not epc_bom._pn_sama("", "WG9000361402")


# ── Endpoint /api/parts/exploded-figure ────────────────────────────────────

@pytest.fixture(autouse=True)
def bersihkan_cache():
    parts_router._exploded_cache.clear()
    yield
    parts_router._exploded_cache.clear()


def test_endpoint_kirim_png_dan_catatan(epc_stub, monkeypatch):
    monkeypatch.setattr(parts_router.ai_export, "exploded_png",
                        lambda svg, ball=None: b"\x89PNG-palsu")
    out = parts_router.exploded_figure_global(pn=PN, _user=USER)
    assert out["found"] is True and out["balon"] == 9
    assert out["png_base64"] and "PNG" in __import__("base64").b64decode(
        out["png_base64"]).decode("latin-1")
    # Peringatan lintas-model WAJIB ada: gambar ini bukan milik unit tertentu.
    assert "lintas-model" in out["catatan"]
    assert "nomor rangka" in out["catatan"]


def test_endpoint_cache_per_pn(epc_stub, monkeypatch):
    hit = []
    monkeypatch.setattr(parts_router.ai_export, "exploded_png",
                        lambda svg, ball=None: hit.append(1) or b"PNG")
    parts_router.exploded_figure_global(pn=PN, _user=USER)
    n = len(epc_stub)
    parts_router.exploded_figure_global(pn=PN.lower(), _user=USER)  # beda huruf
    assert len(epc_stub) == n, "panggilan kedua harus dari cache, tak menembak EPC"
    assert len(hit) == 1


def test_endpoint_gagal_tidak_dicache(monkeypatch):
    monkeypatch.setattr(epc_bom, "_get_auto", lambda u, p, **kw: {"data": []})
    out = parts_router.exploded_figure_global(pn=PN, _user=USER)
    assert out["found"] is False and "alasan" in out
    assert parts_router._exploded_cache == {}, "kegagalan EPC sesaat jangan di-cache"


def test_endpoint_render_gagal(epc_stub, monkeypatch):
    monkeypatch.setattr(parts_router.ai_export, "exploded_png",
                        lambda svg, ball=None: None)
    out = parts_router.exploded_figure_global(pn=PN, _user=USER)
    assert out["found"] is False and "gagal" in (out.get("alasan") or "").lower()


def test_endpoint_pn_kosong():
    from fastapi import HTTPException
    with pytest.raises(HTTPException):
        parts_router.exploded_figure_global(pn="///", _user=USER)


def test_endpoint_terdaftar_di_app():
    from app.main import app
    paths = [getattr(r, "path", "") for r in app.routes]
    assert "/api/parts/exploded-figure" in paths
    # tidak menabrak endpoint lama yang menyajikan PNG per export_id (butuh rangka)
    assert "/api/parts/exploded/{export_id}" in paths
