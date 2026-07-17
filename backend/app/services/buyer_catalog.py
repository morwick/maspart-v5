"""
Service ETALASE PEMBELI — katalog produk siap-jual gaya e-commerce.

Menyatukan sumber yang sudah ada menjadi satu daftar produk yang bisa dibeli:
  - HARGA  : indeks Accurate SAJA (snapshot terjadwal). ⛔ harga.xlsx TIDAK PERNAH
             dipakai sebagai harga jual (aturan keras pemilik) — sama dengan yang
             ditagih saat checkout (harga.price_for_buyer), jadi yang dipajang = yang
             dibayar. Tak berharga di Accurate = tidak dijual.
  - NAMA   : katalog Excel lokal (all_parts_min) → nama barang Accurate (fallback,
             menjaring stok lokal aftermarket di luar katalog Sinotruk).
  - BERAT  : harga.weight_for (syarat bisa dibeli — konsisten aturan keranjang).
  - STOK   : indeks per-gudang ACCURATE SAJA (enrichment 3×/hari; ⛔ stok.xlsx tak
             lagi dipakai — aturan pemilik 2026-07-12); di-scope ke lokasi pembeli
             (gudang.scope_breakdown) minus reservasi aktif.
  - FOTO   : galeri lokal Cari-by-Foto (image_search.photo_url_map — in-memory).
  - LARIS  : agregat qty dari tabel order_items (Supabase, cache 15 mnt).

Produk = punya HARGA > 0 dan BERAT > 0 (tanpa salah satunya keranjang menolak,
jadi tidak dipajang). Semua BACA dari cache in-memory — endpoint etalase tidak
pernah menyentuh Accurate/SIMS live.
"""
from __future__ import annotations

import re
import threading
import time
from typing import Any, Optional

import requests

from . import accurate, gudang, harga, image_search, part_index, reservations

# ── Kategori payung etalase (regex batas-kata pada NAMA part, boleh multi) ──
# Kata kunci = istilah katalog Inggris + serapan lapangan yang memang muncul di
# nama/remark katalog. Urutan = urutan tampil chip kategori di UI.
_KATEGORI: list[tuple[str, str, list[str]]] = [
    ("filter",      "Filter & Saringan",   ["FILTER", "SARINGAN", "STRAINER"]),
    ("mesin",       "Mesin",               ["ENGINE", "PISTON", "CRANKSHAFT", "CAMSHAFT",
                                            "CYLINDER", "TURBOCHARGER", "TURBO", "INJECTOR",
                                            "LINER", "CONNECTING ROD", "FLYWHEEL", "GASKET",
                                            "VALVE", "BELT"]),
    ("rem",         "Rem",                 ["BRAKE", "KAMPAS REM"]),
    ("kopling",     "Kopling",             ["CLUTCH", "KOPLING"]),
    ("transmisi",   "Transmisi",           ["TRANSMISSION", "GEARBOX", "GEAR BOX",
                                            "SYNCHRONIZER", "GEAR", "SHIFT"]),
    ("gardan",      "Gardan & Axle",       ["DIFFERENTIAL", "AXLE", "FINAL DRIVE",
                                            "PROPELLER SHAFT", "HUB"]),
    ("pendingin",   "Pendinginan",         ["RADIATOR", "INTERCOOLER", "WATER PUMP",
                                            "THERMOSTAT", "FAN", "COOLANT"]),
    ("kelistrikan", "Kelistrikan & Lampu", ["ALTERNATOR", "STARTER", "RELAY", "SWITCH",
                                            "SENSOR", "LAMP", "LIGHT", "WIRING", "HARNESS",
                                            "BATTERY", "FUSE", "HORN", "REGULATOR"]),
    ("kabin",       "Kabin & Body",        ["CAB", "CABIN", "DOOR", "MIRROR", "GLASS",
                                            "SEAT", "BUMPER", "FENDER", "GRILLE", "WIPER",
                                            "HANDLE", "LOCK", "SPION"]),
    ("suspensi",    "Suspensi & Per",      ["SPRING", "SHOCK ABSORBER", "STABILIZER",
                                            "AIR BAG", "BUSHING", "TORQUE ROD"]),
    ("kemudi",      "Kemudi",              ["STEERING", "TIE ROD", "DRAG LINK", "KNUCKLE"]),
    ("bearing",     "Bearing & Seal",      ["BEARING", "SEAL", "O-RING", "O RING", "LAHER"]),
]

_KATEGORI_RE: list[tuple[str, "re.Pattern[str]"]] = [
    (key, re.compile(r"(?<!\w)(?:" + "|".join(re.escape(k) for k in kws) + r")(?!\w)"))
    for key, _label, kws in _KATEGORI
]
_KATEGORI_LABEL = {key: label for key, label, _ in _KATEGORI}

_SORTS = {"relevan", "harga_asc", "harga_desc", "nama", "stok"}

# ── Cache katalog produk (build sekali per perubahan sumber, guard TTL) ──────
_build_lock = threading.Lock()
_cache: dict[str, Any] = {"fp": None, "ts": 0.0, "products": [], "kategori_counts": {}}
_CACHE_TTL = 300.0  # cek ulang fingerprint paling cepat tiap 5 menit
_EMPTY_RETRY_SEC = 30.0  # build KOSONG = transien → coba ulang setelah jeda ini


def _flat(pn: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", (pn or "").upper())


def _rupiah(n: int) -> str:
    return "Rp " + f"{int(n):,}".replace(",", ".")


def _fingerprint() -> tuple:
    st = part_index.status()
    try:
        snap_n = len(accurate.snapshot())
    except Exception:
        snap_n = 0
    try:
        # Timestamp indeks Accurate: refresh yang UBAH HARGA tapi jumlah item sama
        # tetap menggeser fingerprint → etalase rebuild → harga pajang == checkout.
        idx_ts = accurate.index_stamp()
    except Exception:
        idx_ts = (0.0, 0.0)
    return (st.get("indexed_at"), snap_n, len(image_search.photo_url_map()), idx_ts)


def _kategori_for(name_up: str) -> list[str]:
    return [key for key, rx in _KATEGORI_RE if rx.search(name_up)]


def _build_products() -> list[dict]:
    """Gabungkan sumber menjadi daftar produk siap-jual (lihat docstring modul)."""
    names = {pn: (nm or "").strip() for pn, nm in part_index.all_parts_min()}
    photos = image_search.photo_url_map()

    # Barang Accurate = SATU-SATUNYA sumber harga jual (termasuk aftermarket di luar
    # katalog). ⛔ harga.xlsx tak pernah dipakai — dulu ia jadi fallback, sehingga part
    # bisa tampil berharga di toko padahal checkout menolaknya.
    picked: dict[str, dict] = {}   # norm_pn -> produk
    try:
        acc_items = accurate.all_items()
    except Exception:
        acc_items = []
    for it in acc_items:
        pn = (it.get("pn") or "").strip().upper()
        price = int(it.get("price") or 0)
        if not pn or price <= 0:
            continue
        key = accurate.norm_pn(pn)
        if key in picked:
            continue
        picked[key] = {
            "part_number": pn,
            "name": names.get(pn) or (it.get("name") or "").strip() or pn,
            "harga": price,
            "stok_total": int(it.get("available_to_sell") or 0),
            "sumber_harga": "accurate",
        }

    out: list[dict] = []
    for p in picked.values():
        pn = p["part_number"]
        berat = harga.weight_for(pn)
        if not berat or berat <= 0:
            continue  # keranjang menolak part tanpa berat — jangan dipajang
        # Breakdown per-gudang: INDEKS ACCURATE (part_index.gudang_breakdown kini
        # mendelegasikan ke sana — ⛔ stok.xlsx tak lagi dipakai, aturan pemilik).
        bd = {g: int(q) for g, q in part_index.gudang_breakdown(pn).items() if int(q or 0) > 0}
        name_up = p["name"].upper()
        out.append({
            "part_number": pn,
            "name": p["name"],
            "harga": p["harga"],
            "harga_display": _rupiah(p["harga"]),
            "berat": int(berat),
            "foto": photos.get(pn),
            "kategori": _kategori_for(name_up),
            "gudang": bd,
            "stok_total": max(int(p["stok_total"]), sum(bd.values())),
            "_hay": f"{pn} {name_up}",
            "_flat": _flat(pn),
        })
    out.sort(key=lambda x: ((x["foto"] is None), x["name"]))
    return out


def _products() -> list[dict]:
    now = time.time()
    if _cache["fp"] is not None and now - _cache["ts"] < _CACHE_TTL:
        return _cache["products"]
    # Build KOSONG tak pernah disimpan sebagai keadaan sah (fp None) — hanya
    # cooldown singkat agar tak rebuild tiap request; lewat itu → coba lagi.
    if _cache["fp"] is None and _cache["ts"] and now - _cache["ts"] < _EMPTY_RETRY_SEC:
        return _cache["products"]
    fp = _fingerprint()
    if fp == _cache["fp"]:
        _cache["ts"] = now
        return _cache["products"]
    with _build_lock:
        if fp == _cache["fp"]:   # thread lain sudah build
            return _cache["products"]
        prods = _build_products()
        counts: dict[str, int] = {}
        for p in prods:
            for k in p["kategori"]:
                counts[k] = counts.get(k, 0) + 1
        # Kejadian nyata 2026-07-15: build pertama jalan saat sumber masih dingin
        # pasca-restart → hasil [] tersimpan dgn fingerprint sah & indeks berat tak
        # ikut fingerprint → etalase /toko KOSONG permanen sampai refresh terjadwal.
        # Guard: hasil kosong disimpan TANPA fingerprint (fp None) sehingga request
        # berikutnya (setelah _EMPTY_RETRY_SEC) membangun ulang — self-healing.
        _cache.update({"fp": fp if prods else None, "ts": time.time(),
                       "products": prods, "kategori_counts": counts})
    return _cache["products"]


# ── Stok di lokasi pembeli (scope + reservasi — konsisten dgn /search) ───────
def _scoped_stock(p: dict, username: str, own: Optional[str],
                  all_names: list[str], resv: dict) -> tuple[int, str]:
    """(qty, label gudang) stok yang boleh dilihat/dibeli pembeli ini.
    Gudang pembeli dulu; bila kosong → FALLBACK gudang terdekat yang masih ada
    stok (keputusan pemilik 2026-07-12: pembeli tetap bisa membeli, badge
    menunjukkan gudang pengirim — 'READY · <gudang>'; ongkir dihitung dari
    gudang pemenuh itu). Reservasi dikurangkan SEBELUM scoping supaya fallback
    tetap bekerja saat gudang sendiri habis direservasi pembeli lain."""
    bd = gudang.shippable(p["gudang"])      # gudang non-kirim (mis. B80) tak menawarkan barang
    if resv:
        pn = p["part_number"]
        bd = {g: q - resv.get((pn, g), 0) for g, q in bd.items()}
        bd = {g: q for g, q in bd.items() if q > 0}
    bd = gudang.scope_breakdown(bd, username, "pembeli", all_names, own=own)
    if not bd:
        return 0, ""
    g, q = next(iter(bd.items()))
    return int(q), gudang.gudang_label(g)


# ── Terlaris (agregat order_items, cache 15 menit) ───────────────────────────
_laris_cache: dict[str, Any] = {"ts": 0.0, "map": {}}
_LARIS_TTL = 900.0


def _terlaris_map() -> dict[str, int]:
    """{PN_UPPER: total qty terjual} dari order_items. {} bila Supabase mati."""
    now = time.time()
    if now - _laris_cache["ts"] < _LARIS_TTL:
        return _laris_cache["map"]
    m: dict[str, int] = {}
    try:
        from .orders import _rest_url, _service_headers, _TIMEOUT
        resp = requests.get(
            _rest_url("order_items"),
            headers={**_service_headers(), "Accept": "application/json"},
            params={"select": "part_number,qty", "limit": "10000"},
            timeout=_TIMEOUT,
        )
        if resp.status_code == 200:
            for r in resp.json() or []:
                pn = str(r.get("part_number") or "").strip().upper()
                if pn:
                    m[pn] = m.get(pn, 0) + int(r.get("qty") or 0)
    except Exception:
        m = dict(_laris_cache["map"])   # pertahankan data lama bila gagal
    _laris_cache.update({"ts": now, "map": m})
    return m


# ── Pencarian di dalam katalog etalase ───────────────────────────────────────
def _match_q(products: list[dict], q: str) -> list[tuple[dict, int]]:
    """(produk, peringkat) yang cocok dengan `q`. Peringkat kecil = lebih relevan:
    0 = kata query ada langsung di nama/PN, 1..N = cocok lewat istilah sinonim ke-N
    (istilah paling spesifik lebih dulu). Peringkat inilah yang dipakai urutan
    'Paling relevan' — tanpa itu hasil terurut alfabetis dan 'kampas rem' malah
    dipimpin 'brake chamber'."""
    tokens = [t for t in q.upper().split() if t]
    if not tokens:
        return [(p, 0) for p in products]
    hits = [p for p in products if all(t in p["_hay"] for t in tokens)]
    if not hits and len(tokens) == 1:
        flat = _flat(tokens[0])
        if len(flat) >= 6:   # PN tanpa pemisah ('202V091007926')
            hits = [p for p in products if flat in p["_flat"]]
    if hits:
        return [(p, 0) for p in hits]

    # Istilah lapangan (kamus TERPUSAT yang sama dgn asisten — services/sinonim,
    # rombakan 2026-07-17: tak lagi impor dari ai_assistant): 'kampas rem' →
    # friction plate / brake lining / brake pad, 'spion' → mirror, dst.
    try:
        from . import sinonim as _sin
        terms, _matched = _sin.expand_query(q)
        kw_sinonim = terms[1:]                  # tanpa query asli (sudah dicoba di atas)
    except Exception:
        return []

    # Payung kategori ('rem' → SEMUA part rem) hanya untuk query KATA KATEGORI POLOS.
    # Untuk 'kampas rem', payung akan menyeret brake chamber/ABS/air compressor —
    # pembeli minta kampasnya, bukan seisi kategori. Dipakai juga sebagai upaya
    # terakhir bila sinonim tak menghasilkan apa pun.
    try:
        payung = [k for k in _sin.umbrella_keywords(q) if k not in terms]
    except Exception:
        payung = []
    kategori_polos = len(tokens) == 1        # 'rem' → ya; 'kampas rem' → tidak
    urut = kw_sinonim + (payung if kategori_polos else [])

    out = _cocok_bertingkat(products, urut)
    if not out and payung:
        out = _cocok_bertingkat(products, payung)
    return out


def _cocok_bertingkat(products: list[dict], terms: list[str]) -> list[tuple[dict, int]]:
    """Cocokkan `terms` berurutan; produk mendapat peringkat = posisi istilah yang
    pertama kali mencocokkannya (istilah lebih awal = lebih spesifik = lebih relevan)."""
    out: list[tuple[dict, int]] = []
    seen: set[str] = set()
    for i, t in enumerate(terms, start=1):
        tu = t.upper()
        for p in products:
            if tu in p["_hay"] and p["part_number"] not in seen:
                seen.add(p["part_number"])
                out.append((p, i))
    return out


def _strip_internal(p: dict, stok: int, label: str) -> dict:
    return {
        "part_number": p["part_number"],
        "name": p["name"],
        "harga": p["harga"],
        "harga_display": p["harga_display"],
        "berat": p["berat"],
        "foto": p["foto"],
        "kategori": p["kategori"],
        "ready": stok > 0,
        "stok": stok,
        "gudang": label,
    }


# ── API service ──────────────────────────────────────────────────────────────
def catalog_page(username: str, buyer_gudang_key: Optional[str], q: str = "",
                 kategori: str = "", sort: str = "relevan", ready_only: bool = False,
                 page: int = 1, page_size: int = 24) -> dict:
    """Satu halaman katalog etalase untuk pembeli ini (stok sudah di-scope)."""
    products = _products()
    own = gudang.buyer_label(buyer_gudang_key)
    all_names = part_index.gudang_names()
    resv = reservations.reserved_map()

    q = (q or "").strip()
    if q:
        cocok = _match_q(products, q)
    else:
        cocok = [(p, 0) for p in products]
    kategori = (kategori or "").strip().lower()
    if kategori:
        cocok = [(p, r) for p, r in cocok if kategori in p["kategori"]]

    rows: list[tuple[dict, int, str, int]] = []
    for p, rank in cocok:
        stok, label = _scoped_stock(p, username, own, all_names, resv)
        if ready_only and stok <= 0:
            continue
        rows.append((p, stok, label, rank))

    sort = sort if sort in _SORTS else "relevan"
    if sort == "harga_asc":
        rows.sort(key=lambda r: r[0]["harga"])
    elif sort == "harga_desc":
        rows.sort(key=lambda r: -r[0]["harga"])
    elif sort == "nama":
        rows.sort(key=lambda r: r[0]["name"])
    elif sort == "stok":
        rows.sort(key=lambda r: -r[1])
    else:  # relevan: kecocokan query dulu, baru READY, berfoto, nama
        rows.sort(key=lambda r: (r[3], r[1] <= 0, r[0]["foto"] is None, r[0]["name"]))

    total = len(rows)
    page_size = max(1, min(int(page_size or 24), 100))
    total_pages = max(1, (total + page_size - 1) // page_size)
    page = min(max(1, int(page or 1)), total_pages)
    start = (page - 1) * page_size
    items = [_strip_internal(p, s, g) for p, s, g, _rank in rows[start:start + page_size]]
    return {
        "items": items,
        "count": total,
        "page": page,
        "page_size": page_size,
        "total_pages": total_pages,
        "lokasi": gudang.gudang_label(own) if own else None,
    }


def home_payload(username: str, buyer_gudang_key: Optional[str]) -> dict:
    """Data beranda etalase: lokasi, kategori (dgn jumlah), terlaris, unggulan."""
    products = _products()
    own = gudang.buyer_label(buyer_gudang_key)
    all_names = part_index.gudang_names()
    resv = reservations.reserved_map()
    counts = dict(_cache.get("kategori_counts") or {})

    def _take(pool: list[dict], n: int) -> list[dict]:
        out = []
        for p in pool:
            stok, label = _scoped_stock(p, username, own, all_names, resv)
            out.append(_strip_internal(p, stok, label))
            if len(out) >= n:
                break
        return out

    # Terlaris: urut qty terjual, hanya yang ada di etalase.
    laris = _terlaris_map()
    by_pn = {p["part_number"]: p for p in products}
    laris_products = [by_pn[pn] for pn, _q in
                      sorted(laris.items(), key=lambda kv: -kv[1]) if pn in by_pn]

    # Unggulan: produk berfoto (kartu paling menjual), stok total terbanyak dulu.
    unggulan_pool = sorted((p for p in products if p["foto"]),
                           key=lambda p: -p["stok_total"])

    return {
        "lokasi": gudang.gudang_label(own) if own else None,
        "total_produk": len(products),
        "kategori": [
            {"key": key, "label": _KATEGORI_LABEL[key], "count": counts.get(key, 0)}
            for key, _label, _kws in _KATEGORI if counts.get(key, 0) > 0
        ],
        "terlaris": _take(laris_products, 8),
        "unggulan": _take(unggulan_pool, 12),
    }


def invalidate_cache() -> None:
    """Paksa build ulang katalog etalase pada permintaan berikutnya (utk test/admin)."""
    _cache.update({"fp": None, "ts": 0.0})
    _laris_cache.update({"ts": 0.0, "map": {}})
