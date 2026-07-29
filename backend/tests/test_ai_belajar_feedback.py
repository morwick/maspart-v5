"""Umpan balik 👎 masuk ke penambangan gap topik (ai_belajar).

Sebelumnya `ai_feedback` sama sekali tidak dibaca oleh apa pun kecuali panel
admin: user menekan jempol ke bawah, dan sinyal terkuat yang kita punya berhenti
di situ. Kini ia menambah bobot pada kelompok gap yang sama — dengan watermark
SENDIRI, karena watermark chat-log akan diam-diam melewatinya.
"""
from __future__ import annotations

import pytest

from app.services import ai_belajar


@pytest.fixture
def feedback(monkeypatch):
    """Ganti ai_feedback.list_feedback dengan daftar yang bisa diatur test.

    Dipatch pada MODUL aslinya, bukan lewat sys.modules: begitu modul itu pernah
    diimpor test lain, `from . import ai_feedback` mengambil atribut paket dan
    stub di sys.modules diabaikan diam-diam — test lolos sendirian lalu gagal di
    suite penuh."""
    from app.services import ai_feedback

    rows: list[dict] = []
    dipanggil: dict = {}

    def fake(rating=None, only_open=False, limit=200):
        dipanggil["rating"] = rating
        return list(rows)

    monkeypatch.setattr(ai_feedback, "list_feedback", fake)
    return rows, dipanggil


def test_dislike_menambah_bobot_gap(feedback):
    rows, dipanggil = feedback
    rows.append({"created_at": "2026-07-29T10:00:00Z",
                 "question": "kenapa truknya loyo waktu nanjak"})
    state: dict = {}
    acc = ai_belajar._serap_feedback_down({}, state)
    assert dipanggil["rating"] == "down"          # hanya 👎, bukan 👍
    (g,) = acc.values()
    assert g["jumlah"] == ai_belajar._BOBOT_DISLIKE
    assert g["dislike"] == 1
    assert "loyo" in g["contoh"]


def test_dislike_menumpuk_di_kelompok_yang_sama(feedback):
    """Bergabung dengan akumulator gap dari chat-log, bukan bikin kelompok baru."""
    rows, _ = feedback
    q = "kenapa truknya loyo waktu nanjak"
    rows.append({"created_at": "2026-07-29T10:00:00Z", "question": q})
    kunci = ai_belajar._gap_key(q)
    acc = {kunci: {"topik": kunci, "contoh": q, "jumlah": 2, "terakhir": ""}}
    out = ai_belajar._serap_feedback_down(acc, {})
    assert out[kunci]["jumlah"] == 2 + ai_belajar._BOBOT_DISLIKE


def test_watermark_mencegah_hitung_ganda(feedback):
    rows, _ = feedback
    rows.append({"created_at": "2026-07-29T10:00:00Z", "question": "rem blong terus"})
    state: dict = {}
    a = ai_belajar._serap_feedback_down({}, state)
    assert state["watermark_feedback"] == "2026-07-29T10:00:00Z"
    b = ai_belajar._serap_feedback_down(a, state)   # putaran kedua, data sama
    assert list(b.values())[0]["jumlah"] == ai_belajar._BOBOT_DISLIKE


def test_watermark_terpisah_dari_chat_log(feedback):
    """Kalau memakai watermark chat-log, 👎 hilang setiap kali user lain sempat
    mengobrol setelahnya — persis sinyal yang paling ingin ditangkap."""
    rows, _ = feedback
    rows.append({"created_at": "2026-07-29T10:00:00Z", "question": "rem blong terus"})
    state = {"watermark_created_at": "2026-07-29T23:59:00Z"}   # chat-log jauh di depan
    acc = ai_belajar._serap_feedback_down({}, state)
    assert acc, "👎 tak boleh terlewat hanya karena chat-log lebih baru"


def test_tabel_feedback_gagal_tak_menjatuhkan_penambangan(monkeypatch):
    from app.services import ai_feedback

    def meledak(**kw):
        raise RuntimeError("tabel ai_feedback belum dibuat")

    monkeypatch.setattr(ai_feedback, "list_feedback", meledak)
    acc = {"x": {"topik": "x", "contoh": "x", "jumlah": 1, "terakhir": ""}}
    assert ai_belajar._serap_feedback_down(acc, {}) == acc


def test_pertanyaan_kosong_diabaikan(feedback):
    rows, _ = feedback
    rows.append({"created_at": "2026-07-29T10:00:00Z", "question": "   "})
    assert ai_belajar._serap_feedback_down({}, {}) == {}
