"""Teks & tabel MANUAL teknik (Bosch ECU China + TFT NanoBCU) — bisa dicari &
dijawab asisten. Store `manual_teks.json.gz` (builder `tools/build_manual_teks.py`).

Record page-level: {sumber, halaman, judul, judul_id, kata_kunci:[], teks, tabel,
tipe, dicari, gambar_ref:[], blok?, kode?}. `teks` = teks halaman China APA ADANYA
(model menerjemahkan runtime, pola pin_ecu). `judul_id`+`kata_kunci` = kurasi
Indonesia (supaya bisa dicari pakai istilah Indonesia — teks aslinya China).
`blok` = struktur kartu gangguan {kondisi_trigger,reaksi,penyebab[],langkah[],
tes_setelah,catatan}. `gambar_ref` = PNG halaman dari `manual_media` (tampil inline).

`dicari=True` = kartu gangguan Bosch + semua halaman TFT (tabel DTC/pin Bosch
tidak — sudah dilayani cari_kode_kesalahan/pin_ecu, tetap findable via 'kode').

Disajikan tool `cari_manual`: teks/tabel/blok + gambar halaman inline + kartu PDF
(TFT via skema_ref). Loader tipis pola `manual_media`. `.json.gz` ikut git.
"""
from __future__ import annotations

import re
from pathlib import Path

from . import search_boost as sb
from .knowledge_util import load_json

_DATA = Path(__file__).parent / "manual_teks.json.gz"


def _load() -> list[dict]:
    return load_json(_DATA)


# ── Prakomputasi per-mtime (haystack + stemset + kosakata typo) ──────
# Sama seperti pengetahuan: elemen berat (stem, kode ternormal, kosakata typo)
# dihitung SEKALI per versi store, bukan tiap kueri.
_HAY_CACHE: dict = {"mtime": None, "rows": None, "ember": None}


def _mtime():
    try:
        return _DATA.stat().st_mtime if _DATA.exists() else None
    except OSError:
        return None


def _hays() -> tuple[list[dict], dict]:
    """(baris ber-prakomputasi utk record `dicari`, kosakata-ember typo)."""
    mt = _mtime()
    ent = _HAY_CACHE
    if ent["mtime"] != mt or ent["rows"] is None:
        rows: list[dict] = []
        for i, r in enumerate(_load()):
            if not r.get("dicari"):
                continue
            idn = (f"{r.get('judul_id','')} {' '.join(r.get('kata_kunci') or [])}").lower()
            judul = (r.get("judul") or "").lower()
            teks = (r.get("teks") or "").lower()
            kode = " ".join(r.get("kode") or []).lower()
            gabung = f"{idn} {judul} {teks} {kode}"
            rows.append({
                "i": i, "r": r, "idn": idn, "judul": judul, "teks": teks,
                "kode": kode, "kode_norm": sb.kode_norm(r.get("kode")),
                "stemset": sb.stem_set(gabung), "gabung": gabung,
            })
        ent["rows"] = rows
        ent["ember"] = sb.vocab_ember(h["gabung"] for h in rows)
        ent["mtime"] = mt
    return ent["rows"], ent["ember"]


def available() -> bool:
    return bool(_load())


def count() -> int:
    return len(_load())


def count_dicari() -> int:
    """Jumlah record yang boleh muncul dari cari_manual (kartu gangguan + TFT)."""
    return sum(1 for r in _load() if r.get("dicari"))


def _hit(term: str, hay: str) -> bool:
    return bool(re.search(r"(?<![a-z0-9])" + re.escape(term) + r"(?![a-z0-9])", hay))


def _words(ql: str) -> list[str]:
    """Token kueri Latin + token PN ternormal (WG-9725.190102 → wg9725190102)."""
    out = [w for w in re.findall(r"[a-z0-9]+", ql) if len(w) >= 2]
    for norm in sb.pn_normal_tokens(ql):
        if norm not in out:
            out.append(norm)
    return out


_MIN_KODE_NORM = sb.MIN_KODE_NORM


def _score_hay(h: dict, ql: str, words: list[str],
               stems: dict[str, tuple[str, ...]] | None, bobot: float = 1.0) -> float:
    """Skor satu record (dari prakomputasi). Fallback stem/PN-ternormal hanya
    bila kecocokan eksak per-kata gagal — DIREDAM (`_BOBOT_STEM`), tak pernah
    menggeser kecocokan langsung. `bobot` meredam varian typo (kueri terkoreksi)."""
    idn, judul, teks, kode = h["idn"], h["judul"], h["teks"], h["kode"]
    kodenorm, stemset = h["kode_norm"], h["stemset"]
    s = 0.0
    if ql and (_hit(ql, idn) or _hit(ql, judul)):
        s += 50
    for w in words:
        berangka = any(c.isdigit() for c in w)
        spec = 3 if berangka else 1
        if _hit(w, idn):
            s += 5 * spec
        elif _hit(w, judul):
            s += 3 * spec
        elif _hit(w, kode):
            s += 4 * spec
        elif berangka and kodenorm and len(w) >= _MIN_KODE_NORM and w in kodenorm:
            s += 4 * spec
        elif _hit(w, teks):
            s += 1 * spec
        elif stems and stemset:
            sk = stems.get(w)
            if sk and any(k in stemset for k in sk):
                s += sb.BOBOT_STEM * spec
    return s * bobot


def _score(r: dict, ql: str, words: list[str]) -> int:
    """Skor satu record MENTAH — dipertahankan utk test/pemakaian ad-hoc."""
    idn = (f"{r.get('judul_id','')} {' '.join(r.get('kata_kunci') or [])}").lower()
    h = {"idn": idn, "judul": (r.get("judul") or "").lower(),
         "teks": (r.get("teks") or "").lower(),
         "kode": " ".join(r.get("kode") or []).lower(),
         "kode_norm": sb.kode_norm(r.get("kode")),
         "stemset": frozenset()}
    return int(_score_hay(h, ql, words, None))


# Diversitas: maksimal sekian record per SUMBER di lintasan pertama — satu
# manual tebal tak menenggelamkan yang lain; kursi sisa diisi ulang.
_MAX_PER_SUMBER = 2

# Ambang RELATIF terhadap skor juara (pola yang sudah terbukti di
# pengetahuan.search_skor). Sebelumnya `sc > 0` sudah cukup masuk, sehingga
# record yang hanya kena SATU kata umum ikut naik dan disajikan sederajat dengan
# kecocokan sungguhan — asisten lalu mengutip manual yang tak relevan dengan
# nada yakin. Ambang absolut tak bisa dipakai di sini (skor field-weighted
# berskala bebas), tapi ambang relatif menyaring ekor tanpa menyentuh juara.
_AMBANG_RELATIF = 0.30


def search_skor(topik: str = "", limit: int = 5) -> list[tuple[float, dict]]:
    """(skor, record) terurut. Fallback typo/stem/PN bila kecocokan eksak tipis;
    diversitas per sumber. Query kosong → []."""
    ql = (topik or "").strip().lower()
    if not ql:
        return []
    rows, ember = _hays()
    words = _words(ql)
    stems = sb.stems_kueri(words)

    berskor: list[tuple[float, int, dict]] = []
    for h in rows:
        sc = _score_hay(h, ql, words, stems)
        if sc > 0:
            berskor.append((sc, h["i"], h["r"]))

    # Fallback typo: bila krecall rendah, ulang kueri terkoreksi (diredam 0.8) &
    # ambil skor tertinggi per record.
    koreksi = sb.koreksi_tipo(ql, ember)
    if koreksi and koreksi != ql:
        kwords = _words(koreksi)
        kstems = sb.stems_kueri(kwords)
        tambahan: dict[int, tuple[float, dict]] = {}
        for h in rows:
            sc = _score_hay(h, koreksi, kwords, kstems, bobot=sb.BOBOT_TIPO)
            if sc > 0:
                tambahan[h["i"]] = (sc, h["r"])
        sudah = {i for _, i, _ in berskor}
        for i, (sc, r) in tambahan.items():
            if i not in sudah:
                berskor.append((sc, i, r))

    berskor.sort(key=lambda t: (-t[0], t[1]))

    # Buang ekor yang jauh di bawah juara — sisanya bukan jawaban, hanya derau.
    if berskor:
        batas = berskor[0][0] * _AMBANG_RELATIF
        berskor = [t for t in berskor if t[0] >= batas]

    # Diversitas per sumber (lintasan 1), lalu isi kursi sisa.
    hasil: list[tuple[float, dict]] = []
    per_sumber: dict[str, int] = {}
    sisa: list[tuple[float, dict]] = []
    for sc, _i, r in berskor:
        src = r.get("sumber") or ""
        if per_sumber.get(src, 0) < _MAX_PER_SUMBER:
            per_sumber[src] = per_sumber.get(src, 0) + 1
            hasil.append((sc, r))
        else:
            sisa.append((sc, r))
        if len(hasil) >= limit:
            break
    for item in sisa:
        if len(hasil) >= limit:
            break
        hasil.append(item)
    return hasil[:limit]


def search(topik: str = "", limit: int = 5) -> list[dict]:
    """Cari record TERKURASI (dicari=True), diranking relevansi. Wrapper
    search_skor — kompatibel pemanggil lama."""
    return [r for _sc, r in search_skor(topik, limit)]


def for_page(sumber: str, halaman: int) -> dict | None:
    if not sumber or not halaman:
        return None
    return next((r for r in _load()
                 if r.get("sumber") == sumber and r.get("halaman") == halaman), None)
