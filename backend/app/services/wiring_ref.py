"""Diagram WIRING / definisi pin sensor-aktuator mesin Bosch & sistem SCR
(truk Sinotruk) — 55 JPEG di `data/wiring/` + `index.json` {file, grp, label}.

Sumber: aplikasi EOL CNHTC (boschimage), diekstrak `tools/build_eol_dtc.py`
dari artifact "Panduan Teknisi Sinotruk". Deploy file via scp (pola
data/manuals). Cache index per-mtime; gambar dibaca on-demand oleh tool
`diagram_wiring` (ai_assistant) lalu di-stash `ai_export.stash_raw` supaya
tampil INLINE di chat lewat kanal `exploded_images`.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from ..core.config import get_settings

_CACHE: dict = {"mtime": None, "rows": []}

_FNAME_RE = re.compile(r"^[A-Za-z0-9_.\-]+\.jpe?g$")

# Istilah lapangan → kata kunci label/nama file diagram.
_SYN: dict[str, list[str]] = {
    "pedal gas": ["accp", "app", "pedal gas"], "gas": ["accp", "app"],
    "aselerasi": ["accp", "app"], "akselerasi": ["accp", "app"],
    "rail": ["rail", "rps"], "common rail": ["rail", "rps"],
    "tekanan rail": ["rail", "rps"],
    "coolant": ["coolant", "cts"], "suhu air": ["coolant", "cts"],
    "radiator": ["radiator", "fan"], "kipas": ["kipas", "fan"],
    "boost": ["boost", "apts"], "udara masuk": ["boost", "apts"],
    "turbo": ["turbo", "tc"], "turbocharger": ["turbo", "tc"],
    "maf": ["hfm", "maf"], "massa udara": ["hfm", "maf"],
    "adblue": ["scr", "adblue", "urea"], "urea": ["scr", "adblue"],
    "scr": ["scr", "adblue"], "dosis": ["dosis", "dv", "scr"],
    "egr": ["egr"], "dpf": ["dpf", "dps"],
    "obd": ["obd"], "can": ["can", "jaringan"], "jaringan": ["can"],
    "camshaft": ["camshaft", "cs"], "noken": ["camshaft", "cs"],
    "starter": ["starter", "sr"], "cruise": ["cruise", "xunhang"],
    "mil": ["mil", "check engine", "cslamp"],
    "check engine": ["mil", "cslamp"],
    "lampu": ["lampu", "lamp"],
    "oli": ["oli", "opts"], "tekanan oli": ["oli", "opts"],
    "bahan bakar": ["bahan bakar", "fuel", "fts", "wifs", "fgr"],
    "solar": ["bahan bakar", "fuel", "fts", "wifs"],
    "pemisah air": ["air dalam bahan bakar", "wifs"],
    "water separator": ["air dalam bahan bakar", "wifs"],
    "kecepatan": ["kecepatan", "vss"], "speedometer": ["kecepatan", "vss"],
    "throttle": ["throttle", "tvact"],
    "pemanas": ["pemanas", "fhr", "jhr", "fgr", "grid"],
    "instrumen": ["instrumen", "meterunit"], "panel": ["instrumen", "meterunit"],
    "gas buang": ["gas buang", "ets"], "exhaust": ["gas buang", "ets"],
    "solenoid": ["solenoid", "eso"], "mati mesin": ["solenoid", "eso"],
    "ac": ["a/c", "acr"],
    # kurasi K4 2026-07-17 (label ambigu didekode):
    "injector": ["pyq", "injector", "injektor"],
    "injektor": ["pyq", "injector"],
    "crankshaft": ["mss", "crankshaft"],
    "kruk as": ["mss", "crankshaft"],
    "putaran mesin": ["mss", "putaran"],
    "remote": ["yctb", "remote"],
    "katalis": ["katalis", "cts"],
    "tangki urea": ["tangki urea", "ratts", "rathv"],
    "pompa urea": ["pompa urea", "rapc", "rapps"],
}


def _dir() -> Path:
    return get_settings().data_path / "wiring"


def _load() -> list[dict]:
    p = _dir() / "index.json"
    try:
        mt = p.stat().st_mtime if p.is_file() else None
    except OSError:
        mt = None
    if mt != _CACHE["mtime"]:
        rows: list[dict] = []
        try:
            if mt is not None:
                rows = json.loads(p.read_text(encoding="utf-8")) or []
        except Exception:
            rows = []
        _CACHE.update(mtime=mt, rows=rows)
    return _CACHE["rows"]


def available() -> bool:
    return bool(_load())


def count() -> int:
    return len(_load())


def labels() -> list[str]:
    return [r.get("label") or r.get("file") for r in _load()]


def _terms(q: str) -> list[str]:
    ql = (q or "").strip().lower()
    terms = [ql] if ql else []
    for k, vs in _SYN.items():
        if re.search(r"(?<![a-z])" + re.escape(k) + r"(?![a-z])", ql):
            for v in vs:
                if v not in terms:
                    terms.append(v)
    return terms


def search(query: str = "", limit: int = 6) -> list[dict]:
    """Cari diagram by label/nama-file/grup (+ sinonim lapangan ID)."""
    rows = _load()
    terms = _terms(query)
    if not terms:
        return []
    out = []
    for r in rows:
        hay = f"{r.get('label','')} {r.get('file','')} {r.get('grp','')}".lower()
        # Batas kata (angka/huruf) — cegah 'app' nyangkut di 'SCR_rapps.jpg'.
        if any(re.search(r"(?<![a-z0-9])" + re.escape(t) + r"(?![a-z0-9])", hay)
               for t in terms):
            out.append(r)
    return out[:limit]


def image_bytes(fname: str) -> bytes | None:
    """Bytes JPEG sebuah diagram — nama file divalidasi ketat + wajib terdaftar
    di index (anti path-traversal)."""
    f = (fname or "").strip()
    if not _FNAME_RE.match(f):
        return None
    if not any(r.get("file") == f for r in _load()):
        return None
    p = _dir() / f
    try:
        return p.read_bytes() if p.is_file() else None
    except OSError:
        return None
