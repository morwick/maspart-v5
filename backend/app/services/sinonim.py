"""
Kamus sinonim istilah lapangan — CRUD + LOOKUP TERPUSAT untuk
data/sinonim/sinonim.json.

File JSON ini SATU-SATUNYA sumber kamus. Sejak rombakan 2026-07-17 modul ini
juga memuat fungsi lookup terpusat (dulu di ai_assistant): `entries()`
(per-mtime — editan admin langsung terpakai tanpa restart), `expand_query()`,
`umbrella_keywords()`, `block()`. ai_assistant memakai wrapper tipis
(_load_sinonim_entries/_expand_query/dst — nama dipertahankan utk test);
buyer_catalog memakai modul ini langsung.

Format entri: {"grup": str, "triggers": [istilah lapangan/Indonesia...],
"keywords": [kata kunci nama part katalog/Inggris...]}.

Tulis ATOMIK (tmp + os.replace) agar pembaca tidak pernah melihat file
setengah jadi; serialisasi lewat lock proses (uvicorn kita single-process).
"""
from __future__ import annotations

import json
import os
import re
import threading
from pathlib import Path

from ..core.config import get_settings
from .knowledge_util import load_json

_lock = threading.Lock()


def _file() -> Path:
    return get_settings().data_path / "sinonim" / "sinonim.json"


def _file_gejala() -> Path:
    """Dataset GEJALA → part (dibangun offline oleh backend/tools/build_gejala_map.py).

    Skemanya SAMA PERSIS dengan sinonim.json, dan itu disengaja: dengan begitu
    seluruh jalur yang sudah ada — expand_query, umbrella_keywords, subset kamus
    di prompt, penjaga istilah deterministik — otomatis ikut memahami keluhan
    lapangan ("mesin pincang", "asap hitam", "rem blong") tanpa satu baris kode
    runtime baru. File belum ada = fitur mati, bukan error.

    Dipisah dari sinonim.json supaya CRUD admin (load/save) tidak pernah menulis
    ulang data turunan ini, dan supaya rebuild-nya bisa menimpa satu file saja.
    """
    return get_settings().data_path / "sinonim" / "gejala_map.json"


def _clean_list(vals) -> list[str]:
    """Rapikan daftar string: strip, buang kosong & duplikat (case-insensitive,
    urutan asli dipertahankan)."""
    out: list[str] = []
    seen: set[str] = set()
    for v in vals or []:
        s = str(v).strip()
        key = s.lower()
        if s and key not in seen:
            seen.add(key)
            out.append(s)
    return out


def _normalize(grup: str, triggers, keywords) -> dict:
    g = (grup or "").strip().lower() or "umum"
    trig = _clean_list(triggers)
    kw = _clean_list(keywords)
    if not trig:
        raise ValueError("Minimal satu istilah lapangan (trigger).")
    if not kw:
        raise ValueError("Minimal satu kata kunci katalog (keyword).")
    return {"grup": g, "triggers": trig, "keywords": kw}


def load() -> list[dict]:
    """Seluruh entri kamus (list kosong bila file belum ada/korup)."""
    p = _file()
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8")) or []
    except Exception:
        return []
    return [e for e in data if isinstance(e, dict)]


# ── LOOKUP TERPUSAT (dipakai ai_assistant + buyer_catalog) ───────────
def entries() -> list[dict]:
    """Entri kamus dgn cache per-mtime (knowledge_util.load_json) — murah
    dipanggil per-query, editan admin tetap langsung terpakai.

    Kamus kurasi (sinonim.json) DULU, lalu dataset gejala turunan — urutannya
    penting: pada seri skor yang sama, entri kurasi manusia harus menang. Grup
    yang sudah ada di kamus kurasi tidak ditimpa dataset turunan."""
    data = load_json(_file())
    out = [e for e in data if isinstance(e, dict)] if isinstance(data, list) else []
    gj = load_json(_file_gejala())
    if isinstance(gj, list):
        ada = {str((e.get("grup") or "")).lower() for e in out}
        out += [e for e in gj
                if isinstance(e, dict) and str((e.get("grup") or "")).lower() not in ada]
    return out


# Klitika (akhiran milik) Bahasa Indonesia yang MENEMPEL di ujung kata tanpa
# spasi: '-nya', '-ku', '-mu'. Tanpa ini seluruh kanal berbasis kamus mati pada
# bentuk follow-up paling alami di lapangan — "filternya apa?", "gambar
# teknisnya mana?", "cucuk pernya berapa?" — karena `(?!\w)` menolak huruf 'n'
# sesudah trigger. Satu fungsi ini dipakai kamus istilah (expand_query,
# umbrella_keywords, subset prompt), Rute Maksud (maksud.cocok), dan penjaga
# istilah deterministik (_paksa_istilah_kamus), jadi tambalannya cukup di sini.
_KLITIKA = r"(?:nya|ku|mu)?"

# Trigger PENDEK tidak boleh berklitika: banyak kata Indonesia biasa kebetulan
# berupa 2 huruf + klitika — 'hanya' (ha+nya), 'punya' (pu+nya), 'tanya',
# 'kamu' (ka+mu), 'ilmu', 'buku' (bu+ku). Ambang 3 huruf menjauhkan itu semua
# sambil tetap melayani trigger lapangan terpendek yang nyata ('per' → 'pernya').
_KLITIKA_MIN_LEN = 3


def hit(trigger: str, text: str) -> bool:
    """Trigger cocok sbg KATA/FRASA utuh, bukan substring di tengah kata
    (mis. trigger 'per' TIDAK boleh cocok di dalam 'persneling') — dengan
    toleransi KLITIKA di ujung ('filternya', 'gambar teknisnya')."""
    t = (trigger or "").strip().lower()
    if not t:
        return False
    ekor = _KLITIKA if len(t) >= _KLITIKA_MIN_LEN else ""
    return re.search(r"(?<!\w)" + re.escape(t) + ekor + r"(?!\w)",
                     (text or "").lower()) is not None


def expand_query(q: str, entries_fn=None) -> tuple[list[str], list[str]]:
    """Perluas query dgn keyword sinonim bila mengandung istilah lapangan.
    Return (daftar istilah cari [termasuk q asli], daftar trigger yang cocok)."""
    terms: list[str] = [q]
    matched: list[str] = []
    for e in (entries_fn or entries)():
        h = next((t for t in (e.get("triggers") or []) if t and hit(t, q)), None)
        if h:
            matched.append(h)
            for kw in (e.get("keywords") or []):
                if kw and kw not in terms:
                    terms.append(kw)
    return terms, matched


# Kata KATEGORI "payung": kata polos Indonesia (+ padanan Inggris) yang mewakili
# SELURUH keluarga sub-part. expand_query tak mengekspansi kata polos spt 'kopling'
# (trigger sinonim semuanya frasa: 'kampas kopling', 'matahari kopling', dst) → jadi
# 'kopling' saja melewatkan hampir semua sub-part. umbrella_keywords menambal ini.
UMBRELLA_KATEGORI = {
    "kopling": ["kopling", "clutch"],
    "clutch": ["kopling", "clutch"],
    "rem": ["rem", "brake"],
    "brake": ["rem", "brake"],
    "gardan": ["gardan", "differential"],
    "transmisi": ["transmisi", "transmission", "gearbox", "persneling"],
    "kabin": ["kabin", "cabin"],
    "mesin": ["mesin", "engine"],
    "kelistrikan": ["kelistrikan", "electric"],
    "filter": ["filter", "saringan"],
    "saringan": ["filter", "saringan"],
    "suspensi": ["suspensi", "suspension"],
}


def umbrella_keywords(kata_kunci: str, entries_fn=None) -> list[str]:
    """Bila `kata_kunci` memuat kata KATEGORI payung (mis. 'kopling', 'rem'),
    kumpulkan keyword katalog dari SEMUA grup sinonim terkait (token payung muncul
    di nama grup / trigger / keyword). [] bila bukan kategori payung."""
    ql = (kata_kunci or "").lower()
    tokens: list[str] = []
    for w, toks in UMBRELLA_KATEGORI.items():
        if hit(w, ql):
            for t in toks:
                if t not in tokens:
                    tokens.append(t)
    if not tokens:
        return []
    kws: list[str] = []
    for e in (entries_fn or entries)():
        hay = " ".join([e.get("grup") or "", *(e.get("triggers") or []),
                        *(e.get("keywords") or [])])
        if any(hit(tok, hay) for tok in tokens):
            for kw in (e.get("keywords") or []):
                if kw and kw not in kws:
                    kws.append(kw)
    return kws


def block(entries_fn=None) -> str:
    """Kamus istilah lapangan (Indonesia → kata kunci Inggris) sbg blok teks."""
    lines: list[str] = []
    for e in (entries_fn or entries)():
        trig = ", ".join(dict.fromkeys(t for t in (e.get("triggers") or []) if t))
        kw = ", ".join(dict.fromkeys(k for k in (e.get("keywords") or []) if k))
        if trig and kw:
            lines.append(f"- {trig} → {kw}")
    return "\n".join(lines)


def _save(entries: list[dict]) -> None:
    p = _file()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, p)  # atomik: pembaca melihat file lama ATAU baru, tak pernah separuh


def add(grup: str, triggers, keywords) -> dict:
    """Tambah entri baru. Tolak bila trigger-set persis sama sudah ada (hindari
    duplikat tak sengaja — admin sebaiknya mengedit entri yang ada)."""
    e = _normalize(grup, triggers, keywords)
    with _lock:
        entries = load()
        new_set = {t.lower() for t in e["triggers"]}
        for old in entries:
            if {str(t).lower() for t in (old.get("triggers") or [])} == new_set:
                raise ValueError(
                    "Entri dengan istilah lapangan yang sama sudah ada "
                    f"(grup '{old.get('grup', '')}') — edit entri itu saja."
                )
        entries.append(e)
        _save(entries)
    return e


def update(index: int, grup: str, triggers, keywords) -> dict:
    e = _normalize(grup, triggers, keywords)
    with _lock:
        entries = load()
        if not 0 <= index < len(entries):
            raise IndexError("Entri tidak ditemukan — data mungkin berubah, muat ulang halaman.")
        entries[index] = e
        _save(entries)
    return e


def delete(index: int) -> dict:
    with _lock:
        entries = load()
        if not 0 <= index < len(entries):
            raise IndexError("Entri tidak ditemukan — data mungkin berubah, muat ulang halaman.")
        gone = entries.pop(index)
        _save(entries)
    return gone
