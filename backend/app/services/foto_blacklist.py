"""
Daftar-hitam foto per Part Number — foto yang TERBUKTI bukan part itu.

Latar: foto part datang dari SIMS (`partCode` apa adanya, tanpa fuzzy) lalu
disalin ke galeri Cari-by-Foto. Kalau SIMS sendiri salah menempel foto — mis.
`AZ992216002101` (离合器从动盘总成, kampas kopling) diberi foto SAUDARANYA
`AZ992316001223` (离合器压盘总成, dekrup) — tak ada satu pun jalur untuk
membuangnya: `index_part`/`append_local_index` hanya bisa MENAMBAH.

Modul ini jadi penyaringnya. Satu file JSON kecil, dibaca di dua tempat:
  1. `/api/parts/photos` — foto salah tak lagi tampil di halaman part.
  2. `image_search._fetch_candidates` + `photo_url_map` — embedding-nya tak
     lagi dipakai "Cari by Foto". Ini yang penting: satu foto dekrup berlabel
     PN kampas membuat siapa pun yang memotret dekrup diarahkan ke PN salah.

Sengaja BUKAN penghapusan baris CSV galeri: keputusan admin bisa keliru, dan
baris CSV memuat embedding yang mahal dihitung ulang. Menyaring itu reversibel
(`pulihkan`), menghapus tidak.

Bentuk file <DATA_DIR>/foto_blacklist.json:
    {
      "AZ992216002101": {
        "semua": false,          # true = SEMUA foto PN ini disembunyikan
        "urls": ["http://...", "learned://..."],
        "catatan": "foto dekrup, bukan kampas",
        "oleh": "admin",
        "pada": "2026-09-07T10:11:12Z"
      }
    }
"""
from __future__ import annotations

import json
import threading
from datetime import datetime, timezone

from ..core.config import get_settings

_lock = threading.RLock()
_cache: dict | None = None
# Dinaikkan setiap kali isi berubah. Pembaca yang meng-cache hasil saringan
# (mis. image_search.photo_url_map) memakai ini sebagai kunci invalidasi —
# tanpa itu peta PN→foto akan tetap menyajikan foto yang baru saja di-blacklist.
_versi = 0


def _path():
    return get_settings().data_path / "foto_blacklist.json"


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _norm_pn(pn: str) -> str:
    return (pn or "").strip().upper()


def _bersih(entri: dict) -> dict:
    """Satu entri PN dinormalkan (bentuk apa pun dari file lama tetap terbaca)."""
    urls = entri.get("urls")
    if not isinstance(urls, list):
        urls = []
    return {
        "semua": bool(entri.get("semua")),
        "urls": sorted({str(u).strip() for u in urls if str(u).strip()}),
        "catatan": str(entri.get("catatan") or ""),
        "oleh": str(entri.get("oleh") or ""),
        "pada": str(entri.get("pada") or ""),
    }


def load() -> dict:
    """Isi daftar-hitam {PN: entri}. Di-cache; file rusak/hilang → {}."""
    global _cache
    with _lock:
        if _cache is not None:
            return _cache
        data: dict = {}
        try:
            p = _path()
            if p.exists():
                saved = json.loads(p.read_text(encoding="utf-8"))
                if isinstance(saved, dict):
                    for pn, entri in saved.items():
                        key = _norm_pn(str(pn))
                        if key and isinstance(entri, dict):
                            data[key] = _bersih(entri)
        except Exception:
            data = {}
        _cache = data
        return data


def _simpan(data: dict) -> None:
    """Tulis ke disk & invalidasi cache. Dipanggil DI DALAM _lock."""
    global _cache, _versi
    # Entri kosong (tak menyembunyikan apa pun) tak perlu disimpan.
    bersih = {pn: e for pn, e in data.items() if e.get("semua") or e.get("urls")}
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(bersih, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(p)
    _cache = bersih
    _versi += 1


def versi() -> int:
    """Nomor revisi isi daftar-hitam — kunci invalidasi cache pembaca."""
    return _versi


def reload() -> dict:
    """Buang cache supaya file dibaca ulang (mis. sesudah scp file baru)."""
    global _cache, _versi
    with _lock:
        _cache = None
        _versi += 1
    return {"ok": True, "total_pn": len(load())}


# ── Baca ─────────────────────────────────────────────────────────────
def entri(pn: str) -> dict:
    """Entri satu PN ({} bila PN itu tak punya foto yang disembunyikan)."""
    return dict(load().get(_norm_pn(pn)) or {})


def diblokir(pn: str, url: str) -> bool:
    """Apakah (pn, url) ini disembunyikan?"""
    e = load().get(_norm_pn(pn))
    if not e:
        return False
    return bool(e.get("semua")) or (str(url or "").strip() in set(e.get("urls") or []))


def filter_urls(pn: str, urls: list[str]) -> list[str]:
    """Buang URL foto yang di-blacklist untuk PN ini. Urutan sisanya utuh."""
    e = load().get(_norm_pn(pn))
    if not e:
        return list(urls or [])
    if e.get("semua"):
        return []
    buang = set(e.get("urls") or [])
    return [u for u in (urls or []) if str(u).strip() not in buang]


def filter_rows(rows: list[dict]) -> list[dict]:
    """Saring kandidat Cari-by-Foto ([{part_number, sims_url, ...}]).

    Dipakai di jalur panas pencarian (ratusan baris per query) — karena itu
    keluar cepat saat daftar-hitam kosong, dan lookup-nya O(1) per baris.
    """
    data = load()
    if not data or not rows:
        return list(rows or [])
    # Set URL dibangun SEKALI per PN, bukan per baris: kandidat bisa ratusan baris
    # per query dan ini jalur panas Cari by Foto.
    peta = {pn: (bool(e.get("semua")), set(e.get("urls") or [])) for pn, e in data.items()}
    out = []
    for r in rows:
        hit = peta.get(_norm_pn(str(r.get("part_number") or "")))
        if hit and (hit[0] or str(r.get("sims_url") or "").strip() in hit[1]):
            continue
        out.append(r)
    return out


def daftar() -> list[dict]:
    """Semua entri sebagai list (untuk layar admin), terbaru di atas."""
    out = [{"pn": pn, **e} for pn, e in load().items()]
    out.sort(key=lambda d: (d.get("pada") or "", d.get("pn") or ""), reverse=True)
    return out


def jumlah_foto() -> int:
    """Total foto yang disembunyikan (entri `semua` dihitung 1 — jumlah aslinya
    tak diketahui tanpa memanggil SIMS)."""
    return sum(1 if e.get("semua") else len(e.get("urls") or []) for e in load().values())


# ── Tulis ────────────────────────────────────────────────────────────
def tambah(pn: str, urls: list[str] | None = None, *, semua: bool = False,
           catatan: str = "", oleh: str = "") -> dict:
    """Sembunyikan foto untuk PN ini. `semua=True` menyembunyikan seluruhnya
    (dipakai saat SIMS memasang set foto part LAIN sepenuhnya).

    Menambah, tidak menimpa: URL yang sudah ada tetap ada.
    """
    key = _norm_pn(pn)
    if not key:
        raise ValueError("Part Number wajib.")
    bersih_url = {str(u).strip() for u in (urls or []) if str(u).strip()}
    if not semua and not bersih_url:
        raise ValueError("Tidak ada foto yang ditandai.")
    with _lock:
        data = dict(load())
        lama = data.get(key) or {}
        e = _bersih({
            "semua": bool(lama.get("semua")) or semua,
            "urls": list(set(lama.get("urls") or []) | bersih_url),
            # Catatan/oleh terbaru menang; yang lama dipertahankan bila kosong.
            "catatan": catatan or lama.get("catatan") or "",
            "oleh": oleh or lama.get("oleh") or "",
            "pada": _now(),
        })
        data[key] = e
        _simpan(data)
    return {"ok": True, "pn": key, **e}


def pulihkan(pn: str, urls: list[str] | None = None) -> dict:
    """Tampilkan lagi foto yang sempat disembunyikan.

    `urls` kosong = pulihkan SEMUA foto PN itu (entri dibuang).
    """
    key = _norm_pn(pn)
    if not key:
        raise ValueError("Part Number wajib.")
    with _lock:
        data = dict(load())
        lama = data.get(key)
        if not lama:
            return {"ok": True, "pn": key, "dipulihkan": 0, "sisa": 0}
        bersih_url = {str(u).strip() for u in (urls or []) if str(u).strip()}
        if not bersih_url:
            n = 1 if lama.get("semua") else len(lama.get("urls") or [])
            data.pop(key, None)
            _simpan(data)
            return {"ok": True, "pn": key, "dipulihkan": n, "sisa": 0}
        sisa = [u for u in (lama.get("urls") or []) if u not in bersih_url]
        n = len(lama.get("urls") or []) - len(sisa)
        # Memulihkan URL tertentu selagi entri ber-`semua` tak masuk akal:
        # `semua` menyembunyikan foto yang bahkan belum pernah kita lihat.
        # Pemulihan per-URL karena itu ikut mencabut `semua`.
        e = _bersih({**lama, "semua": False, "urls": sisa, "pada": _now()})
        if e["urls"]:
            data[key] = e
        else:
            data.pop(key, None)
        _simpan(data)
    return {"ok": True, "pn": key, "dipulihkan": n, "sisa": len(e["urls"])}
