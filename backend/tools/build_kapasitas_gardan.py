# -*- coding: utf-8 -*-
"""
Panen KAPASITAS OLI GARDAN/TRANSMISI per MODEL + TORSI MUR RODA dari dokumen
RESMI Sinotruk (en.sinotruk.com).

KENAPA: dataset `jadwal_servis_truk` (PDF jadwal CNHTC) hanya mencakup SATU
gardan — MCY13. Batas itu nyata dan sudah kami umumkan di tool. Dokumen ini
menghapusnya: tabel per-MODEL gardan dari CNHTC International Co., Ltd
(售后服务部 / After-Sales Service Dept).

SUMBER (keduanya domain RESMI `en.sinotruk.com`; kontrol negatif: nama berkas
karangan di direktori sama → HTTP 404):
  A. Assembly Maintenance Recommendation Table (49 hlm, CN/EN)
     .../attachDir/africadepartment-chinese/2024/03/2024032717000596487.pdf
  B. HOWO Series Vehicle Driver's Manual 4th Version (358 hlm, EN)
     .../attachDir/feizhoufayu/2024/03/2024032211020748078.pdf
     → torsi mur roda "550-600Nm" + kencangkan ULANG setelah ±50 km.

⚠️ NOTASI KAPASITAS: sebagian model ditulis sebagai PENJUMLAHAN, mis.
`17+2×2=21L（中桥）` = 17 L gardan + 2 L × 2 hub = **21 L total**. Angka yang
disimpan adalah HASILNYA (21), rumus aslinya tetap disimpan di `rumus` supaya
bisa diperiksa manusia. Mengambil angka pertama (17) = kurang isi 4 liter.
中桥 = poros TENGAH, 后桥 = poros BELAKANG, 前桥 = poros DEPAN.

VALIDASI SILANG WAJIB: MCY13 harus keluar 18 L (tengah) & 14,5 L (belakang) —
angka yang SUDAH kami punya dari dokumen CNHTC yang BERBEDA. Bila tak cocok,
build DIBATALKAN: berarti parser salah baca tabel, dan kapasitas oli yang salah
merusak gardan.

Output: backend/app/services/kapasitas_gardan.json.gz

Jalankan dari root repo:  py backend/tools/build_kapasitas_gardan.py
"""
from __future__ import annotations

import argparse
import io
import re
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
OUT = SVC / "kapasitas_gardan.json.gz"
BASE = "https://en.sinotruk.com/eportal/attachDir/"
URL_TABEL = BASE + "africadepartment-chinese/2024/03/2024032717000596487.pdf"
URL_MANUAL = BASE + "feizhoufayu/2024/03/2024032211020748078.pdf"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"}
SUMBER = "CNHTC/Sinotruk resmi (en.sinotruk.com) — After-Sales Service Dept"

# ⛔ HANYA GARDAN. Transmisi SENGAJA TIDAK dipanen di sini: percobaan pertama
# memberi HW16709XST = 9,5 L padahal 9,5 L itu milik HW13709XST (hlm 23) —
# salah-pasang SENYAP antar model gearbox. Kapasitas gearbox yang salah sama
# merusaknya dengan gardan, jadi lebih baik tak ada daripada salah. Kapasitas
# gearbox untuk HOWO 371 tetap tersedia dari dataset jadwal_servis_truk (12-12,5 L).
_MODEL = re.compile(r"\b(MCY1[13]|AC1[26]|AC26|ST1[36]|HC16|HT?457|HW1[26])\b")
_POROS = {"中桥": "poros tengah", "后桥": "poros belakang", "前桥": "poros depan"}
# ⚠️ Label poros bisa TERPOTONG SPASI oleh pemenggalan baris PDF ('后 桥'),
# sehingga AC16 sempat kehilangan penanda poros belakangnya → \s* wajib.
_POROS_RE = "|".join(r"\s*".join(k) for k in _POROS)
# '17+2×2=21L' → tangkap rumus + HASIL; atau '18L' polos.
_KAP = re.compile(r"((?:\d+(?:[.,]\d+)?\s*\+\s*\d+\s*[×x*]\s*\d+\s*=\s*)?)"
                  rf"(\d+(?:[.,]\d+)?)\s*L(?:\s*[（(]\s*({_POROS_RE})\s*[)）])?")


def _teks_halaman(pdf: bytes) -> list[str]:
    import pymupdf
    d = pymupdf.open(stream=io.BytesIO(pdf), filetype="pdf")
    return [re.sub(r"\s+", " ", pg.get_text()) for pg in d]


def _unduh(url: str) -> bytes:
    r = requests.get(url, headers=UA, timeout=240)
    if r.status_code != 200 or "pdf" not in (r.headers.get("Content-Type") or ""):
        raise SystemExit(f"⛔ GAGAL unduh {url} (HTTP {r.status_code}) — dibatalkan.")
    return r.content


def parse_gardan(halaman: list[str]) -> list[dict]:
    """Satu entri per MODEL. Halaman dokumen ini = satu model per tabel."""
    hasil: dict[str, dict] = {}
    for teks in halaman:
        m = _MODEL.search(teks)
        # Model WAJIB muncul di AWAL halaman: dokumen ini satu model per tabel,
        # dan kode yang menyempil jauh di badan teks (mis. daftar rujukan) pernah
        # membuat kapasitas milik model LAIN tercatat atas nama model ini.
        if not m or m.start() > 60 or "齿轮油" not in teks:   # 齿轮油 = oli roda gigi
            continue
        model = m.group(1)
        # Kapasitas hanya diambil dari bagian AWAL halaman (sebelum blok
        # rekomendasi oli premium yang penuh angka km & takkan relevan).
        potong = teks.split("**")[0]
        kap = []
        for mm in _KAP.finditer(potong):
            rumus, nilai, poros = mm.group(1).strip(), mm.group(2), mm.group(3)
            e: dict = {"liter": float(nilai.replace(",", "."))}
            if poros:
                # Kunci dinormalkan: teks tertangkap bisa '后 桥' (terpotong baris).
                e["poros"] = _POROS[re.sub(r"\s+", "", poros)]
            if rumus:
                e["rumus"] = (rumus + nilai + "L").replace(" ", "")
            kap.append(e)
        if not kap:
            continue
        e = hasil.setdefault(model, {"model": model, "kapasitas": [],
                                     "jenis": "gardan"})
        for k in kap:
            if k not in e["kapasitas"]:
                e["kapasitas"].append(k)
        if "GL-5" in potong and "oli" not in e:
            e["oli"] = "GL-5 85W-90 (Q/ZZ 21040 / ASTM D7450)"
    return sorted(hasil.values(), key=lambda x: x["model"])


def parse_torsi(teks: str) -> list[dict]:
    out = []
    for m in re.finditer(r"(\d{2,4})\s*[-–~]\s*(\d{2,4})\s*Nm", teks, re.I):
        kal = re.sub(r"\s+", " ", teks[max(0, m.start() - 200):m.start() + 200])
        if re.search(r"wheel|nut", kal, re.I):
            out.append({"item": "Mur roda (wheel nut)",
                        "nm_min": int(m.group(1)), "nm_maks": int(m.group(2)),
                        "catatan": kal.strip()})
            break
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tabel", default="", help="PDF tabel lokal (tanpa unduh)")
    ap.add_argument("--manual", default="", help="PDF manual lokal (tanpa unduh)")
    a = ap.parse_args()

    tabel = Path(a.tabel).read_bytes() if a.tabel else _unduh(URL_TABEL)
    manual = Path(a.manual).read_bytes() if a.manual else _unduh(URL_MANUAL)

    gardan = parse_gardan(_teks_halaman(tabel))
    torsi = parse_torsi(" ".join(_teks_halaman(manual)))
    print(f"[parse] {len(gardan)} model gardan/transmisi, {len(torsi)} entri torsi")
    for g in gardan:
        rinci = ", ".join(
            f"{k['liter']} L" + (f" ({k['poros']})" if k.get("poros") else "")
            + (f" [{k['rumus']}]" if k.get("rumus") else "") for k in g["kapasitas"])
        print(f"   {g['model']:12s} {g['jenis']:10s} {rinci}")
    for t in torsi:
        print(f"   TORSI {t['item']}: {t['nm_min']}-{t['nm_maks']} Nm")

    # ── VALIDASI SILANG: MCY13 wajib 18 (tengah) & 14,5 (belakang) ──
    mcy = next((g for g in gardan if g["model"] == "MCY13"), None)
    if not mcy:
        raise SystemExit("⛔ MCY13 tak terbaca — parser gagal, dibatalkan.")
    peta = {k.get("poros"): k["liter"] for k in mcy["kapasitas"]}
    if peta.get("poros tengah") != 18.0 or peta.get("poros belakang") != 14.5:
        raise SystemExit(f"⛔ MCY13 = {peta} ≠ 18/14,5 L dari dokumen CNHTC lain. "
                         "Parser salah baca tabel — DIBATALKAN (kapasitas salah "
                         "merusak gardan).")
    print("   ✅ validasi silang MCY13 = 18 L (tengah) / 14,5 L (belakang) — cocok")
    # Ambang 4: dokumen memuat 5 model gardan setelah transmisi dikeluarkan
    # (MCY11, MCY13, AC16, AC26, HW16). Penjaga utamanya tetap validasi silang
    # MCY13 di atas — ambang ini hanya menangkap parser yang gagal total.
    if len(gardan) < 4 or not torsi:
        raise SystemExit(f"⛔ hasil terlalu sedikit (gardan={len(gardan)}, "
                         f"torsi={len(torsi)}) — dibatalkan.")

    payload = {
        "sumber": SUMBER,
        "url_tabel": URL_TABEL, "url_manual": URL_MANUAL,
        "diambil": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "gardan": gardan, "torsi": torsi,
        "peringatan": ("Kapasitas per MODEL gardan/transmisi — cocokkan dengan model "
                       "gardan unit (lihat cek_kendaraan). Notasi 'rumus' seperti "
                       "17+2×2=21L berarti gardan + 2 hub; angka 'liter' SUDAH total. "
                       "Dokumen menyebut angka ini ACUAN — pemakaian nyata tetap "
                       "dicek lewat dipstick/lubang isi."),
    }
    write_json_gz(OUT, payload)
    print(f"TULIS {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
