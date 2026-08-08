"""bengkel_resmi — direktori bengkel/service station resmi Sinotruk dari SIMS
(endpoint yang sebelumnya nol referensi di kode). Jaringan di-mock.

Yang dikunci di sini terutama KEJUJURAN: gagal mengambil daftar ≠ "tidak ada
bengkel", dan field yang tak diisi SIMS tak boleh muncul sebagai sel kosong yang
terbaca sebagai fakta.
"""
import pytest

from app.services import sims
from app.services import ai_assistant as ai

USER = {"username": "budi", "role": "user"}

ROWS = [
    {"stationCode": "IDZ002", "stationName": "PT. GRAND MOTORS INDONESIA",
     "stationAddress": "GREEN SEDAYU BIZ PARK CAKUNG, JAKARTA TIMUR",
     "stationStatus": "s-station-status-use", "hotLine": "021-1234567",
     "email": "a@b.com", "latitude": -6.13, "longitude": 106.93,
     "countryName": "Indonesia"},
    {"stationCode": "IDZ010", "stationName": "PT SURABAYA TRUK",
     "stationAddress": "Jl. Rungkut, Surabaya", "stationStatus": "s-station-status-stop",
     "hotLine": "", "email": None},
    {"stationCode": "FJZ001", "stationName": "FIJI MOTORS",
     "stationAddress": "Suva", "stationStatus": "s-station-status-use"},
]


def _mock(monkeypatch, rows):
    monkeypatch.setattr(ai.sims, "stations", lambda: rows)


# ── penyaringan & bentuk hasil ──────────────────────────────────────────────
def test_tanpa_kata_kunci_kembalikan_semua(monkeypatch):
    _mock(monkeypatch, ROWS)
    r = ai._t_bengkel_resmi({}, USER)
    assert r["found"] and r["jumlah_total"] == 3 and r["jumlah_cocok"] == 3


def test_saring_kota(monkeypatch):
    _mock(monkeypatch, ROWS)
    r = ai._t_bengkel_resmi({"kata_kunci": "surabaya"}, USER)
    assert r["jumlah_cocok"] == 1
    assert r["bengkel"][0]["kode"] == "IDZ010"


def test_saring_cocok_ke_alamat_dan_kode(monkeypatch):
    _mock(monkeypatch, ROWS)
    assert ai._t_bengkel_resmi({"kata_kunci": "jakarta"}, USER)["jumlah_cocok"] == 1
    assert ai._t_bengkel_resmi({"kata_kunci": "idz"}, USER)["jumlah_cocok"] == 2


def test_argumen_kota_diterima_sbg_alias(monkeypatch):
    _mock(monkeypatch, ROWS)
    assert ai._t_bengkel_resmi({"kota": "suva"}, USER)["jumlah_cocok"] == 1


def test_status_diterjemahkan(monkeypatch):
    _mock(monkeypatch, ROWS)
    r = ai._t_bengkel_resmi({"kata_kunci": "idz"}, USER)
    st = {b["kode"]: b["status"] for b in r["bengkel"]}
    assert st == {"IDZ002": "aktif", "IDZ010": "berhenti"}


def test_field_kosong_tidak_muncul(monkeypatch):
    """Telepon/email yang tak diisi SIMS TAK boleh jadi kunci kosong — model bisa
    menyajikannya sebagai '-' yang terbaca 'sudah dicek, memang tak punya'."""
    _mock(monkeypatch, ROWS)
    r = ai._t_bengkel_resmi({"kata_kunci": "surabaya"}, USER)
    b = r["bengkel"][0]
    assert "telepon" not in b and "email" not in b and "koordinat" not in b


def test_koordinat_digabung_bila_ada(monkeypatch):
    _mock(monkeypatch, ROWS)
    b = ai._t_bengkel_resmi({"kata_kunci": "jakarta"}, USER)["bengkel"][0]
    assert b["koordinat"] == "-6.13,106.93"
    assert b["telepon"] == "021-1234567"


def test_tak_ada_yang_cocok_bukan_error(monkeypatch):
    _mock(monkeypatch, ROWS)
    r = ai._t_bengkel_resmi({"kata_kunci": "medan"}, USER)
    assert r["found"] is False and r["jumlah_cocok"] == 0
    assert r["jumlah_total"] == 3            # konteks jujur utk model
    assert "error" not in r


def test_plafon_dilaporkan_terbuka(monkeypatch):
    _mock(monkeypatch, [{**ROWS[0], "stationCode": f"X{i:03d}"} for i in range(50)])
    r = ai._t_bengkel_resmi({}, USER)
    assert len(r["bengkel"]) == ai._MAX_BENGKEL
    assert r["terpotong"] == 50 - ai._MAX_BENGKEL


# ── kejujuran gagal-ambil ───────────────────────────────────────────────────
def test_gagal_ambil_bukan_tidak_ada_bengkel(monkeypatch):
    monkeypatch.setattr(ai.sims, "stations", lambda: None)
    r = ai._t_bengkel_resmi({}, USER)
    assert r["_cek_tak_lengkap"] is True
    assert "BUKAN" in r["error"]
    assert "bengkel" not in r


def test_catatan_larang_simpulkan_tak_ada_bengkel(monkeypatch):
    _mock(monkeypatch, ROWS)
    r = ai._t_bengkel_resmi({}, USER)
    assert "jangan mengarang" in r["catatan"].lower()
    assert list(r)[-1] == "catatan"


# ── lapisan service sims.stations ───────────────────────────────────────────
def test_stations_cache_dan_none_saat_gagal(monkeypatch):
    monkeypatch.setattr(sims, "_SIMS_OK", True)
    monkeypatch.setitem(sims._station_cache, "rows", None)
    monkeypatch.setitem(sims._station_cache, "at", 0.0)
    panggil = []

    class F:
        @staticmethod
        def fetch_stations():
            panggil.append(1)
            return ROWS
    monkeypatch.setattr(sims, "_sf", F)
    assert len(sims.stations()) == 3
    assert len(sims.stations()) == 3
    assert len(panggil) == 1                 # panggilan kedua dari cache


def test_stations_hasil_kosong_tak_di_cache_sbg_fakta(monkeypatch):
    """[] dari fetcher bisa berarti GAGAL — jangan dikunci jadi 'tak ada bengkel'."""
    monkeypatch.setattr(sims, "_SIMS_OK", True)
    monkeypatch.setitem(sims._station_cache, "rows", None)
    monkeypatch.setitem(sims._station_cache, "at", 0.0)
    monkeypatch.setattr(sims, "_sf", type("F", (), {"fetch_stations": staticmethod(lambda: [])}))
    assert sims.stations() is None


def test_stations_exception_kembalikan_cache_lama(monkeypatch):
    monkeypatch.setattr(sims, "_SIMS_OK", True)
    monkeypatch.setitem(sims._station_cache, "rows", ROWS)
    monkeypatch.setitem(sims._station_cache, "at", 0.0)     # kedaluwarsa

    def boom():
        raise RuntimeError("jaringan")
    monkeypatch.setattr(sims, "_sf", type("F", (), {"fetch_stations": staticmethod(boom)}))
    assert sims.stations() == ROWS


def test_tool_terdaftar_di_spec_dan_dispatch():
    assert ai._DISPATCH["bengkel_resmi"] is ai._t_bengkel_resmi
    assert "bengkel_resmi" in [s["function"]["name"] for s in ai._tool_specs(USER)]
