"""Tool cari_pengetahuan — bentuk hasil, invarian ekor, gating pembeli.

Invarian yang dikunci di sini:
  - `catatan` WAJIB key TERAKHIR (_cap_tool_content memotong bagian TENGAH;
    kalau catatan ada di kepala, instruksi anti-ngarang hilang senyap);
  - pengetahuan internal TIDAK PERNAH sampai ke role pembeli;
  - system prompt TIDAK berubah walau store diisi (prompt-cache DeepSeek).
"""
import pytest

from app.services import ai_assistant as ai
from app.services import knowledge_util, pengetahuan, sinonim


@pytest.fixture(autouse=True)
def _tmp_store(tmp_path, monkeypatch):
    monkeypatch.setattr(pengetahuan, "_dir", lambda: tmp_path)
    monkeypatch.setattr(sinonim, "entries", lambda: [])
    knowledge_util._LOAD_CACHE.clear()
    yield tmp_path
    knowledge_util._LOAD_CACHE.clear()


def _chunk(**kw):
    d = {
        "id": "aa#0001", "dok_id": "aa", "judul": "Prosedur Retur",
        "judul_id": "Cara mengajukan retur", "kata_kunci": ["retur", "pengembalian"],
        "ringkasan": "Retur diajukan maksimal 7 hari.", "teks": "Barang retur wajib utuh.",
        "tabel": [], "gambar_ref": [], "sumber": "kebijakan.pdf", "halaman": 3,
        "tipe": "pdf", "untuk_pembeli": False, "dicari": True, "kode": [],
    }
    d.update(kw)
    return d


def _isi(rows):
    pengetahuan._save_chunks(rows)
    knowledge_util._LOAD_CACHE.clear()


ADMIN = {"username": "admin", "role": "admin"}
PEMBELI = {"username": "budi", "role": "pembeli"}


def test_hasil_lengkap_dan_catatan_di_ekor():
    _isi([_chunk()])
    res = ai._t_cari_pengetahuan({"topik": "retur"}, ADMIN)
    assert res["found"] is True and res["jumlah"] == 1
    h = res["hasil"][0]
    assert h["judul"] == "Cara mengajukan retur"
    assert h["dokumen"] == "Prosedur Retur"
    assert h["sumber"] == "kebijakan.pdf" and h["halaman"] == 3
    # INVARIAN: catatan wajib terakhir
    assert list(res)[-1] == "catatan"


def test_topik_kosong_dan_store_kosong():
    assert "error" in ai._t_cari_pengetahuan({"topik": "  "}, ADMIN)
    assert "error" in ai._t_cari_pengetahuan({"topik": "retur"}, ADMIN)


def test_nihil_found_false_dengan_catatan_terakhir():
    _isi([_chunk()])
    res = ai._t_cari_pengetahuan({"topik": "zebra kutub utara"}, ADMIN)
    assert res["found"] is False and res["jumlah"] == 0
    assert list(res)[-1] == "catatan"
    assert "mengarang" in res["catatan"].lower()


def test_pembeli_tidak_melihat_pengetahuan_internal():
    _isi([_chunk(untuk_pembeli=False)])
    res = ai._t_cari_pengetahuan({"topik": "retur"}, PEMBELI)
    assert res["found"] is False


def test_pembeli_melihat_yang_dipublikasikan():
    _isi([_chunk(untuk_pembeli=True)])
    res = ai._t_cari_pengetahuan({"topik": "retur"}, PEMBELI)
    assert res["found"] is True and res["hasil"][0]["dokumen"] == "Prosedur Retur"


def test_lapis_kedua_menyaring_walau_search_bocor(monkeypatch):
    """Bila filter search_skor() gagal/di-bypass, handler tetap menahan record
    internal."""
    _isi([_chunk(untuk_pembeli=False)])
    monkeypatch.setattr(pengetahuan, "search_skor",
                        lambda *a, **k: [(60.0, _chunk(untuk_pembeli=False))])
    res = ai._t_cari_pengetahuan({"topik": "retur"}, PEMBELI)
    assert res["found"] is False


def test_isi_dipotong_1200_char():
    _isi([_chunk(teks="retur " + "x" * 4000)])
    res = ai._t_cari_pengetahuan({"topik": "retur"}, ADMIN)
    assert len(res["hasil"][0]["isi"]) == 1200


def test_hasil_pendukung_dipotong_lebih_pendek():
    """Hemat token: juara dapat kutipan penuh, pendukung KUAT potongan pendek.
    (V3: 500 → 400 — jendela kini terarah ke kecocokan, bukan kepala buta,
    jadi lebih pendek TANPA kehilangan bagian yang relevan.)"""
    _isi([
        _chunk(id="aa#0001", judul_id="Prosedur retur", teks="retur " + "x" * 4000),
        _chunk(id="aa#0002", judul_id="Prosedur retur lanjutan",
               teks="retur " + "y" * 4000),
    ])
    res = ai._t_cari_pengetahuan({"topik": "prosedur retur"}, ADMIN)
    assert len(res["hasil"]) == 2
    assert len(res["hasil"][0]["isi"]) == 1200
    assert len(res["hasil"][1]["isi"]) == 400


def test_dokumen_tak_diulang_saat_sama_dengan_judul():
    _isi([_chunk(judul="Prosedur Retur", judul_id="Prosedur Retur")])
    res = ai._t_cari_pengetahuan({"topik": "retur"}, ADMIN)
    assert "dokumen" not in res["hasil"][0]      # string kembar tak dibayar 2x


def test_dokumen_disertakan_saat_beda():
    _isi([_chunk(judul="Kebijakan Gudang", judul_id="Cara retur")])
    res = ai._t_cari_pengetahuan({"topik": "retur"}, ADMIN)
    assert res["hasil"][0]["dokumen"] == "Kebijakan Gudang"


def test_gambar_label_manusiawi_dan_tanpa_balon(_tmp_store):
    """UI merender '{pn} · {nama_figure}' — `balon` terisi akan mencetak
    'Balon …' yang menyesatkan untuk dokumen pengetahuan."""
    media = _tmp_store / "media"
    media.mkdir(parents=True, exist_ok=True)
    (media / "aa_000.png").write_bytes(b"PNG")
    _isi([_chunk(gambar_ref=["aa_000.png"], judul_id="Cara mengajukan retur",
                 sumber="kebijakan.pdf", halaman=3)])
    g = ai._t_cari_pengetahuan({"topik": "retur"}, ADMIN)["gambar"][0]
    assert g["pn"] == "Cara mengajukan retur"
    assert g["nama_figure"] == "kebijakan.pdf · hal 3"     # provenance, bukan judul ulang
    assert not g.get("balon")
    assert g["kategori"] == "Pengetahuan"


def test_caption_dipakai_sebagai_label_bila_ada(_tmp_store):
    media = _tmp_store / "media"
    media.mkdir(parents=True, exist_ok=True)
    (media / "aa_000.png").write_bytes(b"PNG")
    _isi([_chunk(gambar_ref=["aa_000.png"],
                 gambar_info=[{"file": "aa_000.png",
                               "caption": "Gambar 2: alur pengajuan retur",
                               "halaman": 3}])])
    g = ai._t_cari_pengetahuan({"topik": "retur"}, ADMIN)["gambar"][0]
    assert g["nama_figure"] == "Gambar 2: alur pengajuan retur"


def test_cap_gambar_tiga(_tmp_store):
    """Batas nyata 6/giliran diakumulasi lintas SEMUA tool — alat penemuan
    tidak boleh menelan jatah tool lain."""
    media = _tmp_store / "media"
    media.mkdir(parents=True, exist_ok=True)
    refs = []
    for i in range(9):
        f = f"aa_{i:03d}.png"
        (media / f).write_bytes(b"PNG")
        refs.append(f)
    _isi([_chunk(id=f"aa#{i:04d}", judul_id=f"Prosedur retur bagian {i}",
                 gambar_ref=refs[i * 3:i * 3 + 3]) for i in range(3)])
    res = ai._t_cari_pengetahuan({"topik": "prosedur retur"}, ADMIN)
    assert len(res["gambar"]) <= 3


def test_image_id_dibuang_dari_salinan_model(_tmp_store):
    """image_id opaque — gambar inline dibangun dari side-state, bukan teks."""
    media = _tmp_store / "media"
    media.mkdir(parents=True, exist_ok=True)
    (media / "aa_000.png").write_bytes(b"PNG")
    _isi([_chunk(gambar_ref=["aa_000.png"])])
    res = ai._t_cari_pengetahuan({"topik": "retur"}, ADMIN)
    assert res["gambar"][0]["image_id"]                 # hasil UTUH tetap punya
    dump = ai._dump_tool(res, "cari_pengetahuan")
    assert "image_id" not in dump                       # salinan model tidak
    assert dump.rstrip("}").endswith('"')               # catatan tetap di ekor


def test_instruksi_terjemah_hanya_saat_ada_isi_asing():
    """Dokumen Indonesia (mayoritas) tak boleh membayar token untuk instruksi
    terjemah yang tak relevan."""
    _isi([_chunk(bahasa="")])
    res = ai._t_cari_pengetahuan({"topik": "retur"}, ADMIN)
    assert "TERJEMAHKAN" not in res["catatan"]
    assert "bahasa" not in res["hasil"][0]

    _isi([_chunk(bahasa="zh", teks="退货流程")])
    res = ai._t_cari_pengetahuan({"topik": "retur"}, ADMIN)
    assert "TERJEMAHKAN" in res["catatan"]
    assert res["hasil"][0]["bahasa"] == "zh"
    assert list(res)[-1] == "catatan"


def test_tabel_terpotong_diberitahukan_ke_model():
    """V3: angka 'ditampilkan' kini JUJUR (dulu hardcode 8 walau cuma 1 baris
    yang benar-benar terkirim)."""
    _isi([_chunk(tabel=[["a", "b"], ["1", "2"]], baris_total=40)])
    res = ai._t_cari_pengetahuan({"topik": "retur"}, ADMIN)
    assert res["hasil"][0]["tabel_dipotong"] == "1 dari 40 baris"
    assert "buka_pengetahuan" in res["catatan"]


def test_breadcrumb_disertakan_bila_ada():
    _isi([_chunk(jalur=["3 Retur", "3.2 Syarat"])])
    res = ai._t_cari_pengetahuan({"topik": "retur"}, ADMIN)
    assert res["hasil"][0]["bagian_dari"] == "3 Retur › 3.2 Syarat"


def test_peringatan_tabel_pdf_rekonstruksi():
    _isi([_chunk(tabel=[["a", "b"]], tipe="pdf")])
    res = ai._t_cari_pengetahuan({"topik": "retur"}, ADMIN)
    assert "REKONSTRUKSI" in res["catatan"]


def test_tabel_excel_tanpa_peringatan_rekonstruksi():
    _isi([_chunk(tabel=[["a", "b"]], tipe="excel")])
    res = ai._t_cari_pengetahuan({"topik": "retur"}, ADMIN)
    assert "REKONSTRUKSI" not in res["catatan"]


def test_gambar_dibatasi_8_dan_catatan_tetap_terakhir(_tmp_store):
    media = _tmp_store / "media"
    media.mkdir(parents=True, exist_ok=True)
    refs = []
    for i in range(9):
        f = f"aa_{i:03d}.png"
        (media / f).write_bytes(b"PNG")
        refs.append(f)
    _isi([_chunk(id=f"aa#{i:04d}", gambar_ref=refs[i * 3:i * 3 + 3]) for i in range(3)])
    res = ai._t_cari_pengetahuan({"topik": "retur"}, ADMIN)
    assert len(res["gambar"]) <= 8
    assert list(res)[-1] == "catatan"


def test_terdaftar_di_dispatch_dan_spec():
    assert "cari_pengetahuan" in ai._DISPATCH
    names = {s["function"]["name"] for s in ai._tool_specs(ADMIN)}
    assert "cari_pengetahuan" in names
    # pembeli tetap boleh memanggil (isinya yang disaring, bukan tool-nya)
    assert "cari_pengetahuan" in ai._allowed_tool_names(PEMBELI)


def test_prompt_tidak_berubah_walau_store_diisi():
    """Prompt-cache DeepSeek hanya kena bila prefix identik byte-per-byte."""
    sebelum = ai._system_prompt(ADMIN)
    _isi([_chunk(), _chunk(id="aa#0002", judul_id="Klaim garansi")])
    assert ai._system_prompt(ADMIN) == sebelum


# ── penyajian bertingkat sadar-skor (V3) ─────────────────────────────
def _juara_dan_pendukung_lemah():
    """Juara kurasi (skor ~65) + 2 pendukung frasa-di-teks (~23 ≈ 0.35×) —
    rasio di bawah 0.45 → tier lemah."""
    return [
        _chunk(id="aa#0001", judul_id="Prosedur retur barang",
               teks="Langkah retur: " + "a" * 2000),
        _chunk(id="bb#0001", dok_id="bb", judul="Dok B", judul_id="",
               teks="Bab lain menyinggung prosedur retur barang sekilas. " + "b" * 2000),
        _chunk(id="cc#0001", dok_id="cc", judul="Dok C", judul_id="",
               teks="Lampiran juga membahas prosedur retur barang singkat. " + "c" * 2000),
    ]


def test_pendukung_lemah_tanpa_isi_tapi_tetap_tertelusur():
    _isi(_juara_dan_pendukung_lemah())
    res = ai._t_cari_pengetahuan({"topik": "prosedur retur barang"}, ADMIN)
    assert len(res["hasil"]) == 3
    assert "isi" in res["hasil"][0]                    # juara utuh
    for h in res["hasil"][1:]:
        assert "isi" not in h                          # lemah: tanpa isi
        assert h["judul"] and h["sumber"]              # tapi tetap tertelusur
    assert "buka_pengetahuan" in res["catatan"]
    assert list(res)[-1] == "catatan"


def test_payload_lemah_jauh_lebih_hemat():
    """Anggaran token: 2 pendukung lemah TANPA isi — payload harus jauh di
    bawah era V2 (juara 1200 + 2×500 + ringkasan kembar ≈ >3400 char)."""
    _isi(_juara_dan_pendukung_lemah())
    res = ai._t_cari_pengetahuan({"topik": "prosedur retur barang"}, ADMIN)
    assert len(ai._dump_tool(res, "cari_pengetahuan")) < 3000


def test_sinyal_kecocokan_lemah_di_catatan():
    """Skor juara < ambang (hanya kata level-teks) → model diberi tahu agar
    tidak memaksakan jawaban dari bahan lemah."""
    _isi([_chunk(teks="dokumen ini menyebut retur sekali saja tanpa detail")])
    res = ai._t_cari_pengetahuan({"topik": "retur bengkel"}, ADMIN)
    assert "LEMAH" in res["catatan"]
    assert list(res)[-1] == "catatan"


def test_kecocokan_kuat_tanpa_sinyal_lemah():
    _isi([_chunk()])                                   # judul_id kurasi cocok
    res = ai._t_cari_pengetahuan({"topik": "retur"}, ADMIN)
    assert "LEMAH" not in res["catatan"]


def test_isi_terarah_ke_kecocokan_bukan_kepala():
    """Kalimat penjawab di ekor chunk panjang — V2 memotongnya senyap."""
    teks = ("paragraf pembuka tanpa jawaban. " * 70
            + "Denda keterlambatan retur adalah dua persen per hari.")
    _isi([_chunk(judul_id="Ketentuan denda retur", teks=teks)])
    res = ai._t_cari_pengetahuan({"topik": "denda retur"}, ADMIN)
    assert "Denda keterlambatan" in res["hasil"][0]["isi"]


def test_ringkasan_kembar_dengan_isi_dibuang():
    """Ringkasan fallback = kepala teks; bila isi juga mulai dari kepala,
    kembar itu tak dibayar dua kali."""
    teks = "Barang retur wajib utuh dan disertai foto kondisi. " + "x" * 2000
    _isi([_chunk(teks=teks, ringkasan=teks[:200])])
    res = ai._t_cari_pengetahuan({"topik": "retur"}, ADMIN)
    assert "ringkasan" not in res["hasil"][0]

    _isi([_chunk(teks=teks, ringkasan="Aturan kondisi barang saat retur.")])
    res = ai._t_cari_pengetahuan({"topik": "retur"}, ADMIN)
    assert res["hasil"][0]["ringkasan"] == "Aturan kondisi barang saat retur."


def test_baris_tabel_cocok_didahulukan_dan_dicatat():
    baris = [["Kondisi", "Denda"]] + [[f"kasus {i}", str(i)] for i in range(28)]
    baris[20] = ["terlambat retur", "2% per hari"]
    _isi([_chunk(judul_id="Tabel denda retur", tabel=baris, baris_total=28)])
    res = ai._t_cari_pengetahuan({"topik": "terlambat retur"}, ADMIN)
    t = res["hasil"][0]["tabel"]
    assert any("terlambat" in " ".join(b) for b in t[1:])
    assert "DIDAHULUKAN" in res["catatan"]
    assert list(res)[-1] == "catatan"


def test_kode_cocok_kueri_didahulukan_dan_dicap():
    kode = [f"AZ15{i:02d}" for i in range(9)] + ["WG9725190102"]
    _isi([_chunk(teks="daftar kode retur", kode=kode)])
    res = ai._t_cari_pengetahuan({"topik": "retur WG9725190102"}, ADMIN)
    k = res["hasil"][0]["kode"]
    assert k[0] == "WG9725190102"                      # yang ditanya, terdepan
    assert len(k) <= 6
