"""
Scope stok per cabang — akun cabang hanya melihat stok gudangnya sendiri.
Jika gudang sendiri kosong, tampilkan stok dari cabang TERDEKAT yang masih
ada stok. Admin / akun SEE_ALL → semua gudang. (Port dari gudang_config.py.)
"""
from __future__ import annotations

import math
import re

from . import gudang_config

SEE_ALL_ACCOUNTS = {"mas"}

ACCOUNT_GUDANG: dict[str, str] = {
    "jakarta": "01.Jakarta",
    "balikpapan": "03.Balikpapan",
    "palembang": "04.Palembang",
    "makassar": "05.Makasar",
    "jambi": "08.TJP Jambi",
    "banjarmasin": "10.Banjarbaru",
    "muarateweh": "11.Muara Teweh",
    "pontianak": "18.Pontianak",
    "medan": "23.Medan",
}

# Lokasi pembeli & koordinat gudang diatur admin via gudang_config (persisten).


def list_locations() -> list[dict]:
    """Daftar lokasi yang bisa dipilih pembeli: [{key, label}] (label tanpa prefix nomor)."""
    return [{"key": k, "label": gudang_label(v["label"])} for k, v in gudang_config.buyer_locations().items()]


def location(key: str | None) -> dict | None:
    """Detail lokasi (label, origin_postal) dari key, atau None bila tak valid."""
    return gudang_config.buyer_locations().get((key or "").strip().lower())


def buyer_label(key: str | None) -> str | None:
    """Label gudang (mis. '01.Jakarta') dari key pembeli, atau None."""
    loc = location(key)
    return loc["label"] if loc else None


def _haversine(a: tuple, b: tuple) -> float:
    lat1, lon1 = a
    lat2, lon2 = b
    R = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    h = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * R * math.asin(math.sqrt(h))


def branch_label_for(username: str) -> str | None:
    """Label gudang yang dikelola akun cabang ini (None bila bukan cabang).
    Sumber: key di config pembeli (diatur admin) lalu ACCOUNT_GUDANG bawaan."""
    u = (username or "").strip().lower()
    if u in SEE_ALL_ACCOUNTS:
        return None
    loc = gudang_config.buyer_locations().get(u)
    if loc and loc.get("label"):
        return loc["label"]
    return ACCOUNT_GUDANG.get(u)


def gudang_for_user(username: str, role: str) -> str | None:
    """None → lihat semua gudang (admin / SEE_ALL / pembeli / tak terpetakan)."""
    r = (role or "").strip().lower()
    if r in ("admin", "pembeli"):
        return None
    return branch_label_for(username)


def branch_keys() -> set[str]:
    """Semua key/username akun cabang (bawaan ∪ config pembeli)."""
    return set(ACCOUNT_GUDANG) | set(gudang_config.buyer_locations())


def branch_labels() -> set[str]:
    """Semua label gudang yang punya akun cabang (config pembeli ∪ bawaan)."""
    labels = set(ACCOUNT_GUDANG.values())
    for loc in gudang_config.buyer_locations().values():
        if loc.get("label"):
            labels.add(loc["label"])
    return labels


def owning_branch_label(label: str) -> str | None:
    """Cabang yang mengelola stok di gudang `label`:
    - jika `label` sendiri sebuah cabang → label itu;
    - jika sub-gudang (mis. B80/Kerinci) → cabang TERDEKAT (koordinat)."""
    if not label:
        return None
    branches = branch_labels()
    if label in branches:
        return label
    coords = gudang_config.coords_map()
    own = coords.get(label)
    if not own:
        return None
    best, best_d = None, None
    for b in branches:
        bc = coords.get(b)
        if not bc:
            continue
        d = _haversine(own, bc)
        if best_d is None or d < best_d:
            best, best_d = b, d
    return best


def fallback_order(own_gudang: str, all_gudang: list[str]) -> list[str]:
    coords = gudang_config.coords_map()
    own = coords.get(own_gudang)
    known, unknown = [], []
    for g in all_gudang:
        if g == own_gudang:
            continue
        if own and g in coords:
            known.append((_haversine(own, coords[g]), g))
        else:
            unknown.append(g)
    known.sort(key=lambda t: t[0])
    return [g for _, g in known] + unknown


def coords_for_display(display_label: str) -> tuple | None:
    """Koordinat (lat, lon) gudang dari label tampilan (tanpa prefix nomor)."""
    if not display_label:
        return None
    for full, c in gudang_config.coords_map().items():
        if gudang_label(full) == display_label:
            return c
    return None


def pic_for_display(display_label: str) -> str | None:
    """Nomor PIC gudang dari label tampilan (tanpa prefix nomor)."""
    if not display_label:
        return None
    for full, phone in gudang_config.pic_map().items():
        if gudang_label(full) == display_label and phone:
            return phone
    return None


def gudang_label(gudang_name: str) -> str:
    if not gudang_name:
        return ""
    return re.sub(r"^\s*\d+\s*\.\s*", "", gudang_name).strip() or gudang_name


def scope_breakdown(
    breakdown: dict, username: str, role: str, all_names: list[str],
    own: str | None = None,
) -> dict:
    """
    Filter breakdown {gudang: qty} sesuai cakupan user.
    - admin / SEE_ALL / tak terpetakan → breakdown apa adanya.
    - akun cabang: hanya gudang sendiri; bila kosong → gudang terdekat yang ada stok.
    - `own` boleh diberikan eksplisit (mis. gudang terpilih pembeli); bila None,
      dihitung dari `gudang_for_user`.
    `breakdown` hanya berisi gudang ber-qty != 0.
    """
    if own is None:
        own = gudang_for_user(username, role)
    if own is None:
        return breakdown
    if own in breakdown and breakdown[own]:
        return {own: breakdown[own]}
    for g in fallback_order(own, all_names):
        if g in breakdown and breakdown[g]:
            return {g: breakdown[g]}
    return {}
