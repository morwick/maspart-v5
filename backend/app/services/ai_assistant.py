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

import json
import logging
import re
import time

import requests

from ..core.config import get_settings
from . import (catalog_bom, epc, epc_bom, epc_weichai, fault_codes, filter_ref, gudang,
               harga, orders, part_index, populasi, repairkit, sims)

logger = logging.getLogger("maspart.ai")

_TIMEOUT = 60
_MAX_TOOL_ROUNDS = 6          # batas putaran panggil-tool agar tidak loop
_MAX_HISTORY = 16             # batas pesan riwayat yang dikirim balik ke model
_MAX_PART_ROWS = 12           # batas baris hasil pencarian part global (hemat token)
_MAX_PART_ROWS_UNIT = 25      # batas lebih longgar saat difilter ke 1 unit (daftar lengkap)


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


_SINONIM_CACHE: dict = {"mtime": None, "data": []}


def _load_sinonim_entries() -> list:
    """Baca data/sinonim/sinonim.json. Di-cache per mtime file: editan tetap
    langsung terpakai (mtime berubah), tapi tak parse ulang di tiap panggilan
    tool. Format: [{"grup","triggers":[id...],"keywords":[en...]}]."""
    try:
        p = get_settings().data_path / "sinonim" / "sinonim.json"
        if not p.exists():
            return []
        mt = p.stat().st_mtime
        if _SINONIM_CACHE["mtime"] != mt:
            _SINONIM_CACHE["data"] = json.loads(p.read_text(encoding="utf-8")) or []
            _SINONIM_CACHE["mtime"] = mt
        return _SINONIM_CACHE["data"]
    except Exception:
        return []


def _sinonim_block() -> str:
    """Kamus istilah lapangan (Indonesia → kata kunci nama part Inggris) untuk prompt."""
    lines: list[str] = []
    for e in _load_sinonim_entries():
        trig = ", ".join(dict.fromkeys(t for t in (e.get("triggers") or []) if t))
        kw = ", ".join(dict.fromkeys(k for k in (e.get("keywords") or []) if k))
        if trig and kw:
            lines.append(f"- {trig} → {kw}")
    return "\n".join(lines)


def _expand_query(q: str) -> tuple[list[str], list[str]]:
    """Perluas query dgn keyword sinonim bila mengandung istilah lapangan.
    Return (daftar istilah cari [termasuk q asli], daftar trigger yang cocok)."""
    ql = (q or "").lower()
    terms: list[str] = [q]
    matched: list[str] = []

    def _hit(trig: str) -> bool:
        # cocok sbg KATA/FRASA utuh, bukan substring di tengah kata
        # (mis. trigger 'per' TIDAK boleh cocok di dalam 'persneling').
        return re.search(r"(?<!\w)" + re.escape(trig.lower()) + r"(?!\w)", ql) is not None

    for e in _load_sinonim_entries():
        hit = next((t for t in (e.get("triggers") or []) if t and _hit(t)), None)
        if hit:
            matched.append(hit)
            for kw in (e.get("keywords") or []):
                if kw and kw not in terms:
                    terms.append(kw)
    return terms, matched


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


def _tool_specs(user: dict) -> list[dict]:
    role = (user.get("role") or "").lower()
    specs = [
        {
            "type": "function",
            "function": {
                "name": "cari_part",
                "description": (
                    "Cari part di database lokal. Otomatis mencari di Part Number (PN) "
                    "DAN nama part sekaligus — tak perlu menentukan mode. Sistem juga "
                    "OTOMATIS mengerti istilah lapangan Bahasa Indonesia (mis. 'kampas "
                    "rem', 'saringan solar', 'gardan') dan memperluasnya ke kata kunci "
                    "katalog (yang berbahasa Inggris). Cukup teruskan istilah part dari "
                    "user APA ADANYA (Indonesia boleh). Mengembalikan PN, nama, stok "
                    "total, stok per gudang, harga jual lokal, dan UNIT/MODEL sumber. "
                    "Gunakan untuk 'apakah ada', 'stok berapa', 'cari part X'. "
                    "PENTING: data tersusun per unit/model truk. Bila user menyebut "
                    "unit/model (mis. NX360, HOWO-7, SITRAK, SG21), isi parameter "
                    "'unit' agar hasil discoped ke unit itu — jangan campur antar unit. "
                    "AKURASI: ini KATALOG PER-MODEL (perkiraan) — untuk part yang "
                    "menempel di unit user, bila ada nomor rangka pakai tool EPC dulu; "
                    "bila belum ada, minta nomor rangka (VIN) di awal jawaban."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Part Number atau kata kunci nama part (mis. 'injector')."},
                        "mode": {
                            "type": "string",
                            "enum": ["pn", "nama"],
                            "description": "'pn' = cari per Part Number (default), 'nama' = cari per nama part.",
                        },
                        "unit": {
                            "type": "string",
                            "description": "Opsional. Filter hasil ke unit/model tertentu (mis. 'NX360', 'HOWO-7', 'SITRAK', 'SG21'). Kosongkan untuk cari di semua unit.",
                        },
                    },
                    "required": ["query"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "detail_part",
                "description": (
                    "Ambil detail satu Part Number persis: nama, stok total, rincian "
                    "stok per gudang, harga jual lokal, dan SPESIFIKASI fisik resmi "
                    "(berat kg, dimensi cm, satuan, merek). Pakai juga untuk menjawab "
                    "pertanyaan berat/dimensi/ukuran sebuah PN."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "part_number": {"type": "string", "description": "Part Number lengkap/persis."},
                    },
                    "required": ["part_number"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "info_aplikasi",
                "description": (
                    "Ringkasan status data aplikasi: jumlah part terindeks, jumlah "
                    "entri stok & harga, daftar nama gudang, kurs CNY→IDR terkini. "
                    "Gunakan untuk pertanyaan umum tentang isi/daftar gudang/kurs."
                ),
                "parameters": {"type": "object", "properties": {}},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "daftar_unit",
                "description": (
                    "Daftar unit/model truk yang datanya tersedia (mis. NX360HP, "
                    "HOWO-7, SITRAK, Shantui SG21). Pakai bila user menyebut unit yang "
                    "tidak Anda kenal atau ingin tahu unit apa saja yang ada, sebelum "
                    "memakai parameter 'unit' di cari_part."
                ),
                "parameters": {"type": "object", "properties": {}},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "cari_kode_kesalahan",
                "description": (
                    "Cari arti KODE KESALAHAN / fault code / DTC mesin Sinotruk-HOWO "
                    "(ECU Bosch). Bisa pakai SPN+FMI (mis. SPN 1241 FMI 21), kode P/U "
                    "(mis. P0410), atau kata kunci komponen. Mengembalikan deskripsi "
                    "gangguan (teks asli Bahasa China — TERJEMAHKAN ke Indonesia untuk "
                    "user) beserta status lampu MIL/SVS."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "spn": {"type": "integer", "description": "Nomor SPN (Suspect Parameter Number)."},
                        "fmi": {"type": "integer", "description": "Nomor FMI (Failure Mode Identifier)."},
                        "code": {"type": "string", "description": "Kode P/U, mis. 'P0410'."},
                        "query": {"type": "string", "description": "Kata kunci bila SPN/FMI/kode tak diketahui."},
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "cari_filter_shantui",
                "description": (
                    "Cari FILTER untuk alat berat SHANTUI (excavator, bulldozer/buldozer, "
                    "roller, grader) — filter hidrolik & filter mesin (oli, solar/bahan "
                    "bakar, udara, water separator, AC). Mengembalikan Part Name, Part "
                    "Number Shantui, dan CROSS-REFERENCE merek lain (Fleetguard, Donaldson, "
                    "Weichai, HIFI, Sakura, Baldwin, Cummins). Pakai untuk pertanyaan "
                    "filter unit Shantui, mis. 'filter oli SD22', 'filter udara excavator "
                    "SE215', 'cross reference filter solar DH08', 'filter SR10 apa saja'."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "unit": {
                            "type": "string",
                            "description": "Model/tipe unit Shantui (mis. SD22, SD16, SE60W1, SE75W1, SE135F, SE215, DH08, SR10, SG15-B6) ATAU jenis alat (excavator/bulldozer/roller/grader). Kosong = semua.",
                        },
                        "query": {
                            "type": "string",
                            "description": "Jenis/kata kunci filter, mis. 'oli', 'solar', 'udara', 'hidrolik', 'water separator'. Kosong = semua filter unit itu.",
                        },
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "repair_kit_transmisi",
                "description": (
                    "Daftar REPAIR KIT / perpak TRANSMISI (gearbox) per model — komponen "
                    "yang diganti saat servis/overhaul gearbox. Mengembalikan SEAL KIT "
                    "(oil seal + gasket + O-ring) dan opsional OVERHAUL (bearing + "
                    "synchronizer + snap ring). Identifikasi model dari kode (mis. HW19709, "
                    "HW25712, ZF16S2531TO, 8JS85), dari Part Number gearbox assy, ATAU dari "
                    "nama UNIT (mis. 'HOWO-371', 'SITRAK 540'). ⭐ Bila user menyebut NOMOR "
                    "RANGKA/VIN, isi 'rangka' — sistem menanyakan gearbox PERSIS unit itu ke "
                    "EPC pabrik (lebih akurat daripada menebak dari nama unit; dua unit "
                    "'sama' bisa beda gearbox). Pakai untuk pertanyaan 'repair kit / perpak "
                    "/ seal kit / paking transmisi', 'apa saja diganti saat overhaul "
                    "gearbox', dll. Kosongkan 'transmisi' & 'rangka' untuk daftar model."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "transmisi": {
                            "type": "string",
                            "description": "Model gearbox (HW19709 / ZF16S2531TO / 8JS85), PN gearbox assy, ATAU nama unit. Kosongkan untuk daftar model yang tersedia.",
                        },
                        "rangka": {
                            "type": "string",
                            "description": "Nomor rangka/VIN unit (bila user menyebutnya) — gearbox di-resolve PERSIS dari EPC Sinotruk per-VIN, mengalahkan 'transmisi'.",
                        },
                        "tingkat": {
                            "type": "string",
                            "enum": ["seal_kit", "overhaul", "semua"],
                            "description": "'seal_kit' = perpak (seal+gasket+O-ring, default), 'overhaul' = bearing+synchronizer+snap ring, 'semua' = keduanya.",
                        },
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "daftar_transmisi_assy",
                "description": (
                    "Daftar LENGKAP & PASTI SEMUA transmisi/gearbox assy (unit gearbox "
                    "utuh) yang ada di katalog — lintas merek (Sinotruk/HOWO, ZF, Fast, "
                    "Shantui, Wechai), dikelompokkan per seri, dengan PN, nama, stok, dan "
                    "unit pemakai. WAJIB pakai tool ini (bukan cari_part) untuk permintaan "
                    "'listkan/daftar SEMUA transmisi assy', 'ada berapa transmisi assy', "
                    "'list seluruhnya', dsb. — karena cari_part dibatasi jumlah barisnya "
                    "sehingga TIDAK lengkap. Gunakan 'total_transmisi_assy' sbg jumlah resmi."
                ),
                "parameters": {"type": "object", "properties": {}},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "banding_assy",
                "description": (
                    "BANDINGKAN ISI DALAM (komponen internal) DUA PART ASSEMBLY berdasarkan "
                    "Part Number assy-nya — untuk tahu apakah part di dalamnya SAMA atau "
                    "BEDA. Berlaku untuk assembly KATEGORI mana pun yang punya PN assy: "
                    "TRANSMISI/gearbox (mis. HW19709XST201136 vs HW19709XST237036), KOPLING/"
                    "clutch, GARDAN/axle (drive/driven), MESIN/powertrain, KABIN/cab. "
                    "Mengembalikan jumlah part SAMA, yang hanya di salah satu, persen "
                    "kesamaan, verdict, contoh part beda (PN+nama). Pakai untuk 'apakah isi "
                    "assy A dan B sama', 'beda part-nya apa', 'A & B interchangeable?'. "
                    "(Untuk membandingkan KATEGORI antar UNIT, pakai banding_kategori.)"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "pn1": {"type": "string", "description": "Part Number assy pertama (mis. HW19709XST201136)."},
                        "pn2": {"type": "string", "description": "Part Number assy kedua (mis. HW19709XST237036)."},
                    },
                    "required": ["pn1", "pn2"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "isi_assy",
                "description": (
                    "Daftar ISI DALAM (komponen internal lengkap) SATU part ASSEMBLY "
                    "berdasarkan Part Number assy-nya — transmisi/gearbox, kopling, gardan/"
                    "axle, mesin, kabin. Beda dari repair_kit_transmisi (yang hanya seal/"
                    "bearing servis) — ini SELURUH part penyusun assembly. Pakai untuk 'apa "
                    "saja isi dalam HW19709XST201136', 'komponen gardan PN ini'."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "pn": {"type": "string", "description": "Part Number assy (mis. HW19709XST201136)."},
                    },
                    "required": ["pn"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "banding_kategori",
                "description": (
                    "BANDINGKAN satu KATEGORI part antara DUA UNIT truk — untuk tahu apakah "
                    "part kategori itu SAMA/interchangeable antar unit. Kategori (sheet "
                    "katalog): kabin, mesin/powertrain, kopling, transmisi/gearbox, gardan/"
                    "axle (depan=driven, belakang=drive), kelistrikan, REM, sasis/chassis, "
                    "karoseri, dll. Contoh: 'apakah sistem REM NX400 sama dengan V7X400?', "
                    "'kopling HOWO-371 vs HOWO-380 beda apa?'. Mengembalikan jumlah part "
                    "sama, beda di tiap unit, persen kesamaan, verdict, contoh part beda."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "unit1": {"type": "string", "description": "Unit truk pertama (mis. 'NX400 6X4' atau nama varian persis)."},
                        "unit2": {"type": "string", "description": "Unit truk kedua (mis. 'V7X400 8X4')."},
                        "kategori": {"type": "string", "description": "Nama kategori / istilah lapangan: rem, kopling, transmisi, gardan, kabin, kelistrikan, sasis, mesin, karoseri, dll. (atau kode 01..12)."},
                    },
                    "required": ["unit1", "unit2", "kategori"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "isi_kategori",
                "description": (
                    "Daftar part satu KATEGORI untuk SATU UNIT truk (isi sheet kategori). "
                    "Kategori: kabin, mesin, kopling, transmisi, gardan/axle, kelistrikan, "
                    "rem, sasis, karoseri, dll. Contoh: 'part REM apa saja di NX400?', "
                    "'komponen kelistrikan V7X400'."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "unit": {"type": "string", "description": "Unit truk (mis. 'NX400 6X4')."},
                        "kategori": {"type": "string", "description": "Nama kategori / istilah lapangan (rem, kopling, transmisi, gardan, …) atau kode 01..12."},
                    },
                    "required": ["unit", "kategori"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "part_termasuk_assy",
                "description": (
                    "REVERSE LOOKUP: diberi Part Number KOMPONEN (part kecil di dalam "
                    "assembly), tentukan komponen itu **termasuk di ASSEMBLY/TRANSMISI MANA "
                    "saja** (PN assy gearbox/kopling/gardan/mesin yang memuatnya). Pakai untuk "
                    "'part WG2229… ini termasuk transmisi mana?', 'PN ini bagian dari gearbox "
                    "apa', 'dipakai di assy mana'. Boleh BANYAK PN sekaligus (pisah spasi/koma/"
                    "baris). Mengembalikan per PN: daftar PN assy yang memuatnya + jumlahnya — "
                    "JAWAB dari daftar ini (PRESISI), JANGAN menggeneralisasi 'seri HW' saja."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "pn": {"type": "string", "description": "Part Number komponen. Boleh beberapa (pisah spasi/koma/baris)."},
                    },
                    "required": ["pn"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "cek_kendaraan",
                "description": (
                    "Cek SPESIFIKASI/KONFIGURASI kendaraan dari NOMOR RANGKA (VIN / frame "
                    "number) langsung dari database resmi EPC Sinotruk. Mengembalikan: model "
                    "code, brand, seri, drive mode (6x4 dll), Euro, jenis pakai, serta MODEL "
                    "ENGINE, GEARBOX, dan AXLE (depan/tengah/belakang), order no, dealer, "
                    "negara, tanggal keluar pabrik/jual. JUGA mengembalikan 'assembly_utama' = "
                    "daftar PN ASSEMBLY NYATA unit ini (kabin, gardan depan/tengah/belakang, "
                    "mesin, transmisi, kopling) yang bisa dipesan + stok/harga lokal — pakai ini "
                    "untuk 'PN transmisi/mesin/gardan unit rangka X' (lebih tepat dari kode model). "
                    "Pakai untuk 'unit dgn rangka X spesifikasinya apa', 'gearbox/axle/engine unit "
                    "rangka ini apa', 'PN assembly unit ini', cek VIN. "
                    "HANYA unit Sinotruk/HOWO/SITRAK. Boleh VIN penuh atau 8 digit frame."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "rangka": {"type": "string", "description": "Nomor rangka: VIN penuh (mis. LZZ5DMSD5RT108966) atau frame number 8 digit (mis. RT108966)."},
                    },
                    "required": ["rangka"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "assembly_utama_unit",
                "description": (
                    "ASSEMBLY UTAMA yang BENAR-BENAR TERPASANG di satu unit (dari NOMOR "
                    "RANGKA/VIN) — daftar 'four-assembly' resmi EPC Sinotruk: KABIN, GARDAN "
                    "depan/tengah/belakang, MESIN, TRANSMISI, KOPLING — tiap baris memberi PN "
                    "ASSEMBLY NYATA unit itu + stok/harga lokal. INI SUMBER YANG TEPAT untuk "
                    "'kabin/mesin/transmisi/gardan/kopling ASSY unit ini apa', 'PN assembly "
                    "<kategori> unit rangka X', 'transmisi assy unit ini'. ⛔ JANGAN pakai "
                    "kategori_unit (pohon Parts Atlas) untuk ini — Parts Atlas bisa memberi "
                    "cangkang/varian generik (mis. 'cab body assembly') yang BUKAN assembly "
                    "terpasang. Isi 'kategori' untuk menyaring ke satu assembly (mis. 'kabin', "
                    "'transmisi', 'gardan belakang'); kosongkan untuk SEMUA assembly utama. "
                    "HANYA unit Sinotruk/HOWO/SITRAK."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "rangka": {"type": "string", "description": "Nomor rangka: VIN penuh atau frame number 8 digit."},
                        "kategori": {"type": "string", "description": "Opsional. Assembly yang dicari: 'kabin', 'mesin', 'transmisi', 'kopling', 'gardan' (atau 'gardan depan/tengah/belakang'). Kosongkan untuk semua assembly utama."},
                    },
                    "required": ["rangka"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "bom_dari_rangka",
                "description": (
                    "Daftar PART (BOM PABRIK) untuk SATU unit dari NOMOR RANGKA/VIN — "
                    "diambil LANGSUNG dari EPC Sinotruk resmi, jadi PERSIS untuk unit itu "
                    "(bukan asumsi katalog per-model). Pakai untuk 'part apa saja di unit "
                    "rangka X', 'apakah unit rangka X pakai part/injector/… tertentu', "
                    "'PN <komponen> untuk unit rangka ini'. Bisa filter dengan kata_kunci "
                    "(istilah lapangan Indonesia / Inggris / PN — mis. 'injector', 'kampas "
                    "rem', 'WG9'). Tiap part disilangkan ke STOK & HARGA lokal kita bila ada. "
                    "Hasil SELALU memuat 'kategori_breakdown' = JUMLAH part per kategori (kabin, "
                    "rem, transmisi, dll) PERSIS untuk unit INI — pakai ini untuk 'berapa part "
                    "<kategori> di unit ini' (JANGAN pakai isi_kategori yang per-model). Beri "
                    "arg 'kategori' untuk daftar part satu kategori unit itu. Tanpa kata_kunci & "
                    "tanpa kategori = RINGKASAN + breakdown. HANYA unit Sinotruk/HOWO/SITRAK."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "rangka": {"type": "string", "description": "Nomor rangka: VIN penuh atau frame number 8 digit (mis. SJ346500)."},
                        "kata_kunci": {"type": "string", "description": "Opsional. Saring part berdasar nama/PN (mis. 'injector', 'oil seal', 'WG9')."},
                        "kategori": {"type": "string", "description": "Opsional. Saring ke satu kategori untuk unit ini (mis. 'kabin', 'rem', 'transmisi', 'kelistrikan', 'sasis'). Untuk 'berapa/part apa di <kategori> unit ini'."},
                    },
                    "required": ["rangka"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "banding_rangka",
                "description": (
                    "BANDINGKAN PART dua unit (via DUA nomor rangka/VIN) dari EPC — untuk "
                    "'apakah part kabin/rem/mesin/dll kedua rangka ini SAMA atau ada yang BEDA?'. "
                    "Membandingkan SET PART NYATA kedua unit (Loading List per-VIN) dan mengembalikan "
                    "jumlah sama/beda + DAFTAR part yang BEDA. ⛔ WAJIB pakai tool ini untuk "
                    "pertanyaan 'sama/beda' antar dua rangka — JANGAN menyimpulkan dari kemiripan "
                    "kode model atau spesifikasi (engine/gearbox/axle), itu menebak & sering SALAH "
                    "(dua unit model sama bisa beda part). Isi 'kategori' untuk membandingkan satu "
                    "kategori saja (mis. 'kabin'); kosongkan untuk SELURUH part. HANYA Sinotruk/HOWO/SITRAK."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "rangka_1": {"type": "string", "description": "Nomor rangka unit pertama (VIN atau frame 8 digit)."},
                        "rangka_2": {"type": "string", "description": "Nomor rangka unit kedua."},
                        "kategori": {"type": "string", "description": "Opsional. Bandingkan satu kategori saja (mis. 'kabin', 'rem', 'mesin', 'transmisi', 'kelistrikan', 'sasis'). Kosongkan = seluruh part."},
                    },
                    "required": ["rangka_1", "rangka_2"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "part_aus_dari_rangka",
                "description": (
                    "PART persis untuk SATU unit dari NOMOR RANGKA/VIN — diuraikan dari EPC PARTS "
                    "ATLAS (katalog terstruktur resmi). Tool OTOMATIS memilih modul Atlas sesuai "
                    "part: POROS/REM (kampas rem, sepatu rem, baut/mur roda, hub, bearing, seal "
                    "poros — dipisah DEPAN/BELAKANG), MESIN/POWERTRAIN (INJECTOR, common rail, "
                    "pompa injeksi, piston, ring, klep, noken/kruk as, pompa oli/air, turbo, filter "
                    "mesin), KOPLING, atau GEARBOX. INI TOOL WAJIB tiap user sebut NOMOR RANGKA + "
                    "nama part — memberi PN PERSIS per-VIN (mis. injector engine MC07, kampas rem "
                    "depan). ⛔ JANGAN pakai bom_dari_rangka (Loading List datar: internal mesin "
                    "terbungkus assembly, poros tanpa posisi) atau cari_part (lokal per-model, bisa "
                    "salah varian) bila rangka ADA. Untuk poros: isi 'posisi' (depan/belakang) bila "
                    "user minta satu sisi. HANYA unit Sinotruk/HOWO/SITRAK."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "rangka": {"type": "string", "description": "Nomor rangka: VIN penuh atau frame number 8 digit."},
                        "query": {"type": "string", "description": "Part poros yang dicari, istilah lapangan Indonesia/Inggris (mis. 'kampas rem', 'brake shoe', 'baut roda', 'mur roda', 'hub', 'bearing poros')."},
                        "posisi": {"type": "string", "enum": ["depan", "belakang"], "description": "Opsional. 'depan' (poros penumpu/driven axle) atau 'belakang' (poros penggerak/drive axle). Kosongkan untuk kedua poros."},
                    },
                    "required": ["rangka", "query"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "kategori_unit",
                "description": (
                    "POHON KATEGORI resmi EPC untuk SATU unit dari NOMOR RANGKA/VIN — memahami "
                    "SEMUA kategori/assembly unit itu BESERTA TURUNANNYA (sub-assembly berlapis). "
                    "TANPA 'kategori' → daftar LENGKAP kategori tingkat-atas unit (mis. gardan, "
                    "transmisi, mesin, kabin, rem, kelistrikan, dst). DENGAN 'kategori' → buka "
                    "kategori itu: daftar turunan (sub-kategori) + part langsung di dalamnya "
                    "(dengan stok/harga lokal). Bisa drill berlapis: buka turunan dengan memanggil "
                    "lagi memakai nama turunan sbg 'kategori'. Pakai untuk: 'kategori apa saja di "
                    "unit rangka X', 'isi kategori gardan/transmisi/kabin', 'unit ini terdiri dari "
                    "apa saja'. Untuk PART AUS spesifik yg perlu pisah depan/belakang (kampas rem, "
                    "tie rod, baut roda) tetap pakai part_aus_dari_rangka. HANYA Sinotruk/HOWO/SITRAK."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "rangka": {"type": "string", "description": "Nomor rangka: VIN penuh atau frame number 8 digit."},
                        "kategori": {"type": "string", "description": "Opsional. Nama/istilah kategori atau turunan yang mau dibuka (mis. 'gardan', 'transmisi', 'kabin', 'front axle', atau nama turunan dari hasil sebelumnya). Kosongkan untuk daftar semua kategori tingkat-atas."},
                    },
                    "required": ["rangka"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "uraikan_assembly",
                "description": (
                    "URAIKAN satu ASSEMBLY jadi KOMPONEN DI DALAMNYA (isi/turunan), PERSIS seperti "
                    "view 'Spare Part List' bergambar di EPC. WAJIB dipakai saat user minta part "
                    "KECIL yang ADA DI DALAM sebuah assembly — mis. 'karet/bos/seal/pin/ball joint "
                    "dari v-stay', 'isi dari <PN assy>', 'komponen thrust rod', 'turunan assembly X'. "
                    "Assembly bisa disebut via PN (mis. AZ000052000229) ATAU nama/istilah lapangan "
                    "(mis. 'v stay', 'thrust rod', 'tie rod'). Mengembalikan tiap komponen + qty + "
                    "stok/harga lokal. ⛔ JANGAN menjawab pertanyaan komponen-dalam-assembly dengan "
                    "PN assembly-nya sendiri — pakai tool ini untuk mendapat komponen aslinya. "
                    "Butuh NOMOR RANGKA (per-VIN). HANYA Sinotruk/HOWO/SITRAK."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "rangka": {"type": "string", "description": "Nomor rangka: VIN penuh atau frame number 8 digit."},
                        "assembly": {"type": "string", "description": "Assembly yang mau diurai — PN assembly (mis. 'AZ000052000229') atau nama/istilah (mis. 'v stay', 'thrust rod', 'tie rod')."},
                    },
                    "required": ["rangka", "assembly"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "uraikan_mesin",
                "description": (
                    "PART INTERNAL MESIN dari NOMOR RANGKA/VIN — untuk unit Sinotruk yang MESINNYA "
                    "WEICHAI (mis. WP12/WP13). Part internal mesin (blok, kruk as/crankshaft, piston, "
                    "ring, liner/cylinder liner, kepala silinder/cylinder head, klep, noken, pompa "
                    "oli/air, injector, dll) TIDAK ADA di EPC Sinotruk — ADA di EPC WEICHAI terpisah. "
                    "Tool ini mengambilnya OTOMATIS (SSO+BOM). TANPA 'part' → daftar GROUP mesin; "
                    "DENGAN 'part' → cari komponen itu + stok/harga. ⛔ Untuk part internal mesin unit "
                    "bermesin Weichai, JANGAN pakai part_aus_dari_rangka/bom_dari_rangka (itu EPC "
                    "Sinotruk, berhenti di engine assembly) — pakai tool INI. HANYA unit mesin Weichai."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "rangka": {"type": "string", "description": "Nomor rangka: VIN penuh atau frame number 8 digit."},
                        "part": {"type": "string", "description": "Opsional. Komponen mesin yang dicari, istilah Indonesia/Inggris (mis. 'piston', 'ring piston', 'cylinder liner/boring', 'crankshaft/kruk as', 'cylinder head', 'klep/valve', 'injector'). Kosongkan untuk daftar semua group mesin."},
                    },
                    "required": ["rangka"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "pengganti_part",
                "description": (
                    "PERSAMAAN/PENGGANTI (supersession) part MESIN Weichai — jawab 'PN ini diganti "
                    "nomor berapa?', 'part X sudah diskontinu, gantinya apa?', 'persamaan PN Y'. "
                    "Data替换/ECN resmi Weichai (per Part Number, global — tak perlu rangka). "
                    "Mengembalikan PN pengganti terbaru + PN lama + tanggal + tipe (searah/dua-arah), "
                    "disilang ke stok/harga lokal. Untuk part MESIN Weichai (PN numerik). Sesi Weichai "
                    "perlu aktif (kalau belum, cek satu unit mesin dulu)."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "part_number": {"type": "string", "description": "Part Number mesin yang mau dicek penggantinya (mis. '1000076563')."},
                        "rangka": {"type": "string", "description": "Opsional. Nomor rangka unit (untuk mengaktifkan sesi Weichai bila belum aktif)."},
                    },
                    "required": ["part_number"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "repair_kit_mesin",
                "description": (
                    "REPAIR KIT (维修包) MESIN Weichai dari NOMOR RANGKA — paket komponen servis/"
                    "overhaul mesin (seperti repair kit transmisi, tapi utk mesin). Untuk 'repair kit "
                    "mesin unit X', 'paket servis mesin', 'komponen overhaul mesin'. Disilang stok/"
                    "harga lokal. Hanya unit bermesin Weichai (bila mesin tak punya kit terdefinisi, "
                    "tool balas apa adanya)."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "rangka": {"type": "string", "description": "Nomor rangka: VIN penuh atau frame number 8 digit."},
                    },
                    "required": ["rangka"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "unit_dari_part",
                "description": (
                    "REVERSE: dari satu PART NUMBER → daftar MODEL/tipe kendaraan Sinotruk yang "
                    "MEMAKAINYA, langsung dari EPC resmi (lintas SEMUA model, jauh lebih lengkap "
                    "dari katalog lokal). Pakai untuk 'PN ini dipakai di unit/mobil apa saja', "
                    "'part X cocok di model apa', 'ini buat truk apa'. Mengembalikan nama part "
                    "(Inggris) + jumlah model + daftar model. HANYA Sinotruk/HOWO/SITRAK/HOMAN."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "part_number": {"type": "string", "description": "Part Number yang mau dicek dipakai di unit/model apa (mis. AZ1646901003)."},
                    },
                    "required": ["part_number"],
                },
            },
        },
    ]

    # Populasi Unit — data armada/unit terdaftar. HANYA admin & akun 'mas'
    # (SEE_ALL). User lain (cabang/biasa/pembeli) TIDAK diberi tool ini.
    if _can_populasi(user):
        specs.append({
            "type": "function",
            "function": {
                "name": "cek_populasi",
                "description": (
                    "Cek DATA POPULASI UNIT — armada/kendaraan yang terdaftar beserta "
                    "spesifikasinya (mis. kolom MODEL, JENIS, TIPE UNIT, LOKASI KERJA, "
                    "TAHUN, Euro, nomor polisi). Mengembalikan TOTAL unit, jumlah yang "
                    "cocok, rincian jumlah per MODEL/TIPE, dan contoh baris. Gunakan untuk "
                    "'ada berapa unit NX360', 'populasi unit di lokasi X', 'daftar unit "
                    "tahun 2022', 'unit Euro 3 berapa', atau cek per nomor polisi. Catatan: "
                    "ini BUKAN data part/stok — untuk part pakai cari_part."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": (
                                "Kata kunci. Boleh beberapa kata — SEMUA harus muncul "
                                "(mis. 'NX360 2022', 'HOWO Jakarta'). Kosongkan untuk "
                                "melihat ringkasan seluruh populasi."
                            ),
                        },
                    },
                },
            },
        })

    if role == "pembeli":
        specs.append({
            "type": "function",
            "function": {
                "name": "pesanan_saya",
                "description": "Daftar pesanan milik user (pembeli) ini: kode, gudang, total, status, tanggal.",
                "parameters": {"type": "object", "properties": {}},
            },
        })
        specs.append({
            "type": "function",
            "function": {
                "name": "detail_pesanan",
                "description": "Detail satu pesanan milik user ini berdasarkan kode pesanan (item, status, pembayaran, pengiriman).",
                "parameters": {
                    "type": "object",
                    "properties": {"order_code": {"type": "string"}},
                    "required": ["order_code"],
                },
            },
        })

    if _can_orders(user):
        specs.append({
            "type": "function",
            "function": {
                "name": "rekap_penjualan",
                "description": (
                    "Rekap penjualan: omzet, jumlah pesanan, status, per gudang, per "
                    "bulan, dan part terlaris. Admin = semua gudang; akun cabang = "
                    "discoped otomatis ke gudangnya."
                ),
                "parameters": {"type": "object", "properties": {}},
            },
        })
        specs.append({
            "type": "function",
            "function": {
                "name": "daftar_pesanan",
                "description": "Daftar pesanan terbaru. Admin = semua; akun cabang = otomatis gudangnya saja.",
                "parameters": {"type": "object", "properties": {}},
            },
        })

    # Harga SIMS/modal — hanya admin & akun SEE_ALL (mis. 'mas').
    if _can_sims(user):
        specs.append({
            "type": "function",
            "function": {
                "name": "harga_sims",
                "description": (
                    "Cek harga part dari sumber SIMS secara live (dalam CNY) lalu "
                    "dikonversi ke IDR memakai kurs terkini. Gunakan saat user minta "
                    "harga modal/SIMS atau harga yang tidak ada di list lokal."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "part_number": {"type": "string", "description": "Part Number yang dicek harganya."},
                    },
                    "required": ["part_number"],
                },
            },
        })

    return specs


# ═══════════════════════════════════════════════════════════════════════
#  IMPLEMENTASI TOOLS
# ═══════════════════════════════════════════════════════════════════════
def _slim_part(r: dict) -> dict:
    """Ambil field penting saja dari hasil search agar hemat token.
    `unit` = nama file Excel sumber = tipe unit/model truk part ini."""
    out = {
        "part_number": r.get("part_number"),
        "part_name": r.get("part_name"),
        "stok_total": r.get("stok"),
        "stok_per_gudang": r.get("gudang") or {},
        "harga_lokal": r.get("harga"),
        "unit": r.get("file"),
        "lokasi_file": r.get("path"),
    }
    # Keterangan tambahan (kolom Remark katalog) — hanya disertakan bila terisi.
    if r.get("keterangan"):
        out["keterangan"] = r.get("keterangan")
    return out


def _axle_posisi(pn: str) -> str | None:
    """PERKIRAAN posisi poros dari kategori catalog_bom LOKAL (per-model) — kategori
    06 (driven/从动桥/penumpu)=DEPAN, 07 (drive/驱动桥/penggerak)=BELAKANG. Ini hanya
    perkiraan katalog; posisi PASTI per-VIN hanya dari EPC (part_aus_dari_rangka).
    None bila bukan part poros ATAU PN muncul di KEDUA poros (ambigu — tak bisa
    dipastikan dari katalog; jangan tebak)."""
    try:
        entry = catalog_bom.pn_category_map().get(catalog_bom._norm(pn)) or {}
    except Exception:
        return None
    if entry.get("poros_ambigu"):
        return None  # muncul di depan & belakang → tak pasti, jangan klaim satu sisi
    cat = entry.get("kategori")
    if cat == "06":
        return "depan (perkiraan kategori katalog — pastikan via EPC)"
    if cat == "07":
        return "belakang (perkiraan kategori katalog — pastikan via EPC)"
    return None


def _norm(s: str) -> str:
    """Normalisasi untuk pencocokan unit: huruf besar, buang spasi/-/_."""
    return re.sub(r"[\s_\-]", "", (s or "")).upper()


def _stok_int(v) -> int:
    """Parse stok '21' / '—' / '1.234' → int (0 bila kosong/non-numerik)."""
    try:
        s = str(v).strip().replace(".", "").replace(",", "")
        if not s or s.lower() in ("—", "-", "nan", "none"):
            return 0
        return int(float(s))
    except Exception:
        return 0


def _relevansi(name: str, pn: str, q: str, terms: list[str]) -> tuple[int, str | None]:
    """Skor relevansi part terhadap maksud query + kata kunci yang paling cocok.
    Makin SPESIFIK kecocokan (kata kunci terpanjang yang jadi substring nama),
    makin tinggi skornya. Query yang berupa PN diberi skor sangat tinggi."""
    name_l = (name or "").lower()
    ql = (q or "").lower().strip()
    if ql and ql in (pn or "").lower():
        return 1000 + len(ql), None  # query = bagian Part Number → match kuat
    best = None
    for t in terms:
        tl = (t or "").lower().strip()
        # Kata query ASLI yang cocok di nama juga dihitung — tanpa ini, pencarian
        # langsung (mis. 'injector' tanpa sinonim) berskor 0 semua dan
        # 'jumlah_relevan_kuat' salah lapor 0 padahal hasil relevan banyak.
        if tl and tl in name_l:
            if best is None or len(tl) > len(best):
                best = tl
    return (len(best) if best else 0), best


def _t_cari_part(args: dict, user: dict) -> dict:
    q = (args.get("query") or "").strip()
    if not q:
        return {"error": "query kosong"}
    unit = (args.get("unit") or "").strip()

    # Pencarian DETERMINISTIK:
    #  1) ekspansi istilah lapangan → kata kunci katalog (sinonim.json)
    #  2) tiap istilah dicari di NAMA *dan* Part Number sekaligus (tak perlu mode),
    #     lalu hasil digabung & dedup. Jadi 'kampas rem' selalu menemukan
    #     'brake friction plate' dst, dan PN tetap ketemu walau diketik di sini.
    terms, matched_syn = _expand_query(q)

    def _search_terms_rows(term_list: list[str]) -> list[dict]:
        rows_: list[dict] = []
        seen_: set = set()
        for t in term_list:
            if not t:
                continue
            for r in part_index.search_part_number(t) + part_index.search_part_name(t):
                key = (r.get("part_number"), r.get("file"))
                if key not in seen_:
                    seen_.add(key)
                    rows_.append(r)
        return rows_

    # Cari DULU dengan istilah asli + ekspansi sinonim, TANPA koreksi typo — supaya
    # kata lapangan Indonesia yang valid (mis. 'kain') tidak diubah keliru jadi noise.
    search_terms: list[str] = _spelling_variants(list(dict.fromkeys(t for t in terms if t)))
    rows = _search_terms_rows(search_terms)

    # Koreksi salah ketik (mis. 'injektor' → 'injector') HANYA sebagai fallback saat
    # hasil asli benar-benar 0 — jadi tak pernah menambah hasil nyasar saat sudah ada
    # hasil, dan catatan koreksi hanya muncul saat memang relevan.
    corrections: list[tuple[str, str]] = []
    if not rows:
        corr_terms: list[str] = []
        for t in terms:
            ct, corr = part_index.correct_typos(t)
            for pair in corr:
                if pair not in corrections:
                    corrections.append(pair)
            if ct and ct not in search_terms and ct not in corr_terms:
                corr_terms.append(ct)
        if corr_terms:
            corr_terms = _spelling_variants(corr_terms)
            search_terms = list(dict.fromkeys(search_terms + corr_terms))
            rows = _search_terms_rows(corr_terms)

    # Untuk query TRANSMISI/GEARBOX: baris gearbox assy kerap bernama hanya kode
    # "HW….(spec)" TANPA kata 变速器/transmission (mis. HW13709XST216603 di NX280 6X2),
    # sehingga pencarian-nama melewatkannya & seolah varian itu "tak punya transmisi
    # assy". Surface-kan baris assy berdasar PN-nya (sumber kebenaran repairkit), exact
    # match — sub-part yang PN-nya kebetulan memuat kode itu (mis. WG…+008/1) di-skip.
    gearbox_q = _is_gearbox_query(q)
    if gearbox_q:
        seen_keys = {(r.get("part_number"), r.get("file")) for r in rows}
        for r in part_index.search_exact_pns(repairkit.assy_pns_raw()):
            k = (r.get("part_number"), r.get("file"))
            if k not in seen_keys:
                seen_keys.add(k)
                rows.append(r)

    notes: list[str] = []
    if matched_syn:
        notes.append(
            f"Istilah lapangan '{', '.join(dict.fromkeys(matched_syn))}' diperluas ke "
            f"kata kunci katalog: {', '.join(t for t in terms[1:])}."
        )
    if corrections:
        notes.append(
            "Koreksi salah ketik: "
            + "; ".join(f"'{o}' → '{c}'" for o, c in corrections)
            + " (beri tahu user asumsi ejaan yang benar)."
        )
    note = " ".join(notes) if notes else None
    if unit:
        key = _norm(unit)
        # Cocokkan ke nama file (unit) ATAU jalur folder — keduanya memuat model.
        scoped = [r for r in rows if key in _norm(r.get("file")) or key in _norm(r.get("path"))]
        unit_note = (
            f"Difilter ke unit '{unit}'."
            if scoped
            else f"Tidak ada hasil untuk '{q}' pada unit '{unit}' (dari {len(rows)} hasil lintas-unit). "
                 f"Coba tanpa filter unit atau cek daftar_unit untuk nama unit yang benar."
        )
        note = f"{note} {unit_note}" if note else unit_note
        rows = scoped

    # Gabungkan per Part Number: PN yang sama muncul di banyak varian unit
    # ditampilkan SEKALI, dengan daftar varian tempat ia dipakai. Stok & harga
    # berlaku sama per-PN (global), jadi tidak diulang.
    grouped: dict[str, dict] = {}
    order: list[str] = []
    for r in rows:
        pn = (r.get("part_number") or "").upper()
        if not pn:
            continue
        if pn not in grouped:
            slim = _slim_part(r)
            slim.pop("lokasi_file", None)
            slim.pop("unit", None)
            # Hemat token: buang field KOSONG per baris (artinya 'belum ada data' —
            # aturan 5b system prompt sudah menjelaskan cara menyampaikannya).
            if not slim.get("stok_per_gudang"):
                slim.pop("stok_per_gudang", None)
            if slim.get("harga_lokal") in (None, "", "—", "-"):
                slim.pop("harga_lokal", None)
            grouped[pn] = {**slim, "varian_unit": []}
            order.append(pn)
        u = r.get("file")
        if u and u not in grouped[pn]["varian_unit"]:
            grouped[pn]["varian_unit"].append(u)

    items = []
    ql = (q or "").lower().strip()
    for pn in order:
        it = grouped[pn]
        it["jumlah_varian"] = len(it["varian_unit"])
        # Ranking: relevansi (kecocokan paling spesifik) + ketersediaan stok.
        rel, cocok = _relevansi(it.get("part_name") or "", pn, q, terms)
        it["tersedia"] = _stok_int(it.get("stok_total")) > 0
        # 'cocok_kata' = penjelasan kenapa part muncul — hanya berguna bila kata
        # yang cocok BUKAN kata user sendiri (hasil ekspansi sinonim/ejaan).
        if cocok and cocok != ql:
            it["cocok_kata"] = cocok
        # Bila user menanyakan TRANSMISI/GEARBOX, naikkan unit gearbox UTUH ke atas
        # supaya tak tenggelam di antara sub-part (housing/shaft/lever). Sekaligus
        # tandai jenisnya agar AI mengenalinya sebagai transmisi assy.
        if gearbox_q and _is_gearbox_assy(pn, it.get("part_name") or ""):
            rel += 100000
            it["jenis"] = "TRANSMISI ASSY (gearbox/unit utuh)"
        # Tandai kecocokan KUAT vs LEMAH: kuat = match PN/assy atau kata kunci
        # spesifik (frasa atau kata non-generik); lemah = hanya kata umum tunggal
        # (mis. 'seal'/'bolt'). Dipakai utk 'jumlah_relevan_kuat' yang jujur.
        kuat = bool(it.get("jenis")) or (ql and ql in pn.lower())
        if not kuat and cocok:
            cl = cocok.lower().strip()
            kuat = (" " in cl) or (cl not in _GENERIC_KW)
        it["_kuat"] = kuat
        it["_rel"] = rel
        # Posisi poros (06 driven=DEPAN, 07 drive=BELAKANG) — berlaku semua part axle.
        pos = _axle_posisi(pn)
        if pos:
            it["posisi_poros"] = pos
        items.append(it)

    # Urut MURNI berdasarkan KECOCOKAN/KOMPATIBILITAS part dengan katalog (relevansi).
    # Stok TIDAK memengaruhi urutan — part yang stoknya kosong tetap diurut sesuai
    # kecocokannya (cuma ditandai 'tersedia' untuk info). Tiebreak deterministik:
    # jumlah varian unit (lebih umum dipakai) lalu PN, supaya urutan stabil.
    items.sort(key=lambda x: (x["_rel"], x.get("jumlah_varian", 0)), reverse=True)
    jumlah_relevan = sum(1 for it in items if it.get("_kuat"))
    for it in items:
        it.pop("_rel", None)
        it.pop("_kuat", None)

    jumlah_tersedia = sum(1 for it in items if it.get("tersedia"))
    # Saat difilter ke 1 unit, hasil sudah sempit & user biasanya ingin daftar
    # LENGKAP part untuk unit itu — tampilkan lebih banyak supaya part bernama
    # generik (mis. 'Filter element') yang peringkatnya agak bawah tetap ikut.
    # Pencarian global tetap dibatasi ketat agar hemat token.
    row_cap = _MAX_PART_ROWS_UNIT if unit else _MAX_PART_ROWS
    out = items[:row_cap]
    # Catatan jumlah yang JUJUR: bila total membengkak karena kecocokan kata umum
    # (mis. 'seal' pada 'seal kruk as' → ribuan), laporkan 'jumlah_relevan_kuat'
    # agar AI tak menyebut total mentah yang menyesatkan ke user.
    if len(items) > row_cap:
        if 0 < jumlah_relevan < len(items):
            tail = (
                f"{jumlah_relevan} part RELEVAN dengan '{q}' (dari {len(items)} total — "
                f"sisanya hanya cocok di kata umum & berada di peringkat bawah). Ditampilkan "
                f"{len(out)} teratas paling cocok. Saat menyebut jumlah ke user, pakai angka "
                f"RELEVAN ({jumlah_relevan}), JANGAN total mentah ({len(items)})."
            )
        else:
            tail = (
                f"{len(items)} part cocok — ditampilkan {len(out)} teratas (diurut berdasarkan "
                f"KECOCOKAN katalog, bukan stok). Bila kurang tepat, persempit dengan menyebut "
                f"UNIT/MODEL atau kata kunci yang lebih spesifik."
            )
        note = f"{note} {tail}" if note else tail

    # "Mungkin maksud Anda" — hanya saat benar-benar 0 hasil.
    saran = part_index.suggest_names(q, limit=6) if not items else []
    if saran and not note:
        note = "Tidak ada hasil persis — lihat 'saran_mungkin_maksud' untuk part dengan nama serupa."

    # UMPAN BALIK KAMUS: catat pencarian yang 0 hasil. Daftar 'MISS' ini = istilah
    # lapangan yang belum dikenali sistem → kandidat tambahan untuk sinonim.json.
    # Cek log: docker logs <container> 2>&1 | grep MISS  (lihat PROJECT.md §3.5.3).
    if not items:
        logger.info(
            "MISS cari_part query=%r unit=%r sinonim_cocok=%s ada_saran=%s user=%s",
            q, unit or None, matched_syn or [], bool(saran),
            user.get("username") or "?",
        )

    out_res = {
        "query": q, "kata_kunci_dicari": search_terms, "unit_filter": unit or None,
        "catatan": note,
        "jumlah_part_unik": len(items), "jumlah_relevan_kuat": jumlah_relevan,
        "ditampilkan": len(out),
        "jumlah_tersedia_stok": jumlah_tersedia,
        "saran_mungkin_maksud": saran,
        "urutan": "Hasil DIURUT berdasarkan KECOCOKAN/KOMPATIBILITAS part dengan katalog (BUKAN stok). Rekomendasikan part yang paling cocok untuk unit/kebutuhan user — stok hanya info, bukan dasar rekomendasi.",
        "info_stok_harga": "Stok & harga berlaku per Part Number (sama untuk semua varian unit yang memakai PN itu).",
        "hasil": out,
    }
    # User mencari part untuk UNIT spesifik → hasil katalog per-model hanyalah
    # PERKIRAAN. Dorong perilaku EPC-first: tanpa rangka, minta rangka di awal jawaban.
    if unit:
        out_res["peringatan_akurasi"] = (
            "Hasil ini dari KATALOG PER-MODEL (perkiraan) — dua unit bermodel sama bisa "
            "beda PN. Bila user BELUM memberi nomor rangka (VIN) di percakapan, WAJIB "
            "awali jawaban dengan meminta nomor rangka agar part dicek PERSIS via EPC, "
            "dan labeli hasil ini 'perkiraan per-model'. Bila rangka SUDAH ada, utamakan "
            "tool EPC (part_aus_dari_rangka/bom_dari_rangka) alih-alih hasil ini."
        )
    return out_res


def _t_daftar_unit(args: dict, user: dict) -> dict:
    units = part_index.unit_models()
    return {"jumlah": len(units), "unit": units}


def _t_detail_part(args: dict, user: dict) -> dict:
    pn = (args.get("part_number") or "").strip()
    if not pn:
        return {"error": "part_number kosong"}
    rows = part_index.search_part_number(pn)
    exact = [r for r in rows if (r.get("part_number") or "").upper() == pn.upper()]
    hits = exact or rows
    if not hits:
        return {"part_number": pn, "found": False, "pesan": "Tidak ditemukan di database lokal."}
    # Semua varian unit yang memakai PN ini.
    varian = []
    for r in hits:
        u = r.get("file")
        if u and u not in varian:
            varian.append(u)
    base = _slim_part(hits[0])
    base.pop("unit", None)
    base.pop("lokasi_file", None)
    result = {
        "found": True,
        **base,
        "varian_unit": varian,
        "jumlah_varian": len(varian),
        "info_stok_harga": "Stok & harga berlaku per Part Number (sama untuk semua varian unit).",
    }
    # Spesifikasi fisik resmi dari SIMS: berat (untuk ongkir) + dimensi + satuan +
    # merek. Non-fatal: bila SIMS tak punya data / down, detail tetap tampil.
    try:
        spec = sims.get_part_spec(pn)
    except Exception:
        spec = {}
    if spec:
        result["spesifikasi"] = spec
    pos = _axle_posisi(pn)
    if pos:
        result["posisi_poros"] = pos
    return result


def _t_harga_sims(args: dict, user: dict) -> dict:
    if not _can_sims(user):
        return {
            "denied": True,
            "error": "Akses harga SIMS/modal hanya untuk admin & akun 'mas'. "
                     "Jangan menampilkan atau memperkirakan harga SIMS untuk user ini.",
        }
    pn = (args.get("part_number") or "").strip()
    if not pn:
        return {"error": "part_number kosong"}
    try:
        d = harga.cari_harga(pn)
        return {
            "part_number": d.get("pn"),
            "harga_cny": d.get("cny"),
            "harga_idr": d.get("idr"),
            "kurs_cny_idr": d.get("rate"),
            "catatan": d.get("note"),
        }
    except Exception as e:  # pragma: no cover
        return {"error": f"gagal ambil harga SIMS: {e}"}


def _t_info_aplikasi(args: dict, user: dict) -> dict:
    st = part_index.status()
    rate, rate_note = harga.get_rate()
    return {
        "part_terindeks": st.get("part_count"),
        "entri_stok": st.get("stok_entries"),
        "entri_harga": st.get("harga_entries"),
        "daftar_gudang": st.get("gudang_names"),
        "kurs_cny_idr": round(rate, 2),
        "kurs_catatan": rate_note,
        "diindeks_pada": st.get("indexed_at"),
    }


def _t_pesanan_saya(args: dict, user: dict) -> dict:
    rows = orders.list_orders(username=user.get("username"))
    return {"jumlah": len(rows), "pesanan": rows[:30]}


def _t_detail_pesanan(args: dict, user: dict) -> dict:
    code = (args.get("order_code") or "").strip()
    if not code:
        return {"error": "order_code kosong"}
    o = orders.get_order(code, username=user.get("username"))
    if not o:
        return {"order_code": code, "found": False, "pesan": "Pesanan tidak ditemukan / bukan milik Anda."}
    keep = (
        "order_code", "gudang", "status", "subtotal", "shipping_cost", "total",
        "payment_method", "payment_channel", "payment_va", "payment_expiry",
        "paid_at", "courier", "courier_service", "tracking_no",
        "recipient_name", "recipient_address", "created_at", "items",
    )
    return {"found": True, **{k: o.get(k) for k in keep if k in o}}


def _branch_scope(user: dict) -> str | None:
    """Label gudang untuk akun cabang; None untuk admin (lihat semua)."""
    role = (user.get("role") or "").lower()
    if role == "admin":
        return None
    g = gudang.gudang_for_user(user.get("username", ""), role)
    return gudang.gudang_label(g) if g else None


def _t_rekap_penjualan(args: dict, user: dict) -> dict:
    if not _can_orders(user):
        return {"denied": True, "error": "Rekap penjualan hanya untuk admin & akun cabang."}
    return orders.sales_recap(gudang=_branch_scope(user))


def _t_daftar_pesanan(args: dict, user: dict) -> dict:
    if not _can_orders(user):
        return {"denied": True, "error": "Daftar pesanan hanya untuk admin & akun cabang."}
    rows = orders.list_orders(gudang=_branch_scope(user))
    return {"jumlah": len(rows), "pesanan": rows[:30]}


def _t_cari_kode_kesalahan(args: dict, user: dict) -> dict:
    spn = args.get("spn")
    fmi = args.get("fmi")
    code = (args.get("code") or "").strip() or None
    query = (args.get("query") or "").strip() or None
    try:
        spn = int(spn) if spn not in (None, "") else None
        fmi = int(fmi) if fmi not in (None, "") else None
    except (TypeError, ValueError):
        spn = fmi = None

    if spn is None and fmi is None and not code and not query:
        return {"error": "Sebutkan SPN+FMI, kode P/U, atau kata kunci komponen."}

    hits = fault_codes.search(spn=spn, fmi=fmi, code=code, query=query, limit=20)
    return {
        "total_database": fault_codes.count(),
        "kriteria": {"spn": spn, "fmi": fmi, "code": code, "query": query},
        "jumlah_cocok": len(hits),
        "hasil": [
            {
                "kode": r["code"],
                "spn": r["spn"],
                "fmi": r["fmi"],
                "label": r["english"],
                "deskripsi_cn": r["desc_cn"],  # Bahasa China — terjemahkan ke Indonesia
                "lampu_mil": r["mil"],
                "lampu_svs": r["svs"],
            }
            for r in hits
        ],
        "catatan": (
            "deskripsi_cn dalam Bahasa China — sajikan terjemahan Indonesianya. "
            "MIL=lampu check engine, SVS=lampu servis."
        ),
    }


def _t_cari_filter_shantui(args: dict, user: dict) -> dict:
    if not filter_ref.available():
        return {"error": "Data filter Shantui belum tersedia di server."}
    unit = (args.get("unit") or "").strip()
    query = (args.get("query") or "").strip()
    rows = filter_ref.search(unit, query)
    if not rows:
        logger.info(
            "MISS cari_filter_shantui unit=%r query=%r user=%s",
            unit or None, query or None, user.get("username") or "?",
        )
        return {
            "jumlah": 0,
            "hasil": [],
            "catatan": (
                f"Tidak ada filter cocok untuk unit '{unit or '(semua)'}' / kata kunci "
                f"'{query or '(semua)'}'. Unit Shantui yang ada datanya: "
                + ", ".join(filter_ref.units())
                + ". Atau sebut jenis filter (oli/solar/udara/hidrolik/water separator)."
            ),
        }
    return {
        "jumlah": len(rows),
        "hasil": [
            {
                "alat": r["alat"],
                "model_unit": r["model"],
                "jenis_filter": r["jenis"],  # 'hydraulic' / 'engine'
                "nama": r["part_name"],
                "part_number_shantui": r["part_number"],
                "cross_reference": r["cross_reference"],
            }
            for r in rows[:60]
        ],
        "catatan": (
            "cross_reference = part filter SETARA dari merek lain (Fleetguard, Donaldson, "
            "Weichai, HIFI, Sakura, Baldwin, Cummins) — bisa dipakai sebagai pengganti. "
            "part_number_shantui = nomor part asli Shantui."
        ),
    }


def _t_cek_populasi(args: dict, user: dict) -> dict:
    # Akses Populasi Unit hanya admin & akun 'mas' (SEE_ALL).
    if not _can_populasi(user):
        return {"denied": True, "error": "Data populasi unit hanya untuk admin & akun 'mas'."}
    q = (args.get("query") or "").strip()
    try:
        res = populasi.search_summary(q, limit=15)
    except Exception as e:  # pragma: no cover
        return {"error": f"gagal baca data populasi: {e}"}
    if not res.get("available"):
        return {
            "available": False,
            "error": "Data populasi unit belum tersedia (file populasi.xlsx belum diunggah admin).",
        }
    res["catatan"] = (
        "Ini data POPULASI UNIT (armada), bukan stok part. 'jumlah_per_nilai' = "
        "rincian jumlah unit per nilai kolom (mis. per MODEL). Bila user tanya "
        "'berapa unit', pakai 'jumlah_cocok'/'total_semua_unit'. Tampilkan ringkas, "
        "jangan dump semua baris."
    )
    return res


def _gearbox_from_rangka(rangka: str) -> tuple[str, dict]:
    """Resolve MODEL GEARBOX persis sebuah unit dari nomor rangka via EPC config.
    gearboxModelCode EPC berupa string deskriptif (mis. 'HW25712XST变速箱+HW50
    直联式取力器(带液力缓速器)') — kode model = token Latin/angka di AWAL string
    (bagian '+…取力器' adalah PTO, bukan gearbox). Return (kode, info); kode ''
    bila EPC tak menemukan rangka / tak mencantumkan gearbox / EPC down."""
    v = epc.lookup(rangka)
    gb_raw = (v.get("gearbox") or "").strip() if v.get("found") else ""
    m = re.match(r"[A-Za-z0-9\-]+", gb_raw)
    kode = (m.group(0) if m else "").strip("-")
    if kode:
        return kode, {
            "rangka": v.get("frame_number") or rangka,
            "gearbox_epc": gb_raw,
            "model_gearbox": kode,
            "sumber": "EPC Sinotruk — konfigurasi pabrik PER-VIN (pasti untuk unit ini, "
                      "bukan perkiraan per-model)",
        }
    return "", {
        "rangka": rangka,
        "gearbox_epc": None,
        "catatan_epc": "EPC tidak menemukan rangka ini / tidak mencantumkan gearbox "
                       "(atau EPC sedang tidak terjangkau). Hasil di bawah (bila ada) "
                       "di-resolve dari teks user — perkiraan per-model, BUKAN kepastian "
                       "per-unit; sampaikan itu ke user.",
    }


def _t_repair_kit_transmisi(args: dict, user: dict) -> dict:
    if not repairkit.available():
        return {"error": "Data repair kit transmisi belum tersedia di server."}
    q = (args.get("transmisi") or "").strip()
    tingkat = (args.get("tingkat") or "seal_kit").strip().lower()
    rangka = (args.get("rangka") or "").strip()

    # Nomor rangka disebut → tanya EPC gearbox PERSIS unit itu (config pabrik
    # menang atas tebakan dari nama unit; dua unit 'sama' bisa beda gearbox).
    resolusi_epc: dict | None = None
    if rangka:
        kode, resolusi_epc = _gearbox_from_rangka(rangka)
        if kode:
            q = kode
        elif not q:
            return {
                "resolusi_epc": resolusi_epc,
                "jumlah_model_cocok": 0,
                "catatan": "Gearbox unit ini tidak bisa dipastikan dari EPC dan user tidak "
                           "menyebut model/unit. Minta user cek ulang nomor rangkanya, atau "
                           "sebutkan kode model gearbox / nama unit — JANGAN menebak.",
            }
    if not q:
        models = repairkit.list_models()
        unit_tercatat = sorted({u for m in models for u in m.get("unit", [])})
        return {
            "daftar_model": models,
            "total_model": len(models),
            "total_unit_tercatat": len(unit_tercatat),
            "unit_tercatat": unit_tercatat,
            "catatan": "'unit_tercatat' = unit yang PUNYA transmisi assy + DATA repair kit "
                       "(khusus truk Sinotruk/HOWO — ini sumber kebenaran repair kit). "
                       "PENTING: daftar ini BUKAN daftar lengkap semua unit ber-transmisi. "
                       "Unit di LUAR daftar ini (mis. Shantui SD16/SG21/L55, varian Wechai) "
                       "BISA tetap punya transmisi/gearbox assy di katalog walau tanpa data "
                       "repair kit — untuk unit spesifik, JANGAN klaim 'tidak punya transmisi "
                       "assy' dari daftar ini; cek dulu via cari_part(query='transmisi', "
                       "unit=<nama unit>). Sebutkan model/PN/unit (mis. 'HW19709', "
                       "'ZF16S2531TO', '8JS85', PN gearbox assy, atau nama unit) untuk "
                       "melihat repair kit-nya.",
        }
    hits = repairkit.find(q)
    if not hits:
        models = ", ".join(m["model"] for m in repairkit.list_models())
        out = {"jumlah_model_cocok": 0,
               "catatan": f"Tidak ada repair kit transmisi untuk '{q}'. Model tersedia: {models}."}
        if resolusi_epc and resolusi_epc.get("model_gearbox"):
            out["resolusi_epc"] = resolusi_epc
            out["catatan"] = (
                f"Menurut EPC, gearbox unit ini adalah '{resolusi_epc['model_gearbox']}' — "
                f"tapi TIDAK ada data repair kit untuk model itu. Sampaikan apa adanya; "
                f"⛔ JANGAN menawarkan kit model lain seolah-olah cocok. Model dengan data "
                f"kit: {models}."
            )
        elif resolusi_epc:
            out["resolusi_epc"] = resolusi_epc
        return out
    hasil = []
    for mk, entry in hits[:4]:
        hasil.append({
            "model": mk,
            "tipe": entry.get("tipe"),
            "assy_pn": entry.get("assy_pn", []),
            "unit": entry.get("unit", []),
            "tingkat": tingkat,
            **repairkit.kit(entry, tingkat),
        })
    out = {
        "jumlah_model_cocok": len(hits),
        "tingkat": tingkat,
        "catatan": ("Repair kit disusun dari sheet gearbox katalog. 'seal_kit' = perpak "
                    "(oil seal+gasket+O-ring); 'overhaul' = bearing+synchronizer+snap ring. "
                    "Sajikan DIKELOMPOKKAN per kategori dengan PN + nama. Bila daftar sangat "
                    "panjang, tampilkan per kategori beserta jumlahnya & tawarkan rincian/Excel."),
        "hasil": hasil,
    }
    if resolusi_epc:
        out["resolusi_epc"] = resolusi_epc
        if resolusi_epc.get("model_gearbox"):
            out["catatan"] += (" Model gearbox di-RESOLVE dari EPC per-VIN — awali jawaban "
                               "dengan menyebut gearbox terpasang unit ini menurut data pabrik.")
    return out


def _assy_seri(pn: str, name: str, tipe: str | None) -> str:
    """Kelompokkan transmisi assy ke seri/merek untuk penyajian rapi."""
    pu = (pn or "").upper()
    t = (tipe or "")
    if pu.startswith("HW"):
        return "HOWO/Sinotruk (HW)"
    if pu.startswith("WG") or "ZF" in t.upper():
        return "ZF (WG)"
    if "JS" in pu or "FZ" in pu or "FAST" in t.upper() or "8JS" in t.upper():
        return "Fast (JS/8JS)"
    if "变速器" in (name or "") or "变速箱" in (name or ""):
        return "Lainnya (变速器/变速箱)"
    return "Shantui/Wechai & lainnya"


def _t_daftar_transmisi_assy(args: dict, user: dict) -> dict:
    """Daftar LENGKAP & PASTI seluruh transmisi/gearbox assy (unit utuh) di katalog.
    Sumber: scan seluruh katalog (_is_gearbox_assy) ∪ PN assy repair kit. TIDAK
    di-cap seperti cari_part, sehingga jumlahnya otoritatif (anti-undercount)."""
    part_index.ensure_index()
    # Peta PN(ternormalisasi) -> tipe gearbox dari repair kit (bila terdaftar).
    tipe_by_pn: dict[str, str] = {}
    for _mk, e in repairkit._load().items():
        for pn in e.get("assy_pn", []):
            tipe_by_pn[re.sub(r"[\s_\-/]", "", (pn or "")).upper()] = e.get("tipe") or ""

    assy_pns: set[str] = set()
    for pn, name in part_index.all_parts_min():
        if _is_gearbox_assy(pn, name):
            assy_pns.add(pn.upper())
    for pn in repairkit.assy_pns_raw():
        assy_pns.add((pn or "").upper())

    # Gabung per PN: stok per-PN (global) + daftar unit pemakai (dipakai pada).
    grouped: dict[str, dict] = {}
    for r in part_index.search_exact_pns(sorted(assy_pns)):
        pn = (r.get("part_number") or "").upper()
        if not pn:
            continue
        g = grouped.get(pn)
        if g is None:
            norm = re.sub(r"[\s_\-/]", "", pn)
            tipe = tipe_by_pn.get(norm)
            g = grouped[pn] = {
                "part_number": r.get("part_number"),
                "nama": r.get("part_name"),
                "tipe_gearbox": tipe or None,
                "stok": r.get("stok"),
                "harga": r.get("harga"),
                "seri": _assy_seri(pn, r.get("part_name") or "", tipe),
                "dipakai_pada": [],
            }
        u = r.get("file")
        if u and u not in g["dipakai_pada"]:
            g["dipakai_pada"].append(u)

    items = sorted(grouped.values(), key=lambda x: (x["seri"], x["part_number"]))
    ringkasan: dict[str, int] = {}
    for it in items:
        ringkasan[it["seri"]] = ringkasan.get(it["seri"], 0) + 1

    return {
        "total_transmisi_assy": len(items),
        "ringkasan_per_seri": ringkasan,
        "catatan": (
            "Ini daftar LENGKAP & PASTI semua transmisi/gearbox assy (unit utuh) di "
            "katalog — sudah mencakup Sinotruk/HOWO, ZF, Fast, DAN Shantui/Wechai. "
            "Gunakan 'total_transmisi_assy' sebagai jumlah resmi; JANGAN mengarang/"
            "menghitung sendiri. Sajikan dikelompokkan per 'seri' dengan PN, nama, stok, "
            "dan unit pemakai (dipakai_pada). Hanya sebagian punya data repair kit "
            "(lihat tipe_gearbox terisi)."
        ),
        "daftar": items,
    }


def _t_banding_assy(args: dict, user: dict) -> dict:
    if not catalog_bom.available():
        return {"error": "Data katalog BOM belum tersedia di server."}
    pn1 = (args.get("pn1") or "").strip()
    pn2 = (args.get("pn2") or "").strip()
    if not pn1 or not pn2:
        return {"error": "Butuh DUA Part Number assy (pn1 & pn2)."}
    res = catalog_bom.compare_assy(pn1, pn2)
    if "verdict" in res:
        nb = ("⚠️ Kedua assy BEDA KATEGORI — wajar isinya tak nyambung; pastikan user "
              "memang ingin membandingkannya. " if res.get("beda_kategori") else "")
        res["catatan"] = (
            nb + "Tiap assy memakai SATU unit patokan ('unit_patokan') sbg acuan isi part — "
            "adil 1 unit lawan 1 unit. Jawab JUJUR: sebut jumlah part SAMA, jumlah BEDA tiap "
            "sisi, persen_kesamaan; pakai 'verdict'/'ringkasan' — JANGAN bilang '100% sama' "
            "kecuali verdict='identik'. Beda ~10-30 part bisa sekadar varian versi katalog. "
            "Sajikan contoh part beda (hanya_di_1/hanya_di_2) dgn PN+nama.")
    return res


def _t_isi_assy(args: dict, user: dict) -> dict:
    if not catalog_bom.available():
        return {"error": "Data katalog BOM belum tersedia di server."}
    pn = (args.get("pn") or "").strip()
    if not pn:
        return {"error": "Sebutkan Part Number assy (pn)."}
    res = catalog_bom.assy_detail(pn)
    if "parts" in res:
        res["catatan"] = ("Komponen internal LENGKAP assembly (bukan repair kit), mengacu "
                          "katalog 'unit_patokan'. Bila panjang, ringkas jumlahnya & tawarkan "
                          "rincian. Untuk part servis transmisi (seal/bearing) pakai "
                          "repair_kit_transmisi.")
    return res


def _t_banding_kategori(args: dict, user: dict) -> dict:
    if not catalog_bom.available():
        return {"error": "Data katalog BOM belum tersedia di server."}
    u1 = (args.get("unit1") or "").strip()
    u2 = (args.get("unit2") or "").strip()
    kat = (args.get("kategori") or "").strip()
    if not u1 or not u2 or not kat:
        return {"error": "Butuh unit1, unit2, dan kategori."}
    res = catalog_bom.compare_units(u1, u2, kat)
    if "verdict" in res:
        res["catatan"] = (
            "Perbandingan kategori '" + res.get("kategori_nama", kat) + "' antara dua unit. "
            "Jawab JUJUR pakai angka: jumlah part SAMA, beda di tiap unit, persen_kesamaan, "
            "dan 'verdict'. JANGAN klaim '100% sama' kecuali verdict='identik'. Sajikan contoh "
            "part yang beda (hanya_di_1/hanya_di_2) dgn PN+nama. Catatan: kemiripan rendah pada "
            "rem/kopling/kelistrikan antar-model adalah WAJAR (konfigurasi beda per model).")
    return res


def _t_isi_kategori(args: dict, user: dict) -> dict:
    if not catalog_bom.available():
        return {"error": "Data katalog BOM belum tersedia di server."}
    u = (args.get("unit") or "").strip()
    kat = (args.get("kategori") or "").strip()
    if not u or not kat:
        return {"error": "Butuh unit dan kategori."}
    res = catalog_bom.category_parts(u, kat)
    if "parts" in res:
        res["catatan"] = ("Daftar part kategori ini untuk unit tsb. Bila panjang, ringkas "
                          "jumlahnya & tawarkan rincian. 'assy_pn' (bila ada) = PN assembly "
                          "utuh kategori itu.")
    return res


def _t_part_termasuk_assy(args: dict, user: dict) -> dict:
    if not catalog_bom.available():
        return {"error": "Data katalog BOM belum tersedia di server."}
    raw = (args.get("pn") or "").strip()
    if not raw:
        return {"error": "Sebutkan minimal satu Part Number komponen (pn)."}
    pns, seen = [], set()
    for tok in re.split(r"[\s,;]+", raw):
        t = tok.strip()
        if t and t.upper() not in seen:
            seen.add(t.upper())
            pns.append(t)
    hasil = [catalog_bom.part_in_assy(p) for p in pns[:25]]
    return {
        "hasil": hasil,
        "catatan": (
            "Reverse lookup: tiap komponen → daftar PN assy (transmisi/dll) yang MEMUATNYA. "
            "JAWAB PRESISI dari field 'assy' tiap PN — sebut jumlah & PN assy-nya; JANGAN cuma "
            "bilang 'seri HW'. Bila 'found'=0, komponen tak ditemukan di assembly mana pun "
            "(mungkin part non-assembly atau katalog belum ada). Bila satu komponen ada di "
            "banyak assy, boleh ringkas polanya (mis. 'semua varian 9-speed HW19709, bukan "
            "12-speed') tapi tetap tampilkan daftarnya."
        ),
    }


# Istilah kategori assembly UTAMA (Indonesia/Inggris) → kata kunci pencocok pada
# nama Inggris & label China daftar four-assembly. Dipakai memfilter "kabin/mesin/
# transmisi/gardan/kopling ASSY" ke assembly TERPASANG yang tepat.
_ASSY_KAT = {
    "kabin": (["cab"], ["驾驶室", "车身", "奔驰"]),
    "cab": (["cab"], ["驾驶室", "车身", "奔驰"]),
    "mesin": (["engine"], ["发动机"]),
    "engine": (["engine"], ["发动机"]),
    "transmisi": (["transmission", "gear box", "gearbox", "-gear", "speed transmission"], ["变速箱", "变速器"]),
    "gearbox": (["transmission", "gear"], ["变速箱", "变速器"]),
    "persneling": (["transmission", "gear"], ["变速箱", "变速器"]),
    "girboks": (["transmission", "gear"], ["变速箱", "变速器"]),
    "kopling": (["clutch"], ["离合器", "分离轴承"]),
    "clutch": (["clutch"], ["离合器", "分离轴承"]),
    "gardan": (["axle"], ["桥"]),
    "axle": (["axle"], ["桥"]),
    "gardan depan": (["front axle"], ["前桥"]),
    "gardan belakang": (["rear axle"], ["后桥"]),
    "gardan tengah": (["middle axle"], ["中桥"]),
    "poros depan": (["front axle"], ["前桥"]),
    "poros belakang": (["rear axle"], ["后桥"]),
}


def _match_assy_kategori(kategori: str, rows: list[dict]) -> list[dict]:
    """Subset assembly yang cocok istilah kategori (kabin/mesin/transmisi/…).
    Cocokkan kata kunci Inggris ke 'nama' & China ke 'kategori'/'tipe'. Untuk
    gardan, hormati depan/tengah/belakang bila disebut."""
    kl = (kategori or "").lower().strip()
    if not kl:
        return []
    # Ambil pemetaan paling SPESIFIK dulu (mis. 'gardan depan' > 'gardan').
    keys = sorted((k for k in _ASSY_KAT if k in kl), key=len, reverse=True)
    if not keys:
        return []
    en_kw: list[str] = []
    cn_kw: list[str] = []
    for k in keys[:1] if any(" " in k for k in keys) else keys:
        en, cn = _ASSY_KAT[k]
        en_kw += en
        cn_kw += cn
    out = []
    for r in rows:
        name_l = (r.get("nama") or "").lower()
        cn_hay = (r.get("kategori") or "") + " " + (r.get("_tipe_cn") or "")
        if any(w in name_l for w in en_kw) or any(w in cn_hay for w in cn_kw):
            out.append(r)
    return out


def _t_assembly_utama_unit(args: dict, user: dict) -> dict:
    """Daftar ASSEMBLY UTAMA TERPASANG untuk satu unit (per nomor rangka) dari
    EPC 'four-assembly' — kabin, gardan depan/tengah/belakang, mesin, transmisi,
    kopling — dengan PN assembly NYATA + stok/harga lokal. Ini SUMBER OTORITATIF
    untuk 'kabin/mesin/transmisi/gardan assy unit ini apa' (BUKAN pohon Parts Atlas
    yang bisa memberi cangkang/varian generik)."""
    rangka = (args.get("rangka") or "").strip()
    if not rangka:
        return {"error": "Sebutkan nomor rangka (VIN atau frame number)."}
    kategori = (args.get("kategori") or "").strip()

    al = epc_bom.assembly_list(rangka)
    err = al.get("_err")
    if err in ("token_expired", "no_token"):
        return {"found": False, "frame_number": al.get("frame_number"),
                "error": _EPC_TOKEN_MSG, "_token_issue": True}
    if err == "network":
        return {"found": False, "error": "Gagal menghubungi server EPC (jaringan). Coba lagi."}
    if not al.get("found"):
        return {"found": False, "frame_number": al.get("frame_number"),
                "error": "Daftar assembly utama unit ini tidak ditemukan di EPC (cek nomor "
                         "rangka; hanya unit Sinotruk/HOWO/SITRAK)."}

    asm = al["assemblies"]
    pns = [a["pn"] for a in asm]
    local: dict[str, dict] = {}
    for r in part_index.search_exact_pns(pns):
        pn = (r.get("part_number") or "").upper()
        if pn and pn not in local:
            local[pn] = r

    rows = []
    for a in asm:
        lr = local.get(a["pn"], {})
        row = {"part_number": a["pn"], "nama": a["nama"],
               "kategori": a.get("kategori_cn"), "_tipe_cn": a.get("tipe_cn"),
               "ada_di_inventori": bool(lr)}
        if lr:
            row["stok_total"] = lr.get("stok")
            row["harga_lokal"] = lr.get("harga")
            row["stok_per_gudang"] = lr.get("gudang") or {}
        rows.append(row)

    base = {
        "found": True,
        "frame_number": al.get("frame_number"),
        "jumlah_assembly": len(rows),
        "sumber": ("EPC Sinotruk 'four-assembly' (总成代码) — assembly UTAMA yang BENAR-BENAR "
                   "terpasang di VIN ini (kabin, gardan, mesin, transmisi, kopling). PN "
                   "assembly NYATA & bisa dipesan; disilang ke stok/harga lokal. Ini sumber "
                   "yang TEPAT untuk 'kabin/mesin/transmisi/gardan assy unit ini' — BUKAN "
                   "pohon Parts Atlas (yang bisa memberi cangkang/varian generik)."),
        "catatan": ("'kategori' berbahasa China — terjemahkan (驾驶室/奔驰白=kabin, 前桥=gardan "
                    "depan, 中桥=gardan tengah, 后桥=gardan belakang, 发动机=mesin, 变速箱="
                    "transmisi, 离合器=kopling, 分离轴承=bearing pembebas kopling). Sebut PN + "
                    "nama + stok/harga bila ada. ⛔ JANGAN mengarang PN di luar daftar ini."),
    }
    if kategori:
        cocok = _match_assy_kategori(kategori, rows)
        base["kategori_diminta"] = kategori
        base["assembly_cocok"] = [{k: v for k, v in r.items() if k != "_tipe_cn"} for r in cocok]
        if not cocok:
            base["catatan"] = (f"Tidak ada assembly UTAMA yang cocok '{kategori}' di daftar "
                               "four-assembly unit ini — lihat 'assembly_semua' untuk seluruh "
                               "assembly terpasang. ") + base["catatan"]
    base["assembly_semua"] = [{k: v for k, v in r.items() if k != "_tipe_cn"} for r in rows]
    return base


def _t_cek_kendaraan(args: dict, user: dict) -> dict:
    rangka = (args.get("rangka") or "").strip()
    if not rangka:
        return {"error": "Sebutkan nomor rangka (VIN atau frame number)."}
    res = epc.lookup(rangka)
    if res.get("found"):
        res["catatan"] = ("Data dari EPC Sinotruk. Beberapa field bisa berbahasa China "
                          "(mis. gearbox/axle/jenis pakai) — TERJEMAHKAN ke Indonesia saat "
                          "menjawab. Untuk daftar PART unit ini, pakai bom_dari_rangka.")
        # PERKAYA: PN ASSEMBLY UTAMA nyata unit ini (kabin, gardan, mesin, transmisi,
        # kopling) dari EPC — lebih actionable dari sekadar kode model. Disilang ke
        # stok/harga lokal supaya user tahu assembly mana yang ready. Best-effort:
        # bila endpoint/token bermasalah, spesifikasi dasar tetap tampil.
        try:
            al = epc_bom.assembly_list(rangka)
            if al.get("found") and al.get("assemblies"):
                pns = [a["pn"] for a in al["assemblies"]]
                local: dict[str, dict] = {}
                for r in part_index.search_exact_pns(pns):
                    pn = (r.get("part_number") or "").upper()
                    if pn and pn not in local:
                        local[pn] = r
                rows = []
                for a in al["assemblies"]:
                    lr = local.get(a["pn"], {})
                    row = {"part_number": a["pn"], "nama": a["nama"],
                           "kategori": a.get("kategori_cn"), "ada_di_inventori": bool(lr)}
                    if lr:
                        row["stok_total"] = lr.get("stok")
                        row["harga_lokal"] = lr.get("harga")
                        row["stok_per_gudang"] = lr.get("gudang") or {}
                    rows.append(row)
                res["assembly_utama"] = rows
                res["catatan"] += (
                    " 'assembly_utama' = PN ASSEMBLY NYATA unit ini (kabin/gardan/mesin/"
                    "transmisi/kopling) dari EPC — pakai INI (bukan sekadar kode model) bila "
                    "user tanya 'PN transmisi/mesin/gardan unit ini', dan sebut stok/harga "
                    "lokal bila ada. 'kategori' berbahasa China — terjemahkan (前桥=gardan "
                    "depan, 中桥=gardan tengah, 后桥=gardan belakang, 发动机=mesin, 变速箱="
                    "transmisi, 离合器=kopling). ⛔ JANGAN mengarang PN di luar daftar ini.")
        except Exception:
            logger.exception("assembly_list gagal (dilewati)")
    else:
        res["catatan"] = ("VIN/nomor rangka tidak ditemukan di EPC Sinotruk. ⛔ JANGAN MENEBAK "
                          "spesifikasi (engine/gearbox/axle/Euro) unit ini — sampaikan apa adanya "
                          "bahwa unit tak terbaca di EPC & minta user cek ejaan nomor rangka "
                          "(EPC hanya memuat unit Sinotruk/HOWO/SITRAK).")
    return res


_EPC_TOKEN_MSG = ("Token EPC sedang kedaluwarsa/belum diatur, jadi daftar part dari nomor "
                  "rangka tidak bisa diambil saat ini. Mohon admin memperbarui token EPC "
                  "(file data/epc_token.txt).")


def _t_bom_dari_rangka(args: dict, user: dict) -> dict:
    rangka = (args.get("rangka") or "").strip()
    if not rangka:
        return {"error": "Sebutkan nomor rangka (VIN atau frame number)."}
    kata = (args.get("kata_kunci") or "").strip()
    kategori = (args.get("kategori") or "").strip()

    res = epc_bom.loading_list(rangka)
    if not res.get("found"):
        err = res.get("_err")
        if err in ("token_expired", "no_token"):
            return {"found": False, "frame_number": res.get("frame_number"),
                    "error": _EPC_TOKEN_MSG, "_token_issue": True}
        if err == "network":
            return {"found": False, "error": "Gagal menghubungi server EPC (jaringan). Coba lagi."}
        return {"found": False, "frame_number": res.get("frame_number"),
                "error": "BOM unit ini tidak ditemukan di EPC (cek nomor rangka; hanya unit "
                         "Sinotruk/HOWO/SITRAK)."}

    parts = [p for p in (res.get("parts") or []) if p.get("pn")]
    all_pns = [p["pn"] for p in parts]

    # Silang tiap PN ke data lokal: nama Inggris katalog + stok + harga (satu baris per PN).
    local: dict[str, dict] = {}
    for r in part_index.search_exact_pns(all_pns):
        pn = (r.get("part_number") or "").upper()
        if pn and pn not in local:
            local[pn] = r

    # Kategorisasi PERSIS unit ini: PN dari EPC (BOM exact) × peta kategori katalog
    # lokal (kode 01..12). Memberi "berapa part kabin/rem/dll" untuk unit INI —
    # bukan angka per-model. Part tanpa padanan kategori → kode '00' (tak terkategori).
    _pncat = catalog_bom.pn_category_map() if catalog_bom.available() else {}

    def _catcode(pn: str) -> str:
        return (_pncat.get(catalog_bom._norm(pn)) or {}).get("kategori") or "00"

    _bd: dict[str, int] = {}
    for p in parts:
        c = _catcode(p["pn"])
        _bd[c] = _bd.get(c, 0) + 1
    kategori_breakdown = [
        {"kode": k,
         "kategori": catalog_bom.KATEGORI_NAMA.get(k, "Tak terkategori"),
         "jumlah_part": v}
        for k, v in sorted(_bd.items())
    ]

    note = None
    matched: list[dict] | None = None
    # Filter per KATEGORI (mis. 'berapa/part apa di kabin untuk unit ini').
    if kategori and not kata:
        code = catalog_bom.resolve_kategori(kategori)
        if not code:
            return {"found": True, "frame_number": res.get("frame_number"),
                    "jumlah_part_total": res.get("jumlah_part"),
                    "kategori_breakdown": kategori_breakdown,
                    "error": f"Kategori '{kategori}' tak dikenal. Pilih dari daftar di "
                             "kategori_breakdown (mis. kabin, rem, transmisi, kelistrikan)."}
        matched = [p for p in parts if _catcode(p["pn"]) == code]
        note = (f"Difilter ke kategori {catalog_bom.KATEGORI_NAMA.get(code, code)} — "
                "kategorisasi PERSIS untuk unit ini (PN dari EPC × kategori katalog lokal), "
                "bukan angka per-model.")
    if kata:
        terms, matched_syn = _expand_query(kata)
        up_terms = [t.upper() for t in terms if t]

        def _match(p: dict) -> bool:
            hay = " ".join([
                p["pn"],
                local.get(p["pn"], {}).get("part_name") or "",
                p.get("nama_cn") or "",
            ]).upper()
            return any(t in hay for t in up_terms)

        matched = [p for p in parts if _match(p)]
        if matched_syn:
            note = (f"Istilah lapangan '{', '.join(dict.fromkeys(matched_syn))}' diperluas ke "
                    f"kata kunci katalog: {', '.join(up_terms[1:])}.")

    # Nama Inggris RESMI EPC (kamus translate_cn) untuk part tanpa padanan lokal —
    # diisi sebelum render (lihat di bawah). Agar tak lagi cuma nama China.
    epc_en: dict[str, str] = {}

    def _enrich(p: dict) -> dict:
        lr = local.get(p["pn"], {})
        eng = lr.get("part_name") or epc_en.get(p["pn"])  # Inggris dari lokal / kamus EPC
        out = {
            "part_number": p["pn"],                       # IDENTITAS — apa adanya, jangan diubah
            "qty_di_unit": p.get("qty"),                  # IDENTITAS — apa adanya
            # Nama lokal/EPC kadang memuat newline → rapikan satu baris.
            "nama": " ".join((eng or p.get("nama_cn") or "").split()),
            "kategori": catalog_bom.KATEGORI_NAMA.get(_catcode(p["pn"]), "Tak terkategori"),
            "ada_di_inventori": bool(lr),
        }
        # Nama China asli SELALU disertakan (bila ada) → tiap nama bisa diverifikasi.
        if p.get("nama_cn"):
            out["nama_china"] = p["nama_cn"]
        # Bila nama masih China (tak ada padanan Inggris) → minta AI terjemahkan.
        if not eng and p.get("nama_cn"):
            out["nama_perlu_terjemah"] = True
        if lr:
            out["stok_total"] = lr.get("stok")
            out["harga_lokal"] = lr.get("harga")
            out["stok_per_gudang"] = lr.get("gudang") or {}
        return out

    base = {
        "found": True,
        "frame_number": res.get("frame_number"),
        "jumlah_part_total": res.get("jumlah_part"),
        "jumlah_ada_di_inventori_lokal": sum(1 for pn in all_pns if pn in local),
        "kategori_breakdown": kategori_breakdown,
        "sumber": ("EPC Loading List / BOM pabrik (工单BOM 'Loading List') — part yang BENAR-BENAR "
                   "terpasang saat unit ini dirakit (per-VIN). Sumber PALING presisi utk unit ini. "
                   "CATATAN: ini database berbeda dari 'Parts Atlas' terstruktur EPC — sebagian PN "
                   "work-BOM bisa TAK muncul saat dicari di Parts Atlas; itu NORMAL (beda database), "
                   "bukan berarti PN salah."),
    }
    if note:
        base["catatan_sinonim"] = note

    if res.get("partial"):
        # Loading List terpotong (server EPC balas data tak lengkap). JANGAN dipakai
        # menyimpulkan part TIDAK ADA di unit. Suruh AI cek ulang / jangan menebak.
        base["peringatan_data_tidak_lengkap"] = (
            f"⚠️ Loading List unit ini terbaca TIDAK LENGKAP (hanya {res.get('jumlah_part')} "
            "part; unit penuh biasanya ratusan–ribuan) — kemungkinan respons EPC terpotong. "
            "DILARANG menyimpulkan 'part tidak ada di unit ini' dari data ini. Sampaikan ke "
            "user bahwa data EPC sedang tidak lengkap & minta coba lagi sebentar; JANGAN "
            "menebak ada/tidaknya part.")

    if matched is None:
        base["catatan"] = ("Ini RINGKASAN. 'kategori_breakdown' = jumlah part per kategori "
                           "PERSIS untuk unit INI (mis. jumlah part kabin/rem/dll) — pakai itu "
                           "untuk pertanyaan 'berapa part <kategori>', JANGAN pakai angka "
                           "per-model katalog. Untuk rincian: sebutkan kata_kunci ATAU kategori "
                           "(mis. kabin/rem/transmisi). Nama part EPC berbahasa China; yang "
                           "punya padanan lokal tampil bahasa Inggris + stok/harga.")
        return base

    cap = 40
    base["kata_kunci"] = kata
    base["jumlah_cocok"] = len(matched)
    # Nama part yg TAK ada di katalog lokal (cuma China): terjemahkan INSTAN pakai
    # kamus Inggris-resmi-EPC (translate_cn). Yang tak tercakup kamus → biarkan China
    # (AI yang menerjemahkan saat menjawab; nama_china selalu disertakan utk verifikasi).
    try:
        for p in matched[:cap]:
            if p["pn"] not in local:
                t = epc_bom.translate_cn(p.get("nama_cn"))
                if t:
                    epc_en[p["pn"]] = t
    except Exception:
        pass
    base["parts"] = [_enrich(p) for p in matched[:cap]]
    base["terpotong"] = max(0, len(matched) - cap)
    if not matched:
        base["catatan"] = (
            f"Tidak ada part cocok '{kata}' sebagai ITEM TERPISAH di Loading List unit ini. "
            f"PENTING: Loading List = BOM pabrik level ASSEMBLY. Part AUS/SERVIS/POROS (kampas "
            f"rem, sepatu rem, BAUT/MUR RODA, hub, seal, bearing) TIDAK muncul terpisah di sini — "
            f"terbungkus di dalam assembly-nya (mis. kampas rem di '制动器总成/brake assembly'). "
            f"JANGAN simpulkan part tak ada. Untuk part POROS/REM/baut-mur roda/hub/bearing dari "
            f"unit ini, pakai part_aus_dari_rangka(rangka, query='{kata}') — itu menguraikan EPC "
            f"Parts Atlas sampai komponennya & PERSIS untuk VIN ini (sumber WAJIB; BUKAN cari_part "
            f"lokal yg per-model). Untuk part struktural, coba PN-nya langsung (nama EPC China).")
    return base


def _t_banding_rangka(args: dict, user: dict) -> dict:
    """BANDINGKAN PART NYATA dua unit (per nomor rangka) dari EPC Loading List —
    untuk 'apakah part X kedua unit sama?'. Membandingkan SET PN sebenarnya, BUKAN
    menebak dari kemiripan kode model/spesifikasi."""
    r1 = (args.get("rangka_1") or args.get("rangka1") or "").strip()
    r2 = (args.get("rangka_2") or args.get("rangka2") or "").strip()
    if not r1 or not r2:
        return {"error": "Sebutkan DUA nomor rangka: rangka_1 dan rangka_2."}
    kategori = (args.get("kategori") or "").strip()

    # Ambil KEDUA Loading List PARALEL (tiap call ke server China lambat ~30s) → ~½ waktu.
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=2) as _ex:
        _f1 = _ex.submit(epc_bom.loading_list, r1)
        _f2 = _ex.submit(epc_bom.loading_list, r2)
        ll1, ll2 = _f1.result(), _f2.result()
    for ll, rr in ((ll1, r1), (ll2, r2)):
        if not ll.get("found"):
            err = ll.get("_err")
            if err in ("token_expired", "no_token"):
                return {"found": False, "error": _EPC_TOKEN_MSG, "_token_issue": True}
            if err == "network":
                return {"found": False, "error": "Gagal menghubungi server EPC (jaringan). Coba lagi."}
            return {"found": False, "error": f"BOM unit '{rr}' tidak ditemukan di EPC (cek nomor "
                                             "rangka; hanya unit Sinotruk/HOWO/SITRAK)."}
        if ll.get("partial"):
            return {"found": False, "error": f"Data EPC unit '{ll.get('frame_number')}' terbaca "
                    "TIDAK LENGKAP — perbandingan tidak bisa diandalkan sekarang. Coba lagi sebentar.",
                    "_incomplete": True}

    code = None
    kat_nama = "SEMUA part"
    if kategori:
        code = catalog_bom.resolve_kategori(kategori) if catalog_bom.available() else None
        if not code:
            return {"found": False, "error": f"Kategori '{kategori}' tak dikenal (mis. kabin, rem, "
                    "transmisi, mesin, kelistrikan, sasis)."}
        kat_nama = catalog_bom.KATEGORI_NAMA.get(code, kategori)

    _pncat = catalog_bom.pn_category_map() if catalog_bom.available() else {}

    def _cat(pn: str) -> str:
        return (_pncat.get(catalog_bom._norm(pn)) or {}).get("kategori") or "00"

    def _set(ll: dict) -> dict:
        out = {}
        for p in ll.get("parts", []):
            pn = p.get("pn")
            if not pn or (code and _cat(pn) != code):
                continue
            out[pn] = p
        return out

    A, B = _set(ll1), _set(ll2)
    sa, sb = set(A), set(B)
    only1, only2, same = sa - sb, sb - sa, sa & sb

    diff_pns = list(only1) + list(only2)
    localn: dict[str, str] = {}
    for r in part_index.search_exact_pns(diff_pns):
        pn = (r.get("part_number") or "").upper()
        if pn and pn not in localn:
            localn[pn] = r.get("part_name") or ""

    def _row(pn: str, p: dict) -> dict:
        en = localn.get(pn) or epc_bom.translate_cn(p.get("nama_cn"))
        return {"part_number": pn, "qty_di_unit": p.get("qty"),
                "nama": " ".join((en or p.get("nama_cn") or "").split()),
                "nama_china": p.get("nama_cn") or ""}

    cap = 30
    return {
        "found": True,
        "rangka_1": ll1.get("frame_number"), "rangka_2": ll2.get("frame_number"),
        "kategori": kat_nama,
        "jumlah_part_1": len(A), "jumlah_part_2": len(B),
        "jumlah_sama": len(same), "jumlah_beda": len(only1) + len(only2),
        "identik": (not only1 and not only2),
        # Jumlah PER-SISI yang EKSPLISIT — agar AI tak salah pakai 'jumlah_beda' (total) utk tiap sisi.
        "jumlah_hanya_di_rangka_1": len(only1),
        "jumlah_hanya_di_rangka_2": len(only2),
        "hanya_di_rangka_1": [_row(pn, A[pn]) for pn in list(only1)[:cap]],
        "hanya_di_rangka_2": [_row(pn, B[pn]) for pn in list(only2)[:cap]],
        "hanya_di_1_terpotong": max(0, len(only1) - cap),
        "hanya_di_2_terpotong": max(0, len(only2) - cap),
        "daftar_lengkap": (len(only1) <= cap and len(only2) <= cap),  # True = SEMUA beda ditampilkan
        "sumber": ("EPC Loading List per-VIN — membandingkan PART NYATA kedua unit (set PN "
                   "sebenarnya), BUKAN tebakan dari kemiripan kode model/spesifikasi."),
        "catatan": ("identik=true → semua PN di kategori ini SAMA. identik=FALSE → ADA part BEDA: "
                    "WAJIB sebutkan part yang beda (hanya_di_rangka_1 / hanya_di_rangka_2). ⚠️ ANGKA: "
                    "'jumlah_beda' = TOTAL kedua sisi; untuk JUMLAH TIAP SISI pakai "
                    "'jumlah_hanya_di_rangka_1' & '..._2' (JANGAN pakai jumlah_beda utk satu sisi). "
                    "Bila daftar_lengkap=true, SEMUA part beda sudah ada di list — JANGAN tulis "
                    "'sebagian ditampilkan'. JANGAN bilang 'sama persis'. ⛔ JANGAN menyimpulkan "
                    "sama/beda dari kode model atau spesifikasi — pakai angka PART ini. PN & qty apa "
                    "adanya; nama boleh diterjemah (nama_china rujukannya). 📎 Sebuah kartu 'Unduh "
                    "Excel' otomatis muncul di bawah jawaban ini (memuat SELURUH part beda & sama, "
                    "tak dibatasi) — beri tahu user singkat bahwa mereka bisa mengunduhnya bila perlu."),
    }


# Kata kunci tambahan (Inggris + China) per domain PART AUS — Atlas memberi nama
# bilingual; sinonim katalog (_expand_query) sering hanya Inggris, jadi kita
# perkuat dgn istilah China inti agar pencocokan tak meleset.
_AUS_KEYWORDS = {
    "rem": ["friction", "brake shoe", "brake lining", "brake pad",
            "摩擦", "刹车", "制动蹄", "蹄", "制动摩擦"],
    # Tie rod / batang kemudi (sistem KEMUDI di poros depan). Slang lapangan sering
    # ditulis menyatu 'tierod' → cocokkan ke nama EPC "Steering tie rod ..." (spasi).
    "tierod": ["tie rod", "steering tie rod", "tie rod arm", "转向", "横拉杆", "直拉杆"],
    "tie rod": ["tie rod", "steering tie rod", "tie rod arm", "转向", "横拉杆", "直拉杆"],
    "batang stir": ["tie rod", "steering tie rod", "转向", "横拉杆", "直拉杆"],
    "batang kemudi": ["tie rod", "steering tie rod", "转向", "横拉杆", "直拉杆"],
    "gajah duduk": ["tie rod", "steering tie rod", "转向", "横拉杆", "直拉杆"],
    "kemudi": ["steering", "tie rod", "转向"],
    # Thrust rod / batang reaksi (suspensi poros). Slang lapangan: "tintong".
    "tintong": ["thrust rod", "straight thrust rod", "v-type thrust rod", "推力杆"],
    "thrust rod": ["thrust rod", "straight thrust rod", "v-type thrust rod", "推力杆"],
    "v stay": ["v-type thrust rod", "thrust rod", "v型推力杆", "推力杆"],
    "vstay": ["v-type thrust rod", "thrust rod", "v型推力杆", "推力杆"],
    "kopling": ["clutch", "pressure plate", "driven disc", "离合器", "压盘", "从动盘"],
    "seal": ["oil seal", "seal", "油封", "密封"],
    "bearing": ["bearing", "轴承"],
    "filter": ["filter", "element", "滤芯", "滤清器"],
    # Baut/mur RODA & hub (fastener poros — beda depan/belakang). Pakai frasa SPESIFIK
    # ('wheel bolt', bukan 'bolt' polos) agar tak terbanjiri ratusan hex bolt.
    "roda": ["wheel bolt", "车轮螺栓", "wheel nut", "车轮螺母", "hub bolt", "stud"],
    "hub": ["hub assembly", "wheel hub", "轮毂", "hub oil seal"],
    "naf": ["hub assembly", "wheel hub", "轮毂"],
    # MESIN (modul FDJ/Powertrain) — injector & internal mesin ADA di Atlas Powertrain.
    "injektor": ["fuel injector", "injector", "喷油器", "喷油"],
    "injector": ["fuel injector", "喷油器"],
    "nozzle": ["nozzle", "喷嘴"],
    "common rail": ["common rail", "共轨"],
    "piston": ["piston", "活塞"],
    "klep": ["valve", "气门"],
    "noken": ["camshaft", "凸轮轴"],
    "kruk as": ["crankshaft", "曲轴"],
}

# Pemetaan DOMAIN query → modul Atlas yang di-walk + apakah posisi (depan/belakang)
# relevan. Internal MESIN ada di modul Powertrain (FDJ/FDJFJ), kopling di LHQ,
# gearbox di BSX, sisanya poros/rem (CDQ/QDQ, posisi relevan).
_ATLAS_MODULE_MAP = [
    (["injector", "injektor", "nozzle", "喷油", "piston", "活塞", "ring piston",
      "活塞环", "liner", "boring", "缸套", "cylinder", "气缸", "缸盖", "valve", "klep",
      "气门", "camshaft", "noken", "凸轮轴", "crankshaft", "kruk as", "曲轴",
      "common rail", "共轨", "fuel pump", "fuel injection pump", "喷油泵", "oil pump",
      "pompa oli", "机油泵", "water pump", "pompa air", "水泵", "turbo", "增压器",
      "thermostat", "termostat", "节温器", "flywheel", "roda gila", "飞轮",
      "connecting rod", "stang seher", "连杆", "rocker", "pelatuk", "摇臂",
      "fuel filter", "filter solar", "燃油滤", "oil filter", "filter oli", "机油滤",
      "air filter", "filter udara", "空滤", "intercooler", "中冷", "seher", "cylinder head",
      "kepala silinder"],
     ("FDJ", "FDJFJ"), False),
    (["clutch", "kopling", "离合器", "压盘", "matahari kopling", "dekrup", "plat kopling"],
     ("LHQ",), False),
    (["gearbox", "transmisi", "persneling", "perseneling", "变速器", "synchronizer",
      "sincromes", "同步器", "shift fork", "garpu persneling", "拨叉"],
     ("BSX",), False),
    # FILTER umum (query 'filter'/'saringan' TANPA jenis): filter tersebar di MESIN
    # (oli/solar/udara — FDJ/FDJFJ) DAN poros (filter oli gardan — CDQ/QDQ) → walk
    # SEMUA. Tanpa entri ini, 'filter' polos jatuh ke default POROS saja dan filter
    # mesin cuma nyangkut dari tambalan Loading List (tanpa element di dlm assembly).
    # Pemisahan depan/belakang tak relevan untuk penyajian filter → is_axle False.
    (["filter", "saringan", "penyaring", "滤"],
     ("FDJ", "FDJFJ", "CDQ", "QDQ"), False),
]


def _atlas_modules_for(text: str) -> tuple[tuple, bool]:
    """Domain query → (modul Atlas, posisi_relevan). Default: poros/rem (CDQ/QDQ)."""
    t = (text or "").lower()
    for trigs, mods, axle in _ATLAS_MODULE_MAP:
        if any(k.lower() in t for k in trigs):
            return mods, axle
    return ("CDQ", "QDQ"), True


def _t_part_aus_dari_rangka(args: dict, user: dict) -> dict:
    """PART POROS/AXLE presis per-VIN & per-POSISI dari EPC PARTS ATLAS (tree walk) —
    SUMBER WAJIB untuk SEMUA part di poros: kampas rem, sepatu rem, BAUT/MUR RODA, hub,
    bearing, seal poros. Atlas mengurai assembly sampai komponen + memisah DEPAN (modul
    Driven axle 06) vs BELAKANG (Drive axle 07); PERSIS untuk unit ini (bukan per-model,
    bukan Loading List yg datar tanpa posisi)."""
    rangka = (args.get("rangka") or "").strip()
    if not rangka:
        return {"error": "Sebutkan nomor rangka (VIN penuh atau frame number)."}
    query = (args.get("query") or "").strip()
    if not query:
        return {"error": "Sebutkan part aus yang dicari (mis. 'kampas rem')."}
    posisi = (args.get("posisi") or "").strip().lower()

    # Kata kunci: sinonim katalog + istilah inti China/Inggris per domain.
    terms, _syn = _expand_query(query)
    kws = [t for t in terms if t]
    ql = (query + " " + " ".join(terms)).lower()
    for dom, extra in _AUS_KEYWORDS.items():
        if dom in ql:
            kws += extra
    kws = list(dict.fromkeys(k for k in kws if k))

    # Pilih MODUL Atlas sesuai domain: mesin→FDJ/FDJFJ, kopling→LHQ, gearbox→BSX,
    # poros/rem→CDQ/QDQ. Posisi depan/belakang HANYA relevan utk poros (is_axle).
    modules, is_axle = _atlas_modules_for(ql)
    if is_axle and ("depan" in posisi or "front" in posisi):
        want_posisi = "depan"
    elif is_axle and ("belakang" in posisi or "rear" in posisi):
        want_posisi = "belakang"
    else:
        want_posisi = None
    # Buang token GENERIK tunggal (bolt/nut/screw/...) yang membanjiri hasil bila
    # sudah ada kata kunci SPESIFIK (frasa multi-kata atau istilah China). Mis.
    # 'baut roda' → buang 'bolt' polos, sisakan 'wheel bolt'/'车轮螺栓' → tepat.
    _GENERIC = {"bolt", "nut", "screw", "washer", "pin", "ring", "plate", "cover",
                "shaft", "bushing", "gear", "spring", "valve", "pipe", "hose"}
    specific = [k for k in kws if (" " in k.strip()) or any(ord(c) > 0x2E80 for c in k)]
    if specific:
        kws = [k for k in kws if k.lower() not in _GENERIC]
    kws = list(dict.fromkeys(k for k in kws if k))

    res = epc_bom.atlas_find(rangka, kws, modules)
    err = res.get("_err")
    if err in ("token_expired", "no_token"):
        return {"found": False, "frame_number": res.get("frame_number"),
                "error": _EPC_TOKEN_MSG, "_token_issue": True}
    if err == "network":
        return {"found": False, "error": "Gagal menghubungi server EPC (jaringan). Coba lagi."}
    if err == "not_found":
        return {"found": False, "frame_number": res.get("frame_number"),
                "error": "Nomor rangka tidak ditemukan di EPC Parts Atlas (cek ejaan VIN; "
                         "hanya unit Sinotruk/HOWO/SITRAK)."}
    if err:  # api / lainnya
        return {"found": False, "frame_number": res.get("frame_number"),
                "error": "EPC Parts Atlas tidak mengembalikan data untuk unit ini."}

    parts = res.get("parts") or []

    # PERLUASAN POHON (semua grup unit): element/komponen servis kerap ada DI DALAM
    # assembly pada grup lain — kasus nyata: query 'filter' via modul FDJ hanya
    # memberi 'air filter assembly', padahal safety/main element (Mann-Hummel) &
    # element varian (Parker) ada di node 'Double-element air filter assembly'/
    # 'Fuel coarse filter' grup intake/fuel-supply. Buka node pohon yang cocok
    # query & gabungkan komponennya (dedup per PN). Best-effort.
    if not is_axle or not parts:
        try:
            tr = epc_bom.atlas_find_in_tree(rangka, kws)
            if tr.get("found"):
                have = {p["pn"] for p in parts}
                for p in tr["parts"]:
                    if p["pn"] not in have:
                        have.add(p["pn"])
                        parts.append(p)
            if tr.get("incomplete"):
                res["incomplete"] = True
        except Exception:
            logger.exception("atlas_find_in_tree gagal (dilewati)")

    # POROS: JANGAN filter ke satu posisi. Walk Atlas SELALU mengambil kedua poros
    # (CDQ+QDQ) tanpa biaya tambahan, jadi kita kembalikan KEDUANYA sekaligus,
    # dikelompokkan terpisah di bawah. Ini menutup celah model menyalin PN posisi
    # satu ke posisi lain pada follow-up pendek (mis. tanya 'belakang' → model copy
    # jawaban 'depan'). `want_posisi` hanya penanda sisi yang diminta user.

    # GAP-FILL dari EPC LOADING LIST: sebagian part (mis. MUR RODA/车轮螺母) ada di
    # rakitan RODA, bukan di modul poros, jadi TIDAK muncul di walk Atlas CDQ/QDQ.
    # Daripada AI menebak ('sudah termasuk baut'), kita ambil dari Loading List EPC
    # (per-VIN, tapi DATAR tanpa posisi). Hanya untuk kata kunci SPESIFIK yang BELUM
    # terwakili di hasil Atlas — supaya tak menambah assembly-level yang sudah diurai.
    spec_kws = [k for k in kws if (" " in k.strip()) or any(ord(c) > 0x2E80 for c in k)]
    atlas_text = " ".join(((p.get("nama") or "") + " " + (p.get("nama_cn") or "") + " "
                           + (p.get("pn") or "")) for p in parts).lower()
    unmatched = [k.lower() for k in spec_kws if k.lower() not in atlas_text]
    ll_extra: list[dict] = []
    if spec_kws:
        ll = epc_bom.loading_list(rangka)
        atlas_pns = {p["pn"] for p in parts}
        seen_ll: set = set()
        for p in (ll.get("parts") or []):
            pn = (p.get("pn") or "").upper()
            cn = (p.get("nama_cn") or "").lower()
            if not pn or pn in atlas_pns or pn in seen_ll:
                continue
            # (a) kata kunci yang TAK terwakili di Atlas → ambil apa pun yang cocok
            #     (kasus mur roda di rakitan roda). (b) kata kunci yang SUDAH ada di
            #     Atlas → LL hanya menambah baris ELEMENT/komponen TERPASANG per-VIN
            #     (bukan assembly 总成) sbg pelengkap varian Atlas — mis. element
            #     Cummins terpasang di samping varian Parker dari pohon Atlas.
            hit = any(k in cn for k in unmatched) or (
                "总成" not in cn and any(k.lower() in cn for k in spec_kws))
            if hit:
                seen_ll.add(pn)
                ll_extra.append({"pn": pn, "nama": "", "nama_cn": p.get("nama_cn") or "",
                                 "qty": p.get("qty"), "posisi": None, "pengganti": [],
                                 "_ll": True})
                if len(ll_extra) >= 20:
                    break

    if not parts and not ll_extra:
        if res.get("incomplete"):
            # Walk Atlas TERDEGRADASI/terpotong (sebagian call EPC gagal) → kosong di sini
            # BUKAN bukti part tak ada. Jangan simpulkan absen; minta coba lagi.
            return {"found": False, "frame_number": res.get("frame_number"),
                    "order_no": res.get("order_no"), "_incomplete": True,
                    "error": "Penelusuran EPC Parts Atlas untuk unit ini belum tuntas "
                             "(sebagian data EPC gagal diambil/ terpotong). JANGAN simpulkan "
                             f"part '{query}' tidak ada — minta user coba lagi sebentar."}
        return {"found": False, "frame_number": res.get("frame_number"),
                "order_no": res.get("order_no"),
                "error": f"Tidak ada part cocok '{query}' di poros "
                         f"{posisi or 'depan/belakang'} unit ini pada EPC Parts Atlas "
                         "maupun Loading List. Coba istilah lain atau tanpa posisi.",
                "jawaban_wajib": (
                    f"Sampaikan JUJUR ke user: part '{query}' TIDAK DITEMUKAN di EPC/katalog "
                    "untuk unit ini. ⛔ DILARANG KERAS menyebut/mengarang Part Number, stok, "
                    "atau harga apa pun (jangan tampilkan tabel PN). Sarankan: cek ejaan/"
                    "istilah lain (mis. 'tie rod' pakai spasi) atau sebutkan PN langsung."),
                "_atlas": True}

    all_parts = parts + ll_extra

    # Silang tiap PN ke inventori lokal: nama Inggris katalog + stok + harga.
    pns = [p["pn"] for p in all_parts]
    local: dict[str, dict] = {}
    for r in part_index.search_exact_pns(pns):
        pn = (r.get("part_number") or "").upper()
        if pn and pn not in local:
            local[pn] = r

    def _row(p: dict) -> dict:
        lr = local.get(p["pn"], {})
        # Nama lokal/EPC kadang memuat newline/spasi ganda → rapikan satu baris.
        nama = " ".join((lr.get("part_name") or p.get("nama") or p.get("nama_cn") or "").split())
        out = {
            "part_number": p["pn"],
            "nama": nama,
            "nama_china": " ".join((p.get("nama_cn") or "").split()),
            "qty_di_unit": p.get("qty"),
            "posisi_poros": ("depan (poros penumpu / driven axle)" if p.get("posisi") == "depan"
                             else "belakang (poros penggerak / drive axle)" if p.get("posisi") == "belakang"
                             else None),
            "ada_di_inventori": bool(lr),
        }
        if p.get("_ll"):
            out["sumber_baris"] = ("EPC Loading List (per-VIN) — part ini ADA di EPC tapi di "
                                   "rakitan roda, BUKAN modul poros; jadi posisi depan/belakang "
                                   "TIDAK dipisah di data. Jangan klaim posisi yang tak ada.")
            out["posisi_poros"] = None
        if p.get("dari_assembly"):
            # Komponen ini = ISI dari sebuah assembly (element servis) — sebutkan
            # assembly induknya agar user tahu konteks pemasangannya.
            out["di_dalam_assembly"] = p["dari_assembly"]
        if p.get("pengganti"):
            out["part_pengganti"] = p["pengganti"]  # supersession resmi EPC
        if lr:
            out["stok_total"] = lr.get("stok")
            out["harga_lokal"] = lr.get("harga")
            out["stok_per_gudang"] = lr.get("gudang") or {}
        return out

    base = {
        "found": True,
        "frame_number": res.get("frame_number"),
        "order_no": res.get("order_no"),
        "query": query,
        "posisi_diminta": posisi or "semua (depan & belakang)",
        "jumlah_dari_loading_list": len(ll_extra),
        "sumber": ("EPC Parts Atlas (katalog terstruktur resmi Sinotruk) — diuraikan dari "
                   "assembly sampai tiap komponen, PERSIS untuk unit/VIN ini. Sebagian part "
                   "(yang bertanda 'sumber_baris') dilengkapi dari EPC Loading List karena ada "
                   "di rakitan roda, bukan modul poros. Keduanya data EPC resmi per-VIN — BUKAN "
                   "katalog lokal per-model, BUKAN tebakan."),
        "catatan": ("posisi_poros dari Atlas sudah PASTI (DEPAN=Driven axle 06/从动桥, "
                    "BELAKANG=Drive axle 07/驱动桥). Part bertanda 'sumber_baris' = dari Loading "
                    "List, posisi TIDAK dipisah — JANGAN mengarang posisi/keterangan 'sudah "
                    "termasuk part lain'; sebut apa adanya (PN + qty + 'posisi tak dipisah di "
                    "EPC'). 'part_pengganti' bila ada = PERSAMAAN/PENGGANTI resmi EPC (PN lama "
                    "digantikan PN baru ini, format {pn, nama}) — pakai untuk jawab 'persamaan/"
                    "pengganti part X'. Tampilkan stok/harga lokal bila ada. Baris dengan "
                    "'di_dalam_assembly' = KOMPONEN/ELEMENT di dalam assembly tsb (mis. safety/"
                    "main element di dalam air filter assembly) — ini yang biasanya DIBELI saat "
                    "servis: JANGAN dihilangkan; kelompokkan di bawah assembly induknya."),
        "terpotong_walk": res.get("terpotong", False),
        **({"peringatan_tidak_lengkap":
            "⚠️ Penelusuran EPC belum tuntas (sebagian data gagal diambil/terpotong) — "
            "daftar ini bisa BELUM lengkap. Sebut PN yang ada, tapi JANGAN klaim 'cuma ini' "
            "atau 'tidak ada yang lain'; sarankan cek ulang sebentar."}
           if res.get("incomplete") else {}),
    }

    # NON-POROS (mesin/kopling/gearbox): posisi tak relevan → daftar datar seperti biasa.
    if not is_axle:
        base["jumlah"] = len(all_parts)
        base["parts"] = [_row(p) for p in all_parts]
        base["peringatan_posisi"] = (
            "Part ini BUKAN di modul poros (mesin/kopling/gearbox) → tidak ada pemisahan "
            "depan/belakang. Sebut apa adanya.")
        return base

    # POROS: kelompokkan HASIL ke depan / belakang / tanpa_posisi (Loading List).
    # SELALU sertakan KEDUA sisi walau user hanya minta satu — agar model tak perlu
    # (dan tak bisa) menyalin/menebak PN sisi lain.
    rows = [(_row(p), p.get("posisi")) for p in all_parts]
    depan = [r for r, pos in rows if pos == "depan"]
    belakang = [r for r, pos in rows if pos == "belakang"]
    tanpa = [r for r, pos in rows if pos not in ("depan", "belakang")]

    base["jumlah_depan"] = len(depan)
    base["jumlah_belakang"] = len(belakang)
    base["parts_depan"] = depan
    base["parts_belakang"] = belakang
    if tanpa:
        base["parts_tanpa_posisi"] = tanpa
    base["peringatan_posisi"] = (
        "⚠️ KRITIS: hasil ini MEMUAT KEDUA sisi — 'parts_depan' (poros penumpu / driven "
        "axle) DAN 'parts_belakang' (poros penggerak / drive axle). Keduanya OTORITATIF & "
        "SUDAH BENAR untuk VIN ini. Kampas/sepatu rem depan & belakang BIASANYA BEDA PN "
        "(ukuran beda). ATURAN MUTLAK: saat menjawab posisi tertentu, AMBIL PN HANYA dari "
        "grup posisi ITU — DILARANG menyalin PN dari grup posisi lain, dan DILARANG "
        "menjawab dari ingatan/turn sebelumnya. Bila user tanya 'depan' → pakai "
        "parts_depan; 'belakang' → parts_belakang; tak sebut sisi → tampilkan KEDUANYA "
        "sebagai dua kelompok. Boleh bilang 'sama' HANYA bila PN yang sama benar-benar "
        "muncul di kedua grup. 'parts_tanpa_posisi' (bila ada) = dari Loading List, posisi "
        "tak dipisah — jangan diklaim milik salah satu sisi.")
    return base


def _t_unit_dari_part(args: dict, user: dict) -> dict:
    pn = (args.get("part_number") or "").strip()
    if not pn:
        return {"error": "Sebutkan Part Number yang mau dicek dipakai di unit apa."}
    res = epc_bom.reverse_part(pn)
    err = res.get("_err")
    if err in ("token_expired", "no_token"):
        return {"found": False, "error": _EPC_TOKEN_MSG, "_token_issue": True}
    if err == "network":
        return {"found": False, "error": "Gagal menghubungi server EPC (jaringan). Coba lagi."}
    if not res.get("found"):
        if "kandidat" in res:  # tak ada PN yang cocok persis
            kand = res.get("kandidat") or []
            if kand:
                return {"found": False, "part_number": pn,
                        "error": f"PN '{pn}' tidak ditemukan PERSIS di EPC. Mungkin maksudnya "
                                 "salah satu PN mirip berikut?", "kandidat": kand}
            return {"found": False, "part_number": pn,
                    "error": f"PN '{pn}' tidak ditemukan di EPC (cek ejaan; hanya unit "
                             "Sinotruk/HOWO/SITRAK/HOMAN)."}
        return {"found": False, "part_number": pn, "nama": res.get("nama"),
                "error": f"PN '{pn}' dikenal EPC tapi tidak terpetakan ke model kendaraan mana pun."}
    cap = 50
    models = res.get("model") or []
    return {
        "found": True,
        "part_number": pn,
        "nama": res.get("nama"),
        "jumlah_model": res.get("jumlah_model"),
        "model": models[:cap],
        "terpotong": max(0, len(models) - cap),
        "sumber": ("EPC Sinotruk (reverse lookup global) — model kendaraan yang memakai PN ini "
                   "lintas SEMUA model resmi, bukan hanya katalog lokal kita."),
        "catatan": ("Nama model = deskripsi resmi Sinotruk (mis. kode ZZ.../HOWO...). Bila banyak, "
                    "RINGKAS polanya (mis. 'mayoritas dump truck HOWO 8x4') + sebut jumlah model. "
                    "Untuk stok/harga PN-nya, panggil detail_part."),
    }


def _t_kategori_unit(args: dict, user: dict) -> dict:
    """POHON KATEGORI EPC per-VIN. Tanpa 'kategori' → daftar SEMUA kategori/assembly
    tingkat-atas unit (mis. 117). Dengan 'kategori' → buka kategori itu: turunan
    (sub-kategori) + part langsung di dalamnya. Sumber: EPC Parts Atlas resmi,
    PERSIS unit ini (bukan per-model). Staged + cache."""
    rangka = (args.get("rangka") or "").strip()
    if not rangka:
        return {"error": "Sebutkan nomor rangka/VIN unit yang mau dilihat kategorinya."}
    kategori = (args.get("kategori") or "").strip()

    top = epc_bom.category_top(rangka)
    err = top.get("_err")
    if err in ("token_expired", "no_token"):
        return {"found": False, "error": _EPC_TOKEN_MSG, "_token_issue": True}
    if err == "network":
        return {"found": False, "error": "Gagal menghubungi server EPC (jaringan). Coba lagi."}
    if err == "not_found":
        return {"found": False, "error": "Nomor rangka tidak ditemukan di EPC Parts Atlas "
                "(cek ejaan VIN; hanya unit Sinotruk/HOWO/SITRAK)."}
    if err:
        return {"found": False, "error": "EPC Parts Atlas tidak mengembalikan kategori untuk unit ini."}

    cats = top.get("kategori") or []

    # (A) Tanpa kategori → DAFTAR kategori tingkat-atas (assembly) unit ini.
    if not kategori:
        return {
            "found": True,
            "frame_number": top.get("frame_number"),
            "jumlah_kategori": len(cats),
            "kategori": [
                {"nama": c["nama"] or c["nama_cn"], "nama_china": c["nama_cn"],
                 "kode": c["kode_kategori"], "punya_turunan": not c["leaf"]}
                for c in cats
            ],
            "sumber": ("EPC Parts Atlas resmi — daftar LENGKAP kategori/assembly PERSIS untuk "
                       "unit/VIN ini (bukan asumsi per-model)."),
            "catatan": ("Ini kategori TINGKAT-ATAS (assembly). Untuk melihat isi/turunan salah "
                        "satu, panggil lagi kategori_unit dengan 'kategori'=<nama/istilah kategori>. "
                        "Untuk PART AUS spesifik (kampas rem, sepatu rem, tie rod, dsb) yang perlu "
                        "dipisah depan/belakang, pakai part_aus_dari_rangka. JANGAN mengarang PN."),
        }

    # (B) Dengan kategori → resolve via nama + sinonim + istilah China domain.
    terms, _syn = _expand_query(kategori)
    match_terms = [kategori] + [t for t in terms if t]
    ql = (kategori + " " + " ".join(terms)).lower()
    for dom, extra in _AUS_KEYWORDS.items():
        if dom in ql:
            match_terms += extra
    cands = epc_bom.resolve_category(rangka, match_terms)
    if not cands:
        return {
            "found": False,
            "frame_number": top.get("frame_number"),
            "error": f"Kategori '{kategori}' tidak cocok dengan kategori unit ini.",
            "kategori_tersedia": [c["nama"] or c["nama_cn"] for c in cats][:40],
            "catatan": ("Sebut salah satu nama dari 'kategori_tersedia', atau untuk part aus "
                        "spesifik pakai part_aus_dari_rangka."),
        }

    dibuka: list[dict] = []
    for c in cands[:3]:
        opened = epc_bom.category_open(rangka, c["id"], c.get("part_list_id"), c.get("code"))
        parts = opened.get("parts") or []
        # Silang PN ke inventori lokal: nama Inggris + stok + harga.
        pns = [p["pn"] for p in parts]
        local: dict[str, dict] = {}
        for r in part_index.search_exact_pns(pns):
            pn = (r.get("part_number") or "").upper()
            if pn and pn not in local:
                local[pn] = r
        prows: list[dict] = []
        for p in parts:
            lr = local.get(p["pn"], {})
            row = {
                "part_number": p["pn"],
                "nama": " ".join((lr.get("part_name") or p.get("nama") or p.get("nama_cn") or "").split()),
                "nama_china": " ".join((p.get("nama_cn") or "").split()),
                "qty_di_unit": p.get("qty"),
            }
            if p.get("pengganti"):
                row["part_pengganti"] = p["pengganti"]
            if lr:
                row["stok_total"] = lr.get("stok")
                row["harga_lokal"] = lr.get("harga")
                row["stok_per_gudang"] = lr.get("gudang") or {}
            prows.append(row)
        dibuka.append({
            "kategori": c["nama"] or c["nama_cn"],
            "kategori_china": c["nama_cn"],
            "kode": c["kode_kategori"],
            "jumlah_turunan": opened.get("jumlah_sub"),
            "turunan": [
                {"nama": s["nama"] or s["nama_cn"], "nama_china": s["nama_cn"],
                 "punya_turunan": not s["leaf"]}
                for s in (opened.get("sub_kategori") or [])
            ],
            "jumlah_part": len(prows),
            "parts": prows,
        })

    return {
        "found": True,
        "frame_number": top.get("frame_number"),
        "dibuka": dibuka,
        "sumber": ("EPC Parts Atlas resmi — isi kategori PERSIS untuk unit/VIN ini (assembly "
                   "diuraikan ke turunan + part). Bukan katalog per-model, bukan tebakan."),
        "catatan": ("'turunan' = sub-kategori di bawah kategori ini — untuk membukanya panggil "
                    "LAGI kategori_unit dengan 'kategori'=<nama turunan> (bisa berlapis). 'parts' = "
                    "part LANGSUNG di kategori ini (sudah disilang stok/harga lokal bila ada). "
                    "⛔ JANGAN mengarang PN/stok/harga — sebut hanya yang ADA di hasil ini; bila "
                    "kosong, katakan apa adanya."),
    }


_PN_LIKE_RE = re.compile(r"^(?=[0-9A-Z.\-/]*[A-Z])(?=[0-9A-Z.\-/]*[0-9])[0-9A-Z][0-9A-Z.\-/]{5,}$")


def _t_uraikan_assembly(args: dict, user: dict) -> dict:
    """URAIKAN satu ASSEMBLY (per-VIN) → KOMPONEN DI DALAMNYA (isi/turunan), persis
    view 'Spare Part List' bergambar di EPC. Untuk 'karet/bos/seal/pin/isi dari
    <assembly>'. Match assembly via PN (mis. AZ000052000229) atau nama/istilah
    (mis. 'v stay', 'thrust rod'). Menyilang komponen ke stok/harga lokal."""
    rangka = (args.get("rangka") or "").strip()
    if not rangka:
        return {"error": "Sebutkan nomor rangka/VIN unit-nya."}
    assembly = (args.get("assembly") or "").strip()
    if not assembly:
        return {"error": "Sebutkan assembly yang mau diurai (PN assy atau namanya, mis. 'v stay')."}

    # Assembly bisa berupa PN langsung atau istilah (→ ekspansi sinonim).
    pn = assembly.upper() if _PN_LIKE_RE.match(assembly.upper()) else ""
    terms, _syn = _expand_query(assembly)
    match_terms = [assembly] + [t for t in terms if t]
    ql = (assembly + " " + " ".join(terms)).lower()
    for dom, extra in _AUS_KEYWORDS.items():
        if dom in ql:
            match_terms += extra

    res = epc_bom.assembly_components(rangka, match_terms, pn=pn)
    err = res.get("_err")
    if err in ("token_expired", "no_token"):
        return {"found": False, "error": _EPC_TOKEN_MSG, "_token_issue": True}
    if err == "network":
        return {"found": False, "error": "Gagal menghubungi server EPC (jaringan). Coba lagi."}
    if err == "not_found":
        return {"found": False, "error": "Nomor rangka tidak ditemukan di EPC (cek VIN; hanya Sinotruk/HOWO/SITRAK)."}
    if err:
        return {"found": False, "error": "EPC Parts Atlas tidak mengembalikan data untuk unit ini."}

    if not res.get("found"):
        msg = ("Assembly '" + assembly + "' tidak ditemukan di pohon unit ini.")
        if res.get("incomplete"):
            msg = ("Penelusuran pohon EPC unit ini belum tuntas (sebagian data gagal/terpotong) — "
                   "JANGAN simpulkan assembly tak ada; minta user coba lagi sebentar.")
        return {"found": False, "frame_number": res.get("frame_number"), "error": msg,
                "_incomplete": bool(res.get("incomplete"))}

    comps = res.get("components") or []
    pns = [c["pn"] for c in comps]
    local: dict[str, dict] = {}
    for r in part_index.search_exact_pns(pns):
        p = (r.get("part_number") or "").upper()
        if p and p not in local:
            local[p] = r
    rows: list[dict] = []
    for c in comps:
        lr = local.get(c["pn"], {})
        row = {
            "part_number": c["pn"],
            "nama": " ".join((lr.get("part_name") or c.get("nama") or c.get("nama_cn") or "").split()),
            "nama_china": " ".join((c.get("nama_cn") or "").split()),
            "qty_di_assembly": c.get("qty"),
        }
        if c.get("pengganti"):
            row["part_pengganti"] = c["pengganti"]
        if lr:
            row["stok_total"] = lr.get("stok")
            row["harga_lokal"] = lr.get("harga")
            row["stok_per_gudang"] = lr.get("gudang") or {}
        rows.append(row)

    asm = res.get("assembly") or {}
    return {
        "found": True,
        "frame_number": res.get("frame_number"),
        "assembly": {"part_number": asm.get("pn"), "nama": asm.get("nama"),
                     "nama_china": asm.get("nama_cn")},
        "jumlah_komponen": len(rows),
        "komponen": rows,
        "sumber": ("EPC Parts Atlas resmi — daftar KOMPONEN di dalam assembly ini PERSIS untuk "
                   "unit/VIN ini (sama seperti view 'Spare Part List' bergambar di EPC). Komponen "
                   "disilang ke stok/harga katalog lokal."),
        "catatan": ("Ini ISI/turunan dari assembly di atas — JANGAN sebut PN assembly-nya sebagai "
                    "salah satu komponen. Tampilkan PN + nama + qty + stok/harga tiap komponen. "
                    "⛔ JANGAN mengarang PN; sebut hanya komponen yang ADA di daftar ini."),
        **({"peringatan_tidak_lengkap":
            "⚠️ Penelusuran pohon EPC unit ini belum tuntas — daftar komponen bisa belum lengkap."}
           if res.get("incomplete") else {}),
    }


def _t_uraikan_mesin(args: dict, user: dict) -> dict:
    """PART INTERNAL MESIN (Weichai) per-VIN — untuk unit Sinotruk yang bermesin
    Weichai (mis. WP12). Otomatis menempuh EPC Weichai (SSO + BOM). Tanpa 'part' →
    daftar GROUP mesin (Engine Block, Crankshaft, Piston, Cylinder Head, dst).
    Dengan 'part' → cari komponen mesin itu + stok/harga lokal."""
    rangka = (args.get("rangka") or "").strip()
    if not rangka:
        return {"error": "Sebutkan nomor rangka/VIN unit-nya."}
    part = (args.get("part") or args.get("query") or "").strip()

    if part:
        terms, _syn = _expand_query(part)
        match_terms = [part] + [t for t in terms if t]
        ql = (part + " " + " ".join(terms)).lower()
        for dom, extra in _AUS_KEYWORDS.items():
            if dom in ql:
                match_terms += extra
        res = epc_weichai.find_parts(rangka, match_terms)
    else:
        res = epc_weichai.engine_bom(rangka)

    if not res.get("found"):
        reason = res.get("reason")
        if reason in ("no_link", "no_engine", "no_order"):
            return {"found": False,
                    "error": (res.get("message") or "Unit ini bukan bermesin Weichai / tak ada data mesin di EPC Weichai.")
                             + " (Fitur ini hanya untuk unit Sinotruk yang mesinnya Weichai, mis. WP-series.)"}
        return {"found": False, "error": res.get("message") or "Gagal mengambil BOM mesin Weichai. Coba lagi."}

    eng = res.get("engine") or {}
    engine_info = {"model_mesin": eng.get("nama"), "nomor_mesin": eng.get("model"), "order": eng.get("order")}

    # Mode DAFTAR GROUP (tanpa 'part').
    if not part:
        return {
            "found": True, "mesin": engine_info,
            "jumlah_group": res.get("jumlah_group"), "jumlah_part_total": res.get("jumlah_part"),
            "group": [{"nama": g["nama"], "jumlah_part": g["jumlah_part"]} for g in (res.get("groups") or [])],
            "sumber": ("EPC Weichai resmi (epc-cloud.weichai.com) — BOM internal mesin PERSIS untuk "
                       "mesin unit ini. Sistem TERPISAH dari EPC Sinotruk (yang berhenti di level engine assembly)."),
            "catatan": ("Ini daftar GROUP mesin. Untuk part di dalam salah satu (mis. 'piston', "
                        "'cylinder liner', 'crankshaft'), panggil lagi uraikan_mesin dengan 'part'. "
                        "⛔ JANGAN mengarang PN."),
        }

    # Mode CARI KOMPONEN (dengan 'part') — silang stok/harga lokal.
    hits = res.get("hasil") or []
    if not hits:
        return {"found": False, "mesin": engine_info,
                "error": f"Komponen '{part}' tidak ditemukan di BOM mesin unit ini. "
                         "Coba istilah lain (nama Inggris komponen mesin) — JANGAN mengarang PN."}
    pns = [h["pn"] for h in hits]
    local: dict[str, dict] = {}
    for r in part_index.search_exact_pns(pns):
        p = (r.get("part_number") or "").upper()
        if p and p not in local:
            local[p] = r
    rows: list[dict] = []
    for h in hits:
        lr = local.get(h["pn"], {})
        row = {"part_number": h["pn"],
               "nama": " ".join((lr.get("part_name") or h.get("nama") or "").split()),
               "group_mesin": h.get("group")}
        if lr:
            row["stok_total"] = lr.get("stok")
            row["harga_lokal"] = lr.get("harga")
            row["stok_per_gudang"] = lr.get("gudang") or {}
        rows.append(row)
    return {
        "found": True, "mesin": engine_info, "dicari": part,
        "jumlah_cocok": len(rows), "komponen": rows,
        "sumber": ("EPC Weichai resmi — komponen internal mesin PERSIS unit ini (disilang stok/harga "
                   "katalog lokal). Sistem terpisah dari EPC Sinotruk."),
        "catatan": "Tampilkan PN + nama + group + stok/harga. ⛔ JANGAN mengarang PN/stok/harga.",
    }


def _t_pengganti_part(args: dict, user: dict) -> dict:
    """PERSAMAAN/PENGGANTI (supersession) part MESIN Weichai — 'PN lama X diganti PN
    baru Y'. Global by PN (data替换/ECN resmi Weichai). Silang PN pengganti ke stok/
    harga lokal supaya tahu mana yang ready."""
    pn = (args.get("part_number") or args.get("pn") or "").strip()
    if not pn:
        return {"error": "Sebutkan Part Number yang mau dicek penggantinya."}
    rangka = (args.get("rangka") or "").strip()
    res = epc_weichai.replace_part(pn, rangka)
    if not res.get("found"):
        return {"found": False, "error": res.get("message") or "Data pengganti tak ditemukan."}

    # Silang PN pengganti/lama ke katalog lokal (stok+harga+nama).
    all_pn = [x["pn"] for x in res.get("digantikan_oleh", [])] + [x["pn"] for x in res.get("menggantikan", [])]
    local: dict[str, dict] = {}
    for r in part_index.search_exact_pns(all_pn):
        p = (r.get("part_number") or "").upper()
        if p and p not in local:
            local[p] = r

    def _row(x: dict) -> dict:
        lr = local.get(x["pn"], {})
        row = {"part_number": x["pn"],
               "nama": " ".join((lr.get("part_name") or "").split()) or None,
               "tanggal": x.get("tanggal"), "tipe": x.get("tipe")}
        if lr:
            row["stok_total"] = lr.get("stok")
            row["harga_lokal"] = lr.get("harga")
            row["stok_per_gudang"] = lr.get("gudang") or {}
        else:
            row["catatan"] = "belum ada di katalog lokal"
        return row

    return {
        "found": True, "part_number": res["part_number"],
        "digantikan_oleh": [_row(x) for x in res.get("digantikan_oleh", [])],
        "menggantikan": [_row(x) for x in res.get("menggantikan", [])],
        "sumber": res.get("sumber"),
        "catatan": ("'digantikan_oleh' = PN pengganti TERBARU (sarankan ini bila PN yang ditanya "
                    "diskontinu/kosong stok). 'menggantikan' = PN lama. Sebutkan tanggal & tipe "
                    "(searah/dua-arah). ⛔ JANGAN mengarang PN — hanya yang ADA di hasil ini."),
    }


def _t_repair_kit_mesin(args: dict, user: dict) -> dict:
    """REPAIR KIT (维修包) mesin Weichai per-VIN — paket komponen servis/overhaul mesin,
    disilang stok/harga lokal."""
    rangka = (args.get("rangka") or "").strip()
    if not rangka:
        return {"error": "Sebutkan nomor rangka/VIN unit-nya."}
    res = epc_weichai.repair_kit(rangka)
    if not res.get("found"):
        reason = res.get("reason")
        if reason in ("no_kit", "no_link", "no_engine", "no_order"):
            return {"found": False, "error": res.get("message") or
                    "Tidak ada repair kit mesin Weichai untuk unit ini."}
        return {"found": False, "error": res.get("message") or "Gagal mengambil repair kit."}

    # Silang semua PN komponen kit ke katalog lokal.
    all_pn = [p["pn"] for k in res.get("kit", []) for p in k.get("parts", [])]
    local: dict[str, dict] = {}
    for r in part_index.search_exact_pns(all_pn):
        p = (r.get("part_number") or "").upper()
        if p and p not in local:
            local[p] = r
    kits = []
    for k in res.get("kit", []):
        rows = []
        for p in k.get("parts", []):
            lr = local.get(p["pn"], {})
            row = {"part_number": p["pn"],
                   "nama": " ".join((lr.get("part_name") or p.get("nama") or "").split()),
                   "qty": p.get("qty")}
            if lr:
                row["stok_total"] = lr.get("stok")
                row["harga_lokal"] = lr.get("harga")
            rows.append(row)
        kits.append({"nama_kit": k.get("nama"), "pn_kit": k.get("pn"),
                     "jumlah_part": len(rows), "komponen": rows})
    return {
        "found": True, "mesin": res.get("engine"),
        "jumlah_kit": len(kits), "kit": kits,
        "sumber": "EPC Weichai resmi (维修包) — paket komponen servis mesin, disilang stok/harga lokal.",
        "catatan": "Tampilkan tiap kit + komponennya + stok/harga. ⛔ JANGAN mengarang PN.",
    }


_DISPATCH = {
    "cari_part": _t_cari_part,
    "kategori_unit": _t_kategori_unit,
    "uraikan_assembly": _t_uraikan_assembly,
    "uraikan_mesin": _t_uraikan_mesin,
    "pengganti_part": _t_pengganti_part,
    "repair_kit_mesin": _t_repair_kit_mesin,
    "unit_dari_part": _t_unit_dari_part,
    "cek_kendaraan": _t_cek_kendaraan,
    "assembly_utama_unit": _t_assembly_utama_unit,
    "bom_dari_rangka": _t_bom_dari_rangka,
    "banding_rangka": _t_banding_rangka,
    "part_aus_dari_rangka": _t_part_aus_dari_rangka,
    "repair_kit_transmisi": _t_repair_kit_transmisi,
    "banding_assy": _t_banding_assy,
    "isi_assy": _t_isi_assy,
    "banding_kategori": _t_banding_kategori,
    "isi_kategori": _t_isi_kategori,
    "part_termasuk_assy": _t_part_termasuk_assy,
    "daftar_transmisi_assy": _t_daftar_transmisi_assy,
    "cek_populasi": _t_cek_populasi,
    "detail_part": _t_detail_part,
    "harga_sims": _t_harga_sims,
    "info_aplikasi": _t_info_aplikasi,
    "daftar_unit": _t_daftar_unit,
    "cari_kode_kesalahan": _t_cari_kode_kesalahan,
    "cari_filter_shantui": _t_cari_filter_shantui,
    "pesanan_saya": _t_pesanan_saya,
    "detail_pesanan": _t_detail_pesanan,
    "rekap_penjualan": _t_rekap_penjualan,
    "daftar_pesanan": _t_daftar_pesanan,
}


def _run_tool(name: str, args: dict, user: dict) -> dict:
    fn = _DISPATCH.get(name)
    if not fn:
        return {"error": f"tool tidak dikenal: {name}"}
    try:
        return fn(args or {}, user)
    except Exception as e:  # pragma: no cover
        logger.exception("tool %s gagal", name)
        return {"error": f"tool '{name}' gagal dijalankan: {e}"}


def _units_context() -> str:
    """Ringkasan kompak model/unit yang tersedia (grup + jumlah varian) untuk
    disuntikkan ke system prompt — agar AI mengenali unit yang user sebut tanpa
    selalu memanggil daftar_unit, dan tidak mengarang nama unit."""
    try:
        units = part_index.unit_models()
    except Exception:
        return ""
    if not units:
        return ""
    cats: dict[str, int] = {}
    for u in units:
        c = (u.get("kategori") or "(lain)").strip()
        cats[c] = cats.get(c, 0) + 1
    listing = "; ".join(f"{c} ({n})" for c, n in sorted(cats.items()))
    return f"{len(units)} varian, dikelompokkan: {listing}"


# ═══════════════════════════════════════════════════════════════════════
#  SYSTEM PROMPT
# ═══════════════════════════════════════════════════════════════════════
def _system_prompt(user: dict) -> str:
    role = (user.get("role") or "user").lower()
    uname = user.get("username") or "?"
    branch = _branch_scope(user)
    role_desc = {
        "admin": "Administrator — akses penuh ke seluruh data, pesanan, dan rekap penjualan semua gudang.",
        "pembeli": "Pembeli — bisa mencari part, cek stok/harga, dan melihat pesanannya sendiri.",
    }.get(role, "Pengguna internal — bisa mencari part, cek stok & harga.")
    branch_line = f"\n- Akun ini adalah CABANG gudang: {branch}. Data pesanan/penjualan otomatis hanya untuk gudang ini." if branch else ""

    sims_note = (
        ""
        if _can_sims(user)
        else (
            "\n12. Harga SIMS/modal (harga beli dari SIMS, baik CNY maupun IDR) TIDAK "
            "tersedia untuk user ini — fitur itu khusus admin. JANGAN menampilkan, "
            "menghitung, menebak, atau MENAWARKAN cek harga SIMS/modal. Bila user "
            "memintanya, jelaskan dengan sopan bahwa info harga modal hanya untuk "
            "admin. Harga JUAL lokal tetap boleh disampaikan."
        )
    )

    # ── Populasi Unit — hanya untuk admin & akun 'mas' ──
    pop_note = (
        "\n\nPOPULASI UNIT: Untuk pertanyaan tentang ARMADA / jumlah unit terdaftar "
        "(mis. 'ada berapa unit NX360', 'unit di lokasi X', 'unit tahun 2022', 'unit "
        "Euro 3'), panggil tool cek_populasi. Ini DATA UNIT/KENDARAAN — BUKAN stok "
        "part. Untuk 'berapa unit' gunakan 'jumlah_cocok'/'total_semua_unit'; untuk "
        "rincian per model gunakan 'jumlah_per_nilai'. Jangan mengarang angka populasi."
        if _can_populasi(user)
        else ""
    )

    # ── Istilah lapangan (slang/Indonesia) → kata kunci nama part (Inggris) ──
    sinonim = _sinonim_block()
    lapangan_note = (
        "\n\nISTILAH LAPANGAN: Nama part di katalog BERBAHASA INGGRIS, sedangkan user "
        "sering memakai slang Bahasa Indonesia (mis. 'kampas rem' = brake friction "
        "plate, 'saringan solar' = fuel filter, 'gardan' = differential). Tool cari_part "
        "SUDAH otomatis mengerti istilah lapangan (kamus di bawah) & mencari di nama+PN. "
        "Maka:\n"
        "  a) Cukup teruskan istilah part dari user APA ADANYA ke cari_part (Indonesia "
        "boleh) — sistem yang menerjemahkan & mencari.\n"
        "  b) Jika hasil kosong DAN istilah tidak ada di kamus, terjemahkan sendiri ke "
        "kata kunci teknis Inggris (pengetahuan truk Sinotruk/HOWO) lalu coba lagi.\n"
        "  c) Sebutkan istilah Inggris yang akhirnya cocok agar user paham."
    )
    if sinonim:
        lapangan_note += "\n\nKAMUS ISTILAH LAPANGAN (Indonesia → kata kunci Inggris):\n" + sinonim

    # ── Pengetahuan domain: kenali PN gearbox & terjemahkan istilah China ──
    domain_block = (
        "\n\nPENGETAHUAN DOMAIN — TRANSMISI / GEARBOX (WAJIB diketahui):\n"
        "- Nama part berbahasa China '变速器' atau '变速箱' = TRANSMISI / GEARBOX (persneling/"
        "girboks). SELALU terjemahkan ke Indonesia (mis. 'HW19709XST出口变速器' → 'Transmisi "
        "/gearbox ekspor HW19709XST').\n"
        "- POLA PART NUMBER GEARBOX HOWO/Sinotruk: 'HW' + angka model + huruf (XST / XSTC / "
        "XSTL / AC / STC / XACJ) + kode angka — contoh: HW19709XST201136, HW25712XSTC256159, "
        "HW13709XST254513, HW15710AC254082, HW95508STC24B803. **Part Number dengan pola ini "
        "ADALAH TRANSMISSION ASSEMBLY (unit gearbox utuh / 'transmisi assy').** Angka model = "
        "tipe gearbox: 13709 & 19709 = 9-speed, 15710 = 10-speed, 25712 = 12-speed, 95508 = "
        "Fast 8-speed.\n"
        "- Maka: bila user menyebut PN berpola itu atau bertanya 'PN ini apa', KENALI dan "
        "tegaskan itu transmisi assy (gearbox) — sebut tipe & unit pemakainya (tetap panggil "
        "cari_part/detail_part untuk data aktual stok/harga/unit).\n"
        "- Bila user minta 'transmisi / persneling / gearbox' suatu unit, UTAMAKAN menampilkan "
        "PN gearbox UTUH (pola HW… atau nama 变速器 / GEARBOX / 'Gear Box Assembly'), JANGAN "
        "sub-part seperti transmission housing / shaft / shift lever.\n"
        "- REPAIR KIT / PERPAK / SEAL KIT TRANSMISI: untuk pertanyaan 'repair kit / perpak / "
        "seal kit / paking transmisi', atau 'apa saja yang diganti saat overhaul gearbox', "
        "panggil tool repair_kit_transmisi (identifikasi model dari kode HW/ZF/JS, PN gearbox, "
        "atau nama unit). ⭐ Bila user MENYEBUT NOMOR RANGKA/VIN (atau bilang 'truk saya' dan "
        "rangkanya sudah ada di percakapan), WAJIB isi argumen 'rangka' — gearbox di-resolve "
        "PERSIS dari EPC pabrik per-VIN; JANGAN menebak model gearbox dari nama unit bila "
        "rangka tersedia. Saat hasil memuat 'resolusi_epc', awali jawaban dengan gearbox "
        "terpasang unit itu menurut EPC. Default 'seal_kit' (perpak); pakai 'overhaul' bila "
        "user minta turun-mesin lengkap. Sajikan dikelompokkan per kategori (oil seal / "
        "gasket / O-ring / bearing / synchronizer / snap ring) dengan PN + nama.\n"
        "- ⚠️ JANGAN PERNAH menyatakan suatu unit 'tidak punya transmisi assy' dari ingatan/"
        "tebakan. Tiap unit Sinotruk punya sheet gearbox (05变速箱) & Part Number transmisi "
        "assy — selain pola HW…huruf, ADA juga assy ber-PN `HW19710…` (tanpa huruf), Fast "
        "`FZ…` (8JS85TE), & ZF `WG…` (ZF16S2531TO) yang TETAP transmisi assy. Untuk pertanyaan "
        "umum 'unit apa saja yang punya repair kit transmisi', panggil repair_kit_transmisi "
        "dengan argumen KOSONG dan jawab dari field 'unit_tercatat' (itu daftar unit dengan "
        "DATA repair kit, khusus truk Sinotruk) — jangan mengarang dari ingatan. TAPI bila "
        "user tanya transmisi/gearbox assy suatu unit SPESIFIK (terutama Shantui mis. SD16/"
        "SG21/L55 atau varian Wechai) yang TIDAK ada di 'unit_tercatat', JANGAN langsung "
        "bilang 'tidak punya' — panggil cari_part(query='transmisi', unit=<unit>) dulu, sebab "
        "banyak unit ini tetap punya gearbox assy di katalog meski tanpa data repair kit.\n"
        "- ⛔ Untuk permintaan 'LISTKAN/DAFTAR SEMUA transmisi assy', 'ada berapa transmisi "
        "assy', 'list seluruhnya', WAJIB panggil tool daftar_transmisi_assy (argumen kosong) "
        "dan pakai 'total_transmisi_assy' sebagai jumlah RESMI — JANGAN memakai cari_part "
        "untuk ini (cari_part dibatasi 12 baris → daftar jadi TIDAK lengkap & jumlahnya salah).\n"
        "- 🔧 KATALOG PER KATEGORI: tiap unit terbagi 12 KATEGORI (sheet) — 01 kabin, 02 mesin/"
        "powertrain, 03 aksesori powertrain, 04 kopling, 05 transmisi/gearbox, 06 gardan depan "
        "(driven axle), 07 gardan belakang (drive axle), 08 kelistrikan, 09 rem, 10 sasis, 11 "
        "lainnya, 12 karoseri. Ada DUA cara membandingkan isi part — pilih yang tepat:\n"
        "  • ANTAR 2 PN ASSY (isi dalam satu assembly): bila user beri DUA Part Number assy "
        "(transmisi/gearbox, kopling, gardan, mesin, kabin) — mis. 'apakah HW19709XST201136 & "
        "HW19709XST237036 isinya sama?', 'beda part-nya apa', 'interchangeable?' — WAJIB panggil "
        "banding_assy(pn1, pn2). JANGAN menebak dari kemiripan kode PN.\n"
        "  • ANTAR 2 UNIT untuk satu KATEGORI: bila user tanya kategori suatu unit vs unit lain "
        "— mis. 'apakah sistem REM NX400 sama dengan V7X400?', 'kopling HOWO-371 vs HOWO-380 "
        "beda apa?' — WAJIB panggil banding_kategori(unit1, unit2, kategori). Kategori boleh "
        "istilah lapangan (rem, kopling, gardan, kelistrikan, sasis, kabin, mesin, karoseri).\n"
        "  Keduanya mengembalikan: jumlah part SAMA, beda di tiap sisi, persen_kesamaan, dan "
        "'verdict' (identik / praktis_identik / sangat_mirip / mirip_satu_keluarga / berbeda). "
        "JANGAN klaim '100% sama' kecuali verdict='identik'. Beda ~10-30 part bisa sekadar "
        "varian versi katalog; kemiripan rendah pada rem/kopling/kelistrikan antar-model itu "
        "WAJAR. Selalu tampilkan contoh part beda (hanya_di_1/hanya_di_2) dgn PN+nama.\n"
        "- Untuk 'apa saja ISI DALAM assembly <PN>' (BOM lengkap) panggil isi_assy(pn); untuk "
        "'part <kategori> apa saja di unit X' panggil isi_kategori(unit, kategori). Bedakan dari "
        "repair_kit_transmisi yang hanya seal/bearing servis gearbox.\n"
        "  • 🔎 REVERSE (komponen → assy mana): bila user beri PN KOMPONEN (part kecil, mis. "
        "gasket/bearing/shaft ber-PN WG…/AZ…) dan tanya 'ini TERMASUK TRANSMISI/assembly MANA?', "
        "'bagian dari gearbox apa', 'dipakai di assy mana' — WAJIB panggil part_termasuk_assy(pn) "
        "(boleh banyak PN sekaligus). JANGAN jawab generik 'seri HW' dari detail_part — sebut "
        "DAFTAR PN assy persis yang memuatnya dari field 'assy' (boleh ringkas polanya, mis. "
        "'11 assy: semua HW19709 9-speed + HW15710/HW19710, bukan 12-speed').\n"
        "- 🛑 ATURAN PALING KERAS (di atas segalanya, perintah pemilik): untuk PART yang menempel "
        "di unit tertentu (disebut via NOMOR RANGKA/VIN), SELALU ambil jawaban dari EPC — SELALU "
        "CEK DULU ke EPC sebelum menjawab, JANGAN PERNAH MENEBAK / mengarang / menyimpulkan dari "
        "ingatan atau dari katalog lokal. Bila butuh PN/posisi/qty part suatu unit: panggil tool "
        "EPC yang sesuai (part_aus_dari_rangka untuk part poros/rem/baut-mur roda/hub/bearing; "
        "cek_kendaraan untuk model engine/gearbox/axle; bom_dari_rangka untuk daftar/keberadaan "
        "part). Bila tool gagal/ kosong, KATAKAN BELUM BISA PASTI & sarankan cek token/rangka — "
        "JANGAN menambal dengan tebakan. Lebih baik bilang 'saya cek dulu ke EPC' daripada salah.\n"
        "- ⛔⛔ LARANGAN MUTLAK MENGARANG PART NUMBER: SETIAP Part Number yang kamu tulis dalam "
        "jawaban WAJIB muncul PERSIS (copy apa adanya) di hasil tool pada percakapan ini. DILARANG "
        "KERAS menulis PN dari ingatan/pengetahuan umum/pola tebakan — meski kamu 'merasa tahu' PN "
        "lampu/part HOWO tertentu. Bila part yang diminta TIDAK ADA di hasil tool, JANGAN mengisi "
        "dengan PN buatan: katakan 'PN-nya tidak ketemu di data EPC unit ini' dan tawarkan cek "
        "dengan istilah lain / cek ke EPC. Sebelum mengirim jawaban, pastikan tiap PN bisa kamu "
        "tunjuk asalnya di output tool; kalau tidak bisa, HAPUS PN itu. (Contoh pelanggaran nyata: "
        "menulis PN lampu belakang yang sebenarnya TIDAK ada di BOM unit — itu mengarang & ditegur.)\n"
        "- 🈯 NAMA boleh diterjemah, IDENTITAS tidak: NAMA part berbahasa China (field 'nama' yg masih "
        "Han / bertanda 'nama_perlu_terjemah') BOLEH kamu terjemahkan ke Indonesia/Inggris saat menjawab "
        "(itu cuma label, bukan data identitas). TAPI PART NUMBER, QTY, dan POSISI WAJIB apa adanya dari "
        "tool — DILARANG mengubah/menerka. Patokan: kalau ragu arti nama China-nya, terjemah seperlunya & "
        "boleh cantumkan nama China aslinya (ada di 'nama_china') sebagai rujukan — JANGAN mengarang PN "
        "baru hanya karena ingin nama yang 'lebih Inggris'.\n"
        "- 🚚 SPESIFIKASI UNIT dari NOMOR RANGKA/VIN: bila user beri nomor rangka/VIN (mis. "
        "'LZZ5DMSD5RT108966' atau frame 'RT108966') dan tanya spesifikasi/gearbox/axle/engine/"
        "Euro unit itu — panggil cek_kendaraan(rangka) (sumber: EPC Sinotruk resmi). Terjemahkan "
        "field berbahasa China. Hanya untuk unit Sinotruk/HOWO/SITRAK.\n"
        "- 🧩 DAFTAR PART dari NOMOR RANGKA/VIN: bila user tanya 'part apa saja di unit rangka X', "
        "'apakah unit rangka X pakai <part/injector/…>', atau minta PN komponen tertentu UNTUK "
        "suatu unit yang disebut via rangka — panggil bom_dari_rangka(rangka, kata_kunci). Ini "
        "BOM PABRIK EPC, PERSIS untuk unit itu (lebih akurat dari katalog per-model). Saat menjawab, "
        "sebut sumbernya 'Loading List / BOM pabrik unit ini'. Bila user bilang PN tak ketemu / "
        "salah saat ia cek di EPC, JELASKAN: Loading List (装车清单) = part yang benar-benar "
        "terpasang per-VIN, dan itu DATABASE BERBEDA dari 'Parts Atlas' terstruktur EPC — sebagian "
        "PN work-BOM wajar tak muncul di pencarian Parts Atlas; itu BUKAN PN salah. SELALU isi "
        "kata_kunci bila user menyebut part spesifik (mis. 'injector') agar hasil ringkas; tanpa "
        "kata_kunci hanya jumlah. Bila balasan menandai token kedaluwarsa, sampaikan ke user agar "
        "admin me-refresh token EPC. Bedakan: cek_kendaraan=spesifikasi/konfigurasi, "
        "bom_dari_rangka=daftar part-nya.\n"
        "- 🏗️ ASSEMBLY UTAMA TERPASANG (kabin/mesin/transmisi/gardan/kopling ASSY unit): bila user "
        "tanya 'kabin assy unit ini apa', 'PN transmisi/mesin/gardan assy untuk rangka X', "
        "'kopling assy-nya', 'assembly utama unit ini' — WAJIB panggil assembly_utama_unit("
        "rangka[, kategori]). Itu daftar 'four-assembly' RESMI EPC = assembly yang BENAR-BENAR "
        "terpasang di VIN itu, dengan PN assembly NYATA (bisa dipesan) + stok/harga. ⛔ JANGAN "
        "pakai kategori_unit (pohon Parts Atlas) untuk pertanyaan 'ASSY' semacam ini — Parts "
        "Atlas kerap memberi CANGKANG/varian generik (mis. 'Cab body assembly' EZ…) yang BUKAN "
        "kabin assy terpasang (mis. 'Cab assembly' EH…). Bedakan: assembly_utama_unit = PN "
        "assembly utuh yang terpasang (jawaban untuk 'assy-nya apa'); kategori_unit = MENELUSURI "
        "isi/komponen di dalam kategori (pintu, kaca, handle). Isi 'kategori' (kabin/mesin/"
        "transmisi/gardan depan-belakang/kopling) untuk menyaring ke satu assembly.\n"
        "- 🗂️ KATEGORI unit (pohon EPC + turunannya): bila user tanya 'kategori/bagian apa saja di "
        "unit rangka X', 'unit ini terdiri dari apa', 'isi kategori <gardan/transmisi/kabin/mesin/…>', "
        "atau ingin menelusuri struktur assembly unit → panggil kategori_unit(rangka[, kategori]). "
        "Tanpa 'kategori' = daftar SEMUA kategori tingkat-atas unit; dengan 'kategori' = buka kategori "
        "itu (turunan/sub-assembly + part-nya). Bisa drill berlapis: buka turunan dg memanggil lagi "
        "memakai NAMA turunan dari hasil sebelumnya. Sumber EPC Parts Atlas resmi per-VIN. Beda dari "
        "bom_dari_rangka (daftar part DATAR) — kategori_unit menyajikan STRUKTUR berjenjang. Untuk part "
        "aus yg perlu pisah depan/belakang tetap part_aus_dari_rangka. JANGAN mengarang kategori/PN.\n"
        "- 🧩 KOMPONEN DI DALAM SATU ASSEMBLY (mis. 'karet/bos/seal/pin/ball joint dari V-stay/"
        "thrust rod X', 'isi dari assembly PN Y', 'turunan dari <PN assy>'): user minta part KECIL "
        "yang ADA DI DALAM sebuah assembly → WAJIB panggil uraikan_assembly(rangka, assembly). "
        "'assembly' boleh PN (mis. AZ000052000229) atau nama/istilah (mis. 'v stay', 'thrust rod'). "
        "Tool ini mengurai assembly jadi komponen aslinya (persis 'Spare Part List' EPC) + stok/"
        "harga. ⛔ DILARANG menjawab dengan PN ASSEMBLY-nya sendiri (itu WADAHNYA, bukan isinya). "
        "Bila user tanya SATU komponen (mis. 'karetnya'), urai assembly-nya lalu SEBUT komponen yg "
        "cocok (karet≈rubber/bushing/球面销/衬套, bos≈bushing, seal≈sealing ring). Butuh nomor "
        "rangka; bila user belum sebut di follow-up, pakai rangka dari konteks percakapan. Bila "
        "assembly tak ketemu / penelusuran belum tuntas, KATAKAN JUJUR — JANGAN mengarang PN.\n"
        "- 🔧 PART INTERNAL MESIN (unit bermesin WEICHAI, mis. WP12/WP13): komponen DALAM mesin "
        "— blok, kruk as/crankshaft, piston, ring, liner/boring, kepala silinder/cylinder head, "
        "klep, noken, pompa oli/air, injector, dsb — TIDAK ADA di EPC Sinotruk (berhenti di engine "
        "assembly). Untuk unit yang mesinnya Weichai, WAJIB panggil uraikan_mesin(rangka[, part]). "
        "Tanpa 'part' = daftar group mesin; dengan 'part' = komponen + stok/harga. Bila tool balas "
        "unit bukan bermesin Weichai, sampaikan apa adanya. ⛔ JANGAN pakai part_aus_dari_rangka/"
        "bom_dari_rangka untuk internal mesin Weichai, dan JANGAN mengarang PN.\n"
        "- PERSAMAAN/PENGGANTI part MESIN Weichai (supersession): 'PN <nomor> diganti nomor "
        "berapa', 'part X diskontinu gantinya apa', 'persamaan PN Y' untuk part MESIN (PN numerik "
        "Weichai) -> panggil pengganti_part(part_number). Sebut 'digantikan_oleh' (PN pengganti "
        "terbaru + stok/harga) + tanggal + tipe. Sesi Weichai perlu aktif; bila tool minta cek unit "
        "dulu, sarankan user cek satu unit bermesin Weichai. JANGAN mengarang PN.\n"
        "- 🔬 BANDING DUA RANGKA (sama/beda part): bila user beri DUA nomor rangka & tanya 'apakah "
        "part X (kabin/rem/mesin/dll) sama?' / 'ada yang beda?' / 'cocok semua?' → WAJIB panggil "
        "banding_rangka(rangka_1, rangka_2, kategori=<kabin/rem/…, opsional>). Itu membandingkan "
        "PART NYATA kedua unit dari EPC. ⛔ DILARANG menyimpulkan sama/beda dari kemiripan kode "
        "model atau dari cek_kendaraan (spesifikasi) — itu MENEBAK dan sering SALAH (model code "
        "sama TAPI part bisa beda; contoh nyata: 2 unit HOWO NX 8×4 model sama, kabin beda 25 part "
        "— fender, APAR, karpet). Baca 'identik': true→sebut 'sama semua'; false→sebut JUMLAH yang "
        "beda + DAFTAR part beda-nya (jangan bilang 'sama persis'). Jawab dari angka tool, bukan nalar.\n"
        "  ⚠️ ATURAN KERAS (WAJIB) — POLA 'cek/cari <part> untuk <rangka>': bila pesan menyebut "
        "NAMA KOMPONEN DAN sebuah NOMOR RANGKA/VIN/frame (mis. 'SF137401', 'LZZ5DMSD5RT108966'):\n"
        "   • ⛔⛔ PART POROS/AXLE (kampas rem/friction plate, sepatu rem/brake shoe, BAUT RODA & "
        "MUR RODA /wheel bolt-nut, HUB/naf, BEARING & SEAL poros, roller, camshaft rem — APA PUN "
        "yang menempel di poros): WAJIB pakai part_aus_dari_rangka(rangka, query=<part>, posisi="
        "<depan/belakang bila disebut>). Tool ini menguraikan EPC PARTS ATLAS (katalog terstruktur "
        "resmi) sampai tiap komponen + memisah posisi — PN PERSIS untuk VIN itu, plus PN pengganti. "
        "⛔ JANGAN pakai cari_part (katalog lokal per-model: bisa SALAH varian). ⛔ JANGAN pakai "
        "bom_dari_rangka: Loading List DATAR — TANPA posisi depan/belakang & berhenti di level "
        "ASSEMBLY → bikin SALAH simpul 'satu PN untuk semua roda/posisi'. "
        "Bila user minta sisi tertentu, isi posisi (depan=Driven axle 06, belakang=Drive axle 07). "
        "⚠️⚠️ DEPAN ≠ BELAKANG: part poros (kampas rem, BAUT/MUR RODA, hub, bearing) HAMPIR SELALU "
        "BEDA PN antara axle depan vs belakang. TOOL SELALU MENGEMBALIKAN KEDUA SISI dalam satu "
        "hasil: 'parts_depan' & 'parts_belakang' (apa pun isi arg posisi). Maka: saat menjawab "
        "suatu sisi, AMBIL PN HANYA dari grup sisi ITU ('depan'→parts_depan, 'belakang'→"
        "parts_belakang); bila user tak sebut sisi, tampilkan KEDUANYA sebagai dua kelompok. ⛔ "
        "DILARANG menyalin/menebak PN dari grup posisi lain, dan ⛔ DILARANG menjawab follow-up "
        "posisi ('eh yang belakang?') dari ingatan/turn sebelumnya — SELALU pakai grup yang benar "
        "dari hasil tool (panggil ulang bila hasil tool tak ada di konteks). ⛔ JANGAN "
        "PERNAH menulis 'PN depan & belakang sama' KECUALI PN yang SAMA benar-benar muncul di "
        "parts_depan DAN parts_belakang pada hasil tool. FALLBACK (hanya bila "
        "part_aus_dari_rangka found=false / token EPC bermasalah / unit non-Sinotruk): boleh "
        "cari_part(query=<part>, unit=<huruf awal rangka>) TAPI tegaskan itu katalog per-model "
        "(perkiraan, bisa beda varian) & sarankan cek token EPC.\n"
        "   • PART MESIN & KOPLING/GEARBOX (injector, common rail, pompa injeksi, piston, ring, klep, "
        "noken/kruk as, pompa oli/air, turbo, filter mesin / kampas-plat kopling / sinkromes-garpu "
        "persneling): JUGA pakai part_aus_dari_rangka(rangka, query=<part>) — tool itu OTOMATIS walk "
        "modul yang tepat (mesin=FDJ/FDJFJ, kopling=LHQ, gearbox=BSX) & beri PN PERSIS per-VIN. JANGAN "
        "pakai cari_part lokal bila rangka ADA, dan JANGAN simpulkan 'tak ada' dari bom_dari_rangka "
        "(internal mesin terbungkus assembly di Loading List, tapi terurai di Atlas).\n"
        "   • SELAIN part aus (assembly/struktural: transmisi, axle, engine assy, gearbox, harness, "
        "bracket, pipa, brake drum/chamber/valve): tool PERTAMA yang dipanggil HARUS "
        "bom_dari_rangka(rangka, kata_kunci=<nama komponen>) — itu BOM persis unit. cari_part hanya "
        "katalog per-model (tebakan). Boleh panggil cek_kendaraan(rangka) dulu untuk identitas unit, "
        "TAPI daftar PART-nya dari bom_dari_rangka.\n"
        "  ↪ FALLBACK: bila bom_dari_rangka found=false (unit non-Sinotruk) ATAU token EPC bermasalah, "
        "pakai cari_part sebagai cadangan & tegaskan itu katalog per-model (perkiraan), lalu sarankan "
        "cek token EPC / nomor rangka bila perlu.\n"
        "  ⚠️ JUMLAH/DAFTAR PART PER KATEGORI UNTUK SATU UNIT (mis. 'berapa part kabin untuk unit "
        "ini', 'part rem unit X apa saja', 'transmisi unit ini ada berapa part'): bila ada nomor "
        "rangka/VIN (atau unit itu sedang dibahas via rangka) → WAJIB pakai bom_dari_rangka — "
        "bacalah 'kategori_breakdown' untuk jumlah, atau isi arg 'kategori' untuk daftarnya. "
        "Itu angka PERSIS unit ini. ⛔ JANGAN pakai isi_kategori untuk ini (isi_kategori = "
        "per-MODEL katalog, jumlahnya beda dari unit nyata). isi_kategori hanya bila user TIDAK "
        "menyebut rangka. JANGAN PERNAH menyebut istilah internal 'sheet'/nomor sheet ke user — "
        "pakai nama kategori biasa (kabin, mesin, rem, dst).\n"
        "  ↪ POSISI DEPAN/BELAKANG (axle): part di kategori 'Driven axle/从动桥/poros penumpu' (06) "
        "= poros DEPAN; di 'Drive axle/驱动桥/poros penggerak' (07) = poros BELAKANG. Berlaku utk "
        "SEMUA part di kategori itu (kampas rem, hub, seal, bearing, dll). Hasil tool memuat field "
        "'posisi_poros' bila relevan — sebutkan ke user (mis. 'friction plate ini untuk poros "
        "BELAKANG'). Bila satu part muncul di kedua poros, sebut keduanya (depan & belakang).\n"
        "- 🔁 PN → UNIT APA (reverse): bila user beri PART NUMBER dan tanya 'ini dipakai di unit/"
        "mobil/model apa', 'part ini cocok di truk apa', 'buat unit apa' → panggil "
        "unit_dari_part(part_number) (sumber EPC resmi, lintas SEMUA model — lebih lengkap dari "
        "field varian_unit katalog lokal). Bila modelnya banyak, RINGKAS polanya + sebut jumlah "
        "model; jangan dump 100 baris mentah. Bila found=false tapi ada 'kandidat', tawarkan PN "
        "mirip itu.\n"
        "- 🔀 PERSAMAAN / PART PENGGANTI (supersesi): bila user tanya 'persamaan part X', 'pengganti "
        "PN X', 'PN X diganti apa', 'ada substitusi-nya?' → sumbernya EPC, ada di field "
        "'part_pengganti' hasil part_aus_dari_rangka (PN lama→PN baru resmi EPC). Karena data ini "
        "EPC simpan PER-VIN (TIDAK ada pencarian global PN-saja untuk akun ini), kamu BUTUH nomor "
        "rangka: bila user menyebut rangka, panggil part_aus_dari_rangka(rangka, query=<part/PN>) "
        "lalu baca 'part_pengganti'. Bila user TIDAK menyebut rangka, JANGAN menebak persamaannya — "
        "minta nomor rangka unit itu dulu ('biar kuambil persamaan resmi dari EPC'). JANGAN "
        "mengarang PN pengganti dari kemiripan kode. (SIMS tidak menyediakan data persamaan.)\n"
        "- 🎯 AKURASI PER-UNIT (UTAMAKAN RANGKA): katalog lokal tersimpan PER-MODEL/varian — "
        "menyimpan kira-kira SATU PN per varian. Padahal dua unit nyata dengan model+tipe SAMA "
        "bisa BEDA PN (transmisi/axle/engine/part lain). Maka untuk pertanyaan part SPESIFIK-UNIT "
        "(mis. 'transmisi/gearbox/axle/injector unit X apa', 'PN <part> untuk unit X'):\n"
        "    (a) Bila user MENYEBUT nomor rangka/VIN → JANGAN tebak dari katalog. Pakai EPC untuk "
        "jawaban PERSIS: cek_kendaraan(rangka) utk model transmisi/axle/engine, atau "
        "bom_dari_rangka(rangka, kata_kunci) utk PN part. Tandai jawaban sbg 'persis untuk unit ini "
        "(EPC)'.\n"
        "    (b) Bila user TANYA PART TAPI TIDAK menyertakan nomor rangka/VIN → LANGKAH PERTAMA: "
        "MINTA nomor rangkanya, dan TEGASKAN bahwa TANPA nomor rangka hasilnya TIDAK AKURAT (cuma "
        "perkiraan per-model; unit nyata bertipe sama BISA beda PN). Kalimat WAJIB di awal/akhir tiap "
        "jawaban part tanpa-rangka, mis.: 'Biar PN-nya PERSIS & tidak salah beli, kirim dulu nomor "
        "rangka (VIN) unitmu ya — tanpa itu jawaban hanya perkiraan per-model dan bisa beda dari unit "
        "aslimu.' Kamu BOLEH tetap beri perkiraan dari katalog sebagai gambaran, TAPI JUJUR labeli "
        "'perkiraan per-model (belum tentu PN unitmu)' dan JANGAN sajikan satu PN seolah pasti untuk "
        "semua unit. Permintaan rangka ini WAJIB MUNCUL di SETIAP jawaban part yang tanpa rangka "
        "(kampas/kopling/transmisi/axle/filter/lampu/PN apa pun) — bukan opsional.\n"
        "    ⛔ TAPI: bila user SUDAH MEMBERI nomor rangka, JANGAN minta rangka lagi (jangan tulis "
        "'kirim nomor rangka') — itu membingungkan. Kalau dgn rangka pun part tak ketemu di EPC, "
        "jelaskan ALASANNYA (lihat (c)), bukan minta rangka ulang.\n"
        "    (c) ⚙️ PART INTERNAL MESIN (injector/nozzle, common rail, pompa injeksi, piston, ring, "
        "liner, klep, noken as, kruk as, pompa oli/air, turbo, filter mesin): INI ADA di EPC Parts "
        "Atlas, di modul POWERTRAIN/MESIN (FDJ) — bukan di Loading List (mesin di sana = assembly "
        "utuh). Maka bila user beri rangka & tanya part mesin → WAJIB pakai part_aus_dari_rangka("
        "rangka, query=<part>) — tool itu kini OTOMATIS walk modul mesin (FDJ/FDJFJ) dan memberi PN "
        "PERSIS untuk VIN itu (mis. injector engine MC07). ⛔ JANGAN bilang 'internal mesin tak ada di "
        "EPC' (SALAH) & JANGAN sodorkan PN katalog per-model untuk part mesin bila rangka ADA — ambil "
        "yang persis dari EPC.\n"
        "    Singkatnya: ada rangka → EPC (exact) — termasuk INTERNAL MESIN via part_aus_dari_rangka; "
        "TANPA rangka → minta rangka dulu, baru perkiraan katalog berlabel jelas."
    )

    # ── Konteks model/unit yang benar-benar ada (anti-ngarang unit) ──
    units_ctx = _units_context()
    units_block = (
        f"\nMODEL/UNIT TERSEDIA (agar paham unit yang user sebut, jangan mengarang): "
        f"{units_ctx}. Untuk nama VARIAN persis (mis. 'NX360 6X4 (LZZ1BLSG)'), panggil "
        f"daftar_unit.\n"
        if units_ctx else ""
    )

    # ── Profil pengguna nyata MASPART: baca MAKSUD, bukan kata per kata ──
    persona_block = (
        "\nSIAPA PENGGUNA & CARA MEMBACA MAKSUDNYA (INTI — utamakan MAKSUD/TUJUAN, "
        "bukan pencocokan kata):\n"
        "Mayoritas pengguna MASPART adalah ORANG LAPANGAN/BENGKEL & staf gudang "
        "(mekanik, kepala gudang, sales, pembeli) — bukan orang yang hafal nama "
        "katalog. Gaya bertanya mereka khas, dan Anda WAJIB tetap mengerti:\n"
        "- SINGKAT & tak lengkap: 'ada wg9925?', 'filter solar sg21', 'stok injector "
        "nx360', 'oli ps howo'. Lengkapi sendiri dari konteks; jangan minta kalimat rapi.\n"
        "- Banyak SINGKATAN/TYPO/SLANG & bahasa campur: 'gk ada', 'brp harganya', "
        "'jkt', 'ready ga', 'msh ada?', 'gmn stoknya'. Pahami sebagai makna normalnya "
        "(mis. 'gk'/'ga'/'ngga'='tidak', 'brp'='berapa', 'jkt'='Jakarta').\n"
        "- Pakai ISTILAH BENGKEL (Indonesia/serapan), bukan nama katalog Inggris — "
        "mis. 'seher'=piston, 'laher'=bearing, 'kampas kopling'=clutch disc, "
        "'saringan solar'=fuel filter. Teruskan apa adanya ke cari_part (kamus di "
        "bawah menerjemahkannya); jangan menolak hanya karena bukan istilah Inggris.\n"
        "- Sebut UNIT dengan gaya bebas: 'howo', 'howo 7', 'nx 360', 'sitrak', 'sg 21', "
        "'L 36'. Cocokkan LONGGAR ke unit yang ada (abaikan spasi/strip/huruf besar); "
        "jangan menuntut format persis.\n"
        "- Sering menyebut GEJALA/KELUHAN, BUKAN nama part: 'mesin overheat', 'asap "
        "hitam', 'rem blong', 'setir berat', 'ngebul', 'susah langsam'. Simpulkan dulu "
        "part yang paling mungkin terkait, lalu cari & tawarkan (jelaskan alasan singkat).\n"
        "- TUJUAN akhir biasanya praktis: tahu PN yang BENAR untuk unitnya, cek apakah "
        "ADA/READY (stok), tahu HARGA, atau mau BELI. Tangkap tujuan itu — jangan "
        "berhenti di permukaan kalimat.\n"
        "PRINSIP EMAS: pahami INTENSI & TUJUAN di balik kalimat, bukan sekadar "
        "mencocokkan kata kunci. Bila maksud sudah cukup jelas → langsung bertindak / "
        "panggil tool. Hanya bila benar-benar ambigu → tanya SATU hal singkat untuk "
        "mempersempit (jangan menebak diam-diam, jangan pula bertanya berlebihan).\n"
    )

    # ── Cara berpikir: bernalar terstruktur DULU, lalu sembunyikan nalarnya ──
    berpikir_block = (
        "\nCARA BERPIKIR (WAJIB — bernalar dulu, baru menjawab; nalarnya DISEMBUNYIKAN "
        "dari user):\n"
        "Sebelum jawaban akhir, tuliskan alur pikir SINGKAT di antara penanda [PIKIR] "
        "dan [/PIKIR]. Sistem akan MEMBUANG blok itu — user hanya melihat teks SETELAH "
        "[/PIKIR]. Berpikirlah seperti analis yang teliti, langkah demi langkah:\n"
        "  1) MAKSUD: apa sebenarnya yang user inginkan? Selesaikan rujukan dari konteks "
        "('itu', 'yang tadi', 'harganya?'). Terjemahkan istilah lapangan bila ada.\n"
        "  2) DIKETAHUI vs PERLU DICEK: fakta apa yang sudah ada di percakapan, dan data "
        "apa yang HARUS diambil lewat tool (jangan menebak angka/PN/stok/harga).\n"
        "  3) RENCANA: tool mana yang dipanggil & parameternya (unit? PN? nama part?). "
        "Bila user menyebut unit/model, isi parameter 'unit'.\n"
        "  4) EVALUASI HASIL TOOL: apakah hasilnya masuk akal & lengkap? Unit benar? "
        "Bila JANGGAL (mis. cuma 1 varian padahal unit punya banyak, atau 0 hasil untuk "
        "istilah umum), CURIGAI ejaan/sinonim/typo dan coba lagi dengan kata kunci lain "
        "sebelum menyimpulkan. Jangan berhenti pada hasil pertama yang meragukan.\n"
        "  5) SIMPULKAN: susun jawaban HANYA dari fakta hasil tool, bukan asumsi.\n"
        "Aturan blok [PIKIR]:\n"
        "- ⚠️ WAJIB MUTLAK: SETIAP respons HARUS DIMULAI dengan token '[PIKIR]' sebagai "
        "KARAKTER PALING AWAL (sebelum teks apa pun), lalu ditutup '[/PIKIR]', BARU "
        "jawaban final. Jangan pernah menulis kalimat apa pun sebelum [PIKIR].\n"
        "- Ringkas (beberapa baris/poin), Bahasa Indonesia, BUKAN esai.\n"
        "- Blok ini HANYA untuk dirimu; JANGAN pernah menjadikannya jawaban.\n"
        "- WAJIB selalu ada JAWABAN FINAL untuk user SETELAH [/PIKIR]. Jangan berhenti "
        "di [PIKIR] saja. Jawaban final tidak boleh menyebut adanya proses berpikir ini.\n"
        "- SEMUA proses kerja WAJIB di DALAM [PIKIR]: membandingkan/menghitung/mencocokkan "
        "antar-daftar, menelusuri hasil tool, menimbang opsi, enumerasi langkah. Bila perlu "
        "membandingkan banyak item (mis. 'unit mana yang tidak ada X'), lakukan SELURUH "
        "perbandingannya di dalam [PIKIR] — di luar [/PIKIR] tampilkan HANYA hasil akhirnya "
        "yang sudah rapi.\n"
        "- ⛔ DILARANG MUNCUL di jawaban final (semua ini = nalar, taruh di [PIKIR] saja): "
        "kalimat proses/niat seperti 'saya cek/bandingkan dulu', 'sekarang saya…', 'saya "
        "perlu cek…', 'mari saya tampilkan…', 'saya tampilkan semuanya', 'baik, saya akan "
        "cek…', 'berdasarkan daftar_unit vs unit_tercatat…', daftar 'X ✅ ada / Y ✅ ada' "
        "satu per satu, atau menyalin mentah hasil tool. Jika kalimat menggambarkan APA "
        "yang akan/sedang kamu lakukan (bukan informasi untuk user), itu DILARANG di luar "
        "[PIKIR]. Jawaban final = LANGSUNG sapaan/kesimpulan + data rapi (tabel/daftar "
        "ringkas), seolah kamu sudah tahu jawabannya — tanpa mempertontonkan caranya.\n"
        "- ⛔ JANGAN PERNAH menuliskan pemanggilan tool sebagai TEKS dalam jawaban "
        "(mis. menulis '<invoke name=...>' atau '<parameter ...>'). Tool dipanggil "
        "OTOMATIS lewat antarmuka fungsi, bukan diketik di isi pesan. Jika butuh data, "
        "panggil tool lewat mekanisme fungsi; jangan tulis markup-nya ke user.\n"
    )

    # ── Konteks percakapan: pahami pertanyaan lanjutan & rujukan ──
    konteks_block = (
        "\nKONTEKS PERCAKAPAN (WAJIB — pahami maksud dari alur obrolan, bukan hanya 1 pesan):\n"
        "- Pertanyaan lanjutan biasanya merujuk part/unit yang BARU dibahas. Selesaikan "
        "rujukan seperti 'itu', 'yang tadi', 'harganya?', 'stoknya?', 'yang NX400 aja', "
        "'merk lain?' dari giliran sebelumnya — JANGAN minta user mengulang.\n"
        "- Jika user sudah menetapkan UNIT/MODEL, ANGGAP konteks tetap unit itu untuk "
        "pertanyaan berikutnya, sampai user menyebut unit lain atau bilang 'semua unit'.\n"
        "- Permintaan penyaringan atas hasil sebelumnya ('yang ada stok saja', 'yang "
        "termurah', 'di gudang Jakarta', 'di cabangku') → terapkan ke PN/hasil yang "
        "BARUSAN ditampilkan; panggil tool lagi dengan filter sesuai bila perlu.\n"
        "- Jangan berpindah unit/part/topik tanpa diminta. Bila konteks benar-benar "
        "ambigu (tak jelas merujuk apa), tanyakan singkat alih-alih menebak.\n"
        "- Pahami MAKSUD di balik pertanyaan: 'ada gak', 'masih ada?', 'ready?' = cek "
        "stok; 'berapaan', 'harganya' = harga; 'buat unit apa aja', 'cocok di mana' = "
        "varian_unit; 'kenapa/rusak/gejala' = bantu telusuri part terkait.\n"
    )

    return (
        "Anda adalah **Asisten MASPART**, AI yang membantu pengguna aplikasi katalog "
        "& penjualan spare part truk (Sinotruk/HOWO dll). Jawab SELALU dalam Bahasa "
        "Indonesia yang ringkas, jelas, dan ramah.\n\n"
        "KONTEKS PENGGUNA:\n"
        f"- Username: {uname}\n"
        f"- Peran: {role} — {role_desc}{branch_line}\n\n"
        "PRIORITAS SUMBER DATA — EPC DULU (ATURAN #1, di atas aturan lain):\n"
        "- Untuk part yang menempel di UNIT TERTENTU, sumber UTAMA = EPC per-VIN "
        "(nomor rangka). Bila rangka SUDAH ada di percakapan → langsung pakai tool EPC "
        "yang sesuai (part_aus_dari_rangka / bom_dari_rangka / cek_kendaraan / "
        "kategori_unit / uraikan_mesin) — JANGAN cari_part.\n"
        "- Bila user menyebut PART + MODEL unit TANPA nomor rangka (mis. 'transmisi "
        "nx280', 'kampas rem howo', 'filter solar sitrak') → WAJIB AWALI jawaban dengan "
        "MEMINTA NOMOR RANGKA (VIN), jelaskan singkat alasannya: tanpa rangka hasil "
        "hanya perkiraan per-model dan bisa beda dari unit aslinya (dua unit bermodel "
        "sama bisa beda PN). Setelah itu BOLEH lanjut menampilkan perkiraan dari "
        "katalog (cari_part) — tapi labeli jelas 'perkiraan per-model (belum tentu PN "
        "unit Anda)'.\n"
        "- Katalog lokal (cari_part) jadi jawaban utama HANYA untuk pertanyaan umum "
        "lintas-unit (cek stok/harga sebuah PN, 'ada part X?', daftar part) atau saat "
        "EPC gagal / unit non-Sinotruk — dan tetap sebut sumbernya.\n\n"
        "STRUKTUR DATA (WAJIB DIPAHAMI):\n"
        "- Database part tersusun PER UNIT/MODEL truk. Setiap NAMA FILE Excel = satu "
        "varian unit (mis. 'NX360 6X4 (LZZ1BLSG)', 'NX360 DUMP 6X4', 'NX360TH 6X4'). "
        "Satu model (mis. NX360) bisa punya BEBERAPA VARIAN (6X4, DUMP, TH, dst).\n"
        "- Hasil cari_part sudah DIGABUNG per Part Number. Field 'varian_unit' "
        "berisi daftar varian yang memakai PN itu, dan 'jumlah_varian' jumlahnya. "
        "Jika sebuah PN ada di semua varian yang difilter, sampaikan itu (mis. "
        "'dipakai di semua 4 varian NX360').\n"
        "- WAJIB — TIPE KENDARAAN LENGKAP + KODE MODEL: setiap kali menyebut "
        "kendaraan/varian tempat sebuah part dipakai, tampilkan nama varian PERSIS "
        "seperti pada field 'varian_unit', LENGKAP dengan kode model di dalam tanda "
        "kurung. DILARANG memotong, menyingkat, atau menghilangkan kode dalam kurung.\n"
        "    Benar : NX280 4X2 MT (LZZ1CCSD)\n"
        "    Salah : NX280 4X2 MT   ← (kode model dihilangkan)\n"
        "  Bila part dipakai di BEBERAPA kendaraan, tampilkan SEMUA varian lengkap "
        "beserta kodenya — satu per baris — di bawah judul 'Part Digunakan Pada:'. "
        "Contoh:\n"
        "    Part Digunakan Pada:\n"
        "    NX280 4X2 MT (LZZ1CCSD)\n"
        "    NX280 6X4 MT (LZZ1BLVF)\n"
        "    NX440 6X4 AMT (LZZ1BLMJ)\n"
        "  Jangan pernah hanya menampilkan nama seri/spesifikasi umum tanpa kode model.\n"
        "- STOK & HARGA disimpan PER PART NUMBER (global) — nilainya SAMA untuk semua "
        "varian yang memakai PN tsb. Jangan menjumlahkan stok antar varian.\n"
        "- Part yang sama (mis. 'Fuel Injector') bisa ada di banyak unit berbeda. Part "
        "untuk NX360 BERBEDA dengan part untuk SG21 walau namanya mirip.\n"
        + units_block
        + persona_block
        + konteks_block
        + berpikir_block
        + "\nATURAN PENTING:\n"
        "1. Untuk pertanyaan tentang DATA (stok, harga, part, gudang, pesanan, "
        "penjualan), WAJIB panggil tool yang sesuai — JANGAN mengarang angka, PN, "
        "atau kaitan part↔unit. Selalu dasari jawaban pada hasil tool.\n"
        "2. Bila user menyebut UNIT/MODEL (mis. NX360, HOWO-7, SITRAK, SG21, L36), "
        "WAJIB isi parameter 'unit' di cari_part agar hasil discoped ke unit itu. "
        "JANGAN menampilkan part dari unit lain lalu mengeklaim 'cocok untuk' unit "
        "yang diminta. Sebutkan field 'unit' sumber tiap part di jawaban.\n"
        "3. Jika filter unit memberi hasil kosong, katakan terus terang bahwa part "
        "itu tidak tercatat untuk unit tsb — JANGAN ganti dengan part dari unit lain "
        "tanpa memberi tahu user dengan jelas bahwa itu dari unit berbeda.\n"
        "4. Part Number berupa kombinasi huruf+angka (pola seperti 'WG…', 'AZ…', "
        "'200V…-…', 'HW…'). Bila user menyebut PN, gunakan apa adanya.\n"
        "4b. ⛔ ANTI-NGARANG PN (KRITIS): DILARANG KERAS menyebut Part Number apa pun "
        "yang TIDAK muncul di hasil tool pada percakapan ini. JANGAN mengambil PN dari "
        "contoh di dalam instruksi sistem ini (semua PN di instruksi hanya ILUSTRASI "
        "FORMAT, bukan data nyata), JANGAN menebak/menyusun PN sendiri, dan JANGAN "
        "menambah/mengurangi digit sebuah PN. Setiap PN, nama part, stok, harga, dan "
        "kaitan part↔unit di jawaban WAJIB berasal langsung dari hasil cari_part/"
        "detail_part. Bila data yang diminta tak ada di hasil tool, katakan 'tidak "
        "tercatat' — bukan mengarang.\n"
        "5. Tampilkan harga dalam format Rupiah (mis. Rp 1.250.000) dan sebut stok "
        "per gudang bila relevan.\n"
        "5b. Nilai stok/harga '—' (atau kosong) berarti BELUM ADA DATA stok/harga "
        "untuk PN itu di sistem — BUKAN berarti barang habis/stok 0. Sampaikan "
        "sebagai 'belum ada data stok/harga', JANGAN klaim 'habis' atau 'kosong'. "
        "Mayoritas part katalog memang belum punya data stok/harga (hanya sebagian "
        "kecil yang distok), jadi '—' itu normal.\n"
        "5c. Bila hasil cari_part memuat 'jumlah_relevan_kuat', itulah jumlah part "
        "yang BENAR-BENAR relevan — sebut angka itu ke user, JANGAN 'jumlah_part_unik' "
        "(total mentah yang bisa membengkak karena kecocokan kata umum).\n"
        "6. Bila tool mengembalikan kosong / tidak ditemukan, katakan terus terang "
        "dan sarankan langkah lain (cek ejaan PN, cari per nama, atau daftar_unit).\n"
        "7. Jangan menjanjikan aksi yang tak bisa Anda lakukan (Anda hanya membaca "
        "data & memberi info; tidak membuat/mengubah pesanan).\n"
        "8. Jika pertanyaan di luar konteks MASPART, jawab singkat & arahkan kembali "
        "ke fungsi aplikasi.\n"
        "9. Boleh memanggil beberapa tool berturut-turut bila perlu (mis. daftar_unit "
        "dulu untuk tahu nama unit yang benar, lalu cari_part dengan filter unit).\n"
        "10. BERAT & DIMENSI part berasal dari data resmi pabrik (SIMS) dan muncul "
        "di field `spesifikasi` hasil detail_part (berat_kirim_kg/berat_bersih_kg, "
        "dimensi_cm, satuan, merek). Bila user bertanya berat/dimensi/ukuran sebuah "
        "PN, panggil detail_part dan sebutkan apa adanya dari `spesifikasi`. Bila "
        "field itu tidak ada (SIMS tak punya data), katakan berat belum tersedia — "
        "JANGAN mengarang angka.\n"
        "11. Untuk pertanyaan KODE KESALAHAN / fault code / DTC / SPN / FMI / kode P "
        "(mis. 'kode kesalahan SPN 1241 FMI 21' atau 'apa arti P0410'), WAJIB panggil "
        "tool cari_kode_kesalahan. Deskripsi dari tool berbahasa China — SELALU "
        "sajikan TERJEMAHAN BAHASA INDONESIA-nya (boleh sertakan teks asli sebagai "
        "rujukan). Sebutkan SPN, FMI, kode, dan status lampu MIL/SVS. Bila tak ada "
        "yang cocok, sarankan cek ulang angka SPN/FMI.\n"
        "12. FILTER alat berat SHANTUI (excavator, bulldozer, roller, grader): untuk "
        "pertanyaan soal filter unit Shantui — filter oli, solar/bahan bakar, udara, "
        "hidrolik, water separator — WAJIB panggil tool cari_filter_shantui (JANGAN "
        "pakai cari_part untuk ini). Tampilkan Part Name, Part Number Shantui, dan "
        "CROSS-REFERENCE merek lain (Fleetguard/Donaldson/Weichai/HIFI/Sakura/Baldwin/"
        "Cummins) sebagai pilihan pengganti. Kelompokkan per model unit & jenis filter "
        "(hidrolik/mesin), dan tulis nama model unit lengkap apa adanya (mis. "
        "'SE215W（WP6H)', 'SE60W1 DAN SE75W1').\n\n"
        "CARA MENJAWAB PENCARIAN PART (penting agar terasa pintar):\n"
        "- DASAR REKOMENDASI = KECOCOKAN/KOMPATIBILITAS PART DENGAN KATALOG, BUKAN STOK. "
        "Pilih & rekomendasikan part yang paling tepat untuk unit/kebutuhan user menurut "
        "katalog (Part Number yang benar untuk unit itu). JANGAN PERNAH merekomendasikan "
        "suatu part hanya karena stoknya banyak, dan JANGAN menjatuhkan/menurunkan part "
        "yang paling cocok hanya karena stoknya kosong. Hasil cari_part SUDAH DIURUT "
        "berdasarkan kecocokan katalog — sorot 1–3 kandidat paling cocok sebagai jawaban "
        "utama, tetap tampilkan kandidat relevan lain (boleh ringkas).\n"
        "- TAMPILKAN JUGA PART STOK KOSONG — JANGAN disembunyikan: jangan pernah "
        "menghilangkan/menyembunyikan/menurunkan part yang cocok dari daftar hanya karena "
        "stoknya 0. Tetap tampilkan dengan tanda jelas 'Stok: KOSONG (0 pcs)'. Stok itu "
        "INFORMASI saja (biar user tahu perlu indent/restock) — BUKAN dasar memilih atau "
        "mengurutkan part. Bila part paling cocok untuk unit user stoknya kosong, tetap "
        "rekomendasikan part itu sebagai yang BENAR, lalu boleh sebutkan stoknya kosong "
        "dan tawarkan alternatif yang juga KOMPATIBEL (bukan sekadar yang ada stok).\n"
        "- Bila field 'cocok_kata' ada, itu kata kunci katalog yang membuat part cocok; "
        "pakai untuk menjelaskan singkat kenapa part itu muncul.\n"
        "- BILA MENGELOMPOKKAN FILTER PER JENIS: tentukan jenis dari KATA INTI di nama "
        "part di mana pun posisinya, BUKAN dari kata pertama. Pemetaan kata kunci → "
        "kategori: 'fuel'/'solar'/'bahan bakar'/'coarse'/'fine'/'water separator' "
        "(+filter) → Filter Solar/Bahan Bakar; 'oil'/'oli'/'lube' → Filter Oli; "
        "'air'/'udara' → Filter Udara; 'hydraulic'/'hidrolik' → Filter Hidrolik. "
        "Kata seperti 'electrical heater', 'electric pump', 'with O-ring', merek "
        "(Parker/Yida/dll) hanyalah FITUR/embel-embel — JANGAN dipakai menentukan "
        "jenis. CONTOH WAJIB: 'Electrical heater fuel coarse filter (electric pump)' "
        "mengandung 'fuel coarse filter' → masuk FILTER SOLAR/BAHAN BAKAR, BUKAN "
        "'Lainnya'. Taruh di 'Lainnya' HANYA bila nama benar-benar tak memuat kata "
        "kunci jenis filter mana pun.\n"
        "- Bila hasil SANGAT BANYAK atau permintaan ambigu (mis. 'baut', 'seal', 'sensor' "
        "tanpa konteks), ajukan SATU pertanyaan klarifikasi singkat (unit/model? bagian "
        "mana? ada PN-nya?) untuk mempersempit — jangan menebak diam-diam.\n"
        "- Bila hasil KOSONG namun field 'saran_mungkin_maksud' berisi part dengan nama "
        "serupa, tawarkan sebagai 'Mungkin maksud Anda:' (sebut nama + Part Number) agar "
        "user bisa memilih — jangan hanya bilang tidak ditemukan.\n"
        "- Bila field 'catatan' menyebut KOREKSI SALAH KETIK, beri tahu user singkat "
        "bahwa Anda mengasumsikan ejaan yang benar (mis. \"Saya asumsikan maksud Anda "
        "'injector'\").\n"
        "- Bila user menyebut GEJALA/KELUHAN (mis. 'mesin overheat', 'rem blong', 'asap "
        "hitam', 'setir berat'), simpulkan dulu part yang paling mungkin terkait lalu "
        "cari & tawarkan (mis. overheat → radiator, thermostat, water pump, kipas; setir "
        "berat → power steering pump, oli ps). Jelaskan alasannya singkat, jangan "
        "mendiagnosis berlebihan.\n"
        "- SATU ENTRI PER PART NUMBER: sajikan hasil sebagai daftar PER Part Number, "
        "BUKAN tabel yang menggabung beberapa PN di bawah satu judul unit. DILARANG "
        "membuat kategori 'Part tambahan'/'Part lain'/'lainnya'. Tampilkan tiap PN "
        "setara: Part Number, nama, stok, harga.\n"
        "- WAJIB 'Part Digunakan Pada:' PER PART NUMBER: setiap Part Number HARUS "
        "punya daftar 'Part Digunakan Pada:' MILIKNYA SENDIRI, diambil dari field "
        "'varian_unit' PN itu. DILARANG KERAS menggabung beberapa Part Number ke dalam "
        "SATU daftar 'Part Digunakan Pada:' bersama — sebab tiap PN biasanya dipakai "
        "di tipe kendaraan yang BERBEDA, sehingga daftar gabungan menyesatkan. Pastikan "
        "user bisa melihat dengan jelas: PN ini dipakai di tipe apa saja."
        + sims_note
        + pop_note
        + lapangan_note
        + domain_block
    )


# ═══════════════════════════════════════════════════════════════════════
#  PANGGILAN KE DEEPSEEK
# ═══════════════════════════════════════════════════════════════════════
def _post_chat(messages: list[dict], tools: list[dict]) -> dict:
    s = get_settings()
    if not s.ai_configured:
        raise AINotConfigured("DEEPSEEK_API_KEY belum diset di backend/.env")
    url = f"{s.deepseek_base_url.rstrip('/')}/chat/completions"
    payload = {
        "model": s.deepseek_model,
        "messages": messages,
        "temperature": 0.1,
        # Cukup besar agar blok pikir internal [PIKIR] (kerap panjang saat membandingkan
        # banyak part) + jawaban final tidak terpotong → jawaban kosong/pesan aman.
        # 3500 dulu terlalu sempit utk kasus banding/daftar besar.
        "max_tokens": 6000,
    }
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"
    headers = {
        "Authorization": f"Bearer {s.deepseek_api_key}",
        "Content-Type": "application/json",
    }
    # Retry SEKALI untuk kegagalan sementara (jaringan putus, 429 rate-limit,
    # 5xx) — supaya user tak langsung dapat error karena gangguan sesaat.
    for attempt in (1, 2):
        try:
            r = requests.post(url, headers=headers, json=payload, timeout=_TIMEOUT)
        except requests.RequestException as e:
            if attempt == 1:
                time.sleep(1.5)
                continue
            raise RuntimeError(f"Gagal menghubungi DeepSeek (jaringan): {e}") from e
        if r.status_code in (429, 500, 502, 503, 504) and attempt == 1:
            time.sleep(2)
            continue
        if r.status_code >= 400:
            # Jangan bocorkan key; cukup status + pesan ringkas dari DeepSeek.
            try:
                detail = (r.json().get("error") or {}).get("message") or ""
            except Exception:
                detail = r.text[:200]
            raise RuntimeError(f"DeepSeek API error {r.status_code}: {detail}")
        return r.json()


_HIST_RECENT_FULL = 6      # pesan terbaru yang dikirim utuh (rujukan follow-up)
_HIST_CHARS_RECENT = 4000
_HIST_CHARS_OLD = 1500     # pesan lama dipangkas lebih ketat — hemat token


def _sanitize_history(history: list[dict]) -> list[dict]:
    """Ambil hanya peran user/assistant dgn konten teks, batasi panjang.
    Pemangkasan BERTINGKAT: N pesan terbaru dikirim panjang (rujukan follow-up
    'itu/yang tadi'), pesan lebih lama dipangkas ketat — konteks tetap ada,
    token jauh lebih hemat pada obrolan panjang."""
    out: list[dict] = []
    for m in history or []:
        role = m.get("role")
        content = (m.get("content") or "").strip()
        if role in ("user", "assistant") and content:
            out.append({"role": role, "content": content})
    out = out[-_MAX_HISTORY:]
    cut = max(0, len(out) - _HIST_RECENT_FULL)
    for i, m in enumerate(out):
        cap = _HIST_CHARS_OLD if i < cut else _HIST_CHARS_RECENT
        if len(m["content"]) > cap:
            m["content"] = m["content"][:cap] + " …(dipangkas)"
    return out


def _photo_note(candidates: list[dict] | None) -> str:
    """Bangun konteks hasil Cari-by-Foto (DINOv2) untuk disuntikkan ke pesan user,
    karena model teks tidak bisa 'melihat' foto. AI memakai PN kandidat ini untuk
    cek stok/harga/kecocokan via tool."""
    if not candidates:
        return (
            "[FOTO PART TERLAMPIR] Sistem Cari-by-Foto tidak menemukan part yang mirip "
            "di galeri. Sampaikan ke user bahwa fotonya belum dikenali; minta foto yang "
            "lebih jelas (fokus, terang, satu part) atau ketik nomor/nama part."
        )
    lines = []
    for i, c in enumerate(candidates[:6], 1):
        pct = round(float(c.get("similarity") or 0) * 100)
        nm = c.get("part_name") or "(nama tak diketahui)"
        lines.append(f"{i}. {c.get('part_number')} — {nm} (kemiripan {pct}%)")
    return (
        "[FOTO PART TERLAMPIR] Sistem Cari-by-Foto (DINOv2) mengenali kandidat part "
        "berikut dari foto yang diunggah user:\n" + "\n".join(lines) + "\n\n"
        "TUGAS: ambil kandidat dengan kemiripan TERTINGGI sebagai dugaan utama, lalu CEK "
        "stok per gudang, harga, dan unit pemakaian via tool (cari_part/detail_part pakai "
        "Part Number kandidat). Sebut Part Number-nya. Bila kemiripan tertinggi rendah "
        "(<50%), katakan kurang yakin dan tampilkan beberapa kandidat agar user memilih."
    )


# Token mirip Part Number: >=8 char, huruf+angka, dgn pemisah / + . - .
# Disaring lagi (>=2 huruf & >=3 angka) agar TIDAK menangkap kode model unit
# (mis. 'LZZ1CCSD' hanya 1 angka) atau token unit ('190HP' hanya 5 char).
_PNLIKE_RE = re.compile(r"[A-Z0-9][A-Z0-9/+.\-]{6,}")


def _recent_part_numbers(history: list[dict], max_pn: int = 8) -> list[str]:
    """Ambil Part Number dari pesan ASSISTANT terakhir yang memuatnya — untuk
    'memori konteks' giliran berikut (menyelesaikan rujukan 'itu/harganya?')."""
    for m in reversed(history or []):
        if (m or {}).get("role") != "assistant":
            continue
        pns: list[str] = []
        for tok in _PNLIKE_RE.findall((m.get("content") or "").upper()):
            tok = tok.strip(".")
            letters = sum(c.isalpha() for c in tok)
            digits = sum(c.isdigit() for c in tok)
            if len(tok) >= 8 and letters >= 2 and digits >= 3 and tok not in pns:
                pns.append(tok)
        if pns:
            return pns[:max_pn]
    return []


# VIN China (17 char, mulai 'L', tanpa I/O/Q) & frame number 8 char (2 huruf+6 angka,
# mis. RT108966 / SJ346500) — untuk mengingat RANGKA AKTIF di percakapan.
_VIN_FULL_RE = re.compile(r"\bL[A-HJ-NPR-Z0-9]{16}\b")
_FRAME_RE = re.compile(r"\b[A-Z]{2}\d{6}\b")


def _recent_rangka(history: list[dict], max_n: int = 2) -> list[str]:
    """Nomor rangka/VIN yang PALING BARU disebut di percakapan (user/asisten) —
    'unit aktif' untuk follow-up tool EPC tanpa user mengulang rangka."""
    for m in reversed(history or []):
        up = ((m or {}).get("content") or "").upper()
        toks = _VIN_FULL_RE.findall(up) + _FRAME_RE.findall(up)
        if toks:
            return list(dict.fromkeys(toks))[:max_n]
    return []


def _active_context_block(history: list[dict]) -> str:
    """Blok 'KONTEKS AKTIF': PN + nomor rangka yang BARU dibahas, agar model
    menyelesaikan rujukan follow-up tanpa menebak/minta ulang. Disuntik SETELAH
    riwayat (bukan di system prompt) supaya prefix system+riwayat lama stabil →
    prompt-cache DeepSeek tetap kena (input jauh lebih murah)."""
    pns = _recent_part_numbers(history)
    rangka = _recent_rangka(history)
    lines = ["KONTEKS AKTIF (rujukan untuk pesan terakhir user — data yang BARU dibahas):"]
    if rangka:
        lines.append(
            "- Nomor rangka AKTIF: " + ", ".join(rangka) + ". Bila user bertanya lanjutan "
            "soal part/posisi/spesifikasi unit TANPA mengulang rangka ('yang belakang?', "
            "'kalau injectornya?', 'part remnya?'), pakai rangka ini pada tool EPC yang "
            "sesuai — JANGAN minta rangka ulang."
        )
    else:
        lines.append(
            "- BELUM ADA nomor rangka (VIN) di percakapan ini. Bila pesan user menanyakan "
            "part untuk unit tertentu (mis. 'transmisi nx280'), terapkan ATURAN #1 EPC "
            "DULU: awali jawaban dengan meminta nomor rangka, dan labeli hasil katalog "
            "sebagai 'perkiraan per-model (belum tentu PN unit Anda)'."
        )
    if pns:
        lines.append(
            "- Part Number yang BARU ditampilkan: " + ", ".join(pns) + ". Bila user merujuk "
            "tak langsung ('itu', 'yang pertama', 'harganya?', 'stoknya?'), gunakan daftar "
            "ini dan panggil detail_part/harga_sims untuk PN yang dimaksud — JANGAN minta "
            "user mengulang nomor part."
        )
    return "\n".join(lines)


_REASON_RE = re.compile(r"\[PIKIR\].*?\[/PIKIR\]", re.IGNORECASE | re.DOTALL)
_REASON_OPEN_RE = re.compile(r"\[PIKIR\]", re.IGNORECASE)
_REASON_CLOSE_RE = re.compile(r"\[/PIKIR\]", re.IGNORECASE)

# Model kadang MENULISKAN pemanggilan tool sebagai TEKS (format invoke/parameter)
# alih-alih lewat field tool_calls API — markup itu lalu bocor ke layar user.
# Token pembungkus bisa termangle bermacam-macam (mis. '<|…|tool_calls>',
# '<|…|invoke name="…">'), maka kita kunci ke kata kunci DI DALAM tag saja.
_TOOL_MARKUP_TAG_RE = re.compile(
    r"<[^<>]*?\b(?:tool_calls|invoke|parameter)\b[^<>]*?>",
    re.IGNORECASE,
)
_LEAK_INVOKE_RE = re.compile(r"invoke\s+name\s*=\s*\"([^\"]+)\"", re.IGNORECASE)
_LEAK_PARAM_RE = re.compile(
    r"parameter\s+name\s*=\s*\"([^\"]+)\"[^>]*>(.*?)<", re.IGNORECASE | re.DOTALL
)


_TRUNCATED_NOTE = ("\n\n_(Jawaban tampaknya terpotong karena terlalu panjang — "
                   "minta \"lanjutkan\" atau persempit pertanyaannya bila perlu.)_")


def _finish_reason(data: dict) -> str | None:
    """Alasan model berhenti ('stop' | 'length' | 'tool_calls' | …) dari respons API."""
    return ((data.get("choices") or [{}])[0] or {}).get("finish_reason")


# Jawaban final kosong (model hanya menulis nalar [PIKIR] / terpotong): pesan aman
# ini HANYA dipakai setelah retry habis — chat() lebih dulu memaksa model menulis
# ulang jawaban finalnya (lihat _EMPTY_REPLY_CORRECTION di chat()).
_EMPTY_FINAL_MSG = ("Maaf, jawabannya belum lengkap diproses. Coba ulangi pertanyaannya "
                    "ya — atau persempit (mis. sebutkan nomor rangka / PN).")
_MAX_EMPTY_RETRIES = 2
_EMPTY_REPLY_CORRECTION = (
    "[SISTEM — KOREKSI WAJIB] Respons terakhirmu TIDAK berisi jawaban final untuk "
    "user (hanya blok [PIKIR] / kosong / terpotong). Tulis SEKARANG jawaban final "
    "yang rapi berdasarkan hasil tool & nalar sebelumnya: mulai dengan [PIKIR] "
    "SINGKAT, tutup [/PIKIR], lalu jawaban final lengkap. ⚠️ Jangan minta maaf dan "
    "jangan menyebut koreksi ini ke user."
)


def _strip_reasoning(text: str) -> str:
    """Buang blok alur-pikir internal [PIKIR]...[/PIKIR] agar user hanya melihat
    jawaban final. Tahan banting terhadap kasus tak ideal:
      - tag tidak lengkap (hanya pembuka/penutup),
      - model lupa menulis jawaban setelah [/PIKIR] → return "" (pemanggil yang
        memutuskan retry / pesan fallback; JANGAN bocorkan isi nalar)."""
    s = text or ""
    # 1) Buang pasangan [PIKIR]...[/PIKIR] yang lengkap.
    s = _REASON_RE.sub("", s)
    # 2) Bila masih ada penutup tersisa (mis. blok diawali tanpa pembuka),
    #    ambil semua teks SETELAH penutup terakhir = jawaban final.
    if _REASON_CLOSE_RE.search(s):
        s = _REASON_CLOSE_RE.split(s)[-1]
    # 3) Bila ada pembuka tersisa tanpa penutup, buang dari pembuka ke akhir
    #    (itu nalar yang tak tertutup — jangan ditampilkan).
    m = _REASON_OPEN_RE.search(s)
    if m:
        s = s[: m.start()]
    s = s.strip()
    if not s:
        return ""
    # Jaring pengaman: buang markup pemanggilan tool yang bocor sebagai teks.
    return _strip_tool_markup(s)


def _strip_tool_markup(text: str) -> str:
    """Buang blok pemanggilan tool yang BOCOR sebagai teks (model menulis
    <invoke>/<parameter> alih-alih memakai field tool_calls API). Buang seluruh
    rentang dari tag pertama s/d tag terakhir — termasuk nilai parameter di
    antaranya — karena itu bukan jawaban untuk user."""
    if not text:
        return text
    tags = list(_TOOL_MARKUP_TAG_RE.finditer(text))
    if not tags:
        return text
    return (text[: tags[0].start()] + text[tags[-1].end():]).strip()


def _parse_leaked_tool_calls(text: str) -> list[dict]:
    """Parse pemanggilan tool yang ditulis sebagai TEKS menjadi struktur
    [{"name": str, "arguments": dict}, ...] agar bisa DIJALANKAN, bukan
    dibiarkan bocor ke layar. Mengembalikan [] bila tak ada markup."""
    if not text or not _LEAK_INVOKE_RE.search(text):
        return []
    calls: list[dict] = []
    matches = list(_LEAK_INVOKE_RE.finditer(text))
    for i, m in enumerate(matches):
        name = m.group(1).strip()
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        block = text[start:end]
        args: dict = {}
        for pm in _LEAK_PARAM_RE.finditer(block):
            args[pm.group(1).strip()] = (pm.group(2) or "").strip()
        if name:
            calls.append({"name": name, "arguments": args})
    return calls


# ── Guard anti-halusinasi Part Number ───────────────────────────────────
# Model kadang MENGARANG PN (mis. saat tool found=False, ia isi PN berurutan rapi
# + stok/harga palsu — lihat kasus 'tierod'). Kita PATOK jawaban ke DATA: tiap PN
# yang muncul di balasan WAJIB berasal dari (a) hasil tool turn ini, atau (b) pesan
# user. Bila tidak → dianggap karangan. PN = token huruf-besar+angka (≥7 char, ADA
# huruf DAN ADA angka), mis. AZ1623550001, WG4007410031, HW19709XST201136. Token
# murni-angka / harga (2.150.000) sengaja TIDAK diperlakukan sebagai PN (hindari
# false-positive). VIN/frame yang user sebut otomatis ikut 'grounded' dari pesannya.
_PN_TOKEN_RE = re.compile(
    r"(?<![0-9A-Z])(?=[0-9A-Z.\-]*[A-Z])(?=[0-9A-Z.\-]*[0-9])"
    r"[0-9A-Z][0-9A-Z.\-]{6,}(?![0-9A-Z])"
)
# PN MURNI ANGKA (khas Weichai: 612630010054, 1000076563, 9000000401) — ≥9 digit
# kontigu, TIDAK berbatasan digit/titik/strip (agar harga '2.150.000'/'900.000' &
# qty/tahun TIDAK ikut). Tanpa ini, PN numerik bisa dikarang bebas (regex utama minta huruf).
_PN_NUMERIC_RE = re.compile(r"(?<![0-9.\-])[0-9]{9,}(?![0-9.\-])")
_MAX_GUARD_RETRIES = 2


def _extract_pns(text: str) -> set[str]:
    """Himpunan token mirip-PN (uppercase, tanpa titik/strip di ujung) dari teks.
    Mencakup PN alfanumerik (huruf+angka) DAN PN murni-angka panjang (≥9 digit, Weichai)."""
    if not text:
        return set()
    up = text.upper()
    out = {m.group(0).strip(".-") for m in _PN_TOKEN_RE.finditer(up)}
    out |= {m.group(0) for m in _PN_NUMERIC_RE.finditer(up)}
    return out


def _ungrounded_pns(reply: str, grounded: set[str]) -> list[str]:
    """PN di jawaban yang TIDAK ada di data mana pun (grounded) → dugaan karangan."""
    return sorted(p for p in _extract_pns(reply) if p and p not in grounded)


# Kode NAMA UNIT/SERI katalog yang bentuknya mirip PN (mis. 'NX400HP', 'HOWO400',
# 'LZZ5EXSF', 'SG21-C6') — BUKAN part number, jadi TIDAK boleh disamarkan guard
# sebagai "PN karangan" (kasus nyata: 'unit NX400HP' berubah jadi
# '⟨PN tak terverifikasi⟩'). Di-cache; sumber: index katalog + catalog BOM.
_UNIT_TOKEN_CACHE: dict = {"at": 0.0, "tokens": set()}
_UNIT_TOKEN_TTL_SEC = 600


def _unit_name_tokens() -> set[str]:
    now = time.time()
    if _UNIT_TOKEN_CACHE["tokens"] and now - _UNIT_TOKEN_CACHE["at"] < _UNIT_TOKEN_TTL_SEC:
        return _UNIT_TOKEN_CACHE["tokens"]
    toks: set[str] = set()
    try:
        for m in part_index.unit_models():
            toks |= _extract_pns(f"{m.get('unit', '')} {m.get('kategori', '')}")
    except Exception:
        pass
    try:
        toks |= _extract_pns(" ".join(catalog_bom.list_units()))
    except Exception:
        pass
    if toks:
        _UNIT_TOKEN_CACHE["tokens"] = toks
        _UNIT_TOKEN_CACHE["at"] = now
    return toks


def _drop_unit_tokens(bad: list[str]) -> list[str]:
    """Keluarkan kode unit/seri sah dari daftar dugaan PN karangan. Dipanggil
    HANYA saat ada dugaan (lazy) agar tak membangun index di jalur bersih."""
    if not bad:
        return bad
    unit_toks = _unit_name_tokens()
    return [p for p in bad if p not in unit_toks]


def _guard_correction_msg(bad: list[str]) -> str:
    return (
        "[SISTEM — KOREKSI WAJIB] Nomor part berikut yang kamu tulis TIDAK ADA di hasil "
        "tool mana pun pada giliran ini (dugaan KARANGAN): " + ", ".join(bad) + ". "
        "⛔ DILARANG KERAS menyebut/mengarang PN, stok, atau harga yang tidak berasal dari "
        "hasil tool. Bila part yang diminta TIDAK ditemukan di hasil tool (found=false / "
        "kosong), katakan JUJUR bahwa datanya tidak ada di EPC/katalog untuk unit itu & "
        "sarankan cek ejaan/istilah lain (mis. 'tie rod' dengan spasi) atau token EPC. "
        "Tulis ULANG jawabanmu tanpa PN karangan. Bila perlu, PANGGIL ULANG tool dengan "
        "istilah yang benar untuk mendapat PN asli. ⚠️ JANGAN minta maaf, JANGAN menyebut/"
        "menjelaskan koreksi ini ke user — langsung tulis jawaban bersih seolah dari awal."
    )


_NOT_FOUND_REPLY = (
    "Maaf, part yang Anda maksud **tidak ditemukan** di data EPC/katalog untuk unit ini. "
    "Saya tidak menampilkan nomor part karena memang tidak ada datanya — dan saya tidak "
    "akan mengarang. Coba:\n"
    "- periksa ejaan/istilah part-nya (mis. tulis **tie rod** dengan spasi),\n"
    "- pastikan nomor rangka/VIN sudah benar,\n"
    "- atau sebutkan Part Number-nya langsung bila sudah tahu."
)


def _sanitize_ungrounded(reply: str, bad: list[str]) -> str:
    """Jaring terakhir bila model tetap membandel setelah dikoreksi.
    - Bila SEMUA PN di jawaban ternyata karangan → jawaban ini tak punya data nyata:
      ganti TOTAL dengan pesan jujur 'tidak ditemukan' (jangan tampilkan tabel palsu).
    - Bila hanya SEBAGIAN karangan → samarkan yang palsu, pertahankan yang nyata."""
    all_pns = _extract_pns(reply)
    bad_set = {b.upper() for b in bad}
    if all_pns and all_pns <= bad_set:
        return _NOT_FOUND_REPLY
    out = reply
    for pn in bad:
        out = re.sub(re.escape(pn), "⟨PN tak terverifikasi⟩", out, flags=re.IGNORECASE)
    return ("⚠️ Sebagian nomor part tidak dapat diverifikasi dari data EPC/katalog dan "
            "telah disamarkan — jangan dijadikan acuan. Coba ulangi dengan istilah/ejaan "
            "lain atau sebutkan PN yang pasti.\n\n" + out)


def chat(user: dict, history: list[dict], photo_candidates: list[dict] | None = None) -> dict:
    """
    Jalankan satu giliran percakapan.
    `history`: list {role: 'user'|'assistant', content: str} — termasuk pesan
    terbaru dari user di posisi akhir.
    `photo_candidates`: bila user mengunggah foto, hasil Cari-by-Foto (search_by_image)
    yang disuntikkan sebagai konteks ke pesan user terakhir.
    Return {"reply": str, "tools_used": [nama, ...]}.
    """
    history = list(history or [])
    if photo_candidates is not None:
        note = _photo_note(photo_candidates)
        if history and (history[-1] or {}).get("role") == "user":
            base = (history[-1].get("content") or "").strip()
            history[-1] = {**history[-1], "content": (base + "\n\n" + note).strip() if base else note}
        else:
            history.append({"role": "user", "content": note})

    tools = _tool_specs(user)
    # System prompt dibiarkan STABIL antar giliran (tanpa suntikan konteks) agar
    # prefix-nya kena prompt-cache DeepSeek — system prompt ini besar, cache hit
    # memangkas biaya input drastis. Konteks yang berubah-ubah (PN/rangka aktif)
    # disuntik sebagai pesan system TERPISAH tepat sebelum pesan user terakhir.
    messages: list[dict] = [{"role": "system", "content": _system_prompt(user)}]
    messages.extend(_sanitize_history(history))
    ctx = _active_context_block(history)
    if ctx:
        pos = len(messages) - 1 if messages[-1].get("role") == "user" else len(messages)
        messages.insert(pos, {"role": "system", "content": ctx})

    tools_used: list[str] = []
    repairkit_models: list[str] = []  # model transmisi yg dibahas → tombol unduh Excel di UI
    banding_exports: list[dict] = []  # perbandingan rangka → kartu unduh Excel di UI

    def _capture_meta(name: str, args: dict, result: dict) -> None:
        """Kumpulkan metadata untuk tombol/kartu unduh di frontend."""
        if name == "repair_kit_transmisi":
            for h in (result.get("hasil") or []):
                mk = h.get("model")
                if mk and mk not in repairkit_models:
                    repairkit_models.append(mk)
        elif name == "banding_rangka" and result.get("found"):
            r1 = (args.get("rangka_1") or args.get("rangka1") or "").strip()
            r2 = (args.get("rangka_2") or args.get("rangka2") or "").strip()
            kat = (args.get("kategori") or "").strip()
            if r1 and r2:
                item = {"rangka_1": result.get("rangka_1") or r1,
                        "rangka_2": result.get("rangka_2") or r2,
                        "kategori": kat,
                        "kategori_nama": result.get("kategori") or "semua part"}
                if item not in banding_exports:
                    banding_exports.append(item)
    # Guard anti-halusinasi: kumpulan PN yang SAH. Diambil dari SELURUH riwayat —
    # pesan user (PN/VIN yang ia sebut) DAN jawaban asisten turn sebelumnya (yang
    # sudah LOLOS guard saat dibuat) — plus hasil tool turn ini. Menyertakan jawaban
    # asisten sebelumnya penting agar FOLLOW-UP yang menjawab dari konteks TANPA
    # panggil tool ulang tidak salah-tandai PN lama yang sah, sekaligus tetap
    # menangkap PN BARU yang dikarang (tak ada di riwayat mana pun).
    grounded: set[str] = set()
    for _m in history:
        grounded |= _extract_pns((_m or {}).get("content") or "")
    guard_retries = 0
    empty_retries = 0  # model hanya menulis [PIKIR]/kosong → paksa tulis ulang

    for _round in range(_MAX_TOOL_ROUNDS):
        data = _post_chat(messages, tools)
        choice = (data.get("choices") or [{}])[0]
        msg = choice.get("message") or {}
        tool_calls = msg.get("tool_calls") or []
        content = msg.get("content") or ""

        # Tangani pemanggilan tool yang BOCOR sebagai teks (model menulisnya alih-alih
        # memakai field tool_calls API): jalankan tool-nya, jangan biarkan ke layar.
        if not tool_calls:
            leaked = _parse_leaked_tool_calls(content)
            if leaked:
                messages.append({"role": "assistant", "content": _strip_tool_markup(content)})
                for lc in leaked:
                    name = lc["name"]
                    result = _run_tool(name, lc["arguments"], user)
                    tools_used.append(name)
                    grounded |= _extract_pns(json.dumps(result, ensure_ascii=False, default=str))
                    _capture_meta(name, lc["arguments"] or {}, result)
                    messages.append({
                        "role": "user",
                        "content": (
                            f"[HASIL TOOL {name}] (sistem sudah MENJALANKAN tool ini — "
                            "JANGAN tulis pemanggilan tool sebagai teks; pakai hasil ini "
                            "untuk menjawab):\n"
                            + json.dumps(result, ensure_ascii=False, default=str)
                        ),
                    })
                continue

            reply = _strip_reasoning(content)
            # Jawaban final KOSONG (model berhenti di [PIKIR] / terpotong / hanya
            # markup): jangan langsung menyerah dgn pesan generik — paksa model
            # menulis ulang jawaban finalnya dulu (kasus nyata: repairkit-hw19710).
            if not reply:
                if empty_retries < _MAX_EMPTY_RETRIES:
                    empty_retries += 1
                    messages.append({"role": "assistant", "content": content})
                    messages.append({"role": "user", "content": _EMPTY_REPLY_CORRECTION})
                    continue
                reply = _EMPTY_FINAL_MSG
            elif _finish_reason(data) == "length":
                reply += _TRUNCATED_NOTE
            # GUARD anti-halusinasi: SELALU cek (termasuk follow-up TANPA tool) —
            # PN di jawaban wajib ada di riwayat (user/asisten lolos) atau hasil tool.
            # Kode unit/seri sah (NX400HP dll) dikeluarkan dari dugaan karangan.
            bad = _drop_unit_tokens(_ungrounded_pns(reply, grounded))
            if bad and guard_retries < _MAX_GUARD_RETRIES:
                guard_retries += 1
                messages.append({"role": "assistant", "content": content})
                messages.append({"role": "user", "content": _guard_correction_msg(bad)})
                continue
            if bad:
                reply = _sanitize_ungrounded(reply, bad)
            return {"reply": reply, "tools_used": tools_used,
                    "repairkit_models": repairkit_models, "banding_exports": banding_exports}

        # Catat pesan assistant (yang berisi tool_calls) lalu jalankan tiap tool.
        messages.append({
            "role": "assistant",
            "content": _strip_tool_markup(content),
            "tool_calls": tool_calls,
        })
        for tc in tool_calls:
            fn = (tc.get("function") or {})
            name = fn.get("name") or ""
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except Exception:
                args = {}
            result = _run_tool(name, args, user)
            tools_used.append(name)
            grounded |= _extract_pns(json.dumps(result, ensure_ascii=False, default=str))
            _capture_meta(name, args, result)
            messages.append({
                "role": "tool",
                "tool_call_id": tc.get("id"),
                "content": json.dumps(result, ensure_ascii=False, default=str),
            })

    # Putaran tool habis — minta jawaban final tanpa tool.
    final = _post_chat(messages, [])
    msg = (final.get("choices") or [{}])[0].get("message") or {}
    reply = _strip_reasoning(msg.get("content") or "")
    if not reply:
        reply = _EMPTY_FINAL_MSG
    elif _finish_reason(final) == "length":
        reply += _TRUNCATED_NOTE
    bad = _drop_unit_tokens(_ungrounded_pns(reply, grounded))
    if bad:
        reply = _sanitize_ungrounded(reply, bad)
    return {"reply": reply, "tools_used": tools_used,
            "repairkit_models": repairkit_models, "banding_exports": banding_exports}
