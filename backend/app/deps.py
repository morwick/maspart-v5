"""Dependency FastAPI: ambil user dari JWT Bearer token."""
from __future__ import annotations

import time

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .core.security import decode_access_token
from .services import gudang, presence, session_policy
from .services import supabase_client as sb

_bearer = HTTPBearer(auto_error=True)

# Cache hasil re-cek role/is_active agar tidak query DB tiap request.
# Revokasi (akun dinonaktifkan / role diturunkan) berlaku maksimal _AUTH_TTL detik.
_AUTH_TTL = 30.0
_auth_cache: dict[str, tuple[float, dict | None]] = {}


# Role yang MEMBERI hak istimewa — tak boleh diberikan hanya dari klaim token
# yang belum terverifikasi ke DB (lihat _resolve_user, jalur fail-closed).
_PRIVILEGED_ROLES = frozenset({"admin", "pembeli", "user_cabang"})


def _resolve_user(username: str, token_role: str) -> dict | None:
    """Verifikasi user masih aktif & ambil role TERKINI dari DB (di-cache singkat).
    Return dict {username, role} bila boleh; None bila akun nonaktif/terhapus.

    Saat DB tak terjangkau kita FAIL-CLOSED untuk hak istimewa: identitas tetap
    dilayani (agar akses dasar jalan), tetapi role dari token yang belum
    diverifikasi ulang TIDAK dipercaya untuk menaikkan privilege — role
    diturunkan ke 'user'. Dengan begitu token admin yang basi/di-demote (atau
    token palsu di skenario secret bocor) tak bisa dapat akses admin saat outage.
    Role terverifikasi terakhir dari cache tetap dipakai bila ada."""
    now = time.time()
    hit = _auth_cache.get(username)
    if hit and (now - hit[0]) < _AUTH_TTL:
        return hit[1]
    res = sb.fetch_user_role(username)
    if res is False:  # Supabase error → jangan kunci semua user, tapi jangan naikkan privilege
        if hit:
            return hit[1]  # role terverifikasi terakhir
        safe_role = "user" if token_role in _PRIVILEGED_ROLES else (token_role or "user")
        return {"username": username, "role": safe_role}
    resolved = None if res is None else {"username": username, "role": res.get("role") or token_role}
    _auth_cache[username] = (now, resolved)
    return resolved


def get_current_user(
    cred: HTTPAuthorizationCredentials = Depends(_bearer),
) -> dict:
    payload = decode_access_token(cred.credentials)
    if not payload or not payload.get("sub"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token tidak valid atau kedaluwarsa",
            headers={"WWW-Authenticate": "Bearer"},
        )
    username = str(payload["sub"]).strip().lower()
    resolved = _resolve_user(username, payload.get("role", "user"))
    if resolved is not None:
        presence.touch(resolved["username"])  # catat aktivitas utk panel Monitoring
    if resolved is None:
        # Akun dinonaktifkan/dihapus setelah token diterbitkan → tolak.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Akun tidak aktif atau tidak ditemukan. Silakan login ulang.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    # Kebijakan "hanya 1 perangkat": token dari perangkat lama (sid tak cocok
    # dengan sesi aktif) ditolak. Peran diambil dari DB, bukan klaim token.
    if not session_policy.session_valid(username, str(payload.get("sid") or ""), resolved["role"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Akun ini sedang dipakai di perangkat lain. "
                   "Akun dibatasi hanya 1 perangkat — silakan login ulang.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return {"username": resolved["username"], "role": resolved["role"]}


def require_admin(user: dict = Depends(get_current_user)) -> dict:
    if user.get("role") != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Butuh hak akses admin",
        )
    return user


def require_buyer(user: dict = Depends(get_current_user)) -> dict:
    """Hanya akun pembeli yang boleh mengakses fitur belanja."""
    if user.get("role") != "pembeli":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Hanya akun pembeli yang dapat melakukan pembelian.",
        )
    return user


def require_menu(key: str):
    """Dependency: menu `key` (permissions.MENU_TABS) harus aktif untuk user ini.

    Menu Control dulu hanya menyembunyikan menu di sidebar — endpoint-nya tetap
    terbuka, jadi mematikan centang tidak benar-benar mematikan fiturnya (cukup
    buka URL-nya langsung). Dependency ini yang membuat izin itu nyata.
    Admin selalu lolos (permissions.effective mengembalikan semua key untuk admin).
    """
    def _dep(user: dict = Depends(get_current_user)) -> dict:
        from .services import permissions
        allowed = permissions.effective("menu", user["username"], user.get("role", "user"))
        if key not in allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Menu '{permissions.MENU_TABS.get(key, key)}' dimatikan untuk akun ini.",
            )
        return user

    return _dep


def require_branch(user: dict = Depends(get_current_user)) -> dict:
    """Akun cabang (user yang terpetakan ke 1 gudang). Sisipkan `branch_label`."""
    label = gudang.gudang_for_user(user["username"], user.get("role", "user"))
    if not label:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Akun ini bukan akun cabang.",
        )
    return {**user, "branch_label": gudang.gudang_label(label)}


def require_buyer_ready(user: dict = Depends(require_buyer)) -> dict:
    """Pembeli yang SUDAH memilih lokasi gudang. Sisipkan `gudang` ke user dict."""
    g = sb.get_user_gudang(user["username"])
    if not g:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Pilih lokasi gudang dulu sebelum bisa membeli.",
        )
    return {**user, "gudang": g}
