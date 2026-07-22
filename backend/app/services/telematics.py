"""
Telematics / GPS armada — Sinotruk Fleet Service (www.sg.sinotruksfs.com).

Portal pelacakan GPS/IoT armada, TERPISAH dari SIMS (katalog/klaim, port 8082)
& EPC — server, auth, dan kredensial berbeda. Kredensial di env
(TELEMATICS_USERNAME/PASSWORD, UI Coolify). Detail HAR: memory
`sinotruk-telematics-har`.

Auth (terverifikasi live 2026-07-22):
  POST /api/auth/getPublicKey → {id, publicKey}
  → RSA PKCS1_v1.5 enkripsi password (pola sims_fetcher._rsa_encrypt)
  → POST /api/auth/login (form) username & password & encryptId=<id>
  → data.tokenKey (hex 32). Header semua request: Authorization: <tokenKey>
    (⛔ TANPA "Bearer"). Token expire 1 jam → cache + re-login saat 401/code≠200.

⚠️ Sebagian unit ber-`kdVin` DUMMY 'SLGV0123456789888' (placeholder pabrik) —
JANGAN dipakai kunci lookup EPC; pakai `cjh` (frame) / `vin` asli.
"""
from __future__ import annotations

import base64
import logging
import threading
import time

import requests

from ..core.config import get_settings

logger = logging.getLogger("maspart.telematics")

_BASE = "http://www.sg.sinotruksfs.com"
_TIMEOUT = 25
_DUMMY_VIN = "SLGV0123456789888"

# statusCode unit → label Indonesia (dari portal: running/stop/offline/online).
STATUS_LABEL = {
    "running": "Jalan", "stop": "Berhenti",
    "offline": "Offline", "online": "Online (idle)",
}

_lock = threading.Lock()
_token: str | None = None
_token_exp = 0.0
_SESSION_TTL = 55 * 60      # token portal hidup 1 jam → segarkan sebelum itu
# Kode respons yang berarti "token basi, login ulang". Portal ini SINGLE-SESSION:
# login baru membatalkan token lama → sesi lain (probe/paralel) memicu 350.
# ⛔ Bukan hanya 401 (SIMS) — telematics pakai 350 "Login session invalid".
_KODE_RELOGIN = {350, 401, 403}


def available() -> bool:
    return bool(get_settings().telematics_configured)


# ── auth ─────────────────────────────────────────────────────────────
def _rsa_encrypt(public_key_b64: str, plaintext: str) -> str:
    from Crypto.PublicKey import RSA
    from Crypto.Cipher import PKCS1_v1_5
    key = RSA.import_key(base64.b64decode(public_key_b64))
    enc = PKCS1_v1_5.new(key).encrypt(plaintext.encode("utf-8"))
    return base64.b64encode(enc).decode("utf-8")


def _login() -> str:
    """getPublicKey(id) → RSA enkripsi password → login(encryptId=id) → tokenKey.
    Tanpa session cookie: keterkaitan key↔login lewat `encryptId`, bukan cookie
    (terverifikasi live 2026-07-22)."""
    s = get_settings()
    pk = requests.post(f"{_BASE}/api/auth/getPublicKey", timeout=_TIMEOUT).json()
    data = pk.get("data") or {}
    kid, key = data.get("id"), data.get("publicKey")
    if not key:
        raise RuntimeError("gagal ambil RSA public key telematics")
    enc = _rsa_encrypt(key, s.telematics_password)
    r = requests.post(f"{_BASE}/api/auth/login",
                      data={"username": s.telematics_username,
                            "password": enc, "encryptId": kid},
                      timeout=_TIMEOUT).json()
    tok = (r.get("data") or {}).get("tokenKey")
    if r.get("code") != 200 or not tok:
        raise RuntimeError(f"login telematics gagal: {r.get('message')}")
    return tok


def _get_token(force: bool = False) -> str:
    global _token, _token_exp
    with _lock:
        if force or _token is None or time.time() >= _token_exp:
            _token = _login()
            _token_exp = time.time() + _SESSION_TTL
        return _token


def _post(path: str, data: dict) -> dict | list | None:
    """POST form-urlencoded + retry sekali saat token basi (code≠200/HTTP≥400)."""
    def _once() -> requests.Response:
        return requests.post(f"{_BASE}{path}", data=data,
                             headers={"Authorization": _get_token()},
                             timeout=_TIMEOUT)
    def _basi(resp, body) -> bool:
        return (resp.status_code in (401, 403)
                or (isinstance(body, dict) and body.get("code") in _KODE_RELOGIN))
    try:
        r = _once()
        body = r.json() if r.status_code < 400 else {}
        if _basi(r, body):
            _get_token(force=True)          # token kedaluwarsa/invalid → login ulang
            r = _once()
            body = r.json() if r.status_code < 400 else {}
        if r.status_code >= 400:
            logger.info("telematics %s -> HTTP %s", path, r.status_code)
            return None
        if isinstance(body, dict) and body.get("code") not in (200, None):
            logger.info("telematics %s -> code %s (%s)", path, body.get("code"),
                        body.get("message"))
            return None
        return body.get("data") if isinstance(body, dict) else body
    except requests.RequestException as e:
        logger.info("telematics %s gagal: %s", path, e)
        return None


# ── API publik ───────────────────────────────────────────────────────
def dashboard(organization_id, tanggal: str) -> dict | None:
    d = _post("/api/fleetOperationReport/queryDataPanel",
              {"organizationId": organization_id, "time": tanggal, "type": "DAY"})
    if not isinstance(d, dict):
        return None
    def _v(x):
        return (x or {}).get("value") if isinstance(x, dict) else x
    return {
        "total_unit": d.get("vehicleNum"),
        "unit_beroperasi_hari_ini": d.get("attendanceNum"),
        "online_persen": _v(d.get("onlineRate")),
        "jarak_total_km": _v(d.get("mileage")),
        "bbm_total": _v(d.get("fuel")),
        "jam_kerja_total": _v(d.get("workTime")),
    }


def _semua_records(page_size: int = 50, maks_halaman: int = 20) -> list[dict]:
    """Agregasi SELURUH halaman queryCar (257 unit / 50 per halaman)."""
    out: list[dict] = []
    cur = 1
    while cur <= maks_halaman:
        d = _post("/api/terminal-static-information/queryCar",
                  {"pagination": "true", "containsChild": "true",
                   "current": cur, "size": page_size})
        if not isinstance(d, dict):
            break
        recs = d.get("records") or []
        out.extend(recs)
        total = d.get("total") or 0
        if len(out) >= total or not recs:
            break
        cur += 1
    return out


def _fleet_names(rec: dict) -> list[str]:
    return [o.get("organizationName") for o in (rec.get("organizations") or [])
            if o.get("organizationName")]


def _nama_unit(rec: dict) -> str:
    """Label unit: carNumber bila diberi admin, jika tidak cjh (frame)."""
    return (rec.get("carNumber") or "").strip() or rec.get("cjh") or "?"


def semua_unit(fleet: str = "") -> dict | None:
    """Daftar unit + organisasi/fleet. `fleet` (opsional) menyaring per nama org."""
    recs = _semua_records()
    if not recs:
        return None
    if fleet:
        f = fleet.strip().lower()
        recs = [r for r in recs
                if any(f in (n or "").lower() for n in _fleet_names(r))]
    return {"total": len(recs), "records": recs}


def lokasi_semua(organization_id=None) -> dict[str, dict]:
    """Map cjh → status GPS terbaru (posisi, status, km, bbm%, rusak)."""
    org = organization_id if organization_id is not None else (cari_org_id() or "")
    d = _post("/api/running-data/queryAllLocationStatus",
              {"organizationId": org, "isReverseGeo": "false"})
    if not isinstance(d, list):
        return {}
    out: dict[str, dict] = {}
    for c in d:
        cjh = c.get("cjh")
        if cjh:
            out[cjh] = c
    return out


def fleet_breakdown(recs: list[dict]) -> list[dict]:
    """Jumlah unit per fleet/organisasi (satu unit bisa di >1 org)."""
    hit: dict[str, int] = {}
    for r in recs:
        for n in _fleet_names(r):
            hit[n] = hit.get(n, 0) + 1
    return [{"fleet": k, "jumlah": v}
            for k, v in sorted(hit.items(), key=lambda kv: -kv[1])]


def cari_unit(cjh_atau_nama: str) -> dict | None:
    """Cari SATU unit dari cjh/vin/carNumber (untuk ganti nama)."""
    q = (cjh_atau_nama or "").strip().lower()
    if not q:
        return None
    for r in _semua_records():
        if q in (r.get("cjh") or "").lower() or q in (r.get("vin") or "").lower() \
                or q == (r.get("carNumber") or "").lower():
            return r
    return None


def ganti_nama(cjh: str, nama: str) -> dict | None:
    """⚠️ WRITE: ubah carNumber (nama/label) unit di server Sinotruk."""
    d = _post("/api/vehicleManage/updateCarNumber",
              {"cjh": cjh, "carNumber": nama})
    return d if isinstance(d, dict) else None


def rangkum_unit(rec: dict, loc: dict | None = None) -> dict:
    """Satu unit → bentuk sajian asisten (statis + status GPS bila ada)."""
    out = {
        "frame": rec.get("cjh"),
        "vin": None if rec.get("vin") == _DUMMY_VIN else rec.get("vin"),
        "nama": (rec.get("carNumber") or "").strip() or None,
        "fleet": _fleet_names(rec),
        "model": rec.get("model"),
        "brand": rec.get("brandEdition"),
        "engine": rec.get("engine"),
        "gearbox": rec.get("gearboxType"),
        "penggerak": rec.get("driveForm"),
        "ban": rec.get("tireType"),
        "km": rec.get("mileage"),
    }
    if loc:
        out.update({
            "status_gps": STATUS_LABEL.get(loc.get("status"), loc.get("status")),
            "km_gps": loc.get("totalMileage"),
            "bbm_persen": loc.get("fuelLevel"),
            "rusak": loc.get("isFaulty"),
            "posisi": ({"lat": loc.get("lat"), "lng": loc.get("lng"),
                        "waktu": loc.get("revdatetime")}
                       if loc.get("lat") not in (None, 0) else None),
        })
    return out


def cari_org_id() -> int | None:
    """organizationId akar (untuk dashboard) — dari organisasi unit pertama."""
    for r in _semua_records(page_size=1, maks_halaman=1):
        for o in (r.get("organizations") or []):
            if o.get("id"):
                return o["id"]
    return None
