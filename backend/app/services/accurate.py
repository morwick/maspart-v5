"""
Service Accurate Online — stok live dari ERP Accurate (iris.accurate.id).

Auth Accurate = sesi SSO: cookie **JSESSIONID** + parameter **_dsi** (keduanya
didapat dari login SAML di account.accurate.id). Sesi disimpan di
``data/accurate_session.json`` dan **dibaca segar tiap panggil** (pola sama dengan
``epc_token.txt``) → sesi baru cukup ditimpa ke file tanpa restart.

Endpoint data (dibedah dari lalu-lintas web resmi, terverifikasi live):

    POST {BASE}/accurate/inventory/search-item.do
    body: _dsi=<dsi>&keywords=<q>&resetFilter=false
          &sp.pageSize=<N>&sp.start=<OFF>&sp.limit=<N>
    → {"s": true, "d": [ {item...} ], "sp": {rowCount, pageCount, ...}}

Sesi mati → server MEMANTULKAN ke SSO dan membalas **HTML** (form SAML), bukan
JSON. Terdeteksi via ``_looks_expired`` → naikkan ``AccurateSessionExpired``.

Field penting per item (dari response nyata):
  no             kode barang, mis. "003330.WG9725220536+305" (ada prefix "NNNNNN.")
  name           nama/uraian barang
  availableToSell stok yang dapat dijual (dipakai sebagai "stok")
  quantity        kuantitas gudang
  unit1.name      satuan (Pc/Liter/Kg…)
  itemTypeName    "Persediaan" / "Jasa" / dll
  id              id internal Accurate
"""
from __future__ import annotations

import base64
import html as _html
import json
import logging
import re
import threading
import time
from pathlib import Path
from typing import Any, Iterable

import requests

from ..core.config import get_settings

logger = logging.getLogger("maspart.accurate")

# ── Konfigurasi ────────────────────────────────────────────────────────────
def _base() -> str:
    """Base URL app perusahaan (host/zona dari config, mis. iris.accurate.id)."""
    return f"https://{get_settings().accurate_host}"

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36"
)

# Ukuran halaman saat menarik seluruh katalog (server terbukti melayani 200/҂call).
_PAGE_SIZE = 200
_HTTP_TIMEOUT = 30

# TTL cache indeks stok penuh (detik). Menu Stok cukup segar tiap 5 JAM
# (permintaan pemilik 2026-07-06); butuh angka terkini sekarang → admin punya
# endpoint refresh(force=True) (POST /api/stok/refresh).
_INDEX_TTL = 5 * 60 * 60


def _session_file() -> Path:
    return get_settings().data_path / "accurate_session.json"


# ── Sesi (JSESSIONID + _dsi) ───────────────────────────────────────────────
class AccurateError(RuntimeError):
    """Kesalahan umum saat berkomunikasi dengan Accurate."""


class AccurateSessionMissing(AccurateError):
    """File sesi belum ada / tak lengkap (perlu diisi JSESSIONID + _dsi)."""


class AccurateSessionExpired(AccurateError):
    """Sesi Accurate kadaluarsa — server memantulkan ke login SSO."""


def load_session() -> dict[str, str]:
    """Baca ``data/accurate_session.json`` → {"jsessionid", "dsi", ...}.

    Dibaca SEGAR tiap panggil (tak di-cache) supaya penggantian sesi via file
    langsung berlaku tanpa restart. Naikkan ``AccurateSessionMissing`` bila file
    tak ada atau field wajib kosong.
    """
    fp = _session_file()
    try:
        raw = json.loads(fp.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise AccurateSessionMissing(
            f"File sesi Accurate belum ada: {fp}. Isi JSESSIONID + _dsi "
            "(lihat save_session / tools/accurate_set_session)."
        )
    except (OSError, json.JSONDecodeError) as e:
        raise AccurateSessionMissing(f"File sesi Accurate tak terbaca: {e}") from e

    jsid = str(raw.get("jsessionid") or raw.get("JSESSIONID") or "").strip()
    dsi = str(raw.get("dsi") or raw.get("_dsi") or "").strip()
    if not jsid or not dsi:
        raise AccurateSessionMissing(
            "Sesi Accurate tak lengkap: butuh 'jsessionid' dan 'dsi' di "
            f"{fp}."
        )
    return {"jsessionid": jsid, "dsi": dsi, **{k: v for k, v in raw.items()
                                              if k not in ("jsessionid", "dsi")}}


def save_session(jsessionid: str, dsi: str) -> Path:
    """Tulis pasangan sesi ke file (dipakai auto-login tahap-2 & pengisian manual)."""
    jsid = (jsessionid or "").strip()
    d = (dsi or "").strip()
    if not jsid or not d:
        raise ValueError("jsessionid dan dsi wajib diisi.")
    fp = _session_file()
    fp.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "jsessionid": jsid,
        "dsi": d,
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    fp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return fp


def session_status() -> dict[str, Any]:
    """Ringkasan status sesi tanpa membocorkan nilai penuh (untuk endpoint admin)."""
    try:
        s = load_session()
    except AccurateSessionMissing as e:
        return {"configured": False, "detail": str(e)}
    return {
        "configured": True,
        "jsessionid_tail": s["jsessionid"][-8:],
        "dsi_len": len(s["dsi"]),
        "updated_at": s.get("updated_at"),
    }


# ── Auto-login SSO (account.accurate.id → SAML → iris; per-VIN dsi) ─────────
# Alur (dibedah & terverifikasi live):
#   GET  account.accurate.id/            → seed cookie
#   POST /pre-login.do (account,password="up"+b64{v,p,d})       → {d.permit}
#   POST /auth.do (j_username,j_password="ua"+b64{v,p,c=permit,t:null,d})  → 302 /manage
#   GET  iris/accurate/open.do?uid=<uid>&product=aol            → form SAML auto-submit
#        → POST account/idp/sso (SAMLRequest) → POST iris/accurate/saml/SSO (SAMLResponse)
#   POST iris/accurate/open-database.do (uid,product=aol)       → {dsi}
#   JSESSIONID iris diambil dari cookie jar.
_ACCOUNT_BASE = "https://account.accurate.id"
_login_lock = threading.Lock()


def credentials_configured() -> bool:
    return get_settings().accurate_login_configured


def _b64(x: str) -> str:
    return base64.b64encode(x.encode("utf-8")).decode("ascii")


def _parse_form_inputs(fragment: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for tag in re.findall(r"<input\b[^>]*>", fragment, re.I):
        n = re.search(r'name=["\']([^"\']+)["\']', tag)
        v = re.search(r'value=["\']([^"\']*)["\']', tag)
        if n:
            out[_html.unescape(n.group(1))] = _html.unescape(v.group(1) if v else "")
    return out


def _get_form(body: str) -> tuple[str | None, str | None]:
    m = re.search(r"<form\b([^>]*)>(.*?)</form>", body, re.S | re.I)
    if not m:
        return None, None
    am = re.search(r'action=["\']([^"\']+)["\']', m.group(1))
    return (_html.unescape(am.group(1)) if am else None), m.group(2)


def _follow_saml(sess: requests.Session, resp: requests.Response, max_hops: int = 6) -> requests.Response:
    """Ikuti rantai form SAML auto-submit sampai tak ada form lagi."""
    for _ in range(max_hops):
        action, inner = _get_form(resp.text)
        if not action or inner is None:
            break
        resp = sess.post(action, data=_parse_form_inputs(inner), timeout=_HTTP_TIMEOUT)
    return resp


def login() -> dict[str, str]:
    """Login otomatis penuh (SSO) → tulis & kembalikan {jsessionid, dsi}.

    Naikkan ``AccurateSessionMissing`` bila kredensial tak lengkap, atau
    ``AccurateError`` bila alur gagal (mis. akun minta 2FA/OTP).
    """
    cfg = get_settings()
    if not cfg.accurate_login_configured:
        raise AccurateSessionMissing(
            "Kredensial auto-login Accurate belum lengkap (ACCURATE_USERNAME/"
            "PASSWORD/DEVICE_ID/UID)."
        )
    user, pw = cfg.accurate_username, cfg.accurate_password
    device, uid, host = cfg.accurate_device_id, cfg.accurate_uid, cfg.accurate_host
    s = requests.Session()
    s.headers.update({"User-Agent": _UA, "Accept-Language": "id,en-US;q=0.9,en;q=0.8"})
    try:
        s.get(f"{_ACCOUNT_BASE}/", timeout=_HTTP_TIMEOUT)
        # 1) pre-login → challenge 'permit'
        pw_pre = "up" + _b64(json.dumps({"v": 1, "p": pw, "d": device}, separators=(",", ":")))
        r = s.post(f"{_ACCOUNT_BASE}/pre-login.do",
                   data={"account": user, "password": pw_pre},
                   headers={"X-Requested-With": "XMLHttpRequest", "Referer": f"{_ACCOUNT_BASE}/"},
                   timeout=_HTTP_TIMEOUT)
        try:
            d = (r.json() or {}).get("d") or {}
        except ValueError:
            raise AccurateError("pre-login.do gagal (respons bukan JSON).")
        permit = d.get("permit")
        if d.get("requireTotp") or d.get("isEmail2FA"):
            raise AccurateError("Akun Accurate meminta 2FA/OTP — auto-login tak didukung.")
        if not permit:
            raise AccurateError(f"pre-login.do tak mengembalikan challenge: {str(d)[:150]}")
        # 2) auth.do (Spring Security) → sesi account terautentikasi
        jpw = "ua" + _b64(json.dumps({"v": 1, "p": pw, "c": permit, "t": None, "d": device},
                                     separators=(",", ":")))
        s.post(f"{_ACCOUNT_BASE}/auth.do",
               data={"j_username": user, "j_password": jpw},
               headers={"Referer": f"{_ACCOUNT_BASE}/"}, timeout=_HTTP_TIMEOUT)
        # 3) open.do → SAML → sesi iris terautentikasi (JSESSIONID)
        resp = s.get(f"https://{host}/accurate/open.do?uid={uid}&product=aol", timeout=_HTTP_TIMEOUT)
        resp = _follow_saml(s, resp)
        # 4) open-database.do → dsi segar
        od = s.post(f"https://{host}/accurate/open-database.do",
                    data={"uid": uid, "product": "aol"},
                    headers={"X-Requested-With": "XMLHttpRequest", "Referer": resp.url},
                    timeout=_HTTP_TIMEOUT)
        try:
            oj = od.json()
        except ValueError:
            raise AccurateError("open-database.do bukan JSON — login/SAML kemungkinan gagal.")
        dsi = oj.get("dsi")
        if not dsi:
            raise AccurateError(f"open-database.do tak mengembalikan dsi: {str(oj)[:150]}")
    except requests.RequestException as e:
        raise AccurateError(f"Auto-login Accurate gagal (jaringan): {e}") from e

    jsid = next((c.value for c in s.cookies
                 if c.name == "JSESSIONID" and host in (c.domain or "")), None)
    if not jsid:
        jsid = next((c.value for c in s.cookies if c.name == "JSESSIONID"), None)
    if not jsid:
        raise AccurateError("JSESSIONID iris tak ditemukan setelah login.")
    save_session(jsid, dsi)
    return {"jsessionid": jsid, "dsi": dsi}


# Cooldown login: setelah login GAGAL (mis. Accurate throttle → errorTimeout),
# JANGAN coba login lagi selama _LOGIN_COOLDOWN_SEC — pakai fallback lokal dulu.
# Mencegah hantaman login berulang yang bisa memperpanjang throttle / terlihat abnormal.
_LOGIN_COOLDOWN_SEC = 300        # backoff login latar (refresh stok). Aksi user
                                 # (buat penawaran) memakai login_now() yg MENGABAIKAN
                                 # cooldown ini → penawaran tak terblokir backoff latar.
_login_fail_until = 0.0


def _login_guarded() -> dict[str, str]:
    """login() dengan gerbang cooldown (dipanggil di dalam _login_lock)."""
    global _login_fail_until
    if time.time() < _login_fail_until:
        raise AccurateSessionExpired(
            "Login Accurate sedang cooldown setelah gagal berturut — pakai fallback."
        )
    try:
        return login()
    except AccurateError:
        _login_fail_until = time.time() + _LOGIN_COOLDOWN_SEC
        raise


# ── Logout & idle-logout ────────────────────────────────────────────────────
# Aturan keras pemilik: akun Accurate hanya boleh 1 SESI/perangkat. Selama MASPART
# memegang sesi, orang lain TAK BISA login untuk membuat penawaran. Maka MASPART
# harus melepas sesi secepatnya: logout SETELAH membuat penawaran, dan otomatis
# logout saat IDLE. Aman: _ensure_session auto-login lagi saat file sesi hilang,
# jadi stok/harga tetap jalan (login on-demand). Cache data indeks TAK terpengaruh.
# Ambang idle 2 mnt = kompromi: cukup singkat agar orang lain cepat bisa masuk,
# tapi tak thrashing login (tiap login SSO menendang sesi orang lain).
_IDLE_LOGOUT_SEC = 120              # 2 menit tanpa aktivitas → logout
_last_activity = time.monotonic()
_idle_started = False
_idle_lock = threading.Lock()


def _mark_activity() -> None:
    global _last_activity
    _last_activity = time.monotonic()


def logout() -> bool:
    """Tutup sesi Accurate (best-effort): close-database.do lalu hapus file sesi
    → panggilan berikutnya auto-login segar. TAK PERNAH melempar."""
    try:
        s = load_session()
    except AccurateSessionMissing:
        return True                 # sudah tak ada sesi
    try:
        requests.post(f"{_base()}/accurate/close-database.do",
                      data={"_dsi": s["dsi"]},
                      headers={"User-Agent": _UA, "X-Requested-With": "XMLHttpRequest",
                               "Origin": _base(), "Referer": f"{_base()}/accurate/?_dsi={s['dsi']}",
                               "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                               "Cookie": f"JSESSIONID={s['jsessionid']}"},
                      timeout=_HTTP_TIMEOUT, allow_redirects=False)
    except Exception:
        pass                        # server mungkin sudah menutup; tetap hapus file
    try:
        _session_file().unlink(missing_ok=True)
    except OSError:
        pass
    logger.info("Accurate: sesi ditutup (logout).")
    return True


def _idle_logout_loop() -> None:
    while True:
        time.sleep(60)
        try:
            idle = time.monotonic() - _last_activity
            if idle > _IDLE_LOGOUT_SEC and _session_file().exists():
                logout()
        except Exception:           # daemon tak boleh mati
            pass


def start_idle_logout() -> bool:
    """Mulai daemon auto-logout saat idle. Idempoten (aman dipanggil berulang)."""
    global _idle_started
    with _idle_lock:
        if _idle_started:
            return False
        _idle_started = True
    threading.Thread(target=_idle_logout_loop, daemon=True,
                     name="accurate-idle-logout").start()
    return True


def _ensure_session() -> dict[str, str]:
    """Sesi siap-pakai: dari file, atau auto-login bila file kosong & kredensial ada."""
    try:
        return load_session()
    except AccurateSessionMissing:
        if not credentials_configured():
            raise
    with _login_lock:
        try:  # thread lain mungkin sudah login
            return load_session()
        except AccurateSessionMissing:
            return _login_guarded()


def _refresh_session() -> dict[str, str]:
    """Paksa login ulang (dipanggil saat sesi kadaluarsa). Thread-safe + cooldown."""
    with _login_lock:
        return _login_guarded()


def login_now() -> dict[str, str]:
    """Login SEGERA, MENGABAIKAN cooldown backoff — untuk aksi yang DIPICU USER
    (mis. buat penawaran) yang tak boleh terblokir backoff refresh-stok latar.
    Tetap thread-safe & tetap MENGARM cooldown bila gagal (agar latar tak hajar)."""
    global _login_fail_until
    with _login_lock:
        try:
            return login()
        except AccurateError:
            _login_fail_until = time.time() + _LOGIN_COOLDOWN_SEC
            raise


def ensure_session_force() -> dict[str, str]:
    """Sesi siap-pakai untuk aksi user-triggered: pakai file bila ada, kalau tidak
    login SEGERA (abaikan cooldown). Raise bila kredensial tak ada / login gagal."""
    try:
        return load_session()
    except AccurateSessionMissing:
        if not credentials_configured():
            raise
        return login_now()


# ── HTTP ───────────────────────────────────────────────────────────────────
def _looks_expired(resp: requests.Response) -> bool:
    """True bila respons adalah pantulan SSO (HTML/SAML), bukan JSON data."""
    ctype = (resp.headers.get("Content-Type") or "").lower()
    if "application/json" in ctype:
        return False
    head = resp.text[:600].lstrip()
    if head.startswith("{"):
        return False
    # HTML SAML auto-submit atau apa pun non-JSON = sesi tak dikenali.
    return True


def _call(session: dict[str, str], path: str, data: dict[str, Any]) -> dict[str, Any]:
    """POST ke satu endpoint `.do` Accurate (auth via _dsi body + JSESSIONID cookie).

    Naikkan ``AccurateSessionExpired`` bila server memantulkan ke SSO (HTML/redirect),
    ``AccurateError`` bila respons bukan JSON atau ``s=false``.
    """
    dsi = session["dsi"]
    headers = {
        "User-Agent": _UA,
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "X-Requested-With": "XMLHttpRequest",
        "Origin": _base(),
        "Referer": f"{_base()}/accurate/?_dsi={dsi}",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "Cookie": f"JSESSIONID={session['jsessionid']}",
    }
    _mark_activity()   # jaga sesi hidup selama masih dipakai (idle-logout menunda)
    try:
        resp = requests.post(f"{_base()}/accurate/{path}", data={"_dsi": dsi, **data},
                             headers=headers, timeout=_HTTP_TIMEOUT, allow_redirects=False)
    except requests.RequestException as e:
        raise AccurateError(f"Gagal menghubungi Accurate: {e}") from e
    if resp.status_code in (301, 302, 303, 307, 308):
        raise AccurateSessionExpired("Sesi Accurate kadaluarsa (redirect ke SSO).")
    if _looks_expired(resp):
        raise AccurateSessionExpired("Sesi Accurate kadaluarsa — server memantulkan ke login SSO.")
    try:
        j = resp.json()
    except ValueError as e:
        raise AccurateError(f"Respons Accurate bukan JSON valid: {e}") from e
    if not j.get("s", False):
        raise AccurateError(f"Accurate menolak permintaan: {str(j)[:200]}")
    return j


def _search_items_raw(session: dict[str, str], *, keywords: str = "", start: int = 0,
                      limit: int = _PAGE_SIZE) -> dict[str, Any]:
    """Satu panggilan search-item.do → dict JSON ter-parse."""
    return _call(session, "inventory/search-item.do", {
        "keywords": keywords or "", "resetFilter": "false",
        "sp.pageSize": str(limit), "sp.start": str(start), "sp.limit": str(limit),
    })


def _warehouse_raw(session: dict[str, str], item_id: Any) -> dict[str, Any]:
    """view-itemstock-bywarehouse.do → stok per gudang TERKINI untuk 1 item id.
    Endpoint resmi UI Accurate (tab Stok barang). Field: detailWarehouseData[]."""
    return _call(session, "inventory/view-itemstock-bywarehouse.do", {"id": str(item_id)})


def _search_retry(*, keywords: str = "", start: int = 0, limit: int = _PAGE_SIZE) -> dict[str, Any]:
    """Satu panggilan search dengan auto-login: bila sesi kadaluarsa & kredensial
    tersedia → login ulang sekali lalu ulangi. Bila tak ada kredensial, error sesi
    diteruskan (mode sesi-manual)."""
    sess = _ensure_session()
    try:
        return _search_items_raw(sess, keywords=keywords, start=start, limit=limit)
    except AccurateSessionExpired:
        if not credentials_configured():
            raise
        sess = _refresh_session()
        return _search_items_raw(sess, keywords=keywords, start=start, limit=limit)


def search_by_keyword(keyword: str, *, limit: int = 50) -> list[dict[str, Any]]:
    """Cari barang via filter ``keywords`` Accurate (server-side, cepat).

    ``keyword`` dicocokkan server ke kode/nama barang. Dipakai untuk lookup 1 PN
    on-demand tanpa menarik seluruh katalog. Auto-login saat sesi habis.
    """
    kw = (keyword or "").strip()
    if not kw:
        return []
    data = _search_retry(keywords=kw, start=0, limit=limit)
    return [normalize_item(x) for x in (data.get("d") or [])]


def stock_for_live(part_number: str) -> dict[str, Any] | None:
    """Stok Accurate untuk 1 PN via pencarian keyword server-side (cepat, no cache).

    Mengembalikan barang yang PN-ternormalisasinya PERSIS sama; bila tak ada yang
    persis, None (hindari salah-cocok ke barang lain yang kebetulan ikut terfilter).
    Bila ada beberapa yang persis, pilih stok tertinggi.
    """
    want = _norm_pn(part_number)
    if not want:
        return None
    hits = search_by_keyword(part_number, limit=50)
    exact = [h for h in hits if _norm_pn(h["pn"]) == want]
    if not exact:
        return None
    return max(exact, key=lambda h: h["available_to_sell"])


def _warehouse_retry(item_id: Any) -> dict[str, Any]:
    """Panggil view-itemstock-bywarehouse.do dgn auto-login saat sesi habis."""
    sess = _ensure_session()
    try:
        return _warehouse_raw(sess, item_id)
    except AccurateSessionExpired:
        if not credentials_configured():
            raise
        return _warehouse_raw(_refresh_session(), item_id)


# Cache stok per-PN (TTL pendek): tekan latensi detail_part & lalu-lintas Accurate
# saat PN sama dilihat berulang / dipanggil beberapa tool. Stok tak berubah tiap detik.
_STOCK_CACHE_TTL = 90
_stock_cache: dict[str, tuple[float, Any]] = {}
_stock_cache_lock = threading.Lock()


def stock_full(part_number: str) -> dict[str, Any] | None:
    """Stok Accurate 1 PN: agregat + harga dari **INDEKS BERSAMA** (tarikan
    terjadwal tiap ``_INDEX_TTL`` — TIDAK menembak pencarian Accurate per-PN),
    plus rincian **per gudang** yang tetap ditarik live per-PN (data per-gudang
    tidak ikut tarikan massal; 1 panggilan kecil, cache ~90 dtk, non-fatal).

    Non-blocking: indeks belum siap (cold start) / PN tak ada di Accurate →
    None → pemanggil fallback Excel. ``per_gudang`` = daftar {gudang, deskripsi,
    qty, gudang_id} untuk gudang berstok > 0 (urut qty menurun).
    """
    want = _norm_pn(part_number)
    if not want:
        return None
    now = time.time()
    with _stock_cache_lock:
        c = _stock_cache.get(want)
        if c and now - c[0] < _STOCK_CACHE_TTL:
            return c[1]

    def _cache(v: Any) -> Any:
        with _stock_cache_lock:
            _stock_cache[want] = (time.time(), v)
        return v

    entry = (_index_cache.get("by_pn") or {}).get(want)
    if not entry:
        return _cache(None)
    base = dict(entry)
    # Rincian per-gudang: UTAMA dari INDEKS (enrichment 5-jam, dibagi ke semua fitur —
    # tanpa panggilan live). Fallback SATU panggilan live per-PN hanya bila PN belum
    # ter-enrich (mis. window ~8 mnt setelah boot / barang baru berstok).
    gmap = (_index_cache.get("by_gudang") or {}).get(want)
    if gmap is not None:
        per = [{"gudang": g, "qty": q} for g, q in
               sorted(gmap.items(), key=lambda kv: kv[1], reverse=True)]
    else:
        per = []
        try:
            wd = _warehouse_retry(base.get("accurate_id")).get("d") or {}
            for w in wd.get("detailWarehouseData") or []:
                qty = _num(w.get("balance"))
                if qty <= 0:
                    continue
                per.append({
                    "gudang": w.get("warehouseName") or w.get("name") or "",
                    "deskripsi": w.get("description") or "",
                    "qty": qty,
                    "gudang_id": w.get("id"),
                })
            per.sort(key=lambda x: x["qty"], reverse=True)
        except AccurateError:
            per = []  # non-fatal: agregat tetap tampil
    base["per_gudang"] = per
    return _cache(base)


def fetch_all_items() -> list[dict[str, Any]]:
    """Tarik SELURUH barang (paging otomatis sampai rowCount habis). Untuk sync massal.
    Auto-login saat sesi habis (per halaman)."""
    first = _search_retry(start=0, limit=_PAGE_SIZE)
    row_count = int(first.get("sp", {}).get("rowCount") or 0)
    items: list[dict[str, Any]] = list(first.get("d") or [])
    start = _PAGE_SIZE
    while start < row_count:
        page = _search_retry(start=start, limit=_PAGE_SIZE)
        batch = page.get("d") or []
        if not batch:
            break
        items.extend(batch)
        start += _PAGE_SIZE
    return items


# ── Parsing PN ─────────────────────────────────────────────────────────────
# Kode barang Accurate: "NNNNNN.<PN><+suffix>", mis. "003330.WG9725220536+305".
# Prefix "NNNNNN." = nomor urut internal Accurate (bukan bagian PN). Untuk barang
# non-part (oli/grease) sisa setelah titik berupa nama produk, bukan PN — pemanggil
# yang mencocokkan ke katalog memutuskan validitasnya.
_CODE_PREFIX_RE = re.compile(r"^\d{4,}\.")
_PN_SUFFIX_RE = re.compile(r"\+\d+$")


def parse_pn(no: str) -> str:
    """Ambil kandidat Part Number dari field ``no`` Accurate.

    "003330.WG9725220536+305" → "WG9725220536"
    "000674.SNPC TULUX ... 200L" → "SNPC TULUX ... 200L" (bukan PN — biar pemanggil menilai)
    """
    s = (no or "").strip()
    s = _CODE_PREFIX_RE.sub("", s, count=1)  # buang "003330."
    s = _PN_SUFFIX_RE.sub("", s.strip())     # buang "+305"
    return s.strip()


def _norm_pn(pn: str) -> str:
    """Normalisasi PN untuk pencocokan (uppercase, buang spasi & pemisah umum)."""
    return re.sub(r"[\s\-_/]", "", (pn or "").upper())


def norm_pn(pn: str) -> str:
    """Publik: normalisasi PN (dipakai router untuk cocokkan ke snapshot)."""
    return _norm_pn(pn)


def _num(v: Any) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def normalize_item(it: dict[str, Any]) -> dict[str, Any]:
    """Bentuk ringkas 1 barang Accurate untuk konsumsi internal."""
    no = str(it.get("no") or "")
    unit = ((it.get("unit1") or {}) or {}).get("name") or it.get("unit1Name") or ""
    return {
        "no": no,
        "pn": parse_pn(no),
        "name": (it.get("name") or "").strip(),
        "available_to_sell": _num(it.get("availableToSell")),
        "quantity": _num(it.get("quantity")),
        "unit": str(unit).strip(),
        "item_type": (it.get("itemTypeName") or "").strip(),
        # Harga JUAL satuan-1 dari Accurate; fallback ke branchPrice bila unitPrice 0.
        "price": _num(it.get("unitPrice")) or _num(it.get("branchPrice")),
        "accurate_id": it.get("id"),
    }


# ── Indeks stok (cache TTL) ────────────────────────────────────────────────
_index_lock = threading.Lock()
# by_gudang: {norm_pn: {warehouseName: qty}} — rincian per-gudang, diisi enrichment
# latar (enrich_warehouses) sekali per siklus 5-jam & DIBAGI ke semua fitur (stock_full,
# stok_gudang) tanpa panggilan live per-PN. gudang_ts = kapan enrichment terakhir tuntas.
_index_cache: dict[str, Any] = {"ts": 0.0, "items": [], "by_pn": {}, "by_gudang": {}, "gudang_ts": 0.0}


def _build_by_pn(items: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Peta PN-ternormalisasi → barang stok tertinggi (bila duplikat PN)."""
    by_pn: dict[str, dict[str, Any]] = {}
    for raw in items:
        n = normalize_item(raw)
        key = _norm_pn(n["pn"])
        if not key:
            continue
        prev = by_pn.get(key)
        if prev is None or n["available_to_sell"] > prev["available_to_sell"]:
            by_pn[key] = n
    return by_pn


def refresh(force: bool = False) -> dict[str, Any]:
    """Bangun/segarkan indeks stok penuh. Hormati TTL kecuali ``force``."""
    with _index_lock:
        age = time.time() - _index_cache["ts"]
        if not force and _index_cache["items"] and age < _INDEX_TTL:
            return _index_cache
        items = fetch_all_items()
        _index_cache["items"] = [normalize_item(x) for x in items]
        _index_cache["by_pn"] = _build_by_pn(items)
        # View ringkas utk overlay hasil pencarian (dipakai snapshot()) — dibangun
        # sekali di sini agar tiap request pencarian tinggal baca dict jadi.
        _index_cache["snap"] = {
            k: {"stok": v["available_to_sell"], "harga": v["price"], "unit": v["unit"]}
            for k, v in _index_cache["by_pn"].items()
        }
        _index_cache["ts"] = time.time()
        return _index_cache


# ── Enrichment stok PER-GUDANG ke indeks ────────────────────────────────────
# Rincian per-gudang TIDAK ikut tarikan massal search-item.do (itu agregat "[Semua
# Cabang]"); hanya endpoint per-item (view-itemstock-bywarehouse.do) yang punya. Karena
# Accurate men-serialkan panggilan per-sesi (~0,12 dtk/barang; ~8 mnt utk ~3.700 barang
# berstok), enrichment dijalankan SERIAL di THREAD LATAR (bukan di refresh() yang bisa
# dipicu on-demand) sekali per siklus 5-jam, lalu HASILNYA DIBAGI ke semua fitur.
_GUDANG_ENRICH_PUBLISH_EVERY = 100   # terbitkan hasil parsial tiap N barang
_gudang_enrich_running = False
_gudang_enrich_lock = threading.Lock()


def _warehouse_map(item_id: Any) -> dict[str, float]:
    """{warehouseName: qty>0} untuk 1 item id (live, 1 panggilan). {} bila gagal."""
    try:
        wd = _warehouse_retry(item_id).get("d") or {}
    except AccurateError:
        return {}
    out: dict[str, float] = {}
    for w in wd.get("detailWarehouseData") or []:
        qty = _num(w.get("balance"))
        if qty > 0:
            name = w.get("warehouseName") or w.get("name") or ""
            if name:
                out[name] = qty
    return out


def gudang_breakdown(part_number: str) -> dict[str, float]:
    """Rincian stok {warehouseName: qty} 1 PN dari INDEKS (hasil enrichment 5-jam).
    {} bila belum ter-enrich / PN tak ada. Non-blocking, tanpa panggilan live."""
    key = _norm_pn(part_number)
    if not key:
        return {}
    return dict((_index_cache.get("by_gudang") or {}).get(key, {}))


def gudang_enriched_count() -> int:
    """Jumlah PN yang sudah punya rincian per-gudang di indeks (0 = belum siap)."""
    return len(_index_cache.get("by_gudang") or {})


def enrich_warehouses() -> int:
    """Isi indeks per-gudang: tarik rincian gudang utk SEMUA barang berstok>0 (serial,
    santun ke Accurate) → simpan ke _index_cache['by_gudang'] = {norm_pn:{gudang:qty}}.
    Terbitkan parsial berkala agar query dapat data lebih awal. Return jumlah PN
    ter-enrich. Dipanggil dari thread latar terjadwal; skip bila sedang berjalan."""
    global _gudang_enrich_running
    with _gudang_enrich_lock:
        if _gudang_enrich_running:
            return gudang_enriched_count()
        _gudang_enrich_running = True
    try:
        if not available():
            return gudang_enriched_count()
        idx = refresh(force=False)
        in_stock = [(k, v) for k, v in (idx.get("by_pn") or {}).items()
                    if _num(v.get("available_to_sell")) > 0 and v.get("accurate_id") is not None]
        built: dict[str, dict[str, float]] = {}
        for i, (key, v) in enumerate(in_stock, 1):
            built[key] = _warehouse_map(v.get("accurate_id"))
            if i % _GUDANG_ENRICH_PUBLISH_EVERY == 0:
                _index_cache["by_gudang"] = dict(built)   # publish parsial
        _index_cache["by_gudang"] = built
        _index_cache["gudang_ts"] = time.time()
        return len(built)
    finally:
        with _gudang_enrich_lock:
            _gudang_enrich_running = False


def stock_for(part_number: str, force: bool = False) -> dict[str, Any] | None:
    """Stok Accurate untuk 1 PN (atau None bila tak ada di Accurate)."""
    key = _norm_pn(part_number)
    if not key:
        return None
    idx = refresh(force=force)
    return idx["by_pn"].get(key)


def all_items(force: bool = False) -> list[dict[str, Any]]:
    """Seluruh barang Accurate (ternormalisasi) dari indeks ber-cache TTL.

    Dipakai menu Stok untuk menampilkan katalog stok penuh. Auto-login saat sesi
    habis (via ``refresh`` → ``fetch_all_items``)."""
    return list(refresh(force=force)["items"])


def available() -> bool:
    """True bila Accurate bisa dipakai: ada kredensial auto-login ATAU file sesi
    manual (tanpa menembak jaringan)."""
    if credentials_configured():
        return True
    try:
        load_session()
        return True
    except AccurateSessionMissing:
        return False


# ── Refresh indeks TERJADWAL (latar) ─────────────────────────────────────────
# Tanpa ini, tarikan penuh hanya terjadi saat ada user membuka menu Stok setelah
# cache kedaluwarsa → user pertama tiap periode menunggu ±45 dtk. Thread ini
# menarik indeks tiap _INDEX_TTL di belakang layar sehingga cache SELALU hangat
# dan tak ada user yang kena tunggu. Beban Accurate tetap 1 tarikan/periode.
_SCHED_RETRY = 15 * 60  # gagal (sesi/jaringan/throttle) → coba lagi 15 menit
_sched_lock = threading.Lock()
_sched_started = False


def _scheduled_refresh_once() -> float:
    """Satu iterasi refresh terjadwal; return detik tunggu sampai iterasi berikut."""
    try:
        if available():
            refresh(force=True)
            logger.info(
                "[accurate] refresh terjadwal indeks stok OK (%d barang); "
                "mulai enrichment per-gudang…", len(_index_cache["items"]),
            )
            # Rincian per-gudang ditarik SEKALI di sini (serial, ~8 mnt utk ~3.700
            # barang berstok) → dibagi ke semua fitur via indeks. Latar, tak ada user
            # menunggu. NON-FATAL: kegagalan enrichment TIDAK mengorbankan refresh
            # agregat yang sudah sukses — tetap tunggu siklus penuh (data lama dipakai).
            try:
                n = enrich_warehouses()
                logger.info(
                    "[accurate] enrichment per-gudang OK (%d PN); berikutnya %d mnt lagi",
                    n, _INDEX_TTL // 60,
                )
            except Exception as e:
                logger.warning("[accurate] enrichment per-gudang gagal (%s) — data lama dipakai", e)
        # Accurate tak dikonfigurasi → tak ada yang bisa ditarik; cek lagi
        # nanti (murah, tanpa jaringan) kalau-kalau file sesi manual muncul.
        return _INDEX_TTL
    except Exception as e:
        logger.warning(
            "[accurate] refresh terjadwal gagal (%s) — coba lagi %d mnt",
            e, _SCHED_RETRY // 60,
        )
        return _SCHED_RETRY


def _scheduled_refresh_loop() -> None:
    while True:
        time.sleep(_scheduled_refresh_once())


def start_scheduled_refresh() -> bool:
    """Mulai thread daemon refresh indeks berkala. Idempoten (aman dipanggil
    berulang); return True hanya saat thread baru benar-benar dimulai.
    Tarikan pertama berjalan segera → cache hangat sejak server nyala."""
    global _sched_started
    with _sched_lock:
        if _sched_started:
            return False
        _sched_started = True
    threading.Thread(
        target=_scheduled_refresh_loop, daemon=True, name="accurate-index-refresh"
    ).start()
    return True


# ── Snapshot utk overlay hasil PENCARIAN — VIEW dari indeks bersama ─────────
# Dulu snapshot menarik katalog penuh SENDIRI tiap 30 mnt (duplikat tarikan
# massal). Sejak 2026-07-06 (permintaan pemilik: "ambil stok 5 jam sekali,
# dibagi ke semua fitur") sumbernya SATU: indeks terjadwal `_INDEX_TTL`
# (start_scheduled_refresh). View "snap" dibangun saat refresh() — di sini
# tinggal baca, non-blocking, request pencarian tak pernah menunggu tarikan.


def snapshot() -> dict[str, dict[str, Any]]:
    """{norm_pn: {stok,harga,unit}} dari indeks 5-jam bersama (tanpa tarikan
    terpisah). Bisa kosong (cold start / Accurate down) — pemanggil fallback Excel."""
    return _index_cache.get("snap") or {}


def items_matching(terms: Iterable[str], *, limit: int = 400) -> list[dict[str, Any]]:
    """Barang di indeks yang NAMA/PN-nya memuat SALAH SATU `terms` (word-boundary,
    case-insensitive). Beda dari search_index (yg skor+batas kecil utk aftermarket):
    ini mengumpulkan SEMUA yang cocok (utk daftar kategori spt 'kopling'/'rem') —
    in-memory, cepat (satu pindai). Return normalize_item (pn, name, price…)."""
    items = _index_cache.get("items") or []
    if not items:
        return []
    pats = [re.compile(r"(?<!\w)" + re.escape(t.strip().lower()) + r"(?!\w)")
            for t in terms if t and len(t.strip()) >= 3]
    if not pats:
        return []
    out: list[dict[str, Any]] = []
    for it in items:
        hay = f"{it.get('name') or ''} {it.get('pn') or ''}".lower()
        if any(p.search(hay) for p in pats):
            out.append(it)
            if len(out) >= limit:
                break
    return out


def search_index(terms: Iterable[str], *, limit: int = 8) -> list[dict[str, Any]]:
    """Cari INDEKS stok bersama per NAMA/PN — substring, case-insensitive, murah
    (in-memory, NON-BLOCKING: baca cache seperti snapshot(), TIDAK memicu tarikan).

    Indeks stok memuat barang yang TIDAK ada di katalog Sinotruk (aftermarket/
    lokal, sering bernama Indonesia: 'Kaca Spion LH', 'Alternator Regulator',
    'Cucuk Per Depan Faw') — satu-satunya jalan menemukannya per kata kunci.
    `terms` = query asli + ekspansi sinonim. Skor: frasa utuh di nama/PN lebih
    tinggi dari kecocokan kata tunggal; hasil diurut skor, lalu stok, lalu nama.
    [] bila indeks kosong (cold start) — pemanggil tinggal melewatkan."""
    items = _index_cache.get("items") or []
    if not items:
        return []
    # Frasa pendek/generik ('per', 'oli') terlalu banjir → minimal 4 huruf, dan
    # cocok per BATAS KATA (bukan substring di tengah kata: 'per' ≠ 'Super').
    frasa = [t.strip().lower() for t in terms if t and len(t.strip()) >= 4]
    if not frasa:
        return []
    kata = {w for f in frasa for w in re.split(r"\s+", f) if len(w) >= 3}

    def _cocok(needle: str, hay: str) -> bool:
        return re.search(r"(?<!\w)" + re.escape(needle) + r"(?!\w)", hay) is not None

    scored: list[tuple[float, dict[str, Any]]] = []
    for it in items:
        hay = f"{it.get('name') or ''} {it.get('pn') or ''} {it.get('no') or ''}".lower()
        score = 0.0
        for f in frasa:
            hit = (f in hay) if " " in f else _cocok(f, hay)
            if hit:
                score += 10.0 + len(f) / 10.0   # frasa lebih panjang = lebih spesifik
        if not score:
            score = sum(2.0 for w in kata if _cocok(w, hay))
            if score < 4.0:                     # < 2 kata cocok = terlalu lemah (noise)
                score = 0.0
        if score:
            scored.append((score, it))
    scored.sort(key=lambda s: (-s[0], -(s[1].get("available_to_sell") or 0.0),
                               s[1].get("name") or ""))
    return [it for _s, it in scored[:limit]]


# ═══════════════════════════════════════════════════════════════════════════
#  PENAWARAN PENJUALAN (Sales Quotation / SQ) — buat dokumen + tarik PDF resmi
#
#  Dibedah dari HAR UI Accurate (2026-07-10). Kunci yang tak terlihat dari luar:
#    • SEMUA field header/detail berprefix `param.` (form-urlencoded, bukan JSON)
#    • WAJIB token `_usi` (user-session-id) DI SAMPING `_dsi`; `_usi` dipanen dari
#      respons init-sales-quotation.do (tertanam sbg `_usi=…` di dalam teks)
#    • Total & pajak DIHITUNG Accurate via calculate-header (config-agnostic) —
#      kita TIDAK menebak rumus PPN
#  PDF resmi ditarik 2 langkah dari host `*-report.accurate.id`.
#  Lihat memory: accurate-penawaran-api.
# ═══════════════════════════════════════════════════════════════════════════
_USI_RE = re.compile(r"_usi=([A-Za-z0-9+/=]{40,})")
_qdefaults_cache: dict[str, Any] = {"at": 0.0, "val": None}
_QDEFAULTS_TTL = 6 * 3600


def _report_base() -> str:
    """Host mesin cetak: iris.accurate.id → iris-report.accurate.id."""
    return f"https://{get_settings().accurate_host}".replace(".accurate.id", "-report.accurate.id")


def _harvest_usi(session: dict[str, str]) -> str:
    """Ambil token `_usi` dari respons init-sales-quotation.do (satu-satunya sumber)."""
    dsi = session["dsi"]
    headers = {
        "User-Agent": _UA, "Accept": "application/json, text/javascript, */*; q=0.01",
        "X-Requested-With": "XMLHttpRequest", "Origin": _base(),
        "Referer": f"{_base()}/accurate/?_dsi={dsi}",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "Cookie": f"JSESSIONID={session['jsessionid']}",
    }
    r = requests.post(f"{_base()}/accurate/customer/init-sales-quotation.do",
                      data={"_dsi": dsi}, headers=headers, timeout=_HTTP_TIMEOUT, allow_redirects=False)
    if _looks_expired(r):
        raise AccurateSessionExpired("Sesi Accurate kadaluarsa saat init penawaran.")
    m = _USI_RE.search(r.text)
    if not m:
        raise AccurateError("Gagal memperoleh token _usi dari init penawaran.")
    return m.group(1)


def _company_defaults(session: dict[str, str]) -> dict[str, Any]:
    """Default header perusahaan (branch/currency/tax1/warehouse) dari dokumen
    terbaru — dibaca sekali & di-cache. Menghindari hardcode id yang bisa beda
    antar-perusahaan. countAutoNumber dari view dokumen."""
    now = time.time()
    if _qdefaults_cache["val"] and (now - _qdefaults_cache["at"]) < _QDEFAULTS_TTL:
        return _qdefaults_cache["val"]
    rows = _call(session, "customer/search-sales-quotation.do", {"start": 0, "limit": 1}).get("d") or []
    if not rows:
        raise AccurateError("Tak ada dokumen penawaran acuan untuk membaca default perusahaan.")
    v = _call(session, "customer/view-sales-quotation.do", {"id": rows[0]["id"]}).get("d") or {}
    det0 = (v.get("detailItem") or [{}])[0]
    val = {
        "branchId": v.get("branchId") or 50,
        "currencyId": v.get("currencyId") or 50,
        "tax1Id": v.get("tax1Id") or 50,
        "warehouseId": det0.get("warehouseId") or v.get("branchId") or 50,
        "countAutoNumber": v.get("countAutoNumber") or 3,
    }
    _qdefaults_cache.update(at=now, val=val)
    return val


def _sq_headers(dsi: str, jsessionid: str) -> dict[str, str]:
    return {
        "User-Agent": _UA, "Accept": "application/json, text/javascript, */*; q=0.01",
        "X-Requested-With": "XMLHttpRequest", "Origin": _base(),
        "Referer": f"{_base()}/accurate/?_dsi={dsi}",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "Cookie": f"JSESSIONID={jsessionid}",
    }


def search_customers(keyword: str, *, limit: int = 15) -> list[dict[str, Any]]:
    """Cari pelanggan (untuk field 'Dipesan oleh'). Auto-login saat sesi habis."""
    kw = (keyword or "").strip()
    if not kw:
        return []

    def _do(sess):
        rows = _call(sess, "customer/search-customer.do",
                     {"start": 0, "limit": limit, "keywords": kw}).get("d") or []
        return [{"id": r.get("id"), "no": r.get("customerNo"), "name": (r.get("name") or "").strip()}
                for r in rows]

    sess = _ensure_session()
    try:
        return _do(sess)
    except AccurateSessionExpired:
        if not credentials_configured():
            raise
        return _do(_refresh_session())


def item_for_quotation(part_number: str) -> dict[str, Any] | None:
    """Cari 1 barang untuk baris penawaran: {id, no, pn, name, unit_id, unit, price}.
    PN harus PERSIS cocok (hindari salah barang). None bila tak ada."""
    want = _norm_pn(part_number)
    if not want:
        return None
    hits = search_by_keyword(part_number, limit=50)  # normalize_item; punya accurate_id
    exact = [h for h in hits if _norm_pn(h["pn"]) == want]
    if not exact:
        return None
    best = max(exact, key=lambda h: h["available_to_sell"])
    # unit1.id tak disimpan normalize_item → ambil dari hasil mentah search-item sekali.
    unit_id = 100
    sess = _ensure_session()
    try:
        rd = _search_items_raw(sess, keywords=part_number, start=0, limit=50).get("d") or []
    except AccurateSessionExpired:
        rd = _search_items_raw(_refresh_session(), keywords=part_number, start=0, limit=50).get("d") or []
    for it in rd:
        if it.get("id") == best["accurate_id"]:
            unit_id = ((it.get("unit1") or {}) or {}).get("id") or 100
            break
    return {
        "id": best["accurate_id"], "no": best["no"], "pn": best["pn"],
        "name": best["name"], "unit_id": unit_id, "unit": best["unit"],
        "price": best["price"], "available": best["available_to_sell"],
    }


def _calc_totals(session, usi, defaults, customer_id, lines, taxable, inclusive):
    """Minta Accurate menghitung subTotal/pajak/total (config-agnostic)."""
    body = {
        "_dsi": session["dsi"], "_usi": usi,
        "param.customerId": customer_id, "param.currencyId": defaults["currencyId"],
        "param.rate": 1, "param.branchId": defaults["branchId"],
        "param.tax1Id": defaults["tax1Id"],
        "param.taxable": "true" if taxable else "false",
        "param.inclusiveTax": "true" if inclusive else "false",
        "param.forceCalculatePercentTaxable": "true",
    }
    for i, ln in enumerate(lines):
        p = f"param.detailItem[{i}]."
        body[p + "seq"] = i + 1
        body[p + "itemId"] = ln["item_id"]
        body[p + "quantity"] = ln["qty"]
        body[p + "unitPrice"] = ln["unit_price"]
        body[p + "itemUnitId"] = ln.get("unit_id", 100)
        body[p + "useTax1"] = "true" if taxable else "false"
    r = requests.post(f"{_base()}/accurate/customer/calculate-header-sales-quotation.do",
                      data=body, headers=_sq_headers(session["dsi"], session["jsessionid"]),
                      timeout=_HTTP_TIMEOUT, allow_redirects=False)
    if _looks_expired(r):
        raise AccurateSessionExpired("Sesi kadaluarsa saat hitung total.")
    j = r.json()
    if not j.get("s"):
        raise AccurateError(f"Hitung total ditolak: {str(j.get('d'))[:150]}")
    return j.get("d") or {}


def _save_quotation(session, usi, defaults, *, number, customer_id, lines,
                    transdate, taxable, inclusive, description, totals,
                    ignore_warning=False):
    """POST save-sales-quotation.do. Return dict hasil ('r').
    Accurate bisa membalas PERINGATAN (d.w_) — mis. anjuran setel DPP 11/12 —
    yang di UI ditembus dgn tombol 'lanjutkan'. Kita kirim ulang sekali dgn
    ignoreWarning=true (setara klik 'lanjutkan'), bukan menganggapnya gagal."""
    body = {
        "_dsi": session["dsi"], "_usi": usi,
        "param.uniqueDataNumber": int(time.time() * 1000),
        "param.needDetailResult": "false",
        "param.ignoreWarning": "true" if ignore_warning else "false",
        "param.number": number,                       # MANUAL — bukan penomoran otomatis
        "param.countAutoNumber": defaults["countAutoNumber"],
        "param.customerId": customer_id,
        "param.currencyId": defaults["currencyId"], "param.rate": 1,
        "param.transDate": transdate,
        "param.branchId": defaults["branchId"],
        "param.tax1Id": defaults["tax1Id"],
        "param.taxable": "true" if taxable else "false",
        "param.inclusiveTax": "true" if inclusive else "false",
        "param.forceCalculatePercentTaxable": "true",
        "param.saveAsStatusType": "UNAPPROVED",
        "param.transactionCurrencyId": defaults["currencyId"],
        "param.attachments": "[]",
        "param.description": description or "",
        "param.cashDiscount": 0, "param.lastCashDiscount": 0, "param.totalExpense": 0,
        # total & pajak DIHITUNG Accurate (bukan tebakan kita)
        "param.subTotal": totals.get("subTotal", 0),
        "param.totalAmount": totals.get("subTotal", 0) if inclusive else totals.get("totalAmount", 0),
        "param.tax1Amount": totals.get("tax1Amount", 0),
        "param.tax2Amount": 0, "param.tax3Amount": 0, "param.tax4Amount": 0,
        "param.tax1Rate": totals.get("tax1Rate", 0),
        "param.percentTaxable": totals.get("percentTaxable", 100),
    }
    for i, ln in enumerate(lines):
        p = f"param.detailItem[{i}]."
        qty, price = ln["qty"], ln["unit_price"]
        body[p + "_status"] = "insert"
        body[p + "seq"] = i + 1
        body[p + "itemId"] = ln["item_id"]
        body[p + "detailName"] = ln.get("name", "")
        body[p + "quantity"] = qty
        body[p + "itemUnitId"] = ln.get("unit_id", 100)
        body[p + "unitRatio"] = 1
        body[p + "unitPrice"] = price
        body[p + "totalPrice"] = round(qty * price, 2)
        body[p + "warehouseId"] = defaults["warehouseId"]
        body[p + "useTax1"] = "true" if taxable else "false"
        body[p + "useTax2"] = "false"
        body[p + "useTax3"] = "false"
        body[p + "useTax4"] = "false"
    r = requests.post(f"{_base()}/accurate/customer/save-sales-quotation.do",
                      data=body, headers=_sq_headers(session["dsi"], session["jsessionid"]),
                      timeout=_HTTP_TIMEOUT + 15, allow_redirects=False)
    if _looks_expired(r):
        raise AccurateSessionExpired("Sesi kadaluarsa saat simpan penawaran.")
    j = r.json()
    if not j.get("s"):
        d = j.get("d") or {}
        # PERINGATAN saja (bukan error validasi) & belum coba abaikan → ulangi
        # sekali dgn ignoreWarning=true (setara tombol 'lanjutkan' di UI).
        if not ignore_warning and isinstance(d, dict) and set(d.keys()) <= {"w_"}:
            return _save_quotation(session, usi, defaults, number=number, customer_id=customer_id,
                                   lines=lines, transdate=transdate, taxable=taxable,
                                   inclusive=inclusive, description=description, totals=totals,
                                   ignore_warning=True)
        raise AccurateError(f"Accurate menolak simpan penawaran: {str(d)[:200]}")
    return j.get("r") or {}


_MASPART_NUM_RE = re.compile(r"MASPART-0*(\d+)\s*$", re.I)


def next_quotation_number() -> str:
    """Nomor penawaran MASPART BERIKUTNYA: 'MASPART-NN' (NN = nomor MASPART
    tertinggi yang sudah ada + 1, mulai 01). Dihitung dari dokumen Accurate →
    tahan restart & tak pernah bentrok.

    ⛔⛔ PENOMORAN OTOMATIS ACCURATE TIDAK PERNAH DIPAKAI. MASPART selalu memasok
    nomornya sendiri (dikirim sbg param.number manual di _save_quotation). Aturan
    keras dari pemilik: toggle 'Penomoran Otomatis' harus SELALU MATI."""
    def _do(sess):
        rows = _call(sess, "customer/search-sales-quotation.do",
                     {"start": 0, "limit": 200, "keywords": "MASPART"}).get("d") or []
        mx = 0
        for r in rows:
            m = _MASPART_NUM_RE.match(str(r.get("number") or "").strip())
            if m:
                mx = max(mx, int(m.group(1)))
        return f"MASPART-{mx + 1:02d}"

    sess = _ensure_session()
    try:
        return _do(sess)
    except AccurateSessionExpired:
        if not credentials_configured():
            raise
        return _do(_refresh_session())


def create_sales_quotation(*, number: str, customer_id: int, lines: list[dict],
                           transdate: str, taxable: bool = True, inclusive: bool = True,
                           description: str = "") -> dict[str, Any]:
    """Buat Penawaran Penjualan. `lines` = [{item_id, name, qty, unit_price, unit_id}].
    `number` WAJIB (manual). Return {id, number, total}. Auto-login saat sesi habis."""
    if not number.strip():
        raise AccurateError("Nomor penawaran wajib diisi (manual).")
    if not lines:
        raise AccurateError("Minimal satu baris barang.")

    def _flow(sess):
        usi = _harvest_usi(sess)
        defs = _company_defaults(sess)
        totals = _calc_totals(sess, usi, defs, customer_id, lines, taxable, inclusive)
        return _save_quotation(sess, usi, defs, number=number, customer_id=customer_id,
                               lines=lines, transdate=transdate, taxable=taxable,
                               inclusive=inclusive, description=description, totals=totals)

    sess = _ensure_session()
    try:
        r = _flow(sess)
    except AccurateSessionExpired:
        if not credentials_configured():
            raise
        r = _flow(_refresh_session())
    return {"id": r.get("id"), "number": r.get("number") or number,
            "total": _num(r.get("totalAmount"))}


def sales_quotation_pdf(quotation_id: int, *, layout_id: int = 50) -> bytes:
    """Tarik PDF RESMI Accurate untuk 1 penawaran (2 langkah, host *-report).
    layout_id: 50=Default, 551=after disc, 700=Service-JNT, 450=Proforma."""

    def _flow(sess):
        usi = _harvest_usi(sess)
        rep, dsi = _report_base(), sess["dsi"]
        hdr = _sq_headers(dsi, sess["jsessionid"])
        r1 = requests.post(f"{rep}/accurate/company/view-print-layout-execute.do",
                           data={"dataId": quotation_id, "printLayoutId": layout_id,
                                 "transactionType": "SQ", "_usi": usi, "_dsi": dsi},
                           headers=hdr, timeout=_HTTP_TIMEOUT, allow_redirects=False)
        if _looks_expired(r1):
            raise AccurateSessionExpired("Sesi kadaluarsa saat siapkan cetak.")
        cache_id = ((r1.json().get("d") or {}) or {}).get("cacheId")
        if not cache_id:
            raise AccurateError(f"Gagal menyiapkan cetak: {r1.text[:150]}")
        r2 = requests.get(f"{rep}/accurate/report/export-report.do",
                          params={"_dsi": dsi, "_usi": usi, "cacheId": cache_id, "exportType": "pdf"},
                          headers=hdr, timeout=_HTTP_TIMEOUT + 30, allow_redirects=False)
        if r2.content[:4] != b"%PDF":
            raise AccurateError("Respons cetak bukan PDF (sesi/izin?).")
        return r2.content

    sess = _ensure_session()
    try:
        return _flow(sess)
    except AccurateSessionExpired:
        if not credentials_configured():
            raise
        return _flow(_refresh_session())


# ⛔⛔ SENGAJA TIDAK ADA fungsi hapus/ubah penawaran (delete/update/void). Aturan
# keras pemilik: aplikasi HANYA boleh MEMBUAT penawaran (kuantitas part) — tak
# boleh mengubah atau menghapus apa pun di Accurate. Jangan menambah endpoint
# tulis Accurate lain tanpa persetujuan pemilik.


# ── CLI selftest (tanpa server) ────────────────────────────────────────────
if __name__ == "__main__":  # pragma: no cover
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "--all":
        items = fetch_all_items()
        with_stock = [i for i in items if _num(i.get("availableToSell")) > 0]
        print(f"Total barang: {len(items)} | berstok>0: {len(with_stock)}")
    else:
        pn = sys.argv[1] if len(sys.argv) > 1 else "WG9725220536"
        hit = stock_for_live(pn)
        print(f"{pn} -> {hit}")
