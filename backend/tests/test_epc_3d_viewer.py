"""Viewer 3D (.pvz) TANPA nomor rangka — `epc_bom.pvz_untuk_pn` + endpoint
`/api/parts/epc-3d` & proxy `/api/parts/epc-file`.

Kembar dari exploded-figure, tapi mengumpulkan field `d3s` (file 3D PTC Creo
View) alih-alih `d2s` (SVG 2D). Yang dijaga:
  • figure yang dipilih adalah yang MEMUAT PN itu, balonnya ketemu, dan punya .pvz.
  • figure ber-.pvz tapi PN tak terdeteksi dipakai sebagai CADANGAN, bukan dibuang.
  • figure TANPA .pvz dilewati (hanya 2D → tak bisa dirender 3D).
  • proxy file: nama DIBATASI pola aman .pvz/.svg (anti-SSRF), byte diteruskan
    apa adanya TANPA simpan disk (Opsi A), header no-store.
Tanpa jaringan: `_get_auto` & `fetch_file` di-stub.
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.routers import parts as parts_router
from app.services import epc_bom

USER = {"username": "budi", "role": "pembeli"}
PN = "WG9000361402"

# Tiga figure: TANPA-PN (cadangan, ada .pvz), HANYA-2D (dilewati, tak ada .pvz),
# ADA-PN (menang: PN terdeteksi DAN ada .pvz).
_REVERSE = {"data": [
    {"partCode": "FIG-TANPA-PN", "rootId": 1, "partId": 11, "partListId": 111,
     "partName": "Figure lain", "model": "Model A"},
    {"partCode": "FIG-HANYA-2D", "rootId": 3, "partId": 33, "partListId": 333,
     "partName": "Figure 2D saja", "model": "Model C"},
    {"partCode": "FIG-ADA-PN", "rootId": 2, "partId": 22, "partListId": 222,
     "partName": "Auxiliary gas energy storage device", "model": "Model B"},
]}
_ITEMS = {
    "FIG-TANPA-PN": {"data": {"d3s": ["LAIN_A.1.pvz"], "d2s": ["LAIN_A.1.svg"],
        "items": [{"code": "WG111", "name": "Bracket", "ballNum": 1}]}},
    "FIG-HANYA-2D": {"data": {"d3s": [], "d2s": ["HANYA_2D.svg"],
        "items": [{"code": PN, "name": "Drain valve", "ballNum": 5}]}},
    "FIG-ADA-PN": {"data": {"d3s": ["I00050726_C.3.pvz"], "d2s": ["I00050726_C.3.svg"],
        "items": [
            {"code": "WG9100360108", "name": "Air reservoir bracket", "ballNum": 1},
            {"code": PN + "/1", "name": "Drain valve", "ballNum": 9}]}},
}


@pytest.fixture
def epc_stub(monkeypatch):
    panggilan = []

    def fake_get_auto(url, params, **kw):
        panggilan.append((url, dict(params)))
        if url == epc_bom._REVERSE_URL:
            return _REVERSE
        if url == epc_bom._ATLAS_ITEM_URL:
            return _ITEMS.get(params.get("partCode"), {"_err": "api"})
        return {"_err": "api"}

    monkeypatch.setattr(epc_bom, "_get_auto", fake_get_auto)
    return panggilan


# ── pvz_untuk_pn ─────────────────────────────────────────────────────────────

def test_pvz_pilih_figure_yang_memuat_pn_dan_punya_3d(epc_stub):
    d = epc_bom.pvz_untuk_pn(PN)
    assert d["found"] is True
    assert d["d3s"] == ["I00050726_C.3.pvz"]
    assert d["balon"] == 9, "balon PN yang dicari WAJIB ketemu"
    assert d["figure_pn"] == "FIG-ADA-PN"
    assert d["sumber_model"] == "Model B"
    # jalur global: tak boleh ada parameter rangka/cjh
    for _url, params in epc_stub:
        assert "cjh" not in params


def test_pvz_lewati_figure_tanpa_3d(monkeypatch):
    """Figure yang hanya punya d2s (2D) tak boleh dipakai — tak bisa dirender 3D."""
    def fake(url, params, **kw):
        if url == epc_bom._REVERSE_URL:
            return {"data": [_REVERSE["data"][1]]}  # FIG-HANYA-2D saja
        return _ITEMS["FIG-HANYA-2D"]
    monkeypatch.setattr(epc_bom, "_get_auto", fake)
    d = epc_bom.pvz_untuk_pn(PN)
    assert d["found"] is False


def test_pvz_cadangan_bila_pn_tak_terdeteksi(monkeypatch):
    def fake(url, params, **kw):
        if url == epc_bom._REVERSE_URL:
            return {"data": [_REVERSE["data"][0]]}  # FIG-TANPA-PN saja
        return _ITEMS["FIG-TANPA-PN"]
    monkeypatch.setattr(epc_bom, "_get_auto", fake)
    d = epc_bom.pvz_untuk_pn(PN)
    assert d["found"] is True and d["balon"] is None
    assert d["d3s"] == ["LAIN_A.1.pvz"]


def test_pvz_pn_kosong():
    assert epc_bom.pvz_untuk_pn("")["found"] is False


def test_pvz_tanpa_argumen_rangka():
    import inspect
    sig = inspect.signature(epc_bom.pvz_untuk_pn)
    assert "rangka" not in sig.parameters and "vin" not in sig.parameters


# ── Endpoint /api/parts/epc-3d ───────────────────────────────────────────────

def test_endpoint_3d_kembalikan_d3s(epc_stub):
    out = parts_router.epc_3d_untuk_pn(pn=PN, _user=USER)
    assert out["found"] is True and out["d3s"] == ["I00050726_C.3.pvz"]
    assert out["balon"] == 9


def test_endpoint_3d_pn_kosong():
    with pytest.raises(HTTPException):
        parts_router.epc_3d_untuk_pn(pn="   ", _user=USER)


# ── Proxy /api/parts/epc-file ────────────────────────────────────────────────

def test_proxy_teruskan_byte_pvz(monkeypatch):
    monkeypatch.setattr(epc_bom, "fetch_file", lambda n: b"PK\x03\x04pvz")
    resp = parts_router.epc_file_proxy(name="I00050726_C.3.pvz", _user=USER)
    assert resp.body == b"PK\x03\x04pvz"
    assert resp.media_type == "application/octet-stream"
    assert resp.headers.get("cache-control") == "no-store"


def test_proxy_svg_media_type(monkeypatch):
    monkeypatch.setattr(epc_bom, "fetch_file", lambda n: b"<svg/>")
    resp = parts_router.epc_file_proxy(name="A.1.svg", _user=USER)
    assert resp.media_type == "image/svg+xml"


@pytest.mark.parametrize("nama", [
    "../../etc/passwd", "I00050726_C.3.exe", "file.pvz.php",
    "foo/bar.pvz", "no_ext", "a b.pvz", "<script>.svg",
])
def test_proxy_tolak_nama_tak_aman(nama):
    with pytest.raises(HTTPException) as e:
        parts_router.epc_file_proxy(name=nama, _user=USER)
    assert e.value.status_code == 400


def test_proxy_gagal_ambil_502(monkeypatch):
    monkeypatch.setattr(epc_bom, "fetch_file", lambda n: None)
    with pytest.raises(HTTPException) as e:
        parts_router.epc_file_proxy(name="valid.pvz", _user=USER)
    assert e.value.status_code == 502


def test_endpoints_terdaftar_di_router():
    # Cek pada router-nya langsung (deterministik) — bukan app.routes, yang di
    # laptop bisa kosong bila import app.main gagal sebagian (lihat catatan
    # "UKUR DI CONTAINER, JANGAN DI LAPTOP").
    paths = [getattr(r, "path", "") for r in parts_router.router.routes]
    assert "/api/parts/epc-3d" in paths
    assert "/api/parts/epc-file" in paths
