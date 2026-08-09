# -*- coding: utf-8 -*-
"""
Panen tabel kode kesalahan SITRAK (dataset KOMUNITAS) → tabel per-sumber lokal.

Sumber : https://github.com/STAS63-bit/sitrak-error-codes  (branch `master`)
Lisensi: **CC BY 4.0** — bebas dipakai termasuk komersial, WAJIB mencantumkan
         atribusi ke megadata.pro. Atribusi ikut disimpan di payload keluaran
         dan WAJIB disampaikan asisten saat memakai baris ber-sumber "sitrak".

KENAPA dipanen (audit 2026-08-08, terverifikasi sendiri):
  8.042 record / 6.640 pasangan (SPN,FMI) unik; koleksi kita 4.615 → **3.316
  pasangan BARU**. Tiga kode yang BERULANG GAGAL di log produksi ada di sini dan
  belum kita punya: SPN 764 FMI 2 & SPN 744 FMI 5 (retarder Voith — ditanya
  andreas & beni berkali-kali), SPN 520290 FMI 20 (Bosch P060C).
  Sistemnya cocok dgn armada: Bosch, KNORR EBS, WABCO EBS, ZF/AMT, OGP, ECAS.

⚠️ MUTU — sampaikan apa adanya, JANGAN disamakan dgn sumber resmi pabrik:
  • ~95% deskripsi hanya BAHASA RUSIA (Inggris cuma 2.441/8.042 ≈ 30%) →
    disimpan di `deskripsi_ru`; penerjemahan dilakukan saat MENJAWAB, bukan di
    sini (⛔ builder ini TIDAK memanggil API model apa pun — aturan pemilik).
  • Sebagian SPN memakai deskripsi yang SAMA persis untuk banyak FMI (mis. SPN
    520290) → arti per-FMI tak selalu dibedakan. Jangan diperlakukan sebagai
    lembar diagnosa resmi.
  • Penerbitnya usaha diagnosa truk (bukan pabrikan, bukan tinjauan sejawat).
    Metadata GitHub menandai lisensi `NOASSERTION` meski berkas LICENSE jelas
    menyebut CC BY 4.0 — dicatat agar tak ada kejutan di kemudian hari.

Output: backend/app/services/sitrak_dtc.json.gz
        {sumber, url, lisensi, atribusi, diambil, jumlah, rows:[...]}
        Digabung ke store kanonik oleh build_dtc_store.py (HANYA pasangan yang
        belum terwakili sumber resmi — sumber resmi selalu menang).

Jalankan dari root repo:
    py backend/tools/build_sitrak_dtc.py
    py backend/tools/build_sitrak_dtc.py --dari-berkas <path.json>   # tanpa jaringan
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import requests

for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.knowledge_util import write_json_gz  # noqa: E402

SVC = Path(__file__).resolve().parents[1] / "app" / "services"
OUT = SVC / "sitrak_dtc.json.gz"

# ⚠️ branch-nya `master`, BUKAN `main` (raw .../main/... balas 404).
URL = ("https://raw.githubusercontent.com/STAS63-bit/sitrak-error-codes/"
       "master/error-codes.json")
ATRIBUSI = "megadata.pro — SITRAK Error Codes Database (CC BY 4.0)"
LISENSI = "CC BY 4.0"


def _int(v):
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return None


def normalisasi(mentah: list) -> list[dict]:
    """Record hulu → baris ramping & deterministik. Baris tanpa SPN dibuang:
    tanpa SPN ia tak bisa dijangkau pencarian kita sama sekali."""
    out: list[dict] = []
    lihat: set = set()
    for r in mentah:
        if not isinstance(r, dict):
            continue
        spn, fmi = _int(r.get("spn")), _int(r.get("fmi"))
        if spn is None:
            continue
        ru = " ".join(str(r.get("description_ru") or "").split())
        en = " ".join(str(r.get("description_en") or "").split())
        kode = str(r.get("dtc") or "").strip().upper()
        sistem = " ".join(str(r.get("system") or "").split()) or "SITRAK"
        kunci = (spn, fmi, kode, sistem, ru[:60])
        if kunci in lihat:
            continue
        lihat.add(kunci)
        out.append({"spn": spn, "fmi": fmi, "kode": kode,
                    "sistem": sistem, "deskripsi_en": en, "deskripsi_ru": ru})
    # Urutan deterministik → keluaran byte-stable antar build.
    out.sort(key=lambda x: (x["spn"], x["fmi"] if x["fmi"] is not None else -1,
                            x["sistem"], x["kode"], x["deskripsi_ru"]))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dari-berkas", default="", help="pakai JSON lokal, tanpa unduh")
    a = ap.parse_args()

    if a.dari_berkas:
        mentah = json.loads(Path(a.dari_berkas).read_text(encoding="utf-8"))
        print(f"[sumber] berkas lokal: {a.dari_berkas}")
    else:
        print(f"[unduh] {URL}")
        r = requests.get(URL, timeout=180)
        if r.status_code != 200:
            print(f"⛔ GAGAL unduh (HTTP {r.status_code}) — tabel TIDAK ditulis. "
                  "Store kanonik tetap memakai tabel lama.")
            return 2
        mentah = r.json()
    if not isinstance(mentah, list) or not mentah:
        print("⛔ bentuk data hulu tak terduga (bukan list / kosong) — dibatalkan.")
        return 2

    rows = normalisasi(mentah)
    ber_en = sum(1 for x in rows if x["deskripsi_en"])
    pasangan = {(x["spn"], x["fmi"]) for x in rows if x["fmi"] is not None}
    print(f"[normalisasi] {len(mentah)} record hulu → {len(rows)} baris ber-SPN "
          f"({len(pasangan)} pasangan SPN/FMI unik; ber-Inggris {ber_en})")
    if len(rows) < 1000:
        print("⛔ hasil mencurigakan sedikit (<1000) — dibatalkan agar tabel lama "
              "tidak tertimpa data separuh.")
        return 2

    payload = {
        "sumber": "sitrak",
        "url": "https://github.com/STAS63-bit/sitrak-error-codes",
        "lisensi": LISENSI,
        "atribusi": ATRIBUSI,
        "diambil": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "jumlah": len(rows),
        "rows": rows,
    }
    write_json_gz(OUT, payload)
    print(f"TULIS {OUT} ({len(rows)} baris)")
    print(f"ATRIBUSI WAJIB: {ATRIBUSI}")
    print("Lanjutkan: py backend/tools/build_dtc_store.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
