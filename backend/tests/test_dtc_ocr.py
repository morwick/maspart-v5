"""Baca KODE KESALAHAN (SPN/FMI) dari foto panel instrumen (services/dtc_ocr.py).

Semua test OFFLINE: mesin OCR di-stub dengan kotak tiruan, kecuali test regresi
FOTO NYATA yang otomatis di-skip bila `rapidocr_onnxruntime` belum terpasang.
Store DTC (`dtc_codes`) memang dipakai apa adanya — file gz lokal, tanpa jaringan.

⚠️ Test ber-stub memakai `isolasi=False`: produksi menjalankan OCR di PROSES
ANAK, dan monkeypatch tak menyeberang proses (pola sama dgn test_vin_ocr).

⚠️ Yang dijaga di sini bukan "OCR-nya pintar", melainkan dua hal yang menentukan
apakah fitur ini layak dipakai mekanik:
  • angka di LUAR tabel (speedometer, odometer) tak boleh ikut terbaca jadi kode;
  • kode yang tak dikenal store WAJIB pulang sebagai 'rendah' — menukar kode
    kesalahan diam-diam berarti membongkar komponen yang salah.
"""
import io

import pytest

from app.services import dtc_ocr, vin_ocr

# Kode NYATA dari foto uji pemilik: terdaftar di store (P0335 · EMS).
SPN, FMI = 4203, 12
SPN_ASING = 123456                       # tak ada di store, tapi MASIH wajar (19 bit)
SPN_MUSTAHIL = 999998                    # di atas batas J1939 & tak dikenal
SPN_SALAH_BACA = 4263                    # 0→6: satu substitusi dari 4203


def _kotak(x0, y0, x1, y1, teks):
    return (float(x0), float(y0), float(x1), float(y1), teks)


def _tabel(baris=((SPN, FMI),), *, ecu="Engine", jenis="DM1", derau=True):
    """Kotak tiruan meniru TATA LETAK nyata panel SITRAK (koordinat diambil dari
    hasil OCR foto asli, supaya uji kolom benar-benar menguji yang sebenarnya)."""
    out = [_kotak(155, 451, 204, 486, "No"),
           _kotak(258, 438, 342, 488, "SPN"),
           _kotak(416, 441, 496, 482, "FMI"),
           _kotak(543, 445, 617, 490, jenis)]
    y = 505
    for i, (spn, fmi) in enumerate(baris, start=1):
        out.append(_kotak(160, y, 200, y + 33, str(i)))
        out.append(_kotak(268, y, 336, y + 33, str(spn)))
        out.append(_kotak(422, y, 461, y + 33, str(fmi)))
        y += 60
    if derau:                            # jarum speedometer & odometer ikut terpotret
        out.append(_kotak(795, 454, 847, 490, "20"))
        out.append(_kotak(850, 335, 899, 376, "40"))
    if ecu:
        out.append(_kotak(195, 714, 292, 748, ecu))
    return out


def _foto_kosong() -> bytes:
    from PIL import Image
    b = io.BytesIO()
    Image.new("RGB", (40, 30), (10, 10, 10)).save(b, "JPEG")
    return b.getvalue()


# ── normalisasi angka ─────────────────────────────────────────────────
def test_huruf_yang_sering_tertukar_dibetulkan():
    """Layar LCD: '0' terbaca 'O', '1' terbaca 'I'."""
    assert dtc_ocr._angka("42O3") == "4203"
    assert dtc_ocr._angka("I2") == "12"
    assert dtc_ocr._angka(" 4203 ") == "4203"


def test_kata_biasa_bukan_angka():
    """Tanpa ambang ini 'SOS' menjadi '505' dan ikut diadu sebagai kode."""
    for kata in ("SOS", "FMI", "SPN", "Engine", "DM1", "ON"):
        assert dtc_ocr._angka(kata) is None, kata


def test_bukti_layar_kesalahan():
    assert dtc_ocr.ada_bukti(_tabel())
    assert not dtc_ocr.ada_bukti([_kotak(0, 0, 90, 20, "LZZ1BLMJ4TJ465057")])


# ── pembacaan tabel ───────────────────────────────────────────────────
def test_tabel_berkolom_terbaca_dan_diadu_ke_store():
    h = dtc_ocr._dari_kotak(_tabel())
    assert h["ok"] and h["keyakinan"] == "pasti"
    assert [(k["spn"], k["fmi"]) for k in h["kode"]] == [(SPN, FMI)]
    assert h["kode"][0]["dikenal"] and h["kode"][0]["kode"] == "P0335"
    assert h["jenis_pesan"] == "DM1" and h["ecu"] == "Engine"


def test_angka_di_luar_tabel_diabaikan():
    """Regresi dari foto asli: jarum speedometer menyumbang '20' dan '40' —
    dua-duanya angka yang sah untuk sebuah FMI, dan HANYA letaknya yang
    membedakannya dari isi tabel."""
    h = dtc_ocr._dari_kotak(_tabel())
    assert [(k["spn"], k["fmi"]) for k in h["kode"]] == [(SPN, FMI)]


def test_beberapa_baris_kesalahan():
    h = dtc_ocr._dari_kotak(_tabel(((SPN, FMI), (SPN, 2))))
    assert [(k["spn"], k["fmi"]) for k in h["kode"]] == [(SPN, FMI), (SPN, 2)]


def test_kepala_tabel_menyatu_satu_kotak():
    """OCR kadang menyatukan seluruh kepala tabel jadi satu kotak — letak kolom
    lalu ditaksir dari posisi hurufnya."""
    kotak = [_kotak(150, 438, 620, 490, "No.  SPN   FMI   DM1"),
             _kotak(268, 505, 336, 538, str(SPN)),
             _kotak(422, 505, 461, 538, str(FMI))]
    h = dtc_ocr._dari_kotak(kotak)
    assert [(k["spn"], k["fmi"]) for k in h["kode"]] == [(SPN, FMI)]


def test_baris_data_menyatu():
    """Angka rapat → OCR membacanya sebagai satu baris teks ('1 4203 12')."""
    kotak = [_kotak(258, 438, 342, 488, "SPN"), _kotak(416, 441, 496, 482, "FMI"),
             _kotak(160, 505, 470, 538, f"1 {SPN} {FMI}")]
    h = dtc_ocr._dari_kotak(kotak)
    assert [(k["spn"], k["fmi"]) for k in h["kode"]] == [(SPN, FMI)]


def test_bentuk_sebaris():
    """Sebagian panel & alat scanner menuliskannya memanjang, tanpa kolom."""
    h = dtc_ocr._dari_kotak([_kotak(0, 0, 300, 30, f"SPN{SPN}/FMI{FMI}")])
    assert [(k["spn"], k["fmi"]) for k in h["kode"]] == [(SPN, FMI)]
    assert h["keyakinan"] == "tinggi"          # tanpa kolom → tak pernah 'pasti'


def test_foto_terbalik_dipulihkan_dari_koordinat():
    """Foto 180°: isi tiap kotak tetap terbaca benar (model cls memutarnya
    sendiri), yang tertukar hanya tata letaknya → cukup dibalik koordinatnya,
    tanpa membaca ulang gambar."""
    tegak = _tabel()
    terbalik = dtc_ocr._balik(tegak, (1600, 900))
    assert dtc_ocr._dari_kotak(terbalik)["ok"] is False        # apa adanya: gagal
    pulih = dtc_ocr._dari_kotak(dtc_ocr._balik(terbalik, (1600, 900)))
    assert [(k["spn"], k["fmi"]) for k in pulih["kode"]] == [(SPN, FMI)]


# ── pagar kejujuran ───────────────────────────────────────────────────
def test_fmi_di_luar_batas_ditolak():
    """FMI = 5 bit (0..31). 'OC' (jumlah kejadian) di sebagian panel berdiri di
    kolom sesudah FMI dan gampang tertukar."""
    h = dtc_ocr._dari_kotak(_tabel(((SPN, 45),)))
    assert h["ok"] is False and h["keyakinan"] == "gagal"


def test_spn_di_luar_batas_j1939_dibuang():
    """SPN = 19 bit. Yang lebih besar DAN tak dikenal store = salah baca, bukan
    kode baru — dibuang, sehingga hasilnya 'gagal' yang jujur."""
    h = dtc_ocr._dari_kotak(_tabel(((SPN_MUSTAHIL, FMI),)))
    assert h["ok"] is False and h["keyakinan"] == "gagal"


def test_kode_tak_dikenal_jadi_rendah():
    h = dtc_ocr._dari_kotak(_tabel(((SPN_ASING, FMI),)))
    assert h["ok"] and h["keyakinan"] == "rendah"
    assert h["kode"][0]["dikenal"] is False
    assert "BELUM yakin" in dtc_ocr.pesan(h)


def test_usul_koreksi_hanya_bila_tunggal():
    """Satu substitusi angka yang mendarat di store BOLEH ditawarkan; dua usulan
    berarti kita tak tahu yang mana — dan menebak kode kesalahan lebih buruk
    daripada mengaku tak yakin."""
    usul = dtc_ocr._usul_koreksi(SPN_SALAH_BACA, FMI)
    assert usul == [f"SPN {SPN} FMI {FMI}"]
    h = dtc_ocr._dari_kotak(_tabel(((SPN_SALAH_BACA, FMI),)))
    assert h["keyakinan"] == "rendah"          # ⛔ usulan TIDAK menggantikan bacaan
    assert h["kode"][0]["spn"] == SPN_SALAH_BACA
    assert f"SPN {SPN} FMI {FMI}" in dtc_ocr.pesan(h)


def test_fmi_asing_pada_spn_dikenal_tak_dikirim_otomatis():
    """SPN terdaftar tapi FMI-nya tidak = bukti SEPARUH → 'rendah' (user melihat
    dulu). Sebut FMI yang memang terdaftar supaya ia bisa membandingkan layar.

    ⛔ Jangan dinaikkan jadi 'tinggi': insiden produksi 2026-08-12 — nomor urut
    baris terbaca 'SPN 1', dan SPN 1 kebetulan ada di store dengan FMI lain,
    sehingga bacaan yang sepenuhnya salah terkirim sendiri ke asisten."""
    h = dtc_ocr._dari_kotak(_tabel(((SPN, 31),)))
    assert h["keyakinan"] == "rendah"
    assert h["kode"][0]["dikenal"] is False
    assert FMI in h["kode"][0]["fmi_terdaftar"]


def test_nomor_urut_baris_tak_pernah_jadi_spn():
    """REGRESI PRODUKSI 2026-08-12 (foto asli, OCR di container): kepala 'No.'
    dan 'SPN' terbaca MENYATU jadi satu kotak → taksiran kolom SPN bergeser ke
    kiri, dan nomor urut baris ikut masuk toleransi. Dulu angka PERTAMA yang
    menang, jadi hasilnya 'SPN 1 FMI 12'. Koordinat di bawah = hasil OCR
    container apa adanya."""
    kotak = [_kotak(150, 436, 347, 495, "No.SPN"),
             _kotak(423, 443, 490, 481, "FMI"),
             _kotak(542, 444, 619, 490, "DM1"),
             _kotak(795, 450, 846, 492, "20"),      # jarum speedometer
             _kotak(167, 507, 244, 539, "日1"),     # kotak centang + nomor urut
             _kotak(266, 504, 339, 540, "4203"),
             _kotak(432, 506, 458, 533, "12"),
             _kotak(196, 714, 291, 749, "Engine")]
    h = dtc_ocr._dari_kotak(kotak)
    assert [(k["spn"], k["fmi"]) for k in h["kode"]] == [(SPN, FMI)]
    assert h["keyakinan"] == "pasti"


def test_satu_kode_meragukan_menurunkan_seluruhnya():
    """Menyajikan '1 dari 2 pasti' membuat yang kedua ikut dipercaya."""
    h = dtc_ocr._dari_kotak(_tabel(((SPN, FMI), (SPN_ASING, 3))))
    assert h["keyakinan"] == "rendah"


def test_label_ecu_jadi_penentu_sumber_penjelasan():
    """SPN 4203 terdaftar di beberapa ECU; label di layar yang memilihkan."""
    h = dtc_ocr._dari_kotak(_tabel(ecu="Engine"))
    assert h["kode"][0]["unit"] == "EMS"


def test_bukan_layar_kesalahan_mengalah():
    """Tanpa kata SPN/FMI/DM1, modul mengembalikan None supaya pemanggil
    melanjutkan ke pembacaan NOMOR RANGKA pada endpoint yang sama."""
    assert dtc_ocr._dari_kotak(vin_ocr.kotak_datar("LZZ1BLMJ4TJ465057")) is None


def test_pesan_memicu_guard_dtc_first():
    """Pesan hasil baca foto harus dikenali guard DTC-FIRST — kalau tidak, model
    boleh menjawab kode kesalahan dari INGATAN tanpa memanggil tool, dan seluruh
    pagar kejujuran DTC (sumber, FMI tersedia, lembar diagnosa) terlewat.

    Ini mengunci dua fitur pada SATU bentuk kalimat: mengubah `pesan()` tanpa
    menyesuaikan `_DTC_SPN_RE` akan mematikan guard itu diam-diam."""
    from app.services import ai_assistant as ai
    teks = dtc_ocr.pesan(dtc_ocr._dari_kotak(_tabel()))
    assert str(SPN) in ai._dtc_tokens(teks)


def test_pesan_siap_kirim_tanpa_arti_kode():
    """Pesan yang dikirim ke chat sengaja hanya memuat ANGKA + konteks layar:
    penjelasannya pekerjaan tool cari_kode_kesalahan/diagnosa yang sudah membawa
    pagar sumber & bahasanya sendiri."""
    teks = dtc_ocr.pesan(dtc_ocr._dari_kotak(_tabel()))
    assert f"SPN {SPN} FMI {FMI}" in teks and "DM1" in teks and "Engine" in teks
    assert "crankshaft" not in teks.lower()


# ── baris yang tak terbaca lengkap ────────────────────────────────────
def test_baris_tak_lengkap_tidak_hilang_diam_diam():
    """KELUHAN PEMILIK 2026-08-12: layar memuat 2 kode, yang sampai ke asisten
    cuma 1 — angka FMI satu digit ('4') tak terdeteksi OCR, jadi SELURUH
    barisnya lenyap tanpa tanda apa pun, dan sisanya dikirim dengan keyakinan
    'pasti'. Kehilangan itu sekarang WAJIB terdengar."""
    kotak = [_kotak(258, 438, 342, 488, "SPN"), _kotak(416, 441, 496, 482, "FMI"),
             _kotak(155, 451, 204, 486, "No."),
             _kotak(160, 505, 200, 538, "1"), _kotak(268, 505, 336, 538, "3597"),
             _kotak(160, 565, 200, 598, "2"), _kotak(268, 565, 336, 598, str(SPN)),
             _kotak(422, 565, 461, 598, str(FMI))]
    h = dtc_ocr._dari_kotak(kotak)                 # tanpa gambar → tak bisa baca ulang
    assert h["tak_lengkap"] is True
    assert h["keyakinan"] == "rendah"
    assert "tak terbaca lengkap" in dtc_ocr.pesan(h)
    assert [(k["spn"], k["fmi"]) for k in h["kode"]] == [(SPN, FMI)]


def test_angka_jauh_di_bawah_tabel_bukan_baris_data():
    """Angka speedometer/voltmeter di bagian bawah panel berdiri dekat kolom SPN
    dan sempat dianggap 'baris data yang tak lengkap' — bacaan yang sudah benar
    ikut diturunkan jadi ragu. Tabel berhenti di jeda kosong pertama."""
    kotak = _tabel() + [_kotak(271, 1183, 303, 1216, "32")]
    h = dtc_ocr._dari_kotak(kotak)
    assert h["tak_lengkap"] is False and h["keyakinan"] == "pasti"
    assert [(k["spn"], k["fmi"]) for k in h["kode"]] == [(SPN, FMI)]


# ── kesepakatan antar-varian ──────────────────────────────────────────
def _ocr_beruntun(monkeypatch, *jawaban):
    """Ganti mesin OCR: panggilan ke-n mengembalikan `jawaban[n]` — meniru dua
    varian pra-proses yang membaca angka BERBEDA dari foto yang sama."""
    sisa = list(jawaban)
    monkeypatch.setattr(vin_ocr, "_kotak_ocr",
                        lambda im: sisa.pop(0) if sisa else jawaban[-1])


def test_dua_varian_beda_angka_tak_dikirim_otomatis(monkeypatch):
    """REGRESI PRODUKSI 2026-08-12 (foto disilaukan cahaya): '12' terbaca '2',
    dan SPN 4203 FMI 2 KEBETULAN juga kode sah — store justru ikut
    membenarkannya, jadi hasil yang salah keluar 'pasti'. Satu-satunya yang
    membedakan: varian 'clahe' membaca 2, varian 'raw' membaca 12."""
    _ocr_beruntun(monkeypatch, _tabel(((SPN, 2),)), _tabel(((SPN, FMI),)))
    h = dtc_ocr._baca_lokal(_foto_kosong())
    assert h["keyakinan"] == "rendah"
    assert h["kode"][0]["alternatif"], "bacaan varian lain harus ikut disodorkan"
    assert "berbeda" in h["pesan"]


def test_kemungkinan_lain_hanya_yang_terdaftar(monkeypatch):
    """Varian pembanding kadang menghasilkan angka SAMPAH (di produksi: 'SPN 1
    FMI 2' dari kolom nomor urut). Keyakinan tetap turun — bacaan yang goyah
    tetap goyah — tapi sampahnya JANGAN disodorkan sebagai pilihan: itu hanya
    membuat user meragukan bacaan yang sudah benar."""
    _ocr_beruntun(monkeypatch, _tabel(), _tabel(((1, 2),)))
    h = dtc_ocr._baca_lokal(_foto_kosong())
    assert h["keyakinan"] == "rendah"
    assert h["kode"][0]["alternatif"] == []
    assert "Kemungkinan lain" not in h["pesan"]


def test_dua_varian_sepakat_tetap_pasti(monkeypatch):
    _ocr_beruntun(monkeypatch, _tabel(), _tabel())
    h = dtc_ocr._baca_lokal(_foto_kosong())
    assert h["keyakinan"] == "pasti"
    assert [(k["spn"], k["fmi"]) for k in h["kode"]] == [(SPN, FMI)]


def test_varian_yang_membaca_lebih_sedikit_bukan_penyanggah(monkeypatch):
    """Varian pembanding yang melihat 1 dari 2 baris tidak MEMBANTAH angka mana
    pun — ia cuma kehilangan satu baris (angka FMI satu digit gampang tak
    terdeteksi). Menurunkan keyakinan di situ berarti meminta konfirmasi untuk
    bacaan yang justru sempurna."""
    _ocr_beruntun(monkeypatch, _tabel(((SPN, FMI), (4203, 2))), _tabel(((SPN, FMI),)))
    h = dtc_ocr._baca_lokal(_foto_kosong())
    assert h["keyakinan"] == "pasti"
    assert [(k["spn"], k["fmi"]) for k in h["kode"]] == [(SPN, FMI), (4203, 2)]


def test_varian_kosong_bukan_penyanggah(monkeypatch):
    """Varian yang tak membaca apa pun TIDAK mengklaim apa pun — ia tak boleh
    menurunkan keyakinan, kalau tidak hampir tak ada foto yang lolos 'pasti'."""
    _ocr_beruntun(monkeypatch, _tabel(), [_kotak(0, 0, 60, 20, "SPN")])
    h = dtc_ocr._baca_lokal(_foto_kosong())
    assert h["keyakinan"] == "pasti"


# ── jalur gabungan (satu foto, dua kemungkinan) ───────────────────────
def test_foto_rangka_diteruskan_ke_pembaca_rangka(monkeypatch):
    """Foto tanpa bukti kode kesalahan WAJIB jatuh ke jalur nomor rangka."""
    monkeypatch.setattr(vin_ocr, "_kotak_ocr",
                        lambda im: vin_ocr.kotak_datar("LZZ1BLMJ4TJ465057"))
    monkeypatch.setattr(vin_ocr, "armada", lambda: [])
    h = dtc_ocr.baca_foto(_foto_kosong(), isolasi=False)
    assert h["jenis"] == "rangka" and h["rangka"] == "LZZ1BLMJ4TJ465057"


def test_foto_rangka_hanya_dibaca_sekali_oleh_probe(monkeypatch):
    """Probe kode kesalahan berdiri di depan pembacaan nomor rangka, jadi ia
    tak boleh membaca varian kedua sebelum terbukti ini layar kesalahan —
    kalau tidak, tiap foto rangka membayar satu pembacaan penuh sia-sia."""
    n = []
    monkeypatch.setattr(vin_ocr, "_kotak_ocr",
                        lambda im: (n.append(1), vin_ocr.kotak_datar("ABC123"))[1])
    assert dtc_ocr._baca_lokal(_foto_kosong()) == {}
    assert len(n) == 1, f"probe membaca {len(n)} kali untuk foto bukan-panel"


def test_probe_dtc_menang_atas_rangka(monkeypatch):
    monkeypatch.setattr(vin_ocr, "_kotak_ocr", lambda im: _tabel())
    monkeypatch.setattr(vin_ocr, "armada", lambda: [])
    h = dtc_ocr.baca_foto(_foto_kosong(), isolasi=False)
    assert h["jenis"] == "dtc" and h["kode"][0]["spn"] == SPN


def test_hasil_baca_diingat_agar_tak_dibaca_dua_kali(monkeypatch):
    """`_kotak_ocr` mengingat dua gambar terakhir supaya satu foto yang dilewati
    dua pembaca berurutan tidak dibaca dua kali (terukur 2–5 detik per baca)."""
    pytest.importorskip("numpy")
    import numpy as np
    n = []

    def mesin_palsu(bgr):
        n.append(1)
        return _tabel()
    monkeypatch.setattr(vin_ocr, "_mesin", lambda: lambda im: (
        [[[[0, 0], [10, 0], [10, 5], [0, 5]], "SPN", 0.9]], None))
    monkeypatch.setattr(vin_ocr, "_memo", [])
    im = np.zeros((40, 30, 3), dtype=np.uint8)
    a = vin_ocr._kotak_ocr(im)
    b = vin_ocr._kotak_ocr(im)
    assert a == b
    im[0, 0, 0] = 7                                # isi piksel beda → baca lagi
    assert vin_ocr._kotak_ocr(im) == a             # (stub mengembalikan yg sama)
    assert len(vin_ocr._memo) == 2


# ── endpoint /api/ai/ocr-foto ─────────────────────────────────────────
ADMIN = {"username": "admin", "role": "admin"}


@pytest.fixture
def klien():
    from fastapi.testclient import TestClient

    from app import deps
    from app.main import app
    app.dependency_overrides[deps.get_current_user] = lambda: ADMIN
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_endpoint_mengembalikan_kode(klien, monkeypatch):
    from app.routers import ai as ai_router
    monkeypatch.setattr(ai_router.dtc_ocr, "baca_foto",
                        lambda data: {"ok": True, "jenis": "dtc",
                                      "kode": [{"spn": SPN, "fmi": FMI}]})
    r = klien.post("/api/ai/ocr-foto",
                   files={"file": ("panel.jpg", _foto_kosong(), "image/jpeg")})
    assert r.status_code == 200 and r.json()["kode"][0]["spn"] == SPN


def test_endpoint_foto_kebesaran_ditolak(klien, monkeypatch):
    from app.routers import ai as ai_router
    monkeypatch.setattr(ai_router.vin_ocr, "MAX_BYTES", 1024)
    r = klien.post("/api/ai/ocr-foto",
                   files={"file": ("besar.jpg", b"x" * 5000, "image/jpeg")})
    assert r.status_code == 413


def test_endpoint_foto_rusak_jadi_400(klien):
    r = klien.post("/api/ai/ocr-foto",
                   files={"file": ("rusak.jpg", b"bukan gambar", "image/jpeg")})
    assert r.status_code == 400


# ── regresi FOTO NYATA (butuh paket OCR) ──────────────────────────────
FOTO_PANEL = __import__("pathlib").Path(__file__).parent / "data" / "dtc_dashboard.jpg"


FOTO_2BARIS = __import__("pathlib").Path(__file__).parent / "data" / "dtc_dashboard_2baris.jpg"


@pytest.mark.skipif(not FOTO_2BARIS.exists(), reason="foto panel 2 baris tidak ada")
def test_foto_panel_dua_baris_utuh_atau_mengaku():
    """Foto lapangan asli dengan DUA kode (3597/4 dan 4815/10), layar berdebu &
    memantul. Yang dikunci di sini adalah SIFAT, bukan satu hasil tunggal:

    OCR di image produksi lebih tajam daripada di laptop — di container kedua
    baris terbaca utuh ('pasti'), di laptop angka '4' tetap tak terdeteksi.
    Yang TIDAK boleh terjadi di mana pun: satu baris hilang diam-diam sementara
    sisanya dikirim seolah lengkap. Jadi: kalau dua-duanya terbaca → 'pasti';
    kalau tidak → WAJIB mengaku lewat `tak_lengkap` + keyakinan 'rendah'."""
    pytest.importorskip("rapidocr_onnxruntime")
    h = dtc_ocr.baca_dtc(FOTO_2BARIS.read_bytes())
    kode = [(k["spn"], k["fmi"]) for k in h["kode"]]
    assert (3597, 4) in kode or (4815, 10) in kode, h["teks_terbaca"]
    if kode == [(3597, 4), (4815, 10)]:
        assert h["keyakinan"] == "pasti"
    else:
        assert h["tak_lengkap"] is True and h["keyakinan"] == "rendah", kode


@pytest.mark.skipif(not FOTO_PANEL.exists(), reason="foto panel tidak ada")
def test_foto_panel_nyata_terbaca():
    """Foto lapangan asli (panel SITRAK, tabel DM1 satu baris, jarum
    speedometer ikut dalam bingkai) lewat jalur produksi penuh."""
    pytest.importorskip("rapidocr_onnxruntime")
    h = dtc_ocr.baca_dtc(FOTO_PANEL.read_bytes())
    assert [(k["spn"], k["fmi"]) for k in h["kode"]] == [(SPN, FMI)], h["teks_terbaca"]
    assert h["keyakinan"] == "pasti" and h["ecu"] == "Engine"
