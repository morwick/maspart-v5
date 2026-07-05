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
import re
import threading
import time
from pathlib import Path
from typing import Any, Iterable

import requests

from ..core.config import get_settings

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

# TTL cache indeks stok penuh (detik). Lookup stok berulang tak menembak Accurate
# tiap kali; ubah via refresh(force=True).
_INDEX_TTL = 5 * 60


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
_LOGIN_COOLDOWN_SEC = 300
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
    """Stok Accurate 1 PN LENGKAP: agregat + rincian **per gudang** terkini.

    Dua panggilan (search-item + view-itemstock-bywarehouse), di-cache per-PN
    ~90 dtk. ``per_gudang`` = daftar {gudang, deskripsi, qty, gudang_id} untuk gudang
    berstok > 0 (urut qty menurun). Gagal ambil per-gudang non-fatal (agregat tetap).
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

    data = _search_retry(keywords=part_number, start=0, limit=50)
    raws = [it for it in (data.get("d") or [])
            if _norm_pn(parse_pn(str(it.get("no") or ""))) == want]
    if not raws:
        return _cache(None)
    it = max(raws, key=lambda x: _num(x.get("availableToSell")))
    base = normalize_item(it)
    per: list[dict[str, Any]] = []
    try:
        wd = _warehouse_retry(it.get("id")).get("d") or {}
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
_index_cache: dict[str, Any] = {"ts": 0.0, "items": [], "by_pn": {}}


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
        _index_cache["ts"] = time.time()
        return _index_cache


def stock_for(part_number: str, force: bool = False) -> dict[str, Any] | None:
    """Stok Accurate untuk 1 PN (atau None bila tak ada di Accurate)."""
    key = _norm_pn(part_number)
    if not key:
        return None
    idx = refresh(force=force)
    return idx["by_pn"].get(key)


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
