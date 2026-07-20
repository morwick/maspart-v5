"""Store pengetahuan admin — CRUD atomik, peringkat pencarian, gating pembeli.

Semua path dialihkan ke tmp_path agar test tak menyentuh data/ai_pengetahuan
asli. Cache per-mtime knowledge_util dibersihkan tiap test karena beberapa test
menulis file dengan isi berbeda pada path yang sama dalam hitungan milidetik.
"""
import json

import pytest

from app.services import knowledge_util, pengetahuan, sinonim


@pytest.fixture(autouse=True)
def _tmp_store(tmp_path, monkeypatch):
    monkeypatch.setattr(pengetahuan, "_dir", lambda: tmp_path)
    # kamus sinonim kosong → ekspansi tak mencemari asersi peringkat
    monkeypatch.setattr(sinonim, "entries", lambda: [])
    knowledge_util._LOAD_CACHE.clear()
    yield tmp_path
    knowledge_util._LOAD_CACHE.clear()


def _chunk(**kw):
    d = {
        "id": "aa#0001", "dok_id": "aa", "judul": "Dokumen", "judul_id": "",
        "kata_kunci": [], "ringkasan": "", "teks": "", "tabel": [],
        "gambar_ref": [], "sumber": "teks-admin", "halaman": 0, "tipe": "teks",
        "untuk_pembeli": False, "dicari": True, "kode": [],
    }
    d.update(kw)
    return d


def _tulis(rows):
    pengetahuan._save_chunks(rows)
    knowledge_util._LOAD_CACHE.clear()


# ── CRUD dokumen ─────────────────────────────────────────────────────
def test_add_dokumen_tersimpan_dan_berstatus_antre(_tmp_store):
    d = pengetahuan.add_dokumen("  Prosedur Retur  ", oleh="admin",
                                teks_admin="Retur maksimal 7 hari.")
    assert d["judul"] == "Prosedur Retur"
    assert d["status"] == "antre"
    assert d["untuk_pembeli"] is False       # default aman
    on_disk = json.loads((_tmp_store / "dokumen.json").read_text(encoding="utf-8"))
    assert on_disk == [d]


def test_add_dokumen_tolak_judul_kosong():
    with pytest.raises(ValueError):
        pengetahuan.add_dokumen("   ")


def test_update_dokumen_tak_dikenal_keyerror():
    with pytest.raises(KeyError):
        pengetahuan.update_dokumen("nihil", judul="X")


def test_untuk_pembeli_dipropagasi_ke_chunk():
    d = pengetahuan.add_dokumen("Katalog Promo", teks_admin="isi")
    _tulis([_chunk(dok_id=d["id"], id=f"{d['id']}#0001"),
            _chunk(dok_id=d["id"], id=f"{d['id']}#0002")])
    pengetahuan.update_dokumen(d["id"], untuk_pembeli=True)
    knowledge_util._LOAD_CACHE.clear()
    assert all(c["untuk_pembeli"] for c in pengetahuan.chunks())


def test_delete_dokumen_buang_chunk_dan_media(_tmp_store):
    d = pengetahuan.add_dokumen("Sekali Pakai", teks_admin="isi")
    _tulis([_chunk(dok_id=d["id"], id=f"{d['id']}#0001"), _chunk(dok_id="lain")])
    media = _tmp_store / "media"
    media.mkdir(parents=True, exist_ok=True)
    (media / f"{d['id']}_001.png").write_bytes(b"x")
    pengetahuan.delete_dokumen(d["id"])
    knowledge_util._LOAD_CACHE.clear()
    assert [c["dok_id"] for c in pengetahuan.chunks()] == ["lain"]
    assert not (media / f"{d['id']}_001.png").exists()


def test_file_korup_tidak_crash(_tmp_store):
    (_tmp_store / "pengetahuan.json").write_text("{bukan json", encoding="utf-8")
    knowledge_util._LOAD_CACHE.clear()
    assert pengetahuan.chunks() == []
    assert pengetahuan.search("apa saja") == []


# ── chunk ────────────────────────────────────────────────────────────
def test_replace_chunks_hanya_ganti_dokumen_itu():
    _tulis([_chunk(dok_id="aa", id="aa#0001"), _chunk(dok_id="bb", id="bb#0001")])
    pengetahuan.replace_chunks("aa", [_chunk(dok_id="aa", id="aa#0009")])
    knowledge_util._LOAD_CACHE.clear()
    ids = sorted(c["id"] for c in pengetahuan.chunks())
    assert ids == ["aa#0009", "bb#0001"]


def test_update_chunk_kurasi_manual():
    _tulis([_chunk(id="aa#0001")])
    c = pengetahuan.update_chunk("aa#0001", judul_id="Cara Klaim Garansi",
                                 kata_kunci=["garansi", "garansi", " klaim "])
    assert c["judul_id"] == "Cara Klaim Garansi"
    assert c["kata_kunci"] == ["garansi", "klaim"]   # dedup + strip


def test_update_chunk_tak_dikenal_keyerror():
    with pytest.raises(KeyError):
        pengetahuan.update_chunk("tidak#ada", dicari=False)


# ── pencarian ────────────────────────────────────────────────────────
def test_judul_id_menang_atas_teks():
    _tulis([
        _chunk(id="aa#0001", judul_id="Prosedur retur barang",
               teks="ringkasan singkat"),
        _chunk(id="aa#0002", teks="dokumen ini menyebut retur sekali saja"),
    ])
    hasil = pengetahuan.search("retur")
    assert [h["id"] for h in hasil] == ["aa#0001", "aa#0002"]


def test_token_berangka_berbobot_lebih():
    _tulis([
        _chunk(id="aa#0001", kode=["WG9100"], teks="tanpa kata itu"),
        _chunk(id="aa#0002", teks="katalog umum WG9100 tercantum di sini"),
    ])
    hasil = pengetahuan.search("WG9100")
    assert hasil[0]["id"] == "aa#0001"      # cocok di `kode` (×4) > `teks` (×1)


def test_query_kosong_dan_tanpa_kecocokan():
    _tulis([_chunk(id="aa#0001", teks="isi apa pun")])
    assert pengetahuan.search("") == []
    assert pengetahuan.search("zebra kutub") == []


def test_dicari_false_disembunyikan():
    _tulis([_chunk(id="aa#0001", teks="prosedur retur", dicari=False)])
    assert pengetahuan.search("retur") == []


def test_dokumen_nonaktif_disembunyikan():
    d = pengetahuan.add_dokumen("Kebijakan Lama", teks_admin="isi")
    _tulis([_chunk(dok_id=d["id"], id=f"{d['id']}#0001", teks="prosedur retur")])
    assert len(pengetahuan.search("retur")) == 1
    pengetahuan.update_dokumen(d["id"], aktif=False)
    knowledge_util._LOAD_CACHE.clear()
    assert pengetahuan.search("retur") == []


def test_pembeli_hanya_lihat_yang_dipublikasikan():
    _tulis([
        _chunk(id="aa#0001", teks="prosedur retur internal", untuk_pembeli=False),
        _chunk(id="aa#0002", teks="prosedur retur pelanggan", untuk_pembeli=True),
    ])
    assert len(pengetahuan.search("retur")) == 2
    hasil = pengetahuan.search("retur", untuk_pembeli=True)
    assert [h["id"] for h in hasil] == ["aa#0002"]


def test_tabel_ikut_tercari():
    _tulis([_chunk(id="aa#0001", tabel=[["Part", "Torsi"], ["baut roda", "600 Nm"]])])
    assert [h["id"] for h in pengetahuan.search("torsi")] == ["aa#0001"]


def test_ekspansi_sinonim_menemukan_istilah_lapangan(monkeypatch):
    monkeypatch.setattr(sinonim, "entries",
                        lambda: [{"grup": "rem", "triggers": ["kampas rem"],
                                  "keywords": ["brake pad"]}])
    _tulis([_chunk(id="aa#0001", teks="stok brake pad tersedia di gudang")])
    assert [h["id"] for h in pengetahuan.search("kampas rem")] == ["aa#0001"]


def test_kecocokan_langsung_menang_atas_sinonim(monkeypatch):
    monkeypatch.setattr(sinonim, "entries",
                        lambda: [{"grup": "rem", "triggers": ["kampas rem"],
                                  "keywords": ["brake pad"]}])
    _tulis([
        _chunk(id="aa#0001", judul_id="brake pad depan"),
        _chunk(id="aa#0002", judul_id="kampas rem depan"),
    ])
    assert pengetahuan.search("kampas rem")[0]["id"] == "aa#0002"


def test_limit_dihormati():
    _tulis([_chunk(id=f"aa#{i:04d}", teks="prosedur retur") for i in range(9)])
    assert len(pengetahuan.search("retur", limit=3)) == 3


# ── gambar ───────────────────────────────────────────────────────────
def test_image_bytes_menolak_traversal_dan_tak_terdaftar(_tmp_store):
    media = _tmp_store / "media"
    media.mkdir(parents=True, exist_ok=True)
    (media / "aa_001.png").write_bytes(b"PNGDATA")
    _tulis([_chunk(id="aa#0001", gambar_ref=["aa_001.png"])])
    assert pengetahuan.image_bytes("aa_001.png") == b"PNGDATA"
    assert pengetahuan.image_bytes("../../etc/passwd") is None
    assert pengetahuan.image_bytes("aa_001.png/../x.png") is None
    assert pengetahuan.image_bytes("belum_terdaftar.png") is None
    assert pengetahuan.image_bytes("") is None


# ── util ─────────────────────────────────────────────────────────────
def test_kode_dari_teks():
    kode = pengetahuan.kode_dari_teks("Pakai WG9100 dan AZ1560 untuk 12 unit")
    assert "WG9100" in kode and "AZ1560" in kode
    assert "12" not in kode            # angka polos bukan kode
