"""Pencarian nihil → sinyal PEMBELIAN (tool admin permintaan_tak_terlayani).

Audit 2026-08-09 atas 207 entri Pencarian Nihil produksi (289 kejadian):
  131 (45%) gagal TANPA saran      → permintaan sungguhan
  102 (35%) gagal tapi ada PN mirip → salah ketik/varian, ⛔ jangan dibeli
   38 (13%) ternyata sudah ketemu   → basi, harus dibuang
Daftar mentah mencampur ketiganya; tes ini mengunci pemisahannya.
"""
import time

import pytest

from app.services import ai_assistant as A
from app.services import part_index, permintaan_tak_terlayani as P
from app.services import search_log

ADMIN = {"username": "mas", "role": "admin"}
STAF = {"username": "budi", "role": "staff"}


def _miss(q, n=1, umur_hari=1.0):
    return {"query": q, "count": n, "last": int(time.time() - umur_hari * 86400),
            "sources": ["search"]}


@pytest.fixture(autouse=True)
def _kosong(monkeypatch):
    """Semua jalur pencarian nihil kecuali yang di-set tiap tes."""
    monkeypatch.setattr(part_index, "search_part_number", lambda q: [])
    monkeypatch.setattr(part_index, "smart_pn_search", lambda q: ([], ""))
    monkeypatch.setattr(part_index, "search_part_name", lambda q: [])
    monkeypatch.setattr(part_index, "suggest_pns", lambda q: [])


def test_tanpa_pn_mirip_masuk_permintaan_sungguhan(monkeypatch):
    monkeypatch.setattr(search_log, "top_misses",
                        lambda n=500: [_miss("TP1002004233", 9)])
    out = P.analisa()
    assert out["jumlah_tak_terlayani"] == 1
    b = out["permintaan_tak_terlayani"][0]
    assert b["dicari"] == "TP1002004233"
    assert b["berapa_kali"] == 9
    assert b["bentuk"] == "pn"
    assert out["jumlah_kemungkinan_salah_ketik"] == 0


def test_ada_pn_mirip_dipisah_sebagai_salah_ketik(monkeypatch):
    monkeypatch.setattr(search_log, "top_misses",
                        lambda n=500: [_miss("WG972519005", 4)])
    monkeypatch.setattr(part_index, "suggest_pns",
                        lambda q: [{"part_number": "WG9725190055"}])
    out = P.analisa()
    assert out["jumlah_tak_terlayani"] == 0, "salah ketik ⛔ bukan sinyal beli"
    assert out["jumlah_kemungkinan_salah_ketik"] == 1
    assert out["kemungkinan_salah_ketik"][0]["pn_mirip_di_katalog"] == ["WG9725190055"]


def test_yang_kini_KETEMU_dibuang(monkeypatch):
    """13% daftar produksi ternyata sudah bisa ditemukan — membelinya salah."""
    monkeypatch.setattr(search_log, "top_misses",
                        lambda n=500: [_miss("VG61000070005", 3)])
    monkeypatch.setattr(part_index, "search_part_number",
                        lambda q: [{"part_number": "VG61000070005"}])
    out = P.analisa()
    assert out["jumlah_tak_terlayani"] == 0
    assert out["dibuang_karena_sudah_ketemu"] == 1


def test_ketemu_lewat_NAMA_juga_dibuang(monkeypatch):
    monkeypatch.setattr(search_log, "top_misses",
                        lambda n=500: [_miss("fuel filter", 5)])
    monkeypatch.setattr(part_index, "search_part_name", lambda q: [{"part_number": "X"}])
    out = P.analisa()
    assert out["dibuang_karena_sudah_ketemu"] == 1
    assert out["jumlah_tak_terlayani"] == 0


def test_permintaan_basi_dibuang(monkeypatch):
    monkeypatch.setattr(search_log, "top_misses",
                        lambda n=500: [_miss("PN-LAMA-1", 2, umur_hari=200)])
    out = P.analisa(maks_umur_hari=60)
    assert out["jumlah_tak_terlayani"] == 0
    assert out["dibuang_karena_basi"] == 1


def test_gagal_cek_TIDAK_dianggap_tak_ada(monkeypatch):
    """gagal-cek ≠ tidak-ada: indeks error tak boleh melahirkan usulan beli."""
    def boom(q):
        raise RuntimeError("indeks rusak")
    monkeypatch.setattr(search_log, "top_misses", lambda n=500: [_miss("APA-SAJA-123", 5)])
    monkeypatch.setattr(part_index, "search_part_number", boom)
    out = P.analisa()
    assert out["jumlah_tak_terlayani"] == 0


def test_daftar_miss_tak_terbaca_dilaporkan_jujur(monkeypatch):
    def boom(n=500):
        raise RuntimeError("berkas hilang")
    monkeypatch.setattr(search_log, "top_misses", boom)
    out = P.analisa()
    assert out["found"] is False and out["gagal_dicek"] is True


def test_urutan_dari_paling_sering(monkeypatch):
    monkeypatch.setattr(search_log, "top_misses", lambda n=500: [
        _miss("AAA111222", 2), _miss("BBB333444", 15), _miss("CCC555666", 7)])
    out = P.analisa()
    assert [b["dicari"] for b in out["permintaan_tak_terlayani"]] == [
        "BBB333444", "CCC555666", "AAA111222"]


def test_min_kejadian_dan_limit(monkeypatch):
    monkeypatch.setattr(search_log, "top_misses", lambda n=500: [
        _miss("AAA111222", 1), _miss("BBB333444", 9)])
    assert P.analisa(min_kejadian=5)["jumlah_tak_terlayani"] == 1
    assert P.analisa(limit=1)["jumlah_tak_terlayani"] == 1


def test_catatan_melarang_salah_baca_angka(monkeypatch):
    monkeypatch.setattr(search_log, "top_misses", lambda n=500: [_miss("ZZZ999888", 3)])
    out = P.analisa()
    c = out["catatan"].lower()
    assert "jumlah pencarian" in c and "bukan jumlah permintaan barang" in c


# ── gerbang & pendaftaran tool ──────────────────────────────────────────────
def test_hanya_admin():
    assert A._t_permintaan_tak_terlayani({}, STAF).get("denied") is True


def test_terdaftar_di_spec_dan_dispatch():
    nama = {s["function"]["name"] for s in A._tool_specs(ADMIN)}
    assert "permintaan_tak_terlayani" in nama
    assert "permintaan_tak_terlayani" in A._DISPATCH
    assert "permintaan_tak_terlayani" not in {
        s["function"]["name"] for s in A._tool_specs(STAF)}


def test_argumen_ngawur_tak_menjatuhkan(monkeypatch):
    """Model kadang mengirim argumen ngawur; tool tak boleh melempar. Umur
    negatif dijepit ke 1 hari — makanya miss di sini dibuat masih segar."""
    monkeypatch.setattr(search_log, "top_misses",
                        lambda n=500: [_miss("QQQ111222", 2, umur_hari=0.1)])
    out = A._t_permintaan_tak_terlayani(
        {"limit": "banyak", "min_kejadian": None, "maks_umur_hari": -5}, ADMIN)
    assert out["found"] is True
    assert out["permintaan_tak_terlayani"][0]["dicari"] == "QQQ111222"
