"""Router Asisten AI (DeepSeek). Chat pintar yang paham data live aplikasi."""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from fastapi.responses import Response
from pydantic import BaseModel, Field

from ..core.config import get_settings
from ..core.ratelimit import limit
from ..deps import get_current_user, require_admin
from ..services import ai_assistant, ai_export, ai_feedback, image_search

router = APIRouter(prefix="/api/ai", tags=["ai"])

_MAX_PHOTO_BYTES = 12 * 1024 * 1024  # 12 MB
_XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


class ChatTurn(BaseModel):
    role: str
    content: str


class AIChatRequest(BaseModel):
    messages: list[ChatTurn] = Field(default_factory=list)


class FeedbackRequest(BaseModel):
    rating: str                                    # 'up' | 'down'
    question: str = ""                             # pesan user yang dijawab
    answer: str = ""                              # jawaban asisten yang dinilai
    tools: list[str] = Field(default_factory=list)  # tool yang dipakai jawaban itu
    note: str = ""                                # catatan opsional (khususnya utk 👎)
    context: list[ChatTurn] = Field(default_factory=list)  # beberapa giliran terakhir


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


@router.post("/feedback", dependencies=[Depends(limit("ai_feedback", 40, 60))])
def submit_feedback(body: FeedbackRequest, user: dict = Depends(get_current_user)):
    """Simpan 👍/👎 user atas satu jawaban asisten (bahan perbaikan). Semua user
    login boleh memberi feedback. Gagal simpan = 502 (mis. tabel belum dibuat)."""
    if body.rating not in ("up", "down"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "rating harus 'up' atau 'down'.")
    ok = ai_feedback.add_feedback(
        username=user.get("username"),
        role=user.get("role"),
        rating=body.rating,
        question=body.question,
        answer=body.answer,
        tools=body.tools,
        note=body.note,
        # Simpan hanya beberapa giliran terakhir (dipangkas) sebagai konteks review.
        context=[{"role": t.role, "content": (t.content or "")[:2000]} for t in body.context][-8:],
    )
    if not ok:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            "Gagal menyimpan umpan balik. Pastikan tabel 'ai_feedback' sudah dibuat di Supabase.",
        )
    return {"ok": True}


@router.get("/feedback")
def list_feedback(rating: str | None = None, only_open: bool = False,
                  user: dict = Depends(require_admin)):
    """Daftar umpan balik untuk ADMIN review. rating='down' fokus yang jelek;
    only_open=true sembunyikan yang sudah ditandai selesai."""
    rows = ai_feedback.list_feedback(rating=rating, only_open=only_open)
    return {"ringkasan": ai_feedback.summary(), "jumlah": len(rows), "feedback": rows}


@router.post("/feedback/{fb_id}/resolve")
def resolve_feedback(fb_id: int, resolved: bool = True,
                     user: dict = Depends(require_admin)):
    """Tandai satu umpan balik sudah/belum ditangani (triase admin)."""
    if not ai_feedback.mark_resolved(fb_id, resolved):
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Gagal memperbarui status.")
    return {"ok": True, "id": fb_id, "resolved": resolved}


@router.get("/banding-rangka/export")
def export_banding_rangka(
    rangka_1: str = Query(..., description="Nomor rangka/VIN unit pertama"),
    rangka_2: str = Query(..., description="Nomor rangka/VIN unit kedua"),
    kategori: str = Query("", description="Kategori (kabin/rem/…); kosong = semua part"),
    _user: dict = Depends(get_current_user),
):
    """Excel perbandingan LENGKAP (tanpa cap) dua unit — dipicu tombol 'Unduh Excel'
    di bawah jawaban perbandingan asisten."""
    data, fname = ai_export.banding_rangka_excel(rangka_1, rangka_2, kategori)
    if data is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, fname)
    return Response(
        content=data,
        media_type=_XLSX_MIME,
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


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
