"""Endpoint /api/ai/chat-sheet dengan `stream=true` — giliran ber-LAMPIRAN ikut
mengalirkan STATUS langkah lewat SSE.

Latar (keluhan pemilik 2026-08-06): saat mengirim Excel untuk diisikan gambar,
asisten "seperti tidak ada respon" — tak ada "Menyusun jawaban…" seperti giliran
biasa. Sebabnya endpoint lampiran memang satu-satunya jalur TANPA streaming,
padahal justru giliran terlama (baca file + isi kolom + foto + gambar teknis).

Yang dijaga:
  • stream=true → frame progress/delta/reset/done seperti /chat-stream;
  • frame `done` TETAP membawa sheet_id + ringkasan lampiran (tanpa itu klien
    kehilangan lampirannya untuk giliran berikutnya);
  • tanpa `stream` perilakunya persis seperti dulu (satu JSON) — APK lama aman;
  • file rusak tetap HTTP 400 (bukan error di tengah aliran).
"""
from __future__ import annotations

import asyncio
import io
import json

import pytest
from fastapi import HTTPException
from openpyxl import Workbook

from app.routers import ai as ai_router
from app.services import app_config

USER = {"username": "budi", "role": "staf"}


def _xlsx() -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.append(["Part Number", "Qty"])
    ws.append(["WG9725520278", 2])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


class _Upload:
    """UploadFile tiruan secukupnya (dibaca bertahap oleh endpoint)."""

    def __init__(self, data: bytes, filename: str = "recom.xlsx"):
        self.filename = filename
        self._buf = io.BytesIO(data)

    async def read(self, n: int = -1) -> bytes:
        return self._buf.read(n)


@pytest.fixture
def tanpa_perbaikan(monkeypatch):
    monkeypatch.setattr(app_config, "load", lambda: {"version": {}, "config": {}})


def _panggil(monkeypatch, fake_chat, stream: bool = False, stream_tokens: bool = False):
    """Panggil endpoint LANGSUNG (tanpa FastAPI). ⚠️ Default `Form(False)` adalah
    OBJEK (truthy) saat fungsinya dipanggil langsung — jadi nilai stream/
    stream_tokens WAJIB diberikan eksplisit di sini, kalau tidak test 'tanpa
    stream' justru menguji jalur stream."""
    monkeypatch.setattr(ai_router.ai_assistant, "chat", fake_chat)
    return asyncio.run(ai_router.ai_chat_sheet(
        messages=json.dumps([{"role": "user", "content": "isikan gambarnya"}]),
        file=_Upload(_xlsx()), gsheet_url="", conversation_id="c1",
        stream=stream, stream_tokens=stream_tokens, user=USER))


def _frames(resp) -> list[dict]:
    async def _kumpul():
        out = []
        async for chunk in resp.body_iterator:
            if isinstance(chunk, bytes):
                chunk = chunk.decode("utf-8")
            for baris in chunk.strip().split("\n"):
                if baris.startswith("data: "):
                    out.append(json.loads(baris[6:]))
        return out
    return asyncio.run(_kumpul())


def _chat_palsu(tangkap: dict):
    def fake(user, history, sheet_id="", on_progress=None, conversation_id="", **kw):
        tangkap.update(sheet_id=sheet_id, conversation_id=conversation_id, kw=kw)
        if on_progress:
            on_progress("Membaca lampiran…")
            on_progress("Mengisi Excel lampiran…")
        cb = kw.get("on_delta")
        if cb:
            cb("Sedang ")
            cb(None)
            cb("Selesai.")
        return {"reply": "Selesai.", "tools_used": ["sheet_isi_kolom"]}
    return fake


def test_stream_mengalirkan_status_langkah(monkeypatch, tanpa_perbaikan):
    tangkap: dict = {}
    resp = _panggil(monkeypatch, _chat_palsu(tangkap), stream=True)
    frames = _frames(resp)
    assert [f["type"] for f in frames] == ["progress", "progress", "done"]
    assert [f["label"] for f in frames[:2]] == ["Membaca lampiran…", "Mengisi Excel lampiran…"]
    assert tangkap["sheet_id"] and tangkap["conversation_id"] == "c1"
    assert "on_delta" not in tangkap["kw"]        # opt-in, sama seperti /chat-stream


def test_frame_done_tetap_membawa_sheet_id_dan_ringkasan(monkeypatch, tanpa_perbaikan):
    """Tanpa ini klien kehilangan lampiran untuk giliran berikutnya."""
    tangkap: dict = {}
    frames = _frames(_panggil(monkeypatch, _chat_palsu(tangkap), stream=True))
    hasil = frames[-1]["result"]
    assert hasil["reply"] == "Selesai." and hasil["tools_used"] == ["sheet_isi_kolom"]
    assert hasil["sheet_id"] == tangkap["sheet_id"]
    assert hasil["sheet"]["kolom_part_number"] == "Part Number"


def test_stream_tokens_opt_in(monkeypatch, tanpa_perbaikan):
    tangkap: dict = {}
    frames = _frames(_panggil(monkeypatch, _chat_palsu(tangkap),
                              stream=True, stream_tokens=True))
    assert "on_delta" in tangkap["kw"]
    assert [f["type"] for f in frames] == [
        "progress", "progress", "delta", "reset", "delta", "done"]


def test_tanpa_stream_tetap_json_seperti_dulu(monkeypatch, tanpa_perbaikan):
    """APK & klien web lama tak mengirim `stream` — jawabannya harus tetap dict."""
    tangkap: dict = {}
    hasil = _panggil(monkeypatch, _chat_palsu(tangkap))
    assert isinstance(hasil, dict)
    assert hasil["reply"] == "Selesai."
    assert hasil["sheet_id"] and hasil["sheet"]["kolom_part_number"] == "Part Number"


def test_file_rusak_tetap_http_400_bukan_error_di_tengah_aliran(monkeypatch, tanpa_perbaikan):
    monkeypatch.setattr(ai_router.ai_assistant, "chat",
                        lambda *a, **k: pytest.fail("chat tak boleh jalan"))
    with pytest.raises(HTTPException) as e:
        asyncio.run(ai_router.ai_chat_sheet(
            messages="[]", file=_Upload(b"bukan excel", "rusak.xlsx"),
            gsheet_url="", conversation_id="", stream=True, stream_tokens=False,
            user=USER))
    assert e.value.status_code == 400
