"""
Service autentikasi — decoupled dari Streamlit.

Mirror dari supabase.py::authenticate_from_supabase:
  1. ambil user aktif dari Supabase
  2. verifikasi password (bcrypt / legacy)
  3. return {username, role} kalau valid
"""
from __future__ import annotations

import logging
from typing import Optional

from ..core.security import hash_password, verify_password
from .supabase_client import fetch_active_user, get_user_gudang, update_user

logger = logging.getLogger("maspart.auth")


def _upgrade_legacy_password(username_norm: str, plain: str) -> None:
    """Migrasi bertahap: saat user login memakai password plaintext legacy,
    hash-kan ke bcrypt dan HAPUS kolom plaintext. Best-effort — kegagalan tak
    boleh menggagalkan login. Menghilangkan cleartext dari DB tanpa mengunci
    user yang belum pernah login ulang (menuju penghapusan total jalur legacy)."""
    h = hash_password(plain)
    if not h:
        return
    try:
        ok, msg = update_user(username_norm, {"password_hash": h, "password": None})
        if not ok:
            logger.warning("gagal upgrade password legacy %s: %s", username_norm, msg)
    except Exception:
        logger.exception("error upgrade password legacy %s", username_norm)


def authenticate(username: str, password: str) -> Optional[dict]:
    if not username or not password:
        return None

    row = fetch_active_user(username)
    if not row:
        return None

    stored_hash = row.get("password_hash") or ""
    legacy_plain = row.get("password") or ""
    ok = verify_password(password, stored_hash=stored_hash, legacy_plain=legacy_plain)
    if not ok:
        return None

    username_norm = str(row.get("username", "")).strip().lower()
    # Login sukses via plaintext legacy (belum ada hash bcrypt) → upgrade sekarang.
    if not stored_hash and legacy_plain:
        _upgrade_legacy_password(username_norm, password)
    role = row.get("role") or "user"
    # Lokasi gudang hanya relevan untuk pembeli (resilient bila kolom belum ada).
    gudang = get_user_gudang(username_norm) if role == "pembeli" else None
    return {
        "username": username_norm,
        "role": role,
        "gudang": gudang,
    }
