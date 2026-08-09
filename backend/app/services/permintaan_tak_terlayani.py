"""Permintaan yang TIDAK BISA dilayani — sinyal PEMBELIAN dari pencarian nihil.

Asal-usul (audit 2026-08-09 atas 207 entri Pencarian Nihil produksi, 289
kejadian, diukur lewat rantai endpoint /search yang sebenarnya):

    131 (45%)  gagal TANPA saran apa pun   ← yang ini sinyal pembelian
    102 (35%)  gagal tapi ada PN mirip     ← salah ketik / varian, BUKAN sinyal
     38 (13%)  ternyata sudah ketemu       ← miss basi
     18 ( 6%)  tertolong fallback nama

Daftar Pencarian Nihil mentah mencampur keempatnya, jadi tak bisa dipakai
memutuskan pembelian. Modul ini memisahkannya dan membuang yang basi.

⚠️ Pemisah terpenting: query yang punya PN MIRIP di katalog hampir selalu salah
ketik atau varian suffix — membelinya salah. Yang benar-benar berharga adalah
query yang di katalog MAUPUN indeks lokal tak punya kemiripan sama sekali.
"""
from __future__ import annotations

import re
import time

from . import part_index, search_log

# PN Sinotruk/Weichai: campuran huruf+angka, ≥6 karakter, bukan kalimat.
_PN_RE = re.compile(r"^[A-Z0-9][A-Z0-9\-/.+ ]{4,}$", re.I)
_ADA_ANGKA = re.compile(r"\d")


def _bentuk(q: str) -> str:
    """PN atau nama part? Menentukan cara membaca barisnya, bukan nasibnya."""
    s = (q or "").strip()
    if len(s) >= 6 and _ADA_ANGKA.search(s) and _PN_RE.match(s) and " " not in s.strip():
        return "pn"
    return "nama"


def _masih_nihil(q: str) -> bool:
    """Rantai yang PERSIS dipakai endpoint /search — jangan memakai fungsi lain
    yang 'kelihatan mirip' (rows_for_pns): salah ukur pernah membuat klaim
    keterselamatan 4× lebih besar dari kenyataannya."""
    try:
        if part_index.search_part_number(q):
            return False
    except Exception:
        return False                     # gagal cek ≠ tak ada → jangan diklaim
    try:
        hit, _ = part_index.smart_pn_search(q)
        if hit:
            return False
    except Exception:
        return False
    try:
        if part_index.search_part_name(q):
            return False
    except Exception:
        return False
    return True


def analisa(limit: int = 40, min_kejadian: int = 1,
            maks_umur_hari: int = 60) -> dict:
    """Permintaan tak terlayani, terurut dari yang paling sering dicari.

    limit          : berapa baris teratas dikembalikan
    min_kejadian   : abaikan yang dicari lebih jarang dari ini
    maks_umur_hari : abaikan permintaan basi (default 60 hari)
    """
    try:
        rows = search_log.top_misses(500)
    except Exception:
        return {"found": False, "gagal_dicek": True,
                "error": "Daftar pencarian nihil tidak bisa dibaca."}

    sekarang = time.time()
    sungguhan: list[dict] = []
    salah_ketik: list[dict] = []
    n_basi = 0
    n_tua = 0

    for r in rows:
        q = (r.get("query") or "").strip()
        n = int(r.get("count") or 1)
        if not q or n < min_kejadian:
            continue
        last = int(r.get("last") or 0)
        umur = (sekarang - last) / 86400 if last else 999
        if umur > maks_umur_hari:
            n_tua += 1
            continue
        if not _masih_nihil(q):
            n_basi += 1
            continue

        try:
            mirip = part_index.suggest_pns(q)[:3]
        except Exception:
            mirip = []
        baris = {
            "dicari": q,
            "bentuk": _bentuk(q),
            "berapa_kali": n,
            "terakhir_dicari_hari_lalu": round(umur, 1),
            "dari": list(r.get("sources") or []),
        }
        if mirip:
            baris["pn_mirip_di_katalog"] = [m.get("part_number") for m in mirip if m.get("part_number")]
            salah_ketik.append(baris)
        else:
            sungguhan.append(baris)

    def _urut(xs):
        return sorted(xs, key=lambda x: (-x["berapa_kali"],
                                         x["terakhir_dicari_hari_lalu"]))

    sungguhan = _urut(sungguhan)[:limit]
    salah_ketik = _urut(salah_ketik)[:limit]

    return {
        "found": bool(sungguhan or salah_ketik),
        "permintaan_tak_terlayani": sungguhan,
        "jumlah_tak_terlayani": len(sungguhan),
        "total_kejadian_tak_terlayani": sum(x["berapa_kali"] for x in sungguhan),
        "kemungkinan_salah_ketik": salah_ketik,
        "jumlah_kemungkinan_salah_ketik": len(salah_ketik),
        "dibuang_karena_sudah_ketemu": n_basi,
        "dibuang_karena_basi": n_tua,
        "catatan": (
            "'permintaan_tak_terlayani' = dicari user tapi TIDAK ADA di katalog "
            "maupun indeks lokal, dan sampai sekarang masih tidak ada — ini bahan "
            "keputusan PEMBELIAN. 'kemungkinan_salah_ketik' = ada PN mirip di "
            "katalog, jadi hampir selalu salah ketik/varian suffix; ⛔ JANGAN "
            "diusulkan dibeli. Angka 'berapa_kali' adalah jumlah pencarian, BUKAN "
            "jumlah permintaan barang — jangan menyebutnya sebagai order."
        ),
    }
