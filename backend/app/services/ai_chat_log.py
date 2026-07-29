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

import threading
import time

import requests

from .supabase_client import _rest_url, _service_headers

_TIMEOUT = 10


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _cutoff(days: int) -> str:
    """ISO UTC untuk (now - days hari) — batas 'lebih tua dari'."""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - days * 86400))


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
        "  outcome text,\n"  # ok | not_found | empty | sanitized
        "  tokens_in int not null default 0,\n"         # migrations/021
        "  tokens_out int not null default 0,\n"
        "  tokens_cache_hit int not null default 0,\n"
        "  api_calls int not null default 0,\n"
        "  reply text,\n"                                # migrations/022 (teks jawaban AI)
        "  tools_failed text,\n"                         # migrations/023 (tool yang gagal)
        "  session_id text\n"                            # migrations/025 (percakapan)
        ");\n"
        "create index if not exists ai_chat_log_created_idx on ai_chat_log (created_at desc);\n"
        "create index if not exists ai_chat_log_session_idx on ai_chat_log (session_id);\n"
    )


_REPLY_CAP = 4000  # teks jawaban di-cap (bisa beberapa KB) — cukup utk monitoring
# Pertanyaan lapangan sering panjang (tempel daftar PN, keluhan bertele). 500
# char memotong justru bagian yang menjelaskan MAKSUD user — padahal kolomnya
# `text`, jadi menaikkan cap tak menambah biaya skema.
_QUESTION_CAP = 1000


def log_turn(*, username: str | None, role: str | None, question: str,
             tools_used: list[str] | None, rounds: int, latency_ms: int,
             guard_hit: bool, tool_failed: bool, reply_len: int,
             outcome: str, tokens_in: int = 0, tokens_out: int = 0,
             tokens_cache_hit: int = 0, api_calls: int = 0, reply: str = "",
             tools_failed: list[str] | None = None,
             session_id: str = "") -> bool:
    """Simpan satu baris observabilitas. Best-effort: False bila gagal/tabel absen,
    TAK melempar (pemanggil membungkus lagi, tapi tetap aman di sini).

    tokens_* = biaya DeepSeek giliran ini (jumlah seluruh panggilan API-nya),
    dari field `usage` respons. Kolomnya dari migrations/021. `reply` = teks jawaban
    AI (di-cap _REPLY_CAP) dari migrations/022. `tools_failed` = nama tool yang gagal
    (migrations/023). `session_id` = id percakapan (migrations/025) — tanpa ini tiap
    baris berdiri sendiri dan kegagalan FOLLOW-UP (kelas bug terbesar asisten) tak
    bisa direkonstruksi. Bila migrasi belum dijalankan, baris diulang berjenjang
    TANPA kolom yang absen agar log tetap tercatat."""
    tools = tools_used or []
    gagal = tools_failed or []
    base = {
        "username": (username or None),
        "role": (role or None),
        "question": (question or "")[:_QUESTION_CAP] or None,
        "tools": (", ".join(tools) if tools else None),
        "tools_count": len(tools),
        "rounds": int(rounds),
        "latency_ms": int(latency_ms),
        "guard_hit": bool(guard_hit),
        "tool_failed": bool(tool_failed),
        "reply_len": int(reply_len),
        "outcome": outcome or None,
        "created_at": _now(),
    }
    tok = {
        "tokens_in": int(tokens_in or 0),
        "tokens_out": int(tokens_out or 0),
        "tokens_cache_hit": int(tokens_cache_hit or 0),
        "api_calls": int(api_calls or 0),
    }
    with_reply = {**base, **tok, "reply": (reply or "")[:_REPLY_CAP] or None}
    with_failed = {**with_reply, "tools_failed": (", ".join(gagal) if gagal else None)}
    full = {**with_failed, "session_id": (session_id or None)}
    # 5 tingkat berjenjang: +session_id (025) → +tools_failed (023) → reply (022)
    # → token (021) → base. Kolom absen (migrasi belum jalan) bikin PostgREST
    # balas 400 → coba tingkat berikutnya (log tetap tercatat, degradasi bertahap).
    for payload in (full, with_failed, with_reply, {**base, **tok}, base):
        try:
            r = requests.post(
                _rest_url("ai_chat_log"),
                headers=_service_headers("return=minimal"),
                json=payload,
                timeout=_TIMEOUT,
            )
            if r.status_code in (200, 201, 204):
                return True
        except Exception:
            return False
    return False


_SELECT_BASE = ("id,created_at,username,role,question,tools,tools_count,"
                "rounds,latency_ms,guard_hit,tool_failed,reply_len,outcome")
_SELECT_TOKENS = _SELECT_BASE + ",tokens_in,tokens_out,tokens_cache_hit,api_calls"
_SELECT_REPLY = _SELECT_TOKENS + ",reply"
_SELECT_FAILED = _SELECT_REPLY + ",tools_failed"
_SELECT_FULL = _SELECT_FAILED + ",session_id"


def list_logs(limit: int = 200) -> list[dict]:
    """Baris observabilitas terbaru dulu (untuk halaman admin). Kolom terkaya dicoba
    dulu (session_id=025, tools_failed=023, reply=022, token=021); skema lama →
    fallback ke select yang lebih ramping."""
    for sel in (_SELECT_FULL, _SELECT_FAILED, _SELECT_REPLY, _SELECT_TOKENS, _SELECT_BASE):
        try:
            r = requests.get(
                _rest_url("ai_chat_log"),
                headers={**_service_headers(), "Accept": "application/json"},
                params={
                    "select": sel,
                    "order": "created_at.desc",
                    "limit": str(max(1, min(limit, 1000))),
                },
                timeout=_TIMEOUT,
            )
            r.raise_for_status()
            return r.json() or []
        except Exception:
            continue
    return []


def _delete(params: dict) -> tuple[bool, int]:
    """DELETE ke Supabase dgn filter `params`. Return (ok, jumlah_terhapus | -1).
    PostgREST butuh minimal 1 filter (safety); count=exact → header Content-Range."""
    try:
        r = requests.delete(
            _rest_url("ai_chat_log"),
            headers=_service_headers("return=minimal,count=exact"),
            params=params,
            timeout=_TIMEOUT,
        )
        if r.status_code not in (200, 204):
            return False, -1
        n = -1
        cr = r.headers.get("Content-Range", "")
        if "/" in cr:
            tail = cr.rsplit("/", 1)[-1].strip()
            if tail.isdigit():
                n = int(tail)
        return True, n
    except Exception:
        return False, -1


def delete_before(days: int) -> tuple[bool, int]:
    """Hapus baris lebih tua dari `days` hari. days<=0 → hapus SEMUA."""
    if days <= 0:
        return delete_all()
    return _delete({"created_at": f"lt.{_cutoff(days)}"})


def delete_all() -> tuple[bool, int]:
    """Hapus SEMUA baris (filter created_at<=now mencocokkan semua)."""
    return _delete({"created_at": f"lte.{_now()}"})


# ── Retensi otomatis: hapus baris lebih tua dari _RETENTION_DAYS di latar ────
_RETENTION_DAYS = 30
_RETENTION_INTERVAL = 24 * 3600     # cek harian
_retention_lock = threading.Lock()
_retention_started = False


def start_retention() -> bool:
    """Thread daemon: hapus baris >_RETENTION_DAYS hari, sekali di awal lalu harian.
    Idempoten & best-effort (tabel absen/Supabase down → diam)."""
    global _retention_started
    with _retention_lock:
        if _retention_started:
            return False
        _retention_started = True

    def _loop():
        while True:
            try:
                ok, n = delete_before(_RETENTION_DAYS)
                if ok and n > 0:
                    print(f"[chat_log] retensi: {n} baris >{_RETENTION_DAYS} hari dihapus")
            except Exception:  # pragma: no cover
                pass
            time.sleep(_RETENTION_INTERVAL)

    threading.Thread(target=_loop, daemon=True, name="chat-log-retention").start()
    return True


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
    tool_gagal_freq: dict[str, int] = {}
    tool_gagal_nf: dict[str, int] = {}
    tool_gagal_err: dict[str, int] = {}
    gagal_rincian = {"nf": 0, "err": 0, "legacy": 0}
    outcome_freq: dict[str, int] = {}
    for r in rows:
        for t in (r.get("tools") or "").split(", "):
            t = t.strip()
            if t:
                tool_freq[t] = tool_freq.get(t, 0) + 1
        # Entri tools_failed bisa bersuffix jenis: "nama:nf" (lookup jujur nihil)
        # / "nama:err" (error/infra); baris lama tanpa suffix = "legacy".
        for t in (r.get("tools_failed") or "").split(", "):
            t = t.strip()
            if not t:
                continue
            nama, _, kind = t.partition(":")
            tool_gagal_freq[nama] = tool_gagal_freq.get(nama, 0) + 1
            if kind == "nf":
                tool_gagal_nf[nama] = tool_gagal_nf.get(nama, 0) + 1
            elif kind == "err":
                tool_gagal_err[nama] = tool_gagal_err.get(nama, 0) + 1
            gagal_rincian["nf" if kind == "nf" else
                          "err" if kind == "err" else "legacy"] += 1
        o = (r.get("outcome") or "?").strip()
        outcome_freq[o] = outcome_freq.get(o, 0) + 1

    # Tool paling sering GAGAL + rasio gagal/pakai (tool bergantung server eksternal
    # — EPC/SIMS/Accurate — paling rawan). [nama, jml_gagal, rasio_persen, jml_nf,
    # jml_err] — nf ≠ rusak (data memang tak ada); destructuring 3-elemen lama aman.
    tool_gagal_tersering = [
        [t, c, round(100 * c / tool_freq[t], 1) if tool_freq.get(t) else 0.0,
         tool_gagal_nf.get(t, 0), tool_gagal_err.get(t, 0)]
        for t, c in sorted(tool_gagal_freq.items(), key=lambda kv: -kv[1])[:10]
    ]

    def _pct(i: int) -> int:
        return lat[min(len(lat) - 1, int(len(lat) * i / 100))]

    # Token DeepSeek: rata-rata dihitung HANYA dari baris yang punya data token
    # (baris lama sebelum migrasi 021 semua 0 — ikut dirata-rata bikin angka bohong).
    tok_rows = [r for r in rows if int(r.get("tokens_in") or 0) or int(r.get("tokens_out") or 0)]
    tok_in = sum(int(r.get("tokens_in") or 0) for r in tok_rows)
    tok_out = sum(int(r.get("tokens_out") or 0) for r in tok_rows)
    tok_hit = sum(int(r.get("tokens_cache_hit") or 0) for r in tok_rows)
    token = {
        "giliran_terukur": len(tok_rows),
        "total_in": tok_in,
        "total_out": tok_out,
        "rata2_in": round(tok_in / len(tok_rows)) if tok_rows else 0,
        "rata2_out": round(tok_out / len(tok_rows)) if tok_rows else 0,
        "cache_hit_persen": round(100 * tok_hit / tok_in, 1) if tok_in else 0.0,
    }

    return {
        "total": n,
        "latensi_ms": {"p50": _pct(50), "p90": _pct(90), "maks": lat[-1]},
        "guard_menyala": guard,
        "guard_rasio_persen": round(100 * guard / n, 1),
        "tool_gagal": failed,
        "tool_gagal_rasio_persen": round(100 * failed / n, 1),
        "tool_tersering": sorted(tool_freq.items(), key=lambda kv: -kv[1])[:10],
        "tool_gagal_tersering": tool_gagal_tersering,
        "tool_gagal_rincian": gagal_rincian,
        "outcome": outcome_freq,
        "token": token,
    }
