"""
Klien REST Supabase minimal (tanpa library `supabase`, hanya requests) —
setara dengan helper di supabase.py tapi config-nya dari env, bukan st.secrets.

Fase 1 hanya butuh baca tabel `users` untuk login.
"""
from __future__ import annotations

from typing import Optional

import requests

from ..core.config import get_settings


def _base_url() -> str:
    url = get_settings().supabase_url.rstrip("/")
    if url.endswith("/rest/v1"):
        url = url[: -len("/rest/v1")]
    return url


def _headers() -> dict:
    key = get_settings().supabase_key
    return {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }


def _service_headers(prefer: str = "") -> dict:
    """Header dengan service_key — untuk operasi tulis (RLS-protected)."""
    key = get_settings().storage_key  # service_key, fallback anon
    h = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }
    if prefer:
        h["Prefer"] = prefer
    return h


def _rest_url(table: str) -> str:
    return f"{_base_url()}/rest/v1/{table}"


def download_storage_object(path: str, bucket: str | None = None) -> Optional[bytes]:
    """
    Download objek dari Supabase Storage (mis. stok.xlsx, harga.xlsx).
    Pakai service_key (fallback anon) — setara admin_data_uploader.download_dataset.
    Return bytes atau None.
    """
    s = get_settings()
    if not s.supabase_configured:
        return None
    bucket = bucket or s.supabase_data_bucket
    key = s.storage_key
    url = f"{_base_url()}/storage/v1/object/{bucket}/{path}"
    try:
        resp = requests.get(
            url,
            headers={"apikey": key, "Authorization": f"Bearer {key}"},
            timeout=30,
        )
        if resp.status_code == 200:
            return resp.content
    except Exception:
        pass
    return None


def fetch_part_photos(part_number: str) -> list[str]:
    """
    Ambil daftar URL foto untuk part_number dari tabel `part_photos`.
    Mirror admin_foto_part.get_supabase_photo_urls (filter PN uppercase,
    urut created_at). Return list storage_url (bisa kosong).
    """
    s = get_settings()
    pn = (part_number or "").strip().upper()
    if not s.supabase_configured or not pn:
        return []
    key = s.storage_key
    try:
        resp = requests.get(
            _rest_url("part_photos"),
            headers={"apikey": key, "Authorization": f"Bearer {key}", "Accept": "application/json"},
            params={
                "select": "storage_url",
                "part_number": f"eq.{pn}",
                "order": "created_at.asc",
            },
            timeout=10,
        )
        if resp.status_code == 200:
            return [r["storage_url"] for r in (resp.json() or []) if r.get("storage_url")]
    except Exception:
        pass
    return []


def upload_storage_object(
    path: str,
    data: bytes,
    content_type: str = "application/octet-stream",
    bucket: str | None = None,
) -> tuple[bool, str]:
    """Upload/replace objek di Supabase Storage (x-upsert). Return (ok, pesan)."""
    s = get_settings()
    if not s.supabase_configured:
        return False, "Supabase belum dikonfigurasi"
    bucket = bucket or s.supabase_data_bucket
    key = s.storage_key
    url = f"{_base_url()}/storage/v1/object/{bucket}/{path}"
    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": content_type,
        "x-upsert": "true",
    }
    try:
        resp = requests.post(url, headers=headers, data=data, timeout=120)
        if resp.status_code in (200, 201):
            return True, "ok"
        resp2 = requests.put(url, headers=headers, data=data, timeout=120)
        if resp2.status_code in (200, 201):
            return True, "ok"
        return False, f"{resp.status_code}: {resp.text[:200]}"
    except Exception as e:
        return False, str(e)


def list_users() -> list[dict]:
    """Daftar user aktif: [{username, role}]. Untuk panel admin."""
    s = get_settings()
    if not s.supabase_configured:
        return []
    try:
        resp = requests.get(
            _rest_url(s.supabase_table),
            headers=_headers(),
            params={"select": "username,role", "is_active": "eq.true", "order": "username.asc"},
            timeout=10,
        )
        resp.raise_for_status()
        return [
            {"username": r.get("username", ""), "role": r.get("role") or "user"}
            for r in (resp.json() or [])
            if r.get("username")
        ]
    except Exception:
        return []


# ── User CRUD (manajemen user) ───────────────────────────────────────
def list_users_full() -> list[dict]:
    """Semua user (termasuk nonaktif) untuk manajemen."""
    s = get_settings()
    if not s.supabase_configured:
        return []
    try:
        resp = requests.get(
            _rest_url(s.supabase_table),
            headers=_service_headers(),
            params={"select": "username,role,is_active,created_at", "order": "username.asc"},
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json() or []
    except Exception:
        return []


def create_user(username: str, password_hash: str, role: str) -> tuple[bool, str]:
    s = get_settings()
    if not s.supabase_configured:
        return False, "Supabase belum dikonfigurasi"
    try:
        resp = requests.post(
            _rest_url(s.supabase_table),
            headers=_service_headers("return=minimal"),
            json={
                "username": username.strip().lower(),
                "password_hash": password_hash,
                "role": role.strip().lower(),
                "is_active": True,
            },
            timeout=10,
        )
        if resp.status_code in (200, 201):
            return True, "ok"
        txt = resp.text.lower()
        if "duplicate" in txt or "unique" in txt:
            return False, "Username sudah terdaftar."
        return False, f"{resp.status_code}: {resp.text[:200]}"
    except Exception as e:
        return False, str(e)


def update_user(username: str, data: dict) -> tuple[bool, str]:
    s = get_settings()
    if not s.supabase_configured:
        return False, "Supabase belum dikonfigurasi"
    try:
        resp = requests.patch(
            _rest_url(s.supabase_table),
            headers=_service_headers("return=minimal"),
            params={"username": f"eq.{username.strip().lower()}"},
            json=data,
            timeout=10,
        )
        return (resp.status_code in (200, 204)), (
            "ok" if resp.status_code in (200, 204) else f"{resp.status_code}: {resp.text[:200]}"
        )
    except Exception as e:
        return False, str(e)


def get_user_gudang(username: str) -> Optional[str]:
    """Key gudang terpilih milik user (kolom `users.gudang`), atau None."""
    s = get_settings()
    if not s.supabase_configured:
        return None
    try:
        resp = requests.get(
            _rest_url(s.supabase_table),
            headers=_service_headers(),
            params={
                "username": f"eq.{username.strip().lower()}",
                "select": "gudang",
                "limit": "1",
            },
            timeout=10,
        )
        resp.raise_for_status()
        rows = resp.json() or []
        return (rows[0].get("gudang") or None) if rows else None
    except Exception:
        return None


def set_user_gudang(username: str, gudang_key: str) -> tuple[bool, str]:
    """Set lokasi gudang pilihan user (key). Pakai update_user."""
    return update_user(username, {"gudang": gudang_key})


def delete_user(username: str) -> tuple[bool, str]:
    s = get_settings()
    if not s.supabase_configured:
        return False, "Supabase belum dikonfigurasi"
    try:
        resp = requests.delete(
            _rest_url(s.supabase_table),
            headers=_service_headers("return=minimal"),
            params={"username": f"eq.{username.strip().lower()}"},
            timeout=10,
        )
        return (resp.status_code in (200, 204)), "ok"
    except Exception as e:
        return False, str(e)


# ── Permissions (tabel `permissions`) ────────────────────────────────
_PERMS_TABLE = "permissions"


def perms_load(perm_type: str) -> dict:
    """Baca semua baris perm_type → {username: [keys]} (termasuk __default__)."""
    s = get_settings()
    if not s.supabase_configured:
        return {}
    try:
        resp = requests.get(
            _rest_url(_PERMS_TABLE),
            headers={**_service_headers(), "Accept": "application/json"},
            params={"select": "username,keys", "perm_type": f"eq.{perm_type}"},
            timeout=10,
        )
        resp.raise_for_status()
        out = {}
        for row in resp.json() or []:
            u = row.get("username", "")
            k = row.get("keys", [])
            if u:
                out[u] = k if isinstance(k, list) else list(k or [])
        return out
    except Exception:
        return {}


def perms_save(perm_type: str, username: str, keys: list) -> bool:
    """Upsert (perm_type, username) → keys. SELECT lalu PATCH/POST."""
    s = get_settings()
    if not s.supabase_configured:
        return False
    import time as _t

    payload = {"keys": keys, "updated_at": _t.strftime("%Y-%m-%dT%H:%M:%SZ", _t.gmtime())}
    try:
        r0 = requests.get(
            _rest_url(_PERMS_TABLE),
            headers={**_service_headers(), "Accept": "application/json"},
            params={
                "select": "id",
                "perm_type": f"eq.{perm_type}",
                "username": f"eq.{username}",
                "limit": "1",
            },
            timeout=10,
        )
        r0.raise_for_status()
        if r0.json():
            resp = requests.patch(
                _rest_url(_PERMS_TABLE),
                headers=_service_headers("return=minimal"),
                params={"perm_type": f"eq.{perm_type}", "username": f"eq.{username}"},
                json=payload,
                timeout=10,
            )
            return resp.status_code in (200, 204)
        resp = requests.post(
            _rest_url(_PERMS_TABLE),
            headers=_service_headers("return=minimal"),
            json={"perm_type": perm_type, "username": username, **payload},
            timeout=10,
        )
        return resp.status_code in (200, 201, 204)
    except Exception:
        return False


def perms_delete(perm_type: str, username: str) -> bool:
    s = get_settings()
    if not s.supabase_configured:
        return False
    try:
        resp = requests.delete(
            _rest_url(_PERMS_TABLE),
            headers=_service_headers("return=minimal"),
            params={"perm_type": f"eq.{perm_type}", "username": f"eq.{username}"},
            timeout=10,
        )
        return resp.status_code in (200, 204)
    except Exception:
        return False


# ── Foto Part (bucket part-photos + tabel part_photos) ───────────────
PHOTO_BUCKET = "part-photos"


def photo_public_url(storage_path: str) -> str:
    return f"{_base_url()}/storage/v1/object/public/{PHOTO_BUCKET}/{storage_path}"


def fetch_part_photos_full(part_number: str) -> list[dict]:
    s = get_settings()
    pn = (part_number or "").strip().upper()
    if not s.supabase_configured or not pn:
        return []
    try:
        resp = requests.get(
            _rest_url("part_photos"),
            headers={**_service_headers(), "Accept": "application/json"},
            params={
                "select": "id,file_name,storage_path,storage_url,file_size,created_at",
                "part_number": f"eq.{pn}",
                "order": "created_at.asc",
            },
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json() or []
    except Exception:
        return []


def insert_part_photo(row: dict) -> bool:
    s = get_settings()
    if not s.supabase_configured:
        return False
    try:
        resp = requests.post(
            _rest_url("part_photos"),
            headers={
                **_service_headers("resolution=merge-duplicates,return=minimal"),
            },
            params={"on_conflict": "part_number,file_name"},
            json=row,
            timeout=15,
        )
        return resp.status_code in (200, 201, 204)
    except Exception:
        return False


def get_part_photo(photo_id: str) -> Optional[dict]:
    s = get_settings()
    if not s.supabase_configured:
        return None
    try:
        resp = requests.get(
            _rest_url("part_photos"),
            headers={**_service_headers(), "Accept": "application/json"},
            params={"select": "id,storage_path", "id": f"eq.{photo_id}", "limit": "1"},
            timeout=10,
        )
        resp.raise_for_status()
        rows = resp.json() or []
        return rows[0] if rows else None
    except Exception:
        return None


def delete_part_photo(photo_id: str) -> bool:
    s = get_settings()
    if not s.supabase_configured:
        return False
    try:
        resp = requests.delete(
            _rest_url("part_photos"),
            headers=_service_headers("return=minimal"),
            params={"id": f"eq.{photo_id}"},
            timeout=10,
        )
        return resp.status_code in (200, 204)
    except Exception:
        return False


def delete_storage_object(bucket: str, path: str) -> bool:
    s = get_settings()
    if not s.supabase_configured:
        return False
    key = s.storage_key
    try:
        resp = requests.delete(
            f"{_base_url()}/storage/v1/object/{bucket}",
            headers={"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={"prefixes": [path]},
            timeout=15,
        )
        return resp.status_code in (200, 204)
    except Exception:
        return False


# ── Monitoring (user_activity + users.last_active_at) ────────────────
def _now_iso() -> str:
    import time as _t
    return _t.strftime("%Y-%m-%dT%H:%M:%SZ", _t.gmtime())


def log_activity(username: str, action: str, target: str = "", details: dict | None = None) -> None:
    s = get_settings()
    if not s.supabase_configured or not username or not action:
        return
    try:
        requests.post(
            _rest_url("user_activity"),
            headers=_service_headers("return=minimal"),
            json={
                "username": username.strip().lower(),
                "action": action,
                "target": target or None,
                "details": details or None,
                "created_at": _now_iso(),
            },
            timeout=8,
        )
    except Exception:
        pass


def mark_login(username: str) -> None:
    try:
        now = _now_iso()
        requests.patch(
            _rest_url(get_settings().supabase_table),
            headers=_service_headers("return=minimal"),
            params={"username": f"eq.{username.strip().lower()}"},
            json={"last_login_at": now, "last_active_at": now},
            timeout=8,
        )
    except Exception:
        pass


def fetch_user_overview() -> list[dict]:
    s = get_settings()
    if not s.supabase_configured:
        return []
    try:
        resp = requests.get(
            _rest_url(s.supabase_table),
            headers=_service_headers(),
            params={
                "select": "username,role,is_active,last_login_at,last_active_at",
                "order": "username.asc",
            },
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json() or []
    except Exception:
        return []


def fetch_recent_activity(limit: int = 50) -> list[dict]:
    s = get_settings()
    if not s.supabase_configured:
        return []
    try:
        resp = requests.get(
            _rest_url("user_activity"),
            headers=_service_headers(),
            params={
                "select": "username,action,target,created_at",
                "order": "created_at.desc",
                "limit": str(limit),
            },
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json() or []
    except Exception:
        return []


def fetch_user_role(username: str):
    """Re-cek otorisasi: ambil {username, role, is_active} user AKTIF.

    Return: dict (user aktif ditemukan) | None (tidak ada/nonaktif — TOLAK) |
    False (Supabase tidak terjangkau — caller sebaiknya fail-open agar tidak
    mengunci semua user saat gangguan jaringan sesaat).
    """
    s = get_settings()
    if not s.supabase_configured:
        return False  # tak bisa verifikasi → jangan kunci (perlakukan sbg error)
    uname = (username or "").strip().lower()
    if not uname:
        return None
    try:
        resp = requests.get(
            _rest_url(s.supabase_table),
            headers=_headers(),
            params={
                "username": f"eq.{uname}",
                "is_active": "eq.true",
                "select": "username,role,is_active",
                "limit": "1",
            },
            timeout=8,
        )
        resp.raise_for_status()
        rows = resp.json() or []
        return rows[0] if rows else None
    except Exception:
        return False


def fetch_active_user(username: str) -> Optional[dict]:
    """
    Ambil satu user aktif berdasarkan username (case-insensitive, lower).
    Return dict baris user atau None. Setara load_users_from_supabase +
    filter di supabase.py::authenticate_from_supabase.
    """
    s = get_settings()
    if not s.supabase_configured:
        return None

    uname = username.strip().lower()
    try:
        resp = requests.get(
            _rest_url(s.supabase_table),
            headers=_headers(),
            params={
                "username": f"eq.{uname}",
                "is_active": "eq.true",
                "select": "username,password_hash,password,role,is_active",
                "limit": "1",
            },
            timeout=10,
        )
        resp.raise_for_status()
        rows = resp.json()
        return rows[0] if rows else None
    except Exception:
        return None
