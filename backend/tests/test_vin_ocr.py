"""Baca NOMOR RANGKA dari foto (services/vin_ocr.py).

Semua test OFFLINE: mesin OCR & populasi di-stub, kecuali satu test regresi
foto NYATA yang otomatis di-skip bila paket `rapidocr_onnxruntime` belum
terpasang (mis. laptop dev tanpa dependensi penuh).

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
ARMADA = [
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


# ── alur penuh (mesin OCR di-stub) ────────────────────────────────────
def _foto_kosong() -> bytes:
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (64, 48), (128, 128, 128)).save(buf, format="JPEG")
    return buf.getvalue()


@pytest.fixture
def ocr_palsu(monkeypatch):
    """Ganti mesin OCR-nya saja; pra-proses & pemilihan kandidat tetap asli."""
    kotak: dict = {"baris": []}

    def _set(*baris):
        kotak["baris"] = list(baris)

    monkeypatch.setattr(vin_ocr, "_baris_ocr", lambda im: kotak["baris"])
    return _set


def test_alur_penuh_foto_jadi_nomor_rangka(armada, ocr_palsu):
    ocr_palsu("★L7Z1BLMJ4TJ465057")
    hasil = vin_ocr.baca_rangka(_foto_kosong())
    assert hasil["ok"] and hasil["rangka"] == VIN_A
    assert hasil["frame"] == "TJ465057"
    assert hasil["keyakinan"] == "pasti"
    assert "HOWO-NX 6X4" in hasil["pesan"]
    assert hasil["teks_terbaca"] == ["★L7Z1BLMJ4TJ465057"]


def test_alur_penuh_nomor_terpecah_dua_kotak(armada, ocr_palsu):
    """Pahatan renggang / ada bintang di tengah → OCR memecah jadi 2 kotak."""
    ocr_palsu("LZZ1BLMJ4", "TJ465057")
    hasil = vin_ocr.baca_rangka(_foto_kosong())
    assert hasil["rangka"] == VIN_A


def test_alur_penuh_foto_tanpa_nomor(armada, ocr_palsu):
    ocr_palsu("MADE IN CHINA", "SINOTRUK")
    hasil = vin_ocr.baca_rangka(_foto_kosong())
    assert not hasil["ok"] and hasil["keyakinan"] == "gagal"
    assert "tidak terbaca" in hasil["pesan"].lower()


def test_alur_penuh_ocr_error_tak_menjatuhkan_permintaan(armada, monkeypatch):
    def _meledak(im):
        raise RuntimeError("onnx rusak")
    monkeypatch.setattr(vin_ocr, "_baris_ocr", _meledak)
    hasil = vin_ocr.baca_rangka(_foto_kosong())
    assert hasil["keyakinan"] == "gagal" and hasil["ok"] is False


def test_foto_rusak_ditolak_jelas():
    with pytest.raises(ValueError):
        vin_ocr.baca_rangka(b"bukan gambar sama sekali")


def test_populasi_mati_fitur_tetap_jalan(monkeypatch, ocr_palsu):
    """Populasi (Supabase) mati → kehilangan keyakinan 'pasti', bukan mati total."""
    monkeypatch.setattr(vin_ocr, "armada", lambda: [])
    ocr_palsu(_vin_baru())
    hasil = vin_ocr.baca_rangka(_foto_kosong())
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
FOTO_UJI = __import__("pathlib").Path(__file__).parent / "data" / "rangka_chassis.jpg"


@pytest.mark.skipif(not FOTO_UJI.exists(), reason="foto uji tidak ada")
def test_foto_lapangan_nyata_terbaca(armada):
    """Foto lapangan asli (nomor dipahat di chassis, berdebu, kontras rendah).
    Di-skip otomatis bila paket OCR belum terpasang."""
    pytest.importorskip("rapidocr_onnxruntime")
    hasil = vin_ocr.baca_rangka(FOTO_UJI.read_bytes())
    assert hasil["rangka"] == VIN_A, hasil["teks_terbaca"]
    assert hasil["keyakinan"] == "pasti"
