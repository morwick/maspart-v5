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
from . import (abs_scr_codes, accurate, ai_belajar, ai_chat_log, ai_export, ai_knowledge,
               ai_session, ai_sheet,
               catalog_bom, dtc_codes,
               dtc_diagnosa, eol_dtc, epc, epc_bom, epc_manual, epc_shantui, epc_weichai,
               exploded_view,
               fault_codes, fault_pdf, filter_crossref, filter_ref,
               gudang, gudang_config, harga, jadwal_servis_truk, kapasitas_gardan,
               knowledge_links,
               maintenance_ref, maksud, manual_media,
               manual_teks, orders, part_index, part_taxonomy, pengetahuan,
               pengetahuan_index, permintaan_tak_terlayani, pin_ecu, populasi,
               rak, repairkit, reservations, search_log, sims, sims_eol, sims_warranty, sinonim,
               skema_ref, telematics, warranty_kasus, weichai_replace, wiring_ref)

logger = logging.getLogger("maspart.ai")

_TIMEOUT = 60
_MAX_TOOL_ROUNDS = 8          # batas putaran panggil-tool agar tidak loop;
                              # rantai fallback multi-tool butuh > 6 putaran

_MAX_HISTORY = 16             # batas pesan riwayat yang dikirim balik ke model
_KAMUS_SUBSET_MAX = 16        # baris [KAMUS ISTILAH GILIRAN INI] (±1,5–2,5rb chars);
                              # 12 terlalu sempit utk pertanyaan multi-part
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
    """Harga SIMS/modal: admin & akun SEE_ALL ('mas') SELALU; staf lain bila
    diberi centang 'ai_harga_sims' di Menu Control tab Asisten AI (grant = OR,
    centang MEMBERI dan tak pernah mencabut dari admin/'mas')."""
    role = (user.get("role") or "").lower()
    uname = (user.get("username") or "").strip().lower()
    if role == "admin" or uname in gudang.SEE_ALL_ACCOUNTS:
        return True
    return _boleh_ai(user, "ai_harga_sims")


def _can_populasi(user: dict) -> bool:
    """Data Populasi Unit: admin & 'mas' selalu; staf lain bila diberi centang
    'ai_populasi' (tak lagi alias _can_sims — grant-nya key terpisah)."""
    role = (user.get("role") or "").lower()
    uname = (user.get("username") or "").strip().lower()
    if role == "admin" or uname in gudang.SEE_ALL_ACCOUNTS:
        return True
    return _boleh_ai(user, "ai_populasi")


def _can_orders(user: dict) -> bool:
    """Boleh lihat rekap/daftar pesanan: admin (semua gudang) atau akun cabang
    (otomatis discoped ke gudangnya). User biasa/pembeli TIDAK boleh."""
    role = (user.get("role") or "").lower()
    return role == "admin" or bool(gudang.gudang_for_user(user.get("username", ""), role))


def _is_pembeli(user: dict) -> bool:
    return (user.get("role") or "").lower() == "pembeli"


# Gerbang & strip Harga/Stok kini hidup di services/permissions.py (dipakai juga
# router non-AI /api/parts, /api/harga, /api/stok — gerbang SATU untuk semua).
# Alias underscore dipertahankan agar pemakai internal (p2/p3/p5/p6/p7/p9) dan
# test lama tetap bekerja tanpa disentuh.
from .permissions import (HARGA_KEYS as _HARGA_KEYS, STOK_KEYS as _STOK_KEYS,
                          boleh_ai as _boleh_ai,
                          boleh_harga as _boleh_harga, boleh_stok as _boleh_stok,
                          strip_harga as _strip_harga, strip_stok as _strip_stok)


def _rp(n) -> str:
    """Format angka → 'Rp 112.000' (ribuan pakai titik). '—' bila None/invalid."""
    try:
        return "Rp " + f"{int(n):,}".replace(",", ".")
    except (TypeError, ValueError):
        return "—"


def _is_admin(user: dict) -> bool:
    return (user.get("role") or "").lower() == "admin"


# Kemampuan elevated per akun (Menu Control tab Asisten AI) — pola OR:
# admin selalu; staf lain bila key-nya dicentang. Fail-closed via _boleh_ai.
def _can_penawaran(user: dict) -> bool:
    return _is_admin(user) or _boleh_ai(user, "ai_penawaran")


def _can_stok_admin(user: dict) -> bool:
    return _is_admin(user) or _boleh_ai(user, "ai_stok_admin")


def _can_pesanan_bermasalah(user: dict) -> bool:
    return _is_admin(user) or _boleh_ai(user, "ai_pesanan_bermasalah")


def _can_garansi(user: dict) -> bool:
    """Garansi & klaim SIMS (cek_garansi/riwayat_klaim/detail_klaim): admin
    selalu; staf bila dicentang 'ai_garansi'; pembeli TIDAK PERNAH (boleh_ai
    fail-closed) — data klaim memuat nama+HP pelapor & nilai modal CNY."""
    return _is_admin(user) or _boleh_ai(user, "ai_garansi")


def _can_mengajar(user: dict) -> bool:
    """Mengajari pengetahuan lewat chat (tool ajarkan_pengetahuan): admin selalu;
    staf bila dicentang 'ai_mengajar'; pembeli TIDAK PERNAH (boleh_ai fail-closed).
    Beda dari gerbang lain yang menjaga BACA data sensitif — yang dijaga di sini
    adalah MENULIS ke pengetahuan yang nanti dibaca semua orang."""
    return _is_admin(user) or _boleh_ai(user, "ai_mengajar")


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
        # Lokasi rak = peta isi gudang internal. Ia menyebut nama cabang satu per
        # satu persis seperti stok_per_gudang, jadi tanpa baris ini penyembunyian
        # di atas cuma pindah kebocoran ke field sebelahnya.
        result.pop("rak_gudang", None)
    return result


def _rak_untuk(pn: str, user: dict) -> dict:
    """{label gudang → 'A-12'} untuk disisipkan ke hasil tool. {} untuk pembeli.

    Foto kartu stok SENGAJA tidak diikutkan: model tak bisa melihat gambar di
    jalur ini dan URL-nya cuma membakar token — foto urusan UI. Best-effort:
    rak yang gagal dibaca tak boleh menjatuhkan info stok/harga."""
    if _is_pembeli(user):
        return {}
    try:
        data = rak.get_for_pn(pn)
    except Exception:
        return {}
    out: dict[str, str] = {}
    for label, row in (data or {}).items():
        teks = (row.get("rak") or "").strip()
        if not teks:
            continue
        cat = (row.get("catatan") or "").strip()
        out[label] = f"{teks} (catatan: {cat})" if cat else teks
    return out


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


# ── Blok ROUTING PER-VIN, dinamis (2026-08-09) ──────────────────────
# Dulu bagian dari ai_domain.md → ikut SETIAP panggilan API, padahal aturannya
# (part_aus_dari_rangka vs bom_dari_rangka vs uraikan_assembly, filter_unit,
# banding_rangka, katalog_kategori) tak berguna tanpa nomor rangka. Diukur di
# produksi: beban TETAP 44.400 token per panggilan × sampai 8 ronde = 480.084
# token dalam satu giliran; 67% waktu giliran habis di model, bukan tool.
# Sekarang: prompt statik tetap byte-identik (prompt-cache utuh) dan blok ini
# disuntik sbg pesan system di EKOR — zona dinamis, sama spt kamus & rute.
_PERVIN_CACHE: dict = {"mtime": None, "text": ""}
_PERVIN_FILE = Path(__file__).parent / "ai_domain_pervin.md"


def _pervin_block(messages: list[dict] | None = None) -> str:
    """[ROUTING EPC PER-VIN] — hanya bila percakapan menyinggung nomor rangka.

    Gerbangnya SATU fungsi dengan gerbang spec tool (_konteks_rangka) supaya
    aturan & alat tak pernah timpang: mustahil aturan per-VIN muncul tanpa
    tool-nya, atau sebaliknya. Pemurah — lihat catatan di _konteks_rangka.
    """
    if not _konteks_rangka(messages):
        return ""
    try:
        p = _PERVIN_FILE
        mt = p.stat().st_mtime if p.exists() else None
        if mt is None:
            logger.error("ai_domain_pervin.md tidak ada — blok per-VIN kosong")
            return ""
        if _PERVIN_CACHE["mtime"] != mt:
            _PERVIN_CACHE["text"] = (p.read_text(encoding="utf-8")
                                     .replace("\r\n", "\n").strip())
            _PERVIN_CACHE["mtime"] = mt
        return _PERVIN_CACHE["text"]
    except Exception:
        logger.exception("gagal memuat ai_domain_pervin.md")
        return ""


def _kamus_subset_block(messages: list[dict]) -> str:
    """[KAMUS ISTILAH GILIRAN INI] — SUBSET kamus sinonim yang trigger-nya
    muncul di ≤6 pesan user terakhir. Rombakan 3a: kamus penuh (21,5rb chars)
    DIHAPUS dari prompt statik; pencarian tak terpengaruh (semua jalur cari
    sudah ekspansi server-side via sinonim.expand_query) — subset ini hanya
    membantu model memilih kata query & menjelaskan padanan Inggris ke user.
    Disuntik sbg pesan system DINAMIS di ekor (zona bebas prompt-cache).

    Pemilihan BERPRIORITAS: dulu 12 entri PERTAMA dalam urutan file yang menang,
    jadi pada pertanyaan multi-part istilah paling spesifik bisa tak pernah sampai
    ke model hanya karena posisinya di kamus. Kini semua grup yang cocok dikumpulkan
    dulu lalu diurutkan by panjang trigger yang match (desc) — 'cucuk per' menang
    atas 'per', sama seperti aturan penjaga istilah di _paksa_istilah_kamus."""
    teks = " ".join(
        str((m or {}).get("content") or "") for m in (messages or [])[-6:]
        if (m or {}).get("role") == "user")
    if not teks:
        return ""
    cocok: list[tuple[int, str]] = []   # (panjang trigger yg match, baris)
    seen: set[str] = set()
    for e in _load_sinonim_entries():
        grup = e.get("grup") or ""
        if grup in seen:
            continue
        t = max((t for t in (e.get("triggers") or []) if t and sinonim.hit(t, teks)),
                key=len, default=None)
        if not t:
            continue
        seen.add(grup)
        trig = ", ".join(dict.fromkeys(x for x in (e.get("triggers") or []) if x))
        kw = ", ".join(dict.fromkeys(k for k in (e.get("keywords") or []) if k))
        if trig and kw:
            cocok.append((len(t), f"- {trig} → {kw}"))
    # Trigger paling spesifik dulu; urutan file jadi pemecah seri (stabil).
    cocok.sort(key=lambda x: -x[0])
    lines = [b for _, b in cocok[:_KAMUS_SUBSET_MAX]]
    if not lines:
        return ""
    return ("[KAMUS ISTILAH GILIRAN INI] (Indonesia → kata kunci katalog "
            "Inggris; tool cari sudah otomatis memakainya):\n" + "\n".join(lines))


def _load_maksud_entries() -> list:
    """Entri data/maksud/maksud.json (cache per-mtime). Wrapper tipis — pola
    `_load_sinonim_entries`: test/monkeypatch cukup menambal `ai.<attr>` ini."""
    return maksud.entries()


def _maksud_subset_block(messages: list[dict]) -> str:
    """[RUTE MAKSUD GILIRAN INI] — SUBSET Kamus Maksud (frasa user → TOOL) yang
    frasanya muncul di pesan user TERAKHIR.

    Saudara kembar _kamus_subset_block, tapi menjawab pertanyaan yang BERBEDA:
    kamus sinonim mengurus "kata apa yang dicari", store ini mengurus "tool mana
    yang dipakai". Dulu satu-satunya tempat untuk aturan semacam itu adalah
    ai_domain.md (file kode → butuh deploy); sejak 2026-08-01 pemilik bisa
    menambahnya lewat menu Rute Maksud atau langsung dari chat.

    ⚠️ Jendelanya SENGAJA berbeda dari kamus istilah (yang membaca 6 pesan
    terakhir), dan bedanya berasal dari akibat salah rute masing-masing: kamus
    hanya menambah kata kunci — istilah basi paling-paling jadi sinonim ekstra
    yang tak terpakai. Rute maksud MENYETIR PILIHAN TOOL, jadi frasa dari
    pertanyaan lima giliran lalu ("gambar teknisnya mana?") terus memaksa
    gambar_exploded pada pertanyaan berikutnya yang sudah pindah topik. Maksud
    itu milik kalimat yang sedang ditanyakan — bukan milik percakapan.

    Sama seperti kamus: disuntik sbg pesan system DINAMIS di ekor (zona bebas
    prompt-cache) dan hanya entri yang RELEVAN giliran ini — store boleh tumbuh
    tanpa membebani setiap percakapan."""
    teks = next((str((m or {}).get("content") or "")
                 for m in reversed(messages or [])
                 if (m or {}).get("role") == "user"), "")
    if not teks.strip():
        return ""
    try:
        return maksud.block(teks, _load_maksud_entries)
    except Exception:  # pragma: no cover — store rusak tak boleh mematikan chat
        logger.exception("blok rute maksud gagal disusun (dilewati)")
        return ""


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


# ── BATCH: satu panggilan, banyak item (2026-08-17) ─────────────────────────
# Audit 1.189 giliran produksi (17 Jul–16 Agu): 44% panggilan tool (1.394/3.196)
# adalah nama tool yang SAMA diulang dalam SATU giliran. Giliran yang kena butuh
# 2,80 ronde & 43,9 dtk; yang tidak kena 1,09 ronde & 19,8 dtk. Tiap ronde
# membayar ulang blok tetap ±43.832 token (system prompt + spec tool), jadi
# pengulangan itu ongkos terbesar asisten — sekaligus sumber jawaban SALAH:
# rem anti-loop menolak panggilan ke-N lalu model menyimpulkan "tidak ada".
#
# ⛔ Sebab yang TERUKUR, bukan dugaan: tool yang MENDEKLARASIKAN parameter array
# praktis tak pernah diulang (cek_massal_part 9 ulang/70 pakai, kategori_massal_part
# 0, spek_massal_rangka 0, banding_rangka_massal 0) sedangkan tool yang hanya
# MELARANG lewat prosa diulang ratusan kali. Buktinya paling telak: spec
# detail_part sudah memuat larangan huruf besar ber-⛔ ("HANYA untuk satu PN …
# akan DITOLAK sistem") dan tetap jadi pelanggar nomor 1 dengan 251 pengulangan.
# Kesimpulan: larangan PROSA tidak bekerja, SKEMA bekerja. Maka aturannya
# dipindah ke bentuk parameter, bukan ditulis lebih keras.
#
# ⛔ Yang TIDAK boleh berubah: setiap parameter yang kini menerima array WAJIB
# tetap menerima string tunggal apa adanya — riwayat percakapan lama, model yang
# memanggil dengan kebiasaan lama, dan seluruh tes lama harus tetap bekerja.

_MAX_BATCH_DEFAULT = 20       # plafon aman item per panggilan tool ringan
_BATCH_WORKERS = 4            # sejajar dgn ThreadPoolExecutor batch tool di p9


def _as_items(v, maks: int = _MAX_BATCH_DEFAULT, *, upper: bool = False,
              min_len: int = 1) -> list[str]:
    """`str | list | tuple` → daftar item bersih (unik, urutan asli, dipotong).

    Menerima BENTUK LAMA (satu string) maupun BARU (array) — inilah yang membuat
    perubahan skema kompatibel mundur mutlak. String ber-pemisah (`;` `,` baris)
    ikut dipecah karena model kerap menempel daftar user apa adanya; pola ini
    sudah terbukti di _t_cari_part_di_unit & _parse_daftar_pn.

    ⚠️ Pemisah sengaja TIDAK termasuk spasi: nama part lapangan berisi spasi
    ('kampas rem depan'). Tool yang itemnya PN memakai _parse_daftar_pn sendiri.
    """
    if isinstance(v, (list, tuple)):
        toks: list[str] = []
        for x in v:
            toks += re.split(r"[;\n]+", str(x or ""))
    else:
        toks = re.split(r"[;\n]+", str(v or ""))
    out: list[str] = []
    for t in toks:
        s = " ".join(str(t or "").split())
        if upper:
            s = s.upper()
        if len(s) >= min_len and s not in out:
            out.append(s)
    return out[:max(1, maks)]


def _batch_note(name: str, dipakai: list[str], dibuang: list[str]) -> str:
    """Nota JUJUR saat daftar dipotong plafon — user harus tahu apa yang belum
    dicek. Pola sama dengan _rem_tertunda_note: penolakan sistem tidak boleh
    berhenti sebagai catatan diam di dalam hasil."""
    if not dibuang:
        return ""
    return (f"⚠️ {len(dipakai)} item pertama diproses; {len(dibuang)} SISANYA BELUM "
            f"dicek karena plafon satu panggilan: {', '.join(dibuang[:12])}"
            f"{' …' if len(dibuang) > 12 else ''}. ⛔ JANGAN menyatakan apa pun "
            f"(ada/tidak ada) tentang item yang belum dicek — panggil {name} lagi "
            f"untuk sisanya, atau beri tahu user item mana yang belum sempat dicek.")


def _gabung_hasil(field: str, items: list[str], hasil: list[dict]) -> dict:
    """Gabungkan hasil per-item jadi SATU hasil tool.

    Bentuknya sengaja MENIRU hasil satu-item supaya SELURUH lapisan hilir tetap
    bekerja tanpa diubah: `_salvage_rows`/`_fakta_from_tool` mencari list-of-dict
    di kunci yang dikenal, `_row_count_nums` meng-ground jumlah baris, dan guard
    angka membaca nilai di dalamnya. Maka:

      • bila tiap hasil punya list-baris dengan NAMA KUNCI yang sama → baris
        digabung ke satu list dengan kolom penanda item asalnya (lebih banyak
        baris ter-ground daripada sebelumnya — mutu guard NAIK, bukan turun);
      • bila tidak (hasil berbentuk objek tunggal, mis. detail_part/cek_kendaraan)
        → tiap objek diberi penanda item lalu dikumpulkan di kunci 'hasil'
        (kunci itu dikenal _FAKTA_LIST_KEYS & _SALVAGE_LIST_KEYS).

    Galat satu item TIDAK menjatuhkan sisanya — itu justru inti perbaikannya.
    """
    pasang = [(it, r) for it, r in zip(items, hasil) if isinstance(r, dict)]
    if not pasang:
        return {"error": "semua item gagal diproses"}
    if len(pasang) == 1 and len(items) == 1:
        return pasang[0][1]                     # satu item → bentuk LAMA persis

    # Kunci list yang ADA di SEMUA hasil sukses → itu baris datanya.
    sukses = [(it, r) for it, r in pasang if not r.get("error")]
    kunci = ""
    if sukses:
        umum = set(k for k, v in sukses[0][1].items() if isinstance(v, list))
        for _, r in sukses[1:]:
            umum &= set(k for k, v in r.items() if isinstance(v, list))
        # Prioritaskan kunci yang memang dikenal lapisan hilir.
        for k in ("rows", "parts", "part", "hasil", "items", "daftar", "data",
                  "komponen", "kandidat", "unit", "figures"):
            if k in umum:
                kunci = k
                break
        if not kunci and umum:
            kunci = sorted(umum)[0]

    out: dict = {"jumlah_item": len(pasang)}
    gagal = [it for it, r in pasang if r.get("error")]
    if kunci:
        baris: list[dict] = []
        for it, r in sukses:
            for b in (r.get(kunci) or []):
                if isinstance(b, dict):
                    baris.append({field: it, **b})
                else:
                    baris.append({field: it, "nilai": b})
        out[kunci] = baris
        # Field non-list yang berbeda per item tetap dibawa sebagai ringkasan,
        # supaya konteks per item (mis. model/nama unit) tak hilang.
        ringkas = []
        for it, r in sukses:
            sisa = {k: v for k, v in r.items()
                    if k != kunci and not isinstance(v, (list, dict))}
            if sisa:
                ringkas.append({field: it, **sisa})
        if ringkas:
            out["ringkasan_per_item"] = ringkas
    else:
        out["hasil"] = [{field: it, **r} for it, r in pasang]
    if gagal:
        out["item_gagal"] = gagal

    # ⚠️ ANGKAT bendera OPERASI TULIS ke tingkat atas. Tool tulis (2 langkah)
    # menandai butuh persetujuan lewat `perlu_konfirmasi`; bila benderanya cuma
    # ada DI DALAM tiap item, model & klien tak melihatnya dan alur konfirmasi
    # diam-diam terlewat — jenis kegagalan yang sama dengan nota rem yang dulu
    # terkubur di dalam satu hasil tool.
    butuh = [it for it, r in pasang if r.get("perlu_konfirmasi")]
    if butuh:
        out["perlu_konfirmasi"] = True
        out["catatan"] = (
            f"⚠️ KONFIRMASI DULU ke user untuk SELURUH {len(butuh)} item ini "
            f"({', '.join(butuh[:20])}{' …' if len(butuh) > 20 else ''}). "
            "Tampilkan pratinjau tiap item lalu minta persetujuan SEKALI untuk "
            "semuanya. Bila user setuju, panggil tool ini LAGI dengan daftar item "
            "yang SAMA + konfirmasi=true — ⛔ jangan panggil satu per satu.")
    return out


def _fanout(fn, args: dict, user: dict, field: str, items: list[str],
            *, paralel: bool = True) -> dict:
    """Jalankan handler SATU-item untuk tiap item, lalu gabungkan.

    Handler aslinya TIDAK ditulis ulang — dipanggil apa adanya dengan `field`
    diisi satu item. Itu sengaja: badan handler tool ini adalah bagian paling
    teruji di sistem, dan menyalinnya untuk versi 'massal' persis kesalahan yang
    melahirkan 6 kembaran massal yang membelah pilihan model.

    Paralel memakai pola & jumlah worker yang sama dengan eksekusi batch tool di
    p9 (handler memang sudah dipanggil bersamaan dari sana), jadi banyak item
    tidak berarti berkali lipat waktu tunggu.
    """
    def _satu(it: str) -> dict:
        a = dict(args)
        a[field] = it
        try:
            r = fn(a, user)
            return r if isinstance(r, dict) else {"error": "hasil tool tak dikenal"}
        except Exception:       # satu item rusak tak boleh menjatuhkan sisanya
            logger.exception("fanout %s gagal untuk item %r", field, it)
            return {"error": "gagal diproses (gangguan internal)"}

    if len(items) == 1 or not paralel:
        hasil = [_satu(it) for it in items]
    else:
        with ThreadPoolExecutor(max_workers=min(_BATCH_WORKERS, len(items))) as ex:
            hasil = list(ex.map(_satu, items))
    return _gabung_hasil(field, items, hasil)


def _batch_wrap(fn, args: dict, user: dict, field: str, *, maks: int = _MAX_BATCH_DEFAULT,
                upper: bool = False, min_len: int = 1, alias: tuple[str, ...] = (),
                paralel: bool = True) -> dict | None:
    """Pintu masuk seragam di ATAS handler satu-item.

    Return None bila argumennya memang tunggal → pemanggil lanjut ke jalur lama
    (nol perubahan perilaku). Return dict bila memang banyak item.
    """
    v = args.get(field)
    if v in (None, "", [], ()):
        for a in alias:
            if args.get(a) not in (None, "", [], ()):
                v = args.get(a)
                break
    items = _as_items(v, maks=maks + 50, upper=upper, min_len=min_len)
    if len(items) <= 1:
        # ⛔ Jalur lama menerima STRING dan langsung .strip() — bila model kirim
        # array yang menyusut jadi ≤1 item (mis. satu entri terlalu pendek), array
        # itu akan sampai ke sana apa adanya dan meledakkan giliran user dengan
        # AttributeError. Maka dirapikan jadi skalar DI SINI, di satu tempat,
        # bukan menambah isinstance() di 15 handler.
        if isinstance(v, (list, tuple)):
            args[field] = items[0] if items else ""
            for a in alias:
                if isinstance(args.get(a), (list, tuple)):
                    args[a] = args[field]
        return None                              # jalur LAMA, tak tersentuh
    dibuang = items[maks:]
    items = items[:maks]
    sisa = dict(args)
    for a in alias:                              # alias dibersihkan agar tak dobel
        sisa.pop(a, None)
    out = _fanout(fn, sisa, user, field, items, paralel=paralel)
    nota = _batch_note(getattr(fn, "__name__", "tool").removeprefix("_t_"),
                       items, dibuang)
    if nota:
        out["catatan_batch"] = nota
    return out


