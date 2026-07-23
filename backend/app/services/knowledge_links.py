"""Indeks TAUTAN ENTITAS antar-store pengetahuan asisten ("nyambung").

Masalah: 16 store pengetahuan (DTC, manual, wiring, pin ECU, jadwal perawatan,
filter, repairkit, katalog BOM, pengetahuan admin, …) berdiri sendiri — fan-out
lintas-store hanya ada di 3 titik hardcode. Store ini mengindeks ENTITAS bersama
(PN ternormalisasi, kode DTC/(SPN,FMI), model unit, nama ECU) → posting record
di store mana pun yang menyebutnya, sehingga tiap jawaban tool bisa membawa
`pengetahuan_terkait` (lihat _sisip_terkait di ai_parts/p4).

File: data_path/knowledge_links.json.gz (runtime-rebuilt oleh miner ai_belajar —
karenanya di data_path, BUKAN app/services yang di-track git). Bentuk:
  {"entitas": {"pn:WG9100443050": [posting…], "dtc:P0087": […],
               "spnfmi:100:3": […], "model:SD16": […], "ecu:NBCU": […]},
   "model_kanonik": […], "ecu_kanonik": […], "cakupan": {…}}
Posting = {"store", "judul", "buka", "ref"} (+"pembeli": false utk pengetahuan
non-publik). `ref` = kunci alami per store (reindex-stable) — untuk dedup/debug,
TIDAK dikirim ke model. PEMBATAS: hanya entitas yang disebut ≥2 STORE BERBEDA
yang disimpan (tautan = definisi lintas-store; PN katalog-saja tidak membuat
posting) + maks 12 posting/entitas.

Builder inti ADA DI SINI (build()/build_and_save()) supaya miner ai_belajar bisa
rebuild in-process; tools/build_knowledge_links.py = wrapper tipis (pola
ai_knowledge). Semua sumber best-effort: store absen → dilewati.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

from ..core.config import get_settings
from .knowledge_util import load_json, norm_pn, write_json_gz

logger = logging.getLogger("maspart.knowledge_links")

_SVC = Path(__file__).resolve().parent          # store .json.gz sebelah loader
_FILE = "knowledge_links.json.gz"

_MAX_POSTING_PER_ENTITAS = 12
_MAX_JUDUL = 70

# Urutan prioritas store saat menyajikan tautan (yang paling "berisi" duluan).
_PRIORITAS = ("pengetahuan", "manual_teks", "dtc_codes", "jadwal_perawatan",
              "filter_shantui", "repairkit", "part_taxonomy", "pin_ecu",
              "wiring_ref", "manual_media", "skema_ref", "epc_unit",
              "catalog_bom")

# PN di teks bebas — konservatif (hindari tanggal/kode acak): prefix huruf +
# ≥6 digit, ATAU digit murni 9-13 (PN Weichai), ATAU pola 3-digit+huruf SITRAK.
_PN_TEXT_RE = re.compile(r"\b(?:[A-Z]{1,4}\d{6,}[A-Z0-9/\-]*|\d{9,13}|\d{3}[A-Z]\d{4,}[A-Z0-9\-]*)\b")
_DTC_RE = re.compile(r"\b([PBU]\d{4}[A-Z0-9]{0,2})\b")
_SPN_FMI_RE = re.compile(r"\bSPN\s*[.:]?\s*(\d{2,6})\s*[,/ ]\s*FMI\s*[.:]?\s*(\d{1,2})\b",
                         re.IGNORECASE)


def _path() -> Path:
    return get_settings().data_path / _FILE


def _load() -> dict:
    d = load_json(_path(), default={})
    return d if isinstance(d, dict) else {}


def available() -> bool:
    return bool(_load().get("entitas"))


def count() -> int:
    return len(_load().get("entitas") or {})


# ═══════════════════════════════════════════════════════════════════════
#  KUNCI ENTITAS
# ═══════════════════════════════════════════════════════════════════════
def _k_pn(pn: str) -> str | None:
    n = norm_pn(pn)
    return f"pn:{n}" if len(n) >= 6 else None


def _k_dtc(kode: str) -> str | None:
    k = str(kode or "").strip().upper()
    return f"dtc:{k}" if k else None


def _k_spnfmi(spn, fmi) -> str | None:
    try:
        return f"spnfmi:{int(spn)}:{int(fmi)}"
    except Exception:
        return None


def _k_model(model: str) -> str | None:
    m = " ".join(str(model or "").upper().split())
    return f"model:{m}" if len(m) >= 3 else None


def _k_ecu(ecu: str) -> str | None:
    e = str(ecu or "").strip().upper()
    return f"ecu:{e}" if len(e) >= 2 else None


def entitas(pn: str = "", dtc: str = "", spn=None, fmi=None, model: str = "",
            ecu: str = "", teks: str = "") -> list[str]:
    """Susun kunci entitas dari kriteria eksplisit + teks bebas. Pencocokan teks
    memakai daftar model/ECU kanonik hasil build (word-boundary, anti-substring)."""
    out: list[str] = []

    def _add(k):
        if k and k not in out:
            out.append(k)

    _add(_k_pn(pn))
    _add(_k_dtc(dtc))
    _add(_k_spnfmi(spn, fmi))
    _add(_k_model(model))
    _add(_k_ecu(ecu))
    t = str(teks or "").upper()
    if t:
        for m in _PN_TEXT_RE.findall(t):
            _add(_k_pn(m))
        for m in _DTC_RE.findall(t):
            _add(_k_dtc(m))
        for s, f in _SPN_FMI_RE.findall(t):
            _add(_k_spnfmi(s, f))
        d = _load()
        for mdl in d.get("model_kanonik") or []:
            if re.search(rf"(?<![A-Z0-9]){re.escape(mdl)}(?![A-Z0-9])", t):
                _add(f"model:{mdl}")
        for ec in d.get("ecu_kanonik") or []:
            if re.search(rf"(?<![A-Z0-9]){re.escape(ec)}(?![A-Z0-9])", t):
                _add(f"ecu:{ec}")
    return out


def terkait(ents: list[str], exclude_store: str = "", limit: int = 5,
            untuk_pembeli: bool = False) -> list[dict]:
    """Posting store LAIN yang menyebut entitas-entitas ini. Dedup per
    (store, ref); pengetahuan non-publik disembunyikan dari pembeli."""
    idx = _load().get("entitas") or {}
    seen: set[tuple] = set()
    rows: list[dict] = []
    for e in ents or []:
        for p in idx.get(e) or []:
            if p.get("store") == exclude_store:
                continue
            if untuk_pembeli and p.get("pembeli") is False:
                continue
            # dedup by ref DAN by judul — record beda halaman berjudul sama
            # (mis. tabel DTC manual berulang) cuma bikin berisik di jawaban.
            key = (p.get("store"), p.get("ref"))
            key_j = (p.get("store"), p.get("judul"))
            if key in seen or key_j in seen:
                continue
            seen.add(key)
            seen.add(key_j)
            rows.append(p)
    rows.sort(key=lambda p: (_PRIORITAS.index(p.get("store"))
                             if p.get("store") in _PRIORITAS else 99))
    return rows[:max(1, limit)]


# ═══════════════════════════════════════════════════════════════════════
#  BUILDER — kumpulkan (entitas → posting) dari semua store
# ═══════════════════════════════════════════════════════════════════════
def _judul(s: str) -> str:
    s = " ".join(str(s or "").split())
    return s[:_MAX_JUDUL]


def _emit(acc: dict, ent: str | None, store: str, judul: str, buka: str,
          ref: str, pembeli: bool | None = None) -> None:
    if not ent or not judul or not ref:
        return
    row = {"store": store, "judul": _judul(judul), "buka": buka, "ref": ref}
    if pembeli is False:
        row["pembeli"] = False
    acc.setdefault(ent, [])
    if not any(p["store"] == store and p["ref"] == ref for p in acc[ent]):
        acc[ent].append(row)


def _teks_entitas(acc, teks, store, judul, buka, ref, model_set, ecu_set,
                  pembeli=None):
    """Entitas dari TEKS bebas record (label/deskripsi/sinonim media dsb)."""
    t = str(teks or "").upper()
    if not t:
        return
    for m in _PN_TEXT_RE.findall(t):
        _emit(acc, _k_pn(m), store, judul, buka, ref, pembeli)
    for m in _DTC_RE.findall(t):
        _emit(acc, _k_dtc(m), store, judul, buka, ref, pembeli)
    for s, f in _SPN_FMI_RE.findall(t):
        _emit(acc, _k_spnfmi(s, f), store, judul, buka, ref, pembeli)
    for mdl in model_set:
        if re.search(rf"(?<![A-Z0-9]){re.escape(mdl)}(?![A-Z0-9])", t):
            _emit(acc, f"model:{mdl}", store, judul, buka, ref, pembeli)
    for ec in ecu_set:
        if re.search(rf"(?<![A-Z0-9]){re.escape(ec)}(?![A-Z0-9])", t):
            _emit(acc, f"ecu:{ec}", store, judul, buka, ref, pembeli)


def build() -> dict:
    """Bangun indeks dari semua store yang tersedia. Deterministik, offline,
    best-effort per sumber (satu store rusak tidak menjatuhkan build)."""
    s = get_settings()
    acc: dict[str, list[dict]] = {}
    cakupan: dict[str, int] = {}

    # ── daftar kanonik model & ECU (dipakai pencocokan teks di bawah) ──
    model_set: set[str] = set()
    ecu_set: set[str] = set()
    try:
        for r in load_json(_SVC / "jadwal_perawatan.json.gz"):
            m = str(r.get("model") or "").upper().strip()
            if len(m) >= 3:
                model_set.add(m)
    except Exception:
        pass
    try:
        for r in load_json(_SVC / "filter_shantui.json.gz"):
            m = str(r.get("model") or "").upper().strip()
            if len(m) >= 3:
                model_set.add(m)
    except Exception:
        pass
    rk = {}
    try:
        rk = load_json(s.data_path / "repairkit" / "transmisi.json", default={})
        if isinstance(rk, dict):
            for mdl, ent in rk.items():
                if len(str(mdl)) >= 3:
                    model_set.add(str(mdl).upper())
                for u in (ent.get("unit") or []):
                    if len(str(u)) >= 4:
                        model_set.add(str(u).upper())
    except Exception:
        rk = {}
    pin_rows = []
    try:
        pin_rows = load_json(_SVC / "pin_ecu.json.gz")
        for r in pin_rows:
            e = str(r.get("ecu") or "").strip().upper()
            if len(e) >= 2:
                ecu_set.add(e)
    except Exception:
        pass
    try:
        from . import dtc_codes as _dtc
        for u in _dtc.units():
            u2 = str(u).strip().upper()
            if 2 <= len(u2) <= 8 and not u2.isdigit():
                ecu_set.add(u2)
    except Exception:
        pass

    # ── dtc_codes (kanonik) ──
    try:
        from . import dtc_codes as _dtc
        n = 0
        for r in _dtc.rows():
            kode, spn, fmi = r.get("kode"), r.get("spn"), r.get("fmi")
            judul = r.get("label") or r.get("deskripsi") or ""
            if kode:
                buka = f"cari_kode_kesalahan(code='{kode}')"
                ref = str(kode)
            elif spn is not None and fmi is not None:
                buka = f"cari_kode_kesalahan(spn={spn}, fmi={fmi})"
                ref = f"{spn}:{fmi}"
            else:
                continue
            ents = [_k_dtc(kode) if kode else None, _k_spnfmi(spn, fmi)]
            for pn in _PN_TEXT_RE.findall(str(r.get("part") or "").upper()):
                ents.append(_k_pn(pn))
            u = str(r.get("unit") or "").strip().upper()
            if u in ecu_set:
                ents.append(_k_ecu(u))
            for e in ents:
                _emit(acc, e, "dtc_codes", judul, buka, ref)
            n += 1
        cakupan["dtc_codes"] = n
    except Exception:
        logger.exception("links: dtc_codes dilewati")

    # ── manual_teks ──
    try:
        rows = load_json(_SVC / "manual_teks.json.gz")
        for r in rows:
            judul = r.get("judul_id") or r.get("judul") or ""
            if not judul:
                continue
            ref = f"{r.get('sumber')}|{r.get('halaman')}"
            buka = f"cari_manual(topik='{_judul(judul)[:40]}')"
            teks = " ".join([str(judul), " ".join(r.get("kata_kunci") or []),
                             " ".join(str(k) for k in (r.get("kode") or []))])
            _teks_entitas(acc, teks, "manual_teks", judul, buka, ref,
                          model_set, ecu_set)
        cakupan["manual_teks"] = len(rows)
    except Exception:
        logger.exception("links: manual_teks dilewati")

    # ── media: manual_media / wiring / skema (keyed file; entitas dari teks) ──
    for store, path, tool in (
            ("manual_media", s.data_path / "manual_media" / "index.json",
             "diagram_wiring"),
            ("wiring_ref", s.data_path / "wiring" / "index.json",
             "diagram_wiring"),
            ("skema_ref", s.data_path / "manuals" / "skema" / "index.json",
             "diagram_wiring")):
        try:
            rows = load_json(path)
            if isinstance(rows, dict):
                rows = rows.get("items") or rows.get("rows") or []
            n = 0
            for r in rows:
                label = r.get("label") or r.get("deskripsi") or ""
                if not label:
                    continue
                teks = " ".join([str(label), str(r.get("deskripsi") or ""),
                                 " ".join(r.get("sinonim") or []),
                                 str(r.get("seed_teks") or "")])
                _teks_entitas(acc, teks, store, label,
                              f"{tool}(komponen='{_judul(label)[:40]}')",
                              str(r.get("file") or ""), model_set, ecu_set)
                n += 1
            cakupan[store] = n
        except Exception:
            logger.exception("links: %s dilewati", store)

    # ── pin_ecu ──
    try:
        for r in pin_rows:
            ecu = str(r.get("ecu") or "")
            judul = f"Pin {r.get('konektor')}/{r.get('pin')} {r.get('sinyal') or ''} ({ecu})"
            ref = f"{ecu}|{r.get('konektor')}|{r.get('pin')}"
            buka = f"diagram_wiring(komponen='{_judul(str(r.get('sinyal') or ecu))[:40]}')"
            _emit(acc, _k_ecu(ecu), "pin_ecu", judul, buka, ref)
        cakupan["pin_ecu"] = len(pin_rows)
    except Exception:
        logger.exception("links: pin_ecu dilewati")

    # ── jadwal_perawatan ──
    try:
        rows = load_json(_SVC / "jadwal_perawatan.json.gz")
        for r in rows:
            model = str(r.get("model") or "")
            nama = str(r.get("nama") or "")
            jam = r.get("ganti_jam") or []
            judul = f"{nama} — servis {model} tiap {jam[0] if jam else '?'} jam"
            ref = f"{model}|{r.get('varian') or ''}|{r.get('sistem') or ''}|{nama}"
            buka = f"jadwal_perawatan(model='{model}')"
            _emit(acc, _k_model(model), "jadwal_perawatan", judul, buka, ref)
            _emit(acc, _k_pn(r.get("part_number")), "jadwal_perawatan",
                  judul, buka, ref)
        cakupan["jadwal_perawatan"] = len(rows)
    except Exception:
        logger.exception("links: jadwal_perawatan dilewati")

    # ── filter_shantui (PN + cross-reference merek lain) ──
    try:
        rows = load_json(_SVC / "filter_shantui.json.gz")
        for r in rows:
            model = str(r.get("model") or r.get("alat") or "")
            judul = f"{r.get('part_name') or 'Filter'} {model} (cross-ref tersedia)"
            ref = norm_pn(r.get("part_number"))
            buka = f"cari_filter_shantui(unit='{model}')"
            _emit(acc, _k_pn(r.get("part_number")), "filter_shantui", judul, buka, ref)
            for cr in (r.get("cross_reference") or []):
                _emit(acc, _k_pn(cr), "filter_shantui", judul, buka, ref)
            _emit(acc, _k_model(model), "filter_shantui", judul, buka, ref)
        cakupan["filter_shantui"] = len(rows)
    except Exception:
        logger.exception("links: filter_shantui dilewati")

    # ── repairkit (dict model → kit) ──
    try:
        n = 0
        for mdl, ent in (rk or {}).items():
            judul = f"Repair kit transmisi {mdl}"
            buka = f"repair_kit_transmisi(transmisi='{mdl}')"
            _emit(acc, _k_model(mdl), "repairkit", judul, buka, str(mdl))
            for pn in (ent.get("assy_pn") or []):
                _emit(acc, _k_pn(pn), "repairkit", judul, buka, str(mdl))
            for cat in (ent.get("seal_kit") or {}).values():
                for it in (cat or []):
                    _emit(acc, _k_pn((it or {}).get("pn")), "repairkit",
                          judul, buka, str(mdl))
            n += 1
        cakupan["repairkit"] = n
    except Exception:
        logger.exception("links: repairkit dilewati")

    # ── pengetahuan admin (chunk ber-id tak stabil → ref = judul dok|bagian|hal) ──
    try:
        chunks = load_json(s.data_path / "ai_pengetahuan" / "pengetahuan.json")
        if isinstance(chunks, dict):
            chunks = chunks.get("chunks") or chunks.get("rows") or []
        n = 0
        for r in chunks:
            judul = r.get("judul_id") or r.get("judul") or ""
            if not judul:
                continue
            dok = str(r.get("sumber") or "")
            ref = f"{dok}|{r.get('judul') or ''}|{r.get('halaman') or 0}"
            buka = (f"buka_pengetahuan(dokumen='{_judul(dok)[:30]}', "
                    f"bagian='{_judul(str(r.get('judul') or ''))[:30]}')")
            teks = " ".join([str(judul), " ".join(r.get("kata_kunci") or []),
                             " ".join(str(k) for k in (r.get("kode") or []))])
            _teks_entitas(acc, teks, "pengetahuan", judul, buka, ref,
                          model_set, ecu_set,
                          pembeli=bool(r.get("untuk_pembeli")))
            n += 1
        cakupan["pengetahuan"] = n
    except Exception:
        logger.exception("links: pengetahuan dilewati")

    # ── part_taxonomy (Fase C — toleran absen) ──
    try:
        rows = load_json(_SVC / "part_taxonomy.json.gz")
        for r in rows:
            kel = str(r.get("keluarga") or "")
            if not kel:
                continue
            judul = f"Keluarga part: {kel} ({r.get('sistem') or ''})"
            buka = f"info_part(nama='{kel}')"
            for pn in (r.get("contoh_pn") or []):
                _emit(acc, _k_pn(pn), "part_taxonomy", judul, buka, kel)
        cakupan["part_taxonomy"] = len(rows)
    except Exception:
        logger.exception("links: part_taxonomy dilewati")

    # ── edges EPC (Fase B — toleran absen) ──
    try:
        edges = load_json(s.data_path / "ai_belajar" / "part_unit_edges.json.gz",
                          default={})
        n = 0
        for npn, e in (edges or {}).items():
            models = [m for m in (e.get("models") or []) if m][:3]
            judul = ("Dipakai di: " + ", ".join(models)
                     if models else f"Terpasang di {e.get('frames_n') or '?'} unit terpantau")
            buka = "unit_dari_part(pn='" + npn + "')"
            _emit(acc, f"pn:{npn}", "epc_unit", judul, buka, npn)
            for m in models:
                _emit(acc, _k_model(m), "epc_unit",
                      f"{e.get('nama') or npn} dipakai model ini", buka, npn)
            n += 1
        cakupan["epc_unit"] = n
    except Exception:
        logger.exception("links: epc edges dilewati")

    # ── catalog_bom: HANYA utk PN/model yang SUDAH disebut store lain ──
    try:
        import json as _json
        bom_path = s.data_path / "catalog_bom.json"
        if bom_path.exists():
            bom = _json.loads(bom_path.read_text(encoding="utf-8"))
            n = 0
            for uname, u in (bom.get("units") or {}).items():
                for kod, k in (u.get("kategori") or {}).items():
                    assy = (k or {}).get("assy_pn") or ""
                    for p in (k or {}).get("parts") or []:
                        ent = _k_pn(p.get("pn"))
                        if ent and ent in acc:   # sudah disebut store lain
                            _emit(acc, ent, "catalog_bom",
                                  f"{p.get('nama') or p.get('pn')} — katalog {uname[:30]}",
                                  f"detail_part(part_number='{p.get('pn')}')",
                                  f"{uname}|{kod}|{assy}")
                            n += 1
            cakupan["catalog_bom"] = n
    except Exception:
        logger.exception("links: catalog_bom dilewati")

    # ── PEMBATAS: entitas wajib disebut ≥2 store berbeda + cap posting ──
    final: dict[str, list[dict]] = {}
    for ent, posts in acc.items():
        stores = {p["store"] for p in posts}
        if len(stores) < 2:
            continue
        # prioritas: satu posting per store dulu (keragaman), lalu sisanya
        posts.sort(key=lambda p: (_PRIORITAS.index(p["store"])
                                  if p["store"] in _PRIORITAS else 99))
        divers: list[dict] = []
        seen_store: set[str] = set()
        for p in posts:
            if p["store"] not in seen_store:
                divers.append(p)
                seen_store.add(p["store"])
        for p in posts:
            if p not in divers:
                divers.append(p)
        final[ent] = divers[:_MAX_POSTING_PER_ENTITAS]

    cakupan["entitas_tertaut"] = len(final)
    return {"entitas": {k: final[k] for k in sorted(final)},
            "model_kanonik": sorted(model_set),
            "ecu_kanonik": sorted(ecu_set),
            "cakupan": cakupan}


def build_and_save() -> Path:
    """Build + tulis byte-stable. Data sama → byte identik (write_json_gz)."""
    data = build()
    p = _path()
    write_json_gz(p, data)
    logger.info("knowledge_links: %s entitas tertaut → %s",
                (data.get("cakupan") or {}).get("entitas_tertaut"), p)
    return p
