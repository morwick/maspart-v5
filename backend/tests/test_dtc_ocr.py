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


def test_fmi_asing_pada_spn_dikenal_jadi_tinggi():
    """SPN terdaftar tapi FMI-nya tidak: mungkin ECU varian lain — sebutkan FMI
    yang memang terdaftar supaya user bisa membandingkan dengan layarnya."""
    h = dtc_ocr._dari_kotak(_tabel(((SPN, 31),)))
    assert h["keyakinan"] == "tinggi"
    assert h["kode"][0]["dikenal"] is False
    assert FMI in h["kode"][0]["fmi_terdaftar"]


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


@pytest.mark.skipif(not FOTO_PANEL.exists(), reason="foto panel tidak ada")
def test_foto_panel_nyata_terbaca():
    """Foto lapangan asli (panel SITRAK, tabel DM1 satu baris, jarum
    speedometer ikut dalam bingkai) lewat jalur produksi penuh."""
    pytest.importorskip("rapidocr_onnxruntime")
    h = dtc_ocr.baca_dtc(FOTO_PANEL.read_bytes())
    assert [(k["spn"], k["fmi"]) for k in h["kode"]] == [(SPN, FMI)], h["teks_terbaca"]
    assert h["keyakinan"] == "pasti" and h["ecu"] == "Engine"
