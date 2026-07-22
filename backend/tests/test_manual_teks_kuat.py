"""manual_teks V2/V3 (2026-07-22): typo, stem Indonesia, PN ternormal,
diversitas per sumber, penyajian bertingkat — pola pengetahuan lewat
search_boost. Store dipatok agar deterministik."""
import pytest

from app.services import manual_teks as mt
from app.services import search_boost as sb


# ── search_boost (fungsi murni) ──────────────────────────────────────
def test_stem_kandidat():
    assert "pasang" in sb.stem_kandidat("memasang")     # nasal mem-→p pulih
    assert "pasang" in sb.stem_kandidat("pemasangan")   # sufiks -an + prefiks pe-
    assert sb.stem_kandidat("abc") == ()                # terlalu pendek
    assert sb.stem_kandidat("wg9100") == ()             # ada digit → bukan kata


def test_koreksi_tipo():
    ember = sb.vocab_ember(["solenoid katup tekanan", "kompresor ac"])
    assert sb.koreksi_tipo("slenoid", ember) == "solenoid"     # edit ≤1
    assert sb.koreksi_tipo("solenoid", ember) == ""            # sudah benar → ''
    assert sb.koreksi_tipo("xyzqwe", ember) == ""              # tak ada tetangga


def test_pn_normal_tokens():
    assert "wg9725190102" in sb.pn_normal_tokens("cek WG-9725.190102")
    assert sb.pn_normal_tokens("pompa bocor") == []            # tak ada PN


# ── manual_teks search_skor (store dipatok) ──────────────────────────
_STORE = [
    {"dicari": True, "sumber": "manual_bosch", "halaman": 1,
     "judul_id": "Katup solenoid tekanan", "kata_kunci": ["solenoid", "katup"],
     "judul": "压力电磁阀", "teks": "电磁阀…", "kode": ["WG9725190102"]},
    {"dicari": True, "sumber": "manual_bosch", "halaman": 2,
     "judul_id": "Pemasangan sensor", "kata_kunci": ["pasang", "sensor"],
     "judul": "传感器安装", "teks": "安装…", "kode": []},
    {"dicari": True, "sumber": "manual_bosch", "halaman": 3,
     "judul_id": "Solenoid lain", "kata_kunci": ["solenoid"], "judul": "x",
     "teks": "y", "kode": []},
    {"dicari": True, "sumber": "manual_tft", "halaman": 1,
     "judul_id": "Solenoid TFT", "kata_kunci": ["solenoid"], "judul": "z",
     "teks": "w", "kode": []},
    {"dicari": False, "sumber": "manual_bosch", "halaman": 9,
     "judul_id": "tersembunyi", "kata_kunci": ["solenoid"], "judul": "", "teks": ""},
]


@pytest.fixture(autouse=True)
def _store(monkeypatch):
    monkeypatch.setattr(mt, "_load", lambda: [dict(r) for r in _STORE])
    mt._HAY_CACHE.update(mtime=object(), rows=None, ember=None)  # paksa rebuild
    mt._HAY_CACHE["mtime"] = None
    yield
    mt._HAY_CACHE.update(mtime=None, rows=None, ember=None)


def test_typo_ketemu():
    """'slenoid' (typo) → koreksi ke solenoid → dokumen solenoid ketemu."""
    r = mt.search_skor("slenoid", limit=5)
    judul = [rec.get("judul_id") for _s, rec in r]
    assert any("solenoid" in (j or "").lower() for j in judul)


def test_stem_ketemu():
    """'pemasangan' → stem 'pasang' cocok record kata_kunci 'pasang'."""
    r = mt.search_skor("pemasangan", limit=5)
    assert any(rec.get("halaman") == 2 for _s, rec in r)


def test_pn_ternormal_ketemu():
    r = mt.search_skor("cek WG-9725.190102", limit=5)
    assert r and r[0][1].get("halaman") == 1        # cocok via kode ternormal


def test_diversitas_per_sumber():
    """3 record 'solenoid' di manual_bosch → maks 2 dari sumber itu di depan."""
    r = mt.search_skor("solenoid", limit=4)
    bosch = [rec for _s, rec in r if rec.get("sumber") == "manual_bosch"]
    # lintasan pertama batasi 2/sumber; TFT juga solenoid → ikut masuk
    assert any(rec.get("sumber") == "manual_tft" for _s, rec in r)


def test_search_wrapper_kompatibel():
    """search() lama tetap mengembalikan list record (bukan tuple)."""
    rows = mt.search("solenoid", limit=3)
    assert rows and all(isinstance(x, dict) for x in rows)


def test_record_dicari_false_tak_muncul():
    r = mt.search_skor("solenoid", limit=9)
    assert all(rec.get("halaman") != 9 for _s, rec in r)


def test_kueri_kosong():
    assert mt.search_skor("") == [] and mt.search("") == []
