# -*- coding: utf-8 -*-
"""
Build data kode kesalahan ABS + SCR gas 国V dari 2 Excel di data/manuals/
(kurasi pemilik). Teks disajikan Bahasa Indonesia via kamus abs_scr_i18n.json
(terjemahan statik — build tetap deterministik, TANPA panggilan API).

Sumber (data/manuals/, hanya input build — TAK ikut deploy/scp):
  【英】ABS+Fault+Code+Table_故障代码表.xlsx
      → tabel DTC rem ABS (WABCO/Sinotruk): SPN/FMI + SID + Blink Code + Repair Guide.
  【英】EURO+V+Gas+Assisted+SCR+Fault+Code+Table_国V气助SCR故障代码表.xlsx
      → tabel DTC SCR gas 国V: kode P (P0427, P1D06…) + Part + penjelasan + monitoring.

Output:
  backend/app/services/abs_scr_codes.json.gz  — dipakai services/abs_scr_codes.py
  (IKUT commit & push.sh backend/app — BUKAN scp; seperti eol_dtc.json.gz)

Mode:
  --dump-strings : cetak (stdout) daftar string English unik yang perlu
                   diterjemahkan — utk mengisi abs_scr_i18n.json.
  (default)      : terapkan kamus abs_scr_i18n.json (fallback ke English bila
                   sebuah string belum ada di kamus) lalu tulis json.gz.

Jalankan dari root repo:
    python backend/tools/build_abs_scr.py [--dump-strings]
"""
from __future__ import annotations

import argparse
import gzip
import json
from pathlib import Path

import openpyxl

DATA = Path(__file__).resolve().parents[2] / "data"
MANUALS = DATA / "manuals"
OUT_JSON = Path(__file__).resolve().parents[1] / "app" / "services" / "abs_scr_codes.json.gz"
I18N = Path(__file__).resolve().parent / "abs_scr_i18n.json"


# ── util ─────────────────────────────────────────────────────────────
def _find(*needles: str) -> Path:
    """Cari 1 file .xlsx di data/manuals yang namanya memuat SEMUA `needles`."""
    for p in sorted(MANUALS.glob("*.xlsx")):
        if p.name.startswith("~$"):
            continue
        name = p.name
        if all(n.lower() in name.lower() for n in needles):
            return p
    raise SystemExit(f"⛔ Excel dgn kata {needles} tak ditemukan di {MANUALS}")


def _s(v) -> str:
    """Sel → string bersih (rapikan whitespace, buang newline berlebih)."""
    if v is None:
        return ""
    t = str(v).replace("\r", " ").strip()
    return " ".join(t.split())


def _num(v):
    s = _s(v)
    if not s:
        return None
    try:
        return int(float(s))
    except ValueError:
        return None


def _load_wb(path: Path):
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb.active
    return [list(r) for r in ws.iter_rows(values_only=True)]


# ── parser ABS (SPN/FMI + Repair Guide) ──────────────────────────────
# Layout kolom (dikonfirmasi dari file): kolom deskripsi TAK ber-header,
# jadi kita temukan indeks kolom BER-label lalu ambil kolom desc sebagai
# (label+1) — tahan bila urutan/ jumlah kolom spacer bergeser.
def _abs_header_map(rows: list) -> tuple[int, dict]:
    for i, row in enumerate(rows[:5]):
        cells = [_s(c).upper() for c in row]
        if "SPN" in cells and "FMI" in cells:
            idx = {}
            for ci, c in enumerate(cells):
                if c and c not in idx:
                    idx[c] = ci
            return i, idx
    raise SystemExit("⛔ Baris header ABS (SPN/FMI) tak ditemukan")


def parse_abs(path: Path) -> list[dict]:
    rows = _load_wb(path)
    hi, idx = _abs_header_map(rows)
    c_spn = idx["SPN"]
    c_fmi = idx["FMI"]
    c_sid = idx.get("SID")
    c_blink = idx.get("BLINK CODE")
    c_lamp = idx.get("LAMP")
    c_reaction = idx.get("REACTION")
    c_repair = idx.get("REPAIR GUIDE")
    out: list[dict] = []
    seen: set = set()
    for row in rows[hi + 1:]:
        spn = _num(row[c_spn]) if c_spn is not None else None
        fmi = _num(row[c_fmi]) if c_fmi is not None else None
        if spn is None and fmi is None:
            continue
        komponen = _s(row[c_spn + 1]) if c_spn + 1 < len(row) else ""      # nama SPN
        fmi_desc = _s(row[c_fmi + 1]) if c_fmi + 1 < len(row) else ""       # arti FMI
        blink = _s(row[c_blink]) if c_blink is not None else ""
        blink_desc = _s(row[c_blink + 1]) if (c_blink is not None and c_blink + 1 < len(row)) else ""
        lamp = _s(row[c_lamp]) if c_lamp is not None else ""
        reaksi = _s(row[c_reaction]) if c_reaction is not None else ""
        repair = _s(row[c_repair]) if c_repair is not None else ""
        sid = _num(row[c_sid]) if c_sid is not None else None
        key = (spn, fmi, blink_desc, repair)
        if key in seen:
            continue
        seen.add(key)
        out.append({
            "sistem": "ABS",
            "kode": "",
            "spn": spn, "fmi": fmi, "sid": sid,
            "blink": blink,
            "komponen": komponen,          # istilah teknis — dibiarkan English
            "deskripsi": blink_desc or fmi_desc,   # gangguan konkret
            "penyebab": fmi_desc,          # sifat sinyal (arti FMI)
            "reaksi": reaksi,              # reaksi/akibat sistem
            "perbaikan": repair,           # Repair Guide
            "lampu": lamp,
        })
    return out


# ── parser SCR gas 国V (P-code) ──────────────────────────────────────
def parse_scr(path: Path) -> list[dict]:
    rows = _load_wb(path)
    # Header: NO. | Part | Faults Code | Fault Explanation | Monitoring strategy | Torque limit Activation
    hi = 0
    for i, row in enumerate(rows[:5]):
        cells = [_s(c).lower() for c in row]
        if any("code" in c for c in cells) and any("part" == c for c in cells):
            hi = i
            break
    out: list[dict] = []
    seen: set = set()
    for row in rows[hi + 1:]:
        part = _s(row[1]) if len(row) > 1 else ""
        kode = _s(row[2]).upper() if len(row) > 2 else ""
        deskripsi = _s(row[3]) if len(row) > 3 else ""
        monitoring = _s(row[4]) if len(row) > 4 else ""
        torque = _s(row[5]) if len(row) > 5 else ""
        if not kode:
            continue
        if kode in seen:
            continue
        seen.add(kode)
        out.append({
            "sistem": "SCR",
            "kode": kode,
            "spn": None, "fmi": None, "sid": None,
            "blink": "",
            "komponen": part,
            "deskripsi": deskripsi,
            "penyebab": monitoring,        # strategi monitoring = cara deteksi
            "reaksi": torque,              # batas torsi diaktifkan (immediately)
            "perbaikan": "",               # tabel tak memuat langkah perbaikan
            "lampu": "",
        })
    return out


# ── terjemahan ───────────────────────────────────────────────────────
# Kolom yang diterjemahkan (komponen SCR = Part ikut; komponen ABS teknis dibiarkan).
_TR_FIELDS_ABS = ("deskripsi", "penyebab", "reaksi", "perbaikan", "lampu")
_TR_FIELDS_SCR = ("komponen", "deskripsi", "penyebab", "reaksi")


def _tr_fields(row: dict) -> tuple[str, ...]:
    return _TR_FIELDS_ABS if row["sistem"] == "ABS" else _TR_FIELDS_SCR


def _unique_strings(rows: list[dict]) -> list[str]:
    vals: set = set()
    for r in rows:
        for f in _tr_fields(r):
            v = (r.get(f) or "").strip()
            if v:
                vals.add(v)
    return sorted(vals)


def _apply_i18n(rows: list[dict], tr: dict) -> int:
    miss = 0
    for r in rows:
        for f in _tr_fields(r):
            v = (r.get(f) or "").strip()
            if not v:
                continue
            if v in tr and tr[v]:
                r[f] = tr[v]
            else:
                miss += 1
    return miss


# ── main ─────────────────────────────────────────────────────────────
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dump-strings", action="store_true",
                    help="cetak string English unik (JSON) untuk diterjemahkan")
    args = ap.parse_args()

    abs_rows = parse_abs(_find("ABS", "Fault"))
    scr_rows = parse_scr(_find("SCR", "Fault"))
    rows = abs_rows + scr_rows

    if args.dump_strings:
        uniq = _unique_strings(rows)
        print(json.dumps({s: "" for s in uniq}, ensure_ascii=False, indent=2))
        print(f"# {len(uniq)} string unik (ABS {len(abs_rows)} baris, SCR {len(scr_rows)} baris)",
              file=__import__("sys").stderr)
        return

    tr = {}
    if I18N.exists():
        tr = json.loads(I18N.read_text(encoding="utf-8"))
    miss = _apply_i18n(rows, tr)

    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(OUT_JSON, "wt", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, separators=(",", ":"))

    print(f"✅ {OUT_JSON.name}: {len(rows)} baris "
          f"(ABS {len(abs_rows)}, SCR {len(scr_rows)}); "
          f"{miss} string belum diterjemahkan (fallback English).")


if __name__ == "__main__":
    main()
