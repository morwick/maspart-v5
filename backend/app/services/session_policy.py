"""
Kebijakan "akun hanya boleh dipakai di SATU perangkat".

Dicentang admin per-user di Menu Control → tab Sesi (permissions.KINDS['sesi'],
perm_type `session_policy`, key `single_device`).

Cara kerja
----------
JWT MASPART stateless: siapa pun yang memegang token bisa memakainya. Agar satu
akun terikat satu perangkat, tiap login berhasil menerbitkan `sid` (session id)
acak yang ditanam ke token, dan `sid` AKTIF disimpan di server. Tiap request
membandingkan `sid` token dengan `sid` aktif:

    login perangkat A → sid=A tersimpan → token A dipakai … OK
    login perangkat B → sid=B MENIMPA A → token A tak cocok lagi → 401
                                          token B … OK

Jadi login terbaru selalu menang; perangkat lama otomatis terlempar ke halaman
login. Ini yang diharapkan orang saat mengaktifkan "hanya 1 perangkat".

Penyimpanan
-----------
`sid` aktif disimpan di tabel `permissions` yang sudah ada (perm_type
`active_session`, keys=[sid]) — TANPA tabel/DDL baru. Dibaca lewat cache memori
ber-TTL supaya tidak memukul Supabase tiap request.

Sikap saat gangguan (PENTING)
-----------------------------
FAIL-OPEN. Bila Supabase tak terjangkau, `perms_load` mengembalikan {} dan
kebijakan dianggap MATI → user tetap bisa bekerja. Ini disengaja: kegagalan
infrastruktur tidak boleh mengunci seluruh karyawan dari aplikasi. Konsekuensinya
saat outage pembatasan satu-perangkat longgar sementara — trade-off yang dipilih
sadar (ini fitur tata tertib, bukan benteng keamanan).

ADMIN DIKECUALIKAN (lihat permissions.effective): mustahil mengunci diri sendiri.
"""
from __future__ import annotations

import secrets
import threading
import time

from .supabase_client import perms_load, perms_save

_POLICY_TYPE = "session_policy"
_SESSION_TYPE = "active_session"
_KEY = "single_device"

# Cache: perubahan centang admin berlaku <= _POLICY_TTL detik; pelemparan
# perangkat lama terasa <= _SESSION_TTL detik (langsung, bila login terjadi di
# proses yang sama — cache di-update saat start_session).
_POLICY_TTL = 30.0
_SESSION_TTL = 20.0

_lock = threading.Lock()
_policy_cache: tuple[float, dict] | None = None      # (at, {username: [keys]})
_session_cache: tuple[float, dict] | None = None     # (at, {username: [sid]})


def _load(perm_type: str, cache_name: str, ttl: float) -> dict:
    global _policy_cache, _session_cache
    now = time.monotonic()
    with _lock:
        cache = _policy_cache if cache_name == "policy" else _session_cache
        if cache and (now - cache[0]) < ttl:
            return cache[1]
    data = perms_load(perm_type)          # {} bila Supabase mati → fail-open
    with _lock:
        if cache_name == "policy":
            _policy_cache = (now, data)
        else:
            _session_cache = (now, data)
    return data


def invalidate_cache() -> None:
    """Dipanggil setelah admin mengubah centang, agar berlaku SEKETIKA
    (tanpa ini efeknya baru terasa setelah TTL cache habis)."""
    global _policy_cache, _session_cache
    with _lock:
        _policy_cache = None
        _session_cache = None


def enabled(username: str, role: str = "") -> bool:
    """True bila akun ini dibatasi satu perangkat. Admin selalu False."""
    u = (username or "").strip().lower()
    if not u or (role or "").lower() == "admin":
        return False
    data = _load(_POLICY_TYPE, "policy", _POLICY_TTL)
    if u in data:
        return _KEY in (data[u] or [])
    return _KEY in (data.get("__default__") or [])


def active_sid(username: str) -> str:
    keys = _load(_SESSION_TYPE, "session", _SESSION_TTL).get((username or "").strip().lower()) or []
    return keys[0] if keys else ""


def start_session(username: str) -> str:
    """Terbitkan sid baru & jadikan satu-satunya yang sah. Return '' bila gagal
    simpan (fail-open: token tanpa sid tetap dilayani selama sid aktif kosong)."""
    u = (username or "").strip().lower()
    if not u:
        return ""
    sid = secrets.token_urlsafe(12)
    if not perms_save(_SESSION_TYPE, u, [sid]):
        return ""
    global _session_cache
    with _lock:  # segarkan cache agar perangkat lama langsung terlempar
        if _session_cache:
            _session_cache[1][u] = [sid]
        else:
            _session_cache = (time.monotonic(), {u: [sid]})
    return sid


def session_valid(username: str, sid: str, role: str = "") -> bool:
    """Boleh melanjutkan request? Kebijakan mati → selalu True."""
    if not enabled(username, role):
        return True
    aktif = active_sid(username)
    if not aktif:
        # Belum ada sesi terikat (baru dicentang / gagal simpan / Supabase mati).
        # Fail-open: jangan kunci user; ikatan terbentuk saat login berikutnya.
        return True
    return bool(sid) and secrets.compare_digest(sid, aktif)
