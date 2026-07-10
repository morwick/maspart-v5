"""Riwayat login: label perangkat, ringkasan akun dipakai ramai, IP anti-palsu."""
import pytest

from app.core import ratelimit
from app.services import login_history as lh
from app.services import presence


# ── User-Agent → label perangkat ─────────────────────────────────────────────

@pytest.mark.parametrize("ua,harap", [
    ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
     "Chrome/120.0 Safari/537.36", "Chrome di Windows"),
    # Edge menyamar sbg Chrome+Safari → harus tetap terbaca Edge.
    ("Mozilla/5.0 (Windows NT 10.0) AppleWebKit/537.36 Chrome/120.0 Safari/537.36 Edg/120.0",
     "Edge di Windows"),
    # Safari iPhone asli (tak ada token Chrome).
    ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
     "Version/17.0 Mobile/15E148 Safari/604.1", "Safari di iPhone"),
    # Chrome di iOS memakai token CriOS.
    ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) CriOS/120.0 Mobile/15E148 Safari/604.1",
     "Chrome di iPhone"),
    ("Mozilla/5.0 (Linux; Android 13; SM-A536E) AppleWebKit/537.36 Chrome/120.0 Mobile Safari/537.36",
     "Chrome di Android"),
    ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 Version/17.0 Safari/605.1.15",
     "Safari di Mac"),
    ("", "Tidak dikenal"),
    ("curl/8.4.0", "Tidak dikenal"),
])
def test_device_label(ua, harap):
    assert lh.device_label(ua) == harap


# ── IP klien: hop tepercaya dari KANAN (XFF palsu tak bisa menipu) ───────────

class _Req:
    def __init__(self, xff=None, peer="172.16.1.3"):
        self.headers = {"x-forwarded-for": xff} if xff else {}
        self.client = type("C", (), {"host": peer})()


def test_ip_ambil_hop_traefik_bukan_klaim_klien():
    # Klien menyuntik '1.2.3.4'; Traefik menambahkan IP asli 203.0.113.9 di kanan.
    r = _Req("1.2.3.4, 203.0.113.9")
    assert ratelimit.client_ip(r) == "203.0.113.9"


def test_ip_tanpa_xff_pakai_peer():
    assert ratelimit.client_ip(_Req(None, peer="10.0.0.5")) == "10.0.0.5"


# ── Ringkasan "akun dipakai ramai" ───────────────────────────────────────────

_WIN = "Mozilla/5.0 (Windows NT 10.0) AppleWebKit/537.36 Chrome/120.0 Safari/537.36"
_IPH = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605 Version/17 Safari/604"


def _rows(monkeypatch, rows):
    monkeypatch.setattr(lh, "_fetch", lambda params: rows)


def test_sharing_summary_hitung_ip_dan_perangkat_unik(monkeypatch):
    # Kolom SESUAI TABEL NYATA: ip_address + user_agent (bukan ip/device).
    _rows(monkeypatch, [
        {"created_at": "2026-07-10T09:00:00Z", "username": "budi", "ip_address": "1.1.1.1", "user_agent": _WIN},
        {"created_at": "2026-07-09T09:00:00Z", "username": "budi", "ip_address": "2.2.2.2", "user_agent": _IPH},
        {"created_at": "2026-07-08T09:00:00Z", "username": "budi", "ip_address": "1.1.1.1", "user_agent": _WIN},
        {"created_at": "2026-07-08T08:00:00Z", "username": "ani", "ip_address": "3.3.3.3", "user_agent": _WIN},
    ])
    s = lh.sharing_summary(30)
    assert s["budi"]["ip_count"] == 2 and s["budi"]["device_count"] == 2
    assert s["budi"]["login_count"] == 3
    # Baris terurut terbaru dulu → login terakhir = baris pertama.
    assert s["budi"]["last_ip"] == "1.1.1.1"
    assert s["budi"]["last_device"] == "Chrome di Windows"   # diturunkan dari user_agent
    assert s["budi"]["last_at"] == "2026-07-10T09:00:00Z"
    assert s["ani"]["ip_count"] == 1


def test_sharing_summary_abaikan_user_agent_tak_dikenal(monkeypatch):
    """UA aneh (curl/bot) tak boleh ikut menaikkan hitungan 'perangkat berbeda'."""
    _rows(monkeypatch, [
        {"created_at": "2026-07-10T09:00:00Z", "username": "budi", "ip_address": "1.1.1.1", "user_agent": "curl/8"},
        {"created_at": "2026-07-09T09:00:00Z", "username": "budi", "ip_address": "1.1.1.1", "user_agent": _WIN},
    ])
    assert lh.sharing_summary(30)["budi"]["device_count"] == 1


def test_sharing_summary_tabel_belum_ada_tidak_meledak(monkeypatch):
    _rows(monkeypatch, None)        # _fetch gagal → None
    assert lh.sharing_summary(30) == {}


def test_tabel_kosong_bukan_berarti_tabel_tak_ada(monkeypatch):
    """Regresi: dulu panel menyuruh admin membuat tabel padahal tabelnya sudah
    ada, hanya belum ada baris. [] = ada tapi kosong; None = benar-benar absen."""
    _rows(monkeypatch, [])
    assert lh.table_ready() is True
    _rows(monkeypatch, None)
    assert lh.table_ready() is False


def test_list_logins_turunkan_device_dari_user_agent(monkeypatch):
    _rows(monkeypatch, [
        {"id": 1, "created_at": "2026-07-10T09:00:00Z", "username": "budi",
         "ip_address": "1.1.1.1", "user_agent": _IPH, "success": True},
    ])
    row = lh.list_logins()[0]
    assert row["ip"] == "1.1.1.1" and row["device"] == "Safari di iPhone"


def test_record_kirim_kolom_yang_ada_di_tabel(monkeypatch):
    """Kirim ip_address/user_agent/success — BUKAN ip/device/role (kolom itu
    tak ada; dulu inilah sebab semua insert ditolak 400 diam-diam)."""
    dikirim = {}

    class _R:
        status_code = 201

    def _post(url, headers=None, json=None, timeout=None):
        dikirim.update(json or {})
        return _R()

    monkeypatch.setattr(lh.requests, "post", _post)
    assert lh.record("budi", "user", "1.1.1.1", _WIN) is True
    assert set(dikirim) == {"username", "success", "reason", "ip_address", "user_agent", "created_at"}
    assert dikirim["ip_address"] == "1.1.1.1" and dikirim["success"] is True


def test_record_tidak_melempar_saat_supabase_mati(monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("supabase down")
    monkeypatch.setattr(lh.requests, "post", _boom)
    assert lh.record("budi", "user", "1.1.1.1", "curl/8") is False   # diam, bukan crash


def test_record_tolak_username_kosong():
    assert lh.record("", "user", "1.1.1.1", "x") is False


# ── presence menyimpan IP & perangkat terakhir ───────────────────────────────

def test_presence_simpan_ip_dan_device():
    presence.mark_login("budi", ip="203.0.113.9", device="Chrome di Windows")
    p = presence.get("budi")
    assert p["last_ip"] == "203.0.113.9" and p["last_device"] == "Chrome di Windows"
    assert p["online"] is True


def test_presence_user_tak_dikenal_aman():
    p = presence.get("belum-pernah-login")
    assert p["last_ip"] is None and p["last_device"] is None and p["online"] is False


def test_presence_touch_tak_menghapus_ip():
    """Aktivitas biasa (touch) tak boleh mengosongkan IP dari login."""
    presence.mark_login("ani", ip="8.8.8.8", device="Safari di Mac")
    presence.touch("ani")
    assert presence.get("ani")["last_ip"] == "8.8.8.8"
