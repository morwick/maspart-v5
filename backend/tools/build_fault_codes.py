# -*- coding: utf-8 -*-
"""
Build backend/app/services/fault_codes.json — kode kesalahan (DTC) mesin Sinotruk
ECU Bosch MC, diekstrak dari PDF sumber:
    data/manuals/Manual_Sinotruk_MC_BOSCHECU_DH_CHINESE.pdf

Tabel kode error di PDF (header `J1939 DTC`→SPN|FMI, `亮灯状态`→MIL|SVS) punya
kolom per baris data:
    [0]=kode P/U  [3]=English (DFC_…)  [4]=中文  [5]=SPN  [6]=FMI  [7]=MIL  [8]=SVS
PDF juga memuat tabel definisi PIN (针脚) yang BUKAN kode error → hanya tabel
ber-header SPN+FMI+MIL yang diambil.

⚠️ DEV-ONLY: butuh `pdfplumber` (ada di venv, TIDAK di requirements.txt runtime —
skrip ini dijalankan offline; hasil `fault_codes.json` di-commit & dikirim ke
server, jadi container tak perlu pdfplumber).

Jalankan dari root repo:
    python backend/tools/build_fault_codes.py
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # backend/

DATA = Path(__file__).resolve().parents[2] / "data"
PDF = DATA / "manuals" / "Manual_Sinotruk_MC_BOSCHECU_DH_CHINESE.pdf"
OUT = Path(__file__).resolve().parents[1] / "app" / "services" / "fault_codes.json"

_CODE_RE = re.compile(r"^[PU][0-9A-F]{4}$")
_SPN_MAX = 524287  # batas J1939 (19-bit) — di atas ini = korup
_FMI_MAX = 31      # FMI 5-bit (0..31)


def _clean(s: str | None) -> str:
    """Rapikan sel: buang newline sambungan-baris, ciutkan spasi."""
    if not s:
        return ""
    s = s.replace("\r", "\n").replace("\n", "")
    return re.sub(r"\s+", " ", s).strip()


def _int(s: str | None, hi: int) -> int | None:
    v = _clean(s)
    if not v.isdigit():
        return None
    n = int(v)
    return n if n <= hi else None


def _onoff(s: str | None) -> str:
    return "ON" if _clean(s).upper() == "ON" else "OFF"


def _get(row: list, i: int) -> str:
    return row[i] if 0 <= i < len(row) else ""


def _is_fault_table(table: list[list]) -> bool:
    head = " ".join((c or "") for r in table[:6] for c in r).upper()
    return "SPN" in head and "FMI" in head and "MIL" in head


def build() -> tuple[list[dict], dict]:
    import pdfplumber

    rows: list[dict] = []
    seen: set = set()
    stats = {"halaman": 0, "tabel_kode": 0, "baris_mentah": 0,
             "spn_kosong": 0, "spn_korup_dibuang": 0, "duplikat": 0}

    with pdfplumber.open(str(PDF)) as pdf:
        stats["halaman"] = len(pdf.pages)
        for pg in pdf.pages:
            for table in pg.extract_tables():
                if not table or not _is_fault_table(table):
                    continue
                stats["tabel_kode"] += 1
                for r in table:
                    code = _clean(_get(r, 0)).upper()
                    if not _CODE_RE.match(code):
                        continue
                    stats["baris_mentah"] += 1
                    raw_spn = _clean(_get(r, 5))
                    spn = _int(_get(r, 5), _SPN_MAX)
                    fmi = _int(_get(r, 6), _FMI_MAX)
                    if spn is None and raw_spn.isdigit():
                        stats["spn_korup_dibuang"] += 1  # ada angka tapi > batas
                    if spn is None:
                        stats["spn_kosong"] += 1
                    entry = {
                        "code": code,
                        "english": _clean(_get(r, 3)),
                        "desc_cn": _clean(_get(r, 4)),
                        "spn": spn,
                        "fmi": fmi,
                        "mil": _onoff(_get(r, 7)),
                        "svs": _onoff(_get(r, 8)),
                    }
                    key = (entry["code"], entry["english"], entry["spn"], entry["fmi"])
                    if key in seen:
                        stats["duplikat"] += 1
                        continue
                    seen.add(key)
                    rows.append(entry)
    return rows, stats


def main() -> int:
    if not PDF.is_file():
        print(f"⛔ PDF sumber tak ditemukan: {PDF}")
        return 1
    rows, stats = build()
    OUT.write_text(json.dumps(rows, ensure_ascii=False, separators=(",", ":")),
                   encoding="utf-8")
    print(f"Halaman PDF          : {stats['halaman']}")
    print(f"Tabel kode error     : {stats['tabel_kode']}")
    print(f"Baris mentah         : {stats['baris_mentah']}")
    print(f"Duplikat dibuang     : {stats['duplikat']}")
    print(f"SPN korup dibuang    : {stats['spn_korup_dibuang']}")
    print(f"Entri final          : {len(rows)}  (SPN kosong: {stats['spn_kosong']})")
    print(f"Ukuran file          : {OUT.stat().st_size // 1024} KB")
    print(f"-> {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
