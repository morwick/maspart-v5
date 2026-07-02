# -*- coding: utf-8 -*-
"""
Build data/catalog_bom.json — BOM PER UNIT PER KATEGORI (semua sheet katalog).

Thin CLI: logika sebenarnya ada di app/services/catalog_bom.py (satu sumber
kebenaran, dipakai juga oleh endpoint admin 'Rebuild BOM'). Mendukung dua sumbu
banding: antar-UNIT per kategori, dan antar-PN ASSY. Lihat §3.5.5b PROJECT.md.

Jalankan dari root repo:
    python backend/tools/build_catalog_bom.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# agar bisa `from app.services import catalog_bom`
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # backend/

from app.services import catalog_bom  # noqa: E402

DATA = Path(__file__).resolve().parents[2] / "data"


def main() -> int:
    out, stats = catalog_bom.build_data(DATA)
    p = DATA / "catalog_bom.json"
    p.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"File katalog dipindai : {stats['file_katalog_dipindai']}")
    print(f"Unit berkategori      : {stats['unit_berkategori']}")
    print(f"Kategori unik         : {stats['kategori']} -> {list(out['kategori'].keys())}")
    print(f"Assy PN terindeks     : {stats['assy_terindeks']}")
    print(f"Total baris part      : {stats['total_baris_part']}")
    print(f"Ukuran file           : {p.stat().st_size // 1024} KB")
    print(f"-> {p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
