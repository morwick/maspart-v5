# -*- coding: utf-8 -*-
# ai_parts/p1_dasar.py — BAGIAN dari modul app.services.ai_assistant (SATU namespace).
# Dimuat berurutan oleh ai_assistant.py (loader) via exec — sengaja BUKAN
# submodul terpisah: monkeypatch test `ai.<attr>`, relative import, dan
# resolusi global lintas-bagian tetap bekerja persis seperti file tunggal.
# Urutan muat & pembagian: lihat _PARTS di ai_assistant.py.
"""
Asisten AI MASPART — chatbot pintar berbasis DeepSeek (OpenAI-compatible).

Kuncinya: AI diberi **function calling (tools)** yang membaca data LIVE aplikasi
(index part, stok multi-gudang, harga lokal & SIMS, kurs, pesanan, rekap
penjualan). Jadi jawaban tidak mengarang — AI memanggil tool, membaca hasil
aktual, lalu merangkum dalam bahasa Indonesia.

Akses tiap tool discoped sesuai peran user (admin/cabang/pembeli/biasa) supaya
asisten tidak membocorkan data lintas-peran.
"""
from __future__ import annotations

import difflib
import json
import logging
import re
import threading
import time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

import requests

from ..core.config import get_settings
from . import (abs_scr_codes, accurate, ai_chat_log, ai_export, ai_knowledge, ai_sheet, catalog_bom,
               dtc_diagnosa, eol_dtc, epc, epc_bom, epc_weichai, fault_codes, fault_pdf, filter_ref,
               gudang, gudang_config, harga, maintenance_ref, orders, part_index, pin_ecu, populasi,
               repairkit, reservations, search_log, sims, sims_eol, sinonim, wiring_ref)

logger = logging.getLogger("maspart.ai")

_TIMEOUT = 60
_MAX_TOOL_ROUNDS = 8          # batas putaran panggil-tool agar tidak loop;
                              # rantai fallback multi-tool butuh > 6 putaran

_MAX_HISTORY = 16             # batas pesan riwayat yang dikirim balik ke model
_MAX_PART_ROWS = 12           # batas baris hasil pencarian part global (hemat token)
_MAX_PART_ROWS_UNIT = 25      # batas lebih longgar saat difilter ke 1 unit (daftar lengkap)
_MAX_EXPLODED_FIGURES = 6     # batas figure exploded view per panggilan gambar_exploded
                              # (render PNG per-figure + fetch per-gambar di frontend)

# Token GENERIK tunggal (bolt/nut/screw/...) yang membanjiri hasil pencarian bila
# sudah ada kata kunci SPESIFIK (frasa multi-kata atau istilah China). Mis. 'baut
# roda' → buang 'bolt' polos, sisakan 'wheel bolt'/'车轮螺栓' → tepat. Dipakai
# part_aus_dari_rangka & cari_part_di_unit (mode teliti) agar keyword generik tak
# menggusur jawaban sebenarnya.
_GENERIC_KWS = {"bolt", "nut", "screw", "washer", "pin", "ring", "plate", "cover",
                "shaft", "bushing", "gear", "spring", "valve", "pipe", "hose"}


def _tekan_generik(kws: list[str]) -> list[str]:
    """Buang keyword generik tunggal bila ada keyword spesifik (frasa/CJK)."""
    specific = [k for k in kws if (" " in k.strip()) or any(ord(c) > 0x2E80 for c in k)]
    if specific:
        kws = [k for k in kws if k.lower() not in _GENERIC_KWS]
    return list(dict.fromkeys(k for k in kws if k))


class AINotConfigured(RuntimeError):
    pass


# ═══════════════════════════════════════════════════════════════════════
#  DEFINISI TOOLS (skema OpenAI function-calling)
# ═══════════════════════════════════════════════════════════════════════
def _can_sims(user: dict) -> bool:
    """Hanya admin & akun SEE_ALL (mis. 'mas') yang boleh lihat harga SIMS/modal."""
    role = (user.get("role") or "").lower()
    uname = (user.get("username") or "").strip().lower()
    return role == "admin" or uname in gudang.SEE_ALL_ACCOUNTS


def _can_populasi(user: dict) -> bool:
    """Akses data Populasi Unit di asisten — HANYA admin & akun 'mas' (SEE_ALL)."""
    return _can_sims(user)


def _can_orders(user: dict) -> bool:
    """Boleh lihat rekap/daftar pesanan: admin (semua gudang) atau akun cabang
    (otomatis discoped ke gudangnya). User biasa/pembeli TIDAK boleh."""
    role = (user.get("role") or "").lower()
    return role == "admin" or bool(gudang.gudang_for_user(user.get("username", ""), role))


def _is_pembeli(user: dict) -> bool:
    return (user.get("role") or "").lower() == "pembeli"


# Field harga di HASIL TOOL (semua nama yang mungkin dipancarkan handler).
_HARGA_KEYS = ("harga", "harga_lokal", "harga_sims", "harga_jual", "harga_cny",
               "harga_idr", "harga_display", "harga_daftar")


def _boleh_harga(user: dict) -> bool:
    """Boleh melihat HARGA di asisten — SATU sumber kebenaran, mengikuti Menu Control.

    Dulu asisten memakai gudang.can_see_price (admin/'mas' saja) & hanya dicek 2-3
    tool → sisanya membocorkan harga lintas-peran. Kini: admin selalu; pembeli boleh
    (harga jual = yang ia bayar, tampil juga di /toko); staf mengikuti izin kolom
    'col_harga' dari Menu Control (halaman Cari Part/detail sudah pakai izin yang
    SAMA — jadi asisten & halaman konsisten)."""
    role = (user.get("role") or "").lower()
    if role in ("admin", "pembeli"):
        return True
    try:
        from . import permissions
        return "col_harga" in permissions.effective("column", user.get("username", ""), role)
    except Exception:
        return False


def _rp(n) -> str:
    """Format angka → 'Rp 112.000' (ribuan pakai titik). '—' bila None/invalid."""
    try:
        return "Rp " + f"{int(n):,}".replace(",", ".")
    except (TypeError, ValueError):
        return "—"


def _strip_harga(obj):
    """Buang SEMUA field harga dari hasil tool (rekursif: dict & list). Penjaga
    TERPUSAT — dijalankan di _run_tool bila user tak berhak, jadi tak bergantung
    tiap handler ingat mengecek izin."""
    if isinstance(obj, dict):
        for k in list(obj.keys()):
            if k in _HARGA_KEYS:
                obj.pop(k, None)
            else:
                _strip_harga(obj[k])
    elif isinstance(obj, list):
        for it in obj:
            _strip_harga(it)
    return obj


def _is_admin(user: dict) -> bool:
    return (user.get("role") or "").lower() == "admin"


def _boleh_isi_stok_harga(args: dict, user: dict) -> bool:
    """Katalog: stok & harga hanya DIISI bila ADMIN yang secara eksplisit meminta.
    User non-admin TIDAK pernah bisa (walau model mengirim flag-nya)."""
    return _is_admin(user) and bool(args.get("sertakan_stok_harga"))


def _auto_exploded_gambar(rangka: str, pn: str, source: str,
                          kategori: str) -> tuple[list[dict], list[dict], str]:
    """BEST-EFFORT: metadata kartu GAMBAR exploded view untuk sebuah PN per-VIN —
    dipakai agar tiap 'cek part' langsung disertai gambar di bawah jawaban.
    source: 'weichai' (mesin) | 'sinotruk' (Parts Atlas).
    Return (gambar, daftar_balon, nama_figure): gambar=kartu inline; daftar_balon=
    SELURUH balon→part figure pertama (KONTEKS agar asisten bisa jawab follow-up
    'cek no N' tanpa fetch ulang); nama_figure. ([],[],'') bila gagal/kategori kosong.
    Tidak pernah melempar (jawaban utama tak boleh gagal gara-gara gambar)."""
    # ⛔ DIMATIKAN (2026-07-08, permintaan pemilik): kartu thumbnail exploded/foto
    # tidak dipakai lagi. Jangan lakukan catalog_walk (hemat EPC) — kembalikan kosong.
    return [], [], ""
    if not (rangka and pn and kategori):  # noqa: unreachable — sengaja dinonaktifkan
        return [], [], ""
    try:
        if source == "weichai":
            ex = epc_weichai.exploded_figures(rangka, pn, kategori)
        else:
            ex = epc_bom.exploded_figures(rangka, pn, kategori)
        if not ex.get("found"):
            return [], [], ""
        figs = ex.get("figures") or []
        out: list[dict] = []
        for f in figs[:_MAX_EXPLODED_FIGURES]:
            builder = {"kind": "exploded", "svg": f["svg"], "balon": f.get("balon")}
            if source == "weichai":
                builder["source"] = "weichai"
                builder["rangka"] = rangka
            image_id, filename = ai_export.stash_builder(
                f"Exploded {pn} - {f.get('nama') or ''}", builder, ext="png")
            out.append({"image_id": image_id, "filename": filename, "pn": pn,
                        "balon": f.get("balon"), "nama_figure": f.get("nama"),
                        "kategori": f.get("kategori"), "jumlah_item": f.get("jumlah_item")})
        # Daftar balon→part figure PERTAMA (yg memuat PN) — konteks utk 'cek no N'.
        daftar_balon = [{"balon": it.get("balon"), "pn": it.get("pn"), "nama": it.get("nama")}
                        for it in ((figs[0].get("items_ringkas") if figs else []) or [])][:40]
        return out, daftar_balon, (figs[0].get("nama") if figs else "") or ""
    except Exception:
        logger.exception("auto exploded gagal (dilewati) pn=%s source=%s", pn, source)
        return [], [], ""


def _sino_exploded_kat(modules: tuple, posisi: str | None) -> str:
    """Domain modul Atlas (part_aus) → nama KATEGORI katalog untuk mencari figure.
    Kosong utk kasus multi-domain (mis. 'filter' menyebar) agar tak walk berat."""
    if modules == ("FDJ", "FDJFJ"):
        return "mesin"
    if modules == ("LHQ",):
        return "kopling"
    if modules == ("BSX",):
        return "transmisi"
    if modules == ("CDQ", "QDQ"):
        return ("gardan depan" if posisi == "depan"
                else "gardan belakang" if posisi == "belakang" else "")
    return ""


def _hide_gudang_for_buyer(result: dict, user: dict) -> dict:
    """Sembunyikan rincian stok ANTAR-CABANG dari akun pembeli. Pembeli hanya
    berhak melihat total & stok tergudang miliknya (lewat jalur web terscope),
    BUKAN enumerasi kuantitas tiap cabang. Dipakai di semua tool yang bisa
    memuat 'stok_per_gudang' (detail_part, cari_part, stok_accurate). Mutasi
    di tempat + kembalikan result agar enak dirangkai."""
    if _is_pembeli(user):
        result.pop("stok_per_gudang", None)
    return result


# Lookup sinonim TERPUSAT di services/sinonim.py (rombakan 2026-07-17).
# Wrapper tipis di bawah mempertahankan nama lama — puluhan test monkeypatch
# `ai._load_sinonim_entries` / `ai._expand_query` / `ai._umbrella_keywords`,
# dan wrapper meneruskan provider entries SAAT CALL-TIME supaya patch tembus.
_UMBRELLA_KATEGORI = sinonim.UMBRELLA_KATEGORI


def _load_sinonim_entries() -> list:
    """Entri data/sinonim/sinonim.json (cache per-mtime — editan admin langsung
    terpakai tanpa restart). Delegasi ke sinonim.entries()."""
    return sinonim.entries()


def _sinonim_block() -> str:
    """Kamus istilah lapangan (Indonesia → kata kunci nama part Inggris) untuk prompt."""
    return sinonim.block(_load_sinonim_entries)


def _expand_query(q: str) -> tuple[list[str], list[str]]:
    """Perluas query dgn keyword sinonim bila mengandung istilah lapangan.
    Return (daftar istilah cari [termasuk q asli], daftar trigger yang cocok)."""
    return sinonim.expand_query(q, _load_sinonim_entries)


def _umbrella_keywords(kata_kunci: str) -> list[str]:
    """Keyword payung kategori ('kopling' → seluruh keluarga sub-part kopling).
    Delegasi ke sinonim.umbrella_keywords()."""
    return sinonim.umbrella_keywords(kata_kunci, _load_sinonim_entries)


# ── Blok PENGETAHUAN DOMAIN dari file (rombakan 3a 2026-07-17) ───────
# Dulu literal 28,5rb chars inline di _system_prompt; kini ai_domain.md DI
# SEBELAH modul ini (ikut git + push.sh backend/app — satu paket dgn kode,
# BUKAN data/ yang di-scp terpisah: deploy kode & prompt selalu sinkron).
# mtime-cache = byte-identik antar-panggilan (WAJIB utk prompt-cache DeepSeek);
# file berubah hanya saat deploy/edit → satu cache-miss, sama spt edit kode.
_DOMAIN_CACHE: dict = {"mtime": None, "text": ""}
_DOMAIN_FILE = Path(__file__).parent / "ai_domain.md"


def _domain_block() -> str:
    try:
        p = _DOMAIN_FILE
        mt = p.stat().st_mtime if p.exists() else None
        if mt is None:
            logger.error("ai_domain.md tidak ada — blok domain kosong")
            return ""
        if _DOMAIN_CACHE["mtime"] != mt:
            # normalisasi CRLF→LF: byte-stable lintas checkout Windows/Linux
            _DOMAIN_CACHE["text"] = ("\n\n" + p.read_text(encoding="utf-8")
                                     .replace("\r\n", "\n").rstrip())
            _DOMAIN_CACHE["mtime"] = mt
        return _DOMAIN_CACHE["text"]
    except Exception:
        logger.exception("gagal memuat ai_domain.md")
        return ""


def _kamus_subset_block(messages: list[dict]) -> str:
    """[KAMUS ISTILAH GILIRAN INI] — SUBSET kamus sinonim yang trigger-nya
    muncul di ≤6 pesan user terakhir. Rombakan 3a: kamus penuh (21,5rb chars)
    DIHAPUS dari prompt statik; pencarian tak terpengaruh (semua jalur cari
    sudah ekspansi server-side via sinonim.expand_query) — subset ini hanya
    membantu model memilih kata query & menjelaskan padanan Inggris ke user.
    Disuntik sbg pesan system DINAMIS di ekor (zona bebas prompt-cache)."""
    teks = " ".join(
        str((m or {}).get("content") or "") for m in (messages or [])[-6:]
        if (m or {}).get("role") == "user")
    if not teks:
        return ""
    lines: list[str] = []
    seen: set[str] = set()
    for e in _load_sinonim_entries():
        grup = e.get("grup") or ""
        if grup in seen:
            continue
        t = next((t for t in (e.get("triggers") or []) if t and sinonim.hit(t, teks)), None)
        if t:
            seen.add(grup)
            trig = ", ".join(dict.fromkeys(x for x in (e.get("triggers") or []) if x))
            kw = ", ".join(dict.fromkeys(k for k in (e.get("keywords") or []) if k))
            if trig and kw:
                lines.append(f"- {trig} → {kw}")
        if len(lines) >= 12:  # pagar ukuran (±1–2rb chars)
            break
    if not lines:
        return ""
    return ("[KAMUS ISTILAH GILIRAN INI] (Indonesia → kata kunci katalog "
            "Inggris; tool cari sudah otomatis memakainya):\n" + "\n".join(lines))


def _norm_gudang(nama: str) -> str:
    """Nama gudang → basis untuk cocok: buang prefix 'NN.' + spasi + lowercase
    ('04.Palembang' → 'palembang', '25. PT BJM' → 'pt bjm')."""
    return re.sub(r"^\s*\d+\s*\.\s*", "", (nama or "")).strip().lower()


def _gudang_list() -> list[str]:
    """Daftar nama gudang KANONIK dari KONFIGURASI (gudang_config; format 'NN.Nama')
    — bukan dari stok.xlsx. Dipakai resolusi & saran gudang tersedia."""
    try:
        return list(gudang_config.coords_map().keys())
    except Exception:
        return []


def _resolve_gudang(nama: str) -> str | None:
    """Cocokkan nama gudang bebas dari user (mis. 'palembang', 'jakarta') ke nama
    gudang KANONIK config (mis. '04.Palembang'). Prefix 'NN.' diabaikan; case-
    insensitive; cocok bila salah satu memuat yang lain. None bila tak dikenal."""
    want = _norm_gudang(nama)
    if not want:
        return None
    for g in _gudang_list():
        base = _norm_gudang(g)
        if want == base or want in base or (len(base) >= 3 and base in want):
            return g
    return None


def _stok_lokal_rows(terms: list[str], exclude_pns: set[str],
                     limit: int = 6) -> list[dict]:
    """Barang STOK GUDANG (indeks Accurate bersama) yang cocok kata kunci — jalur
    satu-satunya menemukan barang aftermarket/lokal yang TIDAK ada di katalog
    Sinotruk (kasus nyata log: 'Alternator Regulator', 'Kaca Spion LH', 'Cucuk
    Per Depan Faw'). Non-blocking (baca cache indeks, tanpa tarikan) & non-fatal.
    `exclude_pns` = PN hasil katalog (ternormalisasi) agar tak dobel."""
    try:
        hits = accurate.search_index(terms, limit=limit + len(exclude_pns))
    except Exception:
        return []
    rows: list[dict] = []
    for h in hits:
        pn = (h.get("pn") or "").strip().upper()
        if accurate.norm_pn(pn) in exclude_pns:
            continue
        stok = h.get("available_to_sell")
        row = {
            "part_number": pn or (h.get("no") or ""),
            "part_name": h.get("name") or "",
            "stok_total": (f"{stok:.0f} {h.get('unit') or ''}".strip()
                           if stok is not None else "—"),
            "sumber": "Stok gudang (Accurate) — di luar katalog Sinotruk",
        }
        if h.get("price"):
            row["harga_lokal"] = "Rp " + f"{int(h['price']):,}".replace(",", ".")
        rows.append(row)
        if len(rows) >= limit:
            break
    return rows


# ── Posisi part dari NAMA (deterministik — jangan serahkan ke model) ─────────
# 'spion kanan' dulu diputuskan model dgn membaca RH/LH sendiri → rawan salah.
# Sekarang Python yang menandai posisi tiap part; model tinggal menyajikan.
_POSISI_PAT: list[tuple[str, re.Pattern]] = [
    ("kanan",    re.compile(r"(?<![A-Z])(RH|R\.H\.?|RIGHT)(?![A-Z])")),
    ("kiri",     re.compile(r"(?<![A-Z])(LH|L\.H\.?|LEFT)(?![A-Z])")),
    ("depan",    re.compile(r"(?<![A-Z])(FRONT|FRT)(?![A-Z])")),
    ("belakang", re.compile(r"(?<![A-Z])(REAR)(?![A-Z])")),
    ("atas",     re.compile(r"(?<![A-Z])(UPPER|UPR)(?![A-Z])")),
    ("bawah",    re.compile(r"(?<![A-Z])(LOWER|LWR)(?![A-Z])")),
]
_POSISI_CN = [("kanan", "右"), ("kiri", "左"), ("depan", "前"), ("belakang", "后"),
              ("atas", "上"), ("bawah", "下")]


def _parse_posisi(nama_en: str | None, nama_cn: str | None = None) -> list[str]:
    """Deteksi posisi (kanan/kiri/depan/belakang/atas/bawah) dari nama part:
    penanda Inggris (RH/LH/RIGHT/LEFT/FRONT/REAR/UPPER/LOWER) lebih dipercaya;
    bila nama Inggris tak memberi apa-apa, jatuh ke karakter China (右/左/前/后)."""
    tags: list[str] = []
    # 'REAR VIEW MIRROR' / '后视镜': kata rear/后 di situ bagian NAMA BENDA
    # (kaca spion), bukan posisi pemasangan → buang dulu sebelum scan.
    up = (nama_en or "").upper().replace("REARVIEW", " ").replace("REAR VIEW", " ")
    for tag, pat in _POSISI_PAT:
        if pat.search(up):
            tags.append(tag)
    if not tags and nama_cn:
        cn = nama_cn.replace("后视", "")
        for tag, ch in _POSISI_CN:
            if ch in cn:
                tags.append(tag)
    return tags


def _bom_mungkin_maksud(parts: list[dict], local: dict, terms: list[str],
                        limit: int = 5) -> list[dict]:
    """Saran fuzzy 'mungkin maksud' KHUSUS isi Loading List unit ini: part yang
    namanya paling mirip dgn kata query (toleran typo/ejaan). Dipakai saat
    kata_kunci 0 hasil supaya tool tidak menjawab kosong polos."""
    kws = [w for t in terms for w in (t or "").upper().split()
           if len(w) >= 4 and not any(c.isdigit() for c in w)]
    if not kws:
        return []
    best: dict[str, tuple[float, dict]] = {}
    for p in parts:
        nama = (local.get(p["pn"], {}).get("part_name") or p.get("nama_cn") or "")
        up = " ".join(nama.upper().replace("-", " ").split())
        if not up:
            continue
        score = max((difflib.SequenceMatcher(None, k, w).ratio()
                     for w in up.split() for k in kws), default=0.0)
        if score >= 0.72:
            cur = best.get(p["pn"])
            if not cur or score > cur[0]:
                best[p["pn"]] = (score, {"part_number": p["pn"],
                                         "nama": " ".join(nama.split()),
                                         "skor": round(score, 2)})
    rows = sorted(best.values(), key=lambda t: -t[0])
    return [r[1] for r in rows[:limit]]


_DISC_RE = re.compile(r"\bdisc\b", re.IGNORECASE)
_DISK_RE = re.compile(r"\bdisk\b", re.IGNORECASE)


def _spelling_variants(terms: list[str]) -> list[str]:
    """Tambah varian ejaan ganda yang umum di katalog terjemahan: disc↔disk.
    Katalog menulis 'driven disc' DAN 'driven disk' tak konsisten — ini membuat
    pencarian kebal beda ejaan tanpa perlu daftar sinonim manual per kasus.
    Pakai batas-kata agar tidak mengubah kata lain (mis. 'discharge')."""
    out = list(terms)
    seen = {t.lower() for t in out}
    for t in terms:
        for rx, repl in ((_DISC_RE, "disk"), (_DISK_RE, "disc")):
            if rx.search(t):
                v = rx.sub(repl, t)
                if v.lower() not in seen:
                    seen.add(v.lower())
                    out.append(v)
    return out


_HW_GEARBOX_RE = re.compile(r"^HW\d{4,6}[A-Z]")
_GEARBOX_TERMS = ("transmisi", "persneling", "perseneling", "persneleng",
                  "gearbox", "girboks", "bak gigi", "gear box")

# Kata kunci UMUM yang sendirian terlalu luas (mis. ekspansi 'seal kruk as' →
# 'seal' mencocokkan ribuan part). Kecocokan HANYA pada kata umum tunggal ini
# dianggap LEMAH saat menghitung 'jumlah_relevan_kuat' — agar angka yang
# dilaporkan ke user jujur (bukan total ekor panjang yang menyesatkan).
_GENERIC_KW = {
    "seal", "oil seal", "bolt", "nut", "washer", "screw", "valve", "spring",
    "hose", "pipe", "gasket", "ring", "pin", "gear", "cover", "plate",
    "bearing", "shaft", "filter", "switch", "sensor", "cap", "plug",
    "bracket", "clamp", "bushing", "wheel", "joint", "housing", "rod",
}

# Kata struktural/arah/pengisi yang DIBUANG saat BROADEN dalam-unit (fallback
# cari kata inti) — biar tak menyeret seluruh katalog unit (mis. 'assembly',
# 'left', 'front'). Kata BENDA inti (door/handle/glass/lock/brake…) tetap dicari.
_BROADEN_STOP = {
    "for", "and", "the", "with", "sub", "type", "and/or", "assy", "assembly",
    "group", "set", "kit", "part", "parts", "left", "right", "upper", "lower",
    "front", "rear", "inner", "outer", "mounted", "central",
    "untuk", "dan", "dari", "yang", "kiri", "kanan", "depan", "belakang",
    "atas", "bawah", "dalam", "luar", "tengah",
}


def _is_gearbox_assy(pn: str, name: str) -> bool:
    """True bila part ini UNIT TRANSMISI/GEARBOX utuh (bukan sub-part): PN berpola
    gearbox HOWO (HW#####<huruf>…), ATAU nama China 变速器/变速箱, ATAU 'GEARBOX' /
    'Gear Box Assembly' (Shantui/Wechai), ATAU PN terdaftar sbg assy di repair kit
    (menangkap Fast `FZ…`, ZF `WG…`, & HOWO `HW19710…` tanpa huruf yang lolos pola)."""
    pu = (pn or "").upper()
    nu = (name or "").upper()
    if _HW_GEARBOX_RE.match(pu):
        return True
    if "变速器" in (name or "") or "变速箱" in (name or ""):
        return True
    if "GEAR BOX ASSEMBLY" in nu or nu.strip() in ("GEARBOX", "REDUCTION GEARBOX"):
        return True
    # Sumber kebenaran kurasi: PN terdaftar sebagai gearbox assy di transmisi.json.
    try:
        if re.sub(r"[\s_\-/]", "", pu) in repairkit.all_assy_pns():
            return True
    except Exception:
        pass
    return False


def _is_gearbox_query(q: str) -> bool:
    """True bila user memang menanyakan TRANSMISI/GEARBOX itu sendiri (bukan sekadar
    sub-part transmisi). Dipakai untuk menaikkan ranking gearbox assy ke atas."""
    ql = (q or "").lower()
    if any(w in ql for w in _GEARBOX_TERMS):
        return True
    if "变速器" in (q or "") or "变速箱" in (q or ""):
        return True
    return bool(_HW_GEARBOX_RE.match((q or "").upper().replace(" ", "")))


