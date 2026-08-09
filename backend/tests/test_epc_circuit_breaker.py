"""Pemutus arus EPC + kejujuran sebab kegagalan Weichai.

Gangguan nyata 9 Agu 2026: nginx EPC sehat (jawab '/' dalam 0,5 dtk) tapi SEMUA
jalur /api/rest/* diteruskan ke aplikasi yang macet, dan nginx TIDAK membalas
504 — ia hanya menahan sambungan. Satu-satunya rem adalah timeout kita sendiri:
3 percobaan × 30 dtk ≈ 93 dtk PER PANGGILAN. Satu giliran user membakar 338 dtk.

Dua hal yang dikunci di sini:
 1. Sesudah beberapa kegagalan JARINGAN beruntun, jalur EPC gagal CEPAT.
 2. Kegagalan itu dilaporkan sebagai 'gagal memeriksa', BUKAN 'datanya tak ada'.
"""
import pytest

from app.services import epc_bom, epc_weichai


@pytest.fixture(autouse=True)
def _reset():
    epc_bom.circuit_reset()
    yield
    epc_bom.circuit_reset()


class _Jawab:
    """Respons EPC yang sukses."""
    @staticmethod
    def json():
        return {"success": True, "data": {"ok": 1}}


def _mati(*a, **k):
    raise OSError("read timed out")


def _hidup(*a, **k):
    return _Jawab()


def _panggil(n=1):
    for _ in range(n):
        r = epc_bom._get("http://x/api", {}, timeout=1, retries=1)
    return r


@pytest.fixture
def _token(monkeypatch):
    monkeypatch.setattr(epc_bom, "_token", lambda: "TOKEN-UJI")


# ── memutus arus ────────────────────────────────────────────────────────────
def test_arus_terputus_sesudah_gagal_beruntun(monkeypatch, _token):
    monkeypatch.setattr(epc_bom.requests, "get", _mati)
    for _ in range(epc_bom._CB_AMBANG):
        assert epc_bom._get("http://x/api", {}, timeout=1, retries=1)["_err"] == "network"
    assert epc_bom.circuit_state()["terbuka"] is True

    # Sesudah terputus: TIDAK menyentuh jaringan sama sekali.
    def _jangan(*a, **k):
        pytest.fail("jaringan tak boleh disentuh saat arus terputus")
    monkeypatch.setattr(epc_bom.requests, "get", _jangan)
    r = epc_bom._get("http://x/api", {}, timeout=1, retries=1)
    assert r["_err"] == "network" and r.get("_circuit") is True


def test_belum_cukup_gagal_belum_memutus(monkeypatch, _token):
    monkeypatch.setattr(epc_bom.requests, "get", _mati)
    for _ in range(epc_bom._CB_AMBANG - 1):
        epc_bom._get("http://x/api", {}, timeout=1, retries=1)
    assert epc_bom.circuit_state()["terbuka"] is False


def test_sukses_menutup_kembali(monkeypatch, _token):
    monkeypatch.setattr(epc_bom.requests, "get", _mati)
    for _ in range(epc_bom._CB_AMBANG - 1):
        epc_bom._get("http://x/api", {}, timeout=1, retries=1)
    monkeypatch.setattr(epc_bom.requests, "get", _hidup)
    epc_bom._get("http://x/api", {}, timeout=1, retries=1)
    assert epc_bom.circuit_state()["gagal_beruntun"] == 0


def test_error_API_bukan_jaringan_TIDAK_memutus(monkeypatch, _token):
    """EPC menjawab tapi menolak = aplikasi HIDUP. Memutus arus di sini akan
    membutakan kita dari server yang sebenarnya sehat."""
    class _Tolak:
        @staticmethod
        def json():
            return {"success": False, "code": "500", "message": "boom"}
    monkeypatch.setattr(epc_bom.requests, "get", lambda *a, **k: _Tolak())
    for _ in range(epc_bom._CB_AMBANG + 2):
        assert epc_bom._get("http://x/api", {}, timeout=1, retries=1)["_err"] == "api"
    assert epc_bom.circuit_state()["terbuka"] is False


def test_token_expired_TIDAK_memutus(monkeypatch, _token):
    class _Kedaluwarsa:
        @staticmethod
        def json():
            return {"success": False, "code": "110025", "message": "Not has role!"}
    monkeypatch.setattr(epc_bom.requests, "get", lambda *a, **k: _Kedaluwarsa())
    for _ in range(epc_bom._CB_AMBANG + 2):
        epc_bom._get("http://x/api", {}, timeout=1, retries=1)
    assert epc_bom.circuit_state()["terbuka"] is False


# ── penjajakan (half-open) ──────────────────────────────────────────────────
def test_jeda_habis_mengizinkan_SATU_penjajakan(monkeypatch, _token):
    monkeypatch.setattr(epc_bom.requests, "get", _mati)
    for _ in range(epc_bom._CB_AMBANG):
        epc_bom._get("http://x/api", {}, timeout=1, retries=1)
    # Majukan waktu: jeda dianggap habis.
    epc_bom._cb["buka_sampai"] = epc_bom.time.monotonic() - 0.01

    n = {"i": 0}

    def _hitung(*a, **k):
        n["i"] += 1
        raise OSError("masih mati")
    monkeypatch.setattr(epc_bom.requests, "get", _hitung)
    epc_bom._get("http://x/api", {}, timeout=1, retries=1)   # penjajakan
    assert n["i"] == 1
    epc_bom._get("http://x/api", {}, timeout=1, retries=1)   # ditahan lagi
    assert n["i"] == 1, "hanya SATU penjajakan yang boleh lewat"


def test_penjajakan_berhasil_membuka_jalan(monkeypatch, _token):
    monkeypatch.setattr(epc_bom.requests, "get", _mati)
    for _ in range(epc_bom._CB_AMBANG):
        epc_bom._get("http://x/api", {}, timeout=1, retries=1)
    epc_bom._cb["buka_sampai"] = epc_bom.time.monotonic() - 0.01
    monkeypatch.setattr(epc_bom.requests, "get", _hidup)
    assert "data" in epc_bom._get("http://x/api", {}, timeout=1, retries=1)
    assert epc_bom.circuit_state()["terbuka"] is False
    assert epc_bom._get("http://x/api", {}, timeout=1, retries=1).get("_circuit") is None


def test_jatah_penjajakan_TIDAK_tersangkut(monkeypatch):
    """⚠️ Bug yang nyaris lolos: refresh_token mengambil jatah penjajakan lalu
    keluar tanpa melapor → bendera 'menjajaki' tersangkut True dan MEMBLOKIR
    seluruh EPC selamanya. Jalur keluar apa pun wajib mengembalikan jatah."""
    epc_bom._cb.update({"gagal": epc_bom._CB_AMBANG,
                        "buka_sampai": epc_bom.time.monotonic() - 0.01})
    monkeypatch.setattr(epc_bom, "_sims_fetcher", lambda: (_ for _ in ()).throw(OSError("sims mati")))
    epc_bom.refresh_token()
    assert epc_bom._cb["menjajaki"] is False, "jatah penjajakan tersangkut"


def test_refresh_token_dilewati_saat_arus_terputus(monkeypatch):
    """Saat EPC macet, refresh pasti sia-sia — dan ia menyeret login SIMS penuh."""
    epc_bom._cb.update({"gagal": epc_bom._CB_AMBANG,
                        "buka_sampai": epc_bom.time.monotonic() + 60})
    monkeypatch.setattr(epc_bom, "_sims_fetcher",
                        lambda: pytest.fail("login SIMS tak boleh dijalankan"))
    assert epc_bom.refresh_token() == ""


# ── kejujuran sebab (Weichai) ───────────────────────────────────────────────
def test_sino_mati_TIDAK_diklaim_sebagai_bukan_weichai(monkeypatch):
    """⛔ 'gagal memeriksa' TIDAK BOLEH jadi 'unit ini tanpa mesin Weichai'."""
    monkeypatch.setattr(epc_weichai, "_sino_getparam", lambda f: ("", "network"))
    br = epc_weichai._bridge("SJ346500")
    assert br["found"] is False
    assert br["reason"] == "sino_down"
    assert br["gagal_dicek"] is True
    assert "tidak punya link" not in br["message"].lower()
    assert "belum diketahui" in br["message"].lower()


def test_param_kosong_TANPA_sebab_tetap_bukan_weichai(monkeypatch):
    """Kebalikannya harus tetap jujur: benar-benar tak ada link → katakan itu."""
    monkeypatch.setattr(epc_weichai, "_sino_getparam", lambda f: ("", ""))
    br = epc_weichai._bridge("SJ346500")
    assert br["reason"] == "no_link"
    assert "tidak punya link" in br["message"].lower()


def test_pesan_no_session_tak_menyuruh_hal_mustahil(monkeypatch):
    """Saat sebabnya Sinotruk macet, ⛔ jangan menyuruh 'cek unit Weichai dulu'
    — jalur itu justru yang sedang mati."""
    with epc_weichai._lock:
        epc_weichai._tok_cache["sebab"] = "sino_down"
    out = epc_weichai._mint_gagal()
    assert out["reason"] == "sino_down"
    assert out["gagal_dicek"] is True
    assert "cek piston unit" not in out["message"]
    assert "sinotruk" in out["message"].lower()


def test_pesan_no_session_asli_tetap_ada(monkeypatch):
    with epc_weichai._lock:
        epc_weichai._tok_cache["sebab"] = ""
    out = epc_weichai._mint_gagal()
    assert out["reason"] == "no_session"
    assert "belum aktif" in out["message"].lower()


# ── token tak boleh bocor ke log ────────────────────────────────────────────
def test_token_sims_tidak_dicetak_ke_stdout():
    """Log docker menyimpan stdout; badan respons login berisi token utuh."""
    import pathlib
    for nama in ("sims_fetcher.py", "sims_price_fetcher.py"):
        p = pathlib.Path(__file__).resolve().parents[1] / "shared" / nama
        isi = p.read_text(encoding="utf-8")
        assert "Login response: {resp.status_code} | {resp.text" not in isi, nama
        assert 'Token: {token[:60]}' not in isi, nama
