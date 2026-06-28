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

import requests

from ..core.config import get_settings
from . import fault_codes, filter_ref, gudang, harga, orders, part_index, populasi, repairkit

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


def _load_sinonim_entries() -> list:
    """Baca data/sinonim/sinonim.json (segar tiap panggil agar editan langsung
    terpakai). Format: [{"grup","triggers":[id...],"keywords":[en...]}]."""
    try:
        p = get_settings().data_path / "sinonim" / "sinonim.json"
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8")) or []
    except Exception:
        pass
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
                    "'unit' agar hasil discoped ke unit itu — jangan campur antar unit."
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
                    "stok per gudang, dan harga jual lokal."
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
                    "nama UNIT (mis. 'HOWO-371', 'SITRAK 540'). Pakai untuk pertanyaan "
                    "'repair kit / perpak / seal kit / paking transmisi', 'apa saja diganti "
                    "saat overhaul gearbox', dll. Kosongkan 'transmisi' untuk daftar model."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "transmisi": {
                            "type": "string",
                            "description": "Model gearbox (HW19709 / ZF16S2531TO / 8JS85), PN gearbox assy, ATAU nama unit. Kosongkan untuk daftar model yang tersedia.",
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
        if tl and tl != ql and tl in name_l:
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
            grouped[pn] = {**_slim_part(r), "varian_unit": []}
            grouped[pn].pop("lokasi_file", None)
            grouped[pn].pop("unit", None)
            order.append(pn)
        u = r.get("file")
        if u and u not in grouped[pn]["varian_unit"]:
            grouped[pn]["varian_unit"].append(u)

    items = []
    for pn in order:
        it = grouped[pn]
        it["jumlah_varian"] = len(it["varian_unit"])
        # Ranking: relevansi (kecocokan paling spesifik) + ketersediaan stok.
        rel, cocok = _relevansi(it.get("part_name") or "", pn, q, terms)
        it["tersedia"] = _stok_int(it.get("stok_total")) > 0
        if cocok:
            it["cocok_kata"] = cocok
        # Bila user menanyakan TRANSMISI/GEARBOX, naikkan unit gearbox UTUH ke atas
        # supaya tak tenggelam di antara sub-part (housing/shaft/lever). Sekaligus
        # tandai jenisnya agar AI mengenalinya sebagai transmisi assy.
        if gearbox_q and _is_gearbox_assy(pn, it.get("part_name") or ""):
            rel += 100000
            it["jenis"] = "TRANSMISI ASSY (gearbox/unit utuh)"
        it["_rel"] = rel
        items.append(it)

    # Urut MURNI berdasarkan KECOCOKAN/KOMPATIBILITAS part dengan katalog (relevansi).
    # Stok TIDAK memengaruhi urutan — part yang stoknya kosong tetap diurut sesuai
    # kecocokannya (cuma ditandai 'tersedia' untuk info). Tiebreak deterministik:
    # jumlah varian unit (lebih umum dipakai) lalu PN, supaya urutan stabil.
    items.sort(key=lambda x: (x["_rel"], x.get("jumlah_varian", 0)), reverse=True)
    for it in items:
        it.pop("_rel", None)

    jumlah_tersedia = sum(1 for it in items if it.get("tersedia"))
    # Saat difilter ke 1 unit, hasil sudah sempit & user biasanya ingin daftar
    # LENGKAP part untuk unit itu — tampilkan lebih banyak supaya part bernama
    # generik (mis. 'Filter element') yang peringkatnya agak bawah tetap ikut.
    # Pencarian global tetap dibatasi ketat agar hemat token.
    row_cap = _MAX_PART_ROWS_UNIT if unit else _MAX_PART_ROWS
    out = items[:row_cap]
    if note is None and len(items) > row_cap:
        note = (
            f"{len(items)} part cocok — ditampilkan {len(out)} teratas (diurut berdasarkan "
            f"KECOCOKAN katalog, bukan stok). Bila kurang tepat, persempit dengan menyebut "
            f"UNIT/MODEL atau kata kunci yang lebih spesifik."
        )

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

    return {
        "query": q, "kata_kunci_dicari": search_terms, "unit_filter": unit or None,
        "catatan": note,
        "jumlah_part_unik": len(items), "ditampilkan": len(out),
        "jumlah_tersedia_stok": jumlah_tersedia,
        "saran_mungkin_maksud": saran,
        "urutan": "Hasil DIURUT berdasarkan KECOCOKAN/KOMPATIBILITAS part dengan katalog (BUKAN stok). Rekomendasikan part yang paling cocok untuk unit/kebutuhan user — stok hanya info, bukan dasar rekomendasi.",
        "info_stok_harga": "Stok & harga berlaku per Part Number (sama untuk semua varian unit yang memakai PN itu).",
        "hasil": out,
    }


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
    return {
        "found": True,
        **base,
        "varian_unit": varian,
        "jumlah_varian": len(varian),
        "info_stok_harga": "Stok & harga berlaku per Part Number (sama untuk semua varian unit).",
    }


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


def _t_repair_kit_transmisi(args: dict, user: dict) -> dict:
    if not repairkit.available():
        return {"error": "Data repair kit transmisi belum tersedia di server."}
    q = (args.get("transmisi") or "").strip()
    tingkat = (args.get("tingkat") or "seal_kit").strip().lower()
    if not q:
        models = repairkit.list_models()
        unit_tercatat = sorted({u for m in models for u in m.get("unit", [])})
        return {
            "daftar_model": models,
            "total_model": len(models),
            "total_unit_tercatat": len(unit_tercatat),
            "unit_tercatat": unit_tercatat,
            "catatan": "SEMUA unit di 'unit_tercatat' PUNYA transmisi assy + repair kit "
                       "(ini sumber kebenaran). JANGAN mengklaim suatu unit tidak punya "
                       "transmisi assy tanpa mengeceknya di sini. Sebutkan model/PN/unit "
                       "(mis. 'HW19709', 'ZF16S2531TO', '8JS85', PN gearbox assy, atau nama "
                       "unit) untuk melihat repair kit-nya.",
        }
    hits = repairkit.find(q)
    if not hits:
        models = ", ".join(m["model"] for m in repairkit.list_models())
        return {"jumlah_model_cocok": 0,
                "catatan": f"Tidak ada repair kit transmisi untuk '{q}'. Model tersedia: {models}."}
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
    return {
        "jumlah_model_cocok": len(hits),
        "tingkat": tingkat,
        "catatan": ("Repair kit disusun dari sheet gearbox katalog. 'seal_kit' = perpak "
                    "(oil seal+gasket+O-ring); 'overhaul' = bearing+synchronizer+snap ring. "
                    "Sajikan DIKELOMPOKKAN per kategori dengan PN + nama. Bila daftar sangat "
                    "panjang, tampilkan per kategori beserta jumlahnya & tawarkan rincian/Excel."),
        "hasil": hasil,
    }


_DISPATCH = {
    "cari_part": _t_cari_part,
    "repair_kit_transmisi": _t_repair_kit_transmisi,
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
        "atau nama unit). Default 'seal_kit' (perpak); pakai 'overhaul' bila user minta turun-"
        "mesin lengkap. Sajikan dikelompokkan per kategori (oil seal / gasket / O-ring / "
        "bearing / synchronizer / snap ring) dengan PN + nama.\n"
        "- ⚠️ JANGAN PERNAH menyatakan suatu unit 'tidak punya transmisi assy' dari ingatan/"
        "tebakan. Tiap unit Sinotruk punya sheet gearbox (05变速箱) & Part Number transmisi "
        "assy — selain pola HW…huruf, ADA juga assy ber-PN `HW19710…` (tanpa huruf), Fast "
        "`FZ…` (8JS85TE), & ZF `WG…` (ZF16S2531TO) yang TETAP transmisi assy. Untuk pertanyaan "
        "'unit apa saja yang punya / tidak punya transmisi/repair kit', panggil "
        "repair_kit_transmisi dengan argumen KOSONG dan jawab HANYA dari field 'unit_tercatat' "
        "— jangan mengarang atau menghitung sendiri dari ingatan."
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
        "- Ringkas (beberapa baris/poin), Bahasa Indonesia, BUKAN esai.\n"
        "- Blok ini HANYA untuk dirimu; JANGAN pernah menjadikannya jawaban.\n"
        "- WAJIB selalu ada JAWABAN FINAL untuk user SETELAH [/PIKIR]. Jangan berhenti "
        "di [PIKIR] saja. Jawaban final tidak boleh menyebut adanya proses berpikir ini.\n"
        "- SEMUA proses kerja WAJIB di DALAM [PIKIR]: membandingkan/menghitung/mencocokkan "
        "antar-daftar, menelusuri hasil tool, menimbang opsi, enumerasi langkah. Bila perlu "
        "membandingkan banyak item (mis. 'unit mana yang tidak ada X'), lakukan SELURUH "
        "perbandingannya di dalam [PIKIR] — di luar [/PIKIR] tampilkan HANYA hasil akhirnya "
        "yang sudah rapi.\n"
        "- ⛔ DILARANG di jawaban final: kalimat proses seperti 'saya cek/bandingkan dulu', "
        "'sekarang saya…', 'berdasarkan daftar_unit vs unit_tercatat…', daftar 'X ✅ ada / "
        "Y ✅ ada' satu per satu, atau menyalin mentah hasil tool. Jawaban final = LANGSUNG "
        "kesimpulan + data yang sudah dirapikan (tabel/daftar ringkas) untuk user, seolah "
        "kamu sudah tahu jawabannya — tanpa mempertontonkan cara kamu mendapatkannya.\n"
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
        "4. Part Number biasanya huruf+angka (mis. WG9925520270, 200V10100-6126). "
        "Bila user menyebut PN, gunakan apa adanya.\n"
        "5. Tampilkan harga dalam format Rupiah (mis. Rp 1.250.000) dan sebut stok "
        "per gudang bila relevan.\n"
        "6. Bila tool mengembalikan kosong / tidak ditemukan, katakan terus terang "
        "dan sarankan langkah lain (cek ejaan PN, cari per nama, atau daftar_unit).\n"
        "7. Jangan menjanjikan aksi yang tak bisa Anda lakukan (Anda hanya membaca "
        "data & memberi info; tidak membuat/mengubah pesanan).\n"
        "8. Jika pertanyaan di luar konteks MASPART, jawab singkat & arahkan kembali "
        "ke fungsi aplikasi.\n"
        "9. Boleh memanggil beberapa tool berturut-turut bila perlu (mis. daftar_unit "
        "dulu untuk tahu nama unit yang benar, lalu cari_part dengan filter unit).\n"
        "10. JANGAN PERNAH menyebutkan berat part (gram/kg) dalam jawaban tentang "
        "part — meski user bertanya, katakan info berat tidak ditampilkan di sini.\n"
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
        # Cukup besar agar blok pikir internal [PIKIR] + jawaban panjang (mis. daftar
        # part 1 unit yang banyak) tidak terpotong di tengah.
        "max_tokens": 3500,
    }
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"
    r = requests.post(
        url,
        headers={
            "Authorization": f"Bearer {s.deepseek_api_key}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=_TIMEOUT,
    )
    if r.status_code >= 400:
        # Jangan bocorkan key; cukup status + pesan ringkas dari DeepSeek.
        detail = ""
        try:
            detail = (r.json().get("error") or {}).get("message") or ""
        except Exception:
            detail = r.text[:200]
        raise RuntimeError(f"DeepSeek API error {r.status_code}: {detail}")
    return r.json()


def _sanitize_history(history: list[dict]) -> list[dict]:
    """Ambil hanya peran user/assistant dgn konten teks, batasi panjang."""
    out: list[dict] = []
    for m in history or []:
        role = m.get("role")
        content = (m.get("content") or "").strip()
        if role in ("user", "assistant") and content:
            out.append({"role": role, "content": content[:4000]})
    return out[-_MAX_HISTORY:]


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


def _active_context_block(history: list[dict]) -> str:
    """Blok 'KONTEKS AKTIF' yang disuntik ke system prompt: daftar PN yang BARU
    ditampilkan, agar model bisa menyelesaikan rujukan tanpa menebak."""
    pns = _recent_part_numbers(history)
    if not pns:
        return ""
    return (
        "\n\nKONTEKS AKTIF (Part Number yang BARU saja Anda tampilkan ke user): "
        + ", ".join(pns)
        + ".\nBila pesan user merujuk salah satunya secara tak langsung ('itu', 'yang "
        "pertama', 'harganya?', 'stoknya?', 'yang ini') TANPA menyebut PN, gunakan "
        "daftar di atas sebagai rujukan dan panggil detail_part/harga_sims untuk PN "
        "yang dimaksud — JANGAN minta user mengulang nomor part."
    )


_REASON_RE = re.compile(r"\[PIKIR\].*?\[/PIKIR\]", re.IGNORECASE | re.DOTALL)
_REASON_OPEN_RE = re.compile(r"\[PIKIR\]", re.IGNORECASE)
_REASON_CLOSE_RE = re.compile(r"\[/PIKIR\]", re.IGNORECASE)


_TRUNCATED_NOTE = ("\n\n_(Jawaban tampaknya terpotong karena terlalu panjang — "
                   "minta \"lanjutkan\" atau persempit pertanyaannya bila perlu.)_")


def _finish_reason(data: dict) -> str | None:
    """Alasan model berhenti ('stop' | 'length' | 'tool_calls' | …) dari respons API."""
    return ((data.get("choices") or [{}])[0] or {}).get("finish_reason")


def _strip_reasoning(text: str) -> str:
    """Buang blok alur-pikir internal [PIKIR]...[/PIKIR] agar user hanya melihat
    jawaban final. Tahan banting terhadap kasus tak ideal:
      - tag tidak lengkap (hanya pembuka/penutup),
      - model lupa menulis jawaban setelah [/PIKIR] (fallback: jangan kirim kosong)."""
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
    # 4) Fallback: kalau jadi kosong (model cuma menulis nalar tanpa jawaban),
    #    kembalikan teks asli tanpa tag agar user tetap dapat sesuatu.
    if not s:
        s = _REASON_OPEN_RE.sub("", _REASON_CLOSE_RE.sub("", text or "")).strip()
    return s


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
    system_content = _system_prompt(user) + _active_context_block(history)
    messages: list[dict] = [{"role": "system", "content": system_content}]
    messages.extend(_sanitize_history(history))

    tools_used: list[str] = []
    repairkit_models: list[str] = []  # model transmisi yg dibahas → tombol unduh Excel di UI

    for _round in range(_MAX_TOOL_ROUNDS):
        data = _post_chat(messages, tools)
        choice = (data.get("choices") or [{}])[0]
        msg = choice.get("message") or {}
        tool_calls = msg.get("tool_calls") or []

        if not tool_calls:
            reply = _strip_reasoning(msg.get("content") or "")
            if reply and _finish_reason(data) == "length":
                reply += _TRUNCATED_NOTE
            return {"reply": reply,
                    "tools_used": tools_used, "repairkit_models": repairkit_models}

        # Catat pesan assistant (yang berisi tool_calls) lalu jalankan tiap tool.
        messages.append({
            "role": "assistant",
            "content": msg.get("content") or "",
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
            if name == "repair_kit_transmisi":
                for h in (result.get("hasil") or []):
                    mk = h.get("model")
                    if mk and mk not in repairkit_models:
                        repairkit_models.append(mk)
            messages.append({
                "role": "tool",
                "tool_call_id": tc.get("id"),
                "content": json.dumps(result, ensure_ascii=False, default=str),
            })

    # Putaran tool habis — minta jawaban final tanpa tool.
    final = _post_chat(messages, [])
    msg = (final.get("choices") or [{}])[0].get("message") or {}
    reply = _strip_reasoning(msg.get("content") or "")
    if reply and _finish_reason(final) == "length":
        reply += _TRUNCATED_NOTE
    return {"reply": reply,
            "tools_used": tools_used, "repairkit_models": repairkit_models}
