"""Kebijakan 'akun hanya 1 perangkat': default MATI, admin kebal, sesi lama terlempar."""
import pytest

from app.core.security import create_access_token, decode_access_token
from app.services import permissions, session_policy

ADMIN = {"username": "admin", "role": "admin"}


@pytest.fixture(autouse=True)
def _bersih():
    session_policy.invalidate_cache()
    yield
    session_policy.invalidate_cache()


def _policy(monkeypatch, rows: dict):
    """rows = isi tabel permissions utk perm_type 'session_policy'."""
    monkeypatch.setattr(session_policy, "perms_load",
                        lambda t: rows if t == "session_policy" else {})


def _stores(monkeypatch, policy: dict, sessions: dict):
    def _load(t):
        return policy if t == "session_policy" else sessions
    monkeypatch.setattr(session_policy, "perms_load", _load)


# ── DEFAULT HARUS MATI (kalau salah, semua akun terkunci begitu fitur naik) ──

def test_tanpa_baris_kebijakan_mati(monkeypatch):
    _policy(monkeypatch, {})
    assert session_policy.enabled("budi", "user") is False


def test_supabase_mati_kebijakan_mati_fail_open(monkeypatch):
    _policy(monkeypatch, {})     # perms_load mengembalikan {} saat error
    assert session_policy.enabled("budi", "user") is False
    assert session_policy.session_valid("budi", "", "user") is True


def test_permissions_effective_sesi_default_kosong(monkeypatch):
    monkeypatch.setattr(permissions, "perms_load", lambda t: {})
    assert permissions.effective("sesi", "budi", "user") == []
    # Bandingkan dgn kind IZIN biasa: tanpa baris → semua aktif.
    assert permissions.effective("column", "budi", "user") == ["col_stok", "col_harga"]


def test_admin_tak_pernah_dibatasi(monkeypatch):
    monkeypatch.setattr(permissions, "perms_load", lambda t: {"__default__": ["single_device"]})
    assert permissions.effective("sesi", "admin", "admin") == []       # kebal
    _policy(monkeypatch, {"admin": ["single_device"]})
    assert session_policy.enabled("admin", "admin") is False           # walau dicentang
    assert session_policy.session_valid("admin", "sid-ngawur", "admin") is True


def test_overview_sesi_default_tak_tercentang(monkeypatch):
    monkeypatch.setattr(permissions, "perms_load", lambda t: {})
    monkeypatch.setattr(permissions, "list_users", lambda: [])
    assert permissions.overview("sesi")["default"] == []
    assert permissions.overview("column")["default"] == ["col_stok", "col_harga"]


# ── Penegakan ───────────────────────────────────────────────────────────────

def test_dicentang_per_user(monkeypatch):
    _policy(monkeypatch, {"budi": ["single_device"]})
    assert session_policy.enabled("budi", "user") is True
    assert session_policy.enabled("ani", "user") is False


def test_dicentang_lewat_default(monkeypatch):
    _policy(monkeypatch, {"__default__": ["single_device"]})
    assert session_policy.enabled("siapa_pun", "user") is True


def test_baris_user_menang_atas_default(monkeypatch):
    _policy(monkeypatch, {"__default__": ["single_device"], "budi": []})
    assert session_policy.enabled("budi", "user") is False


def test_sid_cocok_lolos_sid_lama_ditolak(monkeypatch):
    _stores(monkeypatch, {"budi": ["single_device"]}, {"budi": ["SID-BARU"]})
    assert session_policy.session_valid("budi", "SID-BARU", "user") is True
    assert session_policy.session_valid("budi", "SID-LAMA", "user") is False
    # Token lama tanpa klaim sid (diterbitkan sebelum fitur ini) juga ditolak.
    assert session_policy.session_valid("budi", "", "user") is False


def test_belum_ada_sesi_terikat_fail_open(monkeypatch):
    """Baru dicentang & user belum login lagi → jangan kunci; ikatan menyusul."""
    _stores(monkeypatch, {"budi": ["single_device"]}, {})
    assert session_policy.session_valid("budi", "", "user") is True


def test_kebijakan_mati_sid_apa_pun_lolos(monkeypatch):
    _stores(monkeypatch, {}, {"budi": ["SID-BARU"]})
    assert session_policy.session_valid("budi", "SID-LAMA", "user") is True


def test_start_session_menimpa_sesi_lama(monkeypatch):
    tersimpan = {}

    def _save(perm_type, username, keys):
        tersimpan[(perm_type, username)] = keys
        return True

    monkeypatch.setattr(session_policy, "perms_save", _save)
    monkeypatch.setattr(session_policy, "perms_load",
                        lambda t: {"budi": ["single_device"]} if t == "session_policy" else {})

    sid1 = session_policy.start_session("budi")
    sid2 = session_policy.start_session("budi")
    assert sid1 and sid2 and sid1 != sid2
    assert tersimpan[("active_session", "budi")] == [sid2]     # hanya yang terbaru
    # Cache ikut tersegarkan → perangkat lama LANGSUNG ditolak, tanpa tunggu TTL.
    assert session_policy.session_valid("budi", sid2, "user") is True
    assert session_policy.session_valid("budi", sid1, "user") is False


def test_start_session_gagal_simpan_kembalikan_kosong(monkeypatch):
    monkeypatch.setattr(session_policy, "perms_save", lambda *a: False)
    assert session_policy.start_session("budi") == ""    # fail-open, bukan crash


# ── JWT membawa sid ─────────────────────────────────────────────────────────

def test_token_membawa_sid_bila_diberikan():
    t = create_access_token("budi", "user", "SID-X")
    assert decode_access_token(t)["sid"] == "SID-X"


def test_token_tanpa_sid_tak_punya_klaim_itu():
    assert "sid" not in decode_access_token(create_access_token("budi", "user"))
