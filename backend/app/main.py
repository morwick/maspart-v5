"""
MASPART API — FastAPI (Fase 1)
==============================
Backend yang membungkus logika Python milik app Streamlit sebagai REST API,
sebagai langkah pertama migrasi ke arsitektur FastAPI + Next.js.

Jalankan (dari folder backend/):
    uvicorn app.main:app --reload
Dokumentasi interaktif: http://127.0.0.1:8000/docs
"""
from __future__ import annotations

import logging
import secrets
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .core.config import get_settings
from .routers import (admin, ai, app_meta, auth, branch, buyer, chat, geo, harga, orders, parts,
                      populasi, rak, repairkit, stok)
from .services import (accurate, ai_chat_log, ai_sinonim_learn, geocode, image_search,
                       part_index, sims, sims_weights)
from .services import orders as orders_service   # NB: `orders` di atas = ROUTER

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger("maspart")

settings = get_settings()


def _warmup():
    """Panaskan index part (cepat berkat disk-cache) + preload model DINOv2."""
    try:
        part_index.ensure_index()
    except Exception as e:  # pragma: no cover
        print(f"[startup] warmup index gagal: {e}")
    try:
        image_search.preload_local_index()
    except Exception as e:  # pragma: no cover
        print(f"[startup] preload galeri lokal gagal: {e}")
    try:
        image_search.preload_model()
    except Exception as e:  # pragma: no cover
        print(f"[startup] preload model gagal: {e}")
    try:
        # Indeks stok Accurate (menu Stok) ditarik TERJADWAL tiap _INDEX_TTL di
        # latar → cache selalu hangat, tak ada user yang menunggu tarikan penuh.
        accurate.start_scheduled_refresh()
    except Exception as e:  # pragma: no cover
        print(f"[startup] scheduler refresh Accurate gagal: {e}")
    try:
        # Auto-logout Accurate saat idle: akun hanya 1 sesi, jadi sesi tak boleh
        # dibiarkan menyala agar orang lain bisa login membuat penawaran.
        accurate.start_idle_logout()
    except Exception as e:  # pragma: no cover
        print(f"[startup] scheduler idle-logout Accurate gagal: {e}")
    try:
        # Usulan sinonim otomatis harian (loop belajar): miss baru cukup banyak →
        # LLM menyusun usulan di latar; admin tinggal Setujui di halaman
        # Pencarian Nihil.
        ai_sinonim_learn.start_scheduled_generate()
    except Exception as e:  # pragma: no cover
        print(f"[startup] scheduler usulan sinonim gagal: {e}")
    try:
        # Indeks persamaan/pengganti part (SIMS partEquivalentQuery, ~17rb baris)
        # ditarik sekali di latar → cari_part bisa menyisipkan persamaan tanpa
        # panggilan live per-part.
        sims.start_equivalents_refresh()
    except Exception as e:  # pragma: no cover
        print(f"[startup] scheduler indeks persamaan gagal: {e}")
    try:
        # Retensi observabilitas AI: hapus baris ai_chat_log > 30 hari (harian, latar).
        ai_chat_log.start_retention()
    except Exception as e:  # pragma: no cover
        print(f"[startup] scheduler retensi chat-log gagal: {e}")
    try:
        # SINYAL PEMBELIAN mingguan ke Telegram. Datanya sudah matang sejak
        # 2026-08-09 tapi tool-nya NOL kali dipanggil dalam 30 hari — informasi
        # dorongan yang selama ini hanya tersedia lewat kanal tarikan.
        from .services import permintaan_tak_terlayani
        permintaan_tak_terlayani.start_laporan_mingguan()
    except Exception as e:  # pragma: no cover
        print(f"[startup] scheduler laporan permintaan gagal: {e}")
    try:
        # Hangatkan indeks BERAT SIMS (persisten /app/data) utk part berharga →
        # SIMS jadi sumber berat utama etalase tanpa input manual. Latar, throttled.
        sims_weights.start()
    except Exception as e:  # pragma: no cover
        print(f"[startup] warmer berat SIMS gagal: {e}")
    try:
        # Kode pos ASAL ongkir tiap gudang: isi otomatis dari koordinat yang sudah
        # diatur admin. Gudang pemenuh tanpa kode pos = ongkir DITOLAK, jadi ini
        # yang membuat gudang non-pilihan (fallback terdekat) tetap bisa mengirim.
        geocode.start_postal_warmer()
    except Exception as e:  # pragma: no cover
        print(f"[startup] auto-isi kode pos gudang gagal: {e}")
    try:
        # Miner belajar-sendiri harian (bebas-LLM, bebas-jaringan-eksternal):
        # chat-log gagal → kandidat sinonim + gap topik admin; cache EPC disk →
        # edges part↔unit → rebuild knowledge_links (tautan antar-pengetahuan).
        from .services import ai_belajar
        ai_belajar.start_scheduled()
    except Exception as e:  # pragma: no cover
        print(f"[startup] scheduler ai_belajar gagal: {e}")
    try:
        # Rekonsiliasi pembayaran (latar, tiap 10 menit). Uang pembeli tak lewat
        # server kita, jadi server mati tak menghentikan pembayaran — yang hilang
        # adalah NOTIFIKASI-nya (retry webhook Midtrans cuma ± 5–6 jam). Penyapu ini
        # menanyakan sendiri status tiap order 'menunggu_pembayaran' ke gateway, jadi
        # pembayaran tetap terkejar walau webhook hangus & pembeli tak membuka halaman.
        orders_service.start_reconcile_scheduler()
    except Exception as e:  # pragma: no cover
        print(f"[startup] scheduler rekonsiliasi pembayaran gagal: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Validasi keamanan konfigurasi: di production gagalkan startup; di dev cuma peringatan.
    for issue in settings.validate_security():
        print(f"[security][WARNING] {issue}")
    # Jalankan di thread terpisah supaya server langsung siap menerima request.
    threading.Thread(target=_warmup, daemon=True).start()
    yield


app = FastAPI(
    title="MASPART API",
    version="0.2.0",
    description="Backend FastAPI untuk MASPART (auth + search PN/Name + foto + cari-by-foto).",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"http://(localhost|127\.0\.0\.1)(:\d+)?",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Header keamanan pada SEMUA respons API (HSTS bila https, anti-sniff, anti-frame).
_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Strict-Transport-Security": "max-age=63072000; includeSubDomains",
}


@app.middleware("http")
async def _security_headers(request: Request, call_next):
    resp = await call_next(request)
    for k, v in _SECURITY_HEADERS.items():
        resp.headers.setdefault(k, v)
    return resp

app.include_router(auth.router)
app.include_router(parts.router)
app.include_router(populasi.router)
app.include_router(harga.router)
app.include_router(stok.router)

app.include_router(orders.router)
app.include_router(geo.router)
app.include_router(admin.router)
app.include_router(buyer.router)
app.include_router(branch.router)
app.include_router(chat.router)
app.include_router(ai.router)
app.include_router(repairkit.router)
app.include_router(app_meta.router)
app.include_router(rak.router)


@app.exception_handler(Exception)
async def _unhandled_exception_handler(request: Request, exc: Exception):
    """Tangkap error tak tertangani: catat lengkap di server, balas generik ke
    klien (jangan bocorkan traceback/detail internal)."""
    err_id = secrets.token_hex(4)
    logger.exception("[%s] Unhandled error on %s %s", err_id, request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content={"detail": f"Terjadi kesalahan internal. Kode: {err_id}"},
    )


@app.get("/health", tags=["meta"])
def health():
    return {
        "status": "ok",
        "supabase_configured": settings.supabase_configured,
        "data_dir": str(settings.data_path),
    }
