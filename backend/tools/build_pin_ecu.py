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

# ── Sumber TAMBAHAN (2026-07-18): 4 PDF English "PIN DEFINITION AND ELECTRIC
# CONTROL SCHEMATIC DIAGRAM" — teks (bukan tabel), format per-konektor:
#   "X1(A102/1) Pin Definition Comparison Table" / "Pin Description Wire color"
#   lalu baris "<pin> <deskripsi EN> [<kode warna kabel A-N>]".
# extract_text WAJIB x_tolerance=1.5 (default 3 melebur spasi antar-kata).
_EN_SOURCES = (  # (potongan nama file, label ECU, mode: "teks"|"tabel")
    ("MC NATIONAL V ENGINE ECU PIN DEFINITION", "Mesin MC National V (Bosch ECU)", "teks"),
    ("NANOBCU PIN DEFINITION", "NanoBCU (body control unit)", "teks"),
    # NBCU: pin di TABEL multi-arsitektur (X1.1…; 4 kolom varian deskripsi)
    ("【英文版】NBCU PIN DEFINITION", "NBCU (body control unit)", "tabel"),
    ("ZF-AMT PIN DEFINITION", "ZF-AMT (transmisi otomatis)", "teks"),
)
_PIN_TBL_RE = re.compile(r"^X\d+\.\d+$")  # X1.1 … X4.18 (NBCU)
_KONEKTOR_RE = re.compile(r"^(.{0,40}?)\s*Pin Definition Comparison Table\s*$", re.I)
# "<pin> <deskripsi> [<kode warna>] [<luas penampang mm2>]" — pin boleh berprefix
# huruf (K01/K54 di MC National V) atau angka polos (NanoBCU/NBCU/ZF-AMT).
_BARIS_EN_RE = re.compile(
    r"^([A-Z]?\d{1,3})\s+(.+?)"
    r"(?:\s+([A-Z]{1,4}(?:/[A-Z]{1,4})*))?"
    r"(?:\s+([\d.]+\s*mm2?))?$")
_SKIP_EN = ("pin description", "wire color", "note", "foreword",
            "conductor", "area", "cross-sectional")


def _s(v) -> str:
    return " ".join(str(v or "").replace("\n", " ").split())


def parse() -> list[dict]:
    """Tabel konektor manual Bosch CN (sumber asli) → baris ber-ecu 'MC (manual Bosch CN)'."""
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
                        "ecu": "MC (manual Bosch CN)", "konektor": "",
                        "pin": pin,
                        "sinyal": cells[1] if len(cells) > 1 else "",
                        "deskripsi": cells[2] if len(cells) > 2 else "",
                        "nilai_uji": cells[3] if len(cells) > 3 else "",
                    })
    return rows


def parse_en(path: Path, ecu: str) -> list[dict]:
    """PDF English per-ECU → baris {ecu, konektor, pin, deskripsi, warna_kabel}."""
    import pdfplumber

    rows: list[dict] = []
    konektor = ""
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            t = page.extract_text(x_tolerance=1.5) or ""
            for line in t.split("\n"):
                line = line.strip()
                if not line:
                    continue
                mk = _KONEKTOR_RE.match(line)
                if mk:
                    konektor = mk.group(1).strip()
                    continue
                low = line.lower()
                if any(low.startswith(s) for s in _SKIP_EN):
                    continue
                if not konektor:
                    continue  # baris pin hanya SETELAH header konektor
                mb = _BARIS_EN_RE.match(line)
                if not mb:
                    continue
                desk = mb.group(2).strip()
                warna = mb.group(3) or ""
                if not warna:
                    # halaman tanpa suffix 'mm2' (konektor A MC National):
                    # ekor ' <KODE-WARNA> <angka>' bocor ke deskripsi → pangkas.
                    mt = re.search(r"\s+([A-Z]{1,4}(?:/[A-Z]{1,4})*)\s+[\d.]+$", desk)
                    if mt:
                        warna = mt.group(1)
                        desk = desk[:mt.start()].strip()
                if len(desk) < 3 or desk.isdigit():
                    continue
                rows.append({
                    "ecu": ecu, "konektor": konektor,
                    "pin": mb.group(1), "sinyal": "",
                    "deskripsi": desk,
                    "nilai_uji": "",
                    "warna_kabel": warna,
                })
    return rows


def parse_en_tabel(path: Path, ecu: str) -> list[dict]:
    """PDF English ber-TABEL pin (NBCU): baris X<konektor>.<pin>, 4 kolom varian
    arsitektur → deskripsi = gabungan unik varian, + warna kabel & luas kabel."""
    import pdfplumber

    rows: list[dict] = []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            t = page.extract_text(x_tolerance=1.5) or ""
            mk = next((_KONEKTOR_RE.match(l.strip()) for l in t.split("\n")
                       if _KONEKTOR_RE.match(l.strip())), None)
            konektor = mk.group(1).strip() if mk else ""
            for tbl in page.extract_tables({"text_x_tolerance": 1.5}):
                for r in tbl:
                    cells = [_s(c) for c in (r or [])]
                    if not cells or not _PIN_TBL_RE.match(cells[0]):
                        continue
                    # kolom: pin | desc varian ×4 | warna | luas — varian identik digabung
                    desks = []
                    for d in cells[1:-2] if len(cells) >= 4 else cells[1:]:
                        d = d.strip()
                        if d and d.lower() != "spare" and d not in desks:
                            desks.append(d)
                    if not desks:
                        desks = ["Spare"]
                    warna = cells[-2] if len(cells) >= 3 and re.fullmatch(
                        r"[A-Z]{1,4}(?:/[A-Z]{1,4})*", cells[-2] or "") else ""
                    rows.append({
                        "ecu": ecu, "konektor": konektor or cells[0].split(".")[0],
                        "pin": cells[0], "sinyal": "",
                        "deskripsi": " / ".join(desks[:3]),
                        "nilai_uji": "",
                        "warna_kabel": warna,
                    })
    return rows


def _find_manual(needle: str) -> Path | None:
    for p in sorted((ROOT / "data" / "manuals").glob("*.pdf")):
        if needle.lower() in p.name.lower():
            return p
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dump-strings", action="store_true")
    args = ap.parse_args()

    if not SRC.exists():
        raise SystemExit(f"⛔ sumber tak ada: {SRC}")
    rows = parse()
    if len(rows) < 100:
        raise SystemExit(f"⛔ hanya {len(rows)} pin terekstrak — layout berubah?")
    # sumber English per-ECU (2026-07-18) — fail-loud bila file ada tapi 0 baris
    per_ecu = {"MC (manual Bosch CN)": len(rows)}
    for needle, ecu, mode in _EN_SOURCES:
        p = _find_manual(needle)
        if p is None:
            print(f"⚠️ lewati (file tak ada): {needle}")
            continue
        got = parse_en_tabel(p, ecu) if mode == "tabel" else parse_en(p, ecu)
        if not got:
            raise SystemExit(f"⛔ {p.name}: 0 pin terekstrak — layout berubah?")
        per_ecu[ecu] = len(got)
        rows.extend(got)
    # dedup (pin sama muncul di >1 tabel/konektor → simpan definisi berbeda)
    seen, uniq = set(), []
    for r in rows:
        key = (r["ecu"], r["konektor"], r["pin"], r["sinyal"], r["deskripsi"])
        if key in seen:
            continue
        seen.add(key)
        uniq.append(r)
    print("per ECU:", per_ecu)

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

    uniq.sort(key=lambda r: (r["ecu"], r["konektor"], len(r["pin"]), r["pin"],
                             r["sinyal"], r["deskripsi"]))
    write_json_gz(OUT, uniq)
    print(f"✅ {OUT.name}: {len(uniq)} baris pin; miss i18n (fallback CN): {miss}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
