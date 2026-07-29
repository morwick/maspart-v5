"""Store DTC KANONIK — satu sumber untuk SEMUA kode kesalahan (union bertanda
`sumber`): bosch (mesin Bosch MC, SPN/FMI, kini ber-deskripsi Indonesia dari
kamus statik), eol (EOL CNHTC 52 unit ECU), abs (ABS WABCO SPN/FMI+Blink),
scr (SCR gas 国V kode P).

Data: `dtc_codes.json.gz` — di-generate `tools/build_dtc_store.py` dari 3 tabel
per-sumber (fault_codes.json, eol_dtc.json.gz, abs_scr_codes.json.gz) + kamus
`tools/i18n/fault_codes_i18n.json`. Skema baris lihat docstring builder.

Service lama (fault_codes / eol_dtc / abs_scr_codes) kini menjadi SHIM yang
membaca store ini via proyeksi `legacy_*()` — bentuk baris & perilaku search
lama dipertahankan supaya handler, guard, dan test tidak berubah.

Regenerasi: python backend/tools/build_dtc_store.py
"""
from __future__ import annotations

from pathlib import Path

from .knowledge_util import load_json

_DATA = Path(__file__).parent / "dtc_codes.json.gz"

_PROJ_CACHE: dict = {"mtime": None}


def _load() -> list[dict]:
    return load_json(_DATA)


def available() -> bool:
    return bool(_load())


def rows(sumber: str | None = None) -> list[dict]:
    data = _load()
    if not sumber:
        return data
    s = sumber.strip().lower()
    return [r for r in data if r.get("sumber") == s]


def count(sumber: str | None = None) -> int:
    return len(rows(sumber))


def units(sumber: str | None = None) -> list[str]:
    seen, res = set(), []
    for r in rows(sumber):
        u = r.get("unit") or ""
        if u and u not in seen:
            seen.add(u)
            res.append(u)
    return res


def search_spn_fmi(spn: int | None, fmi: int | None = None,
                   limit: int = 12) -> list[dict]:
    """Cari baris kanonik berdasar SPN (+FMI opsional) LINTAS SEMUA SUMBER.

    Ada karena tiga celah yang saling menutupi di jalur lama:
      • shim `fault_codes.search` hanya melihat sumber 'bosch';
      • `eol_dtc.search` tak punya parameter spn/fmi sama sekali;
      • proyeksi legacy `_proj()` hanya mengenal bosch/eol/abs/scr, sehingga
        baris `sumber="kartu"` (pasangan yang hanya punya lembar diagnosa PDF)
        TIDAK PERNAH keluar ke konsumen mana pun.
    Akibatnya kode yang datanya sudah kita miliki tetap dijawab "tidak ada di
    semua database" — persis keluhan di log produksi.

    Fallback SPN-saja (bila `fmi` diberi tapi tak ada yang cocok) mengikuti pola
    `abs_scr_codes.search`, satu-satunya store yang sudah melakukannya benar.
    Hasil eksak selalu didahulukan; pemanggil bisa membedakannya lewat `fmi`.
    """
    if spn is None:
        return []
    try:
        spn = int(spn)
    except (TypeError, ValueError):
        return []
    fmi_i: int | None
    try:
        fmi_i = int(fmi) if fmi is not None else None
    except (TypeError, ValueError):
        fmi_i = None

    sama_spn = [r for r in _load() if r.get("spn") == spn]
    if not sama_spn:
        return []
    if fmi_i is None:
        return sama_spn[:limit]
    eksak = [r for r in sama_spn if r.get("fmi") == fmi_i]
    return (eksak or sama_spn)[:limit]


def fmi_tersedia(spn: int | None) -> list[int]:
    """Daftar FMI yang benar-benar terdaftar untuk sebuah SPN (untuk memberi tahu
    user 'FMI 20 tak ada, yang ada 3/4/19') — lintas semua sumber."""
    if spn is None:
        return []
    try:
        spn = int(spn)
    except (TypeError, ValueError):
        return []
    out: list[int] = []
    for r in _load():
        if r.get("spn") == spn and isinstance(r.get("fmi"), int) and r["fmi"] not in out:
            out.append(r["fmi"])
    return sorted(out)


def repair_for(code: str) -> dict | None:
    """Entri EOL ber-perbaikan utk sebuah kode (eksak → prefix) — jembatan hasil
    SPN/FMI Bosch (P0645) ke langkah perbaikan EOL. Bentuk baris = legacy eol."""
    c = (code or "").strip().upper()
    if not c:
        return None
    best = None
    for r in legacy_eol():
        k = r["kode"]
        if (k == c or k.startswith(c)) and r.get("perbaikan"):
            if k == c:
                return r
            if best is None:
                best = r
    return best


# ── proyeksi legacy (bentuk baris service lama; di-cache per-mtime store) ──
def _proj() -> dict:
    try:
        mt = _DATA.stat().st_mtime if _DATA.exists() else None
    except OSError:
        mt = None
    if _PROJ_CACHE["mtime"] != mt:
        bosch, eol, abs_scr = [], [], []
        for r in _load():
            s = r.get("sumber")
            if s == "bosch":
                bosch.append({
                    "code": r["kode"], "english": r["label"],
                    "desc_cn": r["deskripsi_cn"],
                    # ADITIF: terjemahan Indonesia dari kamus statik (baru)
                    "deskripsi": r["deskripsi"],
                    "spn": r["spn"], "fmi": r["fmi"],
                    "mil": r["mil"], "svs": r["svs"],
                })
            elif s == "eol":
                eol.append({
                    "unit": r["unit"], "kode": r["kode"],
                    "deskripsi": r["deskripsi"], "penyebab": r["penyebab"],
                    "perbaikan": r["perbaikan"], "part": r["part"],
                })
            elif s == "abs":
                abs_scr.append({
                    "sistem": "ABS", "kode": "",
                    "spn": r["spn"], "fmi": r["fmi"], "sid": r["sid"],
                    "blink": r["blink"], "komponen": r["label"],
                    "deskripsi": r["deskripsi"], "penyebab": r["penyebab"],
                    "reaksi": r["reaksi"], "perbaikan": r["perbaikan"],
                    "lampu": r["lampu"],
                })
            elif s == "scr":
                abs_scr.append({
                    "sistem": "SCR", "kode": r["kode"],
                    "spn": None, "fmi": None, "sid": None,
                    "blink": "", "komponen": r["part"],
                    "deskripsi": r["deskripsi"], "penyebab": r["penyebab"],
                    "reaksi": r["reaksi"], "perbaikan": "",
                    "lampu": "",
                })
        _PROJ_CACHE.update(mtime=mt, bosch=bosch, eol=eol, abs_scr=abs_scr)
    return _PROJ_CACHE


def legacy_bosch() -> list[dict]:
    return _proj().get("bosch", [])


def legacy_eol() -> list[dict]:
    return _proj().get("eol", [])


def legacy_abs_scr() -> list[dict]:
    return _proj().get("abs_scr", [])
