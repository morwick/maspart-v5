"""
Observabilitas Asisten AI (tabel Supabase `ai_chat_log`).

Tujuan: satu baris ringkas per GILIRAN chat → bisa MENGUKUR kualitas & performa
asisten di dunia nyata (yang selama ini buta: log container terhapus tiap
recreate). Yang dicatat = metadata, BUKAN rahasia: pertanyaan (dipotong), peran,
tool yang dipakai, jumlah ronde, latensi, apakah guard menyala / ada tool gagal,
dan outcome jawaban.

Resilient: bila tabel belum dibuat / Supabase down, log_turn diam (best-effort)
dan TIDAK pernah menjatuhkan jawaban chat. DDL ada di create_table_sql().
"""
from __future__ import annotations

import time

import requests

from .supabase_client import _rest_url, _service_headers

_TIMEOUT = 10


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def create_table_sql() -> str:
    """DDL Supabase (jalankan sekali di SQL Editor). Identik dgn migrations/016."""
    return (
        "create table if not exists ai_chat_log (\n"
        "  id bigint generated always as identity primary key,\n"
        "  created_at timestamptz not null default now(),\n"
        "  username text,\n"
        "  role text,\n"
        "  question text,\n"
        "  tools text,\n"
        "  tools_count int not null default 0,\n"
        "  rounds int not null default 0,\n"
        "  latency_ms int not null default 0,\n"
        "  guard_hit boolean not null default false,\n"
        "  tool_failed boolean not null default false,\n"
        "  reply_len int not null default 0,\n"
        "  outcome text\n"  # ok | not_found | empty | sanitized
        ");\n"
        "create index if not exists ai_chat_log_created_idx on ai_chat_log (created_at desc);\n"
    )


def log_turn(*, username: str | None, role: str | None, question: str,
             tools_used: list[str] | None, rounds: int, latency_ms: int,
             guard_hit: bool, tool_failed: bool, reply_len: int,
             outcome: str) -> bool:
    """Simpan satu baris observabilitas. Best-effort: False bila gagal/tabel absen,
    TAK melempar (pemanggil membungkus lagi, tapi tetap aman di sini)."""
    try:
        tools = tools_used or []
        r = requests.post(
            _rest_url("ai_chat_log"),
            headers=_service_headers("return=minimal"),
            json={
                "username": (username or None),
                "role": (role or None),
                "question": (question or "")[:500] or None,
                "tools": (", ".join(tools) if tools else None),
                "tools_count": len(tools),
                "rounds": int(rounds),
                "latency_ms": int(latency_ms),
                "guard_hit": bool(guard_hit),
                "tool_failed": bool(tool_failed),
                "reply_len": int(reply_len),
                "outcome": outcome or None,
                "created_at": _now(),
            },
            timeout=_TIMEOUT,
        )
        return r.status_code in (200, 201, 204)
    except Exception:
        return False


def list_logs(limit: int = 200) -> list[dict]:
    """Baris observabilitas terbaru dulu (untuk halaman admin)."""
    try:
        r = requests.get(
            _rest_url("ai_chat_log"),
            headers={**_service_headers(), "Accept": "application/json"},
            params={
                "select": ("id,created_at,username,role,question,tools,tools_count,"
                           "rounds,latency_ms,guard_hit,tool_failed,reply_len,outcome"),
                "order": "created_at.desc",
                "limit": str(max(1, min(limit, 1000))),
            },
            timeout=_TIMEOUT,
        )
        r.raise_for_status()
        return r.json() or []
    except Exception:
        return []


def summary() -> dict:
    """Ringkasan dari maks 1000 baris terbaru (agregasi di Python — Supabase REST
    tanpa RPC). Cukup untuk tren harian: volume, latensi, rasio guard/tool-gagal,
    tool tersering, outcome."""
    rows = list_logs(limit=1000)
    n = len(rows)
    if not n:
        return {"total": 0}
    lat = sorted(int(r.get("latency_ms") or 0) for r in rows)
    guard = sum(1 for r in rows if r.get("guard_hit"))
    failed = sum(1 for r in rows if r.get("tool_failed"))
    tool_freq: dict[str, int] = {}
    outcome_freq: dict[str, int] = {}
    for r in rows:
        for t in (r.get("tools") or "").split(", "):
            t = t.strip()
            if t:
                tool_freq[t] = tool_freq.get(t, 0) + 1
        o = (r.get("outcome") or "?").strip()
        outcome_freq[o] = outcome_freq.get(o, 0) + 1

    def _pct(i: int) -> int:
        return lat[min(len(lat) - 1, int(len(lat) * i / 100))]

    return {
        "total": n,
        "latensi_ms": {"p50": _pct(50), "p90": _pct(90), "maks": lat[-1]},
        "guard_menyala": guard,
        "guard_rasio_persen": round(100 * guard / n, 1),
        "tool_gagal": failed,
        "tool_gagal_rasio_persen": round(100 * failed / n, 1),
        "tool_tersering": sorted(tool_freq.items(), key=lambda kv: -kv[1])[:10],
        "outcome": outcome_freq,
    }
