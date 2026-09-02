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

# 10 dtk terlalu longgar: log adalah pekerjaan SAMPINGAN giliran chat — bila
# Supabase lambat, lebih baik cepat menyerah (log_turn best-effort) daripada
# menahan thread. Ditulis lewat log_turn_async pun, thread-nya jangan menganggur
# lama-lama di server 1 vCPU.
_TIMEOUT = 5


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
        # ok | not_found | empty | sanitized | tanya
        # | salvage_llm    → jalur normal gagal, diselamatkan panggilan konteks-bersih
        # | salvage_render → diselamatkan render deterministik dari hasil tool
        # Kolom `text` bebas & agregasi di bawah dinamis → nilai baru TIDAK butuh
        # migrasi maupun perubahan panel admin.
        "  outcome text,\n"
        "  tokens_in int not null default 0,\n"         # migrations/021
        "  tokens_out int not null default 0,\n"
        "  tokens_cache_hit int not null default 0,\n"
        "  api_calls int not null default 0,\n"
        "  reply text,\n"                                # migrations/022 (teks jawaban AI)
        "  tools_failed text,\n"                         # migrations/023 (tool yang gagal)
        "  session_id text,\n"                           # migrations/025 (percakapan)
        "  guard_kinds text,\n"                          # migrations/026 (guard mana yg menyala)
        # migrations/029 — pecahan latensi: waktu menunggu MODEL vs menunggu TOOL,
        # plus waktu sampai potongan jawaban pertama tiba di klien (0 = tak streaming).
        "  model_ms int not null default 0,\n"
        "  tools_ms int not null default 0,\n"
        "  ttft_ms int not null default 0,\n"
        # migrations/030 — SINYAL MUTU IMPLISIT: user mengetik ulang pertanyaan
        # yang sama di sesi yang sama = jawaban pertama tidak memuaskan. Ada
        # karena kanal 👍/👎 menerima NOL baris dalam 30 hari, sementara
        # pengulangan seperti ini terjadi 112 kali di periode yang sama.
        "  diulang boolean not null default false\n"
        ");\n"
        "create index if not exists ai_chat_log_created_idx on ai_chat_log (created_at desc);\n"
        "create index if not exists ai_chat_log_session_idx on ai_chat_log (session_id);\n"
    )


_REPLY_CAP = 4000  # teks jawaban di-cap (bisa beberapa KB) — cukup utk monitoring
# Pertanyaan lapangan sering panjang (tempel daftar PN, keluhan bertele). 500
# char memotong justru bagian yang menjelaskan MAKSUD user — padahal kolomnya
# `text`, jadi menaikkan cap tak menambah biaya skema.
_QUESTION_CAP = 1000

# Indeks tingkat tangga payload yang TERAKHIR diterima Supabase (lihat log_turn).
# 0 = coba dari tingkat terkaya. Sengaja variabel modul: umur proses, bukan
# persisten — restart backend otomatis memprobe skema terbaru sekali lagi.
_tier_memo = 0


def _sid_cadangan(username: str | None) -> str | None:
    """session_id CADANGAN untuk klien yang tak mengirim conversation_id.

    Klien web & APK sekarang sudah mengirimnya (beres sejak 30 Jul 2026), tapi di
    Agustus masih ada 14 giliran (2,4%) tanpa session_id — semuanya dari pemakai
    yang aplikasinya belum diperbarui. Baris tanpa session_id tak bisa
    direkonstruksi jadi percakapan, dan justru kegagalan FOLLOW-UP-lah kelas bug
    terbesar asisten.

    Dikelompokkan per USER + TANGGAL. Kasar, tapi jujur: berprefiks `auto:` agar
    saat menganalisis tak pernah tertukar dengan percakapan sungguhan, dan tak
    pernah menimpa session_id asli (hanya dipakai bila kosong).
    """
    u = (username or "").strip().lower()
    if not u:
        return None
    return f"auto:{u}:{time.strftime('%Y-%m-%d', time.gmtime())}"


def log_turn(*, username: str | None, role: str | None, question: str,
             tools_used: list[str] | None, rounds: int, latency_ms: int,
             guard_hit: bool, tool_failed: bool, reply_len: int,
             outcome: str, tokens_in: int = 0, tokens_out: int = 0,
             tokens_cache_hit: int = 0, api_calls: int = 0, reply: str = "",
             tools_failed: list[str] | None = None,
             session_id: str = "",
             guard_kinds: list[str] | None = None,
             model_ms: int = 0, tools_ms: int = 0, ttft_ms: int = 0,
             diulang: bool = False,
             pikir_chars: int = 0, calls_detail: str = "",
             tools_args: list[str] | None = None) -> bool:
    """Simpan satu baris observabilitas. Best-effort: False bila gagal/tabel absen,
    TAK melempar (pemanggil membungkus lagi, tapi tetap aman di sini).

    tokens_* = biaya DeepSeek giliran ini (jumlah seluruh panggilan API-nya),
    dari field `usage` respons. Kolomnya dari migrations/021. `reply` = teks jawaban
    AI (di-cap _REPLY_CAP) dari migrations/022. `tools_failed` = nama tool yang gagal
    (migrations/023). `session_id` = id percakapan (migrations/025) — tanpa ini tiap
    baris berdiri sendiri dan kegagalan FOLLOW-UP (kelas bug terbesar asisten) tak
    bisa direkonstruksi. Bila migrasi belum dijalankan, baris diulang berjenjang
    TANPA kolom yang absen agar log tetap tercatat.

    `guard_kinds` = guard MANA yang menyala (migrations/026). Tanpa ini `guard_hit`
    hanya boolean dan — lebih buruk — dulu hanya dinaikkan guard anti-karangan,
    sehingga guard yang justru paling berharga (DTC-FIRST: terbukti menghentikan
    14 jawaban kode kesalahan SALAH pada 2026-07-16) sama sekali tak terlihat.

    `model_ms`/`tools_ms`/`ttft_ms` = PECAHAN latensi (migrations/029): berapa ms
    giliran ini menunggu model vs menunggu tool, dan kapan potongan jawaban
    pertama sampai ke klien. Tanpa pecahan itu `latency_ms` cuma memberi tahu
    bahwa giliran lambat, bukan lambat DI MANA."""
    tools = tools_used or []
    gagal = tools_failed or []
    guards = guard_kinds or []
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
    with_session = {**with_failed, "session_id": (session_id or _sid_cadangan(username))}
    with_guard = {**with_session, "guard_kinds": (", ".join(guards) if guards else None)}
    with_fase = {**with_guard, "model_ms": int(model_ms or 0),
                 "tools_ms": int(tools_ms or 0), "ttft_ms": int(ttft_ms or 0)}
    full = {**with_fase, "diulang": bool(diulang)}
    # Telemetri PER PANGGILAN (migrations/031): `pikir_chars` = total char blok
    # nalar [PIKIR] seluruh panggilan giliran ini; `calls_detail` = 'in/hit' tiap
    # panggilan API (urut) — hit/miss per giliran menyembunyikan panggilan mana
    # yang miss; `tools_args` = 'nama#digest' sejajar `tools` — tanpa sidik jari
    # argumen, "follow-up memanggil ulang tool yang sama" tak bisa dibedakan dari
    # panggilan dengan argumen berbeda.
    args_ = tools_args or []
    audit = {**full, "pikir_chars": int(pikir_chars or 0),
             "calls_detail": (str(calls_detail)[:_CALLS_DETAIL_CAP] or None),
             "tools_args": (", ".join(args_)[:_TOOLS_ARGS_CAP] if args_ else None)}
    # 9 tingkat berjenjang: +telemetri per panggilan (031) → +diulang (030) →
    # +fase ms (029) → +guard_kinds (026) → +session_id (025) → +tools_failed
    # (023) → reply (022) → token (021) → base. Kolom absen (migrasi belum
    # jalan) bikin PostgREST balas 400 → coba tingkat berikutnya (log tetap
    # tercatat, degradasi bertahap).
    tangga = (audit, full, with_fase, with_guard, with_session, with_failed,
              with_reply, {**base, **tok}, base)
    # MEMO tingkat: bila migrasi tertinggal, tingkat teratas ditolak SETIAP
    # giliran — itu 1-2 POST sia-sia per giliran chat (masing-masing sampai
    # _TIMEOUT detik). Karena itu tingkat yang TERAKHIR DITERIMA diingat dan
    # dipakai sebagai titik MULAI. Ingatan bukan kunci: begitu tingkat itu pun
    # ditolak, ia dibuang dan tangga lanjut turun sampai baris tercatat.
    # (Setelah migrasi baru dijalankan, proses ini tetap memakai tingkat lamanya
    # sampai backend restart — lihat catatan di _tier_memo.)
    global _tier_memo
    mulai = _tier_memo if 0 <= _tier_memo < len(tangga) else 0
    for i in range(mulai, len(tangga)):
        try:
            r = requests.post(
                _rest_url("ai_chat_log"),
                headers=_service_headers("return=minimal"),
                json=tangga[i],
                timeout=_TIMEOUT,
            )
            if r.status_code in (200, 201, 204):
                _tier_memo = i
                return True
        except Exception:
            _tier_memo = 0
            return False
        _tier_memo = 0          # tingkat ini ditolak → ingatan tak lagi sahih
    return False


def log_turn_async(**kw) -> None:
    """log_turn di thread daemon — API-nya sama, tapi TIDAK menunggu.

    Jalur return giliran chat tak boleh terblokir POST ke Supabase: log adalah
    pekerjaan sampingan, sedangkan user sudah menunggu jawabannya belasan detik.
    Sebelum ini, Supabase lambat (atau tangga fallback yang menabrak timeout
    berkali-kali) langsung menambah detik ke latensi yang DIRASAKAN user.
    Semua exception ditelan — termasuk kegagalan membuat thread."""
    def _kerja():
        try:
            log_turn(**kw)
        except Exception:       # pragma: no cover - best-effort
            pass

    try:
        threading.Thread(target=_kerja, daemon=True, name="chat-log-turn").start()
    except Exception:           # pragma: no cover - server kehabisan thread
        pass


_SELECT_BASE = ("id,created_at,username,role,question,tools,tools_count,"
                "rounds,latency_ms,guard_hit,tool_failed,reply_len,outcome")
_SELECT_TOKENS = _SELECT_BASE + ",tokens_in,tokens_out,tokens_cache_hit,api_calls"
_SELECT_REPLY = _SELECT_TOKENS + ",reply"
_SELECT_FAILED = _SELECT_REPLY + ",tools_failed"
_SELECT_SESSION = _SELECT_FAILED + ",session_id"
_SELECT_GUARD = _SELECT_SESSION + ",guard_kinds"
_SELECT_FULL = _SELECT_GUARD + ",model_ms,tools_ms,ttft_ms"
# Audit 2026-08-28: tangga TULIS sudah mengirim `diulang` (030) tapi tangga BACA
# berhenti di 029 → PostgREST tak pernah mengembalikan kolomnya → summary()
# selalu 0 walau migrasi sudah jalan. Tier baca wajib ikut naik tiap migrasi.
_SELECT_DIULANG = _SELECT_FULL + ",diulang"
# migrations/031 — telemetri per panggilan (nalar, in/hit per panggilan, digest argumen).
_SELECT_AUDIT = _SELECT_DIULANG + ",pikir_chars,calls_detail,tools_args"
_CALLS_DETAIL_CAP = 400    # ±30 panggilan 'in/hit' — plafon _MAX_ITERS jauh di bawah itu
_TOOLS_ARGS_CAP = 2000     # ±80 entri 'nama#digest' — plafon eksekusi/giliran = 30


def list_logs(limit: int = 200) -> list[dict]:
    """Baris observabilitas terbaru dulu (untuk halaman admin). Kolom terkaya dicoba
    dulu (diulang=030, fase ms=029, guard_kinds=026, session_id=025, tools_failed=023,
    reply=022, token=021); skema lama → fallback ke select yang lebih ramping."""
    for sel in (_SELECT_AUDIT, _SELECT_DIULANG, _SELECT_FULL, _SELECT_GUARD,
                _SELECT_SESSION, _SELECT_FAILED, _SELECT_REPLY, _SELECT_TOKENS,
                _SELECT_BASE):
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
    tool_gagal_brake: dict[str, int] = {}
    gagal_rincian = {"nf": 0, "err": 0, "brake": 0, "legacy": 0}
    outcome_freq: dict[str, int] = {}
    # Sebab guard (migrations/026). Baris SEBELUM rilis itu kosong — dan `guard`
    # di atas pun undercount karena dulu hanya guard anti-karangan yang terhitung.
    guard_freq: dict[str, int] = {}
    for r in rows:
        for t in (r.get("tools") or "").split(", "):
            t = t.strip()
            if t:
                tool_freq[t] = tool_freq.get(t, 0) + 1
        # Entri tools_failed bisa bersuffix jenis: "nama:nf" (lookup jujur nihil)
        # / "nama:err" (error/infra) / "nama:brake" (DITOLAK rem anti-loop —
        # belum sempat dicek sama sekali); baris lama tanpa suffix = "legacy".
        # ⚠️ `brake` dulu jatuh ke "legacy" karena rincian ini hanya mengenal
        # nf/err — panel admin lalu melaporkannya sebagai "baris lama pra-migrasi".
        # Akibatnya kelas kegagalan yang paling bisa DIPERBAIKI (plafon panggilan
        # kita sendiri, bukan data/infra) justru yang paling tak terlihat: 33 entri
        # brake dalam 30 hari terhitung sebagai sisa data lama.
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
            elif kind == "brake":
                tool_gagal_brake[nama] = tool_gagal_brake.get(nama, 0) + 1
            gagal_rincian[kind if kind in gagal_rincian else "legacy"] += 1
        for g in (r.get("guard_kinds") or "").split(", "):
            g = g.strip()
            if g:
                guard_freq[g] = guard_freq.get(g, 0) + 1
        o = (r.get("outcome") or "?").strip()
        outcome_freq[o] = outcome_freq.get(o, 0) + 1

    # Tool paling sering GAGAL + rasio gagal/pakai (tool bergantung server eksternal
    # — EPC/SIMS/Accurate — paling rawan). [nama, jml_gagal, rasio_persen, jml_nf,
    # jml_err, jml_brake] — nf ≠ rusak (data memang tak ada), brake ≠ gagal sama
    # sekali (kita sendiri yang menolak menjalankannya). Elemen ditambah di UJUNG:
    # destructuring 3/5-elemen lama tetap aman.
    tool_gagal_tersering = [
        [t, c, round(100 * c / tool_freq[t], 1) if tool_freq.get(t) else 0.0,
         tool_gagal_nf.get(t, 0), tool_gagal_err.get(t, 0),
         tool_gagal_brake.get(t, 0)]
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

    # Pecahan latensi (migrations/029) — sama seperti token: baris pra-migrasi
    # semua 0, dan ikut dirata-rata akan membuat "menunggu model" terlihat jauh
    # lebih ringan daripada kenyataannya. Rata-rata HANYA atas baris terukur.
    fase_rows = [r for r in rows
                 if int(r.get("model_ms") or 0) > 0 or int(r.get("tools_ms") or 0) > 0]
    f_model = sum(int(r.get("model_ms") or 0) for r in fase_rows)
    f_tools = sum(int(r.get("tools_ms") or 0) for r in fase_rows)
    fase = {
        "giliran_terukur": len(fase_rows),
        "rata2_model_ms": round(f_model / len(fase_rows)) if fase_rows else 0,
        "rata2_tools_ms": round(f_tools / len(fase_rows)) if fase_rows else 0,
    }

    # SINYAL MUTU IMPLISIT (migrations/030). Diletakkan sejajar guard/tool_gagal
    # karena inilah satu-satunya angka mutu yang datang dari PERILAKU user, bukan
    # dari penilaian sistem atas dirinya sendiri — dan sampai 👍/👎 benar-benar
    # dipakai, ini satu-satunya yang ada.
    diulang = sum(1 for r in rows if r.get("diulang"))

    return {
        "total": n,
        "latensi_ms": {"p50": _pct(50), "p90": _pct(90), "maks": lat[-1]},
        "pertanyaan_diulang": diulang,
        "pertanyaan_diulang_persen": round(100 * diulang / n, 1),
        "guard_menyala": guard,
        "guard_rasio_persen": round(100 * guard / n, 1),
        "guard_sebab": guard_freq,
        "tool_gagal": failed,
        "tool_gagal_rasio_persen": round(100 * failed / n, 1),
        "tool_tersering": sorted(tool_freq.items(), key=lambda kv: -kv[1])[:10],
        "tool_gagal_tersering": tool_gagal_tersering,
        "tool_gagal_rincian": gagal_rincian,
        "outcome": outcome_freq,
        "token": token,
        "fase": fase,
    }
