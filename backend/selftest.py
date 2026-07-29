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
    _lapor_belajar()
    return 0


def _lapor_belajar() -> None:
    """Umur artefak loop belajar-sendiri (ai_belajar) & dataset gejala.

    Loop ini dirancang rapi tapi mudah TIDAK PERNAH BERJALAN tanpa ada yang
    sadar: schedulernya baru menyala 15 menit setelah startup dan seluruh
    keluarannya berupa file di disk. Bila `state.json` tak pernah terbentuk,
    kamus tak pernah tumbuh dari kegagalan nyata — dan itu senyap sempurna.
    Baris-baris ini membuatnya terlihat."""
    import time as _t
    from pathlib import Path

    from app.core.config import get_settings
    from app.services import ai_belajar

    print("\n── LOOP BELAJAR ───────────────────────────────────────")
    berkas: list[tuple[str, Path]] = [
        ("state ai_belajar", ai_belajar._state_path()),
        ("gap topik", ai_belajar._gap_path()),
        ("pencarian nihil", get_settings().data_path / "search_misses.json"),
        ("dataset gejala", get_settings().data_path / "sinonim" / "gejala_map.json"),
    ]
    for label, p in berkas:
        if not p.exists():
            print(f"  {label:18s}: ⚠️  BELUM ADA ({p.name})")
            continue
        umur_jam = (_t.time() - p.stat().st_mtime) / 3600
        tanda = "⚠️ " if umur_jam > 48 else "✅"
        print(f"  {label:18s}: {tanda} diperbarui {umur_jam:.1f} jam lalu")


if __name__ == "__main__":
    raise SystemExit(main())
