"""
Keamanan: verifikasi password + JWT.

Logika verifikasi password DISALIN PERSIS dari supabase.py::_verify_password
agar perilaku login identik dengan app Streamlit (bcrypt + fallback legacy
plaintext via hmac.compare_digest).
"""
from __future__ import annotations

import hmac
from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt

try:
    import bcrypt
    _HAS_BCRYPT = True
except ImportError:  # pragma: no cover
    _HAS_BCRYPT = False

from .config import get_settings


# ── Password ─────────────────────────────────────────────────────────
def hash_password(plain: str) -> str:
    """Hash bcrypt (rounds=10), identik supabase.py. Return '' kalau gagal."""
    if not plain or not _HAS_BCRYPT:
        return ""
    try:
        return bcrypt.hashpw(plain.strip().encode("utf-8"), bcrypt.gensalt(rounds=10)).decode("utf-8")
    except Exception:
        return ""


def verify_password(plain: str, stored_hash: str = "", legacy_plain: str = "") -> bool:
    """
    Verifikasi password — identik dengan supabase.py::_verify_password.
      - Jika stored_hash (bcrypt) ada → bcrypt.checkpw
      - Jika hanya legacy_plain → hmac.compare_digest
    """
    if not plain:
        return False
    if stored_hash and _HAS_BCRYPT:
        try:
            hash_b = stored_hash.encode("utf-8")
            if bcrypt.checkpw(plain.strip().encode("utf-8"), hash_b):
                return True
            if plain != plain.strip():
                return bcrypt.checkpw(plain.encode("utf-8"), hash_b)
            return False
        except Exception:
            return False
    if legacy_plain:
        return hmac.compare_digest(plain.strip(), legacy_plain.strip())
    return False


# ── JWT ──────────────────────────────────────────────────────────────
def create_access_token(username: str, role: str) -> str:
    s = get_settings()
    now = datetime.now(timezone.utc)
    payload = {
        "sub": username,
        "role": role,
        "iat": now,
        "exp": now + timedelta(minutes=s.jwt_expire_minutes),
    }
    return jwt.encode(payload, s.jwt_secret, algorithm=s.jwt_algorithm)


def decode_access_token(token: str) -> Optional[dict]:
    s = get_settings()
    try:
        return jwt.decode(token, s.jwt_secret, algorithms=[s.jwt_algorithm])
    except jwt.PyJWTError:
        return None
