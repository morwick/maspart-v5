# -*- coding: utf-8 -*-
"""Validator + normalizer REPAIR KIT TRANSMISI (data/repairkit/transmisi.json).

Catatan provenance (rombakan 2026-07-17): transmisi.json = KURASI resmi
(disusun dari sheet '05变速箱 Gearbox' tiap unit katalog). File
transmisi_repairkit.xlsx di sebelahnya adalah HASIL EXPORT
(repairkit.to_excel_bytes()) — BUKAN sumber; membangun JSON dari xlsx itu
sirkular. Maka "builder" repairkit = pemeriksa integritas build-time:
kesalahan kurasi/korupsi ketahuan di sini (fail-loud), bukan diam-diam di
produksi. Service repairkit.py tetap membaca transmisi.json (hot-editable).

Validasi:
  - tiap model punya: tipe, unit[], assy_pn[], seal_kit, overhaul_tambahan;
  - jumlah_seal_kit / jumlah_overhaul_tambahan == jumlah item aktual;
  - kategori seal_kit ⊆ {oil_seal, gasket, o_ring};
    kategori overhaul ⊆ {bearing, synchronizer, snap_ring};
  - tiap item punya pn non-kosong; PN duplikat dalam satu kategori ditolak.
Mode --write menormalkan format file (indent 1, kunci model terurut) —
deterministik, memudahkan diff kurasi.

Jalankan dari root repo:  python backend/tools/build_repairkit.py [--write]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "data" / "repairkit" / "transmisi.json"

_SEAL_CATS = {"oil_seal", "gasket", "o_ring"}
_OVER_CATS = {"bearing", "synchronizer", "snap_ring"}


def validate(data: dict) -> list[str]:
    err: list[str] = []
    if not isinstance(data, dict) or not data:
        return ["file kosong / bukan objek model"]
    for model, v in data.items():
        pre = f"[{model}]"
        if not v.get("tipe"):
            err.append(f"{pre} tanpa 'tipe'")
        if not v.get("unit"):
            err.append(f"{pre} tanpa 'unit'")
        if not v.get("assy_pn"):
            err.append(f"{pre} tanpa 'assy_pn'")
        for grup, cats, cnt_key in (("seal_kit", _SEAL_CATS, "jumlah_seal_kit"),
                                    ("overhaul_tambahan", _OVER_CATS,
                                     "jumlah_overhaul_tambahan")):
            g = v.get(grup) or {}
            luar = set(g) - cats
            if luar:
                err.append(f"{pre} kategori {grup} tak dikenal: {sorted(luar)}")
            n = 0
            for cat, items in g.items():
                seen: set[str] = set()
                for it in items or []:
                    pn = (it.get("pn") or "").strip()
                    if not pn:
                        err.append(f"{pre} {grup}.{cat}: item tanpa pn")
                        continue
                    if pn in seen:
                        err.append(f"{pre} {grup}.{cat}: PN duplikat {pn}")
                    seen.add(pn)
                    n += 1
            stated = v.get(cnt_key)
            if stated is not None and int(stated) != n:
                err.append(f"{pre} {cnt_key}={stated} ≠ item aktual {n}")
    return err


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true",
                    help="tulis ulang file dgn format normal (deterministik)")
    args = ap.parse_args()

    if not SRC.exists():
        raise SystemExit(f"⛔ {SRC} tidak ada")
    data = json.loads(SRC.read_text(encoding="utf-8"))
    err = validate(data)
    if err:
        for e in err:
            print("⛔", e)
        raise SystemExit(f"⛔ {len(err)} masalah integritas di {SRC.name}")

    n_model = len(data)
    n_item = sum(
        len(items or [])
        for v in data.values()
        for grup in ("seal_kit", "overhaul_tambahan")
        for items in (v.get(grup) or {}).values()
    )
    if args.write:
        normal = json.dumps({k: data[k] for k in sorted(data)},
                            ensure_ascii=False, indent=1)
        SRC.write_text(normal + "\n", encoding="utf-8")
        print(f"✍️  dinormalkan: {SRC}")
    print(f"✅ {SRC.name} VALID: {n_model} model gearbox, {n_item} item kit.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
