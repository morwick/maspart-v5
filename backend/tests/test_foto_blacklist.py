"""Daftar-hitam foto per PN — foto yang TERBUKTI bukan part itu.

Kejadian nyata yang melahirkan fitur ini (2026-09-07): SIMS memasang 8 foto
DEKRUP (离合器压盘总成) pada PN `AZ992216002101` yang sebenarnya KAMPAS kopling
(离合器从动盘总成) — label cor di foto terbaca PN pressure plate. Galeri
Cari-by-Foto menyalin 8 URL itu apa adanya, sehingga bukan cuma halaman part
yang salah gambar: memotret dekrup pun mengarah ke PN kampas.

Yang dikunci di sini:
  1. foto ditandai → hilang dari /api/parts/photos DAN dari kandidat Cari by Foto;
  2. sumber disaring SATU-SATU — habisnya foto SIMS harus JATUH ke galeri, bukan
     langsung "tidak ada gambar";
  3. hitung `tersembunyi` tak menggandakan foto yang muncul di dua sumber;
  4. pulihkan mengembalikan keadaan semula (baris galeri tak pernah dihapus).
"""
import pytest

from app.routers import admin as admin_router
from app.routers import parts as parts_router
from app.services import foto_blacklist

PN = "AZ992216002101"
U1 = "http://sims/f?fileUploadInfoId=1946768443548835842"
U2 = "http://sims/f?fileUploadInfoId=1946768445931200514"
U3 = "http://sims/f?fileUploadInfoId=1946768447634087938"


@pytest.fixture
def bl(tmp_path, monkeypatch):
    """Arahkan foto_blacklist.json ke folder sementara + bersihkan cache proses."""
    s = foto_blacklist.get_settings()
    monkeypatch.setattr(type(s), "data_path", property(lambda _self: tmp_path))
    foto_blacklist.reload()
    yield foto_blacklist
    foto_blacklist.reload()


@pytest.fixture
def photos(monkeypatch):
    """Sumber foto dipalsukan: SIMS, galeri lokal, tabel part_photos."""
    src = {"sims": [], "index": [], "lokal": []}
    monkeypatch.setattr(parts_router.sims, "get_images",
                        lambda pn, force_refresh=False: list(src["sims"]))
    monkeypatch.setattr(parts_router.image_search, "indexed_urls",
                        lambda pn: list(src["index"]))
    monkeypatch.setattr(parts_router, "fetch_part_photos", lambda pn: list(src["lokal"]))
    return src


# ── Servis ───────────────────────────────────────────────────────────
def test_tandai_lalu_saring(bl):
    bl.tambah(PN, [U1], oleh="admin", catatan="foto dekrup")
    assert bl.filter_urls(PN, [U1, U2]) == [U2]
    assert bl.diblokir(PN, U1) and not bl.diblokir(PN, U2)
    # PN lain tak ikut terpengaruh.
    assert bl.filter_urls("WG9725160021", [U1]) == [U1]


def test_pn_tak_peka_besar_kecil_huruf(bl):
    bl.tambah(PN.lower(), [U1])
    assert bl.filter_urls(PN, [U1]) == []


def test_semua_menyembunyikan_foto_yang_belum_pernah_dilihat(bl):
    """`semua` dipakai saat SIMS menempelkan SET foto part lain sepenuhnya —
    termasuk foto yang baru muncul setelah admin menandainya."""
    bl.tambah(PN, semua=True, oleh="admin")
    assert bl.filter_urls(PN, [U1, U2, U3]) == []


def test_tambah_menumpuk_tidak_menimpa(bl):
    bl.tambah(PN, [U1])
    bl.tambah(PN, [U2])
    assert bl.filter_urls(PN, [U1, U2, U3]) == [U3]


def test_tambah_tanpa_url_ditolak(bl):
    with pytest.raises(ValueError):
        bl.tambah(PN, [])


def test_pulihkan_sebagian_dan_seluruhnya(bl):
    bl.tambah(PN, [U1, U2])
    bl.pulihkan(PN, [U1])
    assert bl.filter_urls(PN, [U1, U2]) == [U1]
    bl.pulihkan(PN)
    assert bl.filter_urls(PN, [U1, U2]) == [U1, U2]
    assert bl.entri(PN) == {}


def test_pulihkan_per_url_mencabut_semua(bl):
    """`semua` menyembunyikan foto yang belum kita lihat; memulihkan satu URL
    tak masuk akal bila sisanya tetap disapu buta → `semua` ikut dicabut."""
    bl.tambah(PN, [U1, U2], semua=True)
    bl.pulihkan(PN, [U1])
    assert bl.filter_urls(PN, [U1, U2, U3]) == [U1, U3]


def test_bertahan_di_disk(bl):
    bl.tambah(PN, [U1], oleh="admin", catatan="dekrup")
    bl.reload()                      # buang cache → baca ulang file
    e = bl.entri(PN)
    assert e["urls"] == [U1] and e["oleh"] == "admin" and e["catatan"] == "dekrup"
    assert e["pada"]


def test_versi_naik_saat_berubah(bl):
    """Kunci invalidasi cache pembaca (image_search.photo_url_map)."""
    v = bl.versi()
    bl.tambah(PN, [U1])
    assert bl.versi() > v


def test_filter_rows_membuang_kandidat_cari_by_foto(bl):
    rows = [
        {"part_number": PN, "sims_url": U1, "similarity": 0.9},
        {"part_number": PN, "sims_url": U2, "similarity": 0.8},
        {"part_number": "WG9725160021", "sims_url": U1, "similarity": 0.7},
    ]
    bl.tambah(PN, [U1])
    keluar = bl.filter_rows(rows)
    assert [(r["part_number"], r["sims_url"]) for r in keluar] == [
        (PN, U2), ("WG9725160021", U1)]


def test_filter_rows_tanpa_daftar_hitam_meloloskan_semua(bl):
    rows = [{"part_number": PN, "sims_url": U1}]
    assert bl.filter_rows(rows) == rows


# ── Endpoint /api/parts/photos ───────────────────────────────────────
def test_photos_menyaring_sumber_sims(bl, photos):
    photos["sims"] = [U1, U2]
    bl.tambah(PN, [U1])
    r = parts_router.photos(pn=PN, refresh=False, _user={})
    assert r.photos == [U2] and r.source == "sims" and r.tersembunyi == 1


def test_photos_jatuh_ke_galeri_saat_sims_habis_tersaring(bl, photos):
    """Regresi: menyaring SEKALI di akhir akan menghasilkan "tidak ada gambar"
    padahal galeri punya foto benar di bawahnya."""
    photos["sims"] = [U1]
    photos["index"] = [U3]
    bl.tambah(PN, [U1])
    r = parts_router.photos(pn=PN, refresh=False, _user={})
    assert r.photos == [U3] and r.source == "image_index"


def test_photos_tidak_menggandakan_hitungan_lintas_sumber(bl, photos):
    """Galeri lokal berisi URL SIMS yang sama persis — 1 foto salah tetap 1."""
    photos["sims"] = [U1]
    photos["index"] = [U1]
    photos["lokal"] = []
    bl.tambah(PN, [U1])
    r = parts_router.photos(pn=PN, refresh=False, _user={})
    assert r.photos == [] and r.tersembunyi == 1


def test_photos_utuh_tanpa_daftar_hitam(bl, photos):
    photos["sims"] = [U1, U2]
    r = parts_router.photos(pn=PN, refresh=False, _user={})
    assert r.photos == [U1, U2] and r.tersembunyi == 0


# ── Cari by Foto ─────────────────────────────────────────────────────
def test_kandidat_cari_by_foto_disaring(bl, monkeypatch):
    """Inti fitur: foto dekrup berlabel PN kampas tak boleh lagi memberi suara —
    kalau tidak, memotret dekrup akan terus menjatuhkan orang ke PN yang salah."""
    from app.services import image_search

    monkeypatch.setattr(image_search, "local_index_available", lambda: True)
    monkeypatch.setattr(image_search, "_local_search", lambda q, d, n: [
        {"part_number": PN, "sims_url": U1, "similarity": 0.95},
        {"part_number": "AZ992316001223", "sims_url": U3, "similarity": 0.90},
    ])

    assert len(image_search._fetch_candidates([0.1], 0.7, 10)) == 2
    bl.tambah(PN, [U1])
    keluar = image_search._fetch_candidates([0.1], 0.7, 10)
    assert [r["part_number"] for r in keluar] == ["AZ992316001223"]


def test_peta_foto_etalase_ikut_disaring(bl, monkeypatch):
    """photo_url_map meng-cache berdasar JUMLAH baris galeri — yang tak berubah
    saat blacklist. Tanpa versi() ikut jadi kunci, etalase tetap memajang foto
    yang baru saja ditandai salah."""
    from app.services import image_search

    monkeypatch.setattr(image_search, "local_index_available", lambda: True)
    monkeypatch.setattr(image_search, "_local_meta", [(PN, U1), ("AZ992316001223", U3)])

    assert image_search.photo_url_map().get(PN) == U1
    bl.tambah(PN, [U1])
    assert PN not in image_search.photo_url_map()
    assert image_search.photo_url_map().get("AZ992316001223") == U3


# ── Endpoint admin ───────────────────────────────────────────────────
def test_endpoint_admin_tandai_dan_pulihkan(bl):
    admin_router.foto_blacklist_tambah(
        admin_router.FotoSalahRequest(pn=PN, urls=[U1], catatan="dekrup"),
        admin={"username": "admin"})
    assert foto_blacklist.diblokir(PN, U1)

    satu = admin_router.foto_blacklist_list(pn=PN, _admin={})
    assert satu["entri"]["urls"] == [U1] and satu["entri"]["oleh"] == "admin"

    semua = admin_router.foto_blacklist_list(pn="", _admin={})
    assert semua["total_pn"] == 1 and semua["total_foto"] == 1
    assert semua["daftar"][0]["pn"] == PN

    admin_router.foto_blacklist_pulihkan(
        admin_router.FotoPulihRequest(pn=PN, urls=[]), _admin={})
    assert not foto_blacklist.diblokir(PN, U1)


def test_endpoint_admin_tolak_permintaan_kosong(bl):
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as e:
        admin_router.foto_blacklist_tambah(
            admin_router.FotoSalahRequest(pn=PN, urls=[]), admin={"username": "admin"})
    assert e.value.status_code == 400
