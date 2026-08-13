"""
KASUS SERUPA — jembatan KELUHAN LAPANGAN → PART yang benar-benar dipasang,
dari 2.155 klaim garansi nyata armada sendiri (dataset offline).

Bedanya dengan tetangganya:
  • `gejala_map.json` (sinonim.py) = hasil ENUMERASI keluhan lalu divalidasi ke
    katalog — tebakan terdidik, tanpa PN, tanpa frekuensi, tanpa biaya.
  • `part_fast_moving` = laris berdasarkan PENJUALAN/EPC, level MODEL.
  • DI SINI = bukti KERUSAKAN: keluhan nyata → PN yang nyata-nyata dipasang,
    berapa kali, rusaknya bagaimana, di km berapa, biayanya berapa.
Tool live `riwayat_klaim`/`detail_klaim` hanya bisa membuka SATU WO sekali
jalan; agregasi lintas armada memang belum pernah ada — itu celah yang ditutup
modul ini. Sumber data: `data/warranty/warranty_klaim.json.gz`
(builder `backend/tools/build_warranty_dataset.py`).

⛔ JEBAKAN yang sudah diukur (jangan dilonggarkan tanpa ukur ulang):
  1. **Klaim DIBATALKAN wajib dibuang.** 610 dari 3.477 baris part (18%) berasal
     dari klaim berstatus `s-ro-status-zf`. Ikut dihitung → angka "part paling
     sering rusak" melar ±21% dan memasukkan klaim yang justru DITOLAK.
  2. **Keluhan ditulis staf dealer dalam BAHASA INGGRIS**, montir bertanya dalam
     Bahasa Indonesia. Pencocokan mentah pasti meleset → kueri WAJIB lewat
     `sinonim.expand_query` (kamus kurasi + gejala_map) lebih dulu. Jangan bikin
     jalur pencocokan baru di sini.
  3. **68% keluhan unik** (1.215 varian dari 1.785 klaim sah) → pencocokan
     PERSIS hampir selalu nihil. Karena itu skornya per-TOKEN ber-bobot IDF,
     bukan kecocokan kalimat.
  4. Kata umum seperti 'assembly'/'replace' muncul di ratusan kasus. Bobot IDF
     menekannya SENDIRI dari data — jangan tambahkan daftar stopword manual
     yang harus dirawat.
"""
from __future__ import annotations

import math
import re
import statistics
import threading
from collections import Counter, defaultdict
from pathlib import Path

from ..core.config import get_settings
from . import sinonim
from .knowledge_util import load_json, norm_pn

# Status klaim yang DIBUANG dari dasar bukti (lihat jebakan #1).
_STATUS_BATAL = "s-ro-status-zf"

# Token < 3 huruf dibuang (kecuali angka) — 'is', 'of', 'no' tak membawa makna
# dan hanya membengkakkan indeks.
_TOKEN_RE = re.compile(r"[a-z0-9]+")
_MIN_TOKEN = 3

# Kecocokan di teks KELUHAN dihargai lebih tinggi daripada di nama part —
# yang dicari user adalah kasus dengan keluhan serupa. Lihat komentar di _index().
_BOBOT_GEJALA = 2.0

# Kasus dianggap "mirip" bila bobot token kueri yang cocok ≥60% kasus terbaik.
# Tanpa ambang, satu token umum saja menyeret ratusan kasus tak relevan.
_AMBANG_LAYAK = 0.6

_lock = threading.Lock()
_index_cache: dict = {}


def _file() -> Path:
    return get_settings().data_path / "warranty" / "warranty_klaim.json.gz"


def data() -> dict:
    """Isi dataset (cache per-mtime lewat knowledge_util). File belum ada =
    fitur mati (dict kosong), BUKAN error."""
    d = load_json(_file(), default={})
    return d if isinstance(d, dict) else {}


def tersedia() -> bool:
    return bool((data() or {}).get("klaim"))


def _tokens(teks: str) -> set[str]:
    return {t for t in _TOKEN_RE.findall((teks or "").lower())
            if len(t) >= _MIN_TOKEN or t.isdigit()}


def _index() -> dict:
    """Indeks token → kasus + bobot IDF. Dibangun SEKALI per versi berkas
    (kunci = mtime+size dari loader), bukan per-kueri."""
    d = data()
    klaim = d.get("klaim") or []
    global _index_cache
    kunci = (len(klaim), d.get("dibuat"))
    snap = _index_cache                     # baca SEKALI (bisa diganti utas lain)
    if snap.get("kunci") == kunci:
        return snap

    kasus: list[dict] = []
    for k in klaim:
        if k.get("status_code") == _STATUS_BATAL:      # jebakan #1
            continue
        # DUA kantong terpisah, sengaja. Yang dicari user adalah KASUS dengan
        # keluhan serupa, jadi kecocokan di teks keluhan bernilai lebih tinggi
        # daripada kecocokan di nama part. Terukur: disatukan, 'ac tidak dingin'
        # menaruh 'Air dryer' (komponen REM — kebetulan mengandung token 'air')
        # di atas kompresor AC.
        tok_g = _tokens(" ".join(filter(None, [
            k.get("gejala"), k.get("tindakan"), k.get("catatan")])))
        tok_p = _tokens(" ".join(
            [p.get("nama") or "" for p in k.get("part") or []] +
            [p.get("nama_cn") or "" for p in k.get("part") or []]))
        # PN dinormalkan LALU di-lowercase — harus sekasus dengan _tokens.
        # Kalau tidak, satu PN menghasilkan DUA token (huruf besar & kecil),
        # skornya jadi dobel di sebagian kasus saja, dan ambang relatif di
        # `cari()` memangkas himpunan jadi BIAS (terukur: km median satu PN
        # melenceng 16.642 → 2.032). Jangan kembalikan ke uppercase.
        tok_p |= {norm_pn(p.get("pn")).lower() for p in k.get("part") or [] if p.get("pn")}
        kasus.append({"k": k, "tok": tok_g | tok_p, "tok_g": tok_g, "tok_p": tok_p})

    df: Counter = Counter()
    peta: dict[str, set[int]] = defaultdict(set)
    for i, c in enumerate(kasus):
        for t in c["tok"]:
            df[t] += 1
            peta[t].add(i)
    n = max(len(kasus), 1)
    idf = {t: math.log(1 + n / c) for t, c in df.items()}

    # Dict BARU lalu DIGANTI utuh — bukan clear()+update() pada dict yang sedang
    # dipegang pembaca lain. Penugasan atribut modul bersifat atomik di CPython,
    # jadi pembaca selalu melihat indeks lama ATAU baru yang utuh, tak pernah
    # yang separuh kosong. (Dua utas boleh membangun berbarengan — hanya boros
    # sesaat, hasilnya identik.)
    baru = {"kunci": kunci, "kasus": kasus, "peta": peta,
            "idf": idf, "total": len(kasus)}
    with _lock:
        _index_cache = baru
    return baru


def _agregat_part(cocok: list[dict]) -> list[dict]:
    """Part yang dipasang di kasus-kasus yang cocok, diurut dari yang paling
    sering. km diambil MEDIAN (bukan rata-rata): satu unit ber-km ekstrem tak
    boleh menggeser angka yang dipakai untuk saran perawatan."""
    per_pn: dict[str, dict] = {}
    for c in cocok:
        k = c["k"]
        for p in k.get("part") or []:
            pn = p.get("pn")
            if not pn:
                continue
            e = per_pn.setdefault(pn, {
                "pn": pn, "nama": p.get("nama"), "nama_cn": p.get("nama_cn"),
                "kali_dipasang": 0, "_qty": 0, "_km": [], "_mode": Counter(),
                "_harga": [], "_duty": Counter(),
            })
            e["kali_dipasang"] += 1
            e["_qty"] += p.get("qty") or 0
            if isinstance(k.get("km"), (int, float)) and k["km"]:
                e["_km"].append(float(k["km"]))
            if p.get("mode_gagal"):
                e["_mode"][p["mode_gagal"]] += 1
            if isinstance(p.get("harga_cny"), (int, float)):
                e["_harga"].append(float(p["harga_cny"]))
            if p.get("penanggung_jawab"):
                e["_duty"][p["penanggung_jawab"]] += 1

    out = []
    for e in per_pn.values():
        mode = e["_mode"].most_common(1)
        out.append({
            "pn": e["pn"], "nama": e["nama"], "nama_cn": e["nama_cn"],
            "kali_dipasang": e["kali_dipasang"],
            "total_qty": round(e["_qty"], 2) if e["_qty"] % 1 else int(e["_qty"]),
            "mode_gagal_tersering": mode[0][0] if mode else None,
            "km_median": int(statistics.median(e["_km"])) if e["_km"] else None,
            "harga_cny": round(statistics.median(e["_harga"]), 2) if e["_harga"] else None,
            "penanggung_jawab": (e["_duty"].most_common(1)[0][0] if e["_duty"] else None),
        })
    out.sort(key=lambda x: (-x["kali_dipasang"], x["pn"]))
    return out


def cari(gejala: str, batas_kasus: int = 8, batas_part: int = 12) -> dict:
    """Cari kasus perbaikan nyata yang mirip satu keluhan.

    Return dict siap sajikan; `found=False` bila dataset kosong atau tak ada
    kasus yang menyentuh kueri sama sekali (itu jawaban sah, bukan galat)."""
    q = (gejala or "").strip()
    if not q:
        return {"found": False, "catatan": "Keluhan/gejala belum disebutkan."}
    if not tersedia():
        return {"found": False,
                "catatan": "Dataset kasus klaim belum tersedia di server."}

    idx = _index()
    # Jembatan Indonesia → istilah katalog Inggris (jebakan #2).
    terms, matched = sinonim.expand_query(q)

    # Kueri dipecah jadi GRUP token. Kata dari kueri asli = grup satu token
    # (bebas). Keyword hasil ekspansi kamus = grup FRASA: semua tokennya wajib
    # ada. Tanpa aturan frasa, keyword 'air conditioning compressor' menyumbang
    # token 'air' sendirian, dan 'ac tidak dingin' menjawab dengan 'Air dryer'
    # — komponen REM, bukan AC. Di bisnis part itu salah sistem, bukan sekadar
    # peringkat meleset.
    grup: list[set[str]] = [{t} for t in _tokens(q)]
    grup.append({norm_pn(q).lower()})          # user bisa menempel PN langsung
    for t in terms:
        if t != q and (ts := _tokens(t)):
            grup.append(ts)
    # expand_query TIDAK mengembangkan kata KATEGORI polos ('suspensi', 'rem',
    # 'mesin') — itu tugas UMBRELLA_KATEGORI. Terukur: tanpa ini 'dudukan karet
    # suspensi patah' malah menaruh dudukan MESIN di peringkat 1. Dipakai
    # peta kategorinya saja, BUKAN umbrella_keywords() yang menyemburkan
    # keyword seluruh grup terkait dan menenggelamkan presisi.
    for kata, padanan in sinonim.UMBRELLA_KATEGORI.items():
        if sinonim.hit(kata, q):
            for p in padanan:
                if ts := _tokens(p):
                    grup.append(ts)
    # Grup yang memuat token asing bagi korpus tak akan pernah cocok — buang.
    grup = [g for g in grup if g and all(t in idx["idf"] for t in g)]
    q_tok = {t for g in grup for t in g}
    if not q_tok:
        return {"found": False, "kueri": q,
                "catatan": ("Tak satu pun istilah pada keluhan ini pernah muncul di "
                            f"{idx['total']} kasus klaim armada. Bukan berarti part-nya "
                            "tak ada — cek katalog/EPC.")}

    # DUA angka, sengaja dipisah:
    #   massa = bobot IDF token kueri yang cocok, TANPA bonus gejala → dipakai
    #           untuk menentukan LAYAK/tidak (ambang).
    #   skor  = massa + bonus gejala → dipakai untuk MENGURUTKAN saja.
    # Menggabungkan keduanya membuat ambang relatif rapuh: satu token yang
    # kebetulan muncul di teks keluhan sebagian kasus menaikkan puncak, lalu
    # ambang 0,6×puncak MEMBUANG kasus yang sebenarnya sama layaknya. Terukur
    # dua kali pada kueri PN polos: 359 kasus menyusut jadi 82 dan km median
    # melenceng 16.642 → 2.032.
    massa: dict[int, float] = defaultdict(float)
    skor: dict[int, float] = defaultdict(float)
    for g in grup:
        # Kandidat = kasus yang memuat token TERLANGKA di grup; grup frasa lalu
        # disaring lagi agar SELURUH tokennya benar-benar ada.
        langka = max(g, key=lambda t: idx["idf"][t])
        bobot = sum(idx["idf"][t] for t in g)
        for i in idx["peta"][langka]:
            c = idx["kasus"][i]
            if not g <= c["tok"]:
                continue
            massa[i] += bobot
            skor[i] += bobot * (_BOBOT_GEJALA if g & c["tok_g"] else 1.0)
    if not massa:
        return {"found": False, "kueri": q,
                "catatan": f"Tak ada kasus mirip di {idx['total']} klaim armada."}

    puncak = max(massa.values())
    kuat = [i for i, m in massa.items() if m >= puncak * _AMBANG_LAYAK]
    kuat.sort(key=lambda i: (-skor[i], idx["kasus"][i]["k"].get("tanggal") or ""))
    cocok = [idx["kasus"][i] for i in kuat]

    contoh = []
    for c in cocok[:batas_kasus]:
        k = c["k"]
        contoh.append({
            "no_wo": k.get("no_wo"), "tanggal": k.get("tanggal"),
            "unit": k.get("frame"), "km": k.get("km"),
            "gejala": k.get("gejala"), "tindakan": k.get("tindakan"),
            "part": [p.get("pn") for p in k.get("part") or [] if p.get("pn")],
            "biaya_cny": (k.get("biaya") or {}).get("total_cny"),
        })

    mode = Counter(p["mode_gagal"] for c in cocok for p in c["k"].get("part") or []
                   if p.get("mode_gagal"))
    biaya = [(c["k"].get("biaya") or {}).get("total_cny") for c in cocok]
    biaya = [b for b in biaya if isinstance(b, (int, float))]

    return {
        "found": True,
        "kueri": q,
        "istilah_kamus_cocok": matched or None,
        "jumlah_kasus_mirip": len(cocok),
        "dari_total_kasus": idx["total"],
        "part_disarankan": _agregat_part(cocok)[:batas_part],
        "mode_gagal_tersering": [{"mode": m, "jumlah": n} for m, n in mode.most_common(5)],
        "biaya_median_cny": round(statistics.median(biaya), 2) if biaya else None,
        "kasus_contoh": contoh,
        "catatan": (
            f"Dari {idx['total']} klaim garansi NYATA armada (klaim dibatalkan sudah "
            "dibuang). 'part_disarankan' = part yang benar-benar dipasang pada keluhan "
            "serupa, BUKAN rekomendasi katalog — tetap cocokkan dengan unit/VIN sebelum "
            "dipesan. km_median = km saat kerusakan itu biasanya terjadi. Nilai CNY = "
            "modal apa adanya, jangan diubah ke rupiah kecuali diminta."
        ),
    }
