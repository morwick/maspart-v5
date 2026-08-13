# -*- coding: utf-8 -*-
"""
Panen DATASET KLAIM GARANSI SIMS (kerusakan lapangan armada sendiri) →
data/warranty/warranty_klaim.json.gz

Isi tiap baris: gejala yang dilaporkan + tindakan yang dilakukan + PART yang
diganti (PN, qty, harga CNY, MODE KEGAGALAN) + jasa + km + rangka + tanggal.
Ini kasus NYATA, bukan katalog — bahan mentah paling relevan untuk `diagnosa`,
sinyal part cepat-habis, dan pengetahuan gejala→part.

Sumber (semua GET, kredensial sims_fetcher yang sudah ada — lihat memory
`sims-repair-order-har.md`; endpoint dikonfirmasi ulang dari HAR
"Cek Data Data Warranty.har" 2026-08-13):
  • `intl.service.repair/repairOrder/queryRepairOrder`  → daftar klaim (paginasi)
  • `intl.service.repair/repairOrder/detail/{roId}`     → ISI: parts[]/labours[]
  • `intl.service.basic/failureModeApi/getFailureModeByPartCode` → kamus mode gagal
  • `intl.service.basic/positionApi/getResponsibleManufacturer` → kamus pabrik

⛔ JEBAKAN (sudah terbukti, jangan diulang):
  1. `auditDetail/{roId}` TIDAK punya `parts`/`labours` datar — isinya `auditParts`
     yang BERSARANG per tahap audit (gys-idea / s-ro-status-kd / …). Yang datar
     dan siap pakai hanya `detail/{roId}`. Keduanya sudah diuji live 2026-08-13.
  2. `faultModel` di baris part adalah KODE ("029"), bukan nama. Tanpa kamus
     `failureModeApi` angka itu tak berarti apa-apa bagi pembaca.
  3. `parts`/`labours` KOSONG selama WO belum dikerjakan (status kd/pg) — itu
     NORMAL, bukan galat. Baris tetap disimpan (gejala+tindakan tetap berharga).
  4. `vin` di API klaim = FRAME 8 karakter, BUKAN VIN 17 karakter.

Tahan putus: checkpoint per-WO (resumable), dan menolak menimpa berkas lama
bila hasil panen nihil total.

Jalankan dari root repo:
    py backend/tools/build_warranty_dataset.py                # semua klaim
    py backend/tools/build_warranty_dataset.py --maks 50      # uji cepat
    py backend/tools/build_warranty_dataset.py --pekerja 4    # lebih sopan
"""
from __future__ import annotations

import argparse
import gzip
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend" / "shared"))
import sims_fetcher as sf                                    # noqa: E402

BASE = "http://simscloud.cnhtcerp.com:8082/intlapi"
TIMEOUT = 30
KELUARAN = ROOT / "data" / "warranty" / "warranty_klaim.json.gz"
CHECKPOINT = ROOT / "data" / "warranty" / "_checkpoint_wo.json"

# statusCode → label (pemetaan rigor 2026-07-22, lihat sims_warranty.py)
STATUS_LABEL = {
    "s-ro-status-kd": "Order dibuka",
    "s-ro-status-pg": "Kerja ditugaskan",
    "s-ro-status-zj": "Inspeksi akhir",
    "s-ro-status-dbcfwjl": "Menunggu review Manajer Servis Kantor Perwakilan",
    "s-ro-status-dqfwjl": "Menunggu review Manajer Servis Regional",
    "s-ro-status-js": "Selesai",
    "s-ro-status-zf": "Dibatalkan/dikembalikan",
    "s-ro-status-kswx": "Mulai perbaikan",
    "s-ro-status-wg": "Selesai kerja",
    "s-ro-status-bh": "Diajukan bengkel",
}
REPAIR_TYPE = {"ro-part-repair-gh": "ganti", "ro-part-repair-wx": "perbaiki"}


def _headers() -> dict:
    return {**sf.BASE_HEADERS, "Authorization": sf._get_token(), "language": "en"}


def _get(path: str, params: dict | None = None, *, coba: int = 3):
    """GET + refresh token saat 401/403 + retry galat jaringan. None bila gagal."""
    url = BASE + path
    for n in range(coba):
        try:
            r = requests.get(url, params=params or {}, headers=_headers(),
                             timeout=TIMEOUT)
        except requests.RequestException:
            time.sleep(0.8 * (n + 1))
            continue
        if r.status_code in (401, 403):
            sf._reset_token()
            continue
        if r.status_code >= 400:
            return None
        try:
            return r.json()
        except ValueError:
            return None
    return None


def _data(d):
    return d.get("data") if isinstance(d, dict) and "data" in d else d


def _tgl(s):
    return (s or "")[:10]


# ── 1. daftar seluruh klaim ─────────────────────────────────────────────
def panen_daftar(maks: int = 0) -> list[dict]:
    semua, total, hal = [], None, 1
    while True:
        d = _data(_get("/intl.service.repair/repairOrder/queryRepairOrder",
                       {"currentPage": hal, "pageSize": 50}))
        if not isinstance(d, dict):
            break
        total = d.get("totalCount")
        rows = d.get("records") or []
        semua.extend(rows)
        print(f"  daftar hal {hal}: +{len(rows)} (total {len(semua)}/{total})")
        if not rows or (total and len(semua) >= total) or (maks and len(semua) >= maks):
            break
        hal += 1
    return semua[:maks] if maks else semua


# ── 2. isi tiap WO (part + jasa) ────────────────────────────────────────
def isi_wo(ro_id: str) -> dict | None:
    d = _data(_get(f"/intl.service.repair/repairOrder/detail/{ro_id}"))
    return d if isinstance(d, dict) and d.get("roId") else None


# ── 3. kamus mode kegagalan per PN (batch) ──────────────────────────────
def kamus_mode_gagal(part_codes: list[str], ukuran: int = 25) -> dict[str, str]:
    """kode mode gagal → nama. API-nya per-PN, tapi kodenya konsisten global;
    bentrok (kode sama, nama beda) dilaporkan agar tak diam-diam tertimpa."""
    kamus: dict[str, str] = {}
    bentrok: set[str] = set()
    pn = sorted({p for p in part_codes if p})
    for i in range(0, len(pn), ukuran):
        chunk = pn[i:i + ukuran]
        d = _data(_get("/intl.service.basic/failureModeApi/getFailureModeByPartCode",
                       {"partCode": ",".join(chunk), "vmodel": ""}))
        if not isinstance(d, dict):
            continue
        for modes in d.values():
            for m in modes or []:
                k, v = m.get("failureModeCode"), m.get("failureModeName")
                if not k or not v:
                    continue
                if k in kamus and kamus[k] != v:
                    bentrok.add(f"{k}: {kamus[k]} ≠ {v}")
                kamus[k] = v
        print(f"  kamus mode gagal: {i + len(chunk)}/{len(pn)} PN → {len(kamus)} kode")
    if bentrok:
        print("  ⚠️ kode mode gagal BENTROK:", "; ".join(sorted(bentrok)))
    return kamus


# dutyCode → nama Indonesia. Nama resminya (Mandarin) datang dari API; sisi
# Indonesia-nya diterjemahkan di sini supaya asisten tak menyodorkan hanzi.
# ⚠️ `WP` = 成都王牌公司 (Chengdu Wangpai), BUKAN Weichai — catatan lama saya keliru.
PABRIK_ID = {
    "GJ": "CNHTC International", "BSX": "Pabrik Transmisi Jinan",
    "QX": "Pabrik Gardan Jinan", "JININGSYC": "Kendaraan Niaga Jining",
    "AK": "Jinan Truk Ringan", "ZQJZ": "Sinotruk Jinan Kendaraan Khusus",
    "JNSYC": "Kendaraan Niaga Jinan", "AQ": "Chongqing Automobile",
    "FJHX": "Fujian Haixi", "KC": "Pabrik Truk Jinan",
    "WP": "Chengdu Wangpai", "JD": "Pabrik Mesin Jinan",
    "QK": "Pabrik Truk Ringan Jinan", "TZC": "Pabrik Kendaraan Khusus",
    "HF": "Pabrik Mesin Hangzhou", "DC": "Datong Dachi (gearbox)",
    "TAWY": "Sinotruk Tai'an Wuyue Kendaraan Khusus",
    "HBHW": "Sinotruk Hubei Huawei Kendaraan Khusus",
}


def kamus_pabrik(sampel: list[tuple[str, str]]) -> dict[str, dict]:
    """dutyCode → nama pabrik penanggung jawab.
    ⛔ Endpoint MENOLAK parameter kosong (HTTP 400) — wajib chassisNo + partCode
    nyata. ⚠️ Dan balasannya BERBEDA-BEDA per chassis/part (terbukti: ZQJZ ada
    di satu sampel, hilang di sampel lain) → beberapa sampel DIGABUNG."""
    kamus: dict[str, str] = {}
    for chassis, pn in sampel:
        d = _data(_get("/intl.service.basic/positionApi/getResponsibleManufacturer",
                       {"chassisNo": chassis, "partCodeList": pn}))
        if isinstance(d, dict):
            kamus.update({k: v for k, v in d.items() if isinstance(v, str)})
    return {k: {"cn": v, "id": PABRIK_ID.get(k)} for k, v in sorted(kamus.items())}


# ── 4. perakitan baris dataset ──────────────────────────────────────────
def baris(rec: dict, wo: dict | None, modes: dict) -> dict:
    parts, jasa, oli, tambahan = [], [], [], []
    biaya = {}
    if wo:
        for p in wo.get("parts") or []:
            kode = p.get("faultModel")
            parts.append({
                "pn": p.get("partCode"),
                "nama": p.get("partName"),
                "nama_cn": p.get("zhOldPartName"),
                "qty": p.get("partNum"),
                "harga_cny": p.get("partPrice"),
                "total_cny": p.get("partAmount"),
                "jenis": REPAIR_TYPE.get(p.get("repairType") or "", p.get("repairType")),
                "mode_gagal_kode": kode,
                "mode_gagal": modes.get(kode or ""),
                "penanggung_jawab": p.get("dutyCode"),
                "supplier_part_lama": p.get("oldPartSupplierCode"),
                "jenis_garansi": p.get("guaranteeType"),
                "klaim_berulang": p.get("isRepeatClaim") == "1",
            })
        for j in wo.get("labours") or []:
            jasa.append({
                "kode": j.get("labourCode"), "nama": j.get("labourName"),
                "jam_kuota": j.get("labourQuotaNoRepeat"),
                "tarif_cny": j.get("labourPrice"), "total_cny": j.get("labourAmount"),
            })
        for o in wo.get("oils") or []:
            oli.append({"kode": o.get("oilCode"), "nama": o.get("oilName"),
                        "qty": o.get("oilNum"), "total_cny": o.get("oilAmount")})
        for a in wo.get("additionals") or []:
            tambahan.append({"nama": a.get("additionalName"),
                             "total_cny": a.get("additionalAmount")})
        amounts = [a for a in (wo.get("amountList") or [])
                   if isinstance(a.get("totalAmount"), (int, float))]
        if amounts:
            puncak = max(amounts, key=lambda a: a["totalAmount"])
            biaya = {"part_cny": puncak.get("partAmount"),
                     "admin_part_cny": puncak.get("partMgtAmount"),
                     "jasa_cny": puncak.get("labourAmount"),
                     "oli_cny": puncak.get("oilAmount"),
                     "total_cny": puncak.get("totalAmount"),
                     "mata_uang": puncak.get("currencyCode")}
    return {
        "no_wo": rec.get("roNo"),
        "ro_id": rec.get("roId"),
        "frame": rec.get("vin"),
        "model": rec.get("vmodel"),
        "tanggal": _tgl(rec.get("createTime")),
        "tanggal_audit": _tgl(rec.get("roAuditTime")),
        "km": rec.get("mileage"),
        "gejala": rec.get("faultContent"),
        "tindakan": rec.get("handleMethod"),
        "catatan": rec.get("remark"),
        "status": STATUS_LABEL.get(rec.get("statusCode") or "", rec.get("statusCode")),
        "status_code": rec.get("statusCode"),
        "durasi_jam": rec.get("orderDuration"),
        "pelapor": rec.get("reportName"),
        "mekanik": rec.get("majorRepairName"),
        "bengkel": rec.get("stationName"),
        "part": parts,
        "jasa": jasa,
        "oli": oli,
        "tambahan": tambahan,
        "biaya": biaya,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--maks", type=int, default=0, help="batasi jumlah WO (uji)")
    ap.add_argument("--pekerja", type=int, default=6, help="paralel ambil isi WO")
    ap.add_argument("--segar", action="store_true", help="abaikan checkpoint")
    a = ap.parse_args()

    KELUARAN.parent.mkdir(parents=True, exist_ok=True)
    t0 = time.time()

    print("① daftar klaim…")
    daftar = panen_daftar(a.maks)
    if not daftar:
        print("⛔ daftar klaim KOSONG — berkas lama TIDAK ditimpa.")
        return 1

    simpan: dict[str, dict] = {}
    if CHECKPOINT.exists() and not a.segar:
        simpan = json.loads(CHECKPOINT.read_text(encoding="utf-8"))
        print(f"  checkpoint: {len(simpan)} WO sudah terambil")

    perlu = [r for r in daftar if r.get("roId") and r["roId"] not in simpan]
    print(f"② isi WO: {len(perlu)} perlu diambil ({a.pekerja} paralel)…")
    selesai = 0
    with ThreadPoolExecutor(max_workers=a.pekerja) as ex:
        for rec, wo in zip(perlu, ex.map(lambda r: isi_wo(r["roId"]), perlu)):
            if wo:
                simpan[rec["roId"]] = wo
            selesai += 1
            if selesai % 100 == 0:
                CHECKPOINT.write_text(json.dumps(simpan), encoding="utf-8")
                print(f"  {selesai}/{len(perlu)} ({time.time() - t0:.0f}s)")
    CHECKPOINT.write_text(json.dumps(simpan), encoding="utf-8")

    print("③ kamus mode kegagalan…")
    pn_semua = [p.get("partCode") for wo in simpan.values()
                for p in (wo.get("parts") or [])]
    modes = kamus_mode_gagal(pn_semua)
    pasang = [(r.get("vin"), p.get("partCode")) for r in daftar
              for p in (simpan.get(r.get("roId") or "") or {}).get("parts") or []
              if r.get("vin") and p.get("partCode")]
    # sebar sampel ke seluruh rentang data (bukan 12 baris pertama yang mirip)
    langkah = max(len(pasang) // 12, 1)
    pabrik = kamus_pabrik(pasang[::langkah][:12])
    print(f"  kamus pabrik: {len(pabrik)} kode (dari {len(pasang[::langkah][:12])} sampel)")

    print("④ rakit dataset…")
    rows = [baris(r, simpan.get(r.get("roId") or ""), modes) for r in daftar]
    ber_part = sum(1 for r in rows if r["part"])
    n_part = sum(len(r["part"]) for r in rows)

    # Kode yang muncul di data tapi TIDAK ada di kamus — dicatat terang-terangan
    # supaya tak diam-diam tampil sebagai kode mentah di muka pemakai.
    status_asing = sorted({r["status_code"] for r in rows
                           if r["status_code"] and r["status_code"] not in STATUS_LABEL})
    duty_asing = sorted({p["penanggung_jawab"] for r in rows for p in r["part"]
                         if p["penanggung_jawab"] and p["penanggung_jawab"] not in pabrik})
    if status_asing:
        print("  ⚠️ status BELUM terpetakan:", ", ".join(status_asing))
    if duty_asing:
        print("  ⚠️ dutyCode BELUM terpetakan:", ", ".join(duty_asing))

    payload = {
        "dibuat": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "belum_terpetakan": {"status": status_asing, "penanggung_jawab": duty_asing},
        "sumber": "SIMS DMS repair order (simscloud.cnhtcerp.com:8082)",
        "bengkel": "IDZ005 MAS (Pekanbaru)",
        "total_klaim": len(rows),
        "klaim_ber_part": ber_part,
        "total_baris_part": n_part,
        "kamus_mode_gagal": modes,
        "kamus_penanggung_jawab": pabrik,
        "kamus_status": STATUS_LABEL,
        "klaim": rows,
    }
    with gzip.open(KELUARAN, "wt", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)

    kb = KELUARAN.stat().st_size / 1024
    print(f"\n✅ {KELUARAN.relative_to(ROOT)} — {kb:.0f} KB")
    print(f"   {len(rows)} klaim, {ber_part} ber-part, {n_part} baris part, "
          f"{len(modes)} kode mode gagal ({time.time() - t0:.0f}s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
