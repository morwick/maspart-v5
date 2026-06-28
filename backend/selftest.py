"""
Self-test: bangun index dari ../data lalu jalankan beberapa pencarian.
Tidak butuh server FastAPI maupun koneksi Supabase.

    python selftest.py            # ringkasan index + contoh
    python selftest.py WG16       # cari term tertentu
"""
from __future__ import annotations

import sys
import time

from app.services import part_index


def main() -> int:
    t0 = time.time()
    st = part_index.refresh_index()
    dt = time.time() - t0

    print("── INDEX ──────────────────────────────────────────────")
    print(f"data_dir      : {st['data_dir']}")
    print(f"file part     : {st['file_count']}")
    print(f"sheet         : {st['sheet_count']}")
    print(f"part unik (PN): {st['part_count']}")
    print(f"entri stok    : {st['stok_entries']}")
    print(f"entri harga   : {st['harga_entries']}")
    print(f"gudang        : {st['gudang_names']}")
    print(f"waktu build   : {dt:.2f}s")
    print()

    if st["sheet_count"] == 0:
        print("⚠️  Tidak ada file part terindeks. Cek DATA_DIR / isi folder data/.")
        return 1

    term = sys.argv[1] if len(sys.argv) > 1 else None
    if not term:
        # Ambil satu PN nyata dari index sebagai contoh otomatis.
        sample = None
        for fi in part_index._state["excel_files"]:
            for pn in fi.get("part_number_index", {}):
                if pn:
                    sample = pn[:6]
                    break
            if sample:
                break
        term = sample or "A"
        print(f"(tanpa argumen — pakai contoh term: '{term}')")

    res = part_index.search_part_number(term)
    print(f"\n── SEARCH q='{term}' → {len(res)} hasil ──────────────")
    for r in res[:10]:
        print(f"  [{r['file']}] {r['part_number']} | {r['part_name'][:40]} "
              f"| qty={r['quantity']} | stok={r['stok']} | harga={r['harga']}")
    if len(res) > 10:
        print(f"  ... (+{len(res) - 10} lagi)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
