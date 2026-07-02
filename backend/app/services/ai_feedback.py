"""
Umpan balik Asisten AI (tabel Supabase `ai_feedback`).

Tujuan: kumpulkan 👍/👎 user atas tiap jawaban asisten → jadi ANTREAN PERBAIKAN.
Jawaban yang di-👎 (beserta pertanyaan, tool yang dipakai, dan catatan user)
tersimpan supaya admin bisa menelusuri & memperbaiki sumber data / prompt / tool.

Resilient: bila tabel belum dibuat di Supabase, fungsi kembalikan False/[] tanpa
menjatuhkan request chat. SQL pembuatan tabel ada di docstring create_table_sql().
"""
from __future__ import annotations

import time

import requests

from .supabase_client import _rest_url, _service_headers

_TIMEOUT = 15


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def create_table_sql() -> str:
    """DDL Supabase (jalankan sekali di SQL Editor) — disediakan agar mudah setup."""
    return (
        "create table if not exists ai_feedback (\n"
        "  id bigint generated always as identity primary key,\n"
        "  created_at timestamptz not null default now(),\n"
        "  username text,\n"
        "  role text,\n"
        "  rating text not null check (rating in ('up','down')),\n"
        "  question text,\n"
        "  answer text,\n"
        "  tools text,\n"
        "  note text,\n"
        "  context jsonb,\n"
        "  resolved boolean not null default false\n"
        ");\n"
        "create index if not exists ai_feedback_created_idx on ai_feedback (created_at desc);\n"
        "create index if not exists ai_feedback_rating_idx on ai_feedback (rating);\n"
    )


def add_feedback(*, username: str | None, role: str | None, rating: str,
                 question: str, answer: str, tools: list[str] | None,
                 note: str, context: list[dict] | None) -> bool:
    """Simpan satu umpan balik. rating: 'up' | 'down'. False bila gagal/tabel absen."""
    if rating not in ("up", "down"):
        return False
    try:
        r = requests.post(
            _rest_url("ai_feedback"),
            headers=_service_headers("return=minimal"),
            json={
                "username": (username or None),
                "role": (role or None),
                "rating": rating,
                "question": (question or "")[:4000] or None,
                "answer": (answer or "")[:8000] or None,
                "tools": (", ".join(tools) if tools else None),
                "note": (note or "")[:2000] or None,
                "context": (context or None),
                "created_at": _now(),
            },
            timeout=_TIMEOUT,
        )
        return r.status_code in (200, 201, 204)
    except Exception:
        return False


def list_feedback(rating: str | None = None, only_open: bool = False,
                  limit: int = 200) -> list[dict]:
    """Daftar umpan balik terbaru dulu. rating='down' untuk fokus yang jelek;
    only_open=True untuk sembunyikan yang sudah ditandai 'resolved'."""
    try:
        params = {
            "select": "id,created_at,username,role,rating,question,answer,tools,note,resolved",
            "order": "created_at.desc",
            "limit": str(max(1, min(limit, 1000))),
        }
        if rating in ("up", "down"):
            params["rating"] = f"eq.{rating}"
        if only_open:
            params["resolved"] = "eq.false"
        r = requests.get(
            _rest_url("ai_feedback"),
            headers={**_service_headers(), "Accept": "application/json"},
            params=params,
            timeout=_TIMEOUT,
        )
        r.raise_for_status()
        return r.json() or []
    except Exception:
        return []


def summary() -> dict:
    """Ringkasan jumlah 👍/👎 (dihitung dari data terbaru, cap 1000)."""
    rows = list_feedback(limit=1000)
    up = sum(1 for r in rows if r.get("rating") == "up")
    down = sum(1 for r in rows if r.get("rating") == "down")
    open_down = sum(1 for r in rows if r.get("rating") == "down" and not r.get("resolved"))
    return {"total": len(rows), "up": up, "down": down, "down_belum_ditangani": open_down}


def mark_resolved(fb_id: int, resolved: bool = True) -> bool:
    """Tandai satu umpan balik sudah/belum ditangani (untuk triase admin)."""
    try:
        r = requests.patch(
            _rest_url("ai_feedback"),
            headers=_service_headers("return=minimal"),
            params={"id": f"eq.{int(fb_id)}"},
            json={"resolved": bool(resolved)},
            timeout=_TIMEOUT,
        )
        return r.status_code in (200, 204)
    except Exception:
        return False
