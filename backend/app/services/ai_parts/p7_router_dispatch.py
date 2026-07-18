# -*- coding: utf-8 -*-
# ai_parts/p7_router_dispatch.py — BAGIAN dari modul app.services.ai_assistant (SATU namespace).
# Dimuat berurutan oleh ai_assistant.py (loader) via exec — sengaja BUKAN
# submodul terpisah: monkeypatch test `ai.<attr>`, relative import, dan
# resolusi global lintas-bagian tetap bekerja persis seperti file tunggal.
# Urutan muat & pembagian: lihat _PARTS di ai_assistant.py.
from __future__ import annotations

def _t_gambar_exploded(args: dict, user: dict) -> dict:
    sumber = (args.get("sumber") or "").strip().lower()
    if sumber == "mesin":
        return _gambar_exploded_mesin_impl(args, user)
    hasil = _gambar_exploded_atlas_impl(args, user)
    if sumber == "atlas" or hasil.get("found") or hasil.get("_token_issue"):
        return hasil
    # AUTO-FALLBACK: PN tak ketemu di Atlas (bukan isu token/input kosong) →
    # coba sisi mesin Weichai; pakai hasilnya hanya bila BENAR ketemu.
    if hasil.get("error") and (args.get("rangka") or "").strip() \
            and ((args.get("pn") or args.get("part_number") or "").strip()):
        h2 = _gambar_exploded_mesin_impl(args, user)
        if h2.get("found"):
            h2["sumber_dipakai"] = "mesin_weichai"
            h2["catatan"] = ("PN ini TIDAK ada di Parts Atlas Sinotruk tapi KETEMU di "
                            "EPC mesin Weichai (otomatis dialihkan). "
                            + (h2.get("catatan") or ""))
            return h2
    return hasil


def _t_gambar_exploded_mesin(args: dict, user: dict) -> dict:
    return _gambar_exploded_mesin_impl(args, user)


def _t_uraikan_assembly(args: dict, user: dict) -> dict:
    sumber = (args.get("sumber") or "").strip().lower()
    if sumber == "mesin":
        a2 = dict(args)
        if not (a2.get("part") or "").strip():
            a2["part"] = (args.get("assembly") or "").strip()
        return _uraikan_mesin_impl(a2, user)
    hasil = _uraikan_assembly_impl(args, user)
    if sumber == "atlas" or hasil.get("found") or hasil.get("_token_issue"):
        return hasil
    # AUTO-FALLBACK: assembly tak ketemu di Atlas → coba urai di mesin Weichai
    # (payload tiap sisi TETAP bentuk lamanya; hanya ditandai sumber_dipakai).
    if (args.get("rangka") or "").strip() and (args.get("assembly") or "").strip():
        a2 = dict(args)
        a2["part"] = a2.get("assembly") or ""
        try:
            h2 = _uraikan_mesin_impl(a2, user)
        except Exception:
            h2 = {}
        if h2.get("found"):
            h2["sumber_dipakai"] = "mesin_weichai"
            return h2
    return hasil


def _t_uraikan_mesin(args: dict, user: dict) -> dict:
    return _uraikan_mesin_impl(args, user)


def _t_katalog_kategori(args: dict, user: dict) -> dict:
    # 'mesin' juga kategori Atlas (02 powertrain) → HANYA sumber eksplisit yang
    # mengalihkan ke katalog mesin Weichai; tanpa itu perilaku lama dipertahankan.
    if (args.get("sumber") or "").strip().lower() == "mesin":
        return _katalog_mesin_impl(args, user)
    return _katalog_kategori_impl(args, user)


def _t_katalog_mesin(args: dict, user: dict) -> dict:
    return _katalog_mesin_impl(args, user)


def _t_repair_kit_transmisi(args: dict, user: dict) -> dict:
    if (args.get("sumber") or "").strip().lower() == "mesin":
        return _repair_kit_mesin_impl(args, user)
    return _repair_kit_transmisi_impl(args, user)


def _t_repair_kit_mesin(args: dict, user: dict) -> dict:
    return _repair_kit_mesin_impl(args, user)


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
    "cari_part_di_unit": _t_cari_part_di_unit,
    "banding_rangka": _t_banding_rangka,
    "banding_rangka_massal": _t_banding_rangka_massal,
    "part_aus_dari_rangka": _t_part_aus_dari_rangka,
    "repair_kit_transmisi": _t_repair_kit_transmisi,
    "banding_assy": _t_banding_assy,
    "isi_assy": _t_isi_assy,
    "banding_kategori": _t_banding_kategori,
    "isi_kategori": _t_isi_kategori,
    "part_termasuk_assy": _t_part_termasuk_assy,
    "daftar_transmisi_assy": _t_daftar_transmisi_assy,
    "cek_populasi": _t_cek_populasi,
    "banding_part_armada": _t_banding_part_armada,
    "detail_part": _t_detail_part,
    "stok_accurate": _t_stok_accurate,
    "harga_sims": _t_harga_sims,
    "info_aplikasi": _t_info_aplikasi,
    "stok_gudang": _t_stok_gudang,
    "stok_tertahan": _t_stok_tertahan,
    "pesanan_bermasalah": _t_pesanan_bermasalah,
    "alternatif_ready": _t_alternatif_ready,
    "daftar_unit": _t_daftar_unit,
    "cari_kode_kesalahan": _t_cari_kode_kesalahan,
    "diagnosa": _t_diagnosa,
    "cari_filter_shantui": _t_cari_filter_shantui,
    "jadwal_perawatan": _t_jadwal_perawatan,
    "diagram_wiring": _t_diagram_wiring,
    "cari_manual": _t_cari_manual,
    "template_excel_part": _t_template_excel,
    "pesanan_saya": _t_pesanan_saya,
    "detail_pesanan": _t_detail_pesanan,
    "rekap_penjualan": _t_rekap_penjualan,
    "daftar_pesanan": _t_daftar_pesanan,
    "buat_excel": _t_buat_excel,
    "hitung_part": _t_hitung_part,
    "excel_bom_rangka": _t_excel_bom_rangka,
    "excel_stok_gudang": _t_excel_stok_gudang,
    "katalog_kategori": _t_katalog_kategori,
    "katalog_mesin": _t_katalog_mesin,
    "gambar_exploded": _t_gambar_exploded,
    "gambar_exploded_mesin": _t_gambar_exploded_mesin,
    "sheet_ringkasan": _t_sheet_ringkasan,
    "sheet_isi_kolom": _t_sheet_isi_kolom,
    "sheet_isi_foto": _t_sheet_isi_foto,
    "sheet_isi_part_number": _t_sheet_isi_part_number,
    "sheet_cek_qty": _t_sheet_cek_qty,
    "sheet_pilih_sheet": _t_sheet_pilih_sheet,
    "buat_penawaran": _t_buat_penawaran,
}


def _run_tool(name: str, args: dict, user: dict, sheet_id: str = "") -> dict:
    fn = _DISPATCH.get(name)
    if not fn:
        return {"error": f"tool tidak dikenal: {name}"}
    # PENJAGA TERPUSAT (defense in depth): jalankan HANYA tool yang memang
    # ditawarkan ke peran user ini. Tanpa ini, satu-satunya benteng adalah
    # re-check peran di tiap handler — sekali ada tool sensitif baru yang lupa
    # cek, ia bisa dipanggil lintas-peran via prompt-injection/riwayat palsu.
    if name not in _allowed_tool_names(user, sheet_id):
        logger.warning("tool %s ditolak untuk peran %s/%s", name,
                       user.get("role"), user.get("username"))
        return {"denied": True,
                "error": f"Tool '{name}' tidak tersedia untuk peran Anda."}
    args = dict(args or {})
    if name.startswith("sheet_"):
        # sheet_id datang dari server (lampiran giliran ini), BUKAN dari model —
        # model tak boleh memilih file milik siapa pun lewat argumen.
        args["_sheet_id"] = sheet_id
    try:
        res = fn(args, user)
    except Exception:  # pragma: no cover
        # Detail exception hanya ke log server — JANGAN bocorkan pesan internal
        # (path/stack/URL) ke model/user.
        logger.exception("tool %s gagal", name)
        return {"error": f"tool '{name}' gagal dijalankan (gangguan internal — "
                         "sampaikan jujur ke user, jangan mengarang data)."}
    # PENJAGA HARGA TERPUSAT (defense in depth): buang SEMUA field harga bila user
    # tak berhak — tak peduli handler-nya lupa mengecek. Menu Control 'Kolom Harga'
    # kini menguasai asisten sama seperti halaman Cari Part/detail.
    if isinstance(res, dict) and not _boleh_harga(user):
        _strip_harga(res)
    return res


# Nama tool LAMA sisi Weichai → tool gabungan penggantinya (Fase 4). Spec-nya
# tak lagi ditawarkan, tapi eksekusinya tetap SAH (shim di _DISPATCH) — model
# kadang menulis nama lama dari kebiasaan riwayat/leaked tool-call.
_LEGACY_TOOL_ALIAS = {
    "gambar_exploded_mesin": "gambar_exploded",
    "katalog_mesin": "katalog_kategori",
    "uraikan_mesin": "uraikan_assembly",
    "repair_kit_mesin": "repair_kit_transmisi",
}


def _allowed_tool_names(user: dict, sheet_id: str = "") -> set[str]:
    """Nama tool yang SAH untuk peran user — sumber kebenaran sama dgn yang
    ditawarkan ke model (_tool_specs) + alias legacy tool gabungan (Fase 4),
    jadi allow-list eksekusi tak pernah menyimpang dari daftar yang di-expose."""
    names = {f["function"]["name"] for f in _tool_specs(user, sheet_id)}
    names |= {alias for alias, utama in _LEGACY_TOOL_ALIAS.items() if utama in names}
    return names


_MAX_TOOL_CONTENT = 24000  # batas char JSON hasil tool yg di-append ke messages


_TOOL_CAP_TAIL = 3000   # sisakan EKOR: builder menaruh catatan/jawaban_wajib di ujung


def _cap_tool_content(s: str) -> str:
    """Batasi panjang JSON hasil tool yang dimasukkan ke riwayat percakapan.
    Hasil raksasa (banding_rangka_massal, katalog_mesin) bila di-append penuh
    tiap ronde membuat token membengkak & bisa menembus limit konteks model
    (→ API 400 → 502). Tool tetap mengembalikan data lengkap ke frontend lewat
    metadata; yang dipotong hanya salinan untuk konsumsi model.

    Potong KEPALA + EKOR (bukan kepala saja): tool builder menaruh instruksi
    penyetir model (`catatan`/`jawaban_wajib`/`catatan_cakupan`) di UJUNG dict —
    potong-kepala-saja menghapusnya senyap, membuat model kehilangan aturan
    (mis. '⛔ jangan mengarang PN di luar daftar')."""
    if len(s) <= _MAX_TOOL_CONTENT:
        return s
    dipotong = len(s) - _MAX_TOOL_CONTENT
    marker = (f"\n…[dipotong {dipotong} karakter di tengah — hasil terlalu besar; "
              "rangkum dari bagian atas & bawah, jangan menebak yang hilang]…\n")
    head = _MAX_TOOL_CONTENT - _TOOL_CAP_TAIL - len(marker)
    return s[:head] + marker + s[-_TOOL_CAP_TAIL:]


def _compact_result(v):
    """Buang field KOSONG (None, '', [], {}) secara rekursif dari hasil tool
    sebelum diserialisasi ke model — memangkas 15-35% char hasil tool, komponen
    biaya token UNCACHED terbesar per giliran (system prompt sudah ter-cache).
    ⛔ JANGAN buang boolean atau 0: _tool_failed & guard bergantung pada
    found:False / denied / stok 0. ⛔ JANGAN rename key (nama key = kosakata yang
    sudah dikenal model). Panjang elemen LIST tak diubah (struktur tetap)."""
    if isinstance(v, dict):
        out = {}
        for k, val in v.items():
            c = _compact_result(val)
            if c is None or c == "" or c == [] or c == {}:
                continue
            out[k] = c
        return out
    if isinstance(v, list):
        return [_compact_result(x) for x in v]
    return v


def _dump_tool(result) -> str:
    """Serialisasi hasil tool untuk konsumsi model: dikompakkan (buang field
    kosong) + separator rapat (tanpa spasi). Dipakai SEKALI lalu string yang sama
    dipakai ekstraksi PN & konten yang di-append — guard PN melihat persis yang
    dilihat model."""
    return json.dumps(_compact_result(result), ensure_ascii=False,
                      separators=(",", ":"), default=str)


_TOOL_TRIM_KEEP_LAST = 2   # ronde tool TERAKHIR yang isinya dibiarkan UTUH


def _trim_old_tool_messages(messages: list[dict], tool_msg_idx: list[dict],
                            cur_round: int, keep_last: int = _TOOL_TRIM_KEEP_LAST) -> None:
    """Ciutkan CONTENT pesan HASIL TOOL dari ronde LAMA (≤ cur_round - keep_last) jadi
    stub pendek — `messages` dikirim ulang UTUH tiap panggilan API, jadi hasil tool yang
    menumpuk lintas ronde membengkakkan token input (biaya kuadratik). Dipakai pada
    RANTAI panjang (banding/katalog 5-8 ronde) yang paling rawan tembus limit konteks.

    AMAN: grounding/PN/metadata sudah ditangkap ke state samping (`grounded`,
    `_capture_meta`, `_track_pn_source`) saat hasil di-append — trimming tak
    menghilangkannya. `role:tool` + `tool_call_id` DIPERTAHANKAN (pasangan
    assistant.tool_calls↔tool wajib valid); ronde terbaru tetap utuh agar model
    bernalar atas data terkini."""
    batas = cur_round - keep_last
    if batas < 1:
        return
    for e in tool_msg_idx:
        if e.get("stubbed") or e["round"] > batas:
            continue
        i = e["i"]
        if 0 <= i < len(messages):
            messages[i]["content"] = (f"[hasil {e.get('name') or 'tool'} (ronde {e['round']}) "
                                      "sudah dipakai — diringkas untuk hemat konteks]")
        e["stubbed"] = True


def _tool_failed(result: dict) -> bool:
    """True bila hasil tool = kegagalan/kekosongan lookup (error, ditolak, atau
    'tidak ditemukan'). Dipakai untuk mengingatkan model agar TIDAK mengarang
    stok/harga saat data sebenarnya gagal diambil (guard PN tak menangkap angka)."""
    if not isinstance(result, dict):
        return False
    if result.get("error") or result.get("denied"):
        return True
    # found/ditemukan/tersedia == False → lookup nihil (bedakan dari absennya key).
    for k in ("found", "ditemukan", "tersedia"):
        if result.get(k) is False:
            return True
    return False


_LOOKUP_GAGAL_NOTE = (
    "[CATATAN SISTEM] Ada tool yang GAGAL/tak menemukan data. DILARANG mengarang "
    "stok/harga/ketersediaan untuk item itu — sampaikan apa adanya, sarankan "
    "langkah (cek nomor / coba lagi / hubungi admin)."
)


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
