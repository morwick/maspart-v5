"""katalog_kategori: kartu unduh dikirim TANPA menunggu walk EPC 70 dtk
(audit ai_chat_log 2026-08-28: p50 71 dtk sinkron di jalur chat), dan walk
yang sedang berjalan tidak digandakan oleh builder unduhan."""
import threading
import time

import pytest

from app.services import ai_assistant as ai, ai_export, epc_bom

ADMIN = {"username": "admin", "role": "admin"}
ARGS = {"rangka": "LZZ5EXSF9RJ380449", "kategori": "kabin", "format": "excel"}


@pytest.fixture
def _stash(monkeypatch):
    box = {}
    monkeypatch.setattr(ai_export, "stash_builder",
                        lambda judul, builder, ext="xlsx": box.update(builder) or ("EID", "f." + ext))
    return box


def test_walk_lambat_kartu_tetap_dikirim(monkeypatch, _stash):
    monkeypatch.setattr(ai, "_KATALOG_TUNGGU_DTK", 0.05)
    selesai = threading.Event()

    def lambat(r, k):
        selesai.wait(2)
        return {"found": True, "jumlah_figure": 9, "jumlah_part": 99, "figures": [{}]}

    monkeypatch.setattr(ai.epc_bom, "catalog_walk", lambat)
    t0 = time.monotonic()
    r = ai._t_katalog_kategori(ARGS, ADMIN)
    assert time.monotonic() - t0 < 1.5
    assert r["found"] and r["sedang_disusun"] and r["export_id"] == "EID"
    assert "jumlah_figure" not in r and "MASIH DISUSUN" in r["catatan"]
    assert _stash["rangka"] == ARGS["rangka"] and _stash["fmt"] == "excel"
    selesai.set()


def test_walk_cepat_bentuk_lama(monkeypatch, _stash):
    monkeypatch.setattr(ai.epc_bom, "catalog_walk", lambda r, k: {
        "found": True, "frame_number": "RJ380449", "kategori_kode": "01", "lengkap": False,
        "jumlah_figure": 2, "jumlah_part": 5, "kategori_cocok": ["Kabin"], "figures": [{}],
        "incomplete": False})
    r = ai._t_katalog_kategori(ARGS, ADMIN)
    assert r["found"] and "sedang_disusun" not in r
    assert r["jumlah_figure"] == 2 and r["jumlah_baris"] == 5


def test_walk_gagal_tetap_jujur(monkeypatch, _stash):
    monkeypatch.setattr(ai.epc_bom, "catalog_walk", lambda r, k: {"found": False, "_err": "network"})
    r = ai._t_katalog_kategori(ARGS, ADMIN)
    assert r["found"] is False and "jaringan" in r["error"]


def test_catalog_walk_inflight_tidak_digandakan(monkeypatch):
    epc_bom._katalog_cache._d.clear()
    epc_bom._katalog_inflight.clear()
    hitung = {"n": 0}
    mulai = threading.Event()

    def impl(rangka, kategori, ckey):
        hitung["n"] += 1
        mulai.wait(2)
        val = {"found": True, "figures": [], "incomplete": False}
        with epc_bom._katalog_lock:
            epc_bom._katalog_cache[ckey] = {"at": time.monotonic(), "val": val}
        return val

    monkeypatch.setattr(epc_bom, "_catalog_walk_impl", impl)
    hasil = {}
    t1 = threading.Thread(target=lambda: hasil.update(a=epc_bom.catalog_walk("LZZ5EXSF9RJ380449", "kabin")))
    t1.start()
    time.sleep(0.1)
    t2 = threading.Thread(target=lambda: hasil.update(b=epc_bom.catalog_walk("RJ380449", "Kabin")))
    t2.start()
    time.sleep(0.1)
    mulai.set()
    t1.join(3); t2.join(3)
    assert hitung["n"] == 1                         # pemanggil kedua MENUNGGU, bukan walk ulang
    assert hasil["a"]["found"] and hasil["b"] is hasil["a"]
    assert not epc_bom._katalog_inflight
    # cache hangat → tidak walk lagi
    assert epc_bom.catalog_walk("RJ380449", "kabin")["found"] and hitung["n"] == 1
