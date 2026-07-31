"""
Service: Menu Control lengkap — akses Menu, Kolom, dan Sub-tab Harga per user.

Tabel `permissions(perm_type, username, keys[])`:
  - perm_type "nav"          → menu/halaman (khusus app baru, beda dari Streamlit)
  - perm_type "column"       → kolom (col_stok, col_harga) — SAMA dgn Streamlit
  - perm_type "harga_subtab" → sub-tab harga — SAMA dgn Streamlit

Aturan umum: admin → semua; user dgn baris → keys baris; tanpa baris →
"__default__" (kalau ada) / semua; key di ALWAYS selalu aktif.
"""
from __future__ import annotations

import threading
import time

from . import gudang, rak
from .supabase_client import list_users, perms_delete, perms_load, perms_save

# ── Cache ber-TTL untuk pembacaan izin ───────────────────────────────
# `perms_load` = satu request HTTP ke Supabase. Tanpa cache, tiap gerbang
# (_boleh_harga & _boleh_stok di asisten) memukul Supabase SEKALI PER TOOL CALL —
# satu giliran chat bisa 10+ round-trip hanya untuk membaca dua boolean yang sama.
# Pola & TTL disamakan dengan services/session_policy.py; admin.py memanggil
# invalidate_cache() saat centang disimpan/direset supaya perubahan berlaku
# seketika, bukan setelah TTL.
_CACHE_TTL = 30.0
_cache_lock = threading.Lock()
_cache: dict[str, tuple[float, dict]] = {}      # perm_type → (at, data)


def _perms_load_cached(perm_type: str) -> dict:
    now = time.monotonic()
    with _cache_lock:
        hit = _cache.get(perm_type)
        if hit and (now - hit[0]) < _CACHE_TTL:
            return hit[1]
    data = perms_load(perm_type)                # {} bila Supabase mati → fail-open
    with _cache_lock:
        _cache[perm_type] = (time.monotonic(), data)
    return data


def invalidate_cache() -> None:
    """Buang cache izin — dipanggil admin.py setelah set_perm/reset_perm."""
    with _cache_lock:
        _cache.clear()

# ── Definisi tiap "kind" ─────────────────────────────────────────────
MENU_TABS: dict[str, str] = {
    "ai": "Asisten AI",
    "search": "Cari Part",
    "search_image": "Cari by Foto",
    "compare": "Bandingkan 2 Part",
    "batch": "Batch Download",
    "populasi": "Populasi Unit",
    "harga": "Harga",
    "stok": "Stok",
    # Menu DATA (bukan ADMIN — staf gudang bukan admin): input rak + foto kartu
    # stok. Muncul di Menu Control otomatis karena daftar ini yang dibaca.
    "rak": "Rak & Kartu Stok",
}
COLUMN_KEYS: dict[str, str] = {
    "col_stok": "Kolom Stok",
    "col_harga": "Kolom Harga",
}
HARGA_SUBTABS: dict[str, str] = {
    "subtab_list_harga": "List Harga",
    "subtab_cari_harga": "Cari Harga",
    "subtab_batch_harga": "Batch Cari Harga",
}
SESI_KEYS: dict[str, str] = {
    "single_device": "Hanya 1 perangkat",
}
# Kemampuan Asisten AI yang dulu HARDCODED admin-only (atau admin+'mas') —
# kini bisa DIBERIKAN per akun lewat Menu Control tab "Asisten AI".
# pesanan_bermasalah dipisah dari stok_admin: data uang/refund/pembukuan
# beda kelas sensitivitas dari data reservasi stok.
ASISTEN_KEYS: dict[str, str] = {
    "ai_harga_sims": "Harga SIMS (modal)",
    "ai_populasi": "Populasi Unit",
    "ai_penawaran": "Buat Penawaran",
    "ai_stok_admin": "Stok Tertahan & Alternatif",
    "ai_pesanan_bermasalah": "Pesanan Bermasalah",
    # Paket garansi & klaim SIMS (cek_garansi/riwayat_klaim/detail_klaim):
    # data DMS pabrik — nilai klaim CNY, nama+HP pelapor, foto unit.
    "ai_garansi": "Garansi & Klaim SIMS",
}

# kind → (perm_type, semua key+label, key yang selalu aktif)
#
# `default_off`: kind ini BUKAN daftar izin melainkan PEMBATASAN. Aturan umum
# "tanpa baris → semua key aktif" akan mengunci SEMUA akun ke satu perangkat
# begitu fitur ini terpasang. Untuk kind ber-default_off, tanpa baris = KOSONG
# (pembatasan mati), harus dicentang admin secara sadar.
KINDS: dict[str, dict] = {
    # 'search' dulu masuk `always` (tak bisa dimatikan siapa pun) — dilepas atas
    # permintaan pemilik 2026-07-12 agar menu Cari Part bisa disembunyikan per-akun
    # (khususnya pembeli). Tanpa baris izin, semua menu tetap aktif (default lama).
    # Catatan batas: yang disembunyikan MENU & halamannya; endpoint /api/search
    # sengaja TIDAK digembok karena halaman detail part (dibuka dari kartu /toko)
    # memakai endpoint yang sama — datanya sudah di-scope per peran.
    "menu": {"perm_type": "nav", "all": MENU_TABS, "always": set()},
    "column": {"perm_type": "column", "all": COLUMN_KEYS, "always": set()},
    "harga": {"perm_type": "harga_subtab", "all": HARGA_SUBTABS, "always": set()},
    "sesi": {"perm_type": "session_policy", "all": SESI_KEYS, "always": set(),
             "default_off": True},
    # `grant_off`: IZIN ELEVATED (kemampuan yang dulu admin-only). Tanpa baris =
    # KOSONG untuk staf (mencegah eskalasi massal saat fitur ini deploy — beda
    # dari aturan umum "tanpa baris → semua aktif"), tapi ADMIN tetap dapat
    # SEMUA (beda dari default_off yang = PEMBATASAN sehingga admin dapat []).
    "asisten": {"perm_type": "ai_ability", "all": ASISTEN_KEYS, "always": set(),
                "grant_off": True},
}


def is_valid_kind(kind: str) -> bool:
    return kind in KINDS


def effective(kind: str, username: str, role: str) -> list[str]:
    cfg = KINDS[kind]
    all_keys = list(cfg["all"].keys())
    off = cfg.get("default_off", False)
    if role == "admin":
        # Admin punya semua IZIN, tapi tak pernah kena PEMBATASAN (default_off).
        # Tanpa ini, mencentang 'sesi' berisiko mengunci admin dari sistemnya sendiri.
        return [] if off else all_keys
    data = _perms_load_cached(cfg["perm_type"])
    if username in data:
        allowed = set(data[username])
    elif "__default__" in data:
        allowed = set(data["__default__"])
    else:
        allowed = set() if (off or cfg.get("grant_off")) else set(all_keys)
    allowed |= cfg["always"]
    return [k for k in all_keys if k in allowed]


def all_effective(username: str, role: str) -> dict:
    # Label cabang (gudang) bila ini akun cabang — agar frontend tahu menampilkan
    # menu "Pesanan Masuk".
    branch = gudang.gudang_for_user(username, role)
    return {
        "menus": effective("menu", username, role),
        "columns": effective("column", username, role),
        "harga_subtabs": effective("harga", username, role),
        "role": role,
        "branch": gudang.gudang_label(branch) if branch else None,
        # Boleh ekspor kolom harga di Batch Download — admin & akun 'mas' saja.
        "can_price": gudang.can_see_price(username, role),
        # Gudang yang boleh DITULIS akun ini di Rak & Kartu Stok (label penuh).
        # Frontend memakainya untuk (a) memunculkan menu Rak dan (b) menampilkan
        # tombol "Ubah" hanya di baris gudang yang memang ia kelola. [] untuk
        # pembeli & selama migrasi 027 belum jalan (fitur dorman, bukan error).
        "gudang_kelola": [] if role == "pembeli" else rak.gudang_kelola_for(username),
    }


def overview(kind: str) -> dict:
    cfg = KINDS[kind]
    data = perms_load(cfg["perm_type"])
    all_keys = list(cfg["all"].keys())
    # default_off/grant_off → baris "Default (user baru)" mulai TIDAK tercentang.
    bawaan = [] if (cfg.get("default_off") or cfg.get("grant_off")) else all_keys
    return {
        "kind": kind,
        "all_keys": cfg["all"],
        "always": sorted(cfg["always"]),
        "default": data.get("__default__", bawaan),
        "permissions": {u: k for u, k in data.items() if u != "__default__"},
        "users": list_users(),
    }


def set_perm(kind: str, username: str, keys: list[str]) -> bool:
    cfg = KINDS[kind]
    clean = [k for k in keys if k in cfg["all"]]
    for a in cfg["always"]:
        if a not in clean:
            clean.append(a)
    return perms_save(cfg["perm_type"], username, clean)


def reset_perm(kind: str, username: str) -> bool:
    return perms_delete(KINDS[kind]["perm_type"], username)


# ── Gerbang kolom Harga & Stok (server-side) ─────────────────────────
# Dulu hidup di ai_parts/p1_dasar.py dan hanya menjaga jalur Asisten AI;
# dipindah ke sini agar router non-AI (/api/parts, /api/harga, /api/stok)
# memakai gerbang yang SAMA — penyembunyian kolom di React saja tidak
# menutup akses lewat network tab / curl.

# Field harga di hasil tool/endpoint (semua nama yang mungkin dipancarkan).
HARGA_KEYS = ("harga", "harga_lokal", "harga_sims", "harga_jual", "harga_cny",
              "harga_idr", "harga_display", "harga_daftar")

# Field STOK di hasil tool/endpoint. 'gudang' sengaja TIDAK di sini: di banyak
# hasil ia string NAMA gudang, bukan kuantitas — ditangani khusus di strip_stok
# (dibuang hanya bila dict qty).
STOK_KEYS = ("stok", "stok_total", "stok_per_gudang", "stok_di_gudang",
             "stok_accurate", "stok_dapat_dijual", "stok_lokal_tambahan",
             "tertahan", "tersedia", "ada_di_inventori", "jumlah_part_ready")

# Key tambahan khusus respons router (JANGAN dicampur ke set di atas: di hasil
# tool asisten 'quantity' = qty BOM per-unit, bukan stok — mencampurnya bikin
# asisten over-strip).
ROUTER_STOK_EXTRA = ("Stok", "available_to_sell", "quantity", "per_gudang")


def boleh_harga(user: dict) -> bool:
    """Boleh melihat HARGA — SATU sumber kebenaran, mengikuti Menu Control.

    Admin selalu; pembeli boleh (harga jual = yang ia bayar, tampil juga di
    /toko); staf mengikuti izin kolom 'col_harga'. Dipakai asisten AI dan
    router non-AI supaya konsisten."""
    role = (user.get("role") or "").lower()
    if role in ("admin", "pembeli"):
        return True
    try:
        return "col_harga" in effective("column", user.get("username", ""), role)
    except Exception:
        return False


def boleh_stok(user: dict) -> bool:
    """Boleh melihat STOK — kembaran boleh_harga, kunci 'col_stok'.

    Admin selalu; pembeli boleh (wajib tahu ketersediaan untuk membeli di
    /toko — rincian antar-gudang tetap disembunyikan di jalurnya masing-masing);
    staf mengikuti izin kolom 'col_stok'."""
    role = (user.get("role") or "").lower()
    if role in ("admin", "pembeli"):
        return True
    try:
        return "col_stok" in effective("column", user.get("username", ""), role)
    except Exception:
        return False


def boleh_ai(user: dict, key: str) -> bool:
    """Kemampuan Asisten AI ELEVATED (dulu admin-only) — fail-CLOSED.

    Admin selalu True; pembeli selalu False (kemampuan internal, tak pernah
    bisa diberi); staf HANYA bila key-nya dicentang admin di Menu Control tab
    Asisten AI. Supabase mati → perms_load {} → grant_off → set() → False;
    exception apa pun → False. Sengaja BEDA dari boleh_harga/boleh_stok yang
    fail-open — memberi kemampuan elevated tak boleh terjadi karena error."""
    role = (user.get("role") or "").lower()
    if role == "admin":
        return True
    if role == "pembeli":
        return False
    try:
        return key in effective("asisten", user.get("username", ""), role)
    except Exception:
        return False


def strip_harga(obj, extra: tuple = ()):
    """Buang SEMUA field harga (rekursif: dict & list). Penjaga TERPUSAT —
    dipasang di _run_tool asisten & endpoint router, jadi tak bergantung tiap
    handler ingat mengecek izin. `extra` = key tambahan khusus pemanggil."""
    if isinstance(obj, dict):
        for k in list(obj.keys()):
            if k in HARGA_KEYS or k in extra:
                obj.pop(k, None)
            else:
                strip_harga(obj[k], extra)
    elif isinstance(obj, list):
        for it in obj:
            strip_harga(it, extra)
    return obj


def strip_stok(obj, extra: tuple = ()):
    """Buang SEMUA field stok (rekursif: dict & list) — kembaran strip_harga."""
    if isinstance(obj, dict):
        for k in list(obj.keys()):
            # 'gudang' bernilai dict = peta {nama gudang: qty} → itu data stok.
            # Bila string, ia cuma label gudang (mis. filter query) → biarkan.
            if k in STOK_KEYS or k in extra or (k == "gudang" and isinstance(obj[k], dict)):
                obj.pop(k, None)
            else:
                strip_stok(obj[k], extra)
    elif isinstance(obj, list):
        for it in obj:
            strip_stok(it, extra)
    return obj
