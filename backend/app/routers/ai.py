"""Router Asisten AI (DeepSeek). Chat pintar yang paham data live aplikasi."""
from __future__ import annotations

import json
import queue
import threading

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field

from ..core.config import get_settings
from ..core.ratelimit import limit
from ..deps import get_current_user, require_admin, require_menu
from ..services import ai_assistant, ai_export, ai_feedback, ai_sheet, app_config, image_search

router = APIRouter(prefix="/api/ai", tags=["ai"])

# Menu Control → centang "Asisten AI" (key 'ai'). Dulu centang itu HANYA
# menyembunyikan menu di sidebar; endpoint tetap terbuka sehingga fiturnya masih
# bisa dipakai lewat URL langsung. Semua jalur PEMAKAIAN asisten kini dijaga.
require_ai = require_menu("ai")

# ── Mode perbaikan (saklar global admin, panel Menu Control) ─────────
# Disimpan di app_config.config['asisten_perbaikan'] (data/ bind-mount —
# selamat dari redeploy). ADMIN KEBAL: pemilik tetap bisa menguji asisten
# selagi user lain melihat popup "sedang perbaikan". Guard dipasang di SEMUA
# endpoint pemakaian (chat/stream/image/sheet), bukan hanya UI — popup tanpa
# guard server bisa dilewati via URL/API langsung.
_PERBAIKAN_MSG = ("Asisten AI sedang dalam perbaikan. Silakan coba lagi nanti — "
                  "fitur lain aplikasi tetap berjalan normal.")


def _perbaikan_untuk(user: dict) -> bool:
    if (user.get("role") or "").lower() == "admin":
        return False
    try:
        return bool((app_config.load().get("config") or {}).get("asisten_perbaikan"))
    except Exception:                       # config rusak tak boleh mematikan asisten
        return False


def _tolak_bila_perbaikan(user: dict) -> None:
    if _perbaikan_untuk(user):
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, _PERBAIKAN_MSG)

_MAX_PHOTO_BYTES = 12 * 1024 * 1024  # 12 MB
_XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


class ChatTurn(BaseModel):
    role: str
    content: str


class AIChatRequest(BaseModel):
    messages: list[ChatTurn] = Field(default_factory=list)
    # Lampiran Excel dari giliran sebelumnya (dari /chat-sheet). Server memverifikasi
    # id ini milik user yang sama; kalau tidak, lampiran diabaikan diam-diam.
    sheet_id: str = ""
    # Id percakapan yang dibuat KLIEN (UUID, di-reset saat "hapus obrolan") →
    # memori sesi server (services/ai_session.py). Opsional: klien lama yang tak
    # mengirimnya tetap berjalan, hanya tanpa ingatan lintas-giliran.
    conversation_id: str = ""


class FeedbackRequest(BaseModel):
    rating: str                                    # 'up' | 'down'
    question: str = ""                             # pesan user yang dijawab
    answer: str = ""                              # jawaban asisten yang dinilai
    tools: list[str] = Field(default_factory=list)  # tool yang dipakai jawaban itu
    note: str = ""                                # catatan opsional (khususnya utk 👎)
    context: list[ChatTurn] = Field(default_factory=list)  # beberapa giliran terakhir


@router.get("/status")
def ai_status(user: dict = Depends(get_current_user)):
    """Asisten siap dipakai user ini? `available` False juga bila menu 'ai'
    dimatikan admin untuk akun ini ATAU mode perbaikan menyala (halaman
    /asisten & aplikasi memakai ini untuk menutup diri + popup perbaikan)."""
    from ..services import permissions
    allowed = "ai" in permissions.effective("menu", user["username"], user.get("role", "user"))
    perbaikan = _perbaikan_untuk(user)
    return {"available": get_settings().ai_configured and allowed and not perbaikan,
            "allowed": allowed, "perbaikan": perbaikan}


@router.post("/chat", dependencies=[Depends(limit("ai_chat", 15, 60))])
def ai_chat(body: AIChatRequest, user: dict = Depends(require_ai)):
    _tolak_bila_perbaikan(user)
    history = [{"role": m.role, "content": m.content} for m in body.messages]
    if not any(m["role"] == "user" and m["content"].strip() for m in history):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Pesan kosong.")
    try:
        result = ai_assistant.chat(user, history, sheet_id=(body.sheet_id or "").strip(),
                                   conversation_id=(body.conversation_id or "").strip())
    except ai_assistant.AINotConfigured:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Asisten AI belum dikonfigurasi (DEEPSEEK_API_KEY kosong).",
        )
    except Exception as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Asisten AI gagal merespons: {e}")
    return result


@router.post("/chat-stream", dependencies=[Depends(limit("ai_chat", 15, 60))])
def ai_chat_stream(body: AIChatRequest, user: dict = Depends(require_ai)):
    """Versi STREAMING dari /chat: kirim event STATUS langkah (SSE) selagi asisten
    bekerja ('Mencari di EPC…', 'Mengambil stok…', 'Menyusun jawaban…'), lalu satu
    event 'done' berisi hasil AKHIR (jawaban sudah disaring guard — tak ada token
    mentah/PN tak-terverifikasi yang di-stream). chat() dijalankan di thread; label
    progress dialirkan lewat queue. /chat lama tetap ada (foto/sheet/klien non-stream)."""
    _tolak_bila_perbaikan(user)
    history = [{"role": m.role, "content": m.content} for m in body.messages]
    if not any(m["role"] == "user" and m["content"].strip() for m in history):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Pesan kosong.")

    q: "queue.Queue" = queue.Queue()
    _SENTINEL = object()
    box: dict = {}

    def _run():
        try:
            box["result"] = ai_assistant.chat(
                user, history, sheet_id=(body.sheet_id or "").strip(),
                on_progress=lambda label: q.put(("progress", label)),
                conversation_id=(body.conversation_id or "").strip())
        except ai_assistant.AINotConfigured:
            box["error"] = "Asisten AI belum dikonfigurasi (DEEPSEEK_API_KEY kosong)."
        except Exception as e:  # pragma: no cover - dijaga generator
            box["error"] = f"Asisten AI gagal merespons: {e}"
        finally:
            q.put((_SENTINEL, None))

    threading.Thread(target=_run, daemon=True, name="ai-chat-stream").start()

    def _frame(obj: dict) -> str:
        return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"

    def _gen():
        while True:
            kind, payload = q.get()
            if kind is _SENTINEL:
                break
            yield _frame({"type": "progress", "label": payload})
        if "error" in box:
            yield _frame({"type": "error", "message": box["error"]})
        else:
            yield _frame({"type": "done", "result": box.get("result")})

    return StreamingResponse(
        _gen(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


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


@router.get("/banding-rangka/export", dependencies=[Depends(limit("ai_export", 20, 60))])
def export_banding_rangka(
    rangka_1: str = Query(..., description="Nomor rangka/VIN unit pertama"),
    rangka_2: str = Query(..., description="Nomor rangka/VIN unit kedua"),
    kategori: str = Query("", description="Kategori (kabin/rem/…); kosong = semua part"),
    _user: dict = Depends(require_ai),
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


@router.get("/excel/{export_id}", dependencies=[Depends(limit("ai_export", 20, 60))])
def export_ai_excel(export_id: str, _user: dict = Depends(require_ai)):
    """File hasil export asisten (Excel `buat_excel` / katalog bergambar Excel|PDF)
    — dipicu kartu 'Unduh' di bawah jawaban. Payload disimpan sementara (TTL) saat
    tool dijalankan; media type mengikuti ekstensi file (xlsx/pdf)."""
    data, fname = ai_export.generic_excel(export_id)
    if data is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, fname)
    fl = fname.lower()
    # Gambar (exploded PNG / diagram wiring JPG) = tampil INLINE (dipakai <img>
    # di chat); file lain = unduh.
    if fl.endswith((".png", ".jpg", ".jpeg")):
        mime_img = "image/png" if fl.endswith(".png") else "image/jpeg"
        return Response(content=data, media_type=mime_img,
                        headers={"Content-Disposition": f'inline; filename="{fname}"',
                                 "Cache-Control": "private, max-age=86400"})
    if fl.endswith(".pdf"):
        # inline → bisa DIBUKA langsung di tab browser (lembar diagnosa SPN/FMI,
        # PDF penawaran/katalog); frontend pakai blob sehingga tetap bisa unduh.
        return Response(content=data, media_type="application/pdf",
                        headers={"Content-Disposition": f'inline; filename="{fname}"',
                                 "Cache-Control": "private, max-age=86400"})
    return Response(
        content=data,
        media_type=_XLSX_MIME,
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


@router.post("/chat-image", dependencies=[Depends(limit("ai_image", 20, 60))])
async def ai_chat_image(
    messages: str = Form("[]", description="Riwayat chat (JSON list {role, content})."),
    file: UploadFile = File(..., description="Foto part untuk dikenali."),
    conversation_id: str = Form("", description="Id percakapan (memori sesi server)."),
    user: dict = Depends(require_ai),
):
    """Chat dengan FOTO: foto dikenali via Cari-by-Foto (DINOv2) → kandidat Part Number
    disuntikkan ke Asisten AI, lalu AI cek stok/harga/unit dan menjelaskan."""
    _tolak_bila_perbaikan(user)
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
        result = ai_assistant.chat(user, history, photo_candidates=candidates,
                                   conversation_id=(conversation_id or "").strip())
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


@router.post("/chat-sheet", dependencies=[Depends(limit("ai_sheet", 10, 60))])
async def ai_chat_sheet(
    messages: str = Form("[]", description="Riwayat chat (JSON list {role, content})."),
    file: UploadFile | None = File(None, description="File Excel (.xlsx/.xlsm) atau CSV (.csv) yang diunggah user."),
    gsheet_url: str = Form("", description="Alternatif file: link berbagi Google Sheets."),
    conversation_id: str = Form("", description="Id percakapan (memori sesi server)."),
    user: dict = Depends(require_ai),
):
    """Chat dengan LAMPIRAN EXCEL. File dibaca di server (kolom dikenali otomatis),
    disimpan sementara (TTL 2 jam, discoped per-user), lalu asisten menjawab dengan
    tool `sheet_ringkasan`/`sheet_isi_kolom`.

    Isi file TIDAK pernah masuk ke system prompt — hanya lewat hasil tool, agar
    kalimat di dalam sel tak bisa menyetir asisten (prompt injection)."""
    _tolak_bila_perbaikan(user)
    try:
        raw = json.loads(messages or "[]")
    except Exception:
        raw = []
    history = [
        {"role": m.get("role"), "content": str(m.get("content") or "")}
        for m in (raw or [])
        if isinstance(m, dict) and m.get("role")
    ]

    # Sumber: file unggahan ATAU link Google Sheets (impor aman docs.google.com).
    if file is not None:
        # Baca BERTAHAP dgn plafon — .xlsx itu ZIP, file kecil bisa mengembang ratusan MB.
        buf = bytearray()
        while chunk := await file.read(1024 * 1024):
            buf.extend(chunk)
            if len(buf) > ai_sheet.MAX_BYTES:
                raise HTTPException(
                    status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    f"File maksimal {ai_sheet.MAX_BYTES // 1024 // 1024} MB.",
                )
        data, fname = bytes(buf), (file.filename or "")
    elif gsheet_url.strip():
        data, err = ai_sheet.import_gsheet(gsheet_url.strip())
        if data is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, err)
        fname = "google_sheet.xlsx"
    else:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "Lampirkan file Excel/CSV atau berikan link Google Sheets.")

    parsed = ai_sheet.parse_upload(data, fname)
    if not parsed.get("ok"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, parsed.get("error") or "File tidak terbaca.")

    sheet_id = ai_sheet.put_sheet(user.get("username", ""), parsed)
    try:
        result = ai_assistant.chat(user, history, sheet_id=sheet_id,
                                   conversation_id=(conversation_id or "").strip())
    except ai_assistant.AINotConfigured:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Asisten AI belum dikonfigurasi (DEEPSEEK_API_KEY kosong).",
        )
    except Exception as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Asisten AI gagal merespons: {e}")

    # Frontend menyimpan sheet_id & menampilkan ringkasan lampiran.
    result["sheet_id"] = sheet_id
    result["sheet"] = ai_sheet.ringkas(parsed)
    return result
