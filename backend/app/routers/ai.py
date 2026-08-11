"""Router Asisten AI (DeepSeek). Chat pintar yang paham data live aplikasi."""
from __future__ import annotations

import json
import queue
import threading

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field

from ..core.config import get_settings
from ..core.ratelimit import limit
from ..deps import get_current_user, require_admin, require_menu
from ..services import ai_assistant, ai_export, ai_feedback, ai_sheet, app_config, vin_ocr

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
    # OPT-IN streaming token di /chat-stream: klien yang tahu cara menangani frame
    # `delta`/`reset` mengirim true. Default false = protokol lama persis (hanya
    # progress + done), jadi klien web lama & APK 2.2.0 tak terpengaruh.
    stream_tokens: bool = False


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
    out = {"available": get_settings().ai_configured and allowed and not perbaikan,
           "allowed": allowed, "perbaikan": perbaikan}
    # Tawaran belajar di layar pembuka — HANYA untuk yang boleh mengajar
    # (peta kelemahan asisten cukup dilihat orang yang bisa memperbaikinya).
    # Best-effort: gagal membaca gap tak boleh mematikan status asisten.
    if out["available"]:
        try:
            role = (user.get("role") or "").lower()
            boleh_ajar = role == "admin" or (
                role != "pembeli"
                and "ai_mengajar" in permissions.effective("asisten", user["username"], role))
            if boleh_ajar:
                from ..services import ai_belajar
                rows = sorted(ai_belajar.gaps(), key=lambda g: -(g.get("jumlah") or 0))
                if rows:
                    out["gap_ajar"] = {"jumlah": len(rows),
                                       "topik": [g.get("topik") for g in rows[:3]]}
        except Exception:  # pragma: no cover — tawaran hilang, asisten tetap jalan
            pass
    return out


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
    event 'done' berisi hasil AKHIR. chat() dijalankan di thread; label progress
    dialirkan lewat queue. /chat lama tetap ada (foto/sheet/klien non-stream).

    Frame: {type:'progress'|'delta'|'reset'|'done'|'error'}.

    ⚠️ `stream_tokens:true` (OPT-IN) MEMBALIKKAN keputusan lama "tak ada token
    mentah di-stream". Alasannya diukur, bukan selera: giliran p50 15 dtk / p90 46
    dtk dilewatkan user dengan layar kosong, padahal teksnya sudah ditulis model
    sejak detik ke-3. Pembalikan ini disetujui pemilik dengan pagar:
      - yang dialirkan adalah DRAF ('delta'), blok [PIKIR] sudah disaring;
      - begitu guard menyala (±19% giliran: PN tak ter-ground, klaim Excel, DTC/
        EPC-first) server mengirim 'reset' → klien WAJIB mengosongkan draf;
      - frame 'done' tetap SATU-SATUNYA kebenaran dan selalu berisi teks yang
        sudah lewat semua guard — ia MENGGANTI seluruh draf, bukan menambahnya.
    Tanpa `stream_tokens` protokolnya identik dengan sebelumnya (progress+done)."""
    _tolak_bila_perbaikan(user)
    history = [{"role": m.role, "content": m.content} for m in body.messages]
    if not any(m["role"] == "user" and m["content"].strip() for m in history):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Pesan kosong.")
    return _sse_chat(user, history, sheet_id=(body.sheet_id or "").strip(),
                     conversation_id=(body.conversation_id or "").strip(),
                     stream_tokens=bool(body.stream_tokens))


def _sse_chat(user: dict, history: list[dict], sheet_id: str = "",
              conversation_id: str = "", stream_tokens: bool = False,
              extra: dict | None = None) -> StreamingResponse:
    """Jalankan satu giliran chat di thread & alirkan frame SSE.

    SATU sumber untuk /chat-stream DAN /chat-sheet?stream=1 — giliran ber-LAMPIRAN
    dulu tak punya jalur ini sama sekali, sehingga user yang mengunggah Excel
    menatap layar tanpa indikator apa pun sampai jawaban jadi (keluhan pemilik
    2026-08-06). `extra` = field yang ditempelkan ke hasil di frame `done`
    (mis. sheet_id & ringkasan lampiran)."""
    q: "queue.Queue" = queue.Queue()
    _SENTINEL = object()
    box: dict = {}

    def _run():
        try:
            # Kwarg on_delta KONDISIONAL: tanpa opt-in, chat() dipanggil dengan
            # tanda tangan yang persis sama seperti sebelum fitur ini.
            kw: dict = {}
            if stream_tokens:
                kw["on_delta"] = lambda potongan: q.put(("delta", potongan))
            box["result"] = ai_assistant.chat(
                user, history, sheet_id=sheet_id,
                on_progress=lambda label: q.put(("progress", label)),
                conversation_id=conversation_id, **kw)
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
            if kind == "delta":
                # payload None = draf dibatalkan (guard/retry) → klien kosongkan.
                yield _frame({"type": "reset"} if payload is None
                             else {"type": "delta", "text": payload})
                continue
            yield _frame({"type": "progress", "label": payload})
        if "error" in box:
            yield _frame({"type": "error", "message": box["error"]})
        else:
            hasil = dict(box.get("result") or {})
            hasil.update(extra or {})
            yield _frame({"type": "done", "result": hasil})

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


@router.post("/ocr-rangka", dependencies=[Depends(limit("ai_ocr", 20, 60))])
async def ai_ocr_rangka(
    file: UploadFile = File(..., description="Foto nomor rangka (JPG/PNG)."),
    user: dict = Depends(require_ai),
):
    """FOTO nomor rangka → TEKS nomor rangka (OCR di server, tanpa model bahasa).

    Dipakai saat asisten meminta nomor rangka: user di lapangan memotret nomor
    yang dipahat di chassis, klien mengunggahnya ke sini, lalu MENGIRIM hasil
    bacanya sebagai pesan chat biasa. Asisten sendiri tetap tak pernah melihat
    gambar — yang sampai padanya hanya teks.

    Klien memutuskan lewat `keyakinan`: 'pasti'/'tinggi' boleh langsung dikirim,
    'rendah' WAJIB ditawarkan ke user untuk dikoreksi dulu (satu huruf salah =
    unit yang salah), 'gagal' → minta foto ulang."""
    _tolak_bila_perbaikan(user)
    buf = bytearray()
    while chunk := await file.read(512 * 1024):
        buf.extend(chunk)
        if len(buf) > vin_ocr.MAX_BYTES:
            raise HTTPException(
                status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                f"Foto maksimal {vin_ocr.MAX_BYTES // 1024 // 1024} MB.")
    if not buf:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Foto kosong.")
    try:
        # OCR memakan 0,4–3 detik CPU untuk foto wajar, dan sampai ±20 detik untuk
        # foto sulit yang harus menempuh semua jalan (plat beretsa, foto rebah) →
        # threadpool, jangan menahan event loop: satu unggahan foto tak boleh
        # membekukan chat user lain.
        return await run_in_threadpool(vin_ocr.baca_rangka, bytes(buf))
    except ValueError as e:                       # format foto tak didukung
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))
    except ImportError:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Pembaca nomor rangka belum terpasang di server (paket OCR absen).")
    except Exception as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Gagal membaca foto: {e}")


@router.post("/chat-sheet", dependencies=[Depends(limit("ai_sheet", 10, 60))])
async def ai_chat_sheet(
    messages: str = Form("[]", description="Riwayat chat (JSON list {role, content})."),
    file: UploadFile | None = File(None, description="File Excel (.xlsx/.xlsm) atau CSV (.csv) yang diunggah user."),
    gsheet_url: str = Form("", description="Alternatif file: link berbagi Google Sheets."),
    conversation_id: str = Form("", description="Id percakapan (memori sesi server)."),
    stream: bool = Form(False, description="true → jawaban dialirkan SSE (progress/delta/done)."),
    stream_tokens: bool = Form(False, description="Khusus stream=true: ikut alirkan DRAF jawaban."),
    user: dict = Depends(require_ai),
):
    """Chat dengan LAMPIRAN EXCEL. File dibaca di server (kolom dikenali otomatis),
    disimpan sementara (TTL 2 jam, discoped per-user), lalu asisten menjawab dengan
    tool `sheet_ringkasan`/`sheet_isi_kolom`.

    `stream=true` → respons SSE dengan frame yang SAMA seperti /chat-stream
    (progress/delta/reset/done), dan frame `done` membawa `sheet_id` + ringkasan
    lampiran seperti respons JSON biasa. Tanpa `stream` perilakunya persis seperti
    dulu (satu JSON di akhir) — klien lama & APK lama tak terpengaruh.
    ⚠️ Giliran ber-lampiran bisa berjalan menit-menit (isi kolom + foto + gambar
    teknis); tanpa jalur stream, user hanya melihat layar diam tanpa indikator —
    itu keluhan pemilik 2026-08-06 yang melahirkan opsi ini.

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
    # Ringkasan lampiran & sheet_id SELALU ikut hasil — di jalur SSE ia ditempel
    # ke frame `done` (lihat _sse_chat `extra`), di jalur JSON di bawah.
    extra = {"sheet_id": sheet_id, "sheet": ai_sheet.ringkas(parsed)}
    if stream:
        # Berkas sudah terbaca & tersimpan → galat file tetap jadi HTTP 4xx biasa
        # (di atas), bukan error di tengah aliran yang sulit ditangani klien.
        return _sse_chat(user, history, sheet_id=sheet_id,
                         conversation_id=(conversation_id or "").strip(),
                         stream_tokens=bool(stream_tokens), extra=extra)
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
    result.update(extra)
    return result
