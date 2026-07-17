# -*- coding: utf-8 -*-
"""SATU pintu regenerasi SEMUA dataset pengetahuan asisten (rombakan K4).

Menjalankan builder berurutan — urutan deterministik, ai_knowledge SELALU
terakhir (ia merangkum cakupan dataset lain):
  1. build_fault_codes   (PDF Bosch → fault_codes.json)          [butuh pdfplumber]
  2. build_abs_scr       (2 xlsx → abs_scr_codes.json.gz)
  3. build_fault_cards   (216 PDF → dtc_diagnosa.json.gz)        [butuh pdfplumber]
  4. build_dtc_store     (union → dtc_codes.json.gz)
  5. build_maintenance   (4 xlsx → jadwal_perawatan.json.gz)
  6. build_filter_ref    (xlsx → filter_shantui.json.gz)
  7. build_repairkit     (validasi transmisi.json)
  8. build_catalog_bom   (katalog xlsx → catalog_bom.json)       [--with-bom saja]
  9. build_ai_knowledge  (rangkum semua → ai_knowledge.json)

Pakai --only <nama>[,<nama>] untuk sebagian (ai_knowledge tetap ikut terakhir).
Sumber mentah tak berubah → output byte-identik (aman dijalankan kapan pun).
Jalankan dari root repo:  python backend/tools/build_all.py [--with-bom] [--only ...]
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parent

_URUTAN = [
    "build_fault_codes", "build_abs_scr", "build_fault_cards", "build_dtc_store",
    "build_maintenance", "build_filter_ref", "build_repairkit", "build_pin_ecu",
]
_OPSIONAL = ["build_catalog_bom"]
_TERAKHIR = "build_ai_knowledge"


def _run(name: str) -> bool:
    p = TOOLS / f"{name}.py"
    print(f"\n── {name} ──")
    r = subprocess.run([sys.executable, str(p)], cwd=TOOLS.parents[1])
    if r.returncode != 0:
        print(f"⛔ {name} GAGAL (exit {r.returncode})")
        return False
    return True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--with-bom", action="store_true",
                    help="ikutkan build_catalog_bom (lama; hanya bila katalog xlsx berubah)")
    ap.add_argument("--only", default="",
                    help="daftar builder dipisah koma (ai_knowledge selalu ikut terakhir)")
    args = ap.parse_args()

    urutan = list(_URUTAN) + (_OPSIONAL if args.with_bom else [])
    if args.only:
        pilih = {x.strip() for x in args.only.split(",") if x.strip()}
        urutan = [n for n in urutan if n in pilih]

    gagal = [n for n in urutan if not _run(n)]
    # ai_knowledge terakhir — tetap jalan walau ada yang gagal (best-effort per
    # sumber), tapi status akhir mengikuti kegagalan.
    if not _run(_TERAKHIR):
        gagal.append(_TERAKHIR)
    if gagal:
        print(f"\n⛔ selesai DENGAN KEGAGALAN: {', '.join(gagal)}")
        return 1
    print("\n✅ semua builder selesai.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
