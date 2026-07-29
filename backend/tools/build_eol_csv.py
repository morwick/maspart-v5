"""Baca arsip CSV EOL CNHTC mentah → baris kode kesalahan tambahan.

Dipakai `build_dtc_store.py` sebagai sumber KELIMA (setelah bosch / eol-html /
abs / scr / kartu). Bisa juga dijalankan sendiri untuk memeriksa panennya:

    cd backend
    python tools/build_eol_csv.py                 # ringkasan + contoh
    python tools/build_eol_csv.py --dir D:/lain   # folder lain

MENGAPA ADA
`build_eol_dtc.py` hanya membaca satu berkas — `artifact_panduan_teknisi.html`.
Di folder yang sama tersimpan CSV mentah yang **tak pernah dibaca kode mana pun**,
padahal memuat ribuan pasangan SPN/FMI, sebagian sudah lengkap Bahasa Indonesia.
Akibatnya kode seperti SPN 3013 / SPN 520290 dijawab "tidak ada di semua
database", padahal arsipnya kita miliki sejak awal.

⛔ Deterministik & OFFLINE: hanya `csv` dari pustaka standar. Tidak ada panggilan
LLM/jaringan di sini — sengaja, karena terjemahan Indonesia sudah tersedia di
kolom `*_ID` dan tak perlu ditebak ulang.

FAIL-SOFT: folder/berkas tidak ada → kembalikan daftar kosong, JANGAN melempar.
Arsip ini hidup di mesin pemilik (di luar repo), jadi build harus tetap berhasil
di lingkungan mana pun — termasuk CI.
"""
from __future__ import annotations

import argparse
import csv
import os
import re
import sys
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")

# Lokasi arsip; bisa ditimpa env agar mesin lain tak perlu mengubah kode.
DEFAULT_DIR = Path(os.environ.get("CNHTC_CSV_DIR") or "D:/CNHTC_Data")

_SPN_FMI_RE = re.compile(r"^SPN\s*(\d{1,6})\s*/\s*FMI\s*(\d{1,3})$", re.I)
_SPN_MAKS = 524287      # J1939: SPN 19-bit
_FMI_MAKS = 31          # J1939: FMI 5-bit

# Nama sistem di CSV English ("AbsTrouble", "RetarderTrouble") → unit kontrol.
_SISTEM_SUFFIX = re.compile(r"trouble$", re.I)


def _spn_fmi(kode: str) -> tuple[int | None, int | None]:
    m = _SPN_FMI_RE.match((kode or "").strip())
    if not m:
        return None, None
    spn, fmi = int(m.group(1)), int(m.group(2))
    if spn <= 0 or spn > _SPN_MAKS or fmi < 0 or fmi > _FMI_MAKS:
        return None, None
    return spn, fmi


def _bersih(v) -> str:
    """Rapikan sel CSV: buang newline & spasi ganda (isi aslinya sering
    ter-wrap dari PDF sehingga muncul '\n' di tengah kalimat)."""
    return " ".join(str(v or "").split())


def _unit_dari_sistem(s: str) -> str:
    s = _bersih(s)
    return _SISTEM_SUFFIX.sub("", s).strip() or "EOL"


def _baca(p: Path) -> list[dict]:
    if not p.exists():
        return []
    try:
        with p.open(encoding="utf-8-sig", newline="") as f:
            return list(csv.DictReader(f))
    except Exception as e:      # arsip rusak tak boleh menjatuhkan build
        print(f"   ⚠️ gagal membaca {p.name}: {e}")
        return []


def _dari_tambahan_id(rows: list[dict]) -> list[dict]:
    """CNHTC_EOL_KodeError_Tambahan_ID.csv — SUDAH Bahasa Indonesia. Sumber
    berkualitas tertinggi: deskripsi, penyebab, perbaikan, komponen lengkap."""
    out = []
    for r in rows:
        spn, fmi = _spn_fmi(r.get("DTC") or "")
        if spn is None:
            continue
        out.append({
            "unit": _bersih(r.get("Controller")) or "EOL",
            "kode": _bersih(r.get("DTC")).upper(),
            "spn": spn, "fmi": fmi,
            "deskripsi": _bersih(r.get("Deskripsi_ID")),
            "deskripsi_cn": _bersih(r.get("Deskripsi_CN")),
            "penyebab": _bersih(r.get("Penyebab_ID")),
            "perbaikan": _bersih(r.get("Perbaikan_ID")),
            "part": _bersih(r.get("Komponen_ID")),
        })
    return out


def _dari_english_full(rows: list[dict]) -> list[dict]:
    """CNHTC_EOL_KodeError_English_Full.csv — cakupan paling luas tapi hanya
    Inggris, dan banyak barisnya berdeskripsi kosong (dibuang di bawah)."""
    out = []
    for r in rows:
        spn, fmi = _spn_fmi(r.get("Kode") or "")
        if spn is None:
            continue
        out.append({
            "unit": _unit_dari_sistem(r.get("Sistem")),
            "kode": _bersih(r.get("Kode")).upper(),
            "spn": spn, "fmi": fmi,
            "deskripsi": "",                       # bukan Indonesia → jangan mengaku
            "deskripsi_cn": "",
            "label": _bersih(r.get("Deskripsi")),  # simpan sbg label Inggris
            "penyebab": "",
            "perbaikan": _bersih(r.get("Perbaikan")),
            "part": "",
        })
    return out


def kumpulkan(folder: Path | str | None = None) -> list[dict]:
    """Baris tambahan siap dipetakan ke skema store kanonik.

    Prioritas: berkas Indonesia MENANG atas Inggris untuk pasangan (spn,fmi)
    yang sama — kita lebih memilih baris berdeskripsi Indonesia daripada baris
    berlabel Inggris, karena itulah yang bisa dibaca pengguna di lapangan.
    Baris yang tak punya deskripsi MAUPUN label dibuang: menyimpannya hanya
    membuat store berkata 'kode ini terdaftar' tanpa bisa menjelaskan apa pun.
    """
    d = Path(folder or DEFAULT_DIR)
    if not d.exists():
        return []
    id_rows = _dari_tambahan_id(_baca(d / "CNHTC_EOL_KodeError_Tambahan_ID.csv"))
    en_rows = _dari_english_full(_baca(d / "CNHTC_EOL_KodeError_English_Full.csv"))

    peta: dict[tuple[int, int], dict] = {}
    for r in en_rows:                     # Inggris dulu …
        k = (r["spn"], r["fmi"])
        if k not in peta or len(r.get("label") or "") > len(peta[k].get("label") or ""):
            peta[k] = r
    for r in id_rows:                     # … lalu Indonesia menimpanya
        peta[(r["spn"], r["fmi"])] = r

    return [r for r in peta.values()
            if r.get("deskripsi") or r.get("label") or r.get("perbaikan")]


def main() -> int:
    ap = argparse.ArgumentParser(description="Panen kode kesalahan dari arsip CSV EOL.")
    ap.add_argument("--dir", default=str(DEFAULT_DIR), help=f"folder arsip (default {DEFAULT_DIR})")
    args = ap.parse_args()

    d = Path(args.dir)
    if not d.exists():
        print(f"⚠️ folder tidak ada: {d} — build akan melewati sumber CSV (fail-soft).")
        return 0
    rows = kumpulkan(d)
    ber_id = sum(1 for r in rows if r.get("deskripsi"))
    print(f"folder     : {d}")
    print(f"pasangan   : {len(rows)} (ber-deskripsi Indonesia: {ber_id})")
    unit = {}
    for r in rows:
        unit[r["unit"]] = unit.get(r["unit"], 0) + 1
    print("unit teratas:", ", ".join(f"{k}={v}" for k, v in
                                     sorted(unit.items(), key=lambda x: -x[1])[:8]))
    print("\ncontoh:")
    for r in rows[:6]:
        print(f"  SPN {r['spn']:>6} FMI {r['fmi']:>2} [{r['unit']:<10}] "
              f"{(r.get('deskripsi') or r.get('label') or '')[:60]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
