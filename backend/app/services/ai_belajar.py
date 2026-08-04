"""Miner BELAJAR-SENDIRI harian asisten (Fase B program "nyambung" 2026-07-23).

Tiga pekerjaan, SEMUANYA bebas-LLM & bebas-jaringan-eksternal (fakta turunan
data lokal — hasil LLM apa pun tetap lewat pipeline usulan+approve yang ada):

1. mine_chat_logs — ai_chat_log (permukaan yang belum pernah dibaca utk belajar):
   (a) giliran ber-tool istilah yang gagal `nf` → `search_log.record_miss(...)`
       → pipeline belajar sinonim LAMA (usulan LLM tervalidasi + approve admin)
       mengurus sisanya;
   (b) pertanyaan ber-outcome != ok dikelompokkan (token-set, tanpa PN/rangka)
       → kelompok ≥3 = "GAP PENGETAHUAN" utk admin (baca-saja, /admin/ai-belajar).
2. mine_epc_edges — baca data/epc_unit_items/*.json LANGSUNG dari disk (⛔ bukan
   epc_bom._all_items yang bisa fetch jaringan saat TTL basi; file basi tetap
   sah utk mining) + peta rangka→model dari populasi (best-effort) → edges
   part↔unit `part_unit_edges.json.gz` — dibaca builder knowledge_links
   (posting "Dipakai di: <model>"). Cache EPC tumbuh sendiri saat user menyebut
   unit (prefetch) → tautan ikut tumbuh tiap malam = "belajar antar pengetahuan".
3. _tick — bila ada perubahan → rebuild knowledge_links IN-PROCESS (tulis hanya
   bila byte berubah → no-change benar-benar no-op); ai_knowledge menyusul
   otomatis via _maybe_rebuild_async (links ada di _input_mtime-nya).

Scheduler: pola ai_sinonim_learn (24 jam, delay startup, double-lock idempoten).
"""
from __future__ import annotations

import gzip
import json
import logging
import os
import re
import threading
import time
from pathlib import Path

from ..core.config import get_settings
from . import ai_chat_log, knowledge_links, search_log
from .knowledge_util import load_json, norm_pn

logger = logging.getLogger("maspart.ai_belajar")

_SCHED_INTERVAL = 24 * 3600
_SCHED_STARTUP_DELAY = 900          # tunggu part_index & warmer lain siap
_MINE_LOG_LIMIT = 500
_GAP_MIN_JUMLAH = 3                 # kelompok pertanyaan gagal ≥3 = gap
_GAP_MAX = 100
# Outcome yang BUKAN kegagalan utk gap topik: "tanya" = giliran berakhir kartu
# interaktif (tanya_user / kartu konfirmasi ajarkan) — interaksi normal, user
# belum selesai. Tanpa pengecualian ini alur ajar memakan dirinya sendiri:
# "tampilkan topik gagal, bantu ajarkan…" selalu berakhir "tanya" → terhitung
# gagal berulang → jadi topik gap → ditawarkan balik utk diajarkan (meta).
_GAP_OUTCOME_BUKAN_GAGAL = ("ok", "tanya")

# Tool ISTILAH: kegagalan nf-nya = kandidat celah kamus sinonim.
_TOOL_ISTILAH_NF = ("cari_part", "cari_part_di_unit", "stok_gudang",
                    "cari_filter_shantui", "cari_manual", "cari_pengetahuan",
                    "jadwal_perawatan")

_PN_LIKE = re.compile(r"^[A-Z0-9\-./]{6,}$", re.IGNORECASE)
_FRAME_RE = re.compile(r"\b(?:L[A-HJ-NPR-Z0-9]{16}|[A-Z]{2}\d{6})\b")
_ANGKA_RE = re.compile(r"\b\d{4,}\b")
# Stopword ringan utk kunci kelompok gap (token generik pertanyaan).
_GAP_STOP = frozenset("yang untuk dari pada apa bagaimana kenapa berapa tolong "
                      "cek mohon minta bisa ada di ke itu ini dan atau dong ya "
                      "nya unit part harga stok".split())


def _dir() -> Path:
    return get_settings().data_path / "ai_belajar"


def _state_path() -> Path:
    return _dir() / "state.json"


def _edges_path() -> Path:
    return _dir() / "part_unit_edges.json.gz"


def _gap_path() -> Path:
    return _dir() / "gap_topik.json"


def _load_state() -> dict:
    d = load_json(_state_path(), default={})
    return dict(d) if isinstance(d, dict) else {}


def _save_json_atomic(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    os.replace(tmp, path)


def _gz_bytes(data) -> bytes:
    """Serialisasi identik write_json_gz (sort_keys+kompak, gzip mtime=0) —
    untuk BYTE-COMPARE sebelum menulis (no-change = benar-benar no-op)."""
    payload = json.dumps(data, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":")).encode("utf-8")
    import io
    buf = io.BytesIO()
    with gzip.GzipFile(filename="", fileobj=buf, mode="wb", mtime=0) as gz:
        gz.write(payload)
    return buf.getvalue()


def _write_gz_if_changed(path: Path, data) -> bool:
    baru = _gz_bytes(data)
    try:
        if path.exists() and path.read_bytes() == baru:
            return False
    except OSError:
        pass
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(baru)
    return True


# ═══════════════════════════════════════════════════════════════════════
#  1) MINER CHAT-LOG
# ═══════════════════════════════════════════════════════════════════════
def _q_layak_kamus(q: str) -> str:
    """Pertanyaan layak jadi kandidat kamus: frasa istilah pendek 1-6 kata,
    bukan PN/rangka. Return teks ternormalisasi ('' = tidak layak)."""
    q = " ".join(str(q or "").split()).strip(" ?!.")
    words = q.split()
    if not q or not (1 <= len(words) <= 6) or len(q) < 4:
        return ""
    if _PN_LIKE.match(q.replace(" ", "")) and any(c.isdigit() for c in q):
        return ""
    if _FRAME_RE.search(q.upper()):
        return ""
    return q


def _gap_key(q: str) -> str:
    """Kunci kelompok gap: token-set ternormalisasi (tanpa angka/PN/stopword)."""
    t = _FRAME_RE.sub(" ", str(q or "").lower())
    t = _ANGKA_RE.sub(" ", t)
    toks = sorted({w for w in re.findall(r"[a-z]{3,}", t) if w not in _GAP_STOP})
    return " ".join(toks[:8])


_BOBOT_DISLIKE = 3      # satu 👎 setara 3 giliran ber-outcome jelek


def _serap_feedback_down(acc: dict, state: dict) -> dict:
    """Masukkan pertanyaan ber-👎 ke akumulator gap topik yang sama.

    Watermark-nya SENDIRI (`watermark_feedback`), bukan watermark chat-log:
    keduanya tabel berbeda dengan laju berbeda, dan memakai watermark chat-log
    akan diam-diam melewatkan 👎 setiap kali user lain sempat mengobrol setelah
    umpan balik itu diberikan — persis sinyal yang paling ingin kita tangkap.

    Dipisah jadi fungsi sendiri supaya kegagalan tabel ai_feedback (mis. migrasi
    belum jalan) tak pernah menjatuhkan penambangan chat-log yang utama."""
    try:
        from . import ai_feedback
        rows = ai_feedback.list_feedback(rating="down", limit=200) or []
    except Exception:
        return acc
    wm = str(state.get("watermark_feedback") or "")
    terbaru = wm
    for r in rows:
        dibuat = str(r.get("created_at") or "")
        if dibuat <= wm:
            continue            # sudah pernah diserap di putaran sebelumnya
        terbaru = max(terbaru, dibuat)
        q = " ".join(str(r.get("question") or "").split())
        key = _gap_key(q)
        if not key:
            continue
        g = acc.get(key) or {"topik": key, "contoh": q, "jumlah": 0, "terakhir": ""}
        g["jumlah"] = int(g.get("jumlah") or 0) + _BOBOT_DISLIKE
        g["dislike"] = int(g.get("dislike") or 0) + 1
        g["terakhir"] = max(str(g.get("terakhir") or ""),
                            str(r.get("created_at") or "")[:16])
        if not g.get("contoh") or len(q) < len(g["contoh"]):
            g["contoh"] = q
        acc[key] = g
    if terbaru:
        state["watermark_feedback"] = terbaru
    return acc


def mine_chat_logs(limit: int = _MINE_LOG_LIMIT) -> dict:
    """Baca giliran chat BARU (di atas watermark) → misses istilah + gap topik."""
    try:
        rows = ai_chat_log.list_logs(limit) or []
    except Exception:
        rows = []
    state = _load_state()
    watermark = str(state.get("watermark_created_at") or "")
    baru = [r for r in rows if str(r.get("created_at") or "") > watermark]
    n_miss = 0
    if baru:
        # (a) nf tool istilah → record_miss (pipeline sinonim lama)
        for r in baru:
            failed = str(r.get("tools_failed") or "")
            if not any(f"{t}:nf" in failed for t in _TOOL_ISTILAH_NF):
                continue
            q = _q_layak_kamus(r.get("question") or "")
            if not q:
                continue
            try:
                search_log.record_miss(q, "ai", "chat_log")
                n_miss += 1
            except Exception:
                pass
        # (b) gap topik: outcome != ok, dikelompokkan token-set (akumulasi
        #     lintas-hari di state; publik hanya kelompok ≥ ambang)
        acc: dict = dict(state.get("gap_acc") or {})
        for r in baru:
            if (r.get("outcome") or "ok") in _GAP_OUTCOME_BUKAN_GAGAL:
                continue
            q = " ".join(str(r.get("question") or "").split())
            key = _gap_key(q)
            if not key:
                continue
            g = acc.get(key) or {"topik": key, "contoh": q, "jumlah": 0,
                                 "terakhir": ""}
            g["jumlah"] = int(g.get("jumlah") or 0) + 1
            g["terakhir"] = str(r.get("created_at") or "")[:16]
            if len(q) < len(g.get("contoh") or "") or not g.get("contoh"):
                g["contoh"] = q
            acc[key] = g
        # (c) 👎 EKSPLISIT dari user — sinyal terkuat yang kita punya, tapi
        #     sebelumnya hanya duduk di panel admin dan tak pernah dipakai apa
        #     pun secara otomatis. Diberi bobot _BOBOT_DISLIKE karena "user
        #     menekan jempol ke bawah" jauh lebih meyakinkan daripada heuristik
        #     `outcome != ok` (yang juga menyala saat asisten JUJUR bilang data
        #     tak ada). Read-only terhadap tabel ai_feedback.
        acc = _serap_feedback_down(acc, state)
        # rapikan akumulator (cap — kelompok terbanyak menang)
        if len(acc) > 400:
            acc = dict(sorted(acc.items(), key=lambda kv: -kv[1]["jumlah"])[:400])
        state["gap_acc"] = acc
        state["watermark_created_at"] = max(
            str(r.get("created_at") or "") for r in baru)
        _save_json_atomic(_state_path(), state)
        publik = sorted((g for g in acc.values()
                         if g["jumlah"] >= _GAP_MIN_JUMLAH),
                        key=lambda g: -g["jumlah"])[:_GAP_MAX]
        _save_json_atomic(_gap_path(), publik)
        n_gap = len(publik)
    else:
        n_gap = len(gaps())
    return {"baris_baru": len(baru), "miss_terekam": n_miss, "gap_publik": n_gap}


def gaps() -> list[dict]:
    d = load_json(_gap_path(), default=[])
    return list(d) if isinstance(d, list) else []


def resolve_gap(topik: str) -> bool:
    """Hapus satu topik gap (admin menandai sudah ditangani)."""
    rows = [g for g in gaps() if g.get("topik") != topik]
    if len(rows) == len(gaps()):
        return False
    _save_json_atomic(_gap_path(), rows)
    state = _load_state()
    acc = dict(state.get("gap_acc") or {})
    acc.pop(topik, None)
    state["gap_acc"] = acc
    _save_json_atomic(_state_path(), state)
    return True


# ═══════════════════════════════════════════════════════════════════════
#  2) MINER EDGES PART↔UNIT (cache EPC + populasi — disk saja)
# ═══════════════════════════════════════════════════════════════════════
def _peta_rangka_model() -> dict[str, str]:
    """NOMOR RANGKA populasi → MODEL (kunci = 8 char terakhir = frame). Best-
    effort: populasi gagal/absen → {} (edges tetap terbentuk tanpa model)."""
    try:
        from . import populasi
        df = populasi._ensure()
        if df is None or getattr(df, "empty", True):
            return {}
        cols = {str(c).strip().upper(): c for c in df.columns}
        c_rangka = cols.get("NOMOR RANGKA")
        c_model = cols.get("MODEL") or cols.get("TIPE UNIT")
        if not c_rangka or not c_model:
            return {}
        out: dict[str, str] = {}
        for rangka, model in zip(df[c_rangka], df[c_model]):
            r = re.sub(r"[^A-Z0-9]", "", str(rangka or "").upper())
            m = " ".join(str(model or "").upper().split())
            if len(r) >= 8 and m:
                out[r[-8:]] = m
        return out
    except Exception:
        logger.info("ai_belajar: populasi tak tersedia (edges tanpa model)")
        return {}


def mine_epc_edges() -> bool:
    """Turunkan edges {norm_pn: {models, frames_n, nama}} dari cache EPC disk.
    Return True bila file edges BERUBAH."""
    d = get_settings().data_path / "epc_unit_items"
    if not d.exists():
        return False
    peta = _peta_rangka_model()
    edges: dict[str, dict] = {}
    for f in sorted(d.glob("*.json")):
        frame = f.stem.upper()
        try:
            payload = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        model = peta.get(frame) or ""
        for row in (payload.get("rows") or []):
            npn = norm_pn((row or {}).get("pn"))
            if len(npn) < 6:
                continue
            e = edges.get(npn) or {"models": [], "frames": [], "nama": ""}
            if model and model not in e["models"]:
                e["models"].append(model)
            if frame not in e["frames"]:
                e["frames"].append(frame)
            if not e["nama"] and (row.get("nama") or "").strip():
                e["nama"] = " ".join(str(row["nama"]).split())[:60]
            edges[npn] = e
    # bentuk final ringkas (frames → hitungan; models cap 5)
    final = {npn: {"models": sorted(e["models"])[:5],
                   "frames_n": len(e["frames"]),
                   "nama": e["nama"]}
             for npn, e in edges.items()}
    return _write_gz_if_changed(_edges_path(), final)


# ═══════════════════════════════════════════════════════════════════════
#  3) TICK + SCHEDULER
# ═══════════════════════════════════════════════════════════════════════
def _tick() -> dict:
    hasil = {"log": {}, "edges_berubah": False, "links_ditulis": False}
    try:
        hasil["log"] = mine_chat_logs()
    except Exception:
        logger.exception("ai_belajar: mine_chat_logs gagal")
    try:
        hasil["edges_berubah"] = mine_epc_edges()
    except Exception:
        logger.exception("ai_belajar: mine_epc_edges gagal")
    # Dataset fast-moving ikut segar harian: cache EPC tumbuh sendiri saat user
    # menyebut unit (prefetch/stale-refresh) → porsi varian makin akurat.
    # Penulisan byte-stable (write_json_gz) → tanpa perubahan = no-op.
    try:
        from . import fast_moving
        fast_moving.build()
    except Exception:
        logger.exception("ai_belajar: fast_moving.build gagal")
    if hasil["edges_berubah"]:
        try:
            data = knowledge_links.build()
            hasil["links_ditulis"] = _write_gz_if_changed(
                knowledge_links._path(), data)
            # ai_knowledge menyusul otomatis (_maybe_rebuild_async via mtime).
        except Exception:
            logger.exception("ai_belajar: rebuild knowledge_links gagal")
    logger.info("ai_belajar tick: %s", hasil)
    return hasil


_sched_lock = threading.Lock()
_sched_started = False


def _sched_loop() -> None:
    time.sleep(_SCHED_STARTUP_DELAY)
    while True:
        try:
            _tick()
        except Exception:
            logger.exception("ai_belajar: tick gagal (lanjut siklus berikut)")
        time.sleep(_SCHED_INTERVAL)


def start_scheduled() -> bool:
    """Daemon harian (idempoten). Return False bila sudah jalan."""
    global _sched_started
    with _sched_lock:
        if _sched_started:
            return False
        _sched_started = True
    threading.Thread(target=_sched_loop, name="ai-belajar", daemon=True).start()
    return True
