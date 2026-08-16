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


# ── Laporan mingguan DIDORONG ke pemilik (2026-08-17) ───────────────────────
# Modul ini sudah matang sejak 2026-08-09 (memisahkan 45% sinyal beli asli dari
# salah ketik & miss basi), TAPI audit 1.189 giliran menemukan tool
# `permintaan_tak_terlayani` NOL kali dipanggil dalam 30 hari — sementara
# `pengganti_part:nf` justru kegagalan tool NOMOR SATU (50 kejadian). Datanya
# menumpuk dan tak pernah dilihat siapa pun.
#
# Sebabnya bukan kode melainkan ALUR: ini kanal TARIKAN (harus diminta) untuk
# informasi yang sifatnya DORONGAN (pemilik tak tahu ada yang perlu dilihat).
# Maka dijadikan laporan berkala lewat Telegram — kanal notifikasi yang sudah
# dipakai pesanan masuk, jadi tak ada infrastruktur baru.
import logging as _logging
import threading as _threading
import time as _time

_logger = _logging.getLogger("maspart.permintaan")

_LAPORAN_INTERVAL = 7 * 24 * 3600      # mingguan
_LAPORAN_JEDA_AWAL = 15 * 60           # jangan menembak saat boot/redeploy
_LAPORAN_MIN_KEJADIAN = 2              # dicari sekali saja belum tentu sinyal
_LAPORAN_BARIS = 12
_laporan_started = False
_laporan_lock = _threading.Lock()


def teks_laporan(limit: int = _LAPORAN_BARIS,
                 min_kejadian: int = _LAPORAN_MIN_KEJADIAN) -> str:
    """Ringkasan sinyal PEMBELIAN untuk pesan Telegram. "" = tak ada yang perlu
    dilaporkan (jangan kirim pesan kosong tiap minggu — itu melatih orang
    mengabaikan kanalnya)."""
    try:
        d = analisa(limit=limit, min_kejadian=min_kejadian)
    except Exception:
        _logger.exception("laporan permintaan gagal disusun")
        return ""
    baris = (d or {}).get("permintaan_tak_terlayani") or []
    if not baris:
        return ""
    out = [f"🛒 SINYAL PEMBELIAN — {len(baris)} barang dicari tapi TIDAK ADA",
           f"(total {d.get('total_kejadian_tak_terlayani')} pencarian; "
           f"minimal {min_kejadian}× dicari)", ""]
    for b in baris:
        out.append(f"• {b['dicari']} — {b['berapa_kali']}× "
                   f"(terakhir {b['terakhir_dicari_hari_lalu']:.0f} hari lalu)")
    salah = d.get("jumlah_kemungkinan_salah_ketik") or 0
    if salah:
        # ⛔ Angka ini SENGAJA disebut: tanpa itu daftar di atas terbaca seolah
        # seluruh pencarian nihil adalah peluang jualan, padahal 35% justru
        # salah ketik yang keliru bila dibeli.
        out.append("")
        out.append(f"({salah} pencarian nihil lain punya PN mirip di katalog — "
                   "itu salah ketik/varian, JANGAN dibeli.)")
    out.append("")
    out.append("Buka Asisten AI lalu tanya 'permintaan tak terlayani' untuk rincian.")
    return "\n".join(out)


def start_laporan_mingguan() -> bool:
    """Thread daemon: kirim laporan sinyal pembelian sepekan sekali.

    Best-effort dan idempoten seperti ai_chat_log.start_retention — gagal kirim
    tak boleh mengganggu apa pun, dan dipanggil dua kali tetap satu thread."""
    global _laporan_started
    from . import notify
    if not notify.available():
        return False
    with _laporan_lock:
        if _laporan_started:
            return False
        _laporan_started = True

    def _loop():
        _time.sleep(_LAPORAN_JEDA_AWAL)
        while True:
            try:
                teks = teks_laporan()
                if teks:
                    notify.send_async(teks)
            except Exception:       # pragma: no cover — laporan tak boleh fatal
                _logger.exception("laporan permintaan mingguan gagal")
            _time.sleep(_LAPORAN_INTERVAL)

    _threading.Thread(target=_loop, daemon=True, name="laporan-permintaan").start()
    return True
