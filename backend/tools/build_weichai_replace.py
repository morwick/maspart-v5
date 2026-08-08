# -*- coding: utf-8 -*-
"""
Panen TABEL PENGGANTIAN (supersession) part MESIN Weichai — SELURUHNYA, sekali,
ke file lokal supaya lookup pengganti PN mesin jadi INSTAN & OFFLINE.

Latar: tool `pengganti_part` memanggil EPC Weichai `replace/page` LIVE per-PN.
Di log produksi ia nihil 42×, dan tiap panggilan menambah detik ke giliran chat.
Endpoint yang sama ternyata mau melayani permintaan TANPA `partNumber` — yaitu
SELURUH tabel: `total` = 58.100 record (terverifikasi live 2026-08-08).

Bentuk record (apa adanya dari API):
    {oldPartNumber, newPartNumber, replaceGroup, replaceGroupType, replaceType,
     replacementDate, applyitem, remark, dataType}
⚠️ `oldPartNumber`/`newPartNumber` BISA berisi BANYAK PN dipisah koma (relasi
grup many-to-many) — jangan diperlakukan sebagai PN tunggal.

Output: data/weichai_replace.json.gz  (dibaca services/weichai_replace.py)
        {versi, diambil, total_api, halaman, record:[...]}

Sifat:
  • RESUMABLE — checkpoint per halaman ke <out>.part; ulangi perintah yang sama
    untuk melanjutkan setelah putus. Tanpa ini, gagal di halaman 250 = ulang dari 0.
  • SOPAN — jeda antar halaman (default 2,5 dtk) agar tak terbaca serangan.
  • JUJUR — halaman yang gagal setelah retry DICATAT di 'halaman_gagal'; file hasil
    menyandang penanda 'lengkap: false'. ⛔ Jangan pernah menyajikan panen separuh
    sebagai tabel penuh.

Jalankan dari root repo:
    py backend/tools/build_weichai_replace.py                 # panen penuh
    py backend/tools/build_weichai_replace.py --max-halaman 3 # uji cepat
    py backend/tools/build_weichai_replace.py --jeda 3.0
"""
from __future__ import annotations

import argparse
import gzip
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.services import epc_weichai as wc  # noqa: E402

OUT = ROOT / "data" / "weichai_replace.json.gz"
# 500/halaman terukur 8,3 dtk — latensi hampir sama dengan 200/halaman, jadi
# 2,5× lebih hemat waktu total (117 halaman, bukan 291).
PAGE_SIZE = 500
JEDA_DEFAULT = 2.5       # detik antar halaman
RETRY = 3
# Halaman KOSONG dari endpoint ini TIDAK berarti data habis: pada panen
# 2026-08-08 halaman 112 membalas list kosong TANPA error, lalu halaman yang
# sama membalas 200 record beberapa menit kemudian. Menganggapnya 'selesai'
# menghentikan panen di 38% dan melaporkannya sukses (exit 0). Karena itu:
# kosong = anomali yang DIULANG, dan akhir panen ditentukan oleh `total` API.
KOSONG_ULANG = 3


def _kunci(rec: dict) -> str:
    """Identitas record untuk dedup lintas halaman."""
    return "|".join(str(rec.get(k) or "") for k in
                    ("oldPartNumber", "newPartNumber", "replaceGroup",
                     "replacementDate", "replaceType"))


def _ambil_halaman(token: str, page: int, size: int) -> tuple[list, int, str]:
    """(records, total, err). err='' bila sukses."""
    r = wc._get(wc._REPLACE_URL,
                {"pageNo": page, "pageSize": size, "keyword": "",
                 "partNumber": "", "dhhNumber": ""}, token)
    if "_err" in r:
        return [], 0, str(r.get("_err") or "api")
    d = r.get("data") or {}
    lst = d.get("list") if isinstance(d, dict) else d
    try:
        total = int((d or {}).get("total") or 0)
    except (TypeError, ValueError):
        total = 0
    return (lst if isinstance(lst, list) else []), total, ""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--jeda", type=float, default=JEDA_DEFAULT, help="detik antar halaman")
    ap.add_argument("--max-halaman", type=int, default=0, help="0 = semua")
    ap.add_argument("--page-size", type=int, default=PAGE_SIZE, help="record per halaman")
    ap.add_argument("--mulai-ulang", action="store_true", help="abaikan checkpoint")
    a = ap.parse_args()
    size = max(1, min(int(a.page_size), 1000))

    ck = OUT.with_suffix(".part")
    rec: list[dict] = []
    mulai = 1
    gagal: list[int] = []
    if ck.exists() and not a.mulai_ulang:
        try:
            st = json.loads(ck.read_text(encoding="utf-8"))
            rec = st.get("record") or []
            gagal = st.get("halaman_gagal") or []
            # Checkpoint LAMA tak menyimpan page_size — ia selalu ditulis dengan
            # 200. Menganggapnya 'tak diketahui' lalu melanjutkan penomoran apa
            # adanya membuat halaman ke-N dibaca pada ukuran berbeda → puluhan
            # ribu baris terlewat DIAM-DIAM (terjadi 2026-08-08).
            size_lama = int(st.get("page_size") or 200)
            if size_lama != size:
                # Nomor halaman TERIKAT ukuran halaman: melanjutkan nomor lama
                # dengan ukuran baru akan melompati baris. Record lama tetap
                # dipakai sbg benih (dedup by konten), penomoran mulai dari 1.
                mulai = 1
                print(f"[lanjut] page_size berubah {size_lama}→{size}: "
                      f"{len(rec)} record lama dipakai sbg benih, halaman mulai dari 1")
            else:
                mulai = int(st.get("halaman_berikut") or 1)
                print(f"[lanjut] checkpoint: {len(rec)} record, mulai halaman {mulai}")
        except Exception as e:
            print(f"[lanjut] checkpoint rusak ({e}) — mulai dari awal")

    token = wc._ensure_token()
    if not token:
        print("GAGAL: sesi EPC Weichai tak aktif (token tak bisa di-mint).")
        return 2

    seen = {_kunci(x) for x in rec}
    total_api = 0
    hal_akhir = 0            # dihitung dari `total` API begitu halaman pertama tiba
    page = mulai
    t0 = time.time()
    while True:
        if a.max_halaman and page > (mulai - 1) + a.max_halaman:
            break
        if hal_akhir and page > hal_akhir:
            break
        lst, total, err = [], 0, ""
        kosong = 0
        for percobaan in range(1, RETRY + 1):
            lst, total, err = _ambil_halaman(token, page, size)
            if not err and lst:
                break
            if err:
                print(f"  ! halaman {page} gagal ({err}), percobaan {percobaan}/{RETRY}")
            else:
                # Kosong TANPA error: anomali sesaat (lihat KOSONG_ULANG), bukan
                # tanda data habis — akhir panen ditentukan `total`, bukan ini.
                kosong += 1
                print(f"  ? halaman {page} KOSONG tanpa error, percobaan {percobaan}/{RETRY}")
            time.sleep(a.jeda * percobaan)
            if percobaan == 2:                      # token mungkin basi
                token = wc._ensure_token() or token
        if err or (not lst and kosong >= KOSONG_ULANG):
            gagal.append(page)
            print(f"  x halaman {page} DILEWATI ({'error' if err else 'kosong berulang'})")

        total_api = total or total_api
        if total_api and not hal_akhir:
            hal_akhir = (total_api + size - 1) // size
        baru = 0
        for x in lst:
            k = _kunci(x)
            if k not in seen:
                seen.add(k)
                rec.append(x)
                baru += 1
        print(f"  halaman {page}/{hal_akhir or '?'}: +{baru} "
              f"(total {len(rec)}/{total_api or '?'}) [{time.time()-t0:.0f}s]")

        # Checkpoint tiap halaman — panen berjam-jam tak boleh hangus karena 1 putus.
        try:
            ck.write_text(json.dumps({"record": rec, "halaman_berikut": page + 1,
                                      "halaman_gagal": gagal, "total_api": total_api,
                                      "page_size": size}, ensure_ascii=False),
                          encoding="utf-8")
        except Exception as e:
            print(f"  ! checkpoint gagal ditulis: {e}")

        if total_api and len(rec) >= total_api:
            break
        page += 1
        time.sleep(a.jeda)

    # 'lengkap' hanya bila SELURUH halaman tersapu tanpa yang dilewati. Panen
    # separuh yang mengaku lengkap adalah kegagalan paling mahal di sini: ia
    # membuat pengganti_part berhenti bertanya ke sumber live.
    lengkap = bool(total_api) and len(rec) >= total_api and not gagal
    OUT.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "versi": 1,
        "diambil": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "total_api": total_api,
        "halaman_gagal": gagal,
        "lengkap": lengkap,
        "record": rec,
    }
    with gzip.open(OUT, "wt", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
    print(f"\nTULIS {OUT}  ({len(rec)} record, total_api={total_api}, "
          f"lengkap={lengkap}, gagal={len(gagal)} halaman, {time.time()-t0:.0f}s)")
    if lengkap and ck.exists():
        try:
            ck.unlink()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
