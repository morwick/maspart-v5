"""Router metadata aplikasi mobile — endpoint PUBLIK yang dipanggil aplikasi
setiap kali dibuka (`ApiService.appMeta()`), untuk notifikasi update in-app +
feature-flag server-driven.

Sengaja tanpa autentikasi: aplikasi memanggilnya sebelum/di luar sesi login, dan
isinya bukan data sensitif (nomor versi + flag tampilan). Aplikasi menelan
kegagalan apa pun (`catch (_) → pakai default`), jadi endpoint ini tidak boleh
melempar — kegagalan baca file sudah ditangani di `app_config.load()`.
"""
from __future__ import annotations

from fastapi import APIRouter

from ..services import app_config

router = APIRouter(prefix="/api/app", tags=["app"])


@router.get("/meta")
def app_meta():
    cfg = app_config.load()
    return {"version": cfg["version"], "config": cfg["config"]}
