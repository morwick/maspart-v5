"""Tool buka_pengetahuan — membaca SATU bagian secara utuh.

Pembagian peran: cari_pengetahuan MENEMUKAN (dipotong), tool ini MEMBACA
(teks penuh, tabel dijahit utuh, gambar lengkap). Kunci yang dipakai adalah
judul MANUSIAWI, bukan id opaque — konsisten dengan _PROJECTIONS yang justru
membuang export_id/image_id dari salinan model.
"""
import pytest

from app.services import ai_assistant as ai
from app.services import knowledge_util, pengetahuan, sinonim

ADMIN = {"username": "admin", "role": "admin"}
PEMBELI = {"username": "budi", "role": "pembeli"}


@pytest.fixture(autouse=True)
def _tmp_store(tmp_path, monkeypatch):
    monkeypatch.setattr(pengetahuan, "_dir", lambda: tmp_path)
    monkeypatch.setattr(sinonim, "entries", lambda: [])
    knowledge_util._LOAD_CACHE.clear()
    pengetahuan._HAY_CACHE.update(mtime=None, rows=None)
    yield tmp_path
    knowledge_util._LOAD_CACHE.clear()
    pengetahuan._HAY_CACHE.update(mtime=None, rows=None)


def _chunk(**kw):
    d = {"id": "aa#0001", "dok_id": "aa", "judul": "Kebijakan Retur",
         "judul_id": "Syarat pengajuan retur", "kata_kunci": [], "ringkasan": "",
         "teks": "Retur diajukan maksimal tujuh hari.", "tabel": [], "kolom": [],
         "baris_total": 0, "baris_dari": 0, "jalur": [], "gambar_ref": [],
         "gambar_info": [], "gambar_teks": "", "sumber": "kebijakan.pdf",
         "halaman": 3, "tipe": "pdf", "untuk_pembeli": False, "dicari": True,
         "kode": [], "bahasa": "", "skema": 2}
    d.update(kw)
    return d


def _isi(rows):
    pengetahuan._save_chunks(rows)
    knowledge_util._LOAD_CACHE.clear()
    pengetahuan._HAY_CACHE.update(mtime=None, rows=None)


def _buka(**args):
    return ai._t_buka_pengetahuan(args, ADMIN)


# ── dasar ────────────────────────────────────────────────────────────
def test_membaca_isi_penuh_tanpa_truncation_agresif():
    panjang = "Langkah retur. " * 200          # ~3000 char
    _isi([_chunk(teks=panjang)])
    res = _buka(dokumen="Kebijakan Retur", bagian="Syarat pengajuan retur")
    assert res["found"] is True
    assert len(res["isi"]) > 1200              # cari_pengetahuan memotong 1200
    assert res["sumber"] == "kebijakan.pdf" and res["halaman"] == 3
    assert list(res)[-1] == "catatan"          # invarian _cap_tool_content


def test_tanpa_bagian_mengembalikan_daftar_isi():
    _isi([_chunk(id="aa#0001", judul_id="Syarat retur"),
          _chunk(id="aa#0002", judul_id="Biaya kirim balik", halaman=4)])
    res = _buka(dokumen="Kebijakan Retur")
    assert res["found"] is True
    assert [d["judul"] for d in res["daftar_isi"]] == ["Syarat retur", "Biaya kirim balik"]
    assert list(res)[-1] == "catatan"


def test_dokumen_wajib_diisi():
    assert "error" in ai._t_buka_pengetahuan({}, ADMIN)


def test_store_kosong():
    assert "error" in _buka(dokumen="Apa saja")


# ── pencocokan kunci manusiawi ───────────────────────────────────────
def test_cocok_persis_lalu_case_insensitive_lalu_substring():
    _isi([_chunk()])
    assert _buka(dokumen="Kebijakan Retur", bagian="Syarat pengajuan retur")["found"]
    assert _buka(dokumen="kebijakan retur", bagian="SYARAT PENGAJUAN RETUR")["found"]
    assert _buka(dokumen="Kebijakan Retur", bagian="syarat pengajuan")["found"]


def test_bagian_salah_mengembalikan_daftar_sah():
    _isi([_chunk(id="aa#0001", judul_id="Syarat retur"),
          _chunk(id="aa#0002", judul_id="Biaya kirim balik", halaman=4)])
    res = _buka(dokumen="Kebijakan Retur", bagian="bagian yang tidak ada")
    assert res["found"] is False
    assert {d["judul"] for d in res["bagian_tersedia"]} == {"Syarat retur",
                                                            "Biaya kirim balik"}
    assert "jangan menebak" in res["catatan"].lower()
    assert list(res)[-1] == "catatan"


def test_dokumen_salah_mengembalikan_daftar_dokumen():
    _isi([_chunk()])
    res = _buka(dokumen="Dokumen Antah Berantah")
    assert res["found"] is False
    assert res["dokumen_tersedia"] == ["Kebijakan Retur"]


def test_ambigu_tidak_ditebak():
    _isi([_chunk(id="aa#0001", judul_id="Syarat retur gudang A"),
          _chunk(id="aa#0002", judul_id="Syarat retur gudang B", halaman=4)])
    res = _buka(dokumen="Kebijakan Retur", bagian="syarat retur")
    assert res["found"] is False
    assert len(res["bagian_tersedia"]) == 2


def test_buka_lewat_halaman():
    _isi([_chunk(id="aa#0001", halaman=3), _chunk(id="aa#0002", halaman=9,
                                                  judul_id="Bagian lain")])
    assert _buka(dokumen="Kebijakan Retur", halaman=9)["bagian"] == "Bagian lain"


def test_halaman_tak_ada_memberi_daftar():
    _isi([_chunk()])
    res = _buka(dokumen="Kebijakan Retur", halaman=99)
    assert res["found"] is False and res["bagian_tersedia"]


# ── daftar bagian tetap ada saat SUKSES ──────────────────────────────
def test_bagian_tersedia_juga_ada_saat_sukses():
    """_trim_old_tool_messages mengganti hasil ronde lama jadi stub — tanpa ini
    model lupa judul yang sah saat ingin membuka bagian berikutnya."""
    _isi([_chunk(id="aa#0001"), _chunk(id="aa#0002", judul_id="Bagian lain", halaman=4)])
    res = _buka(dokumen="Kebijakan Retur", bagian="Syarat pengajuan retur")
    assert res["found"] is True
    assert len(res["bagian_tersedia"]) == 2


# ── tabel dijahit utuh ───────────────────────────────────────────────
def test_tabel_dijahit_dari_chunk_bersaudara():
    """Tabel panjang dipecah 30 baris/chunk saat indexing; tanpa penjahitan
    asisten tak pernah bisa membaca tabel besar secara utuh."""
    kolom = ["Kondisi", "Batas"]
    head = ["Kondisi", "Batas"]
    _isi([
        _chunk(id="aa#0001", judul_id="Tabel syarat", kolom=kolom, baris_dari=0,
               baris_total=45, tabel=[head, *[[f"k{i}", f"{i} hari"] for i in range(30)]]),
        _chunk(id="aa#0002", judul_id="Tabel syarat lanjutan", kolom=kolom,
               baris_dari=30, baris_total=45,
               tabel=[head, *[[f"k{i}", f"{i} hari"] for i in range(30, 45)]]),
    ])
    res = _buka(dokumen="Kebijakan Retur", bagian="Tabel syarat")
    assert res["baris_ditampilkan"] == 45      # 30 + 15 dijahit
    assert res["baris_total"] == 45
    assert res["tabel"][0] == head
    assert ["k44", "44 hari"] in res["tabel"]


def test_hanya_tabel_dan_hanya_gambar():
    _isi([_chunk(tabel=[["a", "b"], ["1", "2"]], kolom=["a", "b"], baris_total=1)])
    r1 = _buka(dokumen="Kebijakan Retur", bagian="Syarat pengajuan retur", hanya="tabel")
    assert "tabel" in r1 and "isi" not in r1
    r2 = _buka(dokumen="Kebijakan Retur", bagian="Syarat pengajuan retur", hanya="gambar")
    assert "tabel" not in r2 and "isi" not in r2


# ── gambar ───────────────────────────────────────────────────────────
def test_gambar_dilampirkan_dengan_keterangan(_tmp_store):
    media = _tmp_store / "media"
    media.mkdir(parents=True, exist_ok=True)
    (media / "aa_000.png").write_bytes(b"PNG")
    _isi([_chunk(gambar_ref=["aa_000.png"],
                 gambar_info=[{"file": "aa_000.png",
                               "caption": "Gambar 1: alur retur", "halaman": 3}])])
    res = _buka(dokumen="Kebijakan Retur", bagian="Syarat pengajuan retur")
    assert res["gambar"][0]["nama_figure"] == "Gambar 1: alur retur"
    assert not res["gambar"][0].get("balon")
    assert res["gambar_keterangan"][0]["keterangan"] == "Gambar 1: alur retur"
    assert "bukan hasil membaca gambarnya" in res["catatan"]
    assert list(res)[-1] == "catatan"


# ── gating pembeli ───────────────────────────────────────────────────
def test_pembeli_tak_pernah_melihat_judul_bagian_internal():
    """Filter dilakukan SEBELUM pencocokan judul, jadi judul internal tak bocor
    bahkan lewat pesan error."""
    _isi([_chunk(id="aa#0001", judul_id="RAHASIA margin internal",
                 untuk_pembeli=False),
          _chunk(id="aa#0002", judul_id="Syarat retur pelanggan", halaman=4,
                 untuk_pembeli=True)])
    res = ai._t_buka_pengetahuan({"dokumen": "Kebijakan Retur"}, PEMBELI)
    judul = [d["judul"] for d in res.get("daftar_isi", [])]
    assert judul == ["Syarat retur pelanggan"]
    salah = ai._t_buka_pengetahuan(
        {"dokumen": "Kebijakan Retur", "bagian": "tak ada"}, PEMBELI)
    assert "RAHASIA" not in str(salah)


def test_pembeli_tak_bisa_membuka_bagian_internal():
    _isi([_chunk(untuk_pembeli=False)])
    res = ai._t_buka_pengetahuan(
        {"dokumen": "Kebijakan Retur", "bagian": "Syarat pengajuan retur"}, PEMBELI)
    assert res["found"] is False


def test_dokumen_nonaktif_tak_bisa_dibuka():
    d = pengetahuan.add_dokumen("Kebijakan Retur", teks_admin="isi")
    _isi([_chunk(dok_id=d["id"])])
    pengetahuan.update_dokumen(d["id"], aktif=False)
    knowledge_util._LOAD_CACHE.clear()
    pengetahuan._HAY_CACHE.update(mtime=None, rows=None)
    assert _buka(dokumen="Kebijakan Retur")["found"] is False


# ── terjemah & registrasi ────────────────────────────────────────────
def test_instruksi_terjemah_untuk_bagian_asing():
    _isi([_chunk(bahasa="zh", teks="退货流程：货物必须在收到后七天内提出退货申请。")])
    res = _buka(dokumen="Kebijakan Retur", bagian="Syarat pengajuan retur")
    assert res["bahasa"] == "zh" and "TERJEMAHKAN" in res["catatan"]


def test_terdaftar_di_dispatch_proyeksi_dan_gambar_inline():
    assert "buka_pengetahuan" in ai._DISPATCH
    assert "buka_pengetahuan" in ai._PROJECTIONS
    assert "buka_pengetahuan" in ai._TOOLS_GAMBAR_INLINE


def test_spec_hanya_ditawarkan_bila_store_berisi():
    """Spec dikirim tiap panggilan API — instalasi tanpa pengetahuan tak boleh
    membayar tokennya."""
    assert "buka_pengetahuan" not in {s["function"]["name"]
                                      for s in ai._tool_specs(ADMIN)}
    _isi([_chunk()])
    assert "buka_pengetahuan" in {s["function"]["name"]
                                  for s in ai._tool_specs(ADMIN)}
