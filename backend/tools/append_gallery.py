"""
Tambahkan hasil export CSV BARU ke file galeri Cari-by-Foto yang LAMA — dengan aman.

Kenapa tidak copy-paste pakai Excel? File galeri lama besar (~300 MB) dan Excel
sering merusak kolom `embedding` (teks panjang + notasi ilmiah). Script ini:
  - melewati baris header file export baru (tidak ikut tertempel),
  - menjamin file lama diakhiri newline (baris baru tidak nyambung ke baris lama),
  - menulis UTF-8 tanpa BOM (tidak mengotori isi),
  - opsional --dedup: lewati baris (part_number, sims_url) yang sudah ada di file lama,
  - hanya MENAMBAH ke akhir file lama; isi lama tidak pernah dibaca-ulang/ditimpa
    (kecuali --dedup, yang membaca file lama sekali untuk ambil daftar kunci).

Pemakaian (dari folder backend/):
    venv\\Scripts\\python.exe tools\\append_gallery.py <file_export_baru.csv> [--dedup]

Target file lama diambil dari konfigurasi backend (IMAGE_INDEX_CSV / lokasi default),
jadi selalu menambah ke file yang dipakai aplikasi. Setelah selesai, klik
"Reload Galeri" di menu admin (atau restart backend) supaya data baru kebaca.
"""
from __future__ import annotations

import csv
import sys
from pathlib import Path

# Agar bisa `from app.core.config import get_settings` saat dijalankan dari backend/.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.core.config import get_settings  # noqa: E402

REQUIRED_COLS = {"part_number", "sims_url", "embedding"}

# Embedding bisa ~10 KB per sel — naikkan batas field csv.
try:
    csv.field_size_limit(16 * 1024 * 1024)
except Exception:
    pass


def _key(row: dict) -> tuple[str, str]:
    return ((row.get("part_number") or "").strip().upper(), (row.get("sims_url") or "").strip())


def _ensure_trailing_newline(path: Path) -> None:
    """Pastikan file diakhiri '\\n' supaya baris yang ditambahkan tidak menyambung."""
    if not path.exists() or path.stat().st_size == 0:
        return
    with open(path, "rb+") as f:
        f.seek(-1, 2)
        if f.read(1) != b"\n":
            f.write(b"\n")


def _existing_keys(path: Path) -> set[tuple[str, str]]:
    keys: set[tuple[str, str]] = set()
    if not path.exists():
        return keys
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            keys.add(_key(row))
    return keys


def main(argv: list[str]) -> int:
    args = [a for a in argv if not a.startswith("--")]
    dedup = "--dedup" in argv
    if not args:
        print(__doc__)
        return 2

    new_path = Path(args[0])
    if not new_path.is_file():
        print(f"[ERROR] File export baru tidak ditemukan: {new_path}")
        return 1

    old_path = get_settings().image_index_csv_path
    if not old_path:
        print("[ERROR] File galeri lama tidak ditemukan (cek IMAGE_INDEX_CSV / data/).")
        return 1
    old_path = Path(old_path)

    if new_path.resolve() == old_path.resolve():
        print("[ERROR] File export baru sama dengan file galeri lama — batal.")
        return 1

    seen = _existing_keys(old_path) if dedup else set()
    if dedup:
        print(f"[info] {len(seen):,} baris sudah ada di galeri lama (untuk dedup).")

    _ensure_trailing_newline(old_path)

    appended = skipped_dup = skipped_bad = 0
    with open(new_path, newline="", encoding="utf-8-sig") as fin:
        reader = csv.DictReader(fin)
        if not reader.fieldnames or not REQUIRED_COLS.issubset(set(reader.fieldnames)):
            print(
                f"[ERROR] Kolom wajib tidak lengkap di file export. "
                f"Butuh minimal: {sorted(REQUIRED_COLS)}; ada: {reader.fieldnames}"
            )
            return 1
        cols = reader.fieldnames
        with open(old_path, "a", newline="", encoding="utf-8") as fout:
            writer = csv.DictWriter(fout, fieldnames=cols)
            for row in reader:
                pn = (row.get("part_number") or "").strip()
                emb = (row.get("embedding") or "").strip()
                if not pn or not emb:
                    skipped_bad += 1
                    continue
                if dedup and _key(row) in seen:
                    skipped_dup += 1
                    continue
                writer.writerow({c: row.get(c, "") for c in cols})
                if dedup:
                    seen.add(_key(row))
                appended += 1

    print(
        f"[OK] {appended:,} baris ditambahkan ke {old_path.name}"
        + (f" ({skipped_dup:,} duplikat dilewati)" if dedup else "")
        + (f" ({skipped_bad:,} baris kosong dilewati)" if skipped_bad else "")
    )
    print('     Klik "Reload Galeri" di menu admin (atau restart backend) agar terbaca.')
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
