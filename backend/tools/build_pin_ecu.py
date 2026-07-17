# -*- coding: utf-8 -*-
"""Ekstrak tabel DEFINISI PIN ECU dari Manual_Sinotruk_MC_BOSCHECU_DH_CHINESE.pdf
(halaman awal manual; kolom: 针脚 pin | 针脚定义 definisi | 控制逻辑 logika |
有效值/测试方法 nilai uji) → backend/app/services/pin_ecu.json.gz.

Nilai diagnosa: menjawab 'sensor X di pin berapa ECU', 'pin K54 untuk apa'.
(K4 rombakan 2026-07-17; naratif China manual DITUNDA — hanya tabel pin.)

i18n: backend/tools/i18n/pin_ecu_i18n.json (CN→ID, pola abs_scr; fallback CN
aman — model menerjemahkan sendiri bila belum ada). --dump-strings utk mengisi.

Jalankan dari root repo:  python backend/tools/build_pin_ecu.py [--dump-strings]
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
SRC = ROOT / "data" / "manuals" / "Manual_Sinotruk_MC_BOSCHECU_DH_CHINESE.pdf"
OUT = Path(__file__).resolve().parents[1] / "app" / "services" / "pin_ecu.json.gz"
I18N = Path(__file__).resolve().parent / "i18n" / "pin_ecu_i18n.json"

_TR_FIELDS = ("deskripsi", "nilai_uji")
_PIN_RE = re.compile(r"^[KA]?\d{1,3}$")  # K54, A12, 07 — kode pin di kolom 1


def _s(v) -> str:
    return " ".join(str(v or "").replace("\n", " ").split())


def parse() -> list[dict]:
    import pdfplumber

    rows: list[dict] = []
    with pdfplumber.open(SRC) as pdf:
        # tabel pin hanya di ±25 halaman pertama (manual: bagian definisi konektor)
        for page in pdf.pages[:30]:
            for tbl in page.extract_tables():
                if not tbl:
                    continue
                hdr = " ".join(_s(c) for c in (tbl[0] or []))
                if "针脚" not in hdr or "定义" not in hdr:
                    continue
                for r in tbl[1:]:
                    cells = [_s(c) for c in r if _s(c)]
                    if len(cells) < 2:
                        continue
                    pin = cells[0]
                    if not _PIN_RE.match(pin):
                        continue
                    # isi baris nyata: kolom 2 = KODE SINYAL internal (O_S_RL11),
                    # kolom 3 = deskripsi bilingual CN/EN, kolom 4 = nilai uji.
                    rows.append({
                        "pin": pin,
                        "sinyal": cells[1] if len(cells) > 1 else "",
                        "deskripsi": cells[2] if len(cells) > 2 else "",
                        "nilai_uji": cells[3] if len(cells) > 3 else "",
                    })
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dump-strings", action="store_true")
    args = ap.parse_args()

    if not SRC.exists():
        raise SystemExit(f"⛔ sumber tak ada: {SRC}")
    rows = parse()
    if len(rows) < 100:
        raise SystemExit(f"⛔ hanya {len(rows)} pin terekstrak — layout berubah?")
    # dedup (pin sama muncul di >1 tabel/konektor → simpan definisi berbeda)
    seen, uniq = set(), []
    for r in rows:
        key = (r["pin"], r["sinyal"], r["deskripsi"])
        if key in seen:
            continue
        seen.add(key)
        uniq.append(r)

    if args.dump_strings:
        u = dump_strings(uniq, _TR_FIELDS)
        print(json.dumps({s: "" for s in u}, ensure_ascii=False, indent=1))
        print(f"# {len(u)} string unik dari {len(uniq)} pin", file=sys.stderr)
        return 0

    tr = json.loads(I18N.read_text(encoding="utf-8")) if I18N.exists() else {}
    if tr:
        for r in uniq:
            r["deskripsi_asli"] = r["deskripsi"]
    miss = apply_i18n(uniq, tr, _TR_FIELDS)

    uniq.sort(key=lambda r: (len(r["pin"]), r["pin"], r["sinyal"], r["deskripsi"]))
    write_json_gz(OUT, uniq)
    print(f"✅ {OUT.name}: {len(uniq)} baris pin; miss i18n (fallback CN): {miss}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
