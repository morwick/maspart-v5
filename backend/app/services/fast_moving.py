"""Builder dataset FAST MOVING per kode MODEL (turunan cache EPC + populasi).

Masalah yang dijawab: "part fast moving untuk NX400" tidak bisa dijawab dari
EPC langsung — EPC per-VIN (konfigurasi bisa beda antar unit SE-MODEL: running
change, dual supplier, beda opsi), dan tak punya konsep "fast moving". Builder
ini menurunkannya sendiri, SEPENUHNYA offline (disk saja, pola ai_belajar):

  cache data/epc_unit_items/<RANGKA>.json  (hasil mining EPC, per unit)
  + populasi (NOMOR RANGKA → MODEL/JENIS/TAHUN)
  + kamus kategori part aus (data/fast_moving/kamus_kategori.json, editable
    tanpa deploy; fallback _KAMUS_DEFAULT)
  → data/fast_moving/fast_moving.json.gz  (agregat per kode MODEL)

Keputusan desain (hasil diskusi pemilik 2026-08-04):
  - Satuan = SLOT fungsi (nama ternormalisasi), bukan PN — varian intra-model
    tampil SEMUA dengan porsinya (n_unit dari M unit sampel), tak pernah
    memilih satu PN diam-diam.
  - TAHUN unit pembawa tiap varian ikut disimpan → varian bisa dibelah per
    batch produksi ("PN A di unit 2021-2022, PN B di 2023+").
  - pengganti (partAlternates Atlas) terbawa per varian. Flag `pasok`
    (marketability) SENGAJA TIDAK dibawa: audit 2026-08-04 menemukan 78% baris
    bertanda 'b' termasuk filter servis standar yang jelas masih dipasok, dan
    PN yang sama bisa 'b' di satu unit tapi 'g' di unit lain — bukan penanda
    discontinued yang bisa dipercaya.
  - unit_sampel disimpan agar penyaji bisa jujur "berdasarkan N unit sampel";
    daftar per-model = perencanaan stok; unit spesifik tetap wajib per-VIN.
"""
from __future__ import annotations

import logging
import re
from collections import Counter
from pathlib import Path

from ..core.config import get_settings
from .knowledge_util import load_json, write_json_gz

logger = logging.getLogger("maspart.fast_moving")

# ── kamus kategori (fallback bila file data absen) ───────────────────────
# Kunci EN dicocokkan substring pada nama EN (lowercase, berspasi pinggir);
# kunci CJK dicocokkan substring pada nama CN. Negatif global menyaring
# hardware pendukung (bracket filter ≠ filter).
_KAMUS_DEFAULT = {
    "negatif": ["bracket", "clamp", "bolt", "nut", "washer", "screw",
                "pipe", "hose", "flange", "gasket", "seat wrench"],
    "kategori": {
        "filter": ["filter element", "oil filter", "fuel filter", "air filter",
                   "water separator", "filter insert", "cartridge", "滤清器",
                   "滤芯", "干燥筒", "干燥罐"],
        "rem": ["brake lining", "brake pad", "brake shoe", "brake disc",
                "brake drum", "摩擦片", "制动蹄", "制动盘", "制动鼓", "刹车片"],
        "kopling": ["clutch disc", "clutch plate", "clutch driven",
                    "pressure plate", "release bearing", "离合器片",
                    "压盘", "分离轴承", "从动盘"],
        "bearing_seal": ["hub bearing", "wheel bearing", "oil seal",
                         "轮毂轴承", "油封"],
        "belt": ["v-belt", "fan belt", "drive belt", "poly v", "皮带"],
        "karet": ["rubber mount", "rubber support", "rubber bushing",
                  "rubber bearing", "cab mount", "橡胶支座", "橡胶衬套",
                  "橡胶轴承", "缓冲块"],
    },
}

_CJK_RE = re.compile(r"[一-鿿]")
_SLOT_BUANG_RE = re.compile(r"\b(assembly|assy|assemblies)\b")
# HP dari kode model Sinotruk: ZZ + 4 digit kelas + huruf kabin + 2 digit HP×10
# (ZZ3257V404JF1 → 40 → 400 HP; ZZ4257V324HE1B → 320 HP). Best-effort.
_HP_RE = re.compile(r"^Z+\d{4}[A-Z](\d{2})\d")


def _dir_out() -> Path:
    return get_settings().data_path / "fast_moving"


def _kamus_path() -> Path:
    return _dir_out() / "kamus_kategori.json"


def _kamus() -> dict:
    d = load_json(_kamus_path(), default=None)
    if isinstance(d, dict) and d.get("kategori"):
        return d
    return _KAMUS_DEFAULT


def hp_dari_model(model: str) -> int | None:
    m = _HP_RE.match(str(model or "").upper().replace(" ", ""))
    return int(m.group(1)) * 10 if m else None


def _peta_populasi() -> dict[str, dict]:
    """NOMOR RANGKA (8 char akhir) → {model, jenis, tahun}. Best-effort."""
    try:
        from . import populasi
        df = populasi._ensure()
        if df is None or getattr(df, "empty", True):
            return {}
        cols = {str(c).strip().upper(): c for c in df.columns}
        c_rangka = cols.get("NOMOR RANGKA")
        c_model = cols.get("MODEL")
        if not c_rangka or not c_model:
            return {}
        c_jenis, c_tahun = cols.get("JENIS"), cols.get("TAHUN")
        out: dict[str, dict] = {}
        for _, r in df.iterrows():
            rk = re.sub(r"[^A-Z0-9]", "", str(r.get(c_rangka) or "").upper())
            model = " ".join(str(r.get(c_model) or "").upper().split())
            if len(rk) < 8 or not model:
                continue
            out[rk[-8:]] = {
                "model": model,
                "jenis": " ".join(str(r.get(c_jenis) or "").upper().split())
                         if c_jenis else "",
                "tahun": str(r.get(c_tahun) or "").strip()[:4] if c_tahun else "",
            }
        return out
    except Exception:
        logger.info("fast_moving: populasi tak tersedia")
        return {}


def _klasifikasi(nama_en: str, nama_cn: str, kamus: dict) -> str | None:
    """Kategori fast-moving untuk satu baris EPC, None bila bukan."""
    hay_en = f" {nama_en.lower()} "
    for neg in kamus.get("negatif") or []:
        if neg in hay_en:
            return None
    for kat, kws in (kamus.get("kategori") or {}).items():
        for k in kws:
            if _CJK_RE.search(k):
                if k in nama_cn:
                    return kat
            elif k in hay_en:
                return kat
    return None


def _slot_key(nama_en: str, nama_cn: str) -> str:
    """Nama slot fungsi ternormalisasi (buang 'assembly', rapikan spasi)."""
    s = _SLOT_BUANG_RE.sub(" ", (nama_en or "").lower())
    s = " ".join(s.split())
    return s or " ".join((nama_cn or "").split())


def build() -> dict:
    """Bangun data/fast_moving/fast_moving.json.gz dari cache EPC + populasi.
    Return ringkasan {model_n, slot_n, unit_dipakai, unit_tanpa_populasi}."""
    d_items = get_settings().data_path / "epc_unit_items"
    peta = _peta_populasi()
    kamus = _kamus()

    # populasi per model (SEMUA unit, bukan hanya yang ter-cache)
    pop_model: Counter = Counter(v["model"] for v in peta.values())

    # model → frame sampel → baris terklasifikasi
    per_model: dict[str, dict] = {}
    unit_dipakai = unit_tanpa_pop = 0
    for f in sorted(d_items.glob("*.json")) if d_items.exists() else []:
        frame = f.stem.upper()
        info = peta.get(frame)
        if not info:
            unit_tanpa_pop += 1      # unit di luar populasi (mis. milik pihak lain)
            continue
        payload = load_json(f, default=None)
        rows = (payload or {}).get("rows") or []
        if not rows or payload.get("incomplete"):
            continue                 # build parsial = bolong, jangan meracuni porsi
        unit_dipakai += 1
        m = per_model.setdefault(info["model"], {
            "jenis": Counter(), "unit_sampel": [], "slot": {}})
        m["jenis"][info["jenis"]] += 1
        m["unit_sampel"].append(frame)
        terlihat: set = set()        # (slot, pn) unik per unit — qty>1 ≠ 2 unit
        for row in rows:
            pn = str(row.get("pn") or "").strip().upper()
            if not pn:
                continue
            nama = " ".join(str(row.get("nama") or "").split())
            nama_cn = " ".join(str(row.get("nama_cn") or "").split())
            kat = _klasifikasi(nama, nama_cn, kamus)
            if not kat:
                continue
            sk = (kat, _slot_key(nama, nama_cn))
            if (sk, pn) in terlihat:
                continue
            terlihat.add((sk, pn))
            slot = m["slot"].setdefault(sk, {})
            v = slot.setdefault(pn, {
                "pn": pn, "nama": nama, "nama_cn": nama_cn, "qty": row.get("qty"),
                "n_unit": 0, "tahun": set(), "pengganti": []})
            v["n_unit"] += 1
            if info["tahun"]:
                v["tahun"].add(info["tahun"])
            for g in row.get("pengganti") or []:
                gpn = str((g or {}).get("pn") or "").strip().upper()
                if gpn and gpn not in v["pengganti"]:
                    v["pengganti"].append(gpn)

    # bentuk final (deterministik: sort model, slot, varian)
    final_model: dict[str, dict] = {}
    slot_n = 0
    for model, m in sorted(per_model.items()):
        n_sampel = len(m["unit_sampel"])
        slots = []
        for (kat, nama_slot), varian in sorted(m["slot"].items()):
            vs = sorted(varian.values(), key=lambda v: (-v["n_unit"], v["pn"]))
            slots.append({
                "kategori": kat, "slot": nama_slot,
                "varian": [{**v, "tahun": sorted(v["tahun"])} for v in vs],
            })
            slot_n += 1
        jenis = m["jenis"].most_common(1)[0][0] if m["jenis"] else ""
        final_model[model] = {
            "jenis": jenis, "hp": hp_dari_model(model),
            "unit_populasi": pop_model.get(model, 0),
            "unit_sampel": sorted(m["unit_sampel"]),
            "n_sampel": n_sampel, "slot": slots,
        }

    write_json_gz(_dir_out() / "fast_moving.json.gz",
                  {"versi": 1, "model": final_model})
    ringkas = {"model_n": len(final_model), "slot_n": slot_n,
               "unit_dipakai": unit_dipakai, "unit_tanpa_populasi": unit_tanpa_pop}
    logger.info("fast_moving: build %s", ringkas)
    return ringkas


def data() -> dict:
    """Dataset terbangun (cache per-mtime via load_json). {} bila belum ada."""
    d = load_json(_dir_out() / "fast_moving.json.gz", default=None)
    return d if isinstance(d, dict) else {}
