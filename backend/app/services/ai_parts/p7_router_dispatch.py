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
    "cek_garansi": _t_cek_garansi,
    "riwayat_klaim": _t_riwayat_klaim,
    "detail_klaim": _t_detail_klaim,
    "lihat_unit_armada": _t_lihat_unit_armada,
    "ganti_nama_unit": _t_ganti_nama_unit,
    "excel_unit_armada": _t_excel_unit_armada,
    "sheet_isi_nama_telematik": _t_sheet_isi_nama_telematik,
    "daftarkan_unit": _t_daftarkan_unit,
    "sheet_daftar_unit": _t_sheet_daftar_unit,
    "masukkan_unit_fleet": _t_masukkan_unit_fleet,
    "sheet_masukkan_fleet": _t_sheet_masukkan_fleet,
    "buat_fleet": _t_buat_fleet,
    "diagram_wiring": _t_diagram_wiring,
    "cari_manual": _t_cari_manual,
    "cari_pengetahuan": _t_cari_pengetahuan,
    "buka_pengetahuan": _t_buka_pengetahuan,
    "template_excel_part": _t_template_excel,
    "sheet_jadi_penawaran": _t_sheet_jadi_penawaran,
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


# ── Penjaga istilah lapangan (2026-07-22) ────────────────────────────
# Kasus produksi "cucuk per": kamus sinonim tahu itu spring pin, tapi model DUA
# KALI mengarang terjemahannya sendiri ("cross joint", lalu "fuel injector")
# meski aturan prompt + [KAMUS ISTILAH GILIRAN INI] melarang — soft guard
# terbukti kalah. Penegakan deterministik: bila pertanyaan user memuat trigger
# kamus dan kata kunci model tidak memuaskan SATU grup pun (bukan trigger,
# bukan keyword resmi, bukan PN), kata kunci model DITIMPA istilah mentah user
# — ekspansi sinonim di handler yang menerjemahkannya dengan benar.
_TOOLS_ISTILAH = {
    "cari_part": ("query",),
    "cari_part_di_unit": ("kata_kunci", "query"),
    "part_aus_dari_rangka": ("query",),
    "stok_gudang": ("kata_kunci", "query"),
}


def _paksa_istilah_kamus(name: str, args: dict, question: str) -> str:
    """Mutasi `args` in-place bila model mengarang istilah; return catatan
    untuk model ("" = tidak menimpa apa-apa)."""
    fields = _TOOLS_ISTILAH.get(name)
    if not fields or not (question or "").strip():
        return ""
    field = next((f for f in fields if str(args.get(f) or "").strip()), None)
    if not field:
        return ""
    kata = str(args.get(field) or "").strip()
    if any(c.isdigit() for c in kata):
        return ""                      # kemungkinan PN — jangan diganggu
    cocok: list[tuple[str, dict]] = []  # (trigger yg muncul di question, entri)
    try:
        for e in _load_sinonim_entries():
            t = next((t for t in (e.get("triggers") or [])
                      if t and sinonim.hit(t, question)), None)
            if t:
                cocok.append((t, e))
    except Exception:                  # kamus rusak tak boleh mematikan tool
        logger.exception("penjaga istilah gagal membaca kamus (dilewati)")
        return ""
    if not cocok:
        return ""
    for _t, e in cocok:
        istilah = [*(e.get("triggers") or []), *(e.get("keywords") or [])]
        if any(i and sinonim.hit(i, kata) for i in istilah):
            return ""                  # model selaras kamus utk salah satu grup
    # Tak satu grup pun terpuaskan → model mengarang. Trigger TERPANJANG yang
    # muncul di pertanyaan dipakai ('cucuk per' menang atas 'per').
    t, e = max(cocok, key=lambda te: len(te[0]))
    args[field] = t
    kw = ", ".join(k for k in (e.get("keywords") or []) if k)
    logger.info("istilah dipaksa: %r -> %r (tool %s)", kata, t, name)
    return (f"⚠️ kata kunci buatanmu '{kata}' TIDAK sesuai istilah user '{t}' — "
            f"server MENGGANTINYA dengan istilah user (kamus lapangan resmi: "
            f"{kw or t}). Sajikan hasil di atas apa adanya; sebut padanan "
            "istilahnya dari kamus, ⛔ JANGAN memakai tafsiran sendiri.")


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
    # `_q_user` = pertanyaan user giliran ini, dititipkan chat loop (pola
    # _sheet_id: kunci server, BUKAN dari model). Di-pop agar handler & log
    # tak pernah melihatnya; arity _run_tool tetap 4 (banyak test mem-patch).
    question = str(args.pop("_q_user", "") or "")
    if name.startswith("sheet_"):
        # sheet_id datang dari server (lampiran giliran ini), BUKAN dari model —
        # model tak boleh memilih file milik siapa pun lewat argumen.
        args["_sheet_id"] = sheet_id
    catatan_istilah = _paksa_istilah_kamus(name, args, question)
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
    # PENJAGA STOK TERPUSAT — kembaran penjaga harga di atas. Menu Control 'Kolom
    # Stok' (col_stok) dulu hanya ditulis & tak pernah dibaca server, jadi angka
    # stok bocor ke staf yang centangnya dimatikan lewat SEMUA tool ber-EPC
    # (part_aus_dari_rangka, bom_dari_rangka, cari_part_di_unit, …).
    if isinstance(res, dict) and not _boleh_stok(user):
        _strip_stok(res)
    if catatan_istilah and isinstance(res, dict):
        # Key TERAKHIR dengan sengaja — _cap_tool_content memotong tengah,
        # instruksi di ekor selamat.
        res["catatan_istilah"] = catatan_istilah
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
    Hasil raksasa (banding_rangka_massal, bom_dari_rangka) bila di-append penuh
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


# ── Proyeksi per-tool SALINAN MODEL (bukan side-state) ──────────────────────
# Field yang model TAK butuh untuk menjawab dibuang dari salinan yang
# diserialisasi ke messages; hasil UTUH tetap mengalir ke _capture_meta
# (kartu unduh Excel / gambar inline dibangun dari side-state, bukan dari
# teks pesan tool). INVARIAN: hanya BUANG key — JANGAN rename, JANGAN buang
# baris ber-PN, JANGAN ubah urutan (catatan/jawaban_wajib wajib tetap di
# EKOR dict utk _cap_tool_content potong kepala+ekor). Hasil GAGAL
# (_tool_fail_kind != "") lolos utuh agar steering error tak terpangkas.

def _proj_drop(result: dict, keys: tuple[str, ...]) -> dict:
    return {k: v for k, v in result.items() if k not in keys}


def _proj_gambar_tanpa_image_id(result: dict) -> dict:
    """Baris gambar[]: buang image_id (opaque; gambar inline dibangun dari
    side-state exploded_images) — pn/balon/nama_figure/kategori tetap."""
    out = dict(result)
    if isinstance(out.get("gambar"), list):
        out["gambar"] = [
            {k: v for k, v in g.items() if k != "image_id"}
            if isinstance(g, dict) else g
            for g in out["gambar"]
        ]
    return out


_PROJECTIONS = {
    # ringkasan_kategori = duplikat persis kategori_beda + kategori_seragam
    # (mode semua_kategori); export_id = id kartu unduh (side-state).
    "banding_rangka_massal": lambda r: _proj_drop(r, ("ringkasan_kategori",
                                                      "export_id")),
    "katalog_kategori": lambda r: _proj_drop(r, ("export_id",)),
    "katalog_mesin": lambda r: _proj_drop(r, ("export_id",)),
    "gambar_exploded": _proj_gambar_tanpa_image_id,
    "gambar_exploded_mesin": _proj_gambar_tanpa_image_id,
    "uraikan_assembly": _proj_gambar_tanpa_image_id,
    "uraikan_mesin": _proj_gambar_tanpa_image_id,
    "part_aus_dari_rangka": _proj_gambar_tanpa_image_id,
    "cari_pengetahuan": _proj_gambar_tanpa_image_id,
    "buka_pengetahuan": _proj_gambar_tanpa_image_id,
}


def _project_for_model(name: str, result):
    if not isinstance(result, dict) or _tool_fail_kind(result):
        return result
    fn = _PROJECTIONS.get(name)
    return fn(result) if fn else result


def _dump_tool(result, name: str = "") -> str:
    """Serialisasi hasil tool untuk konsumsi model: proyeksi per-tool (buang
    field yang model tak butuh) + dikompakkan (buang field kosong) + separator
    rapat (tanpa spasi). Dipakai SEKALI lalu string yang sama dipakai ekstraksi
    PN & konten yang di-append — guard PN melihat persis yang dilihat model."""
    return json.dumps(_compact_result(_project_for_model(name, result)),
                      ensure_ascii=False, separators=(",", ":"), default=str)


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


# Marker string error yang menandakan gangguan INFRA (bukan lookup nihil jujur).
# Sengaja sempit: banyak hasil not-found sah membawa `error` penjelas — kehadiran
# `error` saja BUKAN bukti infra rusak.
_FAIL_INFRA_MARKERS = ("jaringan", "gangguan internal")


def _tool_fail_kind(result) -> str:
    """Klasifikasi kegagalan hasil tool untuk telemetri:
    ""    → sukses;
    "nf"  → lookup jujur nihil (found/ditemukan/tersedia == False) — data memang
            tidak ada, bukan sistem rusak;
    "err" → error/ditolak/infra (exception, denied, token EPC, jaringan).
    Statistik lama menyatukan keduanya → tool not-found jujur tampak "rusak"."""
    if not isinstance(result, dict):
        return ""
    if result.get("denied") or result.get("_token_issue"):
        return "err"
    for k in ("found", "ditemukan", "tersedia"):
        if result.get(k) is False:
            err = str(result.get("error") or "").lower()
            if any(m in err for m in _FAIL_INFRA_MARKERS):
                return "err"
            return "nf"
    if result.get("error"):
        return "err"
    return ""


def _tool_failed(result: dict) -> bool:
    """True bila hasil tool = kegagalan/kekosongan lookup (error, ditolak, atau
    'tidak ditemukan'). Dipakai untuk mengingatkan model agar TIDAK mengarang
    stok/harga saat data sebenarnya gagal diambil (guard PN tak menangkap angka)."""
    return bool(_tool_fail_kind(result))


def _catat_tool_gagal(daftar: list[str], name: str, kind: str) -> None:
    """Catat entri `nama:kind` ke daftar tools_failed giliran ini — dedupe per
    NAMA; nf lalu err di giliran sama → upgrade ke err (sinyal lebih kuat)."""
    for i, e in enumerate(daftar):
        n, _, k = e.partition(":")
        if n == name:
            if k == "nf" and kind == "err":
                daftar[i] = f"{name}:{kind}"
            return
    daftar.append(f"{name}:{kind}")


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
