"""Pelacakan kehadiran (online/offline) user — IN-MEMORY, tanpa DB.

Sumber kebenaran status online untuk panel Monitoring admin. Di-update:
  • setiap request terautentikasi  → deps.get_current_user() memanggil touch()
  • saat login berhasil             → routers/auth.login() memanggil mark_login()

Kenapa in-memory (bukan DB): "siapa online sekarang" hanya butuh data sesaat.
Menulis last_active ke Supabase tiap request = lambat + kena rate limit. Cukup
dict di memori (O(1)). Konsekuensi: status reset saat container backend restart
& hanya valid untuk 1 proses backend — sesuai setup MASPART (1 container backend).
"""
from __future__ import annotations

import threading
import time
from collections import deque

# User dianggap ONLINE bila aktivitas terakhirnya <= ambang ini.
ONLINE_WINDOW_SEC = 300  # 5 menit (selaras label UI "Online (5 mnt)")
_RECENT_MAX = 100        # ring buffer aktivitas terbaru

_lock = threading.Lock()
# username(lowercase) -> {"last_active": epoch, "last_login": epoch|None}
_state: dict[str, dict] = {}
_recent: deque = deque(maxlen=_RECENT_MAX)  # {created_at, username, action, target}


def _iso(epoch: float | None) -> str | None:
    """Epoch → ISO UTC (…Z). Frontend menambah 'Z' bila absen, tapi kita eksplisit."""
    if not epoch:
        return None
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch))


def touch(username: str) -> None:
    """Tandai user AKTIF sekarang. Dipanggil tiap request terautentikasi (murah)."""
    u = (username or "").strip().lower()
    if not u:
        return
    now = time.time()
    with _lock:
        st = _state.get(u)
        if st is None:
            _state[u] = {"last_active": now, "last_login": None}
        else:
            st["last_active"] = now


def mark_login(username: str) -> None:
    """Catat waktu login + baris aktivitas 'login' untuk panel monitoring."""
    u = (username or "").strip().lower()
    if not u:
        return
    now = time.time()
    with _lock:
        st = _state.get(u)
        if st is None:
            _state[u] = {"last_active": now, "last_login": now}
        else:
            st["last_active"] = now
            st["last_login"] = now
        _recent.appendleft({"created_at": _iso(now), "username": u,
                            "action": "login", "target": None})


def get(username: str) -> dict:
    """Presence 1 user: {online, last_active_at, last_login_at}."""
    u = (username or "").strip().lower()
    now = time.time()
    with _lock:
        st = _state.get(u)
        if not st:
            return {"online": False, "last_active_at": None, "last_login_at": None}
        la = st.get("last_active")
        return {
            "online": bool(la and (now - la) <= ONLINE_WINDOW_SEC),
            "last_active_at": _iso(la),
            "last_login_at": _iso(st.get("last_login")),
        }


def recent(limit: int = 50) -> list[dict]:
    """Aktivitas terbaru (login) — terbaru dulu."""
    with _lock:
        return list(_recent)[: max(0, limit)]
