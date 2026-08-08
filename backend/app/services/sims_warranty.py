"""
SIMS Garansi & Klaim (DMS repair order) — status garansi unit, daftar/isi
work order klaim, jejak persetujuan, kebijakan tarif, dan foto klaim.

Sumber: HAR `simscloud.cnhtcerp.com.har` (dibedah 2026-07-22) + probe live —
seluruh endpoint terbuka dengan kredensial sims_fetcher yang sudah ada.
Detail endpoint & jebakan: memory `sims-repair-order-har.md`. Jebakan kunci:
  * `vehicleApi/getVehicleInfoAndMaintNum` WAJIB `chassisNo` = FRAME 8 karakter
    (mis. 'SJ346555'); VIN 17 karakter → HTTP 500. Frame = 8 char terakhir VIN.
  * `parts`/`labours` di detail WO KOSONG selama order belum dikerjakan
    (status kd/pg) — normal, bukan error.
  * `getActivateContractByVin` selalu null (kontrak servis berbayar — dealer
    tak punya) → JANGAN dipakai sebagai penanda garansi.
  * Kamus status hasil pemetaan RIGOR screenshot portal × API (kolom Repair(h)
    cocok baris-per-baris); `kswx`/`wg` belum pernah terlihat → belum pasti.

Pola auth = sims_eol.py: token sims_fetcher + retry sekali saat 401/403.
"""
from __future__ import annotations

import logging
import re
import threading
import time
from datetime import datetime, timezone

import requests

from . import sims  # menyiapkan sys.path ke backend/shared (jangan dibuang)

logger = logging.getLogger("maspart.sims_warranty")

_BASE = "http://simscloud.cnhtcerp.com:8082/intlapi"
_TIMEOUT = 30

# statusCode → label Indonesia (pemetaan rigor 2026-07-22; lihat docstring).
STATUS_LABEL: dict[str, str] = {
    "s-ro-status-kd": "Order dibuka",
    "s-ro-status-pg": "Kerja ditugaskan",
    "s-ro-status-zj": "Inspeksi akhir",
    "s-ro-status-dbcfwjl": "Menunggu review Manajer Servis Kantor Perwakilan",
    "s-ro-status-dqfwjl": "Menunggu review Manajer Servis Regional",
    "s-ro-status-js": "Selesai",
    "s-ro-status-zf": "Dibatalkan/dikembalikan",
    # Dipetakan 2026-07-22 dari singkatan pinyin standar + jejak audit:
    "s-ro-status-kswx": "Mulai perbaikan",       # 开始维修 (kāishǐ wéixiū)
    "s-ro-status-wg": "Selesai kerja",           # 完工 (wángōng)
    "s-ro-status-bh": "Diajukan bengkel",        # jejak audit: 服务站提交
}

_REPAIR_TYPE = {"ro-part-repair-gh": "ganti", "ro-part-repair-wx": "perbaiki"}


def status_label(code: str) -> str:
    return STATUS_LABEL.get(code or "", code or "?")


def available() -> bool:
    return bool(getattr(sims, "_SIMS_OK", False))


def frame_dari_rangka(rangka: str) -> str:
    """VIN 17 karakter → frame 8 karakter terakhir ('LZZ1ELSF7SJ392741' →
    'SJ392741', terverifikasi live); frame/kode pendek → apa adanya (upper)."""
    r = (rangka or "").strip().upper().replace(" ", "")
    return r[-8:] if len(r) >= 17 else r


def _headers() -> dict:
    import sims_fetcher as sf
    return {**sf.BASE_HEADERS, "Authorization": sf._get_token(), "language": "en"}


def _get(path: str, params: dict | None = None) -> dict | list | None:
    """GET + retry sekali saat token basi (401/403). None bila gagal total."""
    import sims_fetcher as sf
    url = _BASE + path
    r = requests.get(url, params=params or {}, headers=_headers(), timeout=_TIMEOUT)
    if r.status_code in (401, 403):
        sf._reset_token()
        r = requests.get(url, params=params or {}, headers=_headers(), timeout=_TIMEOUT)
    if r.status_code >= 400:
        logger.info("sims_warranty %s -> %s", path, r.status_code)
        return None
    try:
        return r.json()
    except ValueError:
        return None


def _data(d) -> dict | list | None:
    """Bungkus umum SIMS: {'code':0,'data':...} ATAU payload polos."""
    if isinstance(d, dict) and "data" in d:
        return d.get("data")
    return d


# ── cache ringan in-memory (pola per-TTL sederhana) ──────────────────
_cache_lock = threading.Lock()
_CACHE: dict[str, tuple[float, object]] = {}
_TTL_UNIT = 3600.0     # spek & garansi unit jarang berubah
_TTL_KLAIM = 300.0     # daftar/isi klaim bisa bergerak sepanjang hari


def _cached(key: str, ttl: float, fn):
    now = time.time()
    with _cache_lock:
        ent = _CACHE.get(key)
        if ent and now - ent[0] < ttl:
            return ent[1]
    val = fn()
    if val is not None:
        with _cache_lock:
            _CACHE[key] = (now, val)
    return val


def _tanggal(s: str | None) -> str:
    return (s or "")[:10]


def _sekarang() -> datetime:
    """Waktu kini (UTC) — dipisah supaya test bisa membekukannya."""
    return datetime.now(timezone.utc)


# ── API publik ───────────────────────────────────────────────────────
# ── Konfigurasi PABRIK per unit dari `configDesc` (2026-08-08) ──────────────
# `getVehicleInfoAndMaintNum` mengembalikan 61 field; info_unit dulu memungut
# belasan dan MEMBUANG `configDesc` — padahal di situlah konfigurasi pabrik
# per-unit ditulis (dipisah ';'), termasuk **kapasitas tangki BBM** yang selama
# ini dijawab "tidak ada di sistem" ke user lapangan. Contoh nyata:
#   SJ346500 → "…12.00R24轮胎(…);400L油箱;驾驶室双侧顶置警灯;…"
#   RT110063 → "…12.00R20(工程横向);300L油箱;TFT仪表（英文）;…"
# Terverifikasi 14 dari 16 unit uji membawa angkanya (120L–600L); 2 sisanya
# `configDesc`-nya memang KOSONG — itu lubang data, bukan gagal parsing.
# ⚠️ JANGAN pakai \b sesudah L: di '400L油箱' karakter berikutnya adalah CJK,
# dan CJK termasuk \w di Unicode → batas kata tak pernah terjadi & angkanya luput
# (bug nyata: ruas tangki terdeteksi tapi liter selalu kosong). Yang benar =
# 'L tidak diikuti huruf latin lain' (supaya 'LG9704…' dsb tak ikut tertangkap).
_RE_LITER = re.compile(r"(\d+(?:[.,]\d+)?)\s*L(?![A-Za-z])", re.I)


def _liter(teks: str) -> list[float]:
    """Semua angka berliter dalam satu ruas ('600L+400L' → [600, 400])."""
    out: list[float] = []
    for m in _RE_LITER.finditer(teks or ""):
        try:
            out.append(float(m.group(1).replace(",", ".")))
        except ValueError:
            pass
    return out


def konfigurasi_pabrik(config_desc: str) -> dict:
    """Ruas configDesc + kapasitas tangki BBM yang terbaca. {} bila teks kosong.

    ⛔ Ruas dikembalikan APA ADANYA (Bahasa China) — penerjemahan diserahkan ke
    lapisan jawaban. Menebak terjemahan di sini justru menyembunyikan istilah
    aslinya dari pembaca yang bisa memverifikasi."""
    teks = str(config_desc or "").strip()
    if not teks:
        return {}
    ruas = [x.strip() for x in re.split(r"[;；]", teks) if x.strip()]
    out: dict = {"ruas": ruas}

    # 油箱 = tangki bahan bakar. Ruas bisa >1 (unit tangki ganda), dan bisa
    # menyebut material ('120L铁油箱' = tangki besi) — angka L yang dipanen.
    ruas_tangki = [r for r in ruas if "油箱" in r]
    liter = [x for r in ruas_tangki for x in _liter(r)]
    if ruas_tangki:
        out["tangki_teks"] = ruas_tangki
    if liter:
        out["kapasitas_tangki_liter"] = round(sum(liter), 1)
        if len(liter) > 1:
            # Tangki ganda: totalnya sah, tapi rinciannya WAJIB ikut supaya
            # user tak mengira itu satu tangki besar.
            out["tangki_rincian_liter"] = liter
    return out


def info_unit(rangka: str) -> dict | None:
    """Spek unit + status garansi per frame. None bila tak ditemukan/gagal."""
    frame = frame_dari_rangka(rangka)
    if not frame:
        return None

    def _fetch():
        d = _data(_get("/intl.service.basic/vehicleApi/getVehicleInfoAndMaintNum",
                       {"chassisNo": frame}))
        return d if isinstance(d, dict) and d.get("chassisNo") else None

    d = _cached(f"unit:{frame}", _TTL_UNIT, _fetch)
    if not d:
        return None
    akhir = d.get("latestEndDate") or d.get("cnhtcLatestEndDate")
    sisa_hari = None
    aktif = None
    if akhir:
        try:
            dt = datetime.fromisoformat(str(akhir).replace("Z", "+00:00"))
            sisa_hari = (dt - _sekarang()).days
            aktif = sisa_hari >= 0
        except ValueError:
            pass
    persen = d.get("warrantyTimePercentage")
    return {
        "frame": d.get("chassisNo"),
        "unit": " ".join(x for x in (d.get("brandName"), d.get("seriesName"),
                                     d.get("driveMode")) if x),
        "model_code": d.get("modelCode"),
        "emisi": d.get("discharge"),
        "tipe_pakai": d.get("useType"),
        "dealer": d.get("dealerName"),
        "tanggal_keluar_pabrik": _tanggal(d.get("departureDate")),
        "tanggal_jual": _tanggal(d.get("saleDate")),
        "no_order_beli": d.get("orderNo"),
        "garansi": {
            "mulai": _tanggal(d.get("warrantyStartDate")),
            "berakhir": _tanggal(akhir),
            "berakhir_cnhtc": _tanggal(d.get("cnhtcLatestEndDate")),
            "masih_aktif": aktif,
            "sisa_hari": sisa_hari,
            "persen_terpakai": round(persen, 1) if isinstance(persen, (int, float)) else None,
        },
        "komponen": {
            "mesin": {"no_seri": d.get("engineNo"), "model": d.get("engineModelCode")},
            "gearbox": {"no_seri": d.get("gearboxNo"), "model": d.get("gearboxModelCode")},
            "gardan_depan": {"no_seri": d.get("axleFrontNo"), "model": d.get("axleFrontModelCode")},
            "gardan_tengah": {"no_seri": d.get("axleMidNo"), "model": d.get("axleMidModelCode")},
            "gardan_belakang": {"no_seri": d.get("axlxAftNo"), "model": d.get("axlxAftModelCode")},
        },
        "jumlah_servis": {
            "mesin": d.get("engineMaintNum"), "gearbox": d.get("gearboxMaintNum"),
            "gardan": d.get("bridgeMaintNum"), "sasis": d.get("chassisMaintNum"),
        },
        **({"konfigurasi_pabrik": _kf} if (_kf := konfigurasi_pabrik(d.get("configDesc"))) else {}),
    }


def daftar_klaim(vin: str = "", ro_no: str = "", halaman: int = 1,
                 page_size: int = 15) -> dict | None:
    """Daftar work order klaim. vin = FRAME number; keduanya opsional."""
    params: dict = {"currentPage": max(int(halaman or 1), 1),
                    "pageSize": max(min(int(page_size or 15), 50), 1)}
    if vin:
        params["vin"] = frame_dari_rangka(vin)
    if ro_no:
        params["roNo"] = ro_no.strip()
    key = f"klaim:{params}"

    def _fetch():
        d = _get("/intl.service.repair/repairOrder/queryRepairOrder", params)
        return d if isinstance(d, dict) and "records" in d else None

    d = _cached(key, _TTL_KLAIM, _fetch)
    if d is None:
        return None
    rows = []
    for x in (d.get("records") or []):
        rows.append({
            "no_wo": x.get("roNo"),
            "ro_id": x.get("roId"),
            "frame": x.get("vin"),
            "tanggal": _tanggal(x.get("createTime")),
            "km": x.get("mileage"),
            "gejala": x.get("faultContent"),
            "tindakan": x.get("handleMethod"),
            "nopol_atau_catatan": x.get("remark"),
            "status": status_label(x.get("statusCode")),
            "status_code": x.get("statusCode"),
            "durasi_jam": x.get("orderDuration"),
            "pelapor": x.get("reportName"),
            "mekanik": x.get("majorRepairName"),
            "tanggal_audit": _tanggal(x.get("roAuditTime")),
        })
    return {"total": d.get("totalCount"), "halaman": params["currentPage"],
            "klaim": rows}


_TTL_SEMUA = 600.0     # daftar-penuh klaim (paginasi mahal) — cache 10 menit


def semua_klaim(vin: str = "", maks_halaman: int = 40) -> dict | None:
    """SELURUH klaim (paginasi queryRepairOrder digabung) — untuk Excel & rekap.
    vin opsional (frame). Cache 10 menit per kunci. Cap `maks_halaman`×50 baris."""
    frame = frame_dari_rangka(vin) if vin else ""
    key = f"semua:{frame}:{maks_halaman}"

    def _fetch():
        semua: list[dict] = []
        total = None
        for hal in range(1, maks_halaman + 1):
            d = daftar_klaim(vin=frame, halaman=hal, page_size=50)
            if not d:
                break
            total = d.get("total")
            rows = d.get("klaim") or []
            semua.extend(rows)
            if not rows or (total and len(semua) >= total):
                break
        return {"total": total if total is not None else len(semua),
                "klaim": semua}

    return _cached(key, _TTL_SEMUA, _fetch)


_NILAI_MAKS_WO = 60      # batas WO yg dibuka utk agregasi nilai (per-WO lambat)


def nilai_wo(ro_id: str) -> float:
    """Nilai (CNY) satu WO = totalAmount tertinggi di amountList (per tahap
    audit). 0.0 bila tak ada / WO belum bernilai."""
    d = ringkas_audit(ro_id) or {}
    vals = [a.get("totalAmount") for a in (d.get("amountList") or [])
            if isinstance(a.get("totalAmount"), (int, float))]
    return float(max(vals)) if vals else 0.0


def nilai_klaim(ro_ids, maks: int = _NILAI_MAKS_WO) -> dict:
    """Agregasi nilai CNY beberapa WO (buka auditDetail PARALEL). BOUNDED —
    hanya `maks` WO pertama (buka tiap WO lambat). Return {total_cny, jumlah_wo,
    terpotong}."""
    ids = [i for i in ro_ids if i][:maks]
    terpotong = len([i for i in ro_ids if i]) > maks
    if not ids:
        return {"total_cny": 0.0, "jumlah_wo": 0, "terpotong": False}
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=6) as ex:
        vals = list(ex.map(nilai_wo, ids))
    return {"total_cny": round(sum(vals), 2), "jumlah_wo": len(ids),
            "terpotong": terpotong}


def detail_wo(ro_id: str) -> dict | None:
    """Isi lengkap satu WO: part, jasa, oli, biaya (repairOrder/detail)."""
    def _fetch():
        d = _data(_get(f"/intl.service.repair/repairOrder/detail/{ro_id}"))
        return d if isinstance(d, dict) and d.get("roId") else None
    return _cached(f"wo:{ro_id}", _TTL_KLAIM, _fetch)


def ringkas_audit(ro_id: str) -> dict | None:
    """auditDetail — dipakai untuk fileMap foto (+assignInfo)."""
    def _fetch():
        d = _data(_get(f"/intl.service.repair/repairOrder/auditDetail/{ro_id}"))
        return d if isinstance(d, dict) and d.get("roId") else None
    return _cached(f"audit:{ro_id}", _TTL_KLAIM, _fetch)


def jejak_audit(ro_id: str) -> list[dict]:
    """Jejak alur persetujuan (bisa KOSONG bila WO belum masuk alur — normal)."""
    d = _data(_get("/intl.auth/sys/auditLog/getAuditLog", {"dataId": ro_id}))
    rows = d if isinstance(d, list) else []
    rows = sorted(rows, key=lambda e: (e or {}).get("auditTime") or "")
    out = []
    for e in rows:
        out.append({
            "waktu": e.get("auditTime"),
            "tahap": e.get("actName"),
            "oleh": e.get("auditorName"),
            "aksi": e.get("operateType"),
            "opini": e.get("auditOpinion"),
            "tahap_berikut": e.get("toNodeName"),
            "menunggu": e.get("toAuditorName"),
        })
    return out


def kebijakan(ro_id: str) -> dict | None:
    """Kebijakan/tarif garansi yang berlaku untuk satu WO."""
    d = _data(_get(f"/intl.service.repair/orderCommon/getServicePolicyByRo/{ro_id}"))
    if not isinstance(d, dict) or not d.get("servicePolicyId"):
        return None
    return {
        "mata_uang": d.get("declarationCurrencyCode"),
        "tarif_jasa_per_jam": d.get("labourPrice"),
        "tarif_jasa_truk_ringan": d.get("oneSettleLabourPriceLightTruck"),
        "garansi_dealer": d.get("dealerGuarantee"),
        "garansi_cnhtc": d.get("cnhtcGuarantee"),
        "batas_audit_dbc": d.get("dbcAuditAmount"),
        "batas_audit_shfwb": d.get("shfwbAuditAmount"),
        "batas_audit_dq": d.get("dqAuditAmount"),
    }


def foto_bytes(file_upload_info_id: str) -> bytes | None:
    """Unduh satu foto klaim (jpeg). security-token = JWT tanpa prefix Bearer."""
    import sims_fetcher as sf
    tok = sf._get_token()
    if tok.startswith("Bearer "):
        tok = tok[len("Bearer "):]
    try:
        r = requests.get(_BASE + "/intl.file/stdp/file/filesDownload",
                         params={"fileUploadInfoId": file_upload_info_id,
                                 "security-token": tok},
                         headers=_headers(), timeout=_TIMEOUT)
        if r.status_code != 200 or not r.content:
            return None
        if "json" in (r.headers.get("Content-Type") or ""):
            return None                      # balasan error, bukan gambar
        return r.content
    except requests.RequestException:
        return None


def format_part(p: dict) -> dict:
    """Satu baris part klaim → bentuk sajian asisten (CNY apa adanya)."""
    return {
        "pn": p.get("partCode"),
        "nama": p.get("partName"),
        "nama_cn": p.get("zhOldPartName"),
        "qty": p.get("partNum"),
        "harga_cny": p.get("partPrice"),
        "total_cny": p.get("partAmount"),
        "biaya_admin_cny": p.get("partMgtAmount"),
        "jenis": _REPAIR_TYPE.get(p.get("repairType") or "", p.get("repairType")),
        "kode_mode_gagal": p.get("faultModel"),
        "penanggung_jawab": p.get("dutyCode"),
        "supplier_part_lama": p.get("oldPartSupplierCode"),
    }


def format_jasa(j: dict) -> dict:
    return {
        "kode": j.get("labourCode"),
        "nama": j.get("labourName"),
        "jam_kuota": j.get("labourQuotaNoRepeat"),
        "tarif_per_jam_cny": j.get("labourPrice"),
        "total_cny": j.get("labourAmount"),
    }
