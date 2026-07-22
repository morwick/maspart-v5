"""Pipeline indexing: chunking, ekstraksi TXT/CSV/XLSX/gambar, job, pengayaan.

Job dijalankan SINKRON lewat `proses()` supaya test deterministik — antrian
thread-nya sendiri diuji terpisah (test_job_serial).
"""
import io
import json

import pytest

from app.services import knowledge_util, pengetahuan
from app.services import pengetahuan_extract as ext
from app.services import pengetahuan_index as idx


@pytest.fixture(autouse=True)
def _tmp_store(tmp_path, monkeypatch):
    monkeypatch.setattr(pengetahuan, "_dir", lambda: tmp_path)
    knowledge_util._LOAD_CACHE.clear()
    yield tmp_path
    knowledge_util._LOAD_CACHE.clear()


class _Settings:
    """Pengganti Settings — `ai_configured` di aslinya property read-only."""

    def __init__(self, ai=False):
        self.ai_configured = ai
        self.deepseek_base_url = "https://contoh.invalid"
        self.deepseek_api_key = "dummy"
        self.deepseek_model = "deepseek-chat"


@pytest.fixture(autouse=True)
def _tanpa_llm(monkeypatch):
    """Default: LLM tak dikonfigurasi → seluruh test memakai jalur fallback.
    Sekaligus memastikan test TIDAK PERNAH menyentuh API DeepSeek sungguhan."""
    monkeypatch.setattr(idx, "get_settings", lambda: _Settings(False))


def _taruh_berkas(dok, nama, data, ekst):
    d = pengetahuan.berkas_dir()
    d.mkdir(parents=True, exist_ok=True)
    simpan = f"{dok['id']}_0{ekst}"
    (d / simpan).write_bytes(data)
    pengetahuan.update_dokumen(dok["id"], berkas=[
        {"nama": nama, "nama_simpan": simpan, "ukuran": len(data), "ext": ekst}])
    knowledge_util._LOAD_CACHE.clear()


# ── chunking ─────────────────────────────────────────────────────────
def test_teks_pendek_jadi_satu_chunk():
    assert ext.potong_teks("halo dunia") == ["halo dunia"]
    assert ext.potong_teks("   ") == []


def test_teks_panjang_dipecah_tanpa_memotong_kata():
    teks = " ".join(f"kata{i:04d}" for i in range(1200))
    potong = ext.potong_teks(teks)
    assert len(potong) > 1
    assert all(len(p) <= ext.MAX_CHUNK for p in potong)
    # tak ada potongan yang berakhir/berawal di tengah token
    for p in potong:
        assert not p.startswith("ata") and "kata" in p


def test_potong_menghormati_batas_paragraf():
    # panjang harus > MAX_CHUNK supaya benar-benar terpecah
    a, b = "A" * 1100, "B" * 1100
    potong = ext.potong_teks(f"{a}\n\n{b}")
    assert len(potong) > 1
    assert set(potong[0]) == {"A"}          # potongan pertama murni paragraf A


def test_overlap_menjaga_kalimat_terbelah():
    teks = ("x" * 1150) + " KALIMATPENTING " + ("y" * 1150)
    potong = ext.potong_teks(teks)
    assert any("KALIMATPENTING" in p for p in potong)


# ── ekstraksi ────────────────────────────────────────────────────────
def test_ekstrak_txt_utf8_dan_cp1252():
    assert ext.ekstrak("halo".encode("utf-8"), "a.txt")[0]["teks"] == "halo"
    assert ext.ekstrak("kaf\xe9".encode("cp1252"), "a.txt")[0]["teks"] == "kaf\xe9"


def test_ekstrak_csv_jadi_tabel():
    data = b"Part;Torsi\nbaut roda;600 Nm\nbaut as;400 Nm\n"
    bagian = ext.ekstrak(data, "a.csv")
    assert bagian[0]["tabel"][0] == ["Part", "Torsi"]
    assert ["baut roda", "600 Nm"] in bagian[0]["tabel"]


def test_ekstrak_xlsx_semua_sheet():
    from openpyxl import Workbook
    wb = Workbook()
    wb.active.title = "Torsi"
    wb.active.append(["Part", "Nm"])
    wb.active.append(["baut roda", 600])
    ws2 = wb.create_sheet("Oli")
    ws2.append(["Komponen", "Liter"])
    ws2.append(["mesin", 12])
    buf = io.BytesIO()
    wb.save(buf)
    bagian = ext.ekstrak(buf.getvalue(), "a.xlsx")
    subs = {b["sub"] for b in bagian}
    assert subs == {"Torsi", "Oli"}


def test_tabel_besar_dipecah_dengan_header_diulang():
    rows = [["Kol1", "Kol2"]] + [[f"a{i}", f"b{i}"] for i in range(70)]
    bagian = ext._tabel_jadi_bagian(rows, 0, "")
    assert len(bagian) == 3                       # 70 baris / 30
    assert all(b["tabel"][0] == ["Kol1", "Kol2"] for b in bagian)


def test_format_tak_didukung_ditolak():
    with pytest.raises(ValueError, match="belum didukung"):
        ext.ekstrak(b"x", "a.exe")


def test_csv_kosong_tidak_menghasilkan_bagian():
    # _read_csv sengaja permisif (cp1252 menerima hampir semua byte) — yang
    # perlu dijamin: baris kosong tidak menjadi chunk sampah.
    assert ext.ekstrak(b"", "a.csv") == []
    assert ext.ekstrak(b"\n\n;;\n", "a.csv") == []


def test_xlsx_rusak_melempar_pesan_indonesia():
    with pytest.raises(ValueError):
        ext.ekstrak(b"bukan zip sama sekali", "a.xlsx")


def test_zip_bomb_ditolak(monkeypatch):
    monkeypatch.setattr(ext, "MAX_ZIP_ENTRI", 1)
    import zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("a.txt", "x")
        z.writestr("b.txt", "x")
    with pytest.raises(ValueError, match="terlalu banyak"):
        ext._cek_zip(buf.getvalue())


# ── job ──────────────────────────────────────────────────────────────
def test_proses_teks_admin_jadi_chunk_tercari():
    dok = pengetahuan.add_dokumen("Prosedur Retur", teks_admin=
                                  "Retur barang diajukan maksimal 7 hari setelah terima.")
    idx.proses(dok["id"])
    knowledge_util._LOAD_CACHE.clear()
    d = pengetahuan.get_dokumen(dok["id"])
    assert d["status"] == "selesai" and d["jumlah_chunk"] == 1
    assert d["pengayaan"] == "fallback"
    assert d["progres"]["persen"] == 100
    hasil = pengetahuan.search("retur")
    assert hasil and hasil[0]["sumber"] == "teks-admin"


def test_proses_csv_dan_kata_kunci_fallback_terisi():
    dok = pengetahuan.add_dokumen("Tabel Torsi", teks_admin="", tabel_admin=None,
                                  berkas=[{"nama": "x", "nama_simpan": "y", "ext": ".csv"}])
    _taruh_berkas(dok, "torsi.csv", b"Part;Torsi\nbaut roda;600 Nm\n", ".csv")
    idx.proses(dok["id"])
    knowledge_util._LOAD_CACHE.clear()
    c = pengetahuan.chunks_dokumen(dok["id"])
    assert c and c[0]["tipe"] == "csv" and c[0]["sumber"] == "torsi.csv"
    assert c[0]["kata_kunci"]                       # fallback mengisi sesuatu
    assert pengetahuan.search("torsi")


def test_berkas_rusak_jadi_selesai_sebagian_bukan_crash():
    dok = pengetahuan.add_dokumen("Campur", teks_admin="ada isi teks yang benar")
    _taruh_berkas(dok, "rusak.xlsx", b"bukan zip", ".xlsx")
    idx.proses(dok["id"])
    d = pengetahuan.get_dokumen(dok["id"])
    assert d["status"] == "selesai_sebagian"
    assert "rusak.xlsx" in d["error"]
    assert d["jumlah_chunk"] >= 1                   # isi yang sehat tetap terindeks


def test_tanpa_isi_sama_sekali_status_gagal():
    dok = pengetahuan.add_dokumen("Kosong", teks_admin="x")
    pengetahuan.update_dokumen(dok["id"], teks_admin="")
    idx.proses(dok["id"])
    assert pengetahuan.get_dokumen(dok["id"])["status"] == "gagal"


def test_batas_chunk_per_dokumen_dilaporkan(monkeypatch):
    monkeypatch.setattr(idx, "MAX_CHUNK_DOKUMEN", 2)
    dok = pengetahuan.add_dokumen("Besar", teks_admin="x")
    rows = b"a;b\n" + b"\n".join(f"r{i};v{i}".encode() for i in range(200))
    _taruh_berkas(dok, "besar.csv", rows, ".csv")
    pengetahuan.update_dokumen(dok["id"], teks_admin="")
    idx.proses(dok["id"])
    d = pengetahuan.get_dokumen(dok["id"])
    assert d["status"] == "selesai_sebagian"
    assert "batas" in d["error"].lower()


def test_reindex_mempertahankan_kurasi_admin():
    """Tanpa ini, tiap re-index menghapus pekerjaan manual admin."""
    dok = pengetahuan.add_dokumen("Kebijakan", teks_admin="retur barang tujuh hari")
    idx.proses(dok["id"])
    knowledge_util._LOAD_CACHE.clear()
    c = pengetahuan.chunks_dokumen(dok["id"])[0]
    pengetahuan.update_chunk(c["id"], judul_id="Cara retur (kurasi admin)",
                             kata_kunci=["balikin barang"], dicari=True)
    knowledge_util._LOAD_CACHE.clear()

    idx.proses(dok["id"])            # re-index
    knowledge_util._LOAD_CACHE.clear()
    baru = pengetahuan.chunks_dokumen(dok["id"])[0]
    assert baru["judul_id"] == "Cara retur (kurasi admin)"
    assert baru["kata_kunci"] == ["balikin barang"]
    assert baru["kurasi"] is True
    assert pengetahuan.search("balikin barang")      # tetap bisa dicari


def test_kurasi_hilang_dilaporkan_ke_admin():
    dok = pengetahuan.add_dokumen("Kebijakan", teks_admin="retur barang tujuh hari")
    idx.proses(dok["id"])
    knowledge_util._LOAD_CACHE.clear()
    c = pengetahuan.chunks_dokumen(dok["id"])[0]
    pengetahuan.update_chunk(c["id"], judul_id="Kurasi lama")
    # isi diganti total → kunci kurasi tak lagi cocok
    pengetahuan.update_dokumen(dok["id"], teks_admin="isi yang sama sekali berbeda")
    knowledge_util._LOAD_CACHE.clear()
    idx.proses(dok["id"])
    d = pengetahuan.get_dokumen(dok["id"])
    assert d["status"] == "selesai_sebagian"
    assert "kurasi manual" in d["error"]


def test_pengayaan_tidak_menimpa_chunk_terkurasi(monkeypatch):
    _aktifkan_llm(monkeypatch)
    dipanggil = {"n": 0}

    def fake_post(url, **kw):
        dipanggil["n"] += 1
        return _Resp({"chunk": []})
    monkeypatch.setattr(idx.requests, "post", fake_post)
    chunks = [{"id": "aa#0001", "teks": "x", "kurasi": True, "judul_id": "Manusia"}]
    assert idx.perkaya(chunks) == "fallback"
    assert dipanggil["n"] == 0                    # LLM tak dipanggil sama sekali
    assert chunks[0]["judul_id"] == "Manusia"


def test_reindex_mengganti_bukan_menumpuk():
    dok = pengetahuan.add_dokumen("Ulang", teks_admin="retur barang tujuh hari")
    idx.proses(dok["id"])
    idx.proses(dok["id"])
    knowledge_util._LOAD_CACHE.clear()
    assert len(pengetahuan.chunks_dokumen(dok["id"])) == 1


def test_untuk_pembeli_diturunkan_ke_chunk():
    dok = pengetahuan.add_dokumen("Publik", teks_admin="ongkos kirim gratis",
                                  untuk_pembeli=True)
    idx.proses(dok["id"])
    knowledge_util._LOAD_CACHE.clear()
    assert all(c["untuk_pembeli"] for c in pengetahuan.chunks_dokumen(dok["id"]))


def test_gambar_disimpan_dan_tertaut(_tmp_store):
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (40, 30), (255, 0, 0)).save(buf, format="PNG")
    dok = pengetahuan.add_dokumen("Foto", teks_admin="x")
    _taruh_berkas(dok, "diagram.png", buf.getvalue(), ".png")
    pengetahuan.update_dokumen(dok["id"], teks_admin="")
    idx.proses(dok["id"])
    knowledge_util._LOAD_CACHE.clear()
    c = pengetahuan.chunks_dokumen(dok["id"])
    assert c[0]["gambar_ref"]
    f = c[0]["gambar_ref"][0]
    assert (_tmp_store / "media" / f).is_file()
    assert pengetahuan.image_bytes(f)              # terdaftar → boleh dilayani


def test_job_serial_lewat_antrian():
    """Dua dokumen di-antre → dua-duanya terproses oleh pekerja tunggal."""
    a = pengetahuan.add_dokumen("A", teks_admin="prosedur retur pertama")
    b = pengetahuan.add_dokumen("B", teks_admin="prosedur retur kedua")
    idx.antre(a["id"])
    idx.antre(b["id"])
    idx._ANTRIAN.join()
    knowledge_util._LOAD_CACHE.clear()
    assert pengetahuan.get_dokumen(a["id"])["status"] == "selesai"
    assert pengetahuan.get_dokumen(b["id"])["status"] == "selesai"


# ── multibahasa ──────────────────────────────────────────────────────
def test_deteksi_bahasa():
    assert idx.deteksi_bahasa("退货流程：货物必须在收到后七天内提出退货申请。") == "zh"
    assert idx.deteksi_bahasa("これはテストの文章です、返品の手順について。") == "ja"
    assert idx.deteksi_bahasa("Retur barang yang tidak sesuai dengan pesanan") == "id"
    assert idx.deteksi_bahasa("The goods that have been received from the seller") == "en"
    assert idx.deteksi_bahasa("hmm") == ""            # terlalu pendek → ragu


def test_kata_kunci_fallback_mandarin_menghasilkan_bigram():
    """Tanpa ini, dokumen Mandarin keluar dengan kata kunci KOSONG."""
    bagian = {"teks": "退货流程：货物必须在收到后七天内提出退货申请。", "tabel": []}
    kk = idx._kata_kunci_fallback(bagian, {"judul": "Kebijakan", "tag": []})
    assert kk, "kata kunci tidak boleh kosong untuk teks Mandarin"
    assert any(len(k) == 2 and "一" <= k[0] <= "鿿" for k in kk)


def test_dokumen_mandarin_terindeks_dan_bisa_dicari_dengan_kueri_mandarin():
    dok = pengetahuan.add_dokumen(
        "Dokumen Mandarin", pakai_ai=False,
        teks_admin="退货流程：货物必须在收到后七天内提出退货申请。零件号 WG9100。")
    idx.proses(dok["id"])
    knowledge_util._LOAD_CACHE.clear()
    pengetahuan._HAY_CACHE.update(mtime=None, rows=None)
    c = pengetahuan.chunks_dokumen(dok["id"])[0]
    assert c["bahasa"] == "zh" and c["skema"] == 2
    assert pengetahuan.search("退货")
    assert pengetahuan.search("WG9100")


def test_dokumen_asing_tanpa_ai_diberi_peringatan_ke_admin():
    dok = pengetahuan.add_dokumen(
        "Mandarin Tanpa AI", pakai_ai=False,
        teks_admin="退货流程：货物必须在收到后七天内提出退货申请。")
    idx.proses(dok["id"])
    d = pengetahuan.get_dokumen(dok["id"])
    assert d["status"] == "selesai_sebagian"
    assert "bukan Bahasa Indonesia" in d["error"]


def test_dokumen_indonesia_tidak_kena_peringatan_asing():
    dok = pengetahuan.add_dokumen(
        "Indonesia", pakai_ai=False,
        teks_admin="Retur barang yang tidak sesuai dengan pesanan wajib difoto.")
    idx.proses(dok["id"])
    d = pengetahuan.get_dokumen(dok["id"])
    assert d["status"] == "selesai"


# ── pengayaan LLM ────────────────────────────────────────────────────
def _aktifkan_llm(monkeypatch):
    monkeypatch.setattr(idx, "get_settings", lambda: _Settings(True))


def _muatan(kw):
    """Payload chunk dari pesan user — JSON diikuti perintah bahasa (teks)."""
    isi = kw["json"]["messages"][1]["content"]
    return json.loads(isi[: isi.rindex("}") + 1])


class _Resp:
    status_code = 200

    def __init__(self, payload):
        self._p = payload

    def json(self):
        return {"choices": [{"message": {"content": json.dumps(self._p)}}]}


def test_pengayaan_llm_terpakai(monkeypatch):
    _aktifkan_llm(monkeypatch)
    dok = pengetahuan.add_dokumen("Garansi", teks_admin="Klaim garansi maksimal 30 hari.")

    def fake_post(url, **kw):
        kirim = _muatan(kw)
        cid = kirim["chunk"][0]["id"]
        return _Resp({"chunk": [{"id": cid, "judul_id": "Syarat klaim garansi",
                                 "kata_kunci": ["garansi", "klaim"],
                                 "ringkasan": "Klaim maksimal 30 hari."}]})
    monkeypatch.setattr(idx.requests, "post", fake_post)
    idx.proses(dok["id"])
    knowledge_util._LOAD_CACHE.clear()
    c = pengetahuan.chunks_dokumen(dok["id"])[0]
    assert c["judul_id"] == "Syarat klaim garansi"
    assert c["kata_kunci"] == ["garansi", "klaim"]
    assert pengetahuan.get_dokumen(dok["id"])["pengayaan"] == "llm"


def test_llm_gagal_jatuh_ke_fallback_dan_tetap_selesai(monkeypatch):
    _aktifkan_llm(monkeypatch)

    def boom(*a, **kw):
        raise RuntimeError("timeout")
    monkeypatch.setattr(idx.requests, "post", boom)
    dok = pengetahuan.add_dokumen("Garansi", teks_admin="Klaim garansi maksimal 30 hari.")
    idx.proses(dok["id"])
    d = pengetahuan.get_dokumen(dok["id"])
    assert d["status"] == "selesai" and d["pengayaan"] == "fallback"
    assert pengetahuan.search("garansi")           # tetap bisa dicari


def test_llm_json_rusak_jatuh_ke_fallback(monkeypatch):
    _aktifkan_llm(monkeypatch)

    class Rusak:
        status_code = 200

        def json(self):
            return {"choices": [{"message": {"content": "{bukan json"}}]}
    monkeypatch.setattr(idx.requests, "post", lambda *a, **kw: Rusak())
    dok = pengetahuan.add_dokumen("G", teks_admin="Klaim garansi maksimal 30 hari.")
    idx.proses(dok["id"])
    assert pengetahuan.get_dokumen(dok["id"])["status"] == "selesai"


def test_kata_kunci_berangka_fiktif_dibuang():
    chunk = {"teks": "Klaim garansi maksimal 30 hari.", "tabel": []}
    upd = idx._validasi_pengayaan(
        {"judul_id": "Garansi", "kata_kunci": ["garansi", "WG9100", "30 hari"]}, chunk)
    assert "WG9100" not in upd["kata_kunci"]       # PN karangan ditolak
    assert "30 hari" in upd["kata_kunci"]          # angka yang MEMANG ada boleh
    assert "garansi" in upd["kata_kunci"]


def test_perintah_bahasa_ada_di_pesan_user(monkeypatch):
    """Instruksi bahasa WAJIB di ujung pesan user — di system prompt saja
    terbukti diabaikan DeepSeek saat payload-nya JSON Mandarin (produksi)."""
    _aktifkan_llm(monkeypatch)
    tangkap = {}

    def fake_post(url, **kw):
        tangkap["user"] = kw["json"]["messages"][1]["content"]
        return _Resp({"chunk": []})
    monkeypatch.setattr(idx.requests, "post", fake_post)
    idx._llm_batch([{"id": "x#0000", "teks": "isi"}])
    assert tangkap["user"].rstrip().endswith(idx._PERINTAH_USER.strip())
    idx._llm_batch([{"id": "x#0000", "teks": "isi"}], tegur=True)
    assert idx._PERINTAH_TEGUR.strip() in tangkap["user"]


def test_validasi_pengayaan_membuang_field_cjk():
    """Pagar bahasa per-field: pengayaan Mandarin tak boleh masuk indeks."""
    chunk = {"teks": "电控模块连接失败，点击启动测试。", "tabel": []}
    upd = idx._validasi_pengayaan(
        {"judul_id": "电控模块连接失败处理",
         "kata_kunci": ["ECU未连接", "启动测试", "EOL"],
         "ringkasan": "若电控模块连接失败，可点击启动测试。"}, chunk)
    assert "judul_id" not in upd
    assert "ringkasan" not in upd
    assert upd["kata_kunci"] == ["EOL"]            # yang Latin selamat


def test_pengayaan_mandarin_diulang_dengan_teguran(monkeypatch):
    """Putaran 1 ikut bahasa sumber → putaran 2 (tegur) hasil Indonesia dipakai."""
    _aktifkan_llm(monkeypatch)
    dok = pengetahuan.add_dokumen(
        "EOL", teks_admin="电控模块连接失败，点击启动测试即可重连。")
    panggilan = []

    def fake_post(url, **kw):
        kirim = _muatan(kw)
        cid = kirim["chunk"][0]["id"]
        sistem = kw["json"]["messages"][0]["content"]
        panggilan.append("tegur" if idx._PROMPT_TEGUR in sistem else "biasa")
        if len(panggilan) == 1:
            return _Resp({"chunk": [{"id": cid, "judul_id": "电控模块连接失败处理",
                                     "kata_kunci": ["ECU未连接"],
                                     "ringkasan": "点击启动测试。"}]})
        return _Resp({"chunk": [{"id": cid, "judul_id": "Modul kontrol gagal terhubung",
                                 "kata_kunci": ["ecu tidak terhubung", "mulai tes"],
                                 "ringkasan": "Klik mulai tes untuk menghubungkan ulang."}]})
    monkeypatch.setattr(idx.requests, "post", fake_post)
    idx.proses(dok["id"])
    knowledge_util._LOAD_CACHE.clear()
    c = pengetahuan.chunks_dokumen(dok["id"])[0]
    assert panggilan == ["biasa", "tegur"]
    assert c["judul_id"] == "Modul kontrol gagal terhubung"
    assert "ecu tidak terhubung" in c["kata_kunci"]
    d = pengetahuan.get_dokumen(dok["id"])
    assert d["status"] == "selesai" and d["pengayaan"] == "llm"


def test_pengayaan_tetap_asing_jatuh_ke_fallback_dan_dilaporkan(monkeypatch):
    """LLM keras kepala berbahasa Mandarin dua putaran → fallback + admin tahu."""
    _aktifkan_llm(monkeypatch)
    dok = pengetahuan.add_dokumen(
        "EOL", teks_admin="电控模块连接失败，点击启动测试即可重连。")

    def fake_post(url, **kw):
        kirim = _muatan(kw)
        cid = kirim["chunk"][0]["id"]
        return _Resp({"chunk": [{"id": cid, "judul_id": "电控模块连接失败处理",
                                 "kata_kunci": ["ECU未连接", "启动测试"],
                                 "ringkasan": "点击启动测试。"}]})
    monkeypatch.setattr(idx.requests, "post", fake_post)
    idx.proses(dok["id"])
    knowledge_util._LOAD_CACHE.clear()
    c = pengetahuan.chunks_dokumen(dok["id"])[0]
    # Jawaban LLM Mandarin TIDAK dipakai — yang tersimpan fallback deterministik
    # (baris pertama teks sumber), bukan judul karangan model.
    assert c["judul_id"] != "电控模块连接失败处理"
    assert "ECU未连接" not in c["kata_kunci"]
    d = pengetahuan.get_dokumen(dok["id"])
    assert d["status"] == "selesai_sebagian"
    assert "berbahasa asing" in d["error"]
    assert d["pengayaan"] == "fallback"


def test_pengayaan_indonesia_tidak_kena_putaran_ulang(monkeypatch):
    """Dokumen Indonesia normal: satu panggilan saja — tak bayar token teguran."""
    _aktifkan_llm(monkeypatch)
    dok = pengetahuan.add_dokumen("Garansi", teks_admin="Klaim garansi maksimal 30 hari.")
    dipanggil = {"n": 0}

    def fake_post(url, **kw):
        dipanggil["n"] += 1
        kirim = _muatan(kw)
        cid = kirim["chunk"][0]["id"]
        return _Resp({"chunk": [{"id": cid, "judul_id": "Syarat klaim garansi",
                                 "kata_kunci": ["garansi"],
                                 "ringkasan": "Klaim maksimal 30 hari."}]})
    monkeypatch.setattr(idx.requests, "post", fake_post)
    idx.proses(dok["id"])
    assert dipanggil["n"] == 1
    assert pengetahuan.get_dokumen(dok["id"])["status"] == "selesai"


def test_id_asing_dari_llm_diabaikan(monkeypatch):
    _aktifkan_llm(monkeypatch)
    monkeypatch.setattr(idx.requests, "post", lambda *a, **kw: _Resp(
        {"chunk": [{"id": "tidak#ada", "judul_id": "Ngawur"}]}))
    dok = pengetahuan.add_dokumen("G", teks_admin="Klaim garansi maksimal 30 hari.")
    idx.proses(dok["id"])
    knowledge_util._LOAD_CACHE.clear()
    c = pengetahuan.chunks_dokumen(dok["id"])[0]
    assert c["judul_id"] != "Ngawur"
    assert pengetahuan.get_dokumen(dok["id"])["pengayaan"] == "fallback"
