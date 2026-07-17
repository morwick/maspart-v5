# -*- coding: utf-8 -*-
"""Bangun store kanonik JADWAL PERAWATAN Shantui dari 4 xlsx data/manuals/
(nama memuat maintenance/periodic/cycle table; PART FILTER dikecualikan).

Parser 3-template (jangkar 'N/P SHANTUI'; Spanyol X / Inggris √ jam-di-baris-
berikut / Excavator kolom bergeser) DIANGKAT apa adanya dari services/
maintenance_ref.py — sejak rombakan 2026-07-17 parser rapuh ini pensiun dari
RUNTIME: kesalahan parsing ketahuan saat BUILD (fail-loud), bukan diam-diam di
produksi. Service maintenance_ref kini loader tipis atas output builder ini.

Output: backend/app/services/jadwal_perawatan.json.gz — baris:
  {jenis, model, varian, sistem, nama, part_number, qty, ganti_jam[]}
  (+ sistem_asli/nama_asli bila kamus i18n menerjemahkan)
i18n: backend/tools/i18n/maintenance_i18n.json (ES/EN/CN → ID, pola abs_scr;
fallback teks asli — aman saat kamus belum lengkap). --dump-strings utk mengisi.

⚠️ KONSEKUENSI (keputusan pemilik 2026-07-17): ganti/tambah Excel maintenance =
jalankan builder ini + commit + deploy (bukan lagi hot-reload runtime).
Jalankan dari root repo:  python backend/tools/build_maintenance.py [--dump-strings]
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
MANUALS = ROOT / "data" / "manuals"
OUT = Path(__file__).resolve().parents[1] / "app" / "services" / "jadwal_perawatan.json.gz"
I18N = Path(__file__).resolve().parent / "i18n" / "maintenance_i18n.json"

_TR_FIELDS = ("sistem", "nama")

# ── parser (diangkat verbatim dari maintenance_ref.py pra-rombakan) ──
_JENIS_HINT = [
    ("wheel loader", "loader"), ("loader", "loader"),
    ("excavator", "excavator"),
    ("motor grader", "grader"), ("mitor grader", "grader"), ("grader", "grader"),
    ("road roller", "roller"), ("roller", "roller"),
    ("bulldozer", "dozer"), ("buldozer", "dozer"), ("dozer", "dozer"),
]
_MARKS = {"x", "√", "✓", "✔", "●", "◯", "o", "*", "×"}
_EMISI = {"国四": "Euro IV", "国三": "Euro III", "国二": "Euro II", "国一": "Euro I",
          "国Ⅱ": "Euro II", "国Ⅲ": "Euro III"}
_ENGINE_RE = re.compile(
    r"\b(WP\d[\w.\-]*|WD\d[\w.\-]*|NT\d[\w.\-]*|6CT[\w.\-]*|6BT[\w.\-]*|"
    r"QS[BC][\w.\-]*|\dM\d\d[\w.\-]*)\b", re.IGNORECASE)


def files(manuals_dir: Path | None = None) -> list[Path]:
    d = manuals_dir or MANUALS
    if not d.is_dir():
        return []
    out = []
    for p in sorted(d.glob("*.xlsx")):
        n = p.name.lower()
        if n.startswith("~$") or "part filter" in n:
            continue
        if any(k in n for k in ("maintenance", "periodic", "cycle table")):
            out.append(p)
    return out


def _cell(df, r: int, c: int) -> str:
    import pandas as pd
    try:
        v = df.iat[r, c]
        return "" if pd.isna(v) else str(v).strip()
    except Exception:
        return ""


def _is_mark(v: str) -> bool:
    return v.strip().lower() in _MARKS


def _jenis_of(meta: str) -> str:
    m = (meta or "").lower()
    for kw, jn in _JENIS_HINT:
        if kw in m:
            return jn
    return ""


def _clean_model(src: str) -> str:
    for tok in re.findall(r"[A-Za-z0-9][A-Za-z0-9\-]*", src or ""):
        if re.search(r"[A-Za-z]", tok) and re.search(r"\d", tok):
            return tok.upper()
    return ""


def _model_of(model_row: str, unit_line: str, sheet: str) -> str:
    for src in (model_row, unit_line, sheet):
        mv = _clean_model(src)
        if mv:
            return mv
    return ""


def _varian_of(*texts: str) -> str:
    parts: list[str] = []
    blob = " ".join(t for t in texts if t)
    for zh, en in _EMISI.items():
        if zh in blob and en not in parts:
            parts.append(en)
    for mm in re.findall(r"[（(]\s*([^）)]+?)\s*[）)]", blob):
        tok = mm.strip()
        if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.\-]{1,14}", tok) and tok not in parts:
            parts.append(tok)
    for mm in _ENGINE_RE.findall(blob):
        tok = mm.strip().upper()
        if tok not in [p.upper() for p in parts]:
            parts.append(tok)
    return " / ".join(parts)


def _hours(label: str) -> int | None:
    m = re.fullmatch(r"\s*([\d,]+)\s*[hH]?\s*", label or "")
    if not m:
        return None
    try:
        return int(m.group(1).replace(",", ""))
    except ValueError:
        return None


def _hour_cols(df, hdr: int, ncol: int) -> list[tuple[int, int]]:
    best: list[tuple[int, int]] = []
    for r in (hdr, hdr + 1):
        cols = [(c, j) for c in range(ncol) if (j := _hours(_cell(df, r, c))) is not None]
        if len(cols) > len(best):
            best = cols
    return best


def _find_col(df, hdr: int, ncol: int, *keys: str) -> int | None:
    for c in range(ncol):
        up = _cell(df, hdr, c).upper()
        if any(k in up for k in keys):
            return c
    return None


def _norm(s: str) -> str:
    return re.sub(r"[\s_\-()（）]", "", (s or "")).upper()


def parse_file(path: Path) -> list[dict]:
    import pandas as pd

    xls = pd.ExcelFile(path, engine="openpyxl")
    rows: list[dict] = []
    for sheet in xls.sheet_names:
        df = pd.read_excel(xls, sheet_name=sheet, header=None, dtype=str)
        nrow, ncol = df.shape
        if nrow == 0 or ncol == 0:
            continue
        meta_lines: list[str] = []
        modelo_row = ""
        unit_line = ""
        hdr = None
        for r in range(min(nrow, 16)):
            first = _cell(df, r, 0)
            if first:
                meta_lines.append(first)
            up = first.upper()
            if "MODEL" in up and not modelo_row:
                modelo_row = first
            if up.startswith(("UNIDAD", "UNIT", "MACHINE")) and not unit_line:
                unit_line = first
            if hdr is None and any("N/P SHANTUI" in _cell(df, r, c).upper()
                                   for c in range(ncol)):
                hdr = r
        if hdr is None:
            continue

        meta_blob = " ".join(meta_lines)
        jenis = _jenis_of(meta_blob)
        model = _model_of(modelo_row, unit_line, sheet)
        varian = _varian_of(meta_blob, sheet)
        is_bilingual = ("中英文" in sheet) or ("中英" in sheet)

        pn_c = _find_col(df, hdr, ncol, "N/P SHANTUI")
        qty_c = _find_col(df, hdr, ncol, "CANTIDAD", "QUANTITY", "QTY", "数量")
        name2_c = _find_col(df, hdr, ncol, "PART NAME", "零件名称")
        interval = _hour_cols(df, hdr, ncol)
        if pn_c is None or not interval:
            continue

        sistem = ""
        for r in range(hdr + 1, nrow):
            nama = _cell(df, r, 0)
            if not nama:
                continue
            low = nama.lower()
            if low.startswith(("note:", "nota:", "nota ", "note ", "备注", "observ")):
                continue
            pn = _cell(df, r, pn_c)
            qty = _cell(df, r, qty_c) if qty_c is not None else ""
            if not pn and not qty:
                sistem = nama
                continue
            if name2_c is not None:
                n2 = _cell(df, r, name2_c)
                if n2 and n2.lower() not in low:
                    nama = f"{nama} · {n2}"
            ganti = [j for c, j in interval if _is_mark(_cell(df, r, c))]
            rows.append({
                "jenis": jenis, "model": model, "varian": varian,
                "sistem": sistem, "nama": nama, "part_number": pn,
                "qty": qty, "ganti_jam": ganti,
                "_bilingual": is_bilingual, "_base": _norm(model),
            })
    return rows


def _loose(base: str) -> str:
    return re.sub(r"[A-Z]+$", "", base or "") or base


def dedup(rows: list[dict]) -> list[dict]:
    covered = {_loose(r["_base"]) for r in rows if not r["_bilingual"] and r["_base"]}
    kept = [r for r in rows if not (r["_bilingual"] and _loose(r["_base"]) in covered)]
    seen, out = set(), []
    for r in kept:
        key = (r["jenis"], r["model"], r["varian"], r["sistem"], r["nama"],
               r["part_number"], tuple(r["ganti_jam"]))
        if key in seen:
            continue
        seen.add(key)
        out.append({k: v for k, v in r.items() if not k.startswith("_")})
    return out


def build_rows(manuals_dir: Path | None = None) -> list[dict]:
    """Seluruh baris kanonik (parse semua file + dedup). FAIL-LOUD bila sebuah
    file tak menghasilkan baris (anchor hilang/template berubah)."""
    fs = files(manuals_dir)
    rows: list[dict] = []
    for p in fs:
        got = parse_file(p)
        if not got:
            raise SystemExit(f"⛔ {p.name}: 0 baris — anchor 'N/P SHANTUI' hilang "
                             "atau template berubah")
        rows.extend(got)
    return dedup(rows)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dump-strings", action="store_true")
    args = ap.parse_args()

    rows = build_rows()
    if not rows:
        raise SystemExit(f"⛔ tidak ada file maintenance di {MANUALS}")

    if args.dump_strings:
        uniq = dump_strings(rows, _TR_FIELDS)
        print(json.dumps({s: "" for s in uniq}, ensure_ascii=False, indent=1))
        print(f"# {len(uniq)} string unik dari {len(rows)} baris", file=sys.stderr)
        return 0

    tr = json.loads(I18N.read_text(encoding="utf-8")) if I18N.exists() else {}
    # simpan teks asli SEBELUM diterjemahkan (pencarian sinonim lintas-bahasa
    # tetap kena istilah ES/EN/CN asli)
    if tr:
        for r in rows:
            r["sistem_asli"] = r["sistem"]
            r["nama_asli"] = r["nama"]
    miss = apply_i18n(rows, tr, _TR_FIELDS)

    rows.sort(key=lambda r: (r["jenis"], r["model"], r["varian"], r["sistem"],
                             r["nama"], r["part_number"]))
    write_json_gz(OUT, rows)
    models = sorted({r["model"] for r in rows if r["model"]})
    print(f"✅ {OUT.name}: {len(rows)} baris, {len(models)} model; "
          f"string belum diterjemahkan (fallback asli): {miss}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
