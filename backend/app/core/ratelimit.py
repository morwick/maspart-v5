"""
Rate limiter sederhana berbasis memori (sliding window per kunci).

Tanpa dependency eksternal — cukup untuk membendung brute-force login dan spam
webhook pada satu proses uvicorn. Catatan: state disimpan in-process, jadi pada
deployment multi-worker/multi-instance batas berlaku per worker. Untuk produksi
skala besar, ganti backend ke Redis (mis. via slowapi) — antarmuka `hit()` di
bawah sengaja dibuat agar mudah ditukar.
"""
from __future__ import annotations

import threading
import time
from collections import deque

from fastapi import HTTPException, Request, status

_lock = threading.Lock()
_hits: dict[str, deque] = {}


def _client_ip(request: Request) -> str:
    # Hormati proxy umum (X-Forwarded-For) bila ada, fallback ke peer langsung.
    fwd = request.headers.get("x-forwarded-for", "")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def hit(key: str, limit: int, window_seconds: float) -> bool:
    """Catat satu permintaan untuk `key`. True jika masih dalam batas, False jika lewat."""
    now = time.time()
    cutoff = now - window_seconds
    with _lock:
        dq = _hits.setdefault(key, deque())
        while dq and dq[0] < cutoff:
            dq.popleft()
        if len(dq) >= limit:
            return False
        dq.append(now)
        return True


def limit(prefix: str, limit_count: int, window_seconds: float):
    """Dependency FastAPI: batasi per-IP. Lempar 429 bila terlampaui.

    Pemakaian:
        @router.post("/login", dependencies=[Depends(limit("login", 5, 60))])
    """
    def _dep(request: Request) -> None:
        key = f"{prefix}:{_client_ip(request)}"
        if not hit(key, limit_count, window_seconds):
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Terlalu banyak permintaan. Coba lagi sebentar.",
                headers={"Retry-After": str(int(window_seconds))},
            )

    return _dep
