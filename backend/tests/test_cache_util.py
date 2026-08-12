"""Cache in-memory WAJIB berpagar — kelas bug yang menghabiskan RAM produksi.

Latar (2026-08-12): backend produksi 1,66 GB dari jatah 2,44 GB hanya 23 menit
setelah container hidup, merangkak ke 2,31 GB (94,6%) sepanjang hari. Sebabnya
~20 dict cache tingkat-modul yang memeriksa TTL saat DIBACA tapi tak pernah
MENGHAPUS entri kedaluwarsa — jadi cuma tumbuh.

Yang dijaga di sini ada dua lapis:
  1. perilaku `CacheTTL` sendiri (TTL, plafon, urutan buang, setdefault);
  2. ⭐ pengunci kelas bug-nya: SEMUA cache terdaftar punya plafon berhingga, dan
     cache EPC nyata benar-benar berhenti tumbuh saat dipompa ribuan kunci.
Butir 2 itulah yang dulu tak ada, sehingga kebocoran baru ketahuan dari
`docker stats` — bukan dari suite.
"""
import threading
import time

import pytest

from app.services import cache_util
from app.services.cache_util import CacheTTL


# ── perilaku dasar ──────────────────────────────────────────────────────
def test_plafon_membuang_yang_tertua():
    c = CacheTTL("uji.plafon", None, 3)
    for i in range(5):
        c[f"k{i}"] = i
    assert len(c) == 3
    assert c.get("k0") is None and c.get("k1") is None   # tertua dibuang
    assert c.get("k4") == 4


def test_menulis_ulang_menyegarkan_urutan():
    """Kunci yang ditulis ulang jadi TERMUDA — kalau tidak, entri yang justru
    paling sering dipakai malah yang pertama dibuang."""
    c = CacheTTL("uji.urut", None, 3)
    c["a"], c["b"], c["c"] = 1, 2, 3
    c["a"] = 99            # 'a' kembali ke belakang antrean
    c["d"] = 4             # yang dibuang harusnya 'b', bukan 'a'
    assert c.get("a") == 99
    assert c.get("b") is None
    assert c.get("c") == 3 and c.get("d") == 4


def test_ttl_kedaluwarsa(monkeypatch):
    jam = [1000.0]
    monkeypatch.setattr(cache_util.time, "monotonic", lambda: jam[0])
    c = CacheTTL("uji.ttl", 60.0, 100)
    c["a"] = 1
    jam[0] += 59
    assert c.get("a") == 1
    jam[0] += 2                      # lewat 60 dtk
    assert c.get("a") is None
    assert "a" not in c


def test_entri_kedaluwarsa_benar_benar_dihapus(monkeypatch):
    """Inti perbaikannya: kedaluwarsa tak cukup 'tak terbaca' — harus HILANG
    dari memori. Ini persis yang dulu tak terjadi."""
    jam = [1000.0]
    monkeypatch.setattr(cache_util.time, "monotonic", lambda: jam[0])
    c = CacheTTL("uji.buang", 60.0, 10_000)
    for i in range(50):
        c[f"k{i}"] = i
    assert len(c) == 50
    jam[0] += 61
    c["baru"] = 1                    # tulis apa pun → memangkas
    assert len(c) == 1


def test_ttl_none_tak_pernah_kedaluwarsa(monkeypatch):
    jam = [1000.0]
    monkeypatch.setattr(cache_util.time, "monotonic", lambda: jam[0])
    c = CacheTTL("uji.abadi", None, 10)
    c["a"] = 1
    jam[0] += 10 ** 6
    assert c.get("a") == 1


def test_setdefault_mengembalikan_objek_tersimpan():
    """`epc_bom._cat_index` memutasi dict yang dikembalikan setdefault —
    kalau yang dikembalikan salinan, indeks kategorinya diam-diam kosong."""
    c = CacheTTL("uji.setdefault", None, 10)
    dalam = c.setdefault("f", {})
    dalam["nama"] = "node"
    assert c.get("f") == {"nama": "node"}
    assert c.setdefault("f", {}) is dalam        # panggilan kedua = objek SAMA


def test_nilai_none_tetap_dibedakan_dari_absen():
    """`epc_bom._en_cache` menyimpan None untuk 'sudah dicari, tak ada nama EN'.
    Kalau None tak terbedakan dari 'belum pernah dicari', PN itu di-fetch ulang
    terus ke EPC."""
    c = CacheTTL("uji.none", None, 10)
    c["pn"] = None
    sentinel = object()
    assert c.get("pn", sentinel) is None         # ADA, isinya None
    assert c.get("lain", sentinel) is sentinel   # memang absen
    assert "pn" in c


def test_pop_dan_clear():
    c = CacheTTL("uji.pop", None, 10)
    c["a"] = 1
    assert c.pop("a", None) == 1
    assert c.pop("a", None) is None
    with pytest.raises(KeyError):
        c.pop("a")
    c["b"] = 2
    c.clear()
    assert len(c) == 0


def test_getitem_keyerror_saat_absen():
    c = CacheTTL("uji.getitem", None, 10)
    with pytest.raises(KeyError):
        c["hantu"]


def test_maks_nol_ditolak():
    with pytest.raises(ValueError):
        CacheTTL("uji.nol", None, 0)


def test_tahan_banting_banyak_thread():
    """Plafon tak boleh jebol saat ditulis banyak thread sekaligus."""
    c = CacheTTL("uji.thread", None, 50)
    def kerja(n):
        for i in range(200):
            c[f"t{n}-{i}"] = i
            c.get(f"t{n}-{i}")
    ts = [threading.Thread(target=kerja, args=(n,)) for n in range(8)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert len(c) == 50


def test_info_melaporkan_eviksi():
    c = CacheTTL("uji.info", None, 2)
    for i in range(5):
        c[f"k{i}"] = i
    d = c.info()
    assert d["nama"] == "uji.info" and d["entri"] == 2 and d["maks"] == 2
    assert d["dibuang"] == 3        # sinyal 'plafon kesempitan'


# ── ⭐ pengunci kelas bug: tak boleh ada cache tanpa pagar ───────────────
def test_semua_cache_terdaftar_punya_plafon_berhingga():
    import app.services.epc            # noqa: F401  (pastikan modulnya termuat)
    import app.services.epc_bom        # noqa: F401
    import app.services.epc_shantui    # noqa: F401
    import app.services.epc_weichai    # noqa: F401
    import app.services.shipping       # noqa: F401
    import app.services.sims_warranty  # noqa: F401

    semua = cache_util.daftar()
    assert len(semua) >= 20, "cache EPC belum terdaftar — impor modulnya gagal?"
    for c in semua:
        d = c.info()
        assert isinstance(d["maks"], int) and 0 < d["maks"] <= 20_000, d


# ── satu-satunya LOGIKA yang ikut berubah: epc_bom.english_names ────────
# Dulu dua dict paralel (`_en_cache` nilai + `_en_at` stempel waktu); stempelnya
# kini dipegang CacheTTL. Yang gampang hilang dalam penggabungan itu: nilai None
# ("sudah dicari ke EPC, memang tak punya nama Inggris") harus tetap terhitung
# SUDAH DI-CACHE — kalau tidak, tiap PN tanpa nama EN ditembak ulang ke EPC di
# setiap pemanggilan.
def test_english_names_tak_menembak_ulang_pn_yang_sudah_di_cache(monkeypatch):
    from app.services import epc_bom

    epc_bom._en_cache.clear()
    dipanggil = []

    def palsu(pn):
        dipanggil.append(pn)
        return "OIL FILTER" if pn == "PN-ADA" else None

    monkeypatch.setattr(epc_bom, "_english_one", palsu)

    hasil = epc_bom.english_names(["PN-ADA", "PN-KOSONG"])
    assert hasil == {"PN-ADA": "OIL FILTER"}
    assert sorted(dipanggil) == ["PN-ADA", "PN-KOSONG"]

    dipanggil.clear()
    hasil2 = epc_bom.english_names(["PN-ADA", "PN-KOSONG"])
    assert hasil2 == {"PN-ADA": "OIL FILTER"}
    assert dipanggil == [], "PN yang sudah di-cache (termasuk yang None) di-fetch ulang"
    epc_bom._en_cache.clear()


def test_endpoint_memori_menghubungkan_sisa_ram_dgn_gerbang_foto(monkeypatch):
    """/api/admin/monitoring/memori harus menjawab 'kenapa kirim foto gagal':
    di bawah `_SISA_MIN_MB`, OCR foto rangka & kode kesalahan MULAI DITOLAK."""
    from app.routers import admin as admin_router

    monkeypatch.setattr(admin_router.vin_ocr, "_sisa_memori_mb", lambda: 330.0)
    d = admin_router.monitoring_memori(_admin={})
    assert d["sisa_mb"] == 330.0
    assert d["foto_ditolak"] is True          # 330 < 420 → persis insiden 2026-08-12
    assert d["cache_total_entri"] == sum(c["entri"] for c in d["cache"])
    assert any(c["nama"].startswith("epc_bom.") for c in d["cache"])

    monkeypatch.setattr(admin_router.vin_ocr, "_sisa_memori_mb", lambda: 1200.0)
    assert admin_router.monitoring_memori(_admin={})["foto_ditolak"] is False

    # Di laptop (bukan container) jatah tak terbaca → jangan mengarang 'ditolak'.
    monkeypatch.setattr(admin_router.vin_ocr, "_sisa_memori_mb", lambda: None)
    d3 = admin_router.monitoring_memori(_admin={})
    assert d3["sisa_mb"] is None and d3["foto_ditolak"] is False


@pytest.mark.parametrize("nama_modul,nama_cache", [
    ("epc_bom", "_cache"),
    ("epc_bom", "_items_all_cache"),
    ("epc_bom", "_atlas_raw_cache"),
    ("epc_bom", "_cat_open_cache"),
    ("epc_bom", "_fetch_locks"),
    ("epc_weichai", "_bom_cache"),
    ("shipping", "_track_cache"),
    ("sims_warranty", "_CACHE"),
])
def test_cache_nyata_berhenti_tumbuh(nama_modul, nama_cache):
    """Pompa 5.000 kunci unik ke cache SUNGGUHAN. Sebelum perbaikan, tiap satu
    dari ini akan menyimpan kelimaribunya."""
    import importlib
    modul = importlib.import_module(f"app.services.{nama_modul}")
    c = getattr(modul, nama_cache)
    awal = len(c)
    for i in range(5000):
        c[f"__uji{i}"] = {"at": time.monotonic(), "val": i}
    try:
        assert len(c) <= c.info()["maks"]
        assert len(c) < awal + 5000
    finally:
        c.clear()
