# -*- coding: utf-8 -*-
"""Stok PEMASOK Weichai (portal reseller ``weichai.tci-pnp.com/warehouse``).

Diambil **LIVE ON-DEMAND** — hanya saat user menekan "Cek stok Weichai" di
halaman part. BUKAN panen/index massal (keputusan pemilik 2026-08-25): kartu
hasil ``search`` sudah memuat ``unit_id``+``brand_id``+qty, jadi tak perlu
index apa pun.

Alur (terverifikasi live 2026-08-25, lihat backend/tools/scrape_weichai_stock.py):
  • Login  : GET /sso/auth/login → csrf_token (hidden, kutip TUNGGAL) → POST sama.
             ⚠️ Cloudflare: WAJIB User-Agent browser, kalau tidak 403.
             Cookie ``weichai_session`` dipakai ulang (sesi panjang, re-login
             saat kedaluwarsa) — pola ``accurate.py``.
  • Cari   : GET /warehouse/p/1/catalogue?filter=1&search=<PN>&availability=all
                  &branch[]=6&5&4&3&1&... → kartu {barcode, unit, brand, qty, satuan}.
             ⛔ search box TAK andal utk barcode numerik penuh & dash wajib —
             maka kita cocokkan barcode kartu === PN yg diminta (buang nihil palsu).
  • Detail : GET /catalogue/view?barcode=&unit=&brand= → breakdown per-cabang.
             ⛔ ketiga param wajib benar (unit salah → stok 0 palsu).

⛔ Portal TIDAK memberi HARGA (Rp=0) — ini STOK saja. Harga jual dari Accurate.

Non-fatal: semua kegagalan dikembalikan sebagai status, bukan exception ke router.
Cache per-PN pendek (TTL) supaya klik berulang / beberapa staf tak menggempur portal.
"""
from __future__ import annotations

import html
import json
import re
import threading
import time
from pathlib import Path
from urllib.parse import quote

import requests

from ..core.config import get_settings

_LOG_BASE = "https://weichai.tci-pnp.com"
_LOGIN = (_LOG_BASE + "/sso/auth/login?redirect="
          "https%3A%2F%2Fweichai.tci-pnp.com%2Fwarehouse%2Fwelcome")
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
_BRANCHES = ["6", "5", "4", "3", "1"]


def _cred_file() -> Path:
    # <DATA_DIR>/weichai_stock_cred.json — DATA_DIR = bind-mount di server
    # (pola accurate._session_file), BUKAN path relatif kode (beda di container).
    return get_settings().data_path / "weichai_stock_cred.json"

_CARD_SPLIT = 'class="col-sm-6 col-md-4 product-item"'

# Sesi & cache (proses-lokal, pola accurate.py)
_lock = threading.Lock()
_session: requests.Session | None = None
_login_fail_until = 0.0
_LOGIN_COOLDOWN = 120.0                 # jeda setelah login GAGAL
_CACHE: dict[str, tuple[float, dict]] = {}
_CACHE_TTL = 300.0                      # 5 menit


class WeichaiError(RuntimeError):
    pass


def _creds() -> tuple[str, str] | None:
    s = get_settings()
    if s.weichai_pnp_configured:
        return s.weichai_pnp_username, s.weichai_pnp_password
    try:
        cf = _cred_file()
        if cf.exists():
            d = json.loads(cf.read_text(encoding="utf-8"))
            u, p = d.get("username"), d.get("password")
            if u and p:
                return u, p
    except Exception:                   # noqa: BLE001
        pass
    return None


def available() -> bool:
    """True bila kredensial portal tersedia (env atau file)."""
    return _creds() is not None


def _new_session() -> requests.Session:
    cred = _creds()
    if not cred:
        raise WeichaiError("no_credentials")
    u, p = cred
    s = requests.Session()
    s.headers.update({"User-Agent": _UA,
                      "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
                      "Accept-Language": "en-US,en;q=0.9"})
    lp = s.get(_LOGIN, timeout=25)
    lp.raise_for_status()
    m = re.search(r"name='csrf_token' value='([0-9a-f]+)'", lp.text)
    if not m:
        raise WeichaiError("no_csrf")
    r = s.post(_LOGIN, timeout=25, data={
        "csrf_token": m.group(1), "username": u, "password": p, "remember": "1"})
    r.raise_for_status()
    chk = s.get(_cat_url(""), timeout=25)
    if "Product Catalogue" not in chk.text:
        raise WeichaiError("login_failed")
    return s


def _ensure_session() -> requests.Session:
    """Sesi login yang dipakai ulang; re-login bila belum ada/kedaluwarsa."""
    global _session, _login_fail_until
    if _session is not None:
        return _session
    if time.time() < _login_fail_until:
        raise WeichaiError("login_cooldown")
    try:
        _session = _new_session()
    except Exception as e:              # noqa: BLE001
        _login_fail_until = time.time() + _LOGIN_COOLDOWN
        raise WeichaiError(f"login_error:{type(e).__name__}") from e
    return _session


def _cat_url(pn: str) -> str:
    br = "".join(f"&branch%5B%5D={b}" for b in _BRANCHES)
    q = f"&search={quote(pn)}" if pn else ""
    return (f"{_LOG_BASE}/warehouse/p/1/catalogue?filter=1{q}"
            f"&availability=all&order_by=latest{br}&brand=0&type_goods=0")


def _unescape(s: str) -> str:
    return re.sub(r"\\(.)", r"\1", html.unescape(s)).strip()


def _num(s: str) -> float | int:
    s = s.replace(".", "").replace(",", ".")
    try:
        f = float(s)
    except ValueError:
        return 0
    return int(f) if f.is_integer() else f


def _parse_cards(text: str) -> list[dict]:
    out = []
    for b in text.split(_CARD_SPLIT)[1:]:
        barcode = re.search(r'data-barcode="([^"]+)"', b)
        if not barcode:
            continue
        unit = re.search(r'data-id-unit="(\d+)"', b)
        brand = re.search(r'data-id-brand="(\d+)"', b)
        name = re.search(r'<h4 class="mt0 mb0" title="([^"]*)"', b)
        avail = re.search(r'AVAILABLE</p>\s*<h4[^>]*>\s*([\d.,]+)\s*([A-Za-z]+)', b)
        out.append({
            "barcode": barcode.group(1),
            "nama": _unescape(name.group(1)) if name else "",
            "unit_id": unit.group(1) if unit else "",
            "brand_id": brand.group(1) if brand else "",
            "qty": _num(avail.group(1)) if avail else 0,
            "satuan": avail.group(2) if avail else "",
        })
    return out


def _parse_branches(view_html: str) -> list[dict]:
    i = view_html.find("Order By Location")
    if i < 0:
        return []
    seg = view_html[i:i + 8000]
    rows = re.findall(
        r'<td class="bold">([^<]+)</td>\s*<td>\s*([\d.,]+)\s*([A-Za-z]+)', seg)
    return [{"cabang": c.strip(), "qty": _num(q), "satuan": u} for c, q, u in rows]


def _norm(pn: str) -> str:
    return re.sub(r"\s+", "", (pn or "")).upper()


def _fetch(pn: str) -> dict:
    """Panggilan LIVE ke portal — dibungkus cache oleh stok()."""
    s = _ensure_session()
    r = s.get(_cat_url(pn), timeout=25)
    if "Product Catalogue" not in r.text:       # sesi mati → sekali re-login
        global _session
        _session = None
        s = _ensure_session()
        r = s.get(_cat_url(pn), timeout=25)
    cards = _parse_cards(r.text)
    want = _norm(pn)
    # ⛔ cocokkan barcode PERSIS — search box bisa mengembalikan hasil nama yang
    # kebetulan mengandung angka; kita hanya mau part dgn barcode === PN diminta.
    exact = [c for c in cards if _norm(c["barcode"]) == want]
    hit = exact[0] if exact else None
    if not hit:
        return {"configured": True, "found": False}
    # breakdown per-cabang (live) — non-fatal; bila gagal, total tetap tampil.
    per_cabang: list[dict] = []
    try:
        vu = (f"{_LOG_BASE}/warehouse/p/1/catalogue/view?barcode={quote(hit['barcode'])}"
              f"&unit={hit['unit_id']}&brand={hit['brand_id']}")
        per_cabang = _parse_branches(s.get(vu, timeout=25).text)
    except Exception:                   # noqa: BLE001
        per_cabang = []
    return {
        "configured": True,
        "found": True,
        "stock": {
            "barcode": hit["barcode"],
            "nama": hit["nama"],
            "total": hit["qty"],
            "satuan": hit["satuan"],
            "per_cabang": per_cabang,
        },
    }


def stok(pn: str) -> dict:
    """Stok pemasok Weichai untuk 1 PN. Non-fatal — selalu balas dict status:
      {configured:False}                      kredensial belum diset
      {configured:True, found:False}          PN tak ada di Weichai
      {configured:True, error:True}           gagal login/koneksi
      {configured:True, found:True, stock:{…}} sukses (+ per_cabang bila ada)
    """
    if not available():
        return {"configured": False}
    key = _norm(pn)
    now = time.time()
    with _lock:
        cached = _CACHE.get(key)
        if cached and now - cached[0] < _CACHE_TTL:
            return cached[1]
    try:
        res = _fetch(pn)
    except WeichaiError:
        return {"configured": True, "error": True}
    except Exception:                   # noqa: BLE001
        return {"configured": True, "error": True}
    with _lock:
        _CACHE[key] = (now, res)
    return res
