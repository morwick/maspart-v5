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
from datetime import datetime as _dt
from datetime import timedelta as _td
from datetime import timezone as _tz

import requests

from ..core.config import get_settings

logger = logging.getLogger("maspart.telematics")

_BASE = "http://www.sg.sinotruksfs.com"
_TIMEOUT = 25
_DUMMY_VIN = "SLGV0123456789888"

# Portal MENGIRIM header `time-zone` di setiap request (HAR) — server memakainya
# untuk memformat canTime/gpsTime/revdatetime. Tanpa header ini stempel waktu
# ikut zona default server, jadi "terakhir online" bisa meleset berjam-jam.
_TZ_NAMA = "Asia/Jakarta"
WIB = _tz(_td(hours=7))

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


def frame_dari_rangka(rangka: str) -> str:
    """VIN 17 karakter → frame 8 karakter terakhir ('LZZ1ELSF7SJ392741' →
    'SJ392741'); frame/kode pendek → apa adanya (upper). Pola sama dgn
    sims_warranty.frame_dari_rangka."""
    r = (rangka or "").strip().upper().replace(" ", "")
    return r[-8:] if len(r) >= 17 else r


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


def _post(path: str, data: dict, penuh: bool = False) -> dict | list | None:
    """POST form-urlencoded + retry sekali saat token basi (code≠200/HTTP≥400).

    `penuh=True` → kembalikan BODY UTUH (berisi `code`) alih-alih `data` saja.
    Dipakai endpoint TULIS yang sukses tanpa field `data` (unassignCars): di
    sana `data is None` berarti "tak ada isi", BUKAN gagal — kalau dibaca
    seperti biasa, operasi yang berhasil akan dilaporkan gagal."""
    def _once() -> requests.Response:
        return requests.post(f"{_BASE}{path}", data=data,
                             headers={"Authorization": _get_token(),
                                      "time-zone": _TZ_NAMA},
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
        if penuh:
            return body if isinstance(body, dict) else None
        return body.get("data") if isinstance(body, dict) else body
    except requests.RequestException as e:
        logger.info("telematics %s gagal: %s", path, e)
        return None


def _post_json(path: str, obj) -> dict | None:
    """Seperti _post tapi body JSON (endpoint organization pakai JSON, bukan
    form). Kembalikan BODY PENUH (dgn `code`) — sebagian endpoint sukses tanpa
    field `data`. None bila gagal/HTTP≥400. Retry sekali saat token basi."""
    def _once() -> requests.Response:
        return requests.post(f"{_BASE}{path}", json=obj,
                             headers={"Authorization": _get_token(),
                                      "time-zone": _TZ_NAMA},
                             timeout=_TIMEOUT)
    try:
        r = _once()
        body = r.json() if r.status_code < 400 else {}
        basi = (r.status_code in (401, 403)
                or (isinstance(body, dict) and body.get("code") in _KODE_RELOGIN))
        if basi:
            _get_token(force=True)
            r = _once()
            body = r.json() if r.status_code < 400 else {}
        if r.status_code >= 400:
            logger.info("telematics %s -> HTTP %s", path, r.status_code)
            return None
        return body if isinstance(body, dict) else None
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


def _kunci_unit(rec: dict) -> list[str]:
    """SEMUA identifier yang mungkin dipakai user untuk menyebut satu unit.

    User menyebut unit dengan apa saja: frame 8 karakter, VIN 17 karakter, plat
    yang dipasang sebagai nama, bahkan serial kotak GPS. Identifier itu tersebar
    di field yang berbeda (`cjh`, `vin`, `kdVin`, `carNumber`, `sbhList`), jadi
    mencari hanya di satu-dua field membuat unit yang ADA dilaporkan "tidak ada".
    VIN dummy pabrik dibuang — kalau tidak, satu kata kunci itu cocok ke ratusan
    unit sekaligus."""
    kunci = [rec.get("cjh"), rec.get("vin"), rec.get("kdVin"), rec.get("carNumber")]
    kunci += list(rec.get("sbhList") or [])
    out = []
    for k in kunci:
        s = str(k or "").strip().upper()
        if s and s != _DUMMY_VIN and s not in out:
            out.append(s)
    return out


def vin_asli(rec: dict) -> str | None:
    """VIN unit yang BENAR — `kdVin` lebih dulu, baru `vin`.

    ⚠️ Field `vin` di server TERKUNCI ke default firmware (SLGV…888) selamanya;
    VIN sungguhan yang diisi operator masuk ke `kdVin`. Karena itu VIN yang sudah
    diisi tetap terlihat kosong bila hanya `vin` yang dibaca — jangan lapor "VIN
    belum diisi" sebelum `kdVin` dicek."""
    for kandidat in (rec.get("kdVin"), rec.get("vin")):
        v = str(kandidat or "").strip().upper()
        if v and v != _DUMMY_VIN:
            return v
    return None


def peta_link(lat, lng) -> str | None:
    """Koordinat → link Google Maps. Field alamat dari portal sering setengah
    jadi ('Kabupaten Bengkalis, ') atau kosong, jadi link ini yang membuat posisi
    bisa dicek sendiri oleh user."""
    try:
        la, ln = float(lat), float(lng)
    except (TypeError, ValueError):
        return None
    if la == 0 and ln == 0:
        return None
    return f"https://www.google.com/maps?q={la},{ln}"


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


_MAKS_NEWEST = 12          # pagar panggilan queryVehicleNewestInfo per giliran


def sbh_dari_rec(rec: dict) -> str:
    """Serial perangkat GPS terpasang (kunci endpoint running-data)."""
    for s in (rec.get("sbhList") or []):
        if str(s or "").strip():
            return str(s).strip()
    return ""


def info_terbaru(sbh_list: list[str]) -> list[dict]:
    """queryVehicleNewestInfo — telemetri TERBARU per SERIAL GPS (`sbh`, BUKAN
    cjh): status, canTime/gpsTime/revdatetime (kapan terakhir kirim data),
    posisi + `lastlocation` (alamat), speed/rpm/suhu air, jam mesin, km & BBM
    hari ini, sinyal GSM. Portal mengirim SATU sbh per panggilan (`sbhList[0]`),
    tapi server MENERIMA banyak — diukur live di container 2026-08-13: 2 sbh →
    2 record. Susulan satu-satu tetap dipasang sebagai jaring bila suatu saat
    server hanya melayani sebagian.

    `revdatetime` di sini SAMA PERSIS dengan yang dari queryAllLocationStatus
    (dibanding pada 3 unit terlama, 2026-08-13) — jadi ringkasan se-armada cukup
    1 panggilan status massal, tak perlu N panggilan ke sini."""
    sbh = [str(s).strip() for s in (sbh_list or []) if str(s or "").strip()]
    if not sbh:
        return []
    sbh = sbh[:_MAKS_NEWEST]
    d = _post("/api/running-data/queryVehicleNewestInfo",
              {f"sbhList[{i}]": s for i, s in enumerate(sbh)})
    out = {str(r.get("sbh")): r for r in (d or []) if isinstance(r, dict)}
    for s in sbh:                       # susulan: server hanya layani index 0
        if s in out:
            continue
        d1 = _post("/api/running-data/queryVehicleNewestInfo", {"sbhList[0]": s})
        for r in (d1 or []):
            if isinstance(r, dict):
                out[str(r.get("sbh"))] = r
    return [out[s] for s in sbh if s in out]


def waktu_wib(stempel: str):
    """'2026-08-13 09:26:21' (jam portal, WIB) → datetime ber-zona. None bila
    kosong/tak terbaca — pemanggil WAJIB bedakan None dari 'lama offline'."""
    t = (stempel or "").strip()
    for pola in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return _dt.strptime(t, pola).replace(tzinfo=WIB)
        except ValueError:
            continue
    return None


def umur_jam(stempel: str) -> float | None:
    """Berapa JAM lalu stempel itu, relatif sekarang WIB. None bila tak terbaca."""
    w = waktu_wib(stempel)
    if w is None:
        return None
    return (_dt.now(WIB) - w).total_seconds() / 3600.0


def label_umur(jam: float | None) -> str:
    """Jam → frasa Indonesia ('baru saja', '3 jam lalu', '5 hari lalu')."""
    if jam is None:
        return "tidak diketahui"
    if jam < 0:
        return "baru saja"          # jam server sedikit mendahului kita
    if jam < 1:
        return f"{int(jam * 60)} menit lalu" if jam >= 1 / 60 else "baru saja"
    if jam < 24:
        return f"{int(jam)} jam lalu"
    return f"{int(jam // 24)} hari lalu"


def fleet_breakdown(recs: list[dict]) -> list[dict]:
    """Jumlah unit per fleet/organisasi (satu unit bisa di >1 org)."""
    hit: dict[str, int] = {}
    for r in recs:
        for n in _fleet_names(r):
            hit[n] = hit.get(n, 0) + 1
    return [{"fleet": k, "jumlah": v}
            for k, v in sorted(hit.items(), key=lambda kv: -kv[1])]


def cari_unit(cjh_atau_nama: str, recs: list[dict] | None = None) -> dict | None:
    """Cari SATU unit dari frame/VIN/kdVin/nama/serial GPS.

    Dua babak: COCOK PERSIS dulu di seluruh armada, baru cocok SEBAGIAN. Tanpa
    pemisahan itu, mengetik frame lengkap bisa mendarat di unit lain yang
    kebetulan memuat potongannya — yang cocok persis harus selalu menang.
    `recs` boleh dioper bila pemanggil sudah menarik daftar unit (hemat satu
    kali tarik data ke portal)."""
    q = (cjh_atau_nama or "").strip().upper()
    if not q:
        return None
    recs = _semua_records() if recs is None else recs
    for r in recs:
        if q in _kunci_unit(r):
            return r
    for r in recs:
        if any(q in k for k in _kunci_unit(r)):
            return r
    return None


def cari_mirip(q: str, recs: list[dict] | None = None, batas: int = 6) -> dict:
    """Tetangga terdekat saat unit TIDAK ketemu.

    "Tidak ada" itu jawaban mahal: user biasanya salah menyebut AWALAN pabrik
    sementara ekor nomornya benar, jadi pencocokan 6 karakter terakhir sering
    menemukan unit yang sebetulnya dimaksud. Rentang prefix ikut disertakan
    supaya user tahu formatnya wajar atau tidak — mis. awalan 'TJ' ada 116 unit,
    dari TJ458590 sampai TJ524659."""
    q = (q or "").strip().upper()
    if len(q) < 4:
        return {}
    recs = _semua_records() if recs is None else recs
    ekor = q[-6:]
    mirip: list[dict] = []
    for r in recs:
        for k in _kunci_unit(r):
            if ekor in k:
                mirip.append({"frame": r.get("cjh"), "nama": _nama_unit(r),
                              "cocok_pada": k})
                break
        if len(mirip) >= batas:
            break
    out: dict = {"mirip": mirip}
    awalan = q[:2]
    sekeluarga = sorted(str(r.get("cjh") or "").upper() for r in recs
                        if str(r.get("cjh") or "").upper().startswith(awalan))
    if sekeluarga:
        out["prefix"] = {"awalan": awalan, "jumlah": len(sekeluarga),
                         "dari": sekeluarga[0], "sampai": sekeluarga[-1]}
    return out


def ganti_nama(cjh: str, nama: str) -> dict | None:
    """⚠️ WRITE: ubah carNumber (nama/label) unit di server Sinotruk."""
    d = _post("/api/vehicleManage/updateCarNumber",
              {"cjh": cjh, "carNumber": nama})
    return d if isinstance(d, dict) else None


def cari_unit_lengkap(q: str) -> tuple[dict | None, dict]:
    """(unit, tetangga_terdekat) dalam SATU tarikan data.

    Dipisah dari `cari_unit` supaya jalur "tidak ketemu" tidak menarik seluruh
    armada dua kali: kalau unitnya ketemu, tetangga tak dihitung sama sekali."""
    recs = _semua_records()
    rec = cari_unit(q, recs)
    return rec, ({} if rec else cari_mirip(q, recs))


def set_vin(cjh: str, kd_vin: str) -> dict | None:
    """⚠️ WRITE: isi/override VIN unit (updateKdVin).

    Nilai masuk ke field `kdVin` — `vin` di server tetap default firmware dan
    itu NORMAL, bukan tanda gagal (verifikasi lewat `vin_asli`). Perangkat harus
    ONLINE: unit yang mati membalas kode gagal atau menggantung sampai timeout."""
    d = _post("/api/vehicleManage/updateKdVin",
              {"cjh": str(cjh).strip(), "kdVin": str(kd_vin).strip().upper()},
              penuh=True)
    return d if isinstance(d, dict) else None


def keluarkan_dari_fleet(cjh_list) -> bool:
    """⚠️ WRITE: batalkan alokasi unit (unassignCars) → unit kembali ke kolam
    'belum dialokasikan'.

    Ini UNDO dari `masukkan_ke_fleet`, dipakai saat unit salah masuk cabang.
    Sukses ditandai `code` 200: endpoint ini tidak membalas field `data`, jadi
    body dibaca utuh (`penuh=True`) — kalau tidak, yang berhasil malah terbaca
    gagal."""
    cjh = cjh_list if isinstance(cjh_list, str) else ",".join(
        str(c).strip().upper() for c in cjh_list if str(c or "").strip())
    if not cjh:
        return False
    b = _post("/api/organization/unassignCars", {"cjhList": cjh}, penuh=True)
    return isinstance(b, dict) and b.get("code") == 200


def atur_org_unit(perubahan: list[dict]) -> bool:
    """⚠️ WRITE: TAMBAH/HAPUS keanggotaan organisasi banyak unit sekaligus
    (updateMultiVehicleMultiOrg). Body = array
    [{cjh, delOrgId: [...], addOrgId: [...]}].

    Beda dari `masukkan_ke_fleet` yang MEMINDAHKAN unit ke satu fleet tujuan:
    di sini keanggotaan lama dipertahankan (`delOrgId` kosong) dan induk yang
    hilang ditambahkan. Satu panggilan untuk semua unit, bukan N panggilan."""
    if not perubahan:
        return False
    b = _post_json("/api/organization/updateMultiVehicleMultiOrg", perubahan)
    return isinstance(b, dict) and b.get("code") == 200


def _org_tree() -> dict | None:
    """Pohon organisasi/fleet (queryOrganization). Node akar = org login."""
    d = _post("/api/organization/queryOrganization", {})
    return d if isinstance(d, dict) else None


def daftar_fleet() -> list[dict]:
    """Ratakan pohon → [{id, nama, parent_id, jumlah_unit}] (akar diikutkan)."""
    root = _org_tree()
    out: list[dict] = []
    def walk(node):
        org = (node or {}).get("organization") or {}
        if org.get("id"):
            out.append({"id": org["id"], "nama": org.get("organizationName"),
                        "parent_id": org.get("parentId"),
                        "jumlah_unit": node.get("vehicleNum")})
        for c in (node.get("children") or []):
            walk(c)
    if root:
        walk(root)
    return out


def cari_fleet(nama_atau_id) -> dict | None:
    """Resolve fleet dari id (angka) atau NAMA (cocok persis dulu, lalu
    sebagian). Return {id, nama} atau None. >1 cocok sebagian → None + ambigu
    (pemanggil beri tahu user)."""
    q = str(nama_atau_id or "").strip()
    if not q:
        return None
    fleets = daftar_fleet()
    if q.isdigit():
        f = next((x for x in fleets if str(x["id"]) == q), None)
        return {"id": f["id"], "nama": f["nama"]} if f else None
    ql = q.lower()
    persis = [x for x in fleets if (x["nama"] or "").strip().lower() == ql]
    if persis:
        return {"id": persis[0]["id"], "nama": persis[0]["nama"]}
    sub = [x for x in fleets if ql in (x["nama"] or "").lower()]
    if len(sub) == 1:
        return {"id": sub[0]["id"], "nama": sub[0]["nama"]}
    if len(sub) > 1:
        return {"ambigu": [x["nama"] for x in sub[:10]]}
    return None


_TEMP_ID = -14622824    # id sementara node baru (server beri id asli); negatif


def _to_adjust(node: dict) -> dict:
    """Node queryOrganization → node adjustOrganization (change='unchanged').
    Skema newNode dari HAR: id/pid/label/organizationType + isLockedPid/
    isOnceUnlock/isLocked/canBeUnlock."""
    org = node.get("organization") or {}
    nn = {
        "id": org.get("id"), "pid": org.get("parentId"),
        "label": org.get("organizationName"),
        "organizationType": org.get("organizationType"),
        "isLockedPid": node.get("isLockedPid"),
        "isOnceUnlock": node.get("isOnceUnlock"),
        "isLocked": org.get("locked"),
        "canBeUnlock": node.get("canBeUnlock"),
    }
    return {"id": org.get("id"), "change": "unchanged",
            "detail": {"newNode": nn, "oldNode": dict(nn)},
            "children": [_to_adjust(c) for c in (node.get("children") or [])]}


def buat_fleet(nama: str, parent_id=None) -> dict | None:
    """⚠️ WRITE: buat fleet/organisasi baru (adjustOrganization, tree-diff).
    Kirim SELURUH pohon (semua 'unchanged') + node baru 'added' di bawah
    `parent_id` (default = org akar/login). Return {id, nama, parent_id} bila
    sukses (id = id asli hasil server), None bila gagal."""
    root = _org_tree()
    if not root:
        return None
    if parent_id is None:
        parent_id = (root.get("organization") or {}).get("id")
    adj = _to_adjust(root)
    added = {"id": _TEMP_ID, "change": "added",
             "detail": {"newNode": {"id": _TEMP_ID, "pid": parent_id,
                                    "label": nama, "organizationType": 0}},
             "children": []}

    def attach(n: dict) -> bool:
        if n.get("id") == parent_id:
            n.setdefault("children", []).append(added)
            return True
        return any(attach(c) for c in n.get("children") or [])
    if not attach(adj):
        return None                       # parent tak ditemukan di pohon
    b = _post_json("/api/organization/adjustOrganization", adj)
    if not (isinstance(b, dict) and b.get("code") == 200):
        return None
    # id asli: cari fleet ber-nama sama di bawah parent (dari pohon terbaru).
    baru = next((f for f in daftar_fleet()
                 if (f.get("nama") or "").strip().lower() == nama.strip().lower()
                 and f.get("parent_id") == parent_id), None)
    return {"id": (baru or {}).get("id"), "nama": nama, "parent_id": parent_id}


def masukkan_ke_fleet(target_org_id: int, cars: list[dict]) -> bool:
    """⚠️ WRITE: pindahkan unit ke fleet target (updateVehicleOrganization).
    cars = [{cjh, organizationIds:[org unit SAAT INI]}]. Resp sukses = code 200
    'Operate Success' (tanpa data)."""
    b = _post_json("/api/organization/updateVehicleOrganization",
                   {"targetOrgId": int(target_org_id), "updatedCars": cars})
    return isinstance(b, dict) and b.get("code") == 200


def daftarkan(sbh: str, vin: str, mileage=0, euro2: bool = False) -> dict | None:
    """⚠️ WRITE: DAFTARKAN unit baru ke telematics (recordingVehicle).
    `sbh` = serial perangkat GPS terpasang; `vin` = VIN 17-char penuh. Server
    menurunkan cjh dari VIN & memasukkan ke org login. Return record baru atau
    None (gagal/ditolak)."""
    try:
        km = int(mileage or 0)
    except (TypeError, ValueError):
        km = 0
    d = _post("/api/vehicleManage/recordingVehicle",
              {"sbh": str(sbh).strip(), "vin": str(vin).strip().upper(),
               "currentMileage": km,
               "isEuro2Vehicle": "true" if euro2 else "false"})
    return d if isinstance(d, dict) else None


def rangkum_unit(rec: dict, loc: dict | None = None) -> dict:
    """Satu unit → bentuk sajian asisten (statis + status GPS bila ada)."""
    out = {
        "frame": rec.get("cjh"),
        # ⚠️ `vin_asli` membaca kdVin DULU: VIN yang diisi operator masuk ke
        # sana, sedangkan field `vin` terkunci ke default firmware selamanya.
        # Membaca `vin` saja membuat unit yang VIN-nya SUDAH diisi tetap
        # dilaporkan kosong.
        "vin": vin_asli(rec),
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
        # `revdatetime` = kiriman data TERAKHIR. Disajikan SELALU (bukan hanya
        # saat posisi ada) — justru untuk unit offline inilah "kapan terakhir
        # online" yang dicari.
        ts = loc.get("revdatetime")
        out.update({
            "status_gps": STATUS_LABEL.get(loc.get("status"), loc.get("status")),
            "km_gps": loc.get("totalMileage"),
            "bbm_persen": loc.get("fuelLevel"),
            "rusak": loc.get("isFaulty"),
            "terakhir_online": ts or None,
            "terakhir_online_lalu": label_umur(umur_jam(ts)) if ts else None,
            # `peta` = link Google Maps siap klik. Alamat dari portal sering
            # setengah jadi ("Kabupaten Bengkalis, ") atau kosong, jadi tanpa
            # link ini koordinatnya tak bisa dicek sendiri oleh user.
            "posisi": ({"lat": loc.get("lat"), "lng": loc.get("lng"),
                        "waktu": ts,
                        "alamat": (str(loc.get("address")
                                       or loc.get("lastlocation") or "").strip()
                                   or None),
                        "peta": peta_link(loc.get("lat"), loc.get("lng"))}
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
