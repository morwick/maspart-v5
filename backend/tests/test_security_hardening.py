"""Regresi perbaikan keamanan (audit 2026-07-04):
- config: JWT secret default = FATAL; APP_ENV tak dikenal → fail-closed prod.
- ratelimit: X-Forwarded-For diambil dari KANAN (anti-spoof), bukan kiri.
- deps: DB down → role privileged tak dipercaya dari token (turun ke 'user').
- ai_export: guard formula/CSV injection (= + - @ di awal sel).
- auth: login via plaintext legacy → upgrade ke bcrypt + hapus plaintext.
"""
from types import SimpleNamespace

import pytest

from app.core import ratelimit
from app.core.config import Settings


# ── config: secret & env fail-closed ─────────────────────────────────────────
def test_default_jwt_secret_fatal_di_env_apa_pun():
    s = Settings(jwt_secret="dev-secret-ganti-di-produksi", app_env="dev")
    with pytest.raises(RuntimeError):
        s.validate_security()


def test_jwt_secret_kosong_fatal():
    with pytest.raises(RuntimeError):
        Settings(jwt_secret="", app_env="dev").validate_security()


def test_env_tak_dikenal_diperlakukan_produksi():
    assert Settings(app_env="").is_production is True          # lupa set → prod
    assert Settings(app_env="staging").is_production is True   # tak dikenal → prod
    assert Settings(app_env="dev").is_production is False
    assert Settings(app_env="prod").is_production is True


def test_secret_kuat_lolos():
    s = Settings(jwt_secret="x" * 40, app_env="prod", payment_api_key="")
    assert s.validate_security() == []


# ── ratelimit: XFF diambil dari kanan ────────────────────────────────────────
def _req(xff=None, peer="10.0.0.1"):
    headers = {"x-forwarded-for": xff} if xff else {}
    return SimpleNamespace(headers=headers, client=SimpleNamespace(host=peer))


def test_xff_ambil_entri_kanan_bukan_kiri(monkeypatch):
    monkeypatch.setattr(ratelimit, "get_settings",
                        lambda: SimpleNamespace(trusted_proxies=1))
    # klien memalsukan hop kiri; proxy menambahkan IP asli di kanan → pakai kanan
    assert ratelimit._client_ip(_req("1.1.1.1, 203.0.113.9")) == "203.0.113.9"


def test_xff_spoof_tak_ganti_bucket(monkeypatch):
    monkeypatch.setattr(ratelimit, "get_settings",
                        lambda: SimpleNamespace(trusted_proxies=1))
    # dua request, hop kiri (spoof) beda tapi IP asli (kanan) sama → IP sama
    a = ratelimit._client_ip(_req("9.9.9.9, 203.0.113.9"))
    b = ratelimit._client_ip(_req("8.8.8.8, 203.0.113.9"))
    assert a == b == "203.0.113.9"


def test_tanpa_xff_pakai_peer(monkeypatch):
    monkeypatch.setattr(ratelimit, "get_settings",
                        lambda: SimpleNamespace(trusted_proxies=1))
    assert ratelimit._client_ip(_req(None, peer="10.0.0.5")) == "10.0.0.5"


# ── deps: DB down tak menaikkan privilege ────────────────────────────────────
def test_db_down_role_admin_dari_token_diturunkan(monkeypatch):
    from app import deps
    deps._auth_cache.clear()
    monkeypatch.setattr(deps.sb, "fetch_user_role", lambda u: False)  # DB error
    resolved = deps._resolve_user("bob", "admin")
    assert resolved["role"] == "user"          # TIDAK dipercaya admin dari token


def test_db_down_pakai_cache_terverifikasi(monkeypatch):
    from app import deps
    import time as _t
    deps._auth_cache.clear()
    deps._auth_cache["bob"] = (_t.time(), {"username": "bob", "role": "admin"})
    monkeypatch.setattr(deps.sb, "fetch_user_role", lambda u: False)
    assert deps._resolve_user("bob", "admin")["role"] == "admin"  # cache verified


def test_db_ok_role_dari_db(monkeypatch):
    from app import deps
    deps._auth_cache.clear()
    monkeypatch.setattr(deps.sb, "fetch_user_role", lambda u: {"role": "admin"})
    assert deps._resolve_user("bob", "user")["role"] == "admin"


# ── ai_export: guard formula injection ───────────────────────────────────────
@pytest.mark.parametrize("bad", ["=cmd|'/c calc'!A1", "+1+1", "-2+3", "@SUM(A1)",
                                 "=HYPERLINK(\"http://evil\")"])
def test_safe_escape_formula(bad):
    from app.services import ai_export
    out = ai_export._safe(bad)
    assert out == "'" + bad and out.startswith("'")


def test_safe_biarkan_teks_dan_angka():
    from app.services import ai_export
    assert ai_export._safe("WG9114160020") == "WG9114160020"
    assert ai_export._safe("Rp 5.250.000") == "Rp 5.250.000"
    assert ai_export._safe(42) == 42
    assert ai_export._safe(None) is None


def test_safe_bom_tak_menipu():
    from app.services import ai_export
    assert ai_export._safe("﻿=x").startswith("'")  # BOM di depan → tetap ke-escape


# ── auth: upgrade password legacy plaintext ke bcrypt ────────────────────────
def test_login_legacy_plaintext_diupgrade(monkeypatch):
    from app.services import auth
    from app.core import security
    calls = {}
    monkeypatch.setattr(auth, "fetch_active_user", lambda u: {
        "username": "andi", "password_hash": "", "password": "rahasia123",
        "role": "user", "is_active": True})
    monkeypatch.setattr(auth, "get_user_gudang", lambda u: None)

    def fake_update(username, data):
        calls["username"] = username
        calls["data"] = data
        return True, "ok"
    monkeypatch.setattr(auth, "update_user", fake_update)

    res = auth.authenticate("andi", "rahasia123")
    assert res and res["username"] == "andi"
    # password lama di-hash bcrypt & kolom plaintext di-null-kan
    assert calls["username"] == "andi"
    assert calls["data"]["password"] is None
    h = calls["data"]["password_hash"]
    assert h and security.verify_password("rahasia123", stored_hash=h)


def test_login_bcrypt_tak_panggil_upgrade(monkeypatch):
    from app.services import auth
    from app.core import security
    h = security.hash_password("rahasia123")
    monkeypatch.setattr(auth, "fetch_active_user", lambda u: {
        "username": "budi", "password_hash": h, "password": "",
        "role": "user", "is_active": True})
    monkeypatch.setattr(auth, "get_user_gudang", lambda u: None)
    called = {"n": 0}
    monkeypatch.setattr(auth, "update_user",
                        lambda *a, **k: called.__setitem__("n", called["n"] + 1) or (True, "ok"))
    assert auth.authenticate("budi", "rahasia123")["username"] == "budi"
    assert called["n"] == 0  # tak ada upgrade utk yang sudah bcrypt
