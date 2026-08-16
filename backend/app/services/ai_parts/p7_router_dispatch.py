# -*- coding: utf-8 -*-
# ai_parts/p7_router_dispatch.py — BAGIAN dari modul app.services.ai_assistant (SATU namespace).
# Dimuat berurutan oleh ai_assistant.py (loader) via exec — sengaja BUKAN
# submodul terpisah: monkeypatch test `ai.<attr>`, relative import, dan
# resolusi global lintas-bagian tetap bekerja persis seperti file tunggal.
# Urutan muat & pembagian: lihat _PARTS di ai_assistant.py.
from __future__ import annotations

def _t_gambar_exploded_satu(args: dict, user: dict) -> dict:
    """Jalur SATU PN (perilaku lama persis, termasuk auto-fallback Atlas→mesin)."""
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


def _t_gambar_exploded(args: dict, user: dict) -> dict:
    """MULTI-PN (2026-07-23): log produksi — model memanggil tool ini 4× beruntun
    dalam satu giliran (1 PN per panggilan). `pn` kini menerima ARRAY atau string
    berpemisah ';'/',' (maks 4 PN, dedup); tiap PN tetap lewat jalur satu-PN utuh
    (termasuk auto-fallback Atlas→mesin), hasil diagregasi + status per-PN."""
    pn_raw = args.get("pn") or args.get("part_number") or ""
    if isinstance(pn_raw, (list, tuple)):
        pns = [str(x).strip().upper() for x in pn_raw if str(x).strip()]
    else:
        pns = [p.strip().upper() for p in re.split(r"[;,]", str(pn_raw)) if p.strip()]
    pns = list(dict.fromkeys(pns))[:4]
    # TANPA rangka tiap PN lewat jalur lintas-model yang bisa memakan puluhan detik
    # saat dingin → batasi 2 PN agar giliran chat tak menggantung. Dipotong secara
    # TERBUKA (dilaporkan di hasil), bukan diam-diam.
    dipotong: list[str] = []
    if not (args.get("rangka") or "").strip() and len(pns) > 2:
        dipotong, pns = pns[2:], pns[:2]
    if len(pns) <= 1:
        return _t_gambar_exploded_satu({**args, "pn": (pns[0] if pns else "")}, user)

    a_multi = {k: v for k, v in args.items() if k != "balon"}  # balon = mode 1 PN saja
    per_pn: list[dict] = []
    gambar: list[dict] = []
    nihil: list[str] = []
    for p in pns:                                   # sekuensial — fallback per-PN utuh
        h = _t_gambar_exploded_satu({**a_multi, "pn": p}, user)
        ok = bool(h.get("found"))
        row = {"pn": p, "found": ok,
               "sumber_dipakai": h.get("sumber_dipakai") or "atlas"}
        if not ok:
            row["error"] = h.get("error") or h.get("catatan")
            nihil.append(p)
        else:
            for g in (h.get("gambar") or [])[:2]:   # multi-PN: maks 2 figure per PN
                gambar.append({**g, "pn": p})
        per_pn.append(row)
    return {"found": bool(gambar), "pns": pns, "per_pn": per_pn,
            "pn_nihil": nihil, "gambar": gambar[:_MAX_EXPLODED_FIGURES],
            **({"pn_belum_diproses": dipotong} if dipotong else {}),
            "catatan": ("Gambar exploded SIAP untuk PN ber-found=true (tampil inline "
                        "otomatis). 'pn_nihil' = PN TANPA gambar — sampaikan jujur, "
                        "⛔ jangan mengarang. 'balon' diabaikan pada mode multi-PN — "
                        "sorot balon hanya via panggilan 1 PN."
                        + (f" ⚠️ Tanpa nomor rangka hanya 2 PN diproses sekaligus; "
                           f"BELUM diproses: {', '.join(dipotong)} — sampaikan ini & "
                           "tawarkan memprosesnya di giliran berikutnya."
                           if dipotong else ""))}


def _t_gambar_exploded_mesin(args: dict, user: dict) -> dict:
    return _gambar_exploded_mesin_impl(args, user)


def _t_uraikan_assembly(args: dict, user: dict) -> dict:
    _b = _batch_wrap(_t_uraikan_assembly, args, user, "assembly", maks=8, min_len=2)
    if _b is not None:
        return _b
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
    "turunan_assembly": _t_turunan_assembly,
    "uraikan_mesin": _t_uraikan_mesin,
    "part_dari_mesin": _t_part_dari_mesin,
    "cek_massal_part_mesin": _t_cek_massal_part_mesin,
    "cek_massal_part_rangka": _t_cek_massal_part_rangka,
    "spek_massal_rangka": _t_spek_massal_rangka,
    "banding_konfigurasi_rangka": _t_banding_konfigurasi_rangka,
    "pengganti_part": _t_pengganti_part,
    "repair_kit_mesin": _t_repair_kit_mesin,
    "unit_dari_part": _t_unit_dari_part,
    "cek_kendaraan": _t_cek_kendaraan,
    "assembly_utama_unit": _t_assembly_utama_unit,
    "bom_dari_rangka": _t_bom_dari_rangka,
    "cari_part_di_unit": _t_cari_part_di_unit,
    "filter_unit": _t_filter_unit,
    "part_kolong": _t_part_kolong,
    "banding_rangka": _t_banding_rangka,
    "banding_rangka_massal": _t_banding_rangka_massal,
    "part_aus_dari_rangka": _t_part_aus_dari_rangka,
    "part_fast_moving": _t_part_fast_moving,
    "repair_kit_transmisi": _t_repair_kit_transmisi,
    "banding_assy": _t_banding_assy,
    "isi_assy": _t_isi_assy,
    "banding_kategori": _t_banding_kategori,
    "isi_kategori": _t_isi_kategori,
    "part_termasuk_assy": _t_part_termasuk_assy,
    "kategori_massal_part": _t_kategori_massal_part,
    "spek_mesin": _t_spek_mesin,
    "part_aus_katalog": _t_part_aus_katalog,
    "jadwal_servis_truk": _t_jadwal_servis_truk,
    "daftar_transmisi_assy": _t_daftar_transmisi_assy,
    "cek_populasi": _t_cek_populasi,
    "banding_part_armada": _t_banding_part_armada,
    "detail_part": _t_detail_part,
    "cek_massal_part": _t_cek_massal_part,
    "stok_accurate": _t_stok_accurate,
    "harga_sims": _t_harga_sims,
    "info_aplikasi": _t_info_aplikasi,
    "stok_gudang": _t_stok_gudang,
    "stok_tertahan": _t_stok_tertahan,
    "permintaan_tak_terlayani": _t_permintaan_tak_terlayani,
    "pesanan_bermasalah": _t_pesanan_bermasalah,
    "alternatif_ready": _t_alternatif_ready,
    "daftar_unit": _t_daftar_unit,
    "cari_kode_kesalahan": _t_cari_kode_kesalahan,
    "diagnosa": _t_diagnosa,
    "cari_filter_shantui": _t_cari_filter_shantui,
    "tipe_unit_shantui": _t_tipe_unit_shantui,
    "part_shantui": _t_part_shantui,
    "cari_part_shantui": _t_cari_part_shantui,
    "gambar_exploded_shantui": _t_gambar_exploded_shantui,
    "jadwal_perawatan": _t_jadwal_perawatan,
    "info_part": _t_info_part,
    "foto_resmi_part": _t_foto_resmi_part,
    "tanya_user": _t_tanya_user,
    "cek_garansi": _t_cek_garansi,
    "riwayat_klaim": _t_riwayat_klaim,
    "detail_klaim": _t_detail_klaim,
    "sheet_garansi_massal": _t_sheet_garansi_massal,
    "excel_riwayat_klaim": _t_excel_riwayat_klaim,
    "rekap_klaim": _t_rekap_klaim,
    "kasus_serupa": _t_kasus_serupa,
    "lihat_unit_armada": _t_lihat_unit_armada,
    "terakhir_online": _t_terakhir_online,
    "ganti_nama_unit": _t_ganti_nama_unit,
    "excel_unit_armada": _t_excel_unit_armada,
    "sheet_isi_nama_telematik": _t_sheet_isi_nama_telematik,
    "daftarkan_unit": _t_daftarkan_unit,
    "sheet_daftar_unit": _t_sheet_daftar_unit,
    "masukkan_unit_fleet": _t_masukkan_unit_fleet,
    "sheet_masukkan_fleet": _t_sheet_masukkan_fleet,
    "daftar_fleet": _t_daftar_fleet,
    "buat_fleet": _t_buat_fleet,
    "diagram_wiring": _t_diagram_wiring,
    "cari_manual": _t_cari_manual,
    "manual_unit": _t_manual_unit,
    "manual_unit_file": _t_manual_unit_file,
    "cari_pengetahuan": _t_cari_pengetahuan,
    "buka_pengetahuan": _t_buka_pengetahuan,
    "ajarkan_pengetahuan": _t_ajarkan_pengetahuan,
    "topik_gagal": _t_topik_gagal,
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
    "sheet_isi_gambar": _t_sheet_isi_gambar,
    "sheet_isi_foto": _t_sheet_isi_foto,            # shim (lihat _LEGACY_TOOL_ALIAS)
    "sheet_isi_exploded": _t_sheet_isi_exploded,    # shim
    "sheet_isi_part_number": _t_sheet_isi_part_number,
    "sheet_cek_qty": _t_sheet_cek_qty,
    "sheet_pilih_sheet": _t_sheet_pilih_sheet,
    "buat_penawaran": _t_buat_penawaran,
    "buat_permintaan_barang": _t_buat_permintaan_barang,
}


# ── Penjaga istilah lapangan (2026-07-22) ────────────────────────────
# Kasus produksi "cucuk per": kamus sinonim tahu itu spring pin, tapi model DUA
# KALI mengarang terjemahannya sendiri ("cross joint", lalu "fuel injector")
# meski aturan prompt + [KAMUS ISTILAH GILIRAN INI] melarang — soft guard
# terbukti kalah. Penegakan deterministik: bila pertanyaan user memuat trigger
# kamus dan kata kunci model tidak memuaskan SATU grup pun (bukan trigger,
# bukan keyword resmi, bukan PN), kata kunci model DITIMPA istilah mentah user
# — ekspansi sinonim di handler yang menerjemahkannya dengan benar.
# Guard-nya generik; yang dulu kurang cuma TABEL ini — tool pencarian yang tak
# terdaftar tetap menerima karangan model apa adanya. Nama field = nama argumen
# sebenarnya di _tool_specs/handler (alias ikut didaftarkan bila handler
# menerimanya), dan SEMUA tool di sini meneruskan nilainya lewat _expand_query,
# jadi menimpanya dengan istilah MENTAH user justru yang benar.
_TOOLS_ISTILAH = {
    "cari_part": ("query",),
    "cari_part_di_unit": ("kata_kunci", "query"),
    "part_aus_dari_rangka": ("query",),
    "stok_gudang": ("kata_kunci", "query"),
    "cek_massal_part_rangka": ("part", "query"),
    "cek_massal_part_mesin": ("part", "query"),
    "banding_part_armada": ("part",),
    "bom_dari_rangka": ("kata_kunci",),
    "excel_bom_rangka": ("kata_kunci",),
    "excel_stok_gudang": ("kata_kunci", "query"),
}


# Kata terlalu umum untuk membuktikan apa pun bila muncul di kedua sisi.
_ISTILAH_STOP = frozenset(
    "part number nomor kode cari carikan cek cekin tolong untuk dari dan atau "
    "yang ada punya buat mau saya unit truk mobil depan belakang kiri kanan "
    "atas bawah besar kecil baru lama".split())


def _berakar_di_pertanyaan(kata: str, question: str) -> bool:
    """Apakah istilah model tumbuh dari kata yang DIKETIK user sendiri?

    Ini pembeda yang benar antara dua kasus yang tampak sama bagi guard:

      • "carikan cucuk per"        → model menulis "cross joint".
        Tak satu kata pun ('cross', 'joint') ada di pertanyaan → itu tafsiran
        model, dan tafsirannya SALAH. Timpa dengan istilah mentah user.

      • "cek per daun dan bearing roda" → model menulis "wheel bearing".
        Kata 'bearing' ADA di pertanyaan → model sedang melayani permintaan
        KEDUA user, bukan salah menerjemahkan yang pertama. Menimpanya membuang
        separuh pertanyaan.

    Sengaja BUKAN "apakah istilahnya ada di katalog": "cross joint" itu nama part
    yang benar-benar ada — persis sebabnya insiden asli lolos pagar seperti itu.
    Yang salah bukan keberadaannya, melainkan bahwa ia tak pernah disebut user.
    """
    q = (question or "").casefold()
    for w in re.findall(r"[a-z]{4,}", (kata or "").casefold()):
        if w in _ISTILAH_STOP:
            continue
        if w in q:
            return True
    return False


def _paksa_istilah_kamus(name: str, args: dict, question: str) -> str:
    """Mutasi `args` in-place bila model mengarang istilah; return catatan
    untuk model ("" = tidak menimpa apa-apa)."""
    fields = _TOOLS_ISTILAH.get(name)
    if not fields or not (question or "").strip():
        return ""
    field = next((f for f in fields if str(args.get(f) or "").strip()), None)
    if not field:
        return ""
    kata_raw = args.get(field)
    # kata_kunci boleh ARRAY (multi-istilah cari_part_di_unit) — cek gabungannya.
    if isinstance(kata_raw, (list, tuple)):
        kata = "; ".join(str(x).strip() for x in kata_raw if str(x).strip())
    else:
        kata = str(kata_raw or "").strip()
    # PN eksplisit dari model jangan diganggu — TAPI "mengandung satu angka" ≠ PN.
    # Cek lama (any digit) mematikan penjaga untuk kata kunci lapangan yang wajar
    # memuat kode unit/ukuran ('cucuk per NX400', 'gardan 6X4', 'seal 24 mm'), yaitu
    # persis kalimat tempat istilah lapangan paling sering dikarang. Kini dipakai
    # definisi PN yang sama dengan guard anti-halusinasi (_extract_pns): ≥7 char
    # huruf+angka, atau ≥9 digit kontigu (PN numerik Weichai).
    if _extract_pns(kata):
        return ""
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
    # Tak satu grup pun terpuaskan. Trigger TERPANJANG yang muncul di pertanyaan
    # dipakai ('cucuk per' menang atas 'per').
    t, e = max(cocok, key=lambda te: len(te[0]))
    kw = ", ".join(k for k in (e.get("keywords") or []) if k)

    if isinstance(kata_raw, (list, tuple)):
        # Nilai array: istilah user DITAMBAHKAN (istilah lain di daftar bisa sah).
        args[field] = [*[str(x).strip() for x in kata_raw if str(x).strip()], t]
    elif _berakar_di_pertanyaan(kata, question):
        # Skalar, tapi istilah model BERAKAR pada kata user sendiri → jangan ganti.
        logger.info("istilah dibiarkan (berakar di pertanyaan): %r vs user %r (tool %s)",
                    kata, t, name)
        return (f"ℹ️ kata kuncimu '{kata}' berakar pada kata user sendiri, jadi "
                f"server TIDAK menggantinya. Tapi user juga menyebut '{t}' "
                f"(kamus lapangan resmi: {kw or t}) — itu permintaan TERPISAH. "
                f"Pastikan KEDUANYA terjawab: bila perlu cari sekali lagi dengan "
                "istilah itu. Sebut padanan istilahnya saat menjawab.")
    else:
        args[field] = t
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
    # `_cid` = conversation_id giliran ini, titipan chat loop dengan alasan yang
    # sama: draf-tertunda `ajarkan_pengetahuan` dikunci per-percakapan, dan kunci
    # itu TIDAK boleh datang dari model (kalau boleh, model bisa menunjuk draf
    # percakapan orang lain). Di-pop lebih dulu supaya tool LAIN & log tak pernah
    # melihatnya, lalu ditaruh kembali khusus untuk tool yang memang memakainya
    # (pola `_sheet_id`).
    cid = str(args.pop("_cid", "") or "")
    if name == "ajarkan_pengetahuan":
        args["_cid"] = cid
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
    # Semua tool pengisi Excel lampiran dilebur ke sheet_isi_kolom (2026-08-06):
    # "stok + foto + exploded" wajib jadi SATU file, jadi alat pengisinya pun satu.
    "sheet_isi_gambar": "sheet_isi_kolom",
    "sheet_isi_foto": "sheet_isi_kolom",
    "sheet_isi_exploded": "sheet_isi_kolom",
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

# Plafon char SELURUH hasil tool dalam SATU ronde (lihat _cap_ronde). Plafon
# per-hasil saja tak cukup begitu satu ronde boleh berisi banyak panggilan:
# 10 × 24.000 char ≈ 60rb token input DALAM SATU RONDE, dan `messages` dikirim
# ulang utuh tiap panggilan berikutnya (biaya kuadratik). Observabilitas 30 hari
# menunjukkan token input sudah p90 221rb & maks 661rb per giliran — melebarkan
# ronde tanpa pagar ini akan memperburuk angka itu, bukan memperbaikinya.
#
# Nilainya = 4 × _MAX_TOOL_CONTENT dengan sengaja: ronde SEMPIT (≤4 hasil, yang
# selama ini paling umum) mendapat jatah penuh PERSIS seperti sebelumnya —
# penyempitan hanya berlaku pada ronde lebar yang memang baru mungkin terjadi
# setelah plafon per-tool dinaikkan. Jadi tak ada ronde lama yang jadi kekurangan.
_MAX_TOOL_CONTENT_RONDE = 96000
_MIN_TOOL_CONTENT = 4000   # lantai: hasil yang terlalu diciutkan tak berguna


def _cap_ronde(n_hasil: int) -> int:
    """Plafon char per hasil bila SATU ronde mengembalikan `n_hasil` hasil tool."""
    if n_hasil <= 1:
        return _MAX_TOOL_CONTENT
    return max(_MIN_TOOL_CONTENT,
               min(_MAX_TOOL_CONTENT, _MAX_TOOL_CONTENT_RONDE // n_hasil))


def _cap_tool_content(s: str, plafon: int | None = None) -> str:
    """Batasi panjang JSON hasil tool yang dimasukkan ke riwayat percakapan.
    Hasil raksasa (banding_rangka_massal, bom_dari_rangka) bila di-append penuh
    tiap ronde membuat token membengkak & bisa menembus limit konteks model
    (→ API 400 → 502). Tool tetap mengembalikan data lengkap ke frontend lewat
    metadata; yang dipotong hanya salinan untuk konsumsi model.

    Potong KEPALA + EKOR (bukan kepala saja): tool builder menaruh instruksi
    penyetir model (`catatan`/`jawaban_wajib`/`catatan_cakupan`) di UJUNG dict —
    potong-kepala-saja menghapusnya senyap, membuat model kehilangan aturan
    (mis. '⛔ jangan mengarang PN di luar daftar').

    `plafon` (opsional) = plafon char untuk hasil INI; dipakai jalur batch agar
    ronde ber-banyak-hasil tetap muat anggaran (lihat _cap_ronde). Default =
    _MAX_TOOL_CONTENT, persis perilaku lama."""
    batas = max(_MIN_TOOL_CONTENT, int(plafon or _MAX_TOOL_CONTENT))
    if len(s) <= batas:
        return s
    dipotong = len(s) - batas
    marker = (f"\n…[dipotong {dipotong} karakter di tengah — hasil terlalu besar; "
              "rangkum dari bagian atas & bawah, jangan menebak yang hilang]…\n")
    ekor = min(_TOOL_CAP_TAIL, max(600, batas // 4))
    head = batas - ekor - len(marker)
    return s[:head] + marker + s[-ekor:]


# Key yang KOSONGNYA adalah JAWABAN, bukan ketiadaan data. Untuk key lain
# "tak ada field itu" dan "field itu kosong" sama saja bagi model; untuk yang
# ini TIDAK: `digantikan_oleh: []` berarti "sudah dicek, tak ada pengganti",
# sedangkan field yang hilang terbaca "belum dicek" — dan justru pertanyaan
# 'ada penggantinya?' / 'ada stok di gudang lain?' yang paling sering dijawab
# dengan tebakan saat field-nya senyap. (Kegagalan cek tetap dibedakan lewat
# _tool_fail_kind / sumber_dicek, bukan lewat kekosongan ini.)
_COMPACT_KEEP_EMPTY = frozenset((
    "pengganti", "digantikan_oleh", "menggantikan", "stok_per_gudang",
))


def _compact_result(v):
    """Buang field KOSONG (None, '', [], {}) secara rekursif dari hasil tool
    sebelum diserialisasi ke model — memangkas 15-35% char hasil tool, komponen
    biaya token UNCACHED terbesar per giliran (system prompt sudah ter-cache).
    ⛔ JANGAN buang boolean atau 0: _tool_failed & guard bergantung pada
    found:False / denied / stok 0. ⛔ JANGAN buang key di _COMPACT_KEEP_EMPTY:
    kosongnya bermakna 'sudah dicek & nihil'. ⛔ JANGAN rename key (nama key =
    kosakata yang sudah dikenal model). Panjang elemen LIST tak diubah."""
    if isinstance(v, dict):
        out = {}
        for k, val in v.items():
            c = _compact_result(val)
            if (c is None or c == "" or c == [] or c == {}) \
                    and k not in _COMPACT_KEEP_EMPTY:
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
    "gambar_exploded_shantui": _proj_gambar_tanpa_image_id,
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
_TRIM_DIGEST_CAP = 200     # panjang maks INTISARI di dalam stub (char)


def _digest_tool_content(name: str, content: str) -> str:
    """INTISARI deterministik isi hasil tool ronde lama (≤_TRIM_DIGEST_CAP char).

    Stub lama kosong total: setelah dua ronde, seluruh pasangan PN↔unit yang
    ditemukan ronde awal lenyap dari pandangan model, padahal grounding masih
    menerimanya — jadi model tahu 'PN ini boleh disebut' tapi lupa milik unit
    mana. Rantai panjang (banding armada, cek massal per-VIN) persis yang
    memakai trimming ini, dan persis yang paling butuh pasangannya.

    Dibangun dari CONTENT-nya sendiri (bukan dari args/result) supaya tak ada
    state baru yang harus dititipkan chat loop: content = string yang sama yang
    dilihat model. Bila isinya JSON → dipakai `_fakta_from_tool` (satu-satunya
    peringkas hasil tool yang sudah ada & sudah teruji); bila tidak (hasil
    terpotong _cap_tool_content / jalur tool bocor) → jatuh ke token mirip-PN.
    Tak pernah melempar: stub polos selalu jadi cadangan."""
    teks = str(content or "")
    obj = None
    awal = teks.find("{")
    if awal >= 0:
        try:
            obj = json.loads(teks[awal:])
        except Exception:
            obj = None
    baris: list[str] = []
    if isinstance(obj, dict):
        try:
            # Hasil tool memuat konteksnya sendiri (rangka/no mesin) sebagai key
            # biasa → dipakai juga sbg sumber 'args' untuk label _fakta_ctx.
            baris = _fakta_from_tool(name, obj, obj) or []
        except Exception:  # pragma: no cover — peringkas rusak ≠ chat mati
            logger.exception("digest stub gagal (dilewati) tool=%s", name)
            baris = []
    digest = "; ".join(b for b in baris if b)
    if not digest:
        pns = sorted(_extract_pns(teks))[:5]
        digest = ("PN: " + ", ".join(pns)) if pns else ""
    if len(digest) <= _TRIM_DIGEST_CAP:
        return digest
    # Potong di BATAS pemisah, jangan di tengah token: PN separuh ('…12345' dari
    # '…1234567') adalah PN yang tidak pernah ada, dan stub ini dibaca model.
    potong = digest[:_TRIM_DIGEST_CAP]
    for sep in ("; ", ", ", " "):
        p = potong.rfind(sep)
        if p >= _TRIM_DIGEST_CAP // 2:
            potong = potong[:p]
            break
    return potong.rstrip(" ,;") + " …"


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
    bernalar atas data terkini. Stub membawa INTISARI (_digest_tool_content) —
    hemat konteks tanpa membuat model buta pada apa yang sudah ia temukan."""
    batas = cur_round - keep_last
    if batas < 1:
        return
    for e in tool_msg_idx:
        if e.get("stubbed") or e["round"] > batas:
            continue
        i = e["i"]
        if 0 <= i < len(messages):
            nama = e.get("name") or "tool"
            inti = _digest_tool_content(nama, messages[i].get("content") or "")
            messages[i]["content"] = (
                f"[hasil {nama} (ronde {e['round']}) sudah dipakai — diringkas "
                "untuk hemat konteks"
                + (f"; intisari: {inti}" if inti else "")
                + ". Panggil ulang tool bila butuh rinciannya]")
        e["stubbed"] = True


# Marker string error yang menandakan gangguan INFRA (bukan lookup nihil jujur).
# Tetap sempit — kehadiran `error` saja BUKAN bukti infra rusak (banyak hasil
# not-found sah membawa `error` penjelas) — tapi daftar lama cuma 2 frasa,
# sedangkan pesan infra NYATA di repo berbunyi macam-macam: "Gagal menghubungi
# server EPC", "SIMS tidak merespons — coba lagi sebentar lagi", "Accurate belum
# terkonfigurasi/aktif", "Sesi Accurate kadaluarsa", "Indeks stok per-gudang
# sedang disiapkan", "Accurate bermasalah saat mencari". Semuanya dulu jatuh ke
# 'nf' → model diberi tahu "data memang tidak ada" padahal tak ada yang pernah
# dicek. Frasa di bawah dipungut dari string error/pesan yang benar-benar ada di
# kode (bukan tebakan), dan sengaja BERFRASA (bukan kata tunggal seperti
# 'server'/'sesi') supaya tak menyenggol kalimat not-found yang sah.
_FAIL_INFRA_MARKERS = (
    "jaringan", "gangguan internal", "gagal", "coba lagi", "timeout",
    "tidak merespons", "tak merespons", "terkonfigurasi", "bermasalah",
    "sedang disiapkan", "kadaluarsa", "kedaluwarsa", "belum aktif",
    "tak dapat diakses", "tidak dapat diakses",
)

# Field pesan yang ikut dibaca saat mencari marker infra. `error` saja tak cukup:
# jalur Accurate mengembalikan {"tersedia": False, "pesan": "Sesi Accurate
# kadaluarsa …"} — TANPA key `error` sama sekali, jadi klasifikasi lama tak punya
# apa pun untuk dibaca dan otomatis memvonis 'nf'.
_FAIL_MSG_KEYS = ("error", "pesan")


def _tool_fail_kind(result) -> str:
    """Klasifikasi kegagalan hasil tool untuk telemetri:
    ""      → sukses;
    "brake" → DITOLAK rem anti-loop (_MAX_CALLS_PER_TOOL) — belum sempat dicari,
              BUKAN pernyataan tentang datanya;
    "nf"    → lookup jujur nihil (found/ditemukan/tersedia == False) — data memang
              tidak ada, bukan sistem rusak;
    "err"   → error/ditolak/infra (exception, denied, token EPC, jaringan).
    Statistik lama menyatukan keduanya → tool not-found jujur tampak "rusak".

    `brake` dipisah dari `nf` karena keduanya dulu tak terbedakan: hasil rem
    membawa found=False, sehingga model diberi nota "lookup gagal, jangan
    mengarang" dan menyimpulkan puluhan PN yang ditolak rem itu TIDAK ADA di
    data. Yang benar: mereka belum sempat dicek.

    Builder tool boleh MENYATAKAN jenisnya sendiri lewat `_err_kind` ("err"/"nf"/
    "brake"): jalur eksplisit selalu lebih kuat dari tebakan atas prosa, dan ia
    memberi tool baru cara menandai "gagal cek" tanpa harus menitipkan kata kunci
    tertentu di kalimat error."""
    if not isinstance(result, dict):
        return ""
    kind = str(result.get("_err_kind") or "").strip().lower()
    if kind in _FAIL_KIND_RANK:
        return kind
    if result.get("dibatasi"):
        return "brake"
    if result.get("denied") or result.get("_token_issue"):
        return "err"
    # Tool yang menggabung BEBERAPA sumber dan salah satunya gagal diperiksa:
    # hasilnya found=False, tapi itu BUKAN pernyataan "data tidak ada" — sumbernya
    # memang belum sempat ditanya. Field eksplisit, bukan pencocokan substring
    # pada kalimat prosa (_FAIL_INFRA_MARKERS): itu rapuh & sulit dites.
    if result.get("_cek_tak_lengkap"):
        return "err"
    for k in ("found", "ditemukan", "tersedia"):
        if result.get(k) is False:
            teks = " ".join(str(result.get(mk) or "") for mk in _FAIL_MSG_KEYS).lower()
            if any(m in teks for m in _FAIL_INFRA_MARKERS):
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


# Kekuatan sinyal, lemah → kuat. `brake` paling lemah: ia tak mengatakan apa pun
# tentang data maupun kesehatan sistem, hanya "belum sempat dijalankan". Jadi
# begitu tool yang sama benar-benar nihil atau error di giliran itu, catatannya
# di-upgrade — telemetri harus melaporkan fakta terkuat yang kita punya.
_FAIL_KIND_RANK = {"brake": 1, "nf": 2, "err": 3}


def _catat_tool_gagal(daftar: list[str], name: str, kind: str) -> None:
    """Catat entri `nama:kind` ke daftar tools_failed giliran ini — dedupe per
    NAMA; sinyal yang lebih kuat menimpa yang lemah (brake < nf < err)."""
    for i, e in enumerate(daftar):
        n, _, k = e.partition(":")
        if n == name:
            if _FAIL_KIND_RANK.get(kind, 0) > _FAIL_KIND_RANK.get(k, 0):
                daftar[i] = f"{name}:{kind}"
            return
    daftar.append(f"{name}:{kind}")


# Argumen IDENTITAS (barang/unit APA) dan argumen PENCARIAN (dicari APA).
# Keduanya dikutip bila ada: 'cari_part_di_unit(RT110061, kampas rem)'
# menjelaskan kegagalannya jauh lebih baik daripada salah satu saja — nomor
# rangka tanpa istilah, atau istilah tanpa unit, dua-duanya menyisakan tebakan.
_LOOKUP_GAGAL_ARG_ID = ("part_number", "pn", "daftar_pn", "rangka", "rangka_1",
                        "daftar_rangka", "no_mesin", "daftar_no_mesin",
                        "customer", "gudang")
_LOOKUP_GAGAL_ARG_CARI = ("query", "kata_kunci", "part", "topik", "komponen",
                          "kode", "code", "spn", "keluhan")
_LOOKUP_GAGAL_ARG_CAP = 40      # panjang maks TIAP nilai argumen yang dikutip


def _lookup_gagal_arg(args: dict) -> str:
    """Argumen KUNCI sebuah panggilan tool (yang menjelaskan 'gagal untuk apa') —
    maks satu identitas + satu kata pencarian; '' bila tak ada."""
    out: list[str] = []
    for grup in (_LOOKUP_GAGAL_ARG_ID, _LOOKUP_GAGAL_ARG_CARI):
        for k in grup:
            v = (args or {}).get(k)
            if isinstance(v, (list, tuple)):
                v = ", ".join(str(x).strip() for x in v if str(x).strip())
            s = " ".join(str(v or "").split())
            if s:
                out.append(s[:_LOOKUP_GAGAL_ARG_CAP])
                break
    return ", ".join(out)


def _lookup_gagal_note(gagal=()) -> str:
    """[CATATAN SISTEM] 'lookup gagal' — SEBUTKAN tool & argumennya.

    `gagal` = daftar (nama_tool, args) panggilan yang gagal giliran ini; kosong →
    nota generik (perilaku lama persis). Nota generik itu masalahnya: ia disuntik
    SEKALI per giliran, jadi saat 5 tool dipanggil dan 1 gagal, model tak punya
    cara tahu YANG MANA — dan pilihan amannya (menganggap semuanya meragukan,
    atau justru mengabaikan notanya) dua-duanya salah. Menyebut nama tool +
    argumen kuncinya membuat nota ini bisa ditindaklanjuti: model tahu persis PN/
    rangka mana yang tak boleh diklaim."""
    rincian: list[str] = []
    for nama, args in (gagal or ()):
        n = str(nama or "").strip()
        if not n:
            continue
        a = _lookup_gagal_arg(args if isinstance(args, dict) else {})
        item = f"{n}({a})" if a else n
        if item not in rincian:
            rincian.append(item)
    return (
        "[CATATAN SISTEM] "
        + (f"Tool berikut GAGAL/tak menemukan data: {'; '.join(rincian)}. "
           if rincian else "Ada tool yang GAGAL/tak menemukan data. ")
        + "DILARANG mengarang stok/harga/ketersediaan untuk item itu — sampaikan "
        "apa adanya, sarankan langkah (cek nomor / coba lagi / hubungi admin)."
        + (" Tool LAIN di giliran ini yang berhasil tetap boleh kamu pakai."
           if rincian else "")
    )


def _rem_note(rem=()) -> str:
    """[CATATAN SISTEM] rem anti-loop MENOLAK panggilan — item itu BELUM dicek.

    Kembaran `_lookup_gagal_note` untuk kelas yang berkebalikan artinya. Dulu
    penolakan rem hanya dititipkan sebagai field `catatan` DI DALAM satu hasil
    tool, terhimpit di antara belasan hasil lain — dan model melewatinya.
    Bukti produksi 2026-07-24: user menempel 9 nomor rangka, rem meloloskan 3,
    dan jawaban akhir menyatakan KESEMBILAN "tidak terdaftar di telematics".
    Enam di antaranya tak pernah dicek sama sekali.

    Karena itu penolakan rem naik ke tingkat PERCAKAPAN (pesan tersendiri,
    seperti nota lookup-gagal) dan menyebut item yang ditolak satu per satu:
    yang dilarang di sini bukan "mengarang angka" melainkan menyatakan HASIL
    NEGATIF ('tidak ada', 'tidak terdaftar') atas sesuatu yang belum dilihat."""
    rincian: list[str] = []
    for nama, args in (rem or ()):
        n = str(nama or "").strip()
        if not n:
            continue
        a = _lookup_gagal_arg(args if isinstance(args, dict) else {})
        item = f"{n}({a})" if a else n
        if item not in rincian:
            rincian.append(item)
    return (
        "[CATATAN SISTEM] Panggilan tool berikut DITOLAK rem anti-loop (plafon "
        "per giliran), jadi datanya BELUM PERNAH DICEK: "
        + ("; ".join(rincian) if rincian else "(beberapa panggilan)")
        + ". ⛔ DILARANG menyatakan 'tidak ada' / 'tidak ditemukan' / 'tidak "
        "terdaftar' / 'kosong' untuk item itu — kamu belum melihat datanya. "
        "Pilih SALAH SATU: (a) gabungkan sisanya dalam SATU panggilan tool "
        "massal (mis. cek_massal_part dengan array daftar_pn, spek_massal_rangka "
        "untuk banyak nomor rangka), ATAU (b) sebutkan APA ADANYA di jawaban "
        "bahwa item itu BELUM SEMPAT DICEK giliran ini dan tawarkan mengecek "
        "lanjutannya. Item yang benar-benar sudah dicek tetap boleh kamu "
        "simpulkan seperti biasa."
    )


_REM_TERTUNDA_CAP = 8       # item yang dikutip ke user; sisanya diringkas "+N lagi"


def _rem_tertunda_note(rem=()) -> str:
    """Catatan ke USER (bukan ke model): apa yang BELUM SEMPAT DICEK giliran ini.

    Dipasang di jalur akhir bila nota `_rem_note` ke model tak ditindaklanjuti.
    Sengaja TIDAK memakai frasa 'tak terverifikasi' — frasa itu penanda outcome
    'sanitized' di observabilitas, dan menumpanginya akan mengaburkan dua kelas
    yang berbeda (PN dugaan karangan vs lookup yang belum dijalankan)."""
    item: list[str] = []
    for nama, args in (rem or ()):
        n = str(nama or "").strip()
        if not n:
            continue
        a = _lookup_gagal_arg(args if isinstance(args, dict) else {})
        s = f"{n} — {a}" if a else n
        if s not in item:
            item.append(s)
    if not item:
        return ""
    sisa = len(item) - _REM_TERTUNDA_CAP
    daftar = "; ".join(item[:_REM_TERTUNDA_CAP]) + (f"; +{sisa} lagi" if sisa > 0 else "")
    return ("\n\n⚠️ Catatan sistem: batas pengecekan per giliran tercapai, jadi "
            f"berikut BELUM sempat dicek — {daftar}. Bila jawaban di atas "
            "menyatakan item itu tidak ada/tidak terdaftar, abaikan: mintalah "
            "saya mengeceknya lagi (boleh dipecah beberapa pesan).")


# Nota generik (tanpa rincian) — bentuk yang dipakai pemanggil lama di p9.
# ⚠️ Selama p9 masih menyuntikkan konstanta ini, rincian nama-tool BELUM sampai
# ke model; wiring-nya: ganti pemakaian `_LOOKUP_GAGAL_NOTE` dengan
# `_lookup_gagal_note([(nama, args), …])` atas panggilan yang _tool_fail_kind-nya
# != "brake" pada giliran itu.
_LOOKUP_GAGAL_NOTE = _lookup_gagal_note()


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
