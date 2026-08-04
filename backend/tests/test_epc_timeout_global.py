"""Timeout panggilan reverse GLOBAL EPC (bug produksi 2026-08-04).

Gejala: "cek gambar teknis WG9000361402" dijawab "server EPC tidak merespons
(error jaringan)" padahal EPC sehat. Sebab: respons `t=global` PN itu 13,6 MB /
47.525 baris / 48,5 dtk — di atas timeout 30 dtk, jadi 3 percobaan habis dan
divonis '_err=network'. Perbaikan: timeout/retries bisa disetel per panggilan;
ketiga jalur GLOBAL (unit_dari_part, assembly_components_global, figure_global)
memakai _TIMEOUT_GLOBAL. Jalur per-VIN (t=car) TETAP 30 dtk — kecil & sering.
Zero-network: requests.get di-stub.
"""
import pytest

from app.services import epc_bom


@pytest.fixture
def rekam(monkeypatch):
    """Rekam (url, params, timeout) tiap panggilan; balas sukses kosong."""
    jejak = []

    class _R:
        def __init__(self, data):
            self._d = data

        def json(self):
            return {"success": True, "data": self._d}

    def fake_get(url, params=None, headers=None, timeout=None, verify=None):
        p = dict(params or {})
        jejak.append({"url": url, "params": p, "timeout": timeout})
        # match: PN harus dikenali, kalau tidak reverse_part berhenti sebelum
        # sempat memanggil _REVERSE_URL (yang justru sedang diuji).
        if url == epc_bom._MATCH_URL:
            return _R([{"code": p.get("k"), "name": "Drain valve"}])
        return _R([])

    monkeypatch.setattr(epc_bom.requests, "get", fake_get)
    monkeypatch.setattr(epc_bom, "_token", lambda: "TOKEN")
    return jejak


def test_figure_global_pakai_timeout_panjang(rekam):
    epc_bom.figure_global("WG9000361402")
    rev = [j for j in rekam if j["params"].get("t") == "global"]
    assert rev and rev[0]["timeout"] == epc_bom._TIMEOUT_GLOBAL
    assert epc_bom._TIMEOUT_GLOBAL >= 90, "48,5 dtk terukur — beri margin"


def test_assembly_components_global_pakai_timeout_panjang(rekam):
    epc_bom.assembly_components_global("WG9000361402")
    assert rekam[0]["timeout"] == epc_bom._TIMEOUT_GLOBAL


def test_reverse_part_pakai_timeout_panjang(rekam):
    epc_bom.reverse_part("WG9000361402")
    rev = [j for j in rekam if j["url"] == epc_bom._REVERSE_URL]
    assert rev and rev[0]["timeout"] == epc_bom._TIMEOUT_GLOBAL


def test_panggilan_biasa_tetap_30_detik(rekam):
    epc_bom._get(epc_bom._ATLAS_ITEM_URL, {"id": 1})
    assert rekam[0]["timeout"] == 30


def test_retry_jaringan_dihormati(monkeypatch):
    """retries=2 → dua percobaan lalu menyerah (bukan tiga)."""
    n = {"i": 0}

    def boom(*a, **kw):
        n["i"] += 1
        raise OSError("timeout")

    monkeypatch.setattr(epc_bom.requests, "get", boom)
    monkeypatch.setattr(epc_bom, "_token", lambda: "TOKEN")
    monkeypatch.setattr(epc_bom.time, "sleep", lambda s: None)
    r = epc_bom._get("u", {}, timeout=5, retries=2)
    assert r["_err"] == "network" and n["i"] == 2
    assert epc_bom._RETRIES_GLOBAL <= 2, "satu percobaan saja ±1 mnt"
