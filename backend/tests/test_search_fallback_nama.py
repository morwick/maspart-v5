"""Endpoint /parts/search: fallback ke pencarian NAMA + pembersihan miss basi.

Bukti dari daftar Pencarian Nihil PRODUKSI (207 entri, diukur 2026-08-09):
 • 'fuel filter' tercatat GAGAL 5× di kotak PN, padahal pencarian NAMA memberi
   571 hasil — user tak punya cara tahu ia salah kotak. (18 kejadian, 6,2%)
 • 13% entri (38 kejadian) ternyata SUDAH bisa ditemukan hari ini — miss basi
   yang menyesatkan saat dipakai memprioritaskan pekerjaan.

⚠️ Fallback nama dijalankan PALING AKHIR (sesudah exact → smart_pn → SIMS) supaya
pencarian PN tetap presisi; ia hanya menyelamatkan yang tadinya nihil total.
"""
import pytest

from app.routers import parts as parts_router
from app.services import part_index, search_log

USER = {"username": "budi", "role": "admin"}


@pytest.fixture(autouse=True)
def _kosongkan(monkeypatch):
    """Jalur SIMS & Accurate dimatikan — tes ini murni soal urutan fallback."""
    monkeypatch.setattr(parts_router, "_sims_fallback", lambda q: [])
    monkeypatch.setattr(parts_router, "_overlay_accurate", lambda r: r)
    monkeypatch.setattr(parts_router, "_scope_gudang", lambda r, u: r)
    monkeypatch.setattr(part_index, "smart_pn_search", lambda q: ([], ""))


def _baris(pn="X-1", nama="Fuel filter element"):
    return {"part_number": pn, "part_name": nama, "stok": "—", "harga": "—",
            "gudang": {}, "file": "unit", "path": "p", "quantity": "1",
            "sheet": "01", "excel_row": 2}


# ── fallback NAMA ───────────────────────────────────────────────────────────
def test_pn_nihil_jatuh_ke_pencarian_nama(monkeypatch):
    monkeypatch.setattr(part_index, "search_part_number", lambda q: [])
    monkeypatch.setattr(part_index, "search_part_name", lambda q: [_baris()])
    monkeypatch.setattr(search_log, "record_miss", lambda *a, **k: pytest.fail(
        "tak boleh dicatat sbg miss — hasilnya KETEMU lewat nama"))
    r = parts_router.search(q="fuel filter", page=1, page_size=20, user=USER)
    assert [x.part_number for x in r.results] == ["X-1"]


def test_pn_ketemu_tidak_memanggil_pencarian_nama(monkeypatch):
    """Presisi PN dijaga: fallback hanya jalan bila hasilnya benar-benar nihil."""
    monkeypatch.setattr(part_index, "search_part_number", lambda q: [_baris("WG-1", "Bolt")])
    monkeypatch.setattr(part_index, "is_exact_match_found", lambda q: True)
    monkeypatch.setattr(part_index, "search_part_name",
                        lambda q: pytest.fail("pencarian NAMA tak boleh dipanggil"))
    monkeypatch.setattr(search_log, "resolve_miss", lambda q: False)
    r = parts_router.search(q="WG-1", page=1, page_size=20, user=USER)
    assert [x.part_number for x in r.results] == ["WG-1"]


def test_nihil_total_tetap_dicatat_dan_diberi_saran(monkeypatch):
    dicatat = []
    monkeypatch.setattr(part_index, "search_part_number", lambda q: [])
    monkeypatch.setattr(part_index, "search_part_name", lambda q: [])
    monkeypatch.setattr(part_index, "suggest_pns", lambda q: [{"part_number": "MIRIP-1"}])
    monkeypatch.setattr(part_index, "suggest_names", lambda q, limit=6: [])
    monkeypatch.setattr(search_log, "record_miss",
                        lambda q, m="", s="": dicatat.append((q, m, s)))
    r = parts_router.search(q="PN-KARANGAN-9", page=1, page_size=20, user=USER)
    assert r.results == []
    assert dicatat == [("PN-KARANGAN-9", "pn", "search")]
    assert r.saran


# ── pembersihan miss BASI ───────────────────────────────────────────────────
def test_query_yang_kini_ketemu_dicabut_dari_daftar_nihil(monkeypatch):
    dicabut = []
    monkeypatch.setattr(part_index, "search_part_number", lambda q: [_baris("VG-1", "Filter")])
    monkeypatch.setattr(part_index, "is_exact_match_found", lambda q: True)
    monkeypatch.setattr(search_log, "resolve_miss", lambda q: dicabut.append(q) or True)
    parts_router.search(q="VG61000070005", page=1, page_size=20, user=USER)
    assert dicabut == ["VG61000070005"]


def test_pencabutan_hanya_di_halaman_pertama(monkeypatch):
    dicabut = []
    monkeypatch.setattr(part_index, "search_part_number", lambda q: [_baris()])
    monkeypatch.setattr(part_index, "is_exact_match_found", lambda q: True)
    monkeypatch.setattr(search_log, "resolve_miss", lambda q: dicabut.append(q) or True)
    parts_router.search(q="VG-1", page=2, page_size=20, user=USER)
    assert dicabut == []


def test_pencabutan_gagal_tak_menjatuhkan_pencarian(monkeypatch):
    """Daftar miss adalah catatan sampingan — kegagalannya tak boleh terasa user."""
    monkeypatch.setattr(part_index, "search_part_number", lambda q: [_baris()])
    monkeypatch.setattr(part_index, "is_exact_match_found", lambda q: True)

    def boom(q):
        raise RuntimeError("berkas terkunci")
    monkeypatch.setattr(search_log, "resolve_miss", boom)
    r = parts_router.search(q="X-1", page=1, page_size=20, user=USER)
    assert len(r.results) == 1


def test_search_name_juga_mencabut_miss(monkeypatch):
    dicabut = []
    monkeypatch.setattr(part_index, "search_part_name", lambda q: [_baris()])
    monkeypatch.setattr(search_log, "resolve_miss", lambda q: dicabut.append(q) or True)
    parts_router.search_name(q="fuel filter", page=1, page_size=20, user=USER)
    assert dicabut == ["fuel filter"]


# ── perilaku resolve_miss itu sendiri ───────────────────────────────────────
def test_resolve_miss_menghapus_dan_idempoten(monkeypatch, tmp_path):
    monkeypatch.setattr(search_log, "_path", lambda: tmp_path / "search_misses.json")
    search_log._CACHE = {"mtime": None, "data": {}} if hasattr(search_log, "_CACHE") else None
    search_log.record_miss("PN-UJI-123", "pn", "search")
    assert search_log.total_count() >= 1
    assert search_log.resolve_miss("PN-UJI-123") is True
    assert search_log.resolve_miss("PN-UJI-123") is False      # idempoten
