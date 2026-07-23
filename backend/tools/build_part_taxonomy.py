"""Bangun app/services/part_taxonomy.json.gz — taksonomi KELUARGA part dari
~33rb nama part katalog (deterministik; aturan di services/part_taxonomy._RULES).

Kurasi `fungsi`/`gejala_umum`/`catatan` (diisi tools/curate_taxonomy.py)
DIPRESERVASI antar-rebuild via kunci `keluarga` (pola build_manual_teks).

    cd backend
    python tools/build_part_taxonomy.py
"""
from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import part_index, part_taxonomy  # noqa: E402
from app.services.knowledge_util import load_json, write_json_gz  # noqa: E402


def main() -> int:
    parts = part_index.all_parts_min()
    fam: dict[str, dict] = {}
    nama_freq: dict[str, Counter] = {}
    n_cocok = 0
    for pn, nama in parts:
        m = part_taxonomy.match_name(nama)
        if not m:
            continue
        n_cocok += 1
        keluarga, sistem, sub = m
        f = fam.setdefault(keluarga, {
            "keluarga": keluarga, "sistem": sistem, "sub_sistem": sub,
            "nama_kunci": [], "contoh_pn": [], "jumlah_pn": 0,
            "fungsi": "", "gejala_umum": "", "catatan": ""})
        f["jumlah_pn"] += 1
        if len(f["contoh_pn"]) < 8:
            f["contoh_pn"].append(pn)
        nama_freq.setdefault(keluarga, Counter())[
            " ".join(str(nama or "").upper().split())[:50]] += 1

    # nama_kunci = 3 nama part TERSERING keluarga itu (bahan cari + validasi kurasi)
    for kel, f in fam.items():
        f["nama_kunci"] = [n for n, _c in nama_freq[kel].most_common(3) if n]

    # PRESERVASI kurasi dari store lama (kunci = keluarga)
    lama = {r.get("keluarga"): r for r in
            (load_json(part_taxonomy._PATH) or []) if isinstance(r, dict)}
    n_kurasi = 0
    for kel, f in fam.items():
        o = lama.get(kel) or {}
        for k in ("fungsi", "gejala_umum", "catatan"):
            if str(o.get(k) or "").strip():
                f[k] = o[k]
        if f["fungsi"]:
            n_kurasi += 1

    rows = sorted(fam.values(), key=lambda r: -r["jumlah_pn"])
    write_json_gz(part_taxonomy._PATH, rows)
    print(f"✅ ditulis: {part_taxonomy._PATH} "
          f"({part_taxonomy._PATH.stat().st_size / 1024:.0f} KB)")
    print(f"   part diklasifikasi: {n_cocok}/{len(parts)} → "
          f"{len(rows)} keluarga · terkurasi fungsi: {n_kurasi}")
    for r in rows[:8]:
        print(f"   - {r['keluarga']}: {r['jumlah_pn']} PN ({r['sistem']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
