"""Komposisi + cache BERSAMA exploded view per PN (TANPA nomor rangka).

Dua pemakai: `GET /api/parts/exploded-figure` (kartu di halaman detail part) dan
kolom "Exploded View" di Batch Download. Satu cache dipakai keduanya → membuka
gambar di halaman detail part MENGHANGATKAN batch, dan sebaliknya.

⚠️ MAHAL. Panggilan pertama satu PN terukur **94 detik** di produksi (PN yang
dipakai 17.185 model: satu `home/reverse/part?t=global` berisi belasan ribu baris,
lalu s/d 3 `part/tree/item`, unduh SVG, render resvg). Server produksi 1 vCPU dan
sisa RAM ±1,2 GB. Karena itu:
  • paralelisme batch DIBATASI (`PEKERJA_BATCH`),
  • hanya SATU batch exploded boleh jalan (`bangun_dengan_gerbang`),
  • ada ANGGARAN WAKTU (`ANGGARAN_DETIK`) — PN yang tak terjangkau ditandai jujur,
    bukan digantung dan bukan dikarang.

⚠️ Figure yang didapat bersifat LINTAS MODEL: memuat PN itu dari model mana pun,
bukan unit tertentu. Teks peringatannya SATU sumber (`catatan_lintas_model`) supaya
endpoint dan Excel tak pernah menyimpang.
"""
from __future__ import annotations

import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from . import ai_export, epc_bom

TTL = 24 * 3600          # figure EPC praktis statis
MAKS_ENTRI = 80          # ±120 KB/PNG → cap ±10 MB; kini diperebutkan 2 pemakai
MAKS_FIGURE = 3          # makin banyak figure dicoba, makin lambat
PEKERJA_BATCH = 3        # EPC rapuh + server 1 vCPU → turunkan ke 2 bila _err naik
ANGGARAN_DETIK = 900     # 15 menit; batas atas label UI = batas keras server

CACHE: dict[str, dict] = {}      # kunci_pn(pn) → {"at": monotonic, "val": hasil}
_lock = threading.Lock()
_gerbang = threading.BoundedSemaphore(1)


class SedangSibuk(RuntimeError):
    """Sudah ada satu batch exploded yang berjalan di server ini."""


def kunci_pn(pn: str) -> str:
    """Kunci cache: huruf besar, buang semua non-alfanumerik."""
    return re.sub(r"[^A-Z0-9]", "", (pn or "").upper())


def bersihkan_cache() -> None:
    """Kosongkan cache (dipakai test)."""
    with _lock:
        CACHE.clear()


def ambil_cache(pn: str) -> dict | None:
    """Entri cache yang MASIH berlaku, atau None."""
    k = kunci_pn(pn)
    if not k:
        return None
    with _lock:
        c = CACHE.get(k)
        if c and (time.monotonic() - c["at"] < TTL):
            return c["val"]
    return None


def _simpan_cache(pn: str, val: dict) -> None:
    k = kunci_pn(pn)
    if not k:
        return
    with _lock:
        if len(CACHE) >= MAKS_ENTRI:
            CACHE.pop(next(iter(CACHE)), None)      # FIFO
        CACHE[k] = {"at": time.monotonic(), "val": val}


def catatan_lintas_model(sumber_model: str | None = "") -> str:
    """Peringatan WAJIB: gambar ini bukan milik unit tertentu."""
    return ("Gambar ini diambil lintas-model (figure EPC mana pun yang memuat PN ini)"
            + (f", contoh model: {sumber_model}" if sumber_model else "")
            + ". Untuk unit tertentu, gambar & PN pasti tetap harus lewat nomor rangka.")


def figure_untuk_pn(pn: str, *, pakai_cache: bool = True) -> dict:
    """Figure + PNG untuk satu PN, tanpa nomor rangka.

    Sukses → {found:True, part_number, svg, figure_pn, figure_nama, nama_item,
    balon, jumlah_item, sumber_model, jumlah_model_pemakai, catatan, png: bytes}.
    Gagal  → {found:False, part_number, alasan}.

    `png` disimpan sebagai BYTES (bukan base64): Excel butuh bytes, dan endpoint
    yang meng-encode saat menjawab — sekalian hemat 33% RAM cache.
    Kegagalan TIDAK di-cache: bisa cuma EPC ngadat sesaat.
    """
    pnu = (pn or "").strip().upper()
    if not kunci_pn(pnu):
        return {"found": False, "part_number": pnu, "alasan": "Part Number kosong."}
    if pakai_cache:
        c = ambil_cache(pnu)
        if c is not None:
            return c

    d = epc_bom.figure_global(pnu, max_figures=MAKS_FIGURE)
    if not d.get("found") or not d.get("svg"):
        # Bedakan "memang tak ada" dari "EPC sedang bermasalah" — dua hal berbeda,
        # dan yang kedua bisa berhasil kalau dicoba lagi. Menyamakan keduanya
        # membuat user menyimpulkan part-nya tak punya figure padahal cuma ngadat.
        if d.get("_err"):
            return {"found": False, "part_number": pnu,
                    "alasan": ("Server EPC tak menjawab saat PN ini diproses "
                               f"({d['_err']}). Bukan berarti figure-nya tidak ada — "
                               "coba lagi sebentar.")}
        return {"found": False, "part_number": pnu,
                "alasan": ("Figure exploded view untuk PN ini tidak ditemukan di EPC "
                           "(jalur lintas-model). Coba lewat nomor rangka unitnya.")}

    png = ai_export.exploded_png(d["svg"], d.get("balon"))
    if not png:
        return {"found": False, "part_number": pnu,
                "alasan": "Berkas gambar figure gagal diunduh/dirender dari EPC."}

    hasil = {
        "found": True,
        "part_number": pnu,
        "svg": d.get("svg"),
        "figure_pn": d.get("figure_pn"),
        "figure_nama": d.get("figure_nama"),
        "nama_item": d.get("nama_item"),
        "balon": d.get("balon"),
        "jumlah_item": d.get("jumlah_item"),
        "sumber_model": d.get("sumber_model"),
        "jumlah_model_pemakai": d.get("jumlah_model_pemakai"),
        "catatan": catatan_lintas_model(d.get("sumber_model")),
        "png": png,
    }
    _simpan_cache(pnu, hasil)
    return hasil


def png_batch(pns: list[str], pekerja: int = PEKERJA_BATCH,
              anggaran: float = ANGGARAN_DETIK) -> dict[str, dict]:
    """Figure untuk BANYAK PN — paralel terbatas + anggaran waktu.

    Kunci hasil = `kunci_pn(pn)`. Cache-hit dilayani gratis (tak memakai anggaran).
    PN yang tak terjangkau anggaran ATAU gagal per-PN diberi {found:False, alasan}
    — Excel tetap jadi, barisnya jujur, dan TIDAK ada yang dikarang.
    """
    batas = time.monotonic() + max(0.0, float(anggaran))
    urut = [p for p in (pns or []) if kunci_pn(p)]
    if not urut:
        return {}

    def _satu(pn: str) -> tuple[str, dict]:
        k = kunci_pn(pn)
        c = ambil_cache(pn)
        if c is not None:
            return k, c
        if time.monotonic() > batas:
            return k, {"found": False, "part_number": pn.strip().upper(),
                       "alasan": ("Belum terambil — batas waktu proses habis. Coba "
                                  "lagi dengan part number lebih sedikit.")}
        try:
            return k, figure_untuk_pn(pn)
        except Exception:
            return k, {"found": False, "part_number": pn.strip().upper(),
                       "alasan": "EPC sedang tak bisa diakses saat baris ini diproses."}

    hasil: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=max(1, int(pekerja))) as ex:
        for k, d in ex.map(_satu, urut):
            hasil[k] = d
    return hasil


def bangun_dengan_gerbang(fn, *a, **kw):
    """Jalankan `fn` hanya bila tak ada batch exploded lain yang berjalan.

    ⚠️ Gerbang diambil DI DALAM thread pekerja, bukan di router: kalau klien
    memutus koneksi, `await` di router dibatalkan tapi thread-nya TETAP membakar
    CPU. Kalau gerbang dilepas oleh `finally` milik router, batch kedua bisa masuk
    selagi yang pertama masih jalan — dua-duanya melambat dan EPC menolak.
    """
    if not _gerbang.acquire(blocking=False):
        raise SedangSibuk()
    try:
        return fn(*a, **kw)
    finally:
        _gerbang.release()
