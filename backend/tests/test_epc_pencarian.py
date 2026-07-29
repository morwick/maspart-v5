"""EPC: keyword Mandarin, cache basi, dan berhenti menjawab KOSONG.

Tiga cacat dari log produksi (uraikan_assembly nihil 31%, gambar_exploded 30%):

1. Ambang keyword `len >= 3` — heuristik ASCII yang diterapkan buta ke CJK.
   Dalam bahasa Mandarin DUA hanzi sudah satu kata utuh, jadi seluruh keyword
   Mandarin di kamus dibuang sebelum dipakai: 衬套 (bushing), 支架 (bracket),
   座椅 (jok), 板簧 (pegas daun) — persis istilah pada kasus-kasus yang gagal.
2. Indeks item yang lewat 7 hari dibalas None, sehingga unit yang cache-nya
   SUDAH ADA tetap membayar cold-build 56-84 dtk di dalam giliran chat.
3. Assembly dicocokkan ke nama NODE POHON; permintaan yang tak pernah menjadi
   nama node dijawab kosong, padahal indeks item punya `dari_assembly`.

Nol jaringan, nol panggilan model.
"""
from __future__ import annotations

import json
import time

import pytest

from app.services import epc_bom

# conftest mem-patch items_index_ready → False untuk SEMUA test (pengaman agar
# test biasa tak memicu build indeks EPC nyata). Modul ini justru mengujinya,
# jadi fungsi aslinya ditangkap saat impor — sebelum fixture autouse berjalan.
_READY_ASLI = epc_bom.items_index_ready


# ── 1. ambang keyword sadar-CJK ────────────────────────────────────────────

def test_keyword_mandarin_dua_hanzi_diterima():
    for k in ("衬套", "支架", "座椅", "板簧", "钢板", "转向"):
        assert epc_bom.kw_layak(k) is True, k


def test_keyword_latin_pendek_tetap_ditolak():
    """Ambang 3 untuk Latin dipertahankan — 'ab'/'x' membanjiri hasil."""
    for k in ("ab", "x", "", "  "):
        assert epc_bom.kw_layak(k) is False, repr(k)


def test_keyword_latin_normal_diterima():
    for k in ("bushing", "bracket", "seat", "abc"):
        assert epc_bom.kw_layak(k) is True, k


def test_kw_layak_dipakai_jalur_pencarian(monkeypatch):
    """Bukan cuma helper-nya benar — jalur nyata harus memakainya."""
    ditangkap = {}

    def _root(frame):
        return {"orderNo": "ORD1"}

    def _get(url, params):
        ditangkap.setdefault("kw", []).append(params.get("k"))
        return {"data": []}

    monkeypatch.setattr(epc_bom, "_atlas_root_cached", _root)
    monkeypatch.setattr(epc_bom, "_get_auto", _get)
    epc_bom.search_in_unit("RT108966", ["衬套", "ab", "bushing"])
    assert "衬套" in ditangkap["kw"]      # dulu dibuang diam-diam
    assert "ab" not in ditangkap["kw"]
    assert "bushing" in ditangkap["kw"]


# ── 2. cache indeks item: basi tetap disajikan ─────────────────────────────

def _tulis_cache(tmp_path, monkeypatch, frame, umur_detik, incomplete=False, rows=None):
    p = tmp_path / f"{frame}.json"
    p.write_text(json.dumps({
        "ts": time.time() - umur_detik,
        "incomplete": incomplete,
        "rows": rows if rows is not None else [{"pn": "X1", "nama": "Item"}],
    }), encoding="utf-8")
    monkeypatch.setattr(epc_bom, "_items_disk_path", lambda f: tmp_path / f"{f}.json")
    return p


def test_cache_lengkap_basi_tetap_dipakai(tmp_path, monkeypatch):
    """Inti perbaikan: 7 hari lewat ≠ dibuang. Katalog per-VIN praktis statis;
    menunggu rebuild 56-84 dtk di jalur jawab jauh lebih merugikan."""
    _tulis_cache(tmp_path, monkeypatch, "RT108966", epc_bom._ITEMS_DISK_TTL + 3600)
    d = epc_bom._items_disk_load("RT108966")
    assert d is not None
    assert d["stale"] is True
    assert d["rows"]


def test_cache_lengkap_segar_tidak_stale(tmp_path, monkeypatch):
    _tulis_cache(tmp_path, monkeypatch, "RT108966", 60)
    d = epc_bom._items_disk_load("RT108966")
    assert d["stale"] is False


def test_cache_parsial_tetap_kedaluwarsa_keras(tmp_path, monkeypatch):
    """Build PARSIAL isinya bolong — 'part tidak ada' tak boleh jadi vonis
    permanen yang salah, jadi TTL 1 jam-nya tetap keras."""
    _tulis_cache(tmp_path, monkeypatch, "SJ346500",
                 epc_bom._ITEMS_PARTIAL_TTL + 60, incomplete=True)
    assert epc_bom._items_disk_load("SJ346500") is None


def test_cache_kosong_diabaikan(tmp_path, monkeypatch):
    _tulis_cache(tmp_path, monkeypatch, "AA111111", 10, rows=[])
    assert epc_bom._items_disk_load("AA111111") is None


def test_indeks_basi_dianggap_siap(tmp_path, monkeypatch):
    """items_index_ready menentukan apakah jalur TELITI dipakai. Cache basi tapi
    LENGKAP harus dianggap siap — isinya utuh, penyegaran jalan di latar."""
    _tulis_cache(tmp_path, monkeypatch, "RT108966", epc_bom._ITEMS_DISK_TTL + 100)
    epc_bom._items_all_cache.pop("RT108966", None)
    assert _READY_ASLI("RT108966") is True


def test_stale_memicu_refresh_latar(tmp_path, monkeypatch):
    _tulis_cache(tmp_path, monkeypatch, "RT108966", epc_bom._ITEMS_DISK_TTL + 100)
    epc_bom._items_all_cache.pop("RT108966", None)
    dipicu = []
    monkeypatch.setattr(epc_bom, "_refresh_items_latar", lambda f: dipicu.append(f))
    out = epc_bom._all_items("RT108966")
    assert out["found"] is True and out["rows"]
    assert dipicu == ["RT108966"]


# ── 3. uraikan assembly dari indeks item ───────────────────────────────────

_ROWS = [
    {"pn": "AZ9925520280", "nama": "Front spring U-bolt", "nama_cn": "钢板弹簧U型螺栓",
     "qty": 4, "dari_assembly": {"pn": "AZ9925520001", "nama": "Front leaf spring suspension"}},
    {"pn": "AZ000052000343", "nama": "Spring pin", "nama_cn": "弹簧销",
     "qty": 2, "dari_assembly": {"pn": "AZ9925520001", "nama": "Front leaf spring suspension"}},
    {"pn": "WG9725520170", "nama": "Plate spring pressing block", "nama_cn": "压块",
     "qty": 2, "dari_assembly": {"pn": "AZ9925520001", "nama": "Front leaf spring suspension"}},
    {"pn": "WG1642430361", "nama": "Cab seat cushion", "nama_cn": "座椅垫",
     "qty": 1, "dari_assembly": {"pn": "WG1642430300", "nama": "Seat assembly"}},
]


@pytest.fixture
def _items(monkeypatch):
    monkeypatch.setattr(epc_bom, "_all_items",
                        lambda r, _paksa=False: {"found": True, "frame_number": "SJ346500",
                                                 "rows": _ROWS, "incomplete": False})


def test_uraikan_dari_indeks_item(_items):
    out = epc_bom.assembly_components_from_items("SJ346500", ["front spring"])
    assert out["found"] is True
    assert out["assembly"]["nama"] == "Front leaf spring suspension"
    assert len(out["components"]) == 3
    assert out["sumber"] == "indeks_item"


def test_istilah_tak_dirinci_dijawab_JUJUR_bukan_kosong(_items):
    """Kasus nyata 'bushing untuk front spring': EPC memang tidak memodelkan
    bushing sebagai elemen terpisah. Yang benar bukan mengarang PN, tapi juga
    BUKAN membalas kosong — kembalikan isi assembly yang nyata + katakan
    terus terang istilahnya tak dirinci."""
    out = epc_bom.assembly_components_from_items("SJ346500", ["front spring", "bushing"])
    assert out["found"] is True
    assert out["components"], "jangan balas kosong — isi assembly-nya nyata"
    assert "bushing" in out["istilah_tak_dirinci"]
    assert "TIDAK merinci" in out["catatan_kejujuran"]
    assert "JANGAN mengarang" in out["catatan_kejujuran"]


def test_istilah_yang_ADA_tak_ditandai(_items):
    out = epc_bom.assembly_components_from_items("SJ346500", ["front spring", "pin"])
    assert "istilah_tak_dirinci" not in out


def test_cocok_lewat_pn_assembly(_items):
    out = epc_bom.assembly_components_from_items("SJ346500", [], pn="WG1642430300")
    assert out["found"] is True
    assert out["assembly"]["nama"] == "Seat assembly"


def test_keyword_mandarin_ikut_dipakai(_items):
    """座椅 (jok) 2 hanzi — dulu dibuang ambang len>=3."""
    out = epc_bom.assembly_components_from_items("SJ346500", ["座椅"])
    assert out["found"] is True


def test_nihil_bila_assembly_memang_tak_ada(_items):
    out = epc_bom.assembly_components_from_items("SJ346500", ["turbocharger"])
    assert out["found"] is False
