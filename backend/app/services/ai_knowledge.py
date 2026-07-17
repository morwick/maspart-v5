"""
Pengetahuan ter-DERIVE dari DATA FAKTA untuk Asisten AI — bukan hafalan model.

Masalah yang ditutup: model sering "merasa tahu" arti sebuah pola Part Number
atau isi data, lalu mengarang. Modul ini MENGHITUNG pengetahuan itu langsung
dari data nyata di disk (katalog EPC per-model `catalog_bom.json`, stok
terindeks, konfigurasi gudang) menjadi `data/ai_knowledge.json`, lalu
menyajikannya sebagai blok ringkas di system prompt. Semua angka/label di blok
bisa ditunjuk asalnya di data — tidak ada karangan.

Pakai:
    python tools/build_ai_knowledge.py     # bangun ulang data/ai_knowledge.json
    ai_knowledge.knowledge_block(user)     # blok prompt (cache per-mtime file)

Blok sengaja TANPA Part Number contoh utuh (hanya PREFIX pendek) — PN utuh di
prompt terbukti dipapagalkan model sebagai jawaban (lihat memori proyek).
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from ..core.config import get_settings

logger = logging.getLogger("maspart.ai_knowledge")

_KNOWLEDGE_FILE = "ai_knowledge.json"

# Kata nama part yang terlalu generik untuk mencirikan sebuah keluarga prefix.
_STOPWORDS = {
    "ASSEMBLY", "ASSY", "WITH", "AND", "FOR", "THE", "TYPE", "PART",
    "LEFT", "RIGHT", "FRONT", "REAR", "UPPER", "LOWER",
}

# Prefix huruf di awal PN ('WG', 'AZ', 'VG', 'HW', 'Q') ATAU pola digit+huruf
# khas SITRAK/berbasis angka ('810W', '712W').
_ALPHA_RE = re.compile(r"^([A-Z]{1,4})(?=\d)")
_DIGIT_RE = re.compile(r"^(\d{3}[A-Z])")
_SUB_RE = re.compile(r"^([A-Z]{1,3}\d{4})")


def _prefix_of(pn: str) -> str | None:
    pn = (pn or "").strip().upper()
    m = _ALPHA_RE.match(pn)
    if m:
        return m.group(1)
    m = _DIGIT_RE.match(pn)
    if m:
        return m.group(1)
    return None


def _knowledge_path() -> Path:
    return get_settings().data_path / _KNOWLEDGE_FILE


# ═══════════════════════════════════════════════════════════════════════
#  MINER — hitung fakta dari data di disk
# ═══════════════════════════════════════════════════════════════════════
def build(min_sub_count: int = 60, min_sub_share: float = 0.55,
          top_alpha: int = 12, top_sub: int = 26, top_words: int = 4) -> dict:
    """Bangun struktur pengetahuan dari catalog_bom.json + gudang_config.json.
    Deterministik & offline (tanpa jaringan). Return dict siap disimpan."""
    s = get_settings()
    bom_path = s.data_path / "catalog_bom.json"
    bom = json.loads(bom_path.read_text(encoding="utf-8"))
    kat_label: dict[str, str] = bom.get("kategori") or {}

    # Satu PN bisa muncul di banyak unit/kategori — hitung per PN UNIK per
    # kategori (set), supaya unit yang variannya banyak tidak mendominasi.
    pn_kat: dict[str, set[str]] = {}
    pn_nama: dict[str, str] = {}
    for u in (bom.get("units") or {}).values():
        for kod, k in (u.get("kategori") or {}).items():
            for p in k.get("parts") or []:
                pn = (p.get("pn") or "").strip().upper()
                if not pn:
                    continue
                pn_kat.setdefault(pn, set()).add(kod)
                if pn not in pn_nama and p.get("nama"):
                    pn_nama[pn] = str(p["nama"])

    alpha_cnt: Counter[str] = Counter()
    alpha_kat: dict[str, Counter] = {}
    alpha_words: dict[str, Counter] = {}
    sub_cnt: Counter[str] = Counter()
    sub_kat: dict[str, Counter] = {}
    sub_words: dict[str, Counter] = {}

    for pn, kats in pn_kat.items():
        pref = _prefix_of(pn)
        if not pref:
            continue
        alpha_cnt[pref] += 1
        ak = alpha_kat.setdefault(pref, Counter())
        for kod in kats:
            ak[kod] += 1
        words = [w for w in re.split(r"[^A-Z]+", (pn_nama.get(pn) or "").upper())
                 if len(w) >= 3 and w not in _STOPWORDS]
        aw = alpha_words.setdefault(pref, Counter())
        aw.update(dict.fromkeys(words, 1))
        m = _SUB_RE.match(pn)
        if m:
            sub = m.group(1)
            sub_cnt[sub] += 1
            sk = sub_kat.setdefault(sub, Counter())
            for kod in kats:
                sk[kod] += 1
            sw = sub_words.setdefault(sub, Counter())
            sw.update(dict.fromkeys(words, 1))

    def _kat_top(c: Counter, n: int = 3) -> list[list]:
        tot = sum(c.values()) or 1
        return [[kat_label.get(k, k), round(v * 100.0 / tot)] for k, v in c.most_common(n)]

    prefixes = [
        {
            "prefix": p,
            "jumlah_pn": n,
            "kategori_top": _kat_top(alpha_kat[p]),
            "kata_nama_top": [w for w, _ in alpha_words[p].most_common(top_words)],
        }
        for p, n in alpha_cnt.most_common(top_alpha)
    ]

    sub_rows = []
    for sub, n in sub_cnt.most_common():
        if n < min_sub_count or len(sub_rows) >= top_sub:
            continue
        kc = sub_kat[sub]
        dom, domn = kc.most_common(1)[0]
        if domn / (sum(kc.values()) or 1) < min_sub_share:
            continue
        sub_rows.append({
            "prefix": sub,
            "jumlah_pn": n,
            "kategori_dominan": kat_label.get(dom, dom),
            "kata_nama_top": [w for w, _ in sub_words[sub].most_common(top_words)],
        })

    # ── Peta model ↔ awalan rangka (VIN) + keluarga mesin per unit ──
    # Ditambang dari NAMA unit katalog ('HOWO MAX 480 6X4 (LZZ1CLWB)' → awalan
    # VIN di kurung), path file ('ZZ4257V344KF1_…' → kode sasis), dan prefix PN
    # dominan kategori 02 Mesin/Powertrain (MQ→MC MAN; VG→WD615/D10/D12;
    # 61…→Weichai WD/WP). Nilai: asisten mengenali model+jenis mesin dari rangka
    # → routing tool Sinotruk vs Weichai lebih akurat (mode gagal produksi 33%).
    _ENG_FAMILY = (
        ("MQ", "mesin MC (Sinotruk-MAN, MC11/MC13)"),
        ("202V", "mesin MC (Sinotruk-MAN)"),
        ("080V", "mesin MC (Sinotruk-MAN)"),
        ("VG", "mesin Sinotruk WD615/D10/D12"),
        ("61", "mesin Weichai WD/WP"),
        ("1000", "mesin Weichai WP seri baru"),
    )
    unit_vin: list[dict] = []
    for uname, u in (bom.get("units") or {}).items():
        mvin = re.search(r"\(([A-Z0-9]{6,10})\)\s*$", uname or "")
        msas = re.search(r"(ZZ[A-Z0-9]{6,})", u.get("file") or "")
        eng_cnt: Counter[str] = Counter()
        for p in ((u.get("kategori") or {}).get("02") or {}).get("parts") or []:
            pnu = (p.get("pn") or "").strip().upper()
            for pref, _lbl in _ENG_FAMILY:
                if pnu.startswith(pref):
                    eng_cnt[pref] += 1
                    break
        mesin = ""
        if eng_cnt:
            pref, n = eng_cnt.most_common(1)[0]
            if n >= 5:  # sinyal cukup — banyak unit hanya 1-2 part di kategori 02
                mesin = dict(_ENG_FAMILY)[pref]
        unit_vin.append({
            "model": re.sub(r"\s*\([A-Z0-9]{6,10}\)\s*$", "", uname or "").strip(),
            "vin_prefix": mvin.group(1) if mvin else "",
            "sasis": msas.group(1) if msas else "",
            "mesin": mesin,
        })
    unit_vin.sort(key=lambda r: (r["model"], r["vin_prefix"]))

    # Gudang kanonik dari konfigurasi (sumber yang sama dengan gudang_config
    # .coords_map(): kunci 'coords' = nama gudang 'NN.Nama') — bukan tebakan.
    gudang: list[str] = []
    try:
        from . import gudang_config as _gc
        gudang = sorted(_gc.coords_map().keys())
    except Exception:
        pass

    # ── Fakta tambahan dari sumber RESMI lain di disk (best-effort per sumber;
    #    file absen → bagian itu dilewati, sisanya tetap dibangun) ──
    # Kode kesalahan ECU (fault_codes.json — dokumen resmi Bosch/Sinotruk).
    fault_n = 0
    try:
        from . import fault_codes as _fcd
        fault_n = int(_fcd.count() or 0)
    except Exception:
        pass
    # Unit Shantui yang PUNYA data cross-reference filter (data/manuals).
    filter_units: list[str] = []
    try:
        from . import filter_ref as _fr
        if _fr.available():
            filter_units = list(_fr.units())
    except Exception:
        pass
    # Kode error EOL CNHTC (semua unit kontrol; Indonesia + langkah perbaikan)
    # + diagram wiring.
    eol_n = 0
    eol_units_n = 0
    wiring_n = 0
    try:
        from . import eol_dtc as _eol
        eol_n = int(_eol.count() or 0)
        eol_units_n = len(_eol.units())
    except Exception:
        pass
    try:
        from . import wiring_ref as _wr
        wiring_n = int(_wr.count() or 0)
    except Exception:
        pass
    fault_pdf_n = 0
    try:
        from . import fault_pdf as _fp
        fault_pdf_n = int(_fp.count() or 0)
    except Exception:
        pass
    # Model Shantui yang PUNYA jadwal perawatan berkala (data/manuals),
    # dikelompokkan per jenis alat (dozer/loader/excavator/grader/roller).
    maintenance_by_jenis: dict[str, list[str]] = {}
    try:
        from . import maintenance_ref as _mr
        if _mr.available():
            maintenance_by_jenis = _mr.models_by_jenis()
    except Exception:
        pass
    # Model gearbox yang PUNYA data repair kit (transmisi.json — kurasi resmi).
    gearbox_models: list[dict] = []
    try:
        from . import repairkit as _rk
        if _rk.available():
            for m in _rk.list_models():
                gearbox_models.append({"model": m.get("model"),
                                       "tipe": m.get("tipe") or "",
                                       "jumlah_unit": len(m.get("unit") or [])})
    except Exception:
        pass

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "sumber": ["catalog_bom.json (EPC Sinotruk per-model)", "gudang_config.json",
                   "fault_codes.json (DTC resmi)", "filter Shantui (data/manuals)",
                   "jadwal perawatan Shantui (data/manuals)",
                   "transmisi.json (repair kit gearbox)"],
        "cakupan": {
            "unit_katalog_bom": len(bom.get("units") or {}),
            "pn_unik_bom": len(pn_kat),
        },
        "prefix_pn": prefixes,
        "sub_prefix_pn": sub_rows,
        "unit_vin": unit_vin,
        "gudang": gudang,
        "fault_codes": {"jumlah": fault_n, "jumlah_eol": eol_n,
                        "unit_kontrol_eol": eol_units_n, "wiring": wiring_n,
                        "pdf_diagnosa": fault_pdf_n},
        "filter_shantui_units": filter_units,
        "maintenance_shantui": maintenance_by_jenis,
        "gearbox_repairkit": gearbox_models,
    }


def build_and_save(**kw) -> Path:
    data = build(**kw)
    p = _knowledge_path()
    p.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    _CACHE["mtime"] = None  # paksa muat ulang pada pemanggilan berikutnya
    return p


# ═══════════════════════════════════════════════════════════════════════
#  LOADER + RENDERER blok prompt (cache per-mtime — prompt tetap stabil)
# ═══════════════════════════════════════════════════════════════════════
_CACHE: dict = {"mtime": None, "data": None, "block": "", "block_no_gudang": ""}

# Auto-rebuild: bila catalog_bom.json lebih baru dari ai_knowledge.json, bangun
# ulang di THREAD LATAR (blok lama tetap disajikan sampai selesai). Debounce
# lewat flag in-progress + cooldown agar tak dibangun berkali-kali / hammer saat
# gagal. Prompt-cache tetap aman: blok role-invariant → 1 cache-miss per update
# katalog (sama seperti rebuild manual `tools/build_ai_knowledge.py`).
_REBUILD_LOCK = threading.Lock()
_REBUILD: dict = {"on": False, "last": 0.0}
_REBUILD_COOLDOWN_SEC = 30.0


def _catalog_bom_mtime() -> float:
    try:
        return (get_settings().data_path / "catalog_bom.json").stat().st_mtime
    except OSError:
        return 0.0


def _maybe_rebuild_async(knowledge_mtime: float) -> None:
    """Picu rebuild latar bila catalog_bom.json > ai_knowledge.json (deterministik,
    offline). No-op bila belum berubah / rebuild sedang jalan / masih cooldown."""
    bom_mt = _catalog_bom_mtime()
    if not bom_mt or bom_mt <= knowledge_mtime:
        return
    with _REBUILD_LOCK:
        if _REBUILD["on"] or (time.monotonic() - _REBUILD["last"]) < _REBUILD_COOLDOWN_SEC:
            return
        _REBUILD["on"] = True
        _REBUILD["last"] = time.monotonic()

    def _run() -> None:
        try:
            build_and_save()
            logger.info("[ai_knowledge] blok pengetahuan dibangun ulang (catalog_bom.json berubah)")
        except Exception:
            logger.exception("[ai_knowledge] rebuild otomatis gagal")
        finally:
            _REBUILD["on"] = False

    threading.Thread(target=_run, daemon=True, name="ai-knowledge-rebuild").start()


def _load() -> dict | None:
    try:
        p = _knowledge_path()
        if not p.exists():
            return None
        mt = p.stat().st_mtime
        if _CACHE["mtime"] != mt:
            _CACHE["data"] = json.loads(p.read_text(encoding="utf-8"))
            _CACHE["mtime"] = mt
            _CACHE["block"] = _render(_CACHE["data"], with_gudang=True)
            _CACHE["block_no_gudang"] = _render(_CACHE["data"], with_gudang=False)
        _maybe_rebuild_async(mt)      # bangun ulang bila katalog sumber lebih baru
        return _CACHE["data"]
    except Exception:
        logger.exception("gagal memuat %s", _KNOWLEDGE_FILE)
        return None


def _render(d: dict, with_gudang: bool) -> str:
    lines: list[str] = [
        "\n\nPENGETAHUAN DARI DATA NYATA (dihitung otomatis dari katalog EPC "
        "per-model — fakta, bukan hafalan; angka = jumlah PN unik di katalog):",
        "• POLA PREFIX PART NUMBER — pakai untuk MENGENALI keluarga part saat "
        "user menyebut PN (memilih tool/kategori yang tepat), BUKAN untuk "
        "mengarang PN. PN di jawaban tetap WAJIB dari hasil tool:",
    ]
    for r in d.get("prefix_pn") or []:
        kt = ", ".join(f"{k} {v}%" for k, v in (r.get("kategori_top") or [])[:3])
        kw = ", ".join((r.get("kata_nama_top") or [])[:4])
        lines.append(f"  - {r['prefix']}… ({r['jumlah_pn']} PN): {kt}"
                     + (f" — nama umum: {kw}" if kw else ""))
    subs = d.get("sub_prefix_pn") or []
    if subs:
        lines.append("• SUB-KELUARGA (prefix+4 digit, kategori dominan):")
        for r in subs:
            kw = ", ".join((r.get("kata_nama_top") or [])[:3])
            lines.append(f"  - {r['prefix']}… → {r['kategori_dominan']}"
                         + (f" ({kw})" if kw else ""))
    uv = [r for r in (d.get("unit_vin") or []) if r.get("vin_prefix")]
    if uv:
        lines.append(
            "• PETA MODEL ↔ AWALAN RANGKA (VIN) — kenali model & jenis mesin dari "
            "awalan nomor rangka user; part per-unit TETAP WAJIB via tool EPC "
            "ber-rangka (jangan menebak dari peta ini):")
        for r in uv:
            ekstra = "; ".join(x for x in (r.get("sasis"), r.get("mesin")) if x)
            lines.append(f"  - {r['vin_prefix']} → {r['model']}"
                         + (f" ({ekstra})" if ekstra else ""))
    if with_gudang and (d.get("gudang") or []):
        lines.append(
            "• GUDANG/CABANG RESMI (untuk memahami sebutan user spt 'jkt', "
            "'plg', 'mks' — cocokkan longgar ke nama ini): "
            + ", ".join(d["gudang"]))
    fcd = d.get("fault_codes") or {}
    fc = fcd.get("jumlah") or 0
    if fc:
        eol_n = fcd.get("jumlah_eol") or 0
        ekstra = (f" + database EOL CNHTC {eol_n} kode SEMUA unit kontrol "
                  f"({fcd.get('unit_kontrol_eol') or 0} ECU: mesin/ABS-ESP/transmisi/"
                  "EV/BCM/airbag/radar/SCR) BERBAHASA INDONESIA lengkap penyebab + "
                  "LANGKAH PERBAIKAN" if eol_n else "")
        pdfn = fcd.get("pdf_diagnosa") or 0
        pdfx = (f"; plus {pdfn} LEMBAR DIAGNOSA PDF resmi per-pasangan SPN/FMI "
                "(terlampir otomatis sebagai kartu yang bisa dibuka user)" if pdfn else "")
        lines.append(
            f"• KODE KESALAHAN (DTC): database resmi memuat {fc} entri SPN/FMI/kode P"
            f"{ekstra}{pdfx} — pertanyaan kode error/fault code WAJIB via "
            "cari_kode_kesalahan, jangan dari ingatan.")
        wr = fcd.get("wiring") or 0
        if wr:
            lines.append(
                f"• DIAGRAM WIRING: {wr} diagram pin/kabel sensor-aktuator mesin Bosch & "
                "SCR/AdBlue tersedia — permintaan skema kabel/pin/konektor WAJIB via "
                "diagram_wiring (gambar tampil inline).")
    fu = d.get("filter_shantui_units") or []
    if fu:
        lines.append(
            "• FILTER SHANTUI: data cross-reference filter (Fleetguard/Donaldson/Sakura/dll) "
            "TERSEDIA untuk unit: " + ", ".join(fu) +
            " — pertanyaan filter unit-unit ini WAJIB via cari_filter_shantui.")
    mbj = d.get("maintenance_shantui") or {}
    if mbj:
        per = "; ".join(f"{jn}: {', '.join(ms)}" for jn, ms in mbj.items() if ms)
        lines.append(
            "• JADWAL PERAWATAN BERKALA Shantui (part yang diganti tiap interval jam "
            "servis 50–3000 + PN Shantui + qty) TERSEDIA per jenis alat — " + per +
            ". Pertanyaan 'part apa diganti saat servis X jam / jadwal servis berkala' "
            "model-model ini WAJIB via jadwal_perawatan (isi 'jenis' & 'jam' bila disebut).")
    gm = d.get("gearbox_repairkit") or []
    if gm:
        daftar = ", ".join(
            f"{r.get('model')}" + (f" ({r['tipe']})" if r.get("tipe") else "")
            for r in gm)
        lines.append(
            "• MODEL GEARBOX ber-DATA REPAIR KIT — daftar ini HANYA untuk MENGENALI "
            "kode seri transmisi: " + daftar +
            ". ⛔ JANGAN menjawab pertanyaan repair kit / 'model X ada tidak' langsung "
            "dari daftar ini — WAJIB tetap panggil repair_kit_transmisi (verifikasi + "
            "isi kit). Unit di luar daftar tetap bisa punya gearbox — cek cari_part/EPC, "
            "jangan simpulkan 'tidak punya'.")
    cak = d.get("cakupan") or {}
    if cak:
        lines.append(
            f"• CAKUPAN: katalog BOM per-model memuat {cak.get('unit_katalog_bom', '?')} "
            f"unit dan ±{cak.get('pn_unik_bom', '?')} PN unik. Di luar itu masih ada "
            "katalog Excel unit lain, stok Accurate (termasuk barang lokal/aftermarket), "
            "EPC per-VIN, SIMS — jadi 'tidak ada di sini' ≠ 'tidak ada sama sekali': "
            "cek tool yang sesuai dulu.")
    return "\n".join(lines)


def knowledge_block(user: dict | None = None) -> str:
    """Blok prompt pengetahuan. Daftar gudang DISEMBUNYIKAN untuk role pembeli
    (konsisten dengan scoping stok per-gudang). '' bila file belum dibangun."""
    if _load() is None:
        return ""
    pembeli = ((user or {}).get("role") or "").lower() == "pembeli"
    return _CACHE["block_no_gudang"] if pembeli else _CACHE["block"]
