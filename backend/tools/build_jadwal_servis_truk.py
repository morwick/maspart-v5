# -*- coding: utf-8 -*-
"""
Bangun JADWAL SERVIS BERBASIS KM untuk truk Sinotruk dari PDF RESMI CNHTC.

Sumber : https://www.cnhtcgroup.com/wp-content/uploads/2024/11/
         HOWO-SERVICE-AND-MAINTENANCE-SCHEDULE.pdf
         → domain korporat CNHTC/Sinotruk sendiri (BUKAN unggahan pihak ketiga).
         Kontrol negatif saat verifikasi: nama berkas KARANGAN di direktori yang
         sama membalas HTTP 404, berkas asli 200 + application/pdf — jadi ini
         host berkas statis sungguhan, bukan halaman yang memalsukan sukses.

⚠️⚠️ CAKUPAN — JANGAN DIGENERALISASI. Judul dokumennya sendiri:
     "HOWO 371HP engine, axle type trucks".
Angka di sini SAH untuk keluarga itu (mis. gardan MCY13) dan ⛔ TIDAK boleh
disodorkan sebagai spesifikasi NX/SITRAK/V7X/HOMAN atau unit bergardan lain.
Ronde riset sebelumnya menemukan dokumen OEM lain memberi angka gardan yang
BERBEDA (25 L/22 L) untuk varian gardan lain — perbedaan itu nyata, bukan salah
ketik, dan justru sebab kenapa cakupan wajib disebut di setiap jawaban.

MENUTUP CELAH LAMA (audit log produksi):
  • "berapa liter oli yg dibutuhkan jika service 40.000" — dulu dijawab taksiran
    berlabel 'bukan data resmi';
  • kapasitas COOLANT level kendaraan — sebelumnya nihil di SIMS, EPC, & EOL AI;
  • kapasitas oli TRANSMISI & GARDAN — sebelumnya hanya klaim vendor.

Output: backend/app/services/jadwal_servis_truk.json.gz
        {sumber, url, cakupan, diambil, interval_km, item:[...], cairan:[...]}

Jalankan dari root repo:
    py backend/tools/build_jadwal_servis_truk.py
    py backend/tools/build_jadwal_servis_truk.py --dari-berkas <lokal.pdf>
"""
from __future__ import annotations

import argparse
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
OUT = SVC / "jadwal_servis_truk.json.gz"
URL = ("https://www.cnhtcgroup.com/wp-content/uploads/2024/11/"
       "HOWO-SERVICE-AND-MAINTENANCE-SCHEDULE.pdf")
CAKUPAN = "HOWO 371HP (engine & axle type trucks) — gardan MCY13"
SUMBER = "CNHTC/Sinotruk resmi (cnhtcgroup.com)"

# Kode kolom jadwal. 'Test' & 'T/I' ikut karena dipakai di dokumen.
_KODE = r"(?:T/I|Test|[IRATCL])"
_ARTI_KODE = {
    "I": "Periksa/bersihkan/perbaiki atau ganti bila perlu",
    "R": "Ganti", "A": "Setel", "T": "Kencangkan sesuai torsi",
    "C": "Bersihkan", "L": "Lumasi", "Test": "Uji",
    "T/I": "Kencangkan sesuai torsi / periksa",
}
# Kategori (huruf besar) di dokumen.
_KATEGORI = re.compile(
    r"^(ENGINE|COOLING|BRAKE SYSTEM|TRANSMISSION|CLUTCH|STEERING|SUSPENSION|"
    r"WHEELS & TYRES|ELECTRICAL|CAB|UNDER CARRIAGE AND REAR AXLE|CHASSIS|"
    r"AIR SYSTEM|FRONT AXLE|GENERAL)\s*$", re.I)


def _teks(pdf_bytes: bytes) -> str:
    try:
        from pypdf import PdfReader
    except ImportError:                     # pragma: no cover
        from PyPDF2 import PdfReader        # type: ignore
    import io
    rd = PdfReader(io.BytesIO(pdf_bytes))
    t = "\n".join((p.extract_text() or "") for p in rd.pages)
    return re.sub(r"[ \t]+", " ", t)


def parse(teks: str) -> tuple[list[int], list[dict]]:
    """(interval_km, item[]). Item = {kategori, item, kode[12], catatan}."""
    m = re.search(r"SERVICE INTERVAL every KMs \(x1000\)((?:\s+\d+){6,})", teks)
    if not m:
        raise SystemExit("⛔ baris SERVICE INTERVAL tak ditemukan — format berubah?")
    interval = [int(x) * 1000 for x in m.group(1).split()]

    baris_re = re.compile(rf"^(?P<item>.+?)\s+(?P<kode>(?:{_KODE}\s+){{{len(interval)-1}}}{_KODE})"
                          rf"(?P<sisa>.*)$")
    item: list[dict] = []
    kategori = ""
    buf: list[str] = []
    # Catatan bisa MELUAP ke baris berikutnya (mis. Coolant) → digabung.
    for raw in teks.splitlines():
        b = raw.strip()
        if not b:
            continue
        if _KATEGORI.match(b):
            kategori = b.upper()
            buf = []
            continue
        mm = baris_re.match(b)
        if mm:
            kode = mm.group("kode").split()
            item.append({"kategori": kategori,
                         "item": " ".join(mm.group("item").split()),
                         "kode": kode,
                         "catatan": " ".join(mm.group("sisa").split())})
            buf = item[-1]["catatan"] and [] or []
            continue
        # baris lanjutan catatan (bukan header, bukan item ber-kode)
        if item and not re.match(r"^[A-Z ,&]+$", b):
            item[-1]["catatan"] = (item[-1]["catatan"] + " " + b).strip()
    return interval, item


# ⚠️ WAJIB menangkap SELURUH angka liter beserta KONTEKSNYA. Versi pertama hanya
# mengambil angka PERTAMA, sehingga:
#   "40-45Liter"                       → terbaca 45   (rentang hilang)
#   "18L(Middle axle) & 14.5L(Rear axle)" → terbaca 18   (gardan BELAKANG hilang!)
# Kehilangan kedua berbahaya: mengisi gardan belakang 18 L (bukan 14,5 L) =
# overfill. Karena itu tiap angka disimpan bersama teks di sekitarnya.
# Pemisah bisa '-' (rentang '40-45Liter') ATAU '/' (varian '12/12.5L' = tanpa/dengan
# PTO). Keduanya wajib: memakai '-' saja membuat angka 12 pada '12/12.5L' hilang.
# Aman terhadap 'GL-5 85W/90' karena angka WAJIB diikuti huruf L.
_RE_LITER = re.compile(r"(\d+(?:[.,]\d+)?)\s*(?:[-/]\s*(\d+(?:[.,]\d+)?)\s*)?L(?:iter)?\b", re.I)
_RE_GANTI = re.compile(r"replace\s+([\d,\.]+)\s*km", re.I)


def _angka(s: str) -> float:
    return float(s.replace(",", "."))


def cairan_dari(item: list[dict]) -> list[dict]:
    """Item yang catatannya memuat KAPASITAS (liter) → entri cairan terstruktur.

    `kapasitas` = daftar {liter | liter_min/liter_maks, konteks} — konteks diambil
    dari teks tepat SESUDAH angka (mis. '(Middle axle)') supaya pemakai tahu
    angka mana untuk apa."""
    out = []
    for it in item:
        c = " ".join((it.get("catatan") or "").split())
        kap = []
        for m in _RE_LITER.finditer(c):
            ekor = c[m.end():m.end() + 34]
            konteks = ""
            k = re.match(r"\s*\(([^)]{1,30})\)", ekor)
            if k:
                konteks = k.group(1).strip()
            e: dict = ({"liter_min": _angka(m.group(1)), "liter_maks": _angka(m.group(2))}
                       if m.group(2) else {"liter": _angka(m.group(1))})
            if konteks:
                e["konteks"] = konteks
            kap.append(e)
        if not kap:
            continue
        g = _RE_GANTI.search(c)
        e = {"item": it["item"], "kategori": it["kategori"],
             "kapasitas": kap, "spesifikasi": c}
        if g:
            e["ganti_tiap_km"] = int(g.group(1).replace(",", "").replace(".", ""))
        out.append(e)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dari-berkas", default="")
    a = ap.parse_args()

    if a.dari_berkas:
        data = Path(a.dari_berkas).read_bytes()
        print(f"[sumber] berkas lokal {a.dari_berkas}")
    else:
        print(f"[unduh] {URL}")
        r = requests.get(URL, timeout=120,
                         headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
        if r.status_code != 200 or "pdf" not in (r.headers.get("Content-Type") or ""):
            print(f"⛔ GAGAL unduh (HTTP {r.status_code}) — berkas lama TIDAK ditimpa.")
            return 2
        data = r.content
    teks = _teks(data)
    interval, item = parse(teks)
    cairan = cairan_dari(item)
    print(f"[parse] interval: {interval}")
    print(f"[parse] {len(item)} item, {len(cairan)} entri berkapasitas")

    # ── validasi: format berubah = lebih baik GAGAL daripada menulis data cacat.
    if len(interval) != 12 or len(item) < 25 or len(cairan) < 5:
        raise SystemExit(f"⛔ hasil parse mencurigakan (interval={len(interval)}, "
                         f"item={len(item)}, cairan={len(cairan)}) — dibatalkan.")
    wajib = ("Coolant", "Transmission oil", "Engine oil", "Steering fluid", "Clutch fluid")
    kurang = [w for w in wajib if not any(w.lower() in i["item"].lower() for i in item)]
    if kurang:
        raise SystemExit(f"⛔ item wajib hilang: {kurang} — format PDF berubah?")
    for it in item:
        if len(it["kode"]) != len(interval):
            raise SystemExit(f"⛔ jumlah kode ≠ interval pada '{it['item']}'")

    payload = {
        "sumber": SUMBER, "url": URL, "cakupan": CAKUPAN,
        "diambil": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "arti_kode": _ARTI_KODE,
        "interval_km": interval,
        "item": item,
        "cairan": cairan,
        "peringatan": ("Dokumen RESMI CNHTC untuk HOWO 371HP (gardan MCY13). "
                       "⛔ JANGAN digeneralisasi ke NX/SITRAK/V7X/HOMAN atau unit "
                       "bergardan lain — dokumen OEM lain memberi angka gardan "
                       "yang berbeda untuk varian lain."),
    }
    write_json_gz(OUT, payload)
    print(f"TULIS {OUT}")
    for c in cairan:
        rinci = ", ".join(
            (f"{k['liter_min']}-{k['liter_maks']} L" if "liter_maks" in k
             else f"{k['liter']} L") + (f" ({k['konteks']})" if k.get("konteks") else "")
            for k in c["kapasitas"])
        print(f"   {c['item'][:32]:34s} {rinci}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
