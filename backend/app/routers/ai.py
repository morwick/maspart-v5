"""Router Asisten AI (DeepSeek). Chat pintar yang paham data live aplikasi."""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from pydantic import BaseModel, Field

from ..core.config import get_settings
from ..core.ratelimit import limit
from ..deps import get_current_user
from ..services import ai_assistant, image_search

router = APIRouter(prefix="/api/ai", tags=["ai"])

_MAX_PHOTO_BYTES = 12 * 1024 * 1024  # 12 MB


class ChatTurn(BaseModel):
    role: str
    content: str


class AIChatRequest(BaseModel):
    messages: list[ChatTurn] = Field(default_factory=list)


@router.get("/status")
def ai_status(user: dict = Depends(get_current_user)):
    return {"available": get_settings().ai_configured}


@router.post("/chat")
def ai_chat(body: AIChatRequest, user: dict = Depends(get_current_user)):
    history = [{"role": m.role, "content": m.content} for m in body.messages]
    if not any(m["role"] == "user" and m["content"].strip() for m in history):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Pesan kosong.")
    try:
        result = ai_assistant.chat(user, history)
    except ai_assistant.AINotConfigured:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Asisten AI belum dikonfigurasi (DEEPSEEK_API_KEY kosong).",
        )
    except Exception as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Asisten AI gagal merespons: {e}")
    return result


@router.post("/chat-image", dependencies=[Depends(limit("ai_image", 20, 60))])
async def ai_chat_image(
    messages: str = Form("[]", description="Riwayat chat (JSON list {role, content})."),
    file: UploadFile = File(..., description="Foto part untuk dikenali."),
    user: dict = Depends(get_current_user),
):
    """Chat dengan FOTO: foto dikenali via Cari-by-Foto (DINOv2) → kandidat Part Number
    disuntikkan ke Asisten AI, lalu AI cek stok/harga/unit dan menjelaskan."""
    try:
        raw = json.loads(messages or "[]")
    except Exception:
        raw = []
    history = [
        {"role": m.get("role"), "content": str(m.get("content") or "")}
        for m in (raw or [])
        if isinstance(m, dict) and m.get("role")
    ]
    data = await file.read()
    if not data:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Foto kosong.")
    if len(data) > _MAX_PHOTO_BYTES:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "Foto maksimal 12 MB.")

    try:
        candidates = image_search.search_by_image(data, top_k=6, threshold=0.30)
    except Exception:
        candidates = []

    try:
        result = ai_assistant.chat(user, history, photo_candidates=candidates)
    except ai_assistant.AINotConfigured:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Asisten AI belum dikonfigurasi (DEEPSEEK_API_KEY kosong).",
        )
    except Exception as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Asisten AI gagal merespons: {e}")

    # Sertakan kandidat (untuk thumbnail/transparansi di frontend).
    result["photo_candidates"] = [
        {
            "part_number": c.get("part_number"),
            "part_name": c.get("part_name"),
            "similarity": c.get("similarity"),
            "sims_url": c.get("sims_url"),
        }
        for c in (candidates or [])[:6]
    ]
    return result
