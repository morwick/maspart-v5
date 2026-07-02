# -*- coding: utf-8 -*-
"""
Rekonsiliasi AKURASI katalog lokal vs EPC (READ-ONLY — tidak mengubah data).

Untuk sampel unit di populasi: ambil BOM nyata dari EPC (per-VIN) lalu bandingkan
HIMPUNAN PN-nya dengan BOM katalog lokal untuk unit itu (dipetakan via MODEL &
TIPE UNIT). Menjawab: seberapa akurat katalog kita, PN apa yang KURANG (unit
nyata punya, katalog tidak) atau LEBIH (katalog punya, unit nyata tidak).

PN netral bahasa → perbandingan adil walau nama EPC=China, katalog=Inggris.

Hasil: ringkasan di layar + 2 CSV (per-unit & PN-kurang-tersering per model).
TIDAK menulis/mengubah catalog_bom. Jalankan di server (Supabase + token EPC):

    python backend/tools/reconcile_catalog_epc.py --limit 150     # PILOT
    python backend/tools/reconcile_catalog_epc.py --model "NX" --limit 80

Opsi: --limit N(=150)  --vin-col  --model-col  --tipe-col  --model SUBSTR  --workers N(=6)
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
import threading
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # backend/

from app.services import catalog_bom, epc_bom, part_index, populasi  # noqa: E402

_VIN_PAT = re.compile(r"(rangka|chassis|chasis|\bvin\b|frame)", re.IGNORECASE)


def _norm(s) -> str:
    return re.sub(r"[\s_\-/]", "", str(s or "").upper())


def _pick_col(df, override, candidates, pat=None):
    cols = [str(c) for c in df.columns]
    if override:
        if override in cols:
            return override
        sys.exit(f"Kolom '{override}' tak ada. Kolom: {cols}")
    for c in candidates:
        if c in cols:
            return c
    if pat:
        hit = [c for c in cols if pat.search(c)]
        if hit:
            return hit[0]
    return None


def _map_unit(model: str, tipe: str) -> tuple[list[str], str]:
    """Petakan (model, tipe) → unit katalog. Coba model, tipe, gabungan.
    Return (kandidat_unik, status)."""
    cands: list[str] = []
    for q in (model, tipe, f"{model} {tipe}".strip()):
        if q and q.strip():
            for u in catalog_bom.resolve_unit(q):
                if u not in cands:
                    cands.append(u)
    if not cands:
        return [], "unmapped"
    if len(cands) == 1:
        return cands, "mapped"
    return cands, "ambiguous"


def main() -> int:
    ap = argparse.ArgumentParser(description="Rekonsiliasi katalog lokal vs EPC (read-only).")
    ap.add_argument("--limit", type=int, default=150)
    ap.add_argument("--vin-col")
    ap.add_argument("--model-col")
    ap.add_argument("--tipe-col")
    ap.add_argument("--model", help="Saring populasi ke unit yang MODEL/TIPE-nya memuat teks ini.")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--out", default="reconcile")
    args = ap.parse_args()

    if not epc_bom.available():
        sys.exit("Token EPC tak ada (data/epc_token.txt). Isi token dulu.")
    if not catalog_bom.available():
        sys.exit("catalog_bom.json belum ada / kosong.")

    print("Memuat populasi & katalog ...")
    df = populasi.refresh()
    if df is None or df.empty:
        sys.exit("Populasi kosong (cek Supabase / data/populasi/).")
    vin_col = _pick_col(df, args.vin_col, [], _VIN_PAT)
    model_col = _pick_col(df, args.model_col, ["MODEL", "TIPE", "JENIS"])
    tipe_col = _pick_col(df, args.tipe_col, ["TIPE UNIT", "TIPE", "JENIS"])
    if not vin_col:
        sys.exit(f"Kolom VIN tak ketemu. Kolom: {[str(c) for c in df.columns]}")
    print(f"Kolom → VIN:'{vin_col}'  MODEL:'{model_col}'  TIPE:'{tipe_col}'")

    # Susun daftar unit unik per frame.
    units, seen = [], set()
    for _, row in df.iterrows():
        vn = _norm(row.get(vin_col, ""))
        if len(vn) < 6:
            continue
        frame = vn[-8:] if len(vn) >= 11 else vn
        if frame in seen:
            continue
        model = str(row.get(model_col, "")).strip() if model_col else ""
        tipe = str(row.get(tipe_col, "")).strip() if tipe_col else ""
        if args.model and args.model.upper() not in (model + " " + tipe).upper():
            continue
        seen.add(frame)
        units.append({"vin": vn, "frame": frame, "model": model, "tipe": tipe})
        if args.limit and len(units) >= args.limit:
            break
    print(f"Unit sampel: {len(units)}")
    if not units:
        sys.exit("Tak ada unit cocok kriteria.")

    # Ambil BOM EPC paralel.
    epc_sets: dict[str, set] = {}
    stop = threading.Event()
    lock = threading.Lock()
    done = 0

    def fetch(u):
        if stop.is_set():
            return u["frame"], None
        r = epc_bom.loading_list(u["vin"])
        if not r.get("found"):
            if r.get("_err") in ("token_expired", "no_token"):
                stop.set()
            return u["frame"], None
        return u["frame"], {_norm(p["pn"]) for p in r.get("parts", [])}

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = [ex.submit(fetch, u) for u in units]
        for fut in as_completed(futs):
            fr, s = fut.result()
            if s is not None:
                epc_sets[fr] = s
            with lock:
                done += 1
                if done % 25 == 0 or done == len(units):
                    print(f"  EPC {done}/{len(units)}  (ditemukan {len(epc_sets)})")
    if stop.is_set():
        print("\n⚠ TOKEN EPC KEDALUWARSA — refresh data/epc_token.txt lalu ulangi.")

    # Bandingkan per unit.
    rows = []
    miss_by_model: dict[str, Counter] = defaultdict(Counter)  # model -> PN kurang : frekuensi
    acc_by_model: dict[str, list] = defaultdict(list)
    stat = Counter()
    all_missing: set = set()
    for u in units:
        E = epc_sets.get(u["frame"])
        if E is None:
            stat["epc_tak_ada"] += 1
            continue
        stat["epc_ada"] += 1
        cands, mstat = _map_unit(u["model"], u["tipe"])
        stat[mstat] += 1
        cat_unit = cands[0] if mstat == "mapped" else ""
        C = set(catalog_bom.unit_parts(cat_unit).keys()) if cat_unit else set()
        overlap = E & C
        only_e = E - C  # katalog KURANG
        only_c = C - E  # katalog LEBIH
        acc = round(len(overlap) / len(E) * 100, 1) if E else 0.0
        if cat_unit:
            acc_by_model[u["model"]].append(acc)
            for pn in only_e:
                miss_by_model[u["model"]][pn] += 1
            all_missing |= only_e
        rows.append({
            "vin": u["vin"], "model": u["model"], "tipe": u["tipe"],
            "catalog_unit": cat_unit, "map_status": mstat,
            "epc_count": len(E), "cat_count": len(C),
            "overlap": len(overlap), "kurang_di_katalog": len(only_e),
            "lebih_di_katalog": len(only_c), "akurasi_pct": acc,
        })

    # PN yang KURANG: ada di tempat lain di data kita, atau benar-benar tak ada?
    known = set()
    for r in part_index.search_exact_pns(list(all_missing)):
        known.add(_norm(r.get("part_number")))
    pncat = catalog_bom.pn_category_map()

    # ── Ringkasan ──
    mapped_acc = [a for lst in acc_by_model.values() for a in lst]
    avg = round(sum(mapped_acc) / len(mapped_acc), 1) if mapped_acc else 0.0
    print("\n================ RINGKASAN REKONSILIASI ================")
    print(f"Unit sampel              : {len(units)}")
    print(f"  BOM EPC ditemukan      : {stat['epc_ada']}   (tak ada: {stat['epc_tak_ada']})")
    print(f"  ter-map ke katalog     : {stat['mapped']}  | ambigu: {stat['ambiguous']}  | tak ter-map: {stat['unmapped']}")
    print(f"AKURASI katalog rata2    : {avg}%   (overlap PN / PN nyata EPC, unit ter-map)")
    print(f"PN unik 'kurang'         : {len(all_missing)}   (di EPC, tak di katalog unit itu)")
    print(f"  dari itu ADA di data kita (unit lain): {len(known)}  | benar2 tak ada: {len(all_missing - known)}")
    print("=======================================================")

    # Akurasi per model (terendah dulu = paling perlu perhatian).
    print("\nAkurasi per MODEL (terendah dulu, maks 15):")
    per_model = sorted(((m, round(sum(v)/len(v), 1), len(v)) for m, v in acc_by_model.items()),
                       key=lambda x: x[1])
    for m, a, n in per_model[:15]:
        print(f"  {a:5.1f}%  {m!r}  ({n} unit)")

    # CSV per-unit.
    out1 = Path(f"{args.out}_per_unit.csv")
    with out1.open("w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()) if rows else
                           ["vin", "model", "tipe", "catalog_unit", "map_status",
                            "epc_count", "cat_count", "overlap", "kurang_di_katalog",
                            "lebih_di_katalog", "akurasi_pct"])
        w.writeheader()
        w.writerows(rows)

    # CSV PN-kurang tersering per model (kandidat ditambahkan ke katalog).
    out2 = Path(f"{args.out}_missing_pn.csv")
    with out2.open("w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(["model", "pn", "muncul_di_n_unit", "ada_di_data_kita", "nama", "kategori"])
        for model, cnt in miss_by_model.items():
            n_units = len(acc_by_model[model]) or 1
            for pn, freq in cnt.most_common():
                info = pncat.get(pn, {})
                w.writerow([model, pn, freq, int(pn in known),
                            info.get("nama", ""),
                            catalog_bom.kategori_nama(info.get("kategori", "")) if info else ""])
    print(f"\nCSV -> {out1.resolve()}")
    print(f"CSV -> {out2.resolve()}  (PN kurang tersering = kandidat dilengkapi ke katalog)")
    print("\nCatatan: READ-ONLY. Tinjau CSV sebelum mengubah katalog. 'kurang' yang muncul di "
          "BANYAK unit satu model = paling layak ditambahkan.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
