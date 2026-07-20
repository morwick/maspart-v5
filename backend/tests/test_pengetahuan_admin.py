"""Endpoint admin Pengetahuan AI: guard, validasi unggahan, CRUD, propagasi.

Handler dipanggil LANGSUNG (pola test_admin_gudang_postal) — lebih murah dari
TestClient dan cukup, karena otorisasi diuji terpisah lewat inspeksi dependency
tiap route.
"""
import asyncio
import io
import json

import pytest
from fastapi import HTTPException, UploadFile

from app.deps import require_admin
from app.main import app
from app.routers import admin as R
from app.services import knowledge_util, pengetahuan


@pytest.fixture(autouse=True)
def _tmp_store(tmp_path, monkeypatch):
    monkeypatch.setattr(pengetahuan, "_dir", lambda: tmp_path)
    # indexing dijalankan terpisah — endpoint hanya perlu MENGANTRE
    monkeypatch.setattr(R.pengetahuan_index, "antre", lambda dok_id: None)
    knowledge_util._LOAD_CACHE.clear()
    yield tmp_path
    knowledge_util._LOAD_CACHE.clear()


ADMIN = {"username": "admin", "role": "admin"}


def _upload(nama: str, data: bytes) -> UploadFile:
    return UploadFile(filename=nama, file=io.BytesIO(data))


def _tambah(**kw):
    p = dict(judul="Prosedur Retur", deskripsi="", teks="Retur maksimal 7 hari.",
             tabel_json="", tag="", untuk_pembeli=False, pakai_ai=True,
             files=None, admin=ADMIN)
    p.update(kw)
    return asyncio.run(R.pengetahuan_add(**p))


# ── otorisasi ────────────────────────────────────────────────────────
def test_semua_route_pengetahuan_wajib_admin():
    rute = [r for r in app.routes if "/api/admin/pengetahuan" in getattr(r, "path", "")]
    assert rute, "route pengetahuan tidak terdaftar"
    for r in rute:
        deps = [d.call for d in r.dependant.dependencies]
        assert require_admin in deps, f"{r.path} tidak dijaga require_admin"


# ── tambah ───────────────────────────────────────────────────────────
def test_tambah_teks_mengembalikan_202_dan_tersimpan():
    res = _tambah()
    assert res["ok"] is True and res["status"] == "antre"
    d = pengetahuan.get_dokumen(res["id"])
    assert d["judul"] == "Prosedur Retur"
    assert d["teks_admin"] == "Retur maksimal 7 hari."
    assert d["untuk_pembeli"] is False


def test_tambah_tanpa_isi_ditolak():
    with pytest.raises(HTTPException) as e:
        _tambah(teks="")
    assert e.value.status_code == 400


def test_tambah_dengan_berkas_menyimpan_nama_server(_tmp_store):
    res = _tambah(teks="", files=[_upload("Data Torsi.csv", b"a;b\n1;2\n")])
    d = pengetahuan.get_dokumen(res["id"])
    b = d["berkas"][0]
    assert b["nama"] == "Data Torsi.csv"          # nama asli hanya metadata
    assert b["nama_simpan"] == f"{res['id']}_0.csv"   # nama di disk dari server
    assert (_tmp_store / "berkas" / b["nama_simpan"]).is_file()


def test_nama_berkas_ber_path_dinetralkan(_tmp_store):
    res = _tambah(teks="", files=[_upload("../../etc/passwd.txt", b"x")])
    d = pengetahuan.get_dokumen(res["id"])
    assert d["berkas"][0]["nama"] == "passwd.txt"
    assert d["berkas"][0]["nama_simpan"] == f"{res['id']}_0.txt"


def test_ekstensi_tak_didukung_ditolak():
    with pytest.raises(HTTPException) as e:
        _tambah(teks="", files=[_upload("virus.exe", b"MZ")])
    assert e.value.status_code == 400
    assert "tidak didukung" in e.value.detail


def test_magic_byte_tak_cocok_ditolak():
    with pytest.raises(HTTPException) as e:
        _tambah(teks="", files=[_upload("palsu.pdf", b"ini teks biasa")])
    assert e.value.status_code == 400
    assert "tidak cocok" in e.value.detail


def test_pdf_dengan_magic_benar_diterima():
    res = _tambah(teks="", files=[_upload("asli.pdf", b"%PDF-1.7\n...")])
    assert pengetahuan.get_dokumen(res["id"])["berkas"][0]["ext"] == ".pdf"


def test_terlalu_banyak_berkas_ditolak():
    files = [_upload(f"f{i}.txt", b"x") for i in range(11)]
    with pytest.raises(HTTPException) as e:
        _tambah(teks="", files=files)
    assert e.value.status_code == 400


def test_berkas_kelewat_besar_ditolak(monkeypatch):
    monkeypatch.setattr(R, "_MAX_PENGETAHUAN_BYTES", 10)
    with pytest.raises(HTTPException) as e:
        _tambah(teks="", files=[_upload("besar.txt", b"x" * 50)])
    assert e.value.status_code == 413


def test_total_unggahan_kelewat_besar_ditolak(monkeypatch):
    monkeypatch.setattr(R, "_MAX_PENGETAHUAN_TOTAL", 30)
    with pytest.raises(HTTPException) as e:
        _tambah(teks="", files=[_upload("a.txt", b"x" * 20),
                                _upload("b.txt", b"y" * 20)])
    assert e.value.status_code == 413


def test_berkas_ditolak_tidak_meninggalkan_dokumen_setengah_jadi():
    with pytest.raises(HTTPException):
        _tambah(teks="", files=[_upload("ok.txt", b"x"), _upload("bad.exe", b"MZ")])
    assert pengetahuan.load_dokumen() == []


def test_tabel_json_rusak_ditolak():
    with pytest.raises(HTTPException) as e:
        _tambah(tabel_json="{bukan json")
    assert e.value.status_code == 400


def test_tabel_json_valid_tersimpan():
    res = _tambah(tabel_json=json.dumps([["Part", "Torsi"], ["baut", "600"]]))
    assert pengetahuan.get_dokumen(res["id"])["tabel_admin"][0] == ["Part", "Torsi"]


# ── baca ─────────────────────────────────────────────────────────────
def test_list_dan_detail():
    res = _tambah()
    assert R.pengetahuan_list(_admin=ADMIN)["jumlah"] == 1
    detail = R.pengetahuan_detail(res["id"], _admin=ADMIN)
    assert detail["dokumen"]["id"] == res["id"] and detail["chunk"] == []


def test_detail_tak_dikenal_404():
    with pytest.raises(HTTPException) as e:
        R.pengetahuan_detail("nihil", _admin=ADMIN)
    assert e.value.status_code == 404


def test_status_ringkas():
    res = _tambah()
    s = R.pengetahuan_status(res["id"], _admin=ADMIN)
    assert s["status"] == "antre" and "progres" in s


# ── ubah ─────────────────────────────────────────────────────────────
def test_patch_untuk_pembeli_dipropagasi_ke_chunk():
    res = _tambah()
    pengetahuan.replace_chunks(res["id"], [{
        "id": f"{res['id']}#0001", "dok_id": res["id"], "judul": "Prosedur Retur",
        "untuk_pembeli": False, "dicari": True, "teks": "isi",
    }])
    knowledge_util._LOAD_CACHE.clear()
    R.pengetahuan_update(res["id"], R.PengetahuanPatch(untuk_pembeli=True), _admin=ADMIN)
    knowledge_util._LOAD_CACHE.clear()
    assert all(c["untuk_pembeli"] for c in pengetahuan.chunks_dokumen(res["id"]))


def test_patch_kosong_400():
    res = _tambah()
    with pytest.raises(HTTPException) as e:
        R.pengetahuan_update(res["id"], R.PengetahuanPatch(), _admin=ADMIN)
    assert e.value.status_code == 400


def test_patch_dokumen_tak_dikenal_404():
    with pytest.raises(HTTPException) as e:
        R.pengetahuan_update("nihil", R.PengetahuanPatch(judul="X"), _admin=ADMIN)
    assert e.value.status_code == 404


def test_patch_chunk_kurasi():
    res = _tambah()
    pengetahuan.replace_chunks(res["id"], [{
        "id": f"{res['id']}#0001", "dok_id": res["id"], "judul_id": "lama",
        "kata_kunci": [], "dicari": True,
    }])
    knowledge_util._LOAD_CACHE.clear()
    out = R.pengetahuan_update_chunk(res["id"], "0001",
                                     R.PengetahuanChunkPatch(judul_id="baru",
                                                             kata_kunci=["retur"]),
                                     _admin=ADMIN)
    assert out["chunk"]["judul_id"] == "baru" and out["chunk"]["kata_kunci"] == ["retur"]


def test_patch_chunk_tak_dikenal_404():
    res = _tambah()
    with pytest.raises(HTTPException) as e:
        R.pengetahuan_update_chunk(res["id"], "9999",
                                   R.PengetahuanChunkPatch(dicari=False), _admin=ADMIN)
    assert e.value.status_code == 404


def test_reindex_mengantre_ulang():
    res = _tambah()
    pengetahuan.set_status(res["id"], "selesai")
    out = R.pengetahuan_reindex(res["id"], _admin=ADMIN)
    assert out["status"] == "antre"
    assert pengetahuan.get_dokumen(res["id"])["status"] == "antre"


def test_reindex_tak_dikenal_404():
    with pytest.raises(HTTPException) as e:
        R.pengetahuan_reindex("nihil", _admin=ADMIN)
    assert e.value.status_code == 404


# ── hapus & uji cari ─────────────────────────────────────────────────
def test_hapus_dokumen():
    res = _tambah()
    R.pengetahuan_delete(res["id"], _admin=ADMIN)
    assert pengetahuan.load_dokumen() == []


def test_hapus_tak_dikenal_404():
    with pytest.raises(HTTPException) as e:
        R.pengetahuan_delete("nihil", _admin=ADMIN)
    assert e.value.status_code == 404


def test_list_menandai_dokumen_skema_lama():
    res = _tambah()
    pengetahuan.replace_chunks(res["id"], [{
        "id": f"{res['id']}#0001", "dok_id": res["id"], "judul": "Prosedur Retur",
        "teks": "isi", "dicari": True,          # tanpa `skema` → skema 1
    }])
    knowledge_util._LOAD_CACHE.clear()
    d = R.pengetahuan_list(_admin=ADMIN)["dokumen"][0]
    assert d["perlu_reindex"] is True

    pengetahuan.replace_chunks(res["id"], [{
        "id": f"{res['id']}#0001", "dok_id": res["id"], "judul": "Prosedur Retur",
        "teks": "isi", "dicari": True, "skema": 2,
    }])
    knowledge_util._LOAD_CACHE.clear()
    assert R.pengetahuan_list(_admin=ADMIN)["dokumen"][0]["perlu_reindex"] is False


def test_sapu_media_membuang_gambar_yatim(_tmp_store):
    """Re-index menomori ulang gambar; tanpa sapuan, file lama menumpuk di
    bind-mount sampai penuh."""
    res = _tambah()
    media = _tmp_store / "media"
    media.mkdir(parents=True, exist_ok=True)
    (media / f"{res['id']}_000.png").write_bytes(b"PNG")
    (media / f"{res['id']}_001.png").write_bytes(b"PNG")     # jadi yatim
    pengetahuan.replace_chunks(res["id"], [{
        "id": f"{res['id']}#0001", "dok_id": res["id"], "judul": "x",
        "gambar_ref": [f"{res['id']}_000.png"], "dicari": True, "skema": 2,
    }])
    knowledge_util._LOAD_CACHE.clear()
    assert pengetahuan.sapu_media(res["id"]) == 1
    assert (media / f"{res['id']}_000.png").exists()
    assert not (media / f"{res['id']}_001.png").exists()


def test_uji_cari_seperti_asisten():
    res = _tambah()
    pengetahuan.replace_chunks(res["id"], [{
        "id": f"{res['id']}#0001", "dok_id": res["id"], "judul": "Prosedur Retur",
        "judul_id": "Cara retur", "kata_kunci": ["retur"], "teks": "isi",
        "dicari": True, "untuk_pembeli": False,
    }])
    knowledge_util._LOAD_CACHE.clear()
    out = R.pengetahuan_cari(R.PengetahuanCari(q="retur"), _admin=ADMIN)
    assert out["jumlah"] == 1 and out["hasil"][0]["judul_id"] == "Cara retur"
