"""
Service EPC Sinotruk — SERVICE MANUAL (buku servis/perbaikan) per NOMOR RANGKA.

Portal ICMCP (port 18080) menyimpan dokumen servis resmi PER UNIT: manual
perbaikan gearbox ZF, buku gardan, diagram kelistrikan, lembar spesifikasi.
Tak ada satu endpoint "daftar manual per VIN" — rantainya BERTINGKAT:

    getVehicleConfig(rangka)      → brand/subSeries/driveMode/kode gardan
      ├─ maintenance/select       (POSISI + brand/subSeries)  → dokumen umum posisi
      ├─ modellink/getFdjBsxView  (POSISI + chassisNo)        → mesin/transmisi/kopling
      └─ modellink/getQiaoView    (kode gardan + driveMode)   → gardan depan/tengah/belakang
            ↓ id manual
      maintenance/queryMaintenanceManualFile?maintenanceManualId=  → fileCode + nama
            ↓ fileCode
      GET /api/rest/file/<fileCode>                                → PDF

⚠️ DIUKUR 2026-08-14 — 'totalFileCount' dari maintenanceQueryLog/create BUKAN
jumlah manual: nilainya 0/1/None pada unit yang justru punya 3/4/8 dokumen (itu
penghitung log kueri). ⛔ JANGAN dipakai sebagai jumlah dokumen atau validasi.

⚠️ maintenance/select dengan brand/subSeries KOSONG membalas KATALOG GLOBAL
(mis. QDQ 24 dokumen lintas model) — bukan milik unit ini. Filter brand WAJIB
diisi supaya jawabannya benar-benar per-unit.

Auth: header ``token: Bearer <hex>`` — token yang SAMA dengan epc_bom (port 7001),
dicetak otomatis lewat SSO SimsCloud. Token kedaluwarsa dijawab JUJUR oleh server
(``success:false`` + code 110003 'Login expired!'), dikenali _TOKEN_ERR_RE milik
epc_bom → auto-refresh lalu ulang sekali. Tanpa header token, lapisan dokumen
membalas 110025 'Not has role!' (juga dikenali) — jadi tak ada jalur yang diam-diam
mengembalikan daftar kosong.
"""
from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor

import requests
import urllib3

from . import epc, epc_bom
from .cache_util import CacheTTL

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logger = logging.getLogger(__name__)

_BASE = "https://epc.sinotruk.com:18080/api/rest"
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36")

# Posisi (modul kendaraan) di portal EPC → label Indonesia untuk user.
_POSISI = {
    "FDJ": "Mesin",
    "BSX": "Transmisi",
    "LHQ": "Kopling",
    "CDQ": "Gardan depan",
    "QDQ": "Gardan penggerak",
    "DP": "Sasis",
    "DQXT": "Kelistrikan",
    "JSS": "Kabin & bodi",
}
# Posisi yang punya kanal KHUSUS per-chassis (di luar maintenance/select).
_POS_FDJBSX = ("FDJ", "BSX", "LHQ")
_POS_QIAO = ("QDQ", "CDQ")

# Daftar dokumen per rangka relatif statis (dokumen pabrik) tapi TIDAK abadi —
# EPC menambah manual baru. TTL 6 jam + plafon 300 entri (armada 1.335 unit,
# yang ditanya user cuma segelintir per hari).
_cache_daftar = CacheTTL("epc_manual.daftar", 6 * 3600.0, 300)
_lock = threading.Lock()

# Plafon ukuran unduhan. Manual terbesar yang terukur 44 MB (diagram kelistrikan
# C7H); 120 MB memberi ruang lega tanpa membuka pintu ke berkas raksasa yang bisa
# menghabiskan RAM backend (VPS 3,8 GB, backend dibatasi 2500m).
_MAKS_UNDUH = 120 * 1024 * 1024


def available() -> bool:
    """Ada token EPC (dipakai/bisa di-refresh). Sama syaratnya dengan epc_bom."""
    return bool(epc_bom._token()) or True  # token bisa dicetak otomatis saat dipakai


def _headers(terima_biner: bool = False) -> dict:
    return {
        "token": epc_bom._token(),
        "Accept": "*/*" if terima_biner else "application/json, text/plain, */*",
        "Referer": "https://epc.sinotruk.com:18080/",
        "User-Agent": _UA,
    }


def _call(method: str, path: str, *, params: dict | None = None,
          json_body: dict | None = None, timeout: float = 30.0) -> dict:
    """Satu panggilan EPC 18080 → {"data": ...} atau {"_err": ...}.

    Membedakan TIGA hal yang dulu jadi sumber bug di tool EPC lain: jaringan mati
    ('network'), token kedaluwarsa ('token_expired'), dan jawaban sah yang memang
    kosong ({"data": []}). Pemanggil WAJIB membedakan yang pertama-kedua dari
    yang ketiga — 'gagal cek' bukan 'tidak ada manual'.
    """
    if not epc_bom._token():
        return {"_err": "no_token"}
    # Ikut pemutus arus EPC bersama: saat EPC ambruk, 13 panggilan daftar() akan
    # menggantung 13 × timeout kalau tidak dipagari.
    if not epc_bom._cb_boleh_jalan():
        return {"_err": "network"}
    try:
        r = requests.request(method, f"{_BASE}{path}", headers=_headers(),
                             params=params, json=json_body,
                             timeout=timeout, verify=False)
        j = r.json()
    except Exception:
        epc_bom._cb_lapor(True)
        return {"_err": "network"}
    epc_bom._cb_lapor(False)          # server MENJAWAB → EPC hidup
    if not isinstance(j, dict):
        return {"_err": "api", "message": "bad_json"}
    if not j.get("success"):
        code = str(j.get("code") or "")
        msg = str(j.get("message") or "")
        if code == "110025" or epc_bom._TOKEN_ERR_RE.search(msg):
            return {"_err": "token_expired"}
        return {"_err": "api", "message": msg}
    return {"data": j.get("data")}


def _call_auto(method: str, path: str, **kw) -> dict:
    """_call + auto-refresh token via SSO bila kedaluwarsa (transparan ke user)."""
    res = _call(method, path, **kw)
    if res.get("_err") in ("token_expired", "no_token"):
        with epc_bom._refresh_lock:
            res = _call(method, path, **kw)      # thread lain mungkin sudah refresh
            if res.get("_err") in ("token_expired", "no_token") and epc_bom.refresh_token():
                res = _call(method, path, **kw)
    return res


def _rows(res: dict) -> list:
    d = res.get("data")
    return d if isinstance(d, list) else []


def _label(manual: dict, berkas: dict) -> str:
    """Judul yang dilihat user. fileTitle paling bersih & sudah Inggris; sisanya
    jatuh ke title/modelClass (kerap China) lalu nama berkas."""
    for k in (berkas.get("fileTitle"), manual.get("title"),
              manual.get("modelClass"), berkas.get("fileName")):
        if (k or "").strip():
            return str(k).strip()
    return "Dokumen servis"


def daftar(rangka: str) -> dict:
    """Daftar dokumen servis resmi milik SATU unit.

    Kembalian:
      {"found": True, "rangka": .., "unit": {..}, "dokumen": [{nomor, judul, ..}]}
      {"found": False, "_err": "network"|"token_expired"} → GAGAL CEK, bukan 'kosong'
      {"found": False, "kosong": True}                    → EPC menjawab, memang tak ada
    """
    cjh = epc._frame(rangka)
    if not cjh:
        return {"found": False, "error": "Nomor rangka kosong."}
    with _lock:
        if cjh in _cache_daftar:
            return _cache_daftar[cjh]

    cfg = epc.get_config(cjh)
    if cfg.get("_err"):
        return {"found": False, "_err": "network", "rangka": cjh,
                "catatan": "EPC Sinotruk gagal dihubungi — daftar manual unit ini "
                           "BELUM bisa dipastikan. ⛔ JANGAN bilang tidak ada manual; "
                           "minta user coba lagi sebentar."}
    if not cfg:
        return {"found": False, "rangka": cjh,
                "catatan": "Nomor rangka tidak dikenal EPC Sinotruk (cek ejaan; EPC "
                           "hanya memuat unit Sinotruk/HOWO/SITRAK)."}

    brand_id = str(cfg.get("brandId") or "")
    brand_nm = str(cfg.get("brandName") or "")
    sub_id = str(cfg.get("subSeriesId") or "")
    sub_nm = str(cfg.get("subSeriesName") or "")

    # ── Lapis 1: kumpulkan ID manual dari tiga kanal (paralel) ──────────────
    tugas: list = []
    for pos in _POSISI:
        tugas.append(("select", pos, lambda p=pos: _call_auto(
            "POST", "/maintenance/select",
            json_body={"position": p, "modelClass": "", "brandId": brand_id,
                       "brandName": brand_nm, "subSeriesName": sub_nm,
                       "subSeriesId": sub_id, "language": "en-US"})))
    for pos in _POS_FDJBSX:
        tugas.append(("fdjbsx", pos, lambda p=pos: _call_auto(
            "POST", "/modellink/getFdjBsxView",
            json_body={"chassisNo": cjh, "position": p, "language": "en-US"})))
    for pos in _POS_QIAO:
        # ⛔ 'axlxAftModelCode' — salah eja di API EPC (bukan 'axleAft'). Jangan
        # "dirapikan": server mengabaikan kunci yang tak dikenal → gardan belakang
        # hilang dari hasil tanpa error apa pun.
        tugas.append(("qiao", pos, lambda p=pos: _call_auto(
            "POST", "/modellink/getQiaoView",
            json_body={"position": p, "driveMode": cfg.get("driveMode"),
                       "language": "en-US",
                       "axleFrontModelCode": cfg.get("axleFrontModelCode"),
                       "axleMidModelCode": cfg.get("axleMidModelCode"),
                       "axleMidSecModelCode": cfg.get("axleMidSecModelCode"),
                       "axlxAftModelCode": cfg.get("axlxAftModelCode")})))

    with ThreadPoolExecutor(max_workers=6) as ex:
        hasil = list(ex.map(lambda t: (t[0], t[1], t[2]()), tugas))

    gagal_jaringan = any(r.get("_err") in ("network", "token_expired")
                         for _, _, r in hasil)
    manual_per_id: dict = {}
    for src, pos, res in hasil:
        for m in _rows(res):
            mid = m.get("id")
            if mid is not None and mid not in manual_per_id:
                manual_per_id[mid] = (pos, src, m)

    if not manual_per_id:
        if gagal_jaringan:
            return {"found": False, "_err": "network", "rangka": cjh,
                    "catatan": "EPC Sinotruk tak menjawab saat mengambil daftar manual "
                               "— status BELUM pasti. ⛔ JANGAN simpulkan tidak ada."}
        out = {"found": False, "kosong": True, "rangka": cjh,
               "unit": _ringkas_unit(cfg),
               "catatan": "EPC menjawab: tidak ada dokumen servis terdaftar untuk unit ini."}
        with _lock:
            _cache_daftar[cjh] = out
        return out

    # ── Lapis 2: id manual → berkas (paralel) ───────────────────────────────
    ids = list(manual_per_id)
    with ThreadPoolExecutor(max_workers=6) as ex:
        berkas_res = list(ex.map(
            lambda i: (i, _call_auto("GET", "/maintenance/queryMaintenanceManualFile",
                                     params={"maintenanceManualId": i})), ids))

    dokumen: list[dict] = []
    lihat_kode: set = set()
    for mid, res in berkas_res:
        if res.get("_err"):
            gagal_jaringan = gagal_jaringan or res["_err"] in ("network", "token_expired")
            continue
        pos, src, m = manual_per_id[mid]
        for f in _rows(res):
            kode = (f.get("fileCode") or "").strip()
            # Satu berkas fisik kerap tergantung di BEBERAPA id manual (mis. ZF
            # 16S 'dengan' & 'tanpa' retarder → PDF yang sama). Dedup per fileCode
            # supaya user tak melihat baris kembar.
            if not kode or kode in lihat_kode:
                continue
            lihat_kode.add(kode)
            dokumen.append({
                "nomor": len(dokumen) + 1,
                "judul": _label(m, f),
                "bagian": _POSISI.get(pos, pos),
                "nama_berkas": f.get("fileName"),
                "keterangan": f.get("fileDescription"),
                "file_code": kode,
            })

    out = {
        "found": bool(dokumen),
        "rangka": cjh,
        "unit": _ringkas_unit(cfg),
        "jumlah": len(dokumen),
        "dokumen": dokumen,
    }
    if not dokumen:
        out["kosong"] = True
    if gagal_jaringan:
        # Sebagian kanal gagal → daftar ini MUNGKIN belum lengkap. Katakan, jangan
        # sajikan sebagai daftar final.
        out["sebagian"] = True
        out["catatan_gagal"] = ("⚠️ Sebagian kanal EPC tak menjawab — daftar ini bisa "
                                "BELUM lengkap. Sampaikan apa adanya ke user.")
    else:
        with _lock:
            _cache_daftar[cjh] = out
    return out


def _ringkas_unit(cfg: dict) -> dict:
    """Identitas singkat unit supaya user yakin manualnya milik truk yang benar."""
    brand = cfg.get("brandName") or ""
    return {
        "vin": cfg.get("vin"),
        "model": cfg.get("modelCode"),
        "brand": epc._BRAND.get(brand, brand),
        "seri": cfg.get("subSeriesName") or cfg.get("seriesName"),
        "mesin": cfg.get("engineModelCode"),
        "transmisi": cfg.get("gearboxModelCode"),
        "gardan_depan": cfg.get("axleFrontModelCode"),
        "gardan_belakang": cfg.get("axlxAftModelCode"),
    }


def unduh(file_code: str) -> tuple[bytes | None, str]:
    """Ambil PDF satu dokumen → (bytes, pesan_error_kosong) / (None, alasan).

    Diunduh streaming dengan plafon ukuran: berkas EPC bisa puluhan MB dan
    backend berbagi RAM 3,8 GB dengan layanan lain.
    """
    kode = (file_code or "").strip()
    if not kode:
        return None, "Kode berkas kosong."
    for percobaan in (1, 2):
        if not epc_bom._token():
            if not epc_bom.refresh_token():
                return None, "Token EPC tak tersedia dan gagal dicetak ulang."
        if not epc_bom._cb_boleh_jalan():
            return None, "EPC Sinotruk sedang tak bisa dihubungi (pemutus arus aktif)."
        try:
            r = requests.get(f"{_BASE}/file/{kode}", headers=_headers(terima_biner=True),
                             timeout=180, verify=False, stream=True)
        except Exception:
            epc_bom._cb_lapor(True)
            return None, "EPC Sinotruk gagal dihubungi saat mengunduh berkas."
        epc_bom._cb_lapor(False)
        # ⛔ JANGAN memutuskan dari Content-Type. DIUKUR 2026-08-14: endpoint ini
        # membalas `Content-Type: application/json` untuk PDF yang SAH — tipe yang
        # diklaim ikut berubah mengikuti header Accept yang kita kirim. Satu-satunya
        # penanda jujur adalah ISI-nya: berkas PDF selalu diawali '%PDF'.
        buf = bytearray()
        try:
            for bagian in r.iter_content(256 * 1024):
                buf += bagian
                if len(buf) > _MAKS_UNDUH:
                    r.close()
                    return None, (f"Berkas melebihi batas {_MAKS_UNDUH // (1024*1024)} MB "
                                  "— tak diunduh demi jaga memori server.")
                if len(buf) < 4:
                    continue
                if bytes(buf[:4]) != b"%PDF":
                    break        # bukan PDF → hampir pasti amplop error JSON
        except Exception:
            r.close()
            epc_bom._cb_lapor(True)
            return None, "Sambungan ke EPC terputus saat mengunduh berkas."
        r.close()
        if bytes(buf[:4]) == b"%PDF":
            return bytes(buf), ""
        # Bukan PDF → biasanya {"success":false,...} token/akses. Refresh sekali.
        pesan = buf[:200].decode("utf-8", "replace")
        if percobaan == 1 and epc_bom.refresh_token():
            continue
        logger.info("unduh manual gagal kode=%s status=%s awal=%r", kode, r.status_code, pesan)
        return None, f"EPC menolak permintaan berkas (status {r.status_code})."
    return None, "Gagal mengunduh berkas dari EPC."
