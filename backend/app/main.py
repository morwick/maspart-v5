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
from .routers import admin, ai, auth, branch, buyer, chat, geo, harga, orders, parts, populasi, repairkit
from .services import image_search, part_index

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

app.include_router(auth.router)
app.include_router(parts.router)
app.include_router(populasi.router)
app.include_router(harga.router)

app.include_router(orders.router)
app.include_router(geo.router)
app.include_router(admin.router)
app.include_router(buyer.router)
app.include_router(branch.router)
app.include_router(chat.router)
app.include_router(ai.router)
app.include_router(repairkit.router)


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
