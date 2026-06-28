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

from . import gudang
from .supabase_client import list_users, perms_delete, perms_load, perms_save

# ── Definisi tiap "kind" ─────────────────────────────────────────────
MENU_TABS: dict[str, str] = {
    "ai": "Asisten AI",
    "search": "Cari Part",
    "search_image": "Cari by Foto",
    "compare": "Bandingkan 2 Part",
    "batch": "Batch Download",
    "populasi": "Populasi Unit",
    "harga": "Harga",

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

# kind → (perm_type, semua key+label, key yang selalu aktif)
KINDS: dict[str, dict] = {
    "menu": {"perm_type": "nav", "all": MENU_TABS, "always": {"search"}},
    "column": {"perm_type": "column", "all": COLUMN_KEYS, "always": set()},
    "harga": {"perm_type": "harga_subtab", "all": HARGA_SUBTABS, "always": set()},
}


def is_valid_kind(kind: str) -> bool:
    return kind in KINDS


def effective(kind: str, username: str, role: str) -> list[str]:
    cfg = KINDS[kind]
    all_keys = list(cfg["all"].keys())
    if role == "admin":
        return all_keys
    data = perms_load(cfg["perm_type"])
    if username in data:
        allowed = set(data[username])
    elif "__default__" in data:
        allowed = set(data["__default__"])
    else:
        allowed = set(all_keys)
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
    }


def overview(kind: str) -> dict:
    cfg = KINDS[kind]
    data = perms_load(cfg["perm_type"])
    all_keys = list(cfg["all"].keys())
    return {
        "kind": kind,
        "all_keys": cfg["all"],
        "always": sorted(cfg["always"]),
        "default": data.get("__default__", all_keys),
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
