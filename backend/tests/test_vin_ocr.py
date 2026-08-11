"""Baca NOMOR RANGKA dari foto (services/vin_ocr.py).

Semua test OFFLINE: mesin OCR & populasi di-stub, kecuali dua test regresi foto
NYATA yang otomatis di-skip bila paket `rapidocr_onnxruntime` belum terpasang
(mis. laptop dev tanpa dependensi penuh).

⚠️ Test ber-stub memakai `isolasi=False`: produksi menjalankan OCR di PROSES
ANAK (hemat ±150 MB RSS di container yang sudah 94% penuh), dan stub monkeypatch
jelas tak ikut menyeberang ke proses lain.

⚠️ Yang dijaga di sini bukan "OCR-nya pintar", melainkan LAPISAN KEDUA: hasil
baca yang meleset satu huruf tetap mendarat di unit yang benar, dan yang
ambigu DITOLAK jadi keyakinan rendah — sebab satu huruf salah = unit salah,
dan itu menjalar ke seluruh jawaban (BOM, harga, klaim garansi).
"""
import io

import pytest

from app.services import vin_ocr

# Unit armada nyata (populasi) — sengaja ambil dua yang BERTETANGGA RAPAT
# (satu pengiriman, ekor beda 1–2 digit) supaya uji ambigu berarti.
VIN_A = "LZZ1BLMJ4TJ465057"
VIN_B = "LZZ1BLMJ5TJ465052"
VIN_C = "LZZ1BLMJ4SJ476834"          # unit di foto NAMEPLATE
VIN_D = "LZZXMVWL9PS000434"          # unit di foto PLAT ETSA (label Mandarin)
VIN_E = "LZZ5BXVH2RT113872"          # unit di foto PLAT DUA PANEL
ARMADA = [
    (VIN_C, {"model": "ZZ1257V584JF1", "jenis": "HOWO-NX 6X4", "tahun": "2026",
             "customer": "PT UJI"}),
    (VIN_D, {"model": "TAZ5466TYT", "jenis": "SITRAK 6X6", "tahun": "2023",
             "customer": "PT UJI"}),
    (VIN_E, {"model": "ZZ1312V5967F1", "jenis": "HOWO", "tahun": "2024",
             "customer": "PT UJI"}),
    (VIN_A, {"model": "ZZ1257V584JF1", "jenis": "HOWO-NX 6X4", "tahun": "2026",
             "customer": "PT UJI"}),
    (VIN_B, {"model": "ZZ1257V584JF1", "jenis": "HOWO-NX 6X4", "tahun": "2026",
             "customer": "PT UJI"}),
    ("LZZPBXSF5NJ248278", {"model": "ZZ1315N4666E1", "jenis": "HOWO NX 8 X 4",
                           "tahun": "2022", "customer": "PT LAIN"}),
]


@pytest.fixture
def armada(monkeypatch):
    monkeypatch.setattr(vin_ocr, "armada", lambda: ARMADA)
    return ARMADA


# ── normalisasi & bentuk ──────────────────────────────────────────────
def test_bersih_buang_bintang_dan_spasi():
    """Nomor rangka dipahat diapit bintang (★) — OCR ikut membacanya."""
    assert vin_ocr.bersih("★LZZ1BLMJ4 TJ465057★") == "LZZ1BLMJ4TJ465057"
    assert vin_ocr.bersih("lzz-1blmj4tj465057") == "LZZ1BLMJ4TJ465057"


def test_check_digit_sesuai_standar():
    """Digit ke-9 VIN nyata cocok dengan hitungan ISO 3779/GB 16735 (terbukti di
    1.332 dari 1.335 unit armada)."""
    assert vin_ocr.check_digit(VIN_A) == VIN_A[8]
    assert vin_ocr.cd_cocok(VIN_A)
    assert not vin_ocr.cd_cocok(VIN_A[:8] + "0" + VIN_A[9:])


def test_bentuk_vin_menolak_teks_biasa():
    """Regresi salah-terima nyata: diagram wiring SCR terbaca 'CATALYST
    TEMPERATU' — 17 huruf tanpa I/O/Q, jadi HANYA aturan bentuk ekor
    (2 huruf + 6 angka) yang menahannya jadi 'nomor rangka'."""
    assert not vin_ocr.bentuk_vin_wajar("CATALYSTTEMPERATU")
    assert not vin_ocr.bentuk_vin_wajar("LZZ1BLMJ4TJ46505")      # 16 char
    assert not vin_ocr.bentuk_vin_wajar("LZZ1BLMJ4TJ4650I7")     # huruf I terlarang
    assert vin_ocr.bentuk_vin_wajar(VIN_A)


def test_frame_dari_vin():
    assert vin_ocr.frame_dari(VIN_A) == "TJ465057"
    assert vin_ocr.frame_dari("tj465057") == "TJ465057"


# ── snap ke armada ────────────────────────────────────────────────────
def test_satu_huruf_salah_tetap_mendarat_di_unit_benar(armada):
    """Bacaan nyata RapidOCR atas foto pemilik: 'L7Z1BLMJ4TJ465057' (Z→7)."""
    hasil = vin_ocr._snap({"L7Z1BLMJ4TJ465057": 1}, armada)
    assert hasil and hasil["rangka"] == VIN_A
    assert hasil["keyakinan"] == "pasti"
    assert hasil["unit"]["jenis"] == "HOWO-NX 6X4"


def test_dua_huruf_salah_masih_tertolong(armada):
    hasil = vin_ocr._snap({"L7Z1BLM14TJ465057": 1}, armada)
    assert hasil and hasil["rangka"] == VIN_A


def test_bacaan_ambigu_ditolak(monkeypatch):
    """Bacaan yang sama dekatnya ke DUA unit tak boleh dipilih diam-diam —
    lebih baik keyakinan rendah & user mengoreksi daripada unit yang salah.

    Kasusnya nyata: dua unit satu pengiriman beda satu digit ekor (…57 vs …52),
    sementara 7/2/Z justru satu kelas kebingungan OCR. Bacaan berakhir 'Z' sama
    dekatnya ke keduanya."""
    kembar = [(VIN_A, {"jenis": "A"}), ("LZZ1BLMJ4TJ465052", {"jenis": "B"})]
    assert vin_ocr._snap({"LZZ1BLMJ4TJ46505Z": 1}, kembar) is None


def test_string_asing_tidak_nyangkut_ke_unit(armada):
    assert vin_ocr._snap({"CATALYSTTEMPERATU": 1}, armada) is None
    assert vin_ocr._snap({"WG9725550198": 1}, armada) is None      # PN, bukan VIN


def test_frame_8_char_harus_persis(armada):
    """Frame 8 char = bukti tipis (ekor satu pengiriman beda 1 digit) → hanya
    diterima bila praktis persis."""
    assert vin_ocr._snap({"TJ465057": 1}, armada)["rangka"] == VIN_A
    assert vin_ocr._snap({"TJ465099": 1}, armada) is None


# ── NAMEPLATE pabrik (plat CNHTC penuh teks) ──────────────────────────
def test_label_chassis_diambil_walau_menempel():
    """OCR kadang menyatukan label & nilainya jadi satu kotak."""
    assert _pnp(["CHASSISNOLZZ1BLMJ4SJ476834"]) == ["LZZ1BLMJ4SJ476834"]


def test_label_chassis_diambil_dari_kotak_berikutnya():
    """…dan kadang memisah keduanya (persis foto nameplate pemilik)."""
    baris = ["MODEL", "ZZ1257V584JF1", "CHASSISNO", "LZZ1BLMJ4SJ476834",
             "ENGINEOUTPUT280"]
    assert "LZZ1BLMJ4SJ476834" in _pnp(baris)


def _pnp(baris):
    return vin_ocr._kandidat_label(baris)


def test_teks_plat_lain_tak_ikut_jadi_kandidat():
    """Angka lain di plat (GCW 33000, MADE YEAR 2026-01, RAW 26000) tak boleh
    diadu ke armada — itu yang dulu membuat 137 kandidat & 21 detik."""
    kand = {"GCWGVW33000KG26000": 1, "CHINANATIONALHEAV": 1,
            "MADEYEAR202601RAW": 1, "LZZ1BLMJ4SJ476834": 4}
    layak = vin_ocr._urut_kandidat(kand)
    assert layak[0] == "LZZ1BLMJ4SJ476834"          # berlabel → diadu duluan
    assert "CHINANATIONALHEAV" not in layak         # tanpa angka → tak perlu diadu


def test_nomor_utuh_tak_terdesak_potongan_sampah(armada):
    """Regresi plat dua panel: nomor rangka terbaca UTUH (muncul 2×) sementara
    sambungan antar-kotak muncul lebih sering. Urutan lama (jumlah kemunculan
    dulu) menendangnya keluar dari 16 besar, jadi unitnya tak pernah dikenali."""
    kand = {VIN_E: 2}
    for i in range(20):                          # sampah yang lebih sering muncul
        kand[f"LZ5BYV96718MADB{i:02d}"] = 5
    layak = vin_ocr._urut_kandidat(kand)
    assert VIN_E in layak
    assert vin_ocr._snap(kand, ARMADA)["rangka"] == VIN_E


def test_bacaan_penuh_huruf_O_tetap_diadu():
    """Plat beretsa membuat angka 0 terbaca huruf O. Ejaan dibetulkan di tingkat
    KANDIDAT (I/O/Q tak pernah ada di VIN), kalau tidak potongan itu gugur di
    `_cukup_angka` sebelum sempat dibandingkan."""
    kand, berlabel = {}, set()
    vin_ocr._kumpulkan(["LZZXMVWL9PSOOOZBA"], kand, berlabel)
    assert "LZZXMVWL9PS000ZBA" in kand
    assert vin_ocr._cukup_angka("LZZXMVWL9PS000ZBA")
    assert not vin_ocr._cukup_angka("CHINANATIONALHEAV")


def test_indeks_armada_mempersempit_pembanding():
    """Indeks pasangan angka ekor: yang diadu tinggal sebagian kecil armada,
    dan unit yang benar WAJIB tetap masuk."""
    rows = ARMADA
    peta = vin_ocr._indeks(rows)
    kena = vin_ocr._baris_kandidat("L7Z1BLMJ4TJ465057", rows, peta)
    i_a = next(i for i, (v, _) in enumerate(rows) if v == VIN_A)
    assert i_a in kena                   # unit yang benar WAJIB tetap diadu
    assert len(kena) <= len(rows)


# ── unit di luar populasi ─────────────────────────────────────────────
def _vin_baru(dasar: str = "LZZ5ELSD0RT109988") -> str:
    """VIN yang TIDAK ada di armada uji, dengan check digit yang benar."""
    return dasar[:8] + vin_ocr.check_digit(dasar) + dasar[9:]


def test_vin_luar_armada_lolos_lewat_check_digit():
    baru = _vin_baru()
    hasil = vin_ocr._tanpa_armada({baru: 1})
    assert hasil["rangka"] == baru and hasil["keyakinan"] == "tinggi"
    assert hasil["unit"] is None


def test_satu_huruf_salah_dibetulkan_check_digit():
    """Tanpa armada, check digit masih bisa membetulkan satu huruf — tapi hanya
    bila pembetulannya TUNGGAL (kalau ada beberapa, jadi keyakinan rendah)."""
    baru = _vin_baru()
    rusak = baru.replace("0", "O", 1) if "0" in baru else baru
    hasil = vin_ocr._tanpa_armada({rusak: 1})
    assert hasil["keyakinan"] in ("tinggi", "rendah")
    if hasil["keyakinan"] == "tinggi":
        assert vin_ocr.cd_cocok(hasil["rangka"])


def test_check_digit_sendiri_tak_boleh_ditukar():
    """Membetulkan digit pemeriksa selalu 'berhasil' → tak membuktikan apa pun."""
    baru = _vin_baru()
    salah_cd = baru[:8] + ("1" if baru[8] != "1" else "2") + baru[9:]
    for kand in vin_ocr._perbaiki_cd(salah_cd):
        assert kand[8] == salah_cd[8]


def test_teks_asing_tidak_jadi_nomor_rangka():
    assert vin_ocr._tanpa_armada({"CATALYSTTEMPERATU": 1}) is None
    assert vin_ocr._tanpa_armada({}) is None


def test_check_digit_menang_atas_kandidat_berbentuk_sah():
    """Regresi nyata: 'MANUFACTURE DATE 2024' terbaca 'FACTUREDATB900020' —
    ekornya pun 2 huruf + 6 angka. Versi lama pulang di kandidat PERTAMA yang
    bentuknya sah, jadi satu potongan sampah sudah cukup untuk mengalahkan nomor
    rangka yang check digit-nya benar."""
    sampah = "LACTUREDATB900020"                  # bentuk sah, check digit salah
    assert vin_ocr.bentuk_vin_wajar(sampah) and not vin_ocr.cd_cocok(sampah)
    hasil = vin_ocr._tanpa_armada({sampah: 5, VIN_E: 1})
    assert hasil["rangka"] == VIN_E and hasil["keyakinan"] == "tinggi"


def test_kalimat_plat_yang_lolos_check_digit_tetap_ditolak():
    """Foto terbalik: 'Permissible axle load,axle 4 16000' terbaca
    'XLEL0ADAXLE416000' — 17 char, ekor 2 huruf + 6 angka, DAN check digit-nya
    cocok (peluang 1/11). Yang menahannya tinggal awalan 'L' (kode negara
    Tiongkok), sebab tanpa itu ia disodorkan dengan keyakinan TINGGI."""
    palsu = "XLEL0ADAXLE416000"
    assert vin_ocr.cd_cocok(palsu)               # check digit saja tak cukup
    assert not vin_ocr.bentuk_vin_wajar(palsu)
    assert vin_ocr._cd_pasti({palsu: 9}) is None


def test_angka_ekor_terbaca_huruf_dibetulkan_check_digit():
    """'…PS0004B4': huruf B berdiri di posisi yang standar VIN wajibkan ANGKA,
    jadi letak kesalahannya pasti; check digit lalu menunjuk satu-satunya angka
    yang mungkin. Hanya untuk kandidat BERLABEL — teks acak lolos 1/11."""
    rusak = "LZZXMVWL9PS0004B4"
    assert vin_ocr._perbaiki_bentuk(rusak) == [VIN_D]
    assert vin_ocr._tanpa_armada({rusak: 3}) is None            # tanpa label: tidak
    hasil = vin_ocr._tanpa_armada({rusak: 3}, {rusak})          # berlabel: ya
    assert hasil["rangka"] == VIN_D and hasil["keyakinan"] == "tinggi"


def test_dua_angka_ekor_rusak_tak_ditebak():
    """Dua posisi rusak = 100 kemungkinan, ±9 di antaranya lolos check digit →
    bukan bukti. Lebih baik mengaku tak tahu."""
    assert vin_ocr._perbaiki_bentuk("LZZXMVWL9PS0004BA") == []


def test_berlabel_didahulukan_saat_check_digit_sama_sama_cocok():
    palsu = "LZZ5ELSD0RT109988"
    palsu = palsu[:8] + vin_ocr.check_digit(palsu) + palsu[9:]
    assert vin_ocr.cd_cocok(palsu) and vin_ocr.cd_cocok(VIN_D)
    hasil = vin_ocr._cd_pasti({palsu: 9, VIN_D: 1}, {VIN_D})
    assert hasil["rangka"] == VIN_D


# ── jalur di samping label (plat yang nilainya tak terdeteksi) ────────
def _plat(tegak: bool = False):
    """Plat tiruan: sebaris 'huruf' gelap di atas latar terang, dengan ruang
    kosong di kiri untuk labelnya. `tegak=True` = fotonya rebah 90°."""
    import numpy as np
    im = np.full((200, 600, 3), 215, dtype=np.uint8)
    for i in range(17):                          # 17 'karakter' 10×16 px
        x = 200 + i * 16
        im[92:108, x:x + 10] = 25
    return np.rot90(im).copy() if tegak else im


def test_jalur_di_kanan_label_dibaca_ulang(monkeypatch):
    """Label mendatar → nilainya di kanan label, pada ketinggian yang sama."""
    dipanggil = []
    monkeypatch.setattr(vin_ocr, "_baca_jalur",
                        lambda g, ringkas=False: dipanggil.append(g.shape) or [])
    vin_ocr._jalur_label(_plat(), [(20.0, 88.0, 120.0, 112.0, "CHASSIS NO")])
    assert dipanggil, "jalur di samping label tidak dibaca ulang"


def test_jalur_foto_rebah_diputar_jadi_mendatar(monkeypatch):
    """Foto rebah 90°: kotak label jadi TEGAK dan nilainya berdiri di atas/bawah,
    bukan di samping. Kolomnya harus diputar dulu — pengenal hanya bisa membaca
    baris mendatar, dan memutar SELURUH foto jauh lebih mahal."""
    dipanggil = []
    monkeypatch.setattr(vin_ocr, "_baca_jalur",
                        lambda g, ringkas=False: dipanggil.append(g.shape) or [])
    # plat rebah: label yang tadinya (20,88)-(120,112) ikut berputar
    vin_ocr._jalur_label(_plat(tegak=True), [(88.0, 480.0, 112.0, 580.0, "VIN")])
    assert dipanggil, "kolom di atas/bawah label tidak dibaca"
    for tinggi, lebar in dipanggil:
        assert lebar > tinggi, "jalur harus sudah mendatar saat sampai ke pengenal"


def test_label_yang_nilainya_menempel_tak_dibaca_ulang(monkeypatch):
    """Kalau nilainya sudah ikut terbaca di kotak label, tak ada yang perlu
    diselamatkan — jangan buang waktu."""
    dipanggil = []
    monkeypatch.setattr(vin_ocr, "_baca_jalur",
                        lambda g, ringkas=False: dipanggil.append(g.shape) or [])
    vin_ocr._jalur_label(_plat(), [(20.0, 88.0, 400.0, 112.0,
                                    f"CHASSIS NO {VIN_A}")])
    assert not dipanggil


# ── alur penuh (mesin OCR di-stub) ────────────────────────────────────
def _foto_kosong() -> bytes:
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (64, 48), (128, 128, 128)).save(buf, format="JPEG")
    return buf.getvalue()


@pytest.fixture
def ocr_palsu(monkeypatch):
    """Ganti mesin OCR-nya saja; pra-proses & pemilihan kandidat tetap asli.

    Yang di-stub `_kotak_ocr` (teks + LETAK kotak) karena letak itulah yang
    dipakai `_jalur_label` untuk menemukan jalur nilai di samping label."""
    kotak: dict = {"baris": []}

    def _set(*baris):
        kotak["baris"] = list(baris)

    monkeypatch.setattr(vin_ocr, "_kotak_ocr",
                        lambda im: vin_ocr.kotak_datar(*kotak["baris"]))
    return _set


def test_alur_penuh_foto_jadi_nomor_rangka(armada, ocr_palsu):
    ocr_palsu("★L7Z1BLMJ4TJ465057")
    hasil = vin_ocr.baca_rangka(_foto_kosong(), isolasi=False)
    assert hasil["ok"] and hasil["rangka"] == VIN_A
    assert hasil["frame"] == "TJ465057"
    assert hasil["keyakinan"] == "pasti"
    assert "HOWO-NX 6X4" in hasil["pesan"]
    assert hasil["teks_terbaca"] == ["★L7Z1BLMJ4TJ465057"]


def test_alur_penuh_nomor_terpecah_dua_kotak(armada, ocr_palsu):
    """Pahatan renggang / ada bintang di tengah → OCR memecah jadi 2 kotak."""
    ocr_palsu("LZZ1BLMJ4", "TJ465057")
    hasil = vin_ocr.baca_rangka(_foto_kosong(), isolasi=False)
    assert hasil["rangka"] == VIN_A


def test_orientasi_kedua_dicoba_saat_tegak_gagal(armada, monkeypatch):
    """Foto plat sering diambil sambil berdiri di samping unit → rebah 90°. Bila
    orientasi tegak tak menghasilkan apa pun, fotonya diputar dan dicoba lagi.
    ⚠️ Hanya saat gagal: foto normal tak boleh ikut membayar ongkosnya."""
    def _kotak(im):
        tinggi, lebar = im.shape[:2]
        return vin_ocr.kotak_datar(VIN_A) if tinggi > lebar else []

    monkeypatch.setattr(vin_ocr, "_kotak_ocr", _kotak)
    hasil = vin_ocr.baca_rangka(_foto_kosong(), isolasi=False)   # 64×48 (mendatar)
    assert hasil["rangka"] == VIN_A and hasil["keyakinan"] == "pasti"


def test_orientasi_kedua_tak_dijalankan_bila_sudah_ketemu(armada, monkeypatch):
    putaran = {"n": 0}

    def _kotak(im):
        putaran["n"] += 1
        return vin_ocr.kotak_datar(VIN_A)

    monkeypatch.setattr(vin_ocr, "_kotak_ocr", _kotak)
    vin_ocr.baca_rangka(_foto_kosong(), isolasi=False)
    assert putaran["n"] == 1, "varian/orientasi lanjutan tak boleh dijalankan lagi"


def test_alur_penuh_foto_tanpa_nomor(armada, ocr_palsu):
    ocr_palsu("MADE IN CHINA", "SINOTRUK")
    hasil = vin_ocr.baca_rangka(_foto_kosong(), isolasi=False)
    assert not hasil["ok"] and hasil["keyakinan"] == "gagal"
    assert "tidak terbaca" in hasil["pesan"].lower()


def test_alur_penuh_ocr_error_tak_menjatuhkan_permintaan(armada, monkeypatch):
    def _meledak(im):
        raise RuntimeError("onnx rusak")
    monkeypatch.setattr(vin_ocr, "_kotak_ocr", _meledak)
    hasil = vin_ocr.baca_rangka(_foto_kosong(), isolasi=False)
    assert hasil["keyakinan"] == "gagal" and hasil["ok"] is False


def test_foto_rusak_ditolak_jelas():
    with pytest.raises(ValueError):
        vin_ocr.baca_rangka(b"bukan gambar sama sekali")


def test_populasi_mati_fitur_tetap_jalan(monkeypatch, ocr_palsu):
    """Populasi (Supabase) mati → kehilangan keyakinan 'pasti', bukan mati total."""
    monkeypatch.setattr(vin_ocr, "armada", lambda: [])
    ocr_palsu(_vin_baru())
    hasil = vin_ocr.baca_rangka(_foto_kosong(), isolasi=False)
    assert hasil["ok"] and hasil["keyakinan"] == "tinggi" and hasil["unit"] is None


# ── endpoint /api/ai/ocr-rangka ───────────────────────────────────────
ADMIN = {"username": "admin", "role": "admin"}


@pytest.fixture
def klien():
    from fastapi.testclient import TestClient

    from app import deps
    from app.main import app
    app.dependency_overrides[deps.get_current_user] = lambda: ADMIN
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_endpoint_mengembalikan_hasil_baca(klien, monkeypatch):
    from app.routers import ai as ai_router
    monkeypatch.setattr(ai_router.vin_ocr, "baca_rangka",
                        lambda data: {"ok": True, "rangka": VIN_A, "keyakinan": "pasti"})
    r = klien.post("/api/ai/ocr-rangka",
                   files={"file": ("rangka.jpg", _foto_kosong(), "image/jpeg")})
    assert r.status_code == 200 and r.json()["rangka"] == VIN_A


def test_endpoint_foto_kebesaran_ditolak(klien, monkeypatch):
    """Batas dijaga SAMBIL membaca (bukan setelah), supaya unggahan raksasa tak
    perlu ditampung dulu di RAM container."""
    monkeypatch.setattr(vin_ocr, "MAX_BYTES", 1024)
    from app.routers import ai as ai_router
    monkeypatch.setattr(ai_router.vin_ocr, "MAX_BYTES", 1024)
    r = klien.post("/api/ai/ocr-rangka",
                   files={"file": ("besar.jpg", b"x" * 5000, "image/jpeg")})
    assert r.status_code == 413


def test_endpoint_foto_rusak_jadi_400(klien):
    r = klien.post("/api/ai/ocr-rangka",
                   files={"file": ("rusak.jpg", b"bukan gambar", "image/jpeg")})
    assert r.status_code == 400


# ── regresi foto NYATA (butuh paket OCR) ──────────────────────────────
_DATA = __import__("pathlib").Path(__file__).parent / "data"
FOTO_UJI = _DATA / "rangka_chassis.jpg"          # nomor dipahat di chassis
FOTO_PLAT = _DATA / "rangka_nameplate.jpg"       # nameplate pabrik CNHTC
FOTO_ETSA = _DATA / "rangka_plat_etsa.jpg"       # plat beretsa, label Mandarin, rebah
FOTO_PANEL = _DATA / "rangka_plat_dua_panel.jpg"  # plat CNHTC panel Inggris+Mandarin


@pytest.mark.skipif(not FOTO_UJI.exists(), reason="foto uji tidak ada")
def test_foto_lapangan_nyata_terbaca(armada):
    """Foto lapangan asli (nomor dipahat di chassis, berdebu, kontras rendah),
    lewat JALUR PRODUKSI penuh — termasuk proses anak. Di-skip otomatis bila
    paket OCR belum terpasang."""
    pytest.importorskip("rapidocr_onnxruntime")
    hasil = vin_ocr.baca_rangka(FOTO_UJI.read_bytes())
    assert hasil["rangka"] == VIN_A, hasil["teks_terbaca"]
    assert hasil["keyakinan"] == "pasti"


@pytest.mark.skipif(not FOTO_PLAT.exists(), reason="foto plat tidak ada")
def test_nameplate_pabrik_terbaca(armada):
    """Foto NAMEPLATE CNHTC: MODEL/CHASSIS NO/ENGINE OUTPUT/RAW/GCW/MADE YEAR
    semuanya terbaca OCR, jadi yang diuji di sini = nomor rangka-lah yang
    dipilih, bukan salah satu angka lain di plat itu."""
    pytest.importorskip("rapidocr_onnxruntime")
    hasil = vin_ocr.baca_rangka(FOTO_PLAT.read_bytes())
    assert hasil["rangka"] == VIN_C, hasil["teks_terbaca"]
    assert hasil["keyakinan"] == "pasti"


@pytest.mark.skipif(not FOTO_ETSA.exists(), reason="foto plat etsa tidak ada")
def test_plat_etsa_rebah_terbaca(armada):
    """Plat BERETSA (huruf abu-abu di atas pelat perak) yang dipotret REBAH 90°,
    berlabel Mandarin. Foto ini yang menuntut tiga lapis sekaligus: label 'VIN'
    terbaca padahal nilainya tidak terdeteksi sama sekali, kolom di bawah label
    dibaca ulang sebagai satu baris, dan hasilnya ('…PS0004B4', meleset satu
    huruf) baru mendarat di unit yang benar lewat snap armada."""
    pytest.importorskip("rapidocr_onnxruntime")
    hasil = vin_ocr.baca_rangka(FOTO_ETSA.read_bytes())
    assert hasil["rangka"] == VIN_D, hasil["teks_terbaca"]
    assert hasil["keyakinan"] == "pasti"


@pytest.mark.skipif(not FOTO_PANEL.exists(), reason="foto plat dua panel tidak ada")
def test_plat_dua_panel_terbaca(armada):
    """Plat dengan panel Inggris DAN Mandarin berdampingan: 462 potongan teks,
    dan nomor rangkanya terbaca UTUH sejak awal — yang diuji di sini adalah ia
    tidak terdesak keluar dari daftar yang diadu oleh potongan sampah."""
    pytest.importorskip("rapidocr_onnxruntime")
    hasil = vin_ocr.baca_rangka(FOTO_PANEL.read_bytes())
    assert hasil["rangka"] == VIN_E, hasil["teks_terbaca"]
    assert hasil["keyakinan"] == "pasti"


@pytest.mark.skipif(not FOTO_UJI.exists(), reason="foto uji tidak ada")
def test_proses_anak_tak_menyentuh_populasi(armada, monkeypatch):
    """Armada dikirim lewat stdin ke proses anak — anak TIDAK boleh memanggil
    populasi (Supabase) sendiri, kalau tidak tiap foto berarti satu unduhan."""
    pytest.importorskip("rapidocr_onnxruntime")
    hasil = vin_ocr._jalankan_anak(FOTO_UJI.read_bytes(), ARMADA)
    assert hasil is not None and hasil["rangka"] == VIN_A
    assert hasil["unit"]["customer"] == "PT UJI"   # info datang dari stdin induk


def test_memori_container_menipis_foto_ditolak_sopan(armada, monkeypatch):
    """Jatah container tinggal sedikit → JANGAN baca foto: OOM-killer cgroup akan
    memilih proses terbesar (server itu sendiri) dan asisten mati untuk semua
    orang. Menolak sesaat jauh lebih murah."""
    monkeypatch.setattr(vin_ocr, "_sisa_memori_mb", lambda: 120.0)
    monkeypatch.setattr(vin_ocr, "_jalankan_anak",
                        lambda *a: pytest.fail("anak tak boleh dijalankan"))
    hasil = vin_ocr.baca_rangka(_foto_kosong())
    assert hasil["keyakinan"] == "gagal" and not hasil["ok"]
    assert "penuh" in hasil["pesan"].lower()


def test_memori_tak_diketahui_tetap_jalan(armada, ocr_palsu, monkeypatch):
    """Di luar container (laptop dev) tak ada angka cgroup → jangan menghalangi."""
    monkeypatch.setattr(vin_ocr, "_sisa_memori_mb", lambda: None)
    monkeypatch.setattr(vin_ocr, "_jalankan_anak", lambda data, rows: None)
    ocr_palsu("★L7Z1BLMJ4TJ465057")
    assert vin_ocr.baca_rangka(_foto_kosong())["rangka"] == VIN_A


def test_proses_anak_gagal_tetap_dapat_jawaban(armada, ocr_palsu, monkeypatch):
    """Anak tak bisa dijalankan (mis. python terkunci) → jangan gagal total,
    baca saja di dalam proses server."""
    monkeypatch.setattr(vin_ocr, "_jalankan_anak", lambda data, rows: None)
    ocr_palsu("★L7Z1BLMJ4TJ465057")
    hasil = vin_ocr.baca_rangka(_foto_kosong())
    assert hasil["rangka"] == VIN_A and hasil["keyakinan"] == "pasti"
