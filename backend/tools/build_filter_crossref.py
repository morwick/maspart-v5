# -*- coding: utf-8 -*-
"""
Panen PADANAN AFTERMARKET filter (PN OEM → PN merek lain) dari
oilfilter-crossreference.com → data/filter_crossref.json.gz

⚠️⚠️ MUTU & STATUS SUMBER — BACA SEBELUM MEMPERCAYAI DATANYA
Ini sumber PIHAK KETIGA anonim: tanpa halaman lisensi data, operator tak
teridentifikasi (hanya email), berpendapatan afiliasi. robots.txt-nya memang
mengizinkan (`Disallow:` kosong), tapi TIDAK ada pernyataan lisensi data.
Karena itu hasilnya WAJIB disajikan sebagai **referensi/saran**, ⛔ BUKAN
"padanan resmi pabrik", dan pemakai tetap harus mencocokkan fisik/spesifikasi.

Pengukuran jujur sebelum dibangun (sampel acak 25 PN elemen filter kita):
  • CAKUPAN NYATA hanya **8%** (2/25). Sebagian besar PN filter kita TIDAK ada.
  • Satu dari dua hit memberi **307** "padanan" lintas merek kompresor udara —
    entri GENERIK yang menyesatkan. Karena itu hasil >_AMBANG_GENERIK ditandai
    `generik: true` dan pemanggil WAJIB memperlakukannya sebagai sinyal lemah.

⛔ JEBAKAN yang sudah memakan korban saat riset (jangan diulang):
  1. `fuelfilter-crossreference.com` dirender JAVASCRIPT: ia membalas HTTP 200
     untuk merek DAN part number yang DIKARANG sekalipun, dan "padanan" yang
     terlihat di HTML sebenarnya template JS (`' + results[i].brand + '`).
     Pengukuran pertama sempat melaporkan CAKUPAN 100% gara-gara ini.
     → HANYA domain oli dipakai, dan blok <script> DIBUANG sebelum diurai.
  2. Tak ada endpoint pencarian generik (`/search`, `/suggest` cuma memuat
     beranda) → nama MEREK harus ditebak lewat alias per bentuk PN.
  3. 404 = PN memang tak ada di basis mereka (sinyal bersih, bukan error).

Sopan & tahan putus: jeda antar permintaan, checkpoint per-PN (resumable),
dan menolak menimpa berkas lama bila hasilnya nihil total.

Jalankan dari root repo:
    py backend/tools/build_filter_crossref.py                 # semua PN filter
    py backend/tools/build_filter_crossref.py --maks 40       # uji cepat
    py backend/tools/build_filter_crossref.py --jeda 2.0
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

import requests

for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.services import part_index  # noqa: E402
from app.services.knowledge_util import write_json_gz  # noqa: E402

OUT = ROOT / "data" / "filter_crossref.json.gz"
CK = OUT.with_suffix(".part")
BASE = "https://www.oilfilter-crossreference.com"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"}
SUMBER = "oilfilter-crossreference.com (pihak ketiga, tanpa lisensi data eksplisit)"
_AMBANG_GENERIK = 60      # di atas ini = entri catch-all, sinyal lemah

# Nama part yang MENGANDUNG kata filter tapi BUKAN elemen filter — dibuang
# supaya kita tak menembaki situs orang untuk paking/braket/pipa.
_BUKAN_ELEMEN = re.compile(
    r"seat|shell|cover|bracket|housing|cap|base|head|gasket|plug|pipe|hose|"
    r"indicator|mounting|body|elbow|switch|wiring|harness|示意图|滤座|滤壳", re.I)
_FILTER = re.compile(r"filter|滤", re.I)


def daftar_pn() -> dict[str, str]:
    """{PN: nama} elemen filter dari katalog lokal."""
    part_index.ensure_index()
    out: dict[str, str] = {}
    for fi in part_index._state["excel_files"]:
        df = fi.get("dataframe")
        if df is None or "part_name" not in getattr(df, "columns", []):
            continue
        for p, n in zip(df["part_number"].tolist(), df["part_name"].tolist()):
            nama = part_index._sel_teks(n)
            pn = part_index._sel_teks(p).upper()
            if pn and _FILTER.search(nama) and not _BUKAN_ELEMEN.search(nama):
                out.setdefault(pn, nama)
    return out


def alias_merek(pn: str) -> list[str]:
    """Nama merek di URL situs — harus ditebak (tak ada pencarian generik)."""
    if pn.isdigit():
        return ["WEICHAI-POWER", "WEICHAI"]
    if re.match(r"^(WG|VG|AZ|YZ|EZ|LG|FG|YG|WD)", pn):
        return ["SINOTRUK", "HOWO"]
    return ["SINOTRUK", "WEICHAI-POWER"]


def ambil(brand: str, pn: str) -> tuple[list[dict] | None, str]:
    """(padanan, status). None = tak ada/404. status: ok|404|err."""
    try:
        r = requests.get(f"{BASE}/convert/{brand}/{pn}", headers=UA, timeout=30)
    except Exception as e:              # jaringan — BUKAN bukti PN tak ada
        return None, f"err:{type(e).__name__}"
    if r.status_code == 404:
        return None, "404"
    if r.status_code != 200:
        return None, f"err:{r.status_code}"
    # ⛔ WAJIB buang <script>: template JS di dalamnya pernah terbaca sbg padanan.
    html = re.sub(r"<script.*?</script>", " ", r.text, flags=re.S | re.I)
    pasang = []
    lihat = set()
    for m, p in re.findall(r'href="/convert/([^/"]+)/([^"]+)"', html):
        m, p = m.strip(), p.strip()
        if (m == brand and p == pn) or "+" in m or "+" in p or " " in m:
            continue
        if (m, p) in lihat:
            continue
        lihat.add((m, p))
        pasang.append({"merek": m.replace("-", " "), "pn": p})
    return (pasang or None), "ok"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--jeda", type=float, default=1.5, help="detik antar permintaan")
    ap.add_argument("--maks", type=int, default=0, help="0 = semua PN")
    ap.add_argument("--mulai-ulang", action="store_true")
    a = ap.parse_args()

    semua = daftar_pn()
    pns = sorted(semua)
    if a.maks:
        pns = pns[:a.maks]
    print(f"[daftar] {len(semua)} PN elemen filter di katalog; diproses: {len(pns)}")

    hasil: dict[str, dict] = {}
    selesai: set = set()
    if CK.exists() and not a.mulai_ulang:
        try:
            st = json.loads(CK.read_text(encoding="utf-8"))
            hasil = st.get("hasil") or {}
            selesai = set(st.get("selesai") or [])
            print(f"[lanjut] checkpoint: {len(selesai)} PN sudah dicek, "
                  f"{len(hasil)} berpadanan")
        except Exception as e:
            print(f"[lanjut] checkpoint rusak ({e}) — mulai dari awal")

    t0 = time.time()
    gagal_jaringan = 0
    for i, pn in enumerate(pns, 1):
        if pn in selesai:
            continue
        found = None
        for b in alias_merek(pn):
            pasang, status = ambil(b, pn)
            time.sleep(a.jeda)
            if status.startswith("err"):
                gagal_jaringan += 1
            if pasang:
                found = {"merek_sumber": b.replace("-", " "), "nama": semua[pn],
                         "padanan": pasang, "jumlah": len(pasang)}
                if len(pasang) > _AMBANG_GENERIK:
                    # Entri catch-all: 307 "padanan" lintas merek kompresor udara
                    # pernah muncul utk satu PN "Filter Element" generik.
                    found["generik"] = True
                break
        selesai.add(pn)
        if found:
            hasil[pn] = found
            tanda = " ⚠️generik" if found.get("generik") else ""
            print(f"  {i:4d}/{len(pns)} {pn:22s} ✅ {found['jumlah']:3d} padanan{tanda}")
        elif i % 25 == 0:
            print(f"  {i:4d}/{len(pns)} … {len(hasil)} berpadanan "
                  f"[{time.time()-t0:.0f}s]")
        try:
            CK.write_text(json.dumps({"hasil": hasil, "selesai": sorted(selesai)},
                                     ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass

    if not hasil:
        print("⛔ NIHIL TOTAL — berkas lama TIDAK ditimpa (bisa jadi situs berubah/"
              "jaringan bermasalah, bukan bukti tak ada padanan).")
        return 2

    payload = {
        "sumber": SUMBER,
        "url": BASE,
        "diambil": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "pn_dicek": len(selesai),
        "pn_berpadanan": len(hasil),
        "ambang_generik": _AMBANG_GENERIK,
        "peringatan": ("Referensi PIHAK KETIGA, bukan padanan resmi pabrik. "
                       "Cakupan rendah (~8% PN filter). Entri 'generik' = sinyal "
                       "lemah. Wajib dicocokkan fisik/spesifikasi sebelum dipakai."),
        "data": hasil,
    }
    write_json_gz(OUT, payload)
    print(f"\nTULIS {OUT}")
    print(f"  PN dicek: {len(selesai)} | berpadanan: {len(hasil)} "
          f"({100*len(hasil)/max(1,len(selesai)):.0f}%) | gagal jaringan: {gagal_jaringan}"
          f" | {time.time()-t0:.0f}s")
    if CK.exists():
        try:
            CK.unlink()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
