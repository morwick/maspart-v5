"""
PROFIL KLAIM PER KOMPONEN — pengetahuan turunan dari 2.155 klaim garansi nyata:
untuk satu part/komponen, BAGAIMANA ia biasanya rusak, di KM berapa, dan part
apa yang NYATA-NYATA diganti BERSAMAAN dengannya.

Tetangganya `warranty_kasus` menjawab "keluhan X → part apa yang dipasang"
(kasus per kasus). Modul ini menjawab kebalikannya, teragregasi per komponen:
  • "rubber mount suspensi sering rusak kenapa?"  → 148 retak / 120 patah dari
    398 klaim, km median 15.573.
  • "kalau ganti rubber mount biasanya bareng apa?" → bracket per belakang (30×),
    V-stay (14×), bearing balance shaft (12×).
Dipakai oleh `diagnosa_terpandu` (mengisi bukti tiap hipotesis) dan tool
`part_klaim_terkait` (ditanya langsung).

Sengaja DIHITUNG SAAT MUAT dari berkas klaim yang sudah ada — bukan berkas
data baru yang harus di-scp & dirawat terpisah; 1.785 klaim sah terindeks
< 50 ms. Kunci cache = (jumlah klaim, tanggal dibuat) mengikuti pola
`warranty_kasus._index()`.

⛔ Jebakan yang DITURUNKAN dari warranty_kasus (jangan dilonggarkan):
  1. Klaim DIBATALKAN (`s-ro-status-zf`) dibuang — klaim yang DITOLAK bukan
     bukti kerusakan.
  2. Nama part ditulis staf dalam BAHASA INGGRIS; kueri montir Indonesia →
     WAJIB lewat `sinonim.expand_query` (kamus kurasi + gejala_map). Tidak ada
     jalur pencocokan baru: kata kunci dicocokkan sebagai substring nama part.
  3. Angka yang keluar = AGREGAT (jumlah, median, mode). Tidak ada nomor WO,
     nomor rangka, atau biaya — jadi aman disajikan lintas peran; detail WO
     tetap milik tool bergerbang garansi.
"""
from __future__ import annotations

import re
import statistics
import threading
from collections import Counter, defaultdict

from . import sinonim, warranty_kasus
from .knowledge_util import norm_pn

# Kata kunci lebih pendek dari ini terlalu ambigu sebagai substring nama part
# ('oil' cocok di 'oil filter', 'oil cooler', 'oil pump', 'engine oil'…).
_MIN_KATA = 4

# Kata umum di nama part yang tak boleh jadi satu-satunya dasar kecocokan.
_KATA_UMUM = {"assembly", "assy", "component", "bolt", "nut", "washer", "gasket",
              "left", "right", "front", "rear", "upper", "lower", "type", "improved"}

_BATAS_KOMPONEN = 8       # nama part berbeda yang dilaporkan per kueri
_BATAS_BERSAMA = 6        # part yang diganti bersamaan per komponen
_BATAS_MODE = 4
_BATAS_PN = 4
_BATAS_JASA = 3

_lock = threading.Lock()
_cache: dict = {}


def _nama(p: dict) -> str:
    return re.sub(r"\s+", " ", (p.get("nama") or "").strip().lower())


def _index() -> dict:
    """Profil per nama part (lowercase). Dibangun sekali per versi berkas."""
    d = warranty_kasus.data()
    klaim = d.get("klaim") or []
    kunci = (len(klaim), d.get("dibuat"))
    snap = _cache
    if snap.get("kunci") == kunci:
        return snap

    prof: dict[str, dict] = defaultdict(lambda: {
        "klaim": 0, "km": [], "mode": Counter(), "bersama": Counter(),
        "pn": Counter(), "jasa": Counter(), "berulang": 0, "nama_cn": Counter(),
        "pn_bersama": {},
    })
    for k in klaim:
        if k.get("status_code") == warranty_kasus._STATUS_BATAL:      # jebakan #1
            continue
        parts = k.get("part") or []
        nama_set = {_nama(p) for p in parts if _nama(p)}
        pn_nama = {_nama(p): p.get("pn") for p in parts if _nama(p) and p.get("pn")}
        km = k.get("km")
        for p in parts:
            n = _nama(p)
            if not n:
                continue
            s = prof[n]
            s["klaim"] += 1
            if p.get("pn"):
                s["pn"][norm_pn(p["pn"])] += 1
            if p.get("nama_cn"):
                s["nama_cn"][p["nama_cn"]] += 1
            if isinstance(km, (int, float)) and km > 0:
                s["km"].append(int(km))
            if p.get("mode_gagal"):
                s["mode"][p["mode_gagal"]] += 1
            if p.get("klaim_berulang"):
                s["berulang"] += 1
            for j in k.get("jasa") or []:
                if j.get("nama"):
                    s["jasa"][j["nama"]] += 1
            for lain in nama_set:
                if lain != n:
                    s["bersama"][lain] += 1
                    if lain in pn_nama:
                        s["pn_bersama"].setdefault(lain, pn_nama[lain])
    baru = {"kunci": kunci, "prof": dict(prof), "total": sum(1 for k in klaim
            if k.get("status_code") != warranty_kasus._STATUS_BATAL)}
    with _lock:
        globals()["_cache"] = baru
    return baru


def tersedia() -> bool:
    return warranty_kasus.tersedia()


def _kata_kunci(kueri: str) -> list[str]:
    """Kueri (Indonesia/Inggris/PN) → daftar kata kunci Inggris untuk dicocokkan
    ke nama part. Lewat kamus sinonim (jebakan #2), lalu kata mentah kueri
    yang cukup panjang (montir yang sudah menyebut istilah Inggris)."""
    q = (kueri or "").strip()
    if not q:
        return []
    terms, _ = sinonim.expand_query(q)
    kws: list[str] = []
    for t in terms[1:]:                      # [0] = kueri asli
        t = t.strip().lower()
        if t and t not in kws:
            kws.append(t)
    for w in re.findall(r"[a-z][a-z\-]+", q.lower()):
        if len(w) >= _MIN_KATA and w not in _KATA_UMUM and w not in kws:
            kws.append(w)
    return kws


def _cocok(nama: str, kws: list[str]) -> int:
    """Skor kecocokan nama part terhadap kata kunci: frasa multi-kata bernilai
    lebih tinggi; kata umum saja tidak dihitung."""
    skor = 0
    for kw in kws:
        if kw in _KATA_UMUM or len(kw) < _MIN_KATA:
            continue
        if re.search(r"(?<![a-z])" + re.escape(kw) + r"(?![a-z])", nama):
            skor += 2 if " " in kw else 1
    return skor


def _ringkas(nama: str, s: dict) -> dict:
    km = s["km"]
    return {
        "nama": nama,
        "nama_cn": (s["nama_cn"].most_common(1) or [("", 0)])[0][0] or None,
        "pn": [pn for pn, _ in s["pn"].most_common(_BATAS_PN)],
        "klaim": s["klaim"],
        "km_median": int(statistics.median(km)) if km else None,
        "km_min": min(km) if km else None,
        "km_maks": max(km) if km else None,
        "mode_rusak": [{"mode": m, "jumlah": n} for m, n in s["mode"].most_common(_BATAS_MODE)],
        "klaim_berulang": s["berulang"],
        "diganti_bersama": [
            {"nama": lain, "pn": s["pn_bersama"].get(lain), "jumlah": n,
             "persen": round(100 * n / max(s["klaim"], 1))}
            for lain, n in s["bersama"].most_common(_BATAS_BERSAMA)
        ],
        "jasa": [j for j, _ in s["jasa"].most_common(_BATAS_JASA)],
    }


def profil(kueri: str, batas: int = _BATAS_KOMPONEN) -> dict:
    """Profil klaim untuk part/komponen yang cocok dengan `kueri` (nama
    Indonesia/Inggris atau PN). Diurut dari yang paling banyak klaimnya."""
    q = (kueri or "").strip()
    if not q:
        return {"found": False, "catatan": "Sebutkan nama part atau PN-nya dulu."}
    idx = _index()
    prof = idx["prof"]
    if not prof:
        return {"found": False, "_cek_tak_lengkap": True,
                "catatan": "Dataset klaim garansi belum tersedia di server."}

    pn_q = norm_pn(q)
    kandidat: list[tuple[int, str]] = []
    if pn_q and re.search(r"\d", pn_q):
        for nama, s in prof.items():
            if pn_q in s["pn"]:
                kandidat.append((99, nama))
    kws = _kata_kunci(q)
    if not kandidat and kws:
        for nama, s in prof.items():
            sk = _cocok(nama, kws)
            if sk:
                kandidat.append((sk * 1000 + s["klaim"], nama))
    kandidat.sort(reverse=True)
    if not kandidat:
        return {"found": False, "kueri": q, "kata_kunci": kws[:6],
                "dari_total_klaim": idx["total"],
                "catatan": ("Tidak ada klaim garansi armada yang mengganti part ini — "
                            "artinya BELUM PERNAH diklaim, bukan berarti tak pernah rusak. "
                            "Jangan mengarang mode kerusakan.")}

    komponen = [_ringkas(nama, prof[nama]) for _, nama in kandidat[:batas]]
    total_klaim = sum(c["klaim"] for c in komponen)
    return {
        "found": True,
        "kueri": q,
        "kata_kunci": kws[:6],
        "jumlah_komponen_cocok": len(kandidat),
        "total_klaim_komponen": total_klaim,
        "dari_total_klaim": idx["total"],
        "komponen": komponen,
        "ringkasan": "; ".join(
            f"{c['nama']}: {c['klaim']} klaim, km median {c['km_median'] or '-'}, "
            f"mode {', '.join(m['mode'] for m in c['mode_rusak'][:2]) or '-'}"
            for c in komponen[:3]),
        "catatan": (
            "Profil dari klaim garansi SIMS armada sendiri (klaim dibatalkan sudah dibuang). "
            "'mode_rusak' = mode kegagalan yang dicatat mekanik; 'km_median' = km unit saat "
            "klaim; 'diganti_bersama' = part LAIN yang diganti dalam klaim yang SAMA "
            "(persen dari klaim komponen ini) — tawarkan sebagai 'biasanya sekalian diganti', "
            "bukan kewajiban. Angka = frekuensi klaim, BUKAN probabilitas rusak semua unit. "
            "PN di sini adalah PN yang pernah dipasang; untuk unit tertentu tetap cek "
            "kecocokannya ke rangka (cari_part_di_unit) sebelum menawarkan."),
    }


def profil_untuk_kata(kata: list[str], batas: int = 3) -> list[dict]:
    """Versi ringan untuk diagnosa_terpandu: beberapa kata kunci Inggris →
    komponen terbanyak klaimnya (tanpa membungkus catatan)."""
    idx = _index()
    prof = idx["prof"]
    kws = [k.strip().lower() for k in kata if k and k.strip()]
    if not prof or not kws:
        return []
    kandidat = []
    for nama, s in prof.items():
        sk = _cocok(nama, kws)
        if sk:
            kandidat.append((sk * 1000 + s["klaim"], nama))
    kandidat.sort(reverse=True)
    return [_ringkas(nama, prof[nama]) for _, nama in kandidat[:batas]]
