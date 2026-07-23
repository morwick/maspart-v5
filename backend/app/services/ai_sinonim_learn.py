"""
Belajar sinonim OTOMATIS dari pencarian nihil (search_misses) — loop belajar asisten.

Alur: search_misses.json (istilah lapangan yang gagal dicari) → LLM (DeepSeek)
mengusulkan mapping istilah Indonesia → kata kunci nama part Inggris → tiap
keyword DIVALIDASI ke katalog lokal (part_index.search_part_name harus kena ≥1
part; keyword yang tak terbukti ada di katalog dibuang) → usulan tersimpan di
data/sinonim/usulan.json menunggu keputusan admin.

Approve = sinonim.add() (langsung aktif di asisten, reload per-mtime) +
search_log.resolve_miss(). Reject = ditandai supaya tidak diusulkan ulang.
Mode 'auto' (opsional, dipicu eksplisit dari admin): usulan yang SEMUA
keyword-nya tervalidasi & confidence tinggi langsung disetujui.

Resilient: LLM gagal → kembalikan error ringkas tanpa mengubah state.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time

import requests

from ..core.config import get_settings
from . import ai_feedback, part_index, search_log, sinonim

logger = logging.getLogger("maspart.sinonim")

_lock = threading.Lock()
_TIMEOUT = 90
_AUTO_MIN_CONF = 0.8

# Status usulan: pending → approved | rejected (rejected disimpan agar
# generate() berikutnya tidak mengusulkan istilah yang sama lagi).
_STATUSES = ("pending", "approved", "rejected")


def _file():
    return get_settings().data_path / "sinonim" / "usulan.json"


def _load() -> dict:
    try:
        return json.loads(_file().read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def _save(d: dict) -> None:
    p = _file()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, p)  # atomik (pola sama dgn sinonim._save)


def list_usulan(status: str | None = "pending") -> list[dict]:
    """Daftar usulan (default hanya pending), terbaru dulu."""
    with _lock:
        d = _load()
    rows = [v for v in d.values()
            if not status or v.get("status") == status]
    rows.sort(key=lambda v: -int(v.get("created", 0)))
    return rows


# ── Seleksi kandidat dari search_misses ───────────────────────────────────────

_PN_LIKE = re.compile(r"^[A-Z0-9\-./]{6,}$", re.IGNORECASE)


def _existing_triggers() -> set[str]:
    out: set[str] = set()
    for e in sinonim.load():
        for t in e.get("triggers") or []:
            out.add(str(t).strip().lower())
    return out


def _fb_kandidat(have: set, seen: dict, taken: set, limit: int) -> list[dict]:
    """Kandidat dari pertanyaan 👎 (ai_feedback rating='down' belum resolved) —
    loop feedback 2026-07-23: down-vote yang berbentuk FRASA ISTILAH pendek
    (1-6 kata) sering = istilah lapangan yang gagal dipahami; kalimat panjang
    bukan bahan kamus. Feedback TIDAK di-auto-resolve saat usulan disetujui
    (sinonim baru ≠ keluhan pasti beres — antrean admin tetap loop manusia)."""
    out: list[dict] = []
    try:
        rows = ai_feedback.list_feedback(rating="down", only_open=True, limit=100)
    except Exception:
        rows = []
    for r in rows or []:
        q = " ".join(str(r.get("question") or "").split()).strip(" ?!.")
        key = q.lower()
        words = q.split()
        if not q or key in have or key in seen or key in taken:
            continue
        if not (1 <= len(words) <= 6) or len(q) < 4:
            continue
        if _PN_LIKE.match(q.replace(" ", "")) and any(c.isdigit() for c in q):
            continue
        try:   # kamus sudah meng-cover istilahnya → gap-nya bukan di kamus
            if any(sinonim.hit(t, q) for t in have):
                continue
        except Exception:
            pass
        out.append({"query": q, "count": 1, "sumber": "feedback"})
        if len(out) >= limit:
            break
    return out


def _kandidat(limit: int) -> list[dict]:
    """Miss tersering yang BELUM ada di kamus & belum pernah diusulkan/ditolak.
    Query mirip PN (huruf+angka panjang tanpa spasi) dilewati — itu urusan
    suggest_pns, bukan kamus sinonim. Sisa slot diisi kandidat dari umpan
    balik 👎 (_fb_kandidat)."""
    have = _existing_triggers()
    seen = _load()  # dipanggil dalam _lock oleh generate()
    out: list[dict] = []
    for m in search_log.top_misses(300):
        q = (m.get("query") or "").strip()
        key = q.lower()
        if not q or key in have or key in seen:
            continue
        if _PN_LIKE.match(q.replace(" ", "")) and any(c.isdigit() for c in q):
            continue
        out.append(m)
        if len(out) >= limit:
            break
    if len(out) < limit:
        out += _fb_kandidat(have, seen,
                            {(m.get("query") or "").strip().lower() for m in out},
                            limit - len(out))
    return out


# ── Panggilan LLM (DeepSeek, JSON mode) ───────────────────────────────────────

_PROMPT_SISTEM = (
    "Kamu ahli suku cadang truk (Sinotruk/HOWO/Shacman/Weichai) di Indonesia. "
    "Diberi daftar ISTILAH LAPANGAN (Indonesia/campuran) yang GAGAL ditemukan di "
    "katalog part berbahasa Inggris, usulkan untuk tiap istilah: kata kunci nama "
    "part INGGRIS yang lazim dipakai katalog OEM (mis. 'spion' → 'mirror', "
    "'rear view mirror'). Jawab HANYA JSON dengan bentuk: "
    '{"usulan": [{"query": "<istilah asli>", "triggers": ["<istilah asli>", '
    '"<variasi umum lain>"], "keywords": ["<kata kunci EN>", ...], '
    '"grup": "<satu kata: body/mesin/rem/kelistrikan/transmisi/poros/kabin/umum>", '
    '"confidence": <0..1>, "alasan": "<1 kalimat>"}]}. '
    "Aturan: keywords harus istilah katalog GENERIK (bukan nomor part, bukan "
    "merek), maksimal 4 keyword per istilah. Bila istilah tidak bisa dipetakan "
    "ke part (typo tak jelas, bukan part), JANGAN sertakan dalam usulan."
)


def _llm_usulan(misses: list[dict]) -> list[dict]:
    s = get_settings()
    if not s.ai_configured:
        raise RuntimeError("DEEPSEEK_API_KEY belum diset — fitur usulan butuh LLM.")
    daftar = "\n".join(f"- {m['query']} (gagal {m.get('count', 1)}x)" for m in misses)
    r = requests.post(
        f"{s.deepseek_base_url.rstrip('/')}/chat/completions",
        headers={"Authorization": f"Bearer {s.deepseek_api_key}",
                 "Content-Type": "application/json"},
        json={
            "model": s.deepseek_model,
            "messages": [
                {"role": "system", "content": _PROMPT_SISTEM},
                {"role": "user", "content": f"Istilah yang gagal dicari:\n{daftar}"},
            ],
            "temperature": 0.0,
            "max_tokens": 2500,
            "response_format": {"type": "json_object"},
        },
        timeout=_TIMEOUT,
    )
    if r.status_code >= 400:
        raise RuntimeError(f"DeepSeek error {r.status_code}")
    content = ((r.json().get("choices") or [{}])[0].get("message") or {}).get("content") or "{}"
    data = json.loads(content)
    items = data.get("usulan") if isinstance(data, dict) else None
    return [i for i in (items or []) if isinstance(i, dict)]


# ── Validasi keyword terhadap katalog nyata ───────────────────────────────────

def _validate_keywords(keywords: list[str]) -> tuple[list[str], list[str]]:
    """Pisahkan keyword yang TERBUKTI kena ≥1 part di katalog lokal vs tidak.
    Ini pagar utama anti-halusinasi: LLM boleh mengusulkan apa pun, tapi hanya
    keyword yang nyata ada di nama part kita yang dipakai."""
    valid: list[str] = []
    invalid: list[str] = []
    for kw in keywords:
        k = str(kw).strip()
        if not k or len(k) < 3:
            continue
        try:
            hit = bool(part_index.search_part_name(k))
        except Exception:
            hit = False
        (valid if hit else invalid).append(k)
    return valid, invalid


# ── API utama ─────────────────────────────────────────────────────────────────

def generate(limit: int = 10, auto_approve: bool = False) -> dict:
    """Buat usulan sinonim baru dari miss tersering. Satu panggilan LLM untuk
    seluruh batch. auto_approve=True → usulan yang semua keyword-nya valid dan
    confidence ≥ 0.8 langsung disetujui (masuk kamus + miss di-resolve)."""
    with _lock:
        kandidat = _kandidat(max(1, min(limit, 25)))
        if not kandidat:
            return {"dibuat": 0, "auto_disetujui": 0, "usulan": [],
                    "catatan": "Tidak ada miss baru yang perlu diusulkan."}
        try:
            raw = _llm_usulan(kandidat)
        except Exception as e:
            return {"dibuat": 0, "auto_disetujui": 0, "usulan": [], "error": str(e)}

        d = _load()
        made: list[dict] = []
        auto_ok = 0
        by_q = {m["query"].lower(): m for m in kandidat}
        for item in raw:
            q = str(item.get("query") or "").strip()
            key = q.lower()
            if not q or key not in by_q or key in d:
                continue  # halusinasi query / duplikat — buang
            valid, invalid = _validate_keywords(item.get("keywords") or [])
            if not valid:
                continue  # tak ada keyword yang terbukti di katalog → tak berguna
            triggers = [t for t in (item.get("triggers") or [q])
                        if str(t).strip()] or [q]
            try:
                conf = float(item.get("confidence") or 0)
            except Exception:
                conf = 0.0
            u = {
                "id": key,
                "query": q,
                "count_miss": int(by_q[key].get("count", 1)),
                "grup": str(item.get("grup") or "umum").strip().lower(),
                "triggers": triggers,
                "keywords": valid,
                "keywords_dibuang": invalid,
                "confidence": conf,
                "alasan": str(item.get("alasan") or "")[:300],
                "status": "pending",
                "created": int(time.time()),
            }
            if auto_approve and not invalid and conf >= _AUTO_MIN_CONF:
                ok, msg = _apply(u)
                u["status"] = "approved" if ok else "pending"
                u["catatan_apply"] = msg
                if ok:
                    auto_ok += 1
            d[key] = u
            made.append(u)
        _save(d)
    return {"dibuat": len(made), "auto_disetujui": auto_ok, "usulan": made}


def _apply(u: dict) -> tuple[bool, str]:
    """Masukkan satu usulan ke kamus + resolve miss-nya. Duplikat trigger-set
    dianggap sukses (kamus sudah punya)."""
    try:
        sinonim.add(u["grup"], u["triggers"], u["keywords"])
        msg = "Masuk kamus."
    except ValueError as e:
        msg = f"Kamus sudah punya entri serupa: {e}"
    except Exception as e:
        return False, f"Gagal menulis kamus: {e}"
    try:
        search_log.resolve_miss(u["query"])
    except Exception:
        pass
    return True, msg


def approve(usulan_id: str, triggers: list[str] | None = None,
            keywords: list[str] | None = None) -> dict:
    """Setujui satu usulan (admin boleh mengedit triggers/keywords saat approve)."""
    key = (usulan_id or "").strip().lower()
    with _lock:
        d = _load()
        u = d.get(key)
        if not u:
            raise KeyError("Usulan tidak ditemukan.")
        if triggers:
            u["triggers"] = [str(t).strip() for t in triggers if str(t).strip()]
        if keywords:
            u["keywords"] = [str(k).strip() for k in keywords if str(k).strip()]
        ok, msg = _apply(u)
        if ok:
            u["status"] = "approved"
        u["catatan_apply"] = msg
        d[key] = u
        _save(d)
    if not ok:
        raise RuntimeError(msg)
    return u


# ── Generate TERJADWAL (latar) — usulan siap saat admin membuka halaman ──────
# Pola sama dgn accurate.start_scheduled_refresh: thread daemon, idempoten.
# LLM hanya dipanggil bila miss baru cukup banyak → hemat & tidak berisik.
_SCHED_INTERVAL = 24 * 3600     # cek sekali sehari
_SCHED_STARTUP_DELAY = 600      # tunggu startup selesai (indeks part dibangun dulu)
_SCHED_MIN_KANDIDAT = 3         # minimal miss baru sebelum memanggil LLM

_sched_lock = threading.Lock()
_sched_started = False


def start_scheduled_generate() -> bool:
    """Mulai thread daemon usulan sinonim berkala. Idempoten; True hanya saat
    thread baru benar-benar dimulai."""
    global _sched_started
    with _sched_lock:
        if _sched_started:
            return False
        _sched_started = True
    threading.Thread(target=_sched_loop, daemon=True,
                     name="sinonim-usulan-generate").start()
    return True


def _sched_loop() -> None:  # pragma: no cover — loop tidur; logika inti dites via generate()
    time.sleep(_SCHED_STARTUP_DELAY)
    while True:
        try:
            _sched_tick()
        except Exception as e:
            logger.warning("usulan sinonim terjadwal gagal: %s", e)
        time.sleep(_SCHED_INTERVAL)


def _sched_tick() -> None:
    """Satu putaran: cek kandidat; cukup banyak → generate (LLM sekali)."""
    if not get_settings().ai_configured:
        return
    with _lock:
        n = len(_kandidat(_SCHED_MIN_KANDIDAT))
    if n < _SCHED_MIN_KANDIDAT:
        return
    res = generate(limit=10)
    logger.info("usulan sinonim terjadwal: %s dibuat (%s pending menunggu admin)",
                res.get("dibuat"), len(list_usulan("pending")))


def reject(usulan_id: str) -> dict:
    """Tolak usulan (tetap disimpan sbg 'rejected' agar tak diusulkan ulang);
    miss-nya ikut di-resolve supaya tidak muncul terus di daftar."""
    key = (usulan_id or "").strip().lower()
    with _lock:
        d = _load()
        u = d.get(key)
        if not u:
            raise KeyError("Usulan tidak ditemukan.")
        u["status"] = "rejected"
        d[key] = u
        _save(d)
    try:
        search_log.resolve_miss(u["query"])
    except Exception:
        pass
    return u
