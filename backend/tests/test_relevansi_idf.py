"""Skor relevansi cari_part — token batas-kata + bobot IDF + cakupan query.

Versi lama memakai `len(kata_kunci_terpanjang_yang_jadi_SUBSTRING_nama)`. Tes di
sini mengunci tiga cacat yang ditimbulkannya supaya tidak kambuh:
  1. substring, bukan batas kata  → 'as' cocok di CHASSIS/GASKET
  2. bias panjang                 → kata panjang generik mengalahkan match tepat
  3. tanpa bobot kelangkaan       → 'BOLT' senilai 'TURBOCHARGER'
part_index.idf di-mock → tanpa Excel/data.
"""
import pytest

from app.services import ai_assistant as ai
from app.services import part_index


# DF buatan: BOLT/SCREW/SEAL sangat umum, TURBO/CRANKSHAFT langka.
# TURBO & SCREW sengaja SAMA-SAMA 5 huruf — supaya tes kelangkaan tidak bisa
# lulus hanya karena salah satunya lebih panjang (itu justru cacat yang lama).
_DF = {"BOLT": 4000, "SEAL": 3000, "OIL": 2500, "TRANSMISSION": 900,
       "TURBOCHARGER": 12, "CRANKSHAFT": 30, "CHASSIS": 800, "GASKET": 1500,
       "AS": 700, "RODA": 600, "BELAKANG": 500, "DEPAN": 500,
       "SCREW": 3500, "TURBO": 15, "ASSEMBLY": 2000, "RING": 1800}
_N = 5000


@pytest.fixture(autouse=True)
def mock_idf(monkeypatch):
    import math

    def fake_idf(word: str) -> float:
        df = _DF.get((word or "").upper(), 5)
        return max(0.0, math.log((_N + 1) / (df + 1)))

    monkeypatch.setattr(part_index, "idf", fake_idf)


def _skor(nama, q, terms=None, pn="WG1234567890"):
    return ai._relevansi(nama, pn, q, terms if terms is not None else [q])


# ── 1. batas kata ────────────────────────────────────────────────────
def test_substring_parsial_tidak_lagi_dianggap_cocok():
    """'as' TIDAK boleh cocok di CHASSIS / GASKET — dulu cocok karena substring."""
    assert _skor("CHASSIS FRAME", "as")[0] == 0
    assert _skor("GASKET CYLINDER", "as")[0] == 0


def test_kata_utuh_tetap_cocok():
    """Tapi 'as' sebagai KATA utuh tetap harus kena."""
    skor, cocok = _skor("AS RODA BELAKANG", "as")
    assert skor > 0
    assert cocok == "as"


def test_seal_tidak_cocok_di_sealant():
    assert _skor("SEALANT SILICONE", "seal")[0] == 0
    assert _skor("OIL SEAL", "seal")[0] > 0


# ── 2. bias panjang ──────────────────────────────────────────────────
# Bentuk `terms` di sini meniru produksi: hasil _expand_query + broaden, yaitu
# CAMPURAN frasa asli dan kata-kata lepas — bukan satu frasa utuh saja. Justru
# di bentuk inilah bias panjang versi lama muncul.
_TERMS_OIL_SEAL = ["oil seal", "oil", "seal", "transmission"]


def test_kata_ekspansi_panjang_tak_boleh_mengalahkan_match_asli():
    """Cacat lama: skor = panjang karakter, jadi 'transmission' (12 huruf, kata
    EKSPANSI) mengalahkan 'oil seal' (8 huruf, yang benar-benar diketik user).
    Part transmisi pun naik ke atas untuk query 'oil seal'."""
    tepat = _skor("OIL SEAL", "oil seal", _TERMS_OIL_SEAL)[0]
    ekspansi_panjang = _skor("TRANSMISSION HOUSING COVER", "oil seal",
                             _TERMS_OIL_SEAL)[0]
    assert tepat > ekspansi_panjang


def test_kata_user_kalah_panjang_tetap_menang():
    """Query 'seal' yang diekspansi ke 'gasket': nama SEAL RING harus menang
    atas GASKET RING. Dulu kalah karena 'gasket' (6) lebih panjang dari
    'seal' (4)."""
    kata_user = _skor("SEAL RING", "seal", ["seal", "gasket"])[0]
    kata_ekspansi = _skor("GASKET RING", "seal", ["seal", "gasket"])[0]
    assert kata_user > kata_ekspansi


# ── 3. bobot kelangkaan (IDF) ────────────────────────────────────────
def test_kata_langka_menang_atas_kata_umum_pada_panjang_SAMA():
    """'TURBO' dan 'SCREW' sama-sama 5 huruf, jadi versi lama memberi skor
    IDENTIK. Padahal TURBO nyaris menentukan jawaban, SCREW hampir tak
    memberi tahu apa-apa."""
    langka = _skor("TURBO ASSEMBLY", "turbo")[0]
    umum = _skor("SCREW ASSEMBLY", "screw")[0]
    assert langka > umum


def test_cocok_kata_memilih_token_paling_menentukan():
    """'cocok_kata' = alasan part ini muncul → token ber-IDF tertinggi."""
    _, cocok = _skor("OIL SEAL CRANKSHAFT", "crankshaft")
    assert cocok == "crankshaft"


# ── 4. cakupan query ─────────────────────────────────────────────────
def test_cocok_semua_kata_menang_atas_cocok_sebagian():
    """Setelah broaden, terms = kata lepas. Versi lama menilai keduanya SAMA
    (dua-duanya cocok di kata terpanjang 'belakang'), padahal yang satu
    menjawab seluruh maksud user dan yang lain hanya sepotong."""
    terms = ["as", "roda", "belakang"]
    penuh = _skor("AS RODA BELAKANG", "as roda belakang", terms)[0]
    sebagian = _skor("BELAKANG SPRING BRACKET", "as roda belakang", terms)[0]
    assert penuh > sebagian


def test_frasa_berurutan_menang_atas_kata_terpencar():
    """Dua nama sama-sama memuat 'oil' dan 'seal', jadi versi lama memberi
    skor identik. Yang berurutan ('OIL SEAL') jelas lebih meyakinkan."""
    terms = ["oil", "seal"]
    urut = _skor("OIL SEAL CRANKSHAFT", "oil seal", terms)[0]
    pencar = _skor("SEAL RING FOR OIL PUMP", "oil seal", terms)[0]
    assert urut > pencar


# ── 5. invarian yang WAJIB dipertahankan ─────────────────────────────
def test_tingkat_pn_tetap_menang_mutlak():
    """Query yang merupakan bagian PN tetap 1000+ dan mengalahkan skor nama."""
    skor_pn, cocok = ai._relevansi("APAPUN", "WG1234567890", "wg12345", ["wg12345"])
    assert skor_pn >= 1000
    assert cocok is None
    assert _skor("TURBOCHARGER ASSEMBLY", "turbocharger")[0] < 1000


def test_skor_nama_tak_pernah_menabrak_tingkat_pn():
    """Berapa pun banyaknya kata langka yang cocok, skor nama < 1000."""
    nama = "TURBOCHARGER CRANKSHAFT SEAL BOLT GASKET"
    q = "turbocharger crankshaft seal bolt gasket"
    assert _skor(nama, q)[0] < 1000


def test_tanpa_kecocokan_skor_nol_dan_cocok_none():
    assert _skor("RADIATOR CORE", "turbocharger") == (0, None)


def test_nama_kosong_aman():
    assert _skor("", "seal") == (0, None)
    assert _skor("   ", "seal") == (0, None)


def test_query_kosong_aman():
    assert _skor("OIL SEAL", "", terms=[])[0] == 0


# ── 6. ekspansi sinonim diredam di bawah kata asli user ──────────────
def test_kata_asli_user_menang_atas_hasil_ekspansi():
    """Dua part sama-sama cocok satu kata dengan IDF sama; yang cocok dengan
    kata yang BENAR-BENAR diketik user harus menang atas yang cuma cocok lewat
    terjemahan sinonim."""
    asli = ai._relevansi("CRANKSHAFT ASSY", "PN1", "crankshaft",
                         ["crankshaft", "kruk as"])[0]
    ekspansi = ai._relevansi("CRANKSHAFT ASSY", "PN1", "kruk as",
                             ["kruk as", "crankshaft"])[0]
    assert asli > ekspansi
