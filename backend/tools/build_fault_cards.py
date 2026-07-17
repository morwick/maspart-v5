# -*- coding: utf-8 -*-
"""Ekstrak 216 kartu PDF lembar diagnosa (data/Fault/SPN <n> FMI <m>[ (VARIAN)].pdf)
→ dataset TERSTRUKTUR yang bisa DIBACA model: backend/app/services/dtc_diagnosa.json.gz.

Sebelumnya isi PDF hanya tampil ke user sebagai kartu (fault_pdf.py — TIDAK diubah;
kartu tetap tampil); model cuma tahu SPN/FMI dari nama file. Dataset ini membuat
model ikut MEMBACA: judul, part terkait, kondisi trigger, reaksi/proteksi,
penyebab bernomor, langkah troubleshooting bernomor.

Isi kartu 100% English (terverifikasi 216/216) dgn layout form seragam —
parser memakai pdfplumber extract_tables (baris label kiri + isi kanan).
FAIL-LOUD: kartu tanpa anchor 'Possible cause'/'Troubleshooting' → SystemExit.

i18n: backend/tools/i18n/fault_cards_i18n.json (EN→ID, pola abs_scr_i18n; diisi
bertahap — string yang belum ada dibiarkan English, AMAN karena model paham EN).
Mode --dump-strings mencetak string unik untuk mengisi kamus.

Skema baris (join ke store DTC via (spn, fmi)):
  {spn, fmi, kode, no_kartu, varian, file, judul, part_terkait,
   kondisi_trigger, reaksi, penyebab:[...], langkah:[...], tes_setelah}

Jalankan dari root repo:  python backend/tools/build_fault_cards.py [--dump-strings]
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
FAULT_DIR = ROOT / "data" / "Fault"
OUT = Path(__file__).resolve().parents[1] / "app" / "services" / "dtc_diagnosa.json.gz"
I18N = Path(__file__).resolve().parent / "i18n" / "fault_cards_i18n.json"

_NAME_RE = re.compile(r"^SPN (\d+) FMI (\d+)(?:\s*\(([^)]+)\))?\.pdf$", re.I)
_CODE_RE = re.compile(r"^[PU][0-9A-F]{4}$")
_ITEM_RE = re.compile(r"^\s*(\d+)[.、]\s*")

# kolom yang diterjemahkan kamus i18n (judul/teks bebas; kode & PN tidak)
_TR_FIELDS = ("judul", "part_terkait", "kondisi_trigger", "reaksi",
              "penyebab", "langkah", "tes_setelah")


def _cell(s) -> str:
    return " ".join(str(s or "").replace("\n", " ").split())


def _items(text: str) -> list[str]:
    """Pecah teks blok bernomor '1. … 2. …' (newline dalam sel dipertahankan
    pdfplumber) menjadi list per-item; blok tanpa nomor → [teks utuh]."""
    raw = str(text or "")
    parts: list[str] = []
    cur: list[str] = []
    for line in raw.split("\n"):
        if _ITEM_RE.match(line) and cur:
            parts.append(" ".join(" ".join(cur).split()))
            cur = [line]
        else:
            cur.append(line)
    if cur:
        parts.append(" ".join(" ".join(cur).split()))
    out = []
    for p in parts:
        p = _ITEM_RE.sub("", p).strip()
        if p:
            out.append(p)
    return out


def parse_card(path: Path) -> dict:
    import pdfplumber

    m = _NAME_RE.match(path.name)
    if not m:
        raise SystemExit(f"⛔ nama file tak dikenal: {path.name}")
    spn, fmi = int(m.group(1)), int(m.group(2))
    varian = (m.group(3) or "").strip()

    rows: list[list[str]] = []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            for tbl in page.extract_tables():
                for r in tbl:
                    cells = [c for c in r if c not in (None, "")]
                    if cells:
                        rows.append([str(c) for c in cells])

    card = {"spn": spn, "fmi": fmi, "kode": "", "no_kartu": "", "varian": varian,
            "file": path.name, "judul": "", "part_terkait": "",
            "kondisi_trigger": "", "reaksi": "", "penyebab": [], "langkah": [],
            "tes_setelah": ""}
    section = ""  # menampung konten baris SETELAH baris label seksi
    for cells in rows:
        first = _cell(cells[0])
        rest = [c for c in cells[1:]]
        low = first.lower()
        if low.startswith("no：") or low.startswith("no:"):
            card["no_kartu"] = first.split("：")[-1].split(":")[-1].strip()
        elif low in ("fault", "information", "fault information"):
            if rest:
                card["judul"] = (card["judul"] + " " + " ".join(_cell(c) for c in rest)).strip()
        elif low.startswith("p code"):
            continue  # baris header tabel kode
        elif low == "-" or low.startswith("- classification"):
            continue
        elif _CODE_RE.match(first.upper()):
            card["kode"] = first.upper()
        elif low.startswith("fault-related parts"):
            card["part_terkait"] = " ".join(_cell(c) for c in rest).strip()
        elif low.startswith("fault trigger condition"):
            card["kondisi_trigger"] = " ".join(_cell(c) for c in rest).strip()
        elif low.startswith("fault protection strategy"):
            card["reaksi"] = " ".join(_cell(c) for c in rest).strip()
        elif low.startswith("possible cause"):
            section = "penyebab"
        elif low.startswith("troubleshooting recommendation"):
            section = "langkah"
        elif low.startswith("test after troubleshooting"):
            section = "tes_setelah"
        elif section:
            teks = "\n".join([str(cells[0])] + [str(c) for c in rest])
            if section == "tes_setelah":
                card["tes_setelah"] = (card["tes_setelah"] + " " + _cell(teks)).strip()
            else:
                card[section].extend(_items(teks))
    return card


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dump-strings", action="store_true",
                    help="cetak string English unik (JSON) untuk mengisi kamus i18n")
    args = ap.parse_args()

    files = sorted(FAULT_DIR.glob("*.pdf"))
    if not files:
        raise SystemExit(f"⛔ tidak ada PDF di {FAULT_DIR}")
    cards, gagal = [], []
    for f in files:
        c = parse_card(f)
        if not c["penyebab"] or not c["langkah"]:
            gagal.append(f.name)
        cards.append(c)
    if gagal:
        raise SystemExit("⛔ anchor Possible cause/Troubleshooting tak ketemu di: "
                         + ", ".join(gagal))

    if args.dump_strings:
        uniq = dump_strings(cards, _TR_FIELDS)
        print(json.dumps({s: "" for s in uniq}, ensure_ascii=False, indent=1))
        print(f"# {len(uniq)} string unik dari {len(cards)} kartu", file=sys.stderr)
        return 0

    tr = json.loads(I18N.read_text(encoding="utf-8")) if I18N.exists() else {}
    miss = apply_i18n(cards, tr, _TR_FIELDS)

    cards.sort(key=lambda c: (c["spn"], c["fmi"], c["varian"], c["file"]))
    write_json_gz(OUT, cards)
    n_kode = sum(1 for c in cards if c["kode"])
    print(f"✅ {OUT.name}: {len(cards)} kartu dari {len(files)} PDF; "
          f"ber-kode P: {n_kode}; string belum diterjemahkan (fallback EN): {miss}")
    for c in cards[:3]:
        print(f"   contoh: SPN {c['spn']} FMI {c['fmi']} {c['kode']} — "
              f"{len(c['penyebab'])} penyebab, {len(c['langkah'])} langkah")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
