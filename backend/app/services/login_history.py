"""
Riwayat login (tabel Supabase `login_history`) — untuk memantau AKUN DIPAKAI RAMAI.

Satu baris per login berhasil: kapan, siapa, dari IP mana, pakai perangkat apa.
Dari sini panel Monitoring bisa menghitung: dalam N hari terakhir akun ini login
dari berapa IP berbeda & berapa perangkat berbeda. Banyak IP/perangkat berbeda
= indikasi password dibagi-bagi.

⚠️ BATAS KEPASTIAN — jangan menjual ini sebagai bukti mutlak:
  • IP berubah sendiri saat pindah WiFi/kuota, dan satu kantor berbagi satu IP
    publik. Jadi IP berbeda ≠ pasti orang berbeda, IP sama ≠ pasti orang sama.
  • User-Agent dilaporkan browser dan bisa dipalsukan; dua HP model sama
    menghasilkan string identik.
  Gunakan sebagai SINYAL untuk ditelusuri, bukan vonis.

Resilient: bila tabel belum dibuat / Supabase down, semua fungsi diam (best-effort)
dan TIDAK pernah menjatuhkan login. DDL ada di create_table_sql() & migrations/017.

⚠️ SKEMA WARISAN: tabel `login_history` sudah ada sejak aplikasi Streamlit lama
dengan kolom username/success/reason/ip_address/user_agent/created_at. Kita PAKAI
apa adanya (tak ada kolom `ip`/`device`/`role`): `device` diturunkan dari
`user_agent` saat dibaca, `role` diambil dari roster user di panel Monitoring.

PRIVASI: IP & perangkat = data pribadi. Hanya admin (require_admin) yang bisa
melihatnya. Retensi otomatis: baris lebih tua dari _RETENTION_DAYS dibuang.
"""
from __future__ import annotations

import re
import time

import requests

from .supabase_client import _rest_url, _service_headers

_TIMEOUT = 10
_RETENTION_DAYS = 90     # riwayat login disimpan 3 bulan


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _cutoff(days: int) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - days * 86400))


def create_table_sql() -> str:
    """DDL Supabase (jalankan sekali di SQL Editor). Identik dgn migrations/017.

    ⚠️ Skema ini MENGIKUTI tabel `login_history` yang sudah ada sejak aplikasi
    Streamlit lama (username/success/reason/ip_address/user_agent/created_at) —
    bukan bikin kolom baru. `device` TIDAK disimpan; ia diturunkan dari
    `user_agent` saat dibaca (device_label), jadi tak ada data ganda.
    """
    return (
        "create table if not exists login_history (\n"
        "  id bigint generated always as identity primary key,\n"
        "  created_at timestamptz not null default now(),\n"
        "  username text not null,\n"
        "  success boolean not null default true,\n"
        "  reason text,\n"
        "  ip_address text,\n"
        "  user_agent text\n"
        ");\n"
        "create index if not exists login_history_created_idx on login_history (created_at desc);\n"
        "create index if not exists login_history_user_idx on login_history (username, created_at desc);\n"
    )


# ── Parsing User-Agent → label perangkat yang enak dibaca ──
# Sengaja regex sederhana (tanpa dependency): cukup untuk "Chrome di Windows".
_OS = [
    (re.compile(r"Windows NT 10|Windows NT 11"), "Windows"),
    (re.compile(r"Windows NT"), "Windows (lama)"),
    (re.compile(r"iPhone"), "iPhone"),
    (re.compile(r"iPad"), "iPad"),
    (re.compile(r"Android"), "Android"),
    (re.compile(r"Mac OS X|Macintosh"), "Mac"),
    (re.compile(r"CrOS"), "ChromeOS"),
    (re.compile(r"Linux"), "Linux"),
]
# Urutan PENTING: Edge/Opera/Samsung menyamar sebagai Chrome; Chrome menyamar
# sebagai Safari. Cek yang paling spesifik dulu.
_BROWSER = [
    (re.compile(r"Edg/"), "Edge"),
    (re.compile(r"OPR/|Opera"), "Opera"),
    (re.compile(r"SamsungBrowser"), "Samsung Internet"),
    (re.compile(r"Firefox/|FxiOS"), "Firefox"),
    (re.compile(r"CriOS"), "Chrome"),
    (re.compile(r"Chrome/"), "Chrome"),
    (re.compile(r"Safari/"), "Safari"),
]


def device_label(user_agent: str) -> str:
    """'Chrome di Windows'. Kosong/aneh → 'Tidak dikenal'."""
    ua = (user_agent or "").strip()
    if not ua:
        return "Tidak dikenal"
    os_name = next((n for rx, n in _OS if rx.search(ua)), "")
    br = next((n for rx, n in _BROWSER if rx.search(ua)), "")
    if br and os_name:
        return f"{br} di {os_name}"
    return br or os_name or "Tidak dikenal"


def record(username: str, role: str, ip: str, user_agent: str,
           success: bool = True, reason: str = "") -> bool:
    """Simpan satu login. Best-effort — False bila gagal/tabel absen, tak melempar.
    `role` tak disimpan (tabel tak punya kolomnya); peran diambil dari roster user
    di panel Monitoring."""
    u = (username or "").strip().lower()
    if not u:
        return False
    try:
        r = requests.post(
            _rest_url("login_history"),
            headers=_service_headers("return=minimal"),
            json={
                "username": u,
                "success": bool(success),
                "reason": (reason or None),
                "ip_address": (ip or None),
                "user_agent": (user_agent or "")[:400] or None,
                "created_at": _now(),
            },
            timeout=_TIMEOUT,
        )
        return r.status_code in (200, 201, 204)
    except Exception:
        return False


def _fetch(params: dict) -> list[dict] | None:
    """None = tabel absen / Supabase error. [] = tabel ADA tapi kosong.
    Bedanya penting: panel hanya menyuruh admin membuat tabel bila benar None."""
    try:
        r = requests.get(
            _rest_url("login_history"),
            headers={**_service_headers(), "Accept": "application/json"},
            params=params,
            timeout=_TIMEOUT,
        )
        r.raise_for_status()
        return r.json() or []
    except Exception:
        return None


def table_ready() -> bool:
    """True bila tabel `login_history` bisa dibaca (walau masih 0 baris)."""
    return _fetch({"select": "id", "limit": "1"}) is not None


def ip_network(ip: str) -> str:
    """Alamat → identitas JARINGAN, untuk menghitung 'berapa lokasi berbeda'.

    IPv6 dgn privacy extensions (Windows/Android) MENGGANTI 64 bit belakang tiap
    beberapa jam, jadi satu orang di satu WiFi tampak punya belasan 'IP berbeda'.
    Yang menandakan jaringan adalah 64 bit DEPAN (prefix /64) — itu yang kita
    hitung. IPv4 dipakai utuh (satu alamat = satu titik keluar).
      2001:448a:8080:50f:c1fe:… ─┐
      2001:448a:8080:50f:74a9:… ─┴→ 2001:448a:8080:50f::/64  (1 jaringan)
    """
    s = (ip or "").strip()
    if not s or ":" not in s:
        return s                      # IPv4 / kosong
    groups = s.split(":")[:4]         # 4 grup pertama = /64
    return ":".join(groups) + "::/64"


def _row_out(r: dict) -> dict:
    """Baris DB → bentuk yang dipakai UI. `device` DITURUNKAN dari user_agent."""
    return {
        "id": r.get("id"),
        "created_at": r.get("created_at"),
        "username": r.get("username"),
        "ip": r.get("ip_address"),
        "device": device_label(r.get("user_agent") or ""),
        "success": r.get("success", True),
    }


def list_logins(username: str = "", limit: int = 200) -> list[dict]:
    """Riwayat login terbaru dulu; `username` kosong = semua user."""
    params = {
        "select": "id,created_at,username,ip_address,user_agent,success",
        "order": "created_at.desc",
        "limit": str(max(1, min(limit, 1000))),
    }
    if username.strip():
        params["username"] = f"eq.{username.strip().lower()}"
    return [_row_out(r) for r in (_fetch(params) or [])]


def sharing_summary(days: int = 30) -> dict[str, dict]:
    """Per user dalam `days` hari terakhir:
        {username: {ip_count, device_count, login_count, ips[], devices[],
                    last_ip, last_device, last_at}}
    Dipakai panel Monitoring untuk menandai akun yang kemungkinan dipakai ramai.
    Hanya login BERHASIL yang dihitung — percobaan gagal bukan bukti pemakaian.
    `ip_count` menghitung JARINGAN unik (ip_network), bukan alamat unik: IPv6
    memutar alamatnya sendiri, dan tanpa ini satu orang tampak seperti banyak.
    `ips` tetap berisi alamat penuh untuk ditampilkan apa adanya.
    Kosong ({}) bila tabel belum ada ATAU belum ada login tercatat."""
    rows = _fetch({
        "select": "created_at,username,ip_address,user_agent,success",
        "created_at": f"gte.{_cutoff(days)}",
        "success": "is.true",
        "order": "created_at.desc",
        "limit": "5000",
    }) or []
    out: dict[str, dict] = {}
    for r in rows:
        u = (r.get("username") or "").strip().lower()
        if not u:
            continue
        d = out.setdefault(u, {"ips": [], "devices": [], "jaringan": [], "login_count": 0,
                               "last_ip": None, "last_device": None, "last_at": None})
        ip = r.get("ip_address")
        dev = device_label(r.get("user_agent") or "")
        # rows terurut terbaru dulu → yang pertama ditemui = login terakhir.
        if d["last_at"] is None:
            d["last_at"] = r.get("created_at")
            d["last_ip"] = ip
            d["last_device"] = dev
        d["login_count"] += 1
        if ip:
            if ip not in d["ips"]:
                d["ips"].append(ip)
            net = ip_network(ip)
            if net not in d["jaringan"]:
                d["jaringan"].append(net)
        if dev and dev != "Tidak dikenal" and dev not in d["devices"]:
            d["devices"].append(dev)
    for d in out.values():
        d["ip_count"] = len(d["jaringan"])   # jaringan unik, bukan alamat unik
        d["alamat_count"] = len(d["ips"])
        d["device_count"] = len(d["devices"])
    return out


def purge_old() -> int:
    """Buang baris lebih tua dari _RETENTION_DAYS. Return jumlah (atau -1)."""
    try:
        r = requests.delete(
            _rest_url("login_history"),
            headers=_service_headers("return=minimal,count=exact"),
            params={"created_at": f"lt.{_cutoff(_RETENTION_DAYS)}"},
            timeout=_TIMEOUT,
        )
        if r.status_code not in (200, 204):
            return -1
        cr = r.headers.get("Content-Range", "")
        tail = cr.rsplit("/", 1)[-1].strip() if "/" in cr else ""
        return int(tail) if tail.isdigit() else -1
    except Exception:
        return -1
