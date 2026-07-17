# -*- coding: utf-8 -*-
"""Bangun store kanonik FILTER SHANTUI dari data/manuals/PART FILTER SHANTUI.xlsx
(4 sheet jenis alat; blok HYDRAULIC kiri / ENGINE kanan; kolom cross-reference
merek dinamis). Parser DIANGKAT apa adanya dari services/filter_ref.py — sejak
rombakan 2026-07-17 pensiun dari runtime (fail-loud saat build).

Output: backend/app/services/filter_shantui.json.gz — baris:
  {alat, jenis, model, part_name, part_number, cross_reference[]}
i18n opsional: backend/tools/i18n/filter_i18n.json (EN→ID utk part_name;
fallback asli aman). --dump-strings utk mengisi.

⚠️ Ganti Excel = jalankan builder ini + commit + deploy (bukan hot-reload).
Jalankan dari root repo:  python backend/tools/build_filter_ref.py [--dump-strings]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.knowledge_util import apply_i18n, dump_strings, write_json_gz  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "data" / "manuals" / "PART FILTER SHANTUI.xlsx"
OUT = Path(__file__).resolve().parents[1] / "app" / "services" / "filter_shantui.json.gz"
I18N = Path(__file__).resolve().parent / "i18n" / "filter_i18n.json"

_TR_FIELDS = ("part_name",)


def _cell(df, r: int, c: int) -> str:
    import pandas as pd
    try:
        v = df.iat[r, c]
        return "" if pd.isna(v) else str(v).strip()
    except Exception:
        return ""


def parse(path: Path) -> list[dict]:
    """(diangkat verbatim dari filter_ref._parse pra-rombakan)"""
    import pandas as pd

    xls = pd.ExcelFile(path, engine="openpyxl")
    rows: list[dict] = []
    for sheet in xls.sheet_names:
        df = pd.read_excel(xls, sheet_name=sheet, header=None, dtype=str)
        nrow, ncol = df.shape
        eng_col = next((c for c in range(ncol) if "ENGINE" in _cell(df, 0, c).upper()), None)
        blocks = [("hydraulic", 0, eng_col if eng_col is not None else ncol)]
        if eng_col is not None:
            blocks.append(("engine", eng_col, ncol))
        for ftype, c0, c1 in blocks:
            current_model = ""
            name_c = pn_c = None
            cross_cs: list[int] = []
            mode = None
            for r in range(nrow):
                first = _cell(df, r, c0)
                up = first.upper()
                if up in ("HYDRAULIC FILTER", "ENGINE FILTER", ""):
                    continue
                if up == "NO":
                    name_c, pn_c = c0 + 1, c0 + 2
                    cross_cs = [
                        c for c in range(c0 + 3, c1)
                        if any(_cell(df, rr, c) for rr in range(r + 1, min(r + 12, nrow)))
                    ]
                    mode = "data"
                    continue
                if re.fullmatch(r"\d+", first):
                    if mode != "data":
                        continue
                    name = _cell(df, r, name_c)
                    pn = _cell(df, r, pn_c)
                    if not (name or pn):
                        continue
                    cross = [_cell(df, r, c) for c in cross_cs if _cell(df, r, c)]
                    rows.append({
                        "alat": sheet, "jenis": ftype, "model": current_model,
                        "part_name": name, "part_number": pn,
                        "cross_reference": cross,
                    })
                elif up not in ("PART NAME", "PART NUMBER"):
                    current_model = first
                    mode = "expect"
    return rows


def build_rows(src: Path | None = None) -> list[dict]:
    p = src or SRC
    if not p.is_file():
        raise SystemExit(f"⛔ sumber tak ada: {p}")
    rows = parse(p)
    if not rows:
        raise SystemExit(f"⛔ {p.name}: 0 baris — layout berubah?")
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dump-strings", action="store_true")
    args = ap.parse_args()

    rows = build_rows()
    if args.dump_strings:
        uniq = dump_strings(rows, _TR_FIELDS)
        print(json.dumps({s: "" for s in uniq}, ensure_ascii=False, indent=1))
        print(f"# {len(uniq)} string unik dari {len(rows)} baris", file=sys.stderr)
        return 0

    tr = json.loads(I18N.read_text(encoding="utf-8")) if I18N.exists() else {}
    if tr:
        for r in rows:
            r["part_name_asli"] = r["part_name"]
    miss = apply_i18n(rows, tr, _TR_FIELDS)

    rows.sort(key=lambda r: (r["alat"], r["jenis"], r["model"],
                             r["part_number"], r["part_name"]))
    write_json_gz(OUT, rows)
    alat = sorted({r["alat"] for r in rows})
    print(f"✅ {OUT.name}: {len(rows)} baris; alat: {', '.join(alat)}; "
          f"miss i18n (fallback asli): {miss}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
