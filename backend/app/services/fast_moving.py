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
                "pipe", "hose", "flange", "gasket", "seat wrench",
                # Aksesori sekitar filter yang bukan barang aus (keluhan pemilik
                # 2026-08-06: daftar filter terisi kabel & saklar, sementara
                # filter servisnya sendiri tenggelam).
                "filter seat", "filter shell", "filter cover", "end cover",
                "extension cord", "wiring harness", "switch", "sensor",
                "indicator", "connector", "filter mesh", "casing", "alarm",
                "suction filter"],
    "kategori": {
        # ⚠️ Nama EPC harfiah: 'fuel coarse filter' TIDAK memuat 'fuel filter',
        # dan CN 粗滤器 bukan 滤清器 — tanpa kata-kata ini filter solar kasar /
        # water separator hilang dari daftar (keluhan pemilik 2026-08-06).
        "filter": ["filter element", "oil filter", "fuel filter", "air filter",
                   "coarse filter", "fine filter", "water separator",
                   "oil-water separator", "urea filter", "adblue filter",
                   "filter insert", "cartridge", "滤清器", "滤芯", "粗滤器",
                   "精滤器", "机油滤", "柴油滤", "空滤器", "油水分离器",
                   "尿素滤", "干燥筒", "干燥罐"],
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
        # Aksesori LISTRIK yang menempel di mesin. Untuk unit bermesin Weichai
        # keduanya TIDAK ADA di pohon Atlas (sama seperti element filter mesin)
        # — ikut ditambal dari EPC Weichai, lihat _lengkapi_mesin.
        "kelistrikan_mesin": ["alternator", "generator", "starter motor",
                              "start motor", "发电机", "起动机", "启动机"],
    },
    # Nama LAPANGAN Indonesia (lihat _istilah). Aturan BERURUT: yang khusus
    # dulu ('fuel coarse filter' sebelum 'fuel filter'), umum belakangan.
    "istilah": [
        {"id": "filter water separator (solar bawah)",
         "kata": ["oil-water separator", "water separator", "油水分离器"]},
        {"id": "filter solar kasar (bawah)",
         "kata": ["fuel coarse filter", "coarse filter element", "燃油粗滤"]},
        {"id": "filter solar halus (atas)",
         "kata": ["fuel filter", "diesel filter", "燃油滤清器", "柴油滤"]},
        {"id": "filter oli mesin",
         "kata": ["engine oil filter", "oil filter element", "机油滤"]},
        {"id": "filter udara — elemen utama",
         "kata": ["main filter element", "主滤芯"]},
        {"id": "filter udara — elemen safety",
         "kata": ["safety filter element", "安全滤芯"]},
        {"id": "filter udara (rumah/assembly)",
         "kata": ["air filter", "空滤器"]},
        {"id": "filter urea / AdBlue",
         "kata": ["urea filter", "adblue", "尿素滤"]},
        {"id": "filter oli power steering",
         "kata": ["steering oil tank filter", "转向油罐滤", "转向油滤"]},
        {"id": "tabung air dryer (pengering angin)",
         "kata": ["air drying", "air dryer", "干燥筒", "干燥罐"]},
        {"id": "filter oli (transmisi/hidrolik)",
         "kata": ["oil filter", "油滤器"]},
        {"id": "kampas rem",
         "kata": ["brake lining", "brake friction", "摩擦片", "刹车片"]},
        {"id": "sepatu rem", "kata": ["brake shoe", "制动蹄"]},
        {"id": "cakram rem", "kata": ["brake disc", "制动盘"]},
        {"id": "tromol rem", "kata": ["brake drum", "制动鼓"]},
        {"id": "kampas kopling",
         "kata": ["clutch disc", "clutch driven", "离合器片", "从动盘"]},
        {"id": "matahari/dekrup kopling", "kata": ["pressure plate", "压盘"]},
        {"id": "drek lahar (release bearing)",
         "kata": ["release bearing", "分离轴承"]},
        {"id": "bearing roda",
         "kata": ["hub bearing", "wheel bearing", "轮毂轴承"]},
        {"id": "seal oli", "kata": ["oil seal", "油封"]},
        {"id": "v-belt / tali kipas",
         "kata": ["v-belt", "fan belt", "drive belt", "poly v", "皮带"]},
        {"id": "karet support/mounting",
         "kata": ["rubber mount", "rubber support", "rubber bushing",
                  "rubber bearing", "cab mount", "橡胶支座", "缓冲块"]},
        {"id": "motor starter (dinamo starter)",
         "kata": ["starter motor", "start motor", "起动机", "启动机"]},
        {"id": "alternator (dinamo ampere)",
         "kata": ["alternator", "generator", "发电机"]},
    ],
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
        # File data boleh hanya menimpa sebagian: tanpa 'istilah' sendiri, nama
        # lapangan JANGAN ikut hilang (editor kamus tak wajib tahu bagian itu).
        return d if d.get("istilah") else {**d, "istilah": _KAMUS_DEFAULT["istilah"]}
    return _KAMUS_DEFAULT


def hp_dari_model(model: str) -> int | None:
    """⚠️ PERKIRAAN, dan TERBUKTI BISA MELESET: ZZ3317V486JB1R terbaca 480 HP
    padahal mesin unitnya WP12.400E201 = 400 HP (temuan pemilik 2026-08-06).
    Dua digit itu ternyata tak selalu tenaga. Pakai HANYA untuk pencocokan label
    pasaran ('NX400'); untuk MENYEBUT tenaga ke user, pakai `hp_dari_mesin` atas
    kode mesin EPC unit ybs."""
    m = _HP_RE.match(str(model or "").upper().replace(" ", ""))
    return int(m.group(1)) * 10 if m else None


# Kode mesin Weichai/Sinotruk: WP12.400E201 → 400 HP; MC11.42 → 420; MT13.54 →
# 540. Tiga digit = tenaga apa adanya, dua digit = ×10.
_HP_MESIN_RE = re.compile(r"\b(?:WP|MC|MT|WD|YC|ISM|SC)\d{1,2}[A-Z]?\.(\d{2,3})")


def hp_dari_mesin(kode: str) -> int | None:
    """Tenaga (HP) dari KODE MESIN EPC — sumber yang SAH untuk menyebut tenaga
    sebuah unit. None bila kodenya tak dikenali (⛔ jangan diganti tebakan)."""
    m = _HP_MESIN_RE.search(str(kode or "").upper())
    if not m:
        return None
    n = int(m.group(1))
    return n if n >= 100 else n * 10


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


def urutan_istilah() -> dict[str, int]:
    """{nama lapangan → urutan TAMPIL}. Sengaja terpisah dari urutan LIST kamus:
    list diurut menurut prioritas COCOK (khusus dulu — 'fuel coarse filter'
    sebelum 'fuel filter'), sedangkan yang dilihat user harus urut kepentingan
    servis (filter oli → solar → udara → … → air dryer) lewat field 'urut'.
    Dibaca TOOL saat menyajikan, jadi mengubah kamus langsung terasa TANPA
    membangun ulang dataset."""
    out: dict[str, int] = {}
    for i, a in enumerate(_kamus().get("istilah") or []):
        if a.get("id"):
            try:
                out[a["id"]] = int(a.get("urut", i))
            except (TypeError, ValueError):
                out[a["id"]] = i
    return out


def peta_populasi() -> dict[str, dict]:
    """NOMOR RANGKA (8 char akhir) → {model, jenis, tahun} dari populasi.

    Dipakai TOOL asisten (fast moving per nomor rangka) sebagai JARING kedua
    setelah EPC getVehicleConfig: EPC otoritatif untuk unitnya sendiri, populasi
    menolong unit yang EPC-nya sedang tak menjawab / tak mengenalnya."""
    return _peta_populasi()


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


def _istilah(nama_en: str, nama_cn: str, kamus: dict) -> str:
    """Nama LAPANGAN Indonesia untuk satu baris ('filter oli mesin', 'filter
    solar halus (atas)', 'kampas rem') — '' bila tak ada aturan yang cocok.

    Ada karena nama EPC harfiah tak dikenali orang bengkel: 'Oil filter element
    component', 'Fuel filter element With O-ring', 'Filter cartridge (Shanghai
    Yida)' — pemilik membaca daftar itu lalu menyimpulkan filter oli & filter
    solarnya TIDAK ADA (2026-08-06), padahal ada. Aturan berurut: yang pertama
    cocok dipakai (khusus dulu, umum belakangan)."""
    hay_en = f" {(nama_en or '').lower()} "
    for aturan in kamus.get("istilah") or []:
        for k in aturan.get("kata") or []:
            if _CJK_RE.search(k):
                if k in (nama_cn or ""):
                    return aturan.get("id") or ""
            elif k in hay_en:
                return aturan.get("id") or ""
    return ""


def _slot_key(nama_en: str, nama_cn: str) -> str:
    """Nama slot fungsi ternormalisasi (buang 'assembly', rapikan spasi)."""
    s = _SLOT_BUANG_RE.sub(" ", (nama_en or "").lower())
    s = " ".join(s.split())
    return s or " ".join((nama_cn or "").split())


# PN suffix varian ('/1', '/2') = part SAMA (aturan bisnis yang juga dipegang
# accurate.index_key) — dilebur ke PN dasar; suffix aslinya disimpan di pn_sub.
_SUFFIX_RE = re.compile(r"/\d{1,2}$")


# ── Pelengkap MESIN WEICHAI ─────────────────────────────────────────────────
# Katalog EPC Sinotruk BERHENTI di batas mesin: untuk unit bermesin Weichai,
# element filter oli & solar mesin TIDAK ADA di pohon Atlas — mereka hanya hidup
# di EPC Weichai. Akibatnya daftar fast moving bolong justru di part yang paling
# sering diganti (terukur 2026-08-07: 17 dari 42 model tanpa slot filter oli
# mesin sama sekali). `_t_filter_unit` sudah lama menambal ini per-unit lewat
# `_weichai_filter_fallback`; di sini tambalannya dipasang di HULU (builder)
# supaya dataset per-model ikut lengkap.
#
# Dibayar SEKALI saat build harian, bukan tiap kali user bertanya: jembatan SSO
# Weichai panggilan pertamanya lambat. Hasilnya di-cache per RANGKA di disk —
# BOM mesin praktis statis, jadi build besok tak menembak jaringan lagi.
# PEMICU: filter OLI MESIN adalah sinyal paling andal "part sisi mesin hilang".
# `_t_filter_unit` menuntut oli DAN solar sama-sama hilang, tapi ukuran nyata
# dataset (2026-08-07) menunjukkan itu terlalu ketat di level model: 17 model
# tanpa filter oli mesin, hanya 6 yang juga tanpa filter solar — filter solar
# kerap ADA di pohon Sinotruk karena terpasang di sasis, sedangkan filter olinya
# di sisi mesin. Memakai syarat DAN membuat 11 model tetap bolong.
_ID_MESIN_PEMICU = {"filter oli mesin"}
# PANEN: daftar TERSENDIRI, sengaja tidak diturunkan dari _ID_MESIN_PEMICU —
# mempersempit pemicu tak boleh ikut mempersempit apa yang dipanen. Begitu
# bridge terbuka, sekalian ambil filter solar & aksesori listrik mesin:
# mahalnya ada di panggilan pertama, bukan di jumlah baris yang dibaca.
_ID_MESIN_AMBIL = {"filter oli mesin", "filter solar halus (atas)",
                   "filter solar kasar (bawah)", "filter water separator (solar bawah)",
                   "alternator (dinamo ampere)", "motor starter (dinamo starter)"}

# Bagian filter yang BUKAN barang habis pakai — di BOM Weichai mereka berdiri
# sebagai baris sendiri ('Fuel Filter Seat' 27×, 'Oil Filter Seat' 15×,
# 'Fuel Filter Bracket' 12×, 'Oil Filter Base Gasket' 9×) dan akan mengotori
# daftar fast moving kalau ikut terbawa.
_WC_BUKAN_ELEMENT = ("seat", "bracket", "base", "gasket", "cover", "housing",
                     "cap", "support", "clamp", "bolt", "screw", "pipe", "hose")


def _wc_istilah(nama: str, kamus: dict) -> str:
    """Istilah lapangan untuk satu baris EPC MESIN Weichai.

    Konteksnya sudah PASTI mesin, dan itu mengubah arti: 'Oil Filter' polos di
    sini adalah filter oli MESIN, sedangkan kamus umum menjatuhkannya ke 'filter
    oli (transmisi/hidrolik)' (aturan mesin menuntut kata 'engine'/'element').
    Terukur 2026-08-07: gara-gara itu 17 model tetap tanpa filter oli mesin
    meski bridge Weichai sudah terbuka."""
    low = " ".join((nama or "").lower().split())
    if any(n in low for n in _WC_BUKAN_ELEMENT):
        return ""
    idl = _istilah(nama, nama, kamus)
    if idl in _ID_MESIN_AMBIL:
        return idl                       # kamus sudah yakin (halus/kasar/dsb)
    if "oil filter" in low:
        return "filter oli mesin"
    if "fuel" in low and "filter" in low:
        return "filter solar halus (atas)"
    return ""
_WC_TERMS = ["filter", "滤芯", "alternator", "generator", "发电机",
             "starter", "起动机", "启动机"]
_WC_SAMPEL_MAKS = 2          # unit sampel yang dicoba per model
_WC_TTL = 30 * 24 * 3600.0   # sukses: BOM mesin statis
_WC_TTL_GAGAL = 6 * 3600.0   # gagal: jangan menghantam bridge tiap build
_WC_AKTIF = True             # kill-switch (di-monkeypatch test)


def _wc_cache_path() -> Path:
    return _dir_out() / "weichai_mesin.json.gz"


def _wc_ambil(frame: str, cache: dict, stat: Counter) -> list[dict]:
    """Baris filter mesin Weichai untuk satu rangka — lewat cache disk.
    [] bila unit ini bukan bermesin Weichai / bridge gagal (fail-open:
    kegagalan TIDAK boleh menjatuhkan build seluruh dataset)."""
    import time as _t

    e = cache.get(frame)
    if e:
        umur = _t.time() - float(e.get("at") or 0)
        # 'bukan unit Weichai' sama statisnya dengan BOM-nya → TTL panjang.
        # Hanya kegagalan INFRA (jaringan/sesi) yang dicoba lagi cepat; kalau
        # tidak, mesin Sinotruk (MC/MT) akan dihantam tiap build harian.
        ttl = _WC_TTL_GAGAL if e.get("err") not in (None, "not_found") else _WC_TTL
        if umur <= ttl:
            stat["cache"] += 1
            return list(e.get("rows") or [])
    try:
        from . import epc_weichai
        res = epc_weichai.find_parts(frame, _WC_TERMS) or {}
    except Exception:
        logger.exception("fast_moving: EPC Weichai gagal utk %s", frame)
        res = {"_err": "exception"}
    if res.get("_err") or not res.get("found"):
        # BUKAN unit Weichai, atau bridge/sesi bermasalah — dua-duanya tak ada
        # yang bisa ditambahkan. Dibedakan hanya untuk TTL cache & telemetri.
        stat["gagal" if res.get("_err") else "bukan_weichai"] += 1
        cache[frame] = {"at": _t.time(), "err": res.get("_err") or "not_found",
                        "rows": []}
        return []
    rows = [{"pn": str(h.get("pn") or "").strip().upper(),
             "nama": " ".join(str(h.get("nama") or "").split()),
             "group": " ".join(str(h.get("group") or "").split())}
            for h in (res.get("hasil") or []) if (h or {}).get("pn")]
    stat["ambil"] += 1
    cache[frame] = {"at": _t.time(), "rows": rows}
    return rows


def _lengkapi_mesin(per_model: dict, kamus: dict) -> Counter:
    """Tambahkan baris filter mesin Weichai ke model yang pohon Sinotruk-nya
    tak memuatnya. Memutasi `per_model` di tempat; return telemetri."""
    stat: Counter = Counter()
    if not _WC_AKTIF or not per_model:
        return stat
    cache = load_json(_wc_cache_path(), default={}) or {}
    for model, m in sorted(per_model.items()):
        punya = {r.get("id") for rows in m["unit"].values() for r in rows}
        if _ID_MESIN_PEMICU & punya:
            continue                       # pohon Sinotruk sudah lengkap
        stat["model_bolong"] += 1
        for frame in list(m["unit"])[:_WC_SAMPEL_MAKS]:
            tambah = 0
            for h in _wc_ambil(frame, cache, stat):
                kat = _klasifikasi(h["nama"], h["nama"], kamus)
                if not kat:
                    continue
                idl = _wc_istilah(h["nama"], kamus)
                if idl not in _ID_MESIN_AMBIL:
                    continue               # hanya tutup lubang MESIN-nya
                pn = h["pn"]
                m["unit"][frame].append({
                    "kat": kat, "slot": _slot_key(h["nama"], h["nama"]),
                    "id": idl, "pn": _SUFFIX_RE.sub("", pn), "pn_asli": pn,
                    "nama": h["nama"], "nama_cn": h["nama"], "qty": None,
                    "assembly": h["group"], "pengganti": [], "wc": True,
                })
                tambah += 1
            if tambah:
                stat["baris"] += tambah
                stat["model_ditambal"] += 1
                break                      # satu unit sampel sudah cukup
    try:
        write_json_gz(_wc_cache_path(), cache)
    except Exception:  # pragma: no cover — cache gagal tulis ≠ build gagal
        logger.exception("fast_moving: gagal menyimpan cache Weichai")
    return stat


def build() -> dict:
    """Bangun data/fast_moving/fast_moving.json.gz dari cache EPC + populasi.
    Return ringkasan {model_n, slot_n, unit_dipakai, unit_tanpa_populasi}."""
    d_items = get_settings().data_path / "epc_unit_items"
    peta = _peta_populasi()
    kamus = _kamus()

    # populasi per model (SEMUA unit, bukan hanya yang ter-cache)
    pop_model: Counter = Counter(v["model"] for v in peta.values())

    # model → {frame → baris terklasifikasi} (agregasi ditunda: perlu lihat
    # SEMUA unit dulu untuk membedakan varian vs multi-posisi)
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
            "jenis": Counter(), "unit": {}, "tahun": {}})
        m["jenis"][info["jenis"]] += 1
        m["tahun"][frame] = info["tahun"]
        baris = m["unit"].setdefault(frame, [])
        for row in rows:
            pn = str(row.get("pn") or "").strip().upper()
            if not pn:
                continue
            nama = " ".join(str(row.get("nama") or "").split())
            nama_cn = " ".join(str(row.get("nama_cn") or "").split())
            kat = _klasifikasi(nama, nama_cn, kamus)
            if not kat:
                continue
            baris.append({
                "kat": kat, "slot": _slot_key(nama, nama_cn),
                "id": _istilah(nama, nama_cn, kamus),
                "pn": _SUFFIX_RE.sub("", pn), "pn_asli": pn,
                "nama": nama, "nama_cn": nama_cn, "qty": row.get("qty"),
                "assembly": " ".join(str((row.get("dari_assembly") or {})
                                         .get("nama") or "").split()),
                "pengganti": [str((g or {}).get("pn") or "").strip().upper()
                              for g in (row.get("pengganti") or [])
                              if (g or {}).get("pn")],
            })

    # Tambal lubang MESIN sebelum agregasi: unit bermesin Weichai tak punya
    # element filter oli/solar di pohon Sinotruk (lihat _lengkapi_mesin).
    stat_wc = _lengkapi_mesin(per_model, kamus)

    # bentuk final (deterministik: sort model, slot, varian)
    final_model: dict[str, dict] = {}
    slot_n = 0
    for model, m in sorted(per_model.items()):
        frames = m["unit"]
        n_sampel = len(frames)
        # Nama generik ("oil seal") bisa memayungi BEBERAPA POSISI berbeda:
        # bila SATU unit saja memuat >1 PN dasar utk (kategori, slot) yang sama,
        # itu multi-posisi (bukan varian antar unit) → pecah per assembly induk
        # supaya porsi n_unit tiap slot tetap jujur.
        multi: Counter = Counter()
        for rows in frames.values():
            per_slot: dict = {}
            for r in rows:
                per_slot.setdefault((r["kat"], r["slot"]), set()).add(r["pn"])
            for k, pns in per_slot.items():
                multi[k] = max(multi[k], len(pns))

        def _key(r: dict) -> tuple:
            if multi[(r["kat"], r["slot"])] > 1 and r["assembly"]:
                return (r["kat"], f"{r['slot']} — {r['assembly'].lower()}")
            return (r["kat"], r["slot"])

        # Sisa multi-PN SETELAH dipecah per assembly (mis. sepatu rem kiri+kanan
        # dalam satu assembly) = KO-EKSIS: keduanya terpasang bersamaan, bukan
        # varian pilihan → ditandai agar penyaji tidak menyuruh memilih.
        ko: Counter = Counter()
        for rows in frames.values():
            per_k: dict = {}
            for r in rows:
                per_k.setdefault(_key(r), set()).add(r["pn"])
            for k, pns in per_k.items():
                ko[k] = max(ko[k], len(pns))

        agg: dict = {}
        nama_id: dict = {}           # slot → Counter(istilah lapangan)
        for frame, rows in frames.items():
            terlihat: set = set()    # (slot, pn) unik per unit — qty>1 ≠ 2 unit
            for r in rows:
                k = _key(r)
                if r.get("id"):
                    nama_id.setdefault(k, Counter())[r["id"]] += 1
                if (k, r["pn"]) in terlihat:
                    continue
                terlihat.add((k, r["pn"]))
                v = agg.setdefault(k, {}).setdefault(r["pn"], {
                    "pn": r["pn"], "nama": r["nama"], "nama_cn": r["nama_cn"],
                    "qty": r["qty"], "n_unit": 0, "tahun": set(),
                    "pn_sub": set(), "pengganti": [],
                    # Asal katalog: baris dari EPC Weichai TIDAK boleh tampak
                    # seolah datang dari pohon Sinotruk unit itu.
                    **({"sumber": "EPC Weichai"} if r.get("wc") else {})})
                v["n_unit"] += 1
                if r["pn_asli"] != r["pn"]:
                    v["pn_sub"].add(r["pn_asli"])
                th = m["tahun"].get(frame)
                if th:
                    v["tahun"].add(th)
                for gpn in r["pengganti"]:
                    if gpn not in v["pengganti"]:
                        v["pengganti"].append(gpn)

        slots = []
        for (kat, nama_slot), varian in sorted(agg.items()):
            vs = sorted(varian.values(), key=lambda v: (-v["n_unit"], v["pn"]))
            c_id = nama_id.get((kat, nama_slot))
            slots.append({
                "kategori": kat, "slot": nama_slot,
                # Istilah lapangan (bila ada aturan yang cocok): dipakai ASISTEN
                # sebagai judul baris, nama EPC jadi keterangan.
                **({"nama_id": sorted(c_id.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]}
                   if c_id else {}),
                **({"ko_eksis": True} if ko[(kat, nama_slot)] > 1 else {}),
                # Slot yang SELURUH variannya dari EPC Weichai: penyaji wajib
                # bisa menyebut asalnya (part mesin memang tak ada di Atlas).
                **({"sumber": "EPC Weichai"}
                   if vs and all(v.get("sumber") == "EPC Weichai" for v in vs) else {}),
                "varian": [{**v, "tahun": sorted(v["tahun"]),
                            "pn_sub": sorted(v["pn_sub"])} for v in vs],
            })
            slot_n += 1
        jenis = m["jenis"].most_common(1)[0][0] if m["jenis"] else ""
        final_model[model] = {
            "jenis": jenis, "hp": hp_dari_model(model),
            "unit_populasi": pop_model.get(model, 0),
            "unit_sampel": sorted(frames),
            "n_sampel": n_sampel, "slot": slots,
        }

    write_json_gz(_dir_out() / "fast_moving.json.gz",
                  {"versi": 1, "model": final_model})
    ringkas = {"model_n": len(final_model), "slot_n": slot_n,
               "unit_dipakai": unit_dipakai, "unit_tanpa_populasi": unit_tanpa_pop,
               "mesin_weichai": dict(stat_wc)}
    logger.info("fast_moving: build %s", ringkas)
    return ringkas


def data() -> dict:
    """Dataset terbangun (cache per-mtime via load_json). {} bila belum ada."""
    d = load_json(_dir_out() / "fast_moving.json.gz", default=None)
    return d if isinstance(d, dict) else {}
