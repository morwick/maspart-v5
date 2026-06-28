"""
Service autentikasi — decoupled dari Streamlit.

Mirror dari supabase.py::authenticate_from_supabase:
  1. ambil user aktif dari Supabase
  2. verifikasi password (bcrypt / legacy)
  3. return {username, role} kalau valid
"""
from __future__ import annotations

from typing import Optional

from ..core.security import verify_password
from .supabase_client import fetch_active_user, get_user_gudang


def authenticate(username: str, password: str) -> Optional[dict]:
    if not username or not password:
        return None

    row = fetch_active_user(username)
    if not row:
        return None

    ok = verify_password(
        password,
        stored_hash=(row.get("password_hash") or ""),
        legacy_plain=(row.get("password") or ""),
    )
    if not ok:
        return None

    username_norm = str(row.get("username", "")).strip().lower()
    role = row.get("role") or "user"
    # Lokasi gudang hanya relevan untuk pembeli (resilient bila kolom belum ada).
    gudang = get_user_gudang(username_norm) if role == "pembeli" else None
    return {
        "username": username_norm,
        "role": role,
        "gudang": gudang,
    }
