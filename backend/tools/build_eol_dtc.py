# -*- coding: utf-8 -*-
"""
Build data kode error EOL CNHTC + diagram wiring dari artifact HTML
"Panduan Teknisi Sinotruk" (kurasi final milik pemilik, diterjemahkan Indonesia).

Sumber: file HTML artifact (arsip: D:/CNHTC_Data/artifact_panduan_teknisi.html;
CSV mentah pendukung juga di D:/CNHTC_Data/). Di dalam HTML ada:
  const DTC=[{c: unit kontrol, dtc: kode, d: deskripsi ID, p: penyebab ID,
              f: langkah perbaikan ID, part: komponen terkait}, ...]   (±5.910)
  IMGS=[{nama, grp, label, uri: data:image/jpeg;base64,...}, ...]      (55)

Output:
  1. backend/app/services/eol_dtc.json.gz  — dipakai services/eol_dtc.py
     (ikut commit & push.sh backend/app — BUKAN scp)
  2. data/wiring/<nama>.jpg + data/wiring/index.json — diagram wiring
     (deploy via scp ke /opt/maspart/data/wiring/)

Jalankan dari root repo:
    python backend/tools/build_eol_dtc.py [--html <path>]
"""
from __future__ import annotations

import argparse
import base64
import gzip
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # backend/

DATA = Path(__file__).resolve().parents[2] / "data"
OUT_JSON = Path(__file__).resolve().parents[1] / "app" / "services" / "eol_dtc.json.gz"
OUT_WIRING = DATA / "wiring"
DEFAULT_HTML = Path("D:/CNHTC_Data/artifact_panduan_teknisi.html")

_FNAME_RE = re.compile(r"^[A-Za-z0-9_.\-]+\.jpe?g$")


def _arr(html: str, marker: str):
    i = html.find(marker)
    if i < 0:
        return None
    v, _ = json.JSONDecoder().raw_decode(html, i + len(marker))
    return v


def build(html_path: Path) -> dict:
    html = html_path.read_text(encoding="utf-8")
    stats: dict = {}

    # ── DTC ──
    dtc_raw = _arr(html, "const DTC=")
    if not dtc_raw:
        raise SystemExit("⛔ array 'const DTC=' tidak ditemukan di HTML")
    rows, seen = [], set()
    buang = 0
    for r in dtc_raw:
        kode = (r.get("dtc") or "").strip()
        desk = (r.get("d") or "").strip()
        if not kode or not desk or desk.lower() in ("dicadangkan", "reserved", "保留"):
            buang += 1
            continue
        entry = {
            "unit": (r.get("c") or "").strip(),
            "kode": kode.upper(),
            "deskripsi": desk,
            "penyebab": (r.get("p") or "").strip(),
            "perbaikan": (r.get("f") or "").strip(),
            "part": (r.get("part") or "").strip(),
        }
        key = (entry["unit"], entry["kode"], entry["deskripsi"])
        if key in seen:
            buang += 1
            continue
        seen.add(key)
        rows.append(entry)
    with gzip.open(OUT_JSON, "wt", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, separators=(",", ":"))
    stats["dtc_mentah"] = len(dtc_raw)
    stats["dtc_dibuang"] = buang
    stats["dtc_final"] = len(rows)
    stats["unit_kontrol"] = len({r["unit"] for r in rows})
    stats["ber_perbaikan"] = sum(1 for r in rows if r["perbaikan"])

    # ── IMGS (diagram wiring) ──
    imgs = _arr(html, "IMGS=")
    n_img = 0
    index = []
    if imgs:
        OUT_WIRING.mkdir(parents=True, exist_ok=True)
        for im in imgs:
            nama = (im.get("nama") or "").strip()
            uri = im.get("uri") or ""
            if not _FNAME_RE.match(nama) or not uri.startswith("data:image/jpeg;base64,"):
                continue
            (OUT_WIRING / nama).write_bytes(
                base64.b64decode(uri.split(",", 1)[1]))
            index.append({"file": nama,
                          "grp": (im.get("grp") or "").strip(),
                          "label": (im.get("label") or "").strip()})
            n_img += 1
        (OUT_WIRING / "index.json").write_text(
            json.dumps(index, ensure_ascii=False, indent=1), encoding="utf-8")
    stats["wiring_jpg"] = n_img
    return stats


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--html", type=Path, default=DEFAULT_HTML)
    args = ap.parse_args()
    if not args.html.is_file():
        print(f"⛔ HTML sumber tak ditemukan: {args.html}")
        return 1
    s = build(args.html)
    print(f"DTC mentah        : {s['dtc_mentah']}")
    print(f"Dibuang (cadangan/duplikat): {s['dtc_dibuang']}")
    print(f"DTC final         : {s['dtc_final']}  ({s['unit_kontrol']} unit kontrol; "
          f"{s['ber_perbaikan']} ber-langkah-perbaikan)")
    print(f"Diagram wiring    : {s['wiring_jpg']} jpg -> {OUT_WIRING}")
    print(f"Ukuran json.gz    : {OUT_JSON.stat().st_size // 1024} KB -> {OUT_JSON}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
