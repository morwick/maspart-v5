# -*- coding: utf-8 -*-
# ai_parts/p6_tools_excel_katalog.py — BAGIAN dari modul app.services.ai_assistant (SATU namespace).
# Dimuat berurutan oleh ai_assistant.py (loader) via exec — sengaja BUKAN
# submodul terpisah: monkeypatch test `ai.<attr>`, relative import, dan
# resolusi global lintas-bagian tetap bekerja persis seperti file tunggal.
# Urutan muat & pembagian: lihat _PARTS di ai_assistant.py.
from __future__ import annotations

def _t_buat_excel(args: dict, user: dict) -> dict:
    """EXPORT EXCEL GENERIK — model menyusun judul+kolom+baris dari hasil tool
    percakapan; payload disimpan (ai_export.stash_export) dan frontend memunculkan
    kartu unduh. PN dalam isi WAJIB grounded (hasil tool/riwayat) — anti-karangan."""
    judul = str(args.get("judul") or "").strip() or "Data MASPART"
    kolom = [str(k).strip() for k in (args.get("kolom") or []) if str(k).strip()]
    baris_raw = args.get("baris") or []
    if not kolom or not isinstance(baris_raw, list) or not baris_raw:
        return {"error": "Isi 'kolom' (judul kolom) dan 'baris' (data) — disalin PERSIS "
                         "dari hasil tool yang sudah ada di percakapan ini."}

    baris: list[list[str]] = []
    for r in baris_raw[:_EXCEL_MAX_ROWS]:
        if isinstance(r, (list, tuple)):
            row = ["" if v is None else str(v) for v in r]
        elif isinstance(r, dict):   # model kadang mengirim object per baris
            row = ["" if r.get(k) is None else str(r.get(k)) for k in kolom]
        else:
            row = [str(r)]
        baris.append((row + [""] * len(kolom))[:len(kolom)])

    # Anti-halusinasi: token mirip-PN di isi file wajib pernah muncul dari tool /
    # riwayat percakapan (set 'grounded' disuntik chat() via _grounded).
    grounded = args.get("_grounded")
    if isinstance(grounded, set):
        toks: set[str] = set()
        for row in baris:
            for v in row:
                toks |= _extract_pns(v)
        bad = _drop_unit_tokens(sorted(t for t in toks if t not in grounded))
        if bad:
            return {"error": ("PN berikut TIDAK pernah muncul dari hasil tool/riwayat "
                              "percakapan (dugaan karangan): " + ", ".join(bad[:10]) +
                              ". ⛔ Isi Excel hanya dengan data PERSIS dari hasil tool — "
                              "panggil tool datanya dulu bila perlu, lalu ulangi buat_excel.")}

    export_id, filename = ai_export.stash_export(judul, kolom, baris)
    return {"found": True, "export_id": export_id, "filename": filename,
            "judul": judul, "jumlah_baris": len(baris),
            "catatan": ("File Excel siap — KARTU UNDUH otomatis muncul di bawah jawabanmu. "
                        "Jawab SINGKAT (sebut judul + jumlah baris). ⛔ JANGAN tulis ulang "
                        "tabelnya, JANGAN membuat link/URL unduhan sendiri.")}


def _t_hitung_part(args: dict, user: dict) -> dict:
    """HITUNG DETERMINISTIK atas part yang SUDAH ada di percakapan: total harga
    (± qty per item), urutkan termurah/termahal, filter harga/ready. SEMUA hitungan
    di Python (PASTI) atas harga OTORITATIF Accurate — model TAK menghitung sendiri
    (aritmatika LLM rawan salah). Harga → hanya untuk yang berhak (harga gate); PN
    wajib GROUNDED (anti-karangan). Kembalikan rincian + total + item tanpa harga."""
    if not _boleh_harga(user):
        return {"denied": True,
                "error": "Total/urutan harga hanya untuk pengguna yang berhak melihat harga."}
    items_in = args.get("items") or []
    if not isinstance(items_in, list) or not items_in:
        return {"error": "Sebutkan 'items' = daftar {pn, qty} yang mau dihitung/diurutkan."}

    # Normalisasi input {pn, qty}.
    pns_in: list[tuple[str, int]] = []
    for it in items_in:
        if isinstance(it, dict):
            pn = str(it.get("pn") or it.get("part_number") or "").strip().upper()
            try:
                qty = int(it.get("qty") or it.get("jumlah") or 1)
            except (TypeError, ValueError):
                qty = 1
        else:
            pn, qty = str(it).strip().upper(), 1
        if pn:
            pns_in.append((pn, max(1, qty)))
    if not pns_in:
        return {"error": "Tidak ada Part Number yang bisa dihitung."}

    # Anti-halusinasi: tiap PN wajib pernah muncul dari tool/riwayat (grounded).
    grounded = args.get("_grounded")
    if isinstance(grounded, set):
        bad = _drop_unit_tokens(sorted({pn for pn, _ in pns_in} - grounded))
        if bad:
            return {"error": ("PN berikut TIDAK pernah muncul dari hasil tool/riwayat "
                              "percakapan (dugaan karangan): " + ", ".join(bad[:10]) +
                              ". ⛔ Panggil tool datanya dulu, lalu ulangi hitung_part.")}

    # Angka MENTAH (bukan string 'Rp …') dari indeks Accurate + nama dari katalog.
    from . import accurate
    snap = accurate.snapshot()
    lokal = part_index.rows_for_pns([pn for pn, _ in pns_in])

    def _int_or_none(v):
        try:
            return int(v)
        except (TypeError, ValueError):
            return None

    urut = (args.get("urutkan") or "").strip().lower()
    hmax = _int_or_none(args.get("harga_maks"))
    hmin = _int_or_none(args.get("harga_min"))
    hanya_ready = bool(args.get("hanya_ready"))

    items_out: list[dict] = []
    tanpa_harga: list[str] = []
    total_num = 0
    seen: set[str] = set()
    for pn, qty in pns_in:
        if pn in seen:
            continue
        seen.add(pn)
        e = snap.get(accurate.index_key(pn) or accurate.norm_pn(pn)) if snap else None
        harga_num = _int_or_none((e or {}).get("harga"))
        stok_num = _int_or_none((e or {}).get("stok"))
        nama = " ".join(((lokal.get(pn, {}) or {}).get("part_name") or "").split())
        ready = bool(stok_num and stok_num > 0)
        if harga_num is None:
            tanpa_harga.append(pn)
            continue
        if hmax is not None and harga_num > hmax:
            continue
        if hmin is not None and harga_num < hmin:
            continue
        if hanya_ready and not ready:
            continue
        subtotal = harga_num * qty
        total_num += subtotal
        items_out.append({
            "part_number": pn, "nama": nama,
            "harga": _rp(harga_num), "harga_num": harga_num,
            "qty": qty, "subtotal": _rp(subtotal), "subtotal_num": subtotal,
            "stok": stok_num, "ready": ready,
        })

    if urut in ("termurah", "murah", "asc", "naik"):
        items_out.sort(key=lambda x: x["harga_num"])
    elif urut in ("termahal", "mahal", "desc", "turun"):
        items_out.sort(key=lambda x: -x["harga_num"])

    if not items_out and not tanpa_harga:
        return {"found": False,
                "error": "Tak ada part yang cocok syarat / tak ada di indeks Accurate."}

    return {
        "found": True,
        "items": items_out,
        "jumlah_item": len(items_out),
        "total": _rp(total_num),
        "total_num": total_num,
        "items_tanpa_harga": tanpa_harga,
        "catatan": ("Total, subtotal, & urutan DIHITUNG SISTEM = PASTI. Sajikan apa adanya; "
                    "⛔ JANGAN menghitung ulang / mengubah angka / menebak harga. Item di "
                    "'items_tanpa_harga' memang TIDAK punya harga di Accurate & TAK ikut "
                    "total — sebutkan apa adanya."),
    }


_EXCEL_SERVER_MAX = 4000   # plafon baris export server-side (BOM terbesar ~2rb)


def _excel_stok_harga_cols(user: dict, dengan_stok: bool, dengan_harga: bool) -> tuple[bool, bool]:
    """Gerbang peran kolom Excel: pembeli tak boleh melihat rincian stok gudang
    (aturan audit hardening); harga & stok mengikuti izin kolom Menu Control."""
    if _is_pembeli(user):
        return False, False
    if dengan_harga and not _boleh_harga(user):
        dengan_harga = False
    if dengan_stok and not _boleh_stok(user):
        dengan_stok = False
    return dengan_stok, dengan_harga


def _rincian_gudang_str(pn: str) -> tuple[int, str]:
    """(stok_total, 'NN.Gudang: q · …') dari indeks Accurate — untuk kolom Excel."""
    br = accurate.gudang_breakdown(pn)
    pairs = sorted(((g, _acc_qty(v)) for g, v in br.items() if _acc_qty(v) > 0),
                   key=lambda kv: kv[1], reverse=True)
    return sum(q for _, q in pairs), " · ".join(f"{g}: {q}" for g, q in pairs)


def _t_excel_bom_rangka(args: dict, user: dict) -> dict:
    """EXPORT EXCEL BOM per-VIN yang dibangun DI SERVER — data ditarik langsung dari
    EPC + indeks Accurate, TIDAK lewat model. Ini yang membuat 'Excel BOM lengkap
    dengan stok & harga' bisa benar: buat_excel menuntut model menyalin ulang baris
    (terpangkas & rawan salin), sedangkan BOM bisa 1.500+ part."""
    rangka = (args.get("rangka") or "").strip()
    if not rangka:
        return {"error": "Sebutkan nomor rangka/VIN unit."}
    kategori = (args.get("kategori") or "").strip()
    kata = (args.get("kata_kunci") or "").strip()
    dengan_stok, dengan_harga = _excel_stok_harga_cols(
        user, bool(args.get("dengan_stok")), bool(args.get("dengan_harga")))

    res = epc_bom.loading_list(rangka)
    if not res.get("found"):
        err = res.get("_err")
        if err in ("token_expired", "no_token"):
            return {"found": False, "error": _EPC_TOKEN_MSG, "_token_issue": True}
        if err == "network":
            return {"found": False, "error": "Gagal menghubungi server EPC (jaringan). Coba lagi."}
        return {"found": False, "error": "BOM unit ini tidak ditemukan di EPC (cek nomor rangka)."}
    frame = res.get("frame_number") or rangka
    parts = [p for p in (res.get("parts") or []) if p.get("pn")]

    label_filter = ""
    if kategori and not kata:
        code = catalog_bom.resolve_kategori(kategori) if catalog_bom.available() else None
        if not code:
            return {"error": f"Kategori '{kategori}' tak dikenal — pakai mis. kabin, rem, "
                             "transmisi, kelistrikan, mesin; atau kosongkan untuk BOM lengkap."}
        _pncat = catalog_bom.pn_category_map()
        parts = [p for p in parts
                 if (_pncat.get(catalog_bom._norm(p["pn"])) or {}).get("kategori") == code]
        label_filter = catalog_bom.KATEGORI_NAMA.get(code, kategori)
    elif kata:
        terms, _m = _expand_query(kata)
        up_terms = [t.upper() for t in terms if t]
        local_pre = {(r.get("part_number") or "").upper(): r
                     for r in part_index.search_exact_pns([p["pn"] for p in parts])}

        def _hit(p: dict) -> bool:
            hay = " ".join([p["pn"], local_pre.get(p["pn"].upper(), {}).get("part_name") or "",
                            p.get("nama_cn") or ""]).upper()
            return any(t in hay for t in up_terms)

        parts = [p for p in parts if _hit(p)]
        label_filter = kata
    if not parts:
        return {"found": False, "error": f"Tidak ada part '{label_filter}' di BOM unit ini — "
                                         "coba kategori/kata lain atau BOM lengkap."}
    parts = parts[:_EXCEL_SERVER_MAX]

    local = {(r.get("part_number") or "").upper(): r
             for r in part_index.search_exact_pns([p["pn"] for p in parts])}
    snap = accurate.snapshot() if dengan_harga else {}

    kolom = ["No", "Part Number", "Nama Part", "Qty"]
    if dengan_stok:
        kolom += ["Stok Total", "Stok per Gudang"]
    if dengan_harga:
        kolom += ["Harga"]
    baris: list[list[str]] = []
    for i, p in enumerate(parts, start=1):
        pn = p["pn"]
        nama = (local.get(pn.upper(), {}).get("part_name") or p.get("nama_cn") or "").strip()
        # Sel Qty/Stok/Harga = ANGKA mentah (bukan "Rp …"/str) supaya rumus Excel
        # user jalan — aturan pemilik 2026-07-20. Kolom "No" tetap label teks.
        row = [str(i), pn, nama, ai_export.ke_angka(p.get("qty") or "")]
        if dengan_stok:
            total, rinci = _rincian_gudang_str(pn)
            row += [ai_export.ke_angka(total), rinci]
        if dengan_harga:
            e = snap.get(accurate.index_key(pn))
            hg = (e or {}).get("harga")
            row += [int(hg) if hg else "—"]
        baris.append(row)

    judul = f"BOM {frame}" + (f" — {label_filter}" if label_filter else " (lengkap)")
    export_id, filename = ai_export.stash_export(judul, kolom, baris)
    return {"found": True, "export_id": export_id, "filename": filename, "judul": judul,
            "jumlah_baris": len(baris), "frame_number": frame,
            "kolom_stok": dengan_stok, "kolom_harga": dengan_harga,
            "catatan": ("File Excel BOM siap — kartu unduh muncul OTOMATIS di bawah jawaban. "
                        "Jawab SINGKAT (judul + jumlah baris + kolom yang disertakan). "
                        "⛔ JANGAN tulis ulang isi tabel & JANGAN membuat link sendiri.")}


def _t_excel_stok_gudang(args: dict, user: dict) -> dict:
    """EXPORT EXCEL daftar stok kategori dibangun DI SERVER dari indeks Accurate —
    LENGKAP (tanpa pangkas 40 baris seperti jawaban chat stok_gudang). `gudang`
    kosong = semua gudang (kolom rincian per-gudang)."""
    if _is_pembeli(user):
        return {"error": "Rincian stok antar-gudang tidak tersedia untuk akun pembeli."}
    kata = (args.get("kata_kunci") or args.get("query") or "").strip()
    if not kata:
        return {"error": "Sebutkan part/kategori yang dicari (mis. 'kampas rem', 'filter oli')."}
    gud = (args.get("gudang") or "").strip()
    gudang_kanonik = None
    if gud:
        gudang_kanonik = _resolve_gudang(gud)
        if not gudang_kanonik:
            return {"found": False, "error": f"Gudang '{gud}' tak dikenal.",
                    "gudang_tersedia": [_norm_gudang(g) for g in _gudang_list()],
                    "jawaban_wajib": "Sebutkan salah satu gudang dari 'gudang_tersedia'."}
    _stok_dummy, dengan_harga = _excel_stok_harga_cols(user, True, bool(args.get("dengan_harga")))

    terms, _m = _expand_query(kata)
    for kw in _umbrella_keywords(kata):
        if kw not in terms:
            terms.append(kw)
    search_terms = list(dict.fromkeys(t for t in terms if t))

    want_g = _norm_gudang(gudang_kanonik) if gudang_kanonik else None
    hasil: list[dict] = []
    for it in accurate.items_matching(search_terms, limit=_EXCEL_SERVER_MAX):
        pn = (it.get("pn") or "").upper()
        if not pn:
            continue
        total, rinci = _rincian_gudang_str(pn)
        if want_g:
            br = accurate.gudang_breakdown(pn)
            qty = next((_acc_qty(v) for g, v in br.items() if _norm_gudang(g) == want_g), 0)
            if qty <= 0:
                continue
        else:
            qty = total
            if total <= 0:
                continue
        price = it.get("price")
        hasil.append({"pn": pn, "nama": it.get("name") or part_index.name_for(pn),
                      "qty": qty, "total": total, "rinci": rinci,
                      # ANGKA mentah — `hasil` HANYA dipakai membangun sel Excel
                      # di bawah, tak pernah dikirim ke model.
                      "harga": int(price) if price else "—"})
    if not hasil:
        if accurate.gudang_enriched_count() == 0:
            return {"found": False, "error": "Indeks stok per-gudang sedang disiapkan — coba lagi beberapa menit."}
        tempat = f"di gudang {gudang_kanonik}" if gudang_kanonik else "di gudang mana pun"
        return {"found": False, "error": f"Tidak ada part '{kata}' yang berstok {tempat}."}
    hasil.sort(key=lambda x: x["qty"], reverse=True)

    label_g = gudang.gudang_label(gudang_kanonik) if gudang_kanonik else ""
    if gudang_kanonik:
        kolom = ["No", "Part Number", "Nama Part", f"Stok {label_g}", "Stok Total"]
    else:
        kolom = ["No", "Part Number", "Nama Part", "Stok Total", "Stok per Gudang"]
    if dengan_harga:
        kolom += ["Harga"]
    baris = []
    for i, h in enumerate(hasil, start=1):
        row = [str(i), h["pn"], h["nama"]]
        row += ([h["qty"], h["total"]] if gudang_kanonik
                else [h["total"], h["rinci"]])
        if dengan_harga:
            row += [h["harga"]]
        baris.append(row)

    judul = f"Stok {kata}" + (f" — Gudang {label_g}" if gudang_kanonik else " — Semua Gudang")
    export_id, filename = ai_export.stash_export(judul, kolom, baris)
    return {"found": True, "export_id": export_id, "filename": filename, "judul": judul,
            "jumlah_baris": len(baris), "kolom_harga": dengan_harga,
            "catatan": ("File Excel stok siap — kartu unduh muncul OTOMATIS di bawah jawaban. "
                        "Jawab SINGKAT (judul + jumlah part). ⛔ JANGAN tulis ulang isi tabel "
                        "& JANGAN membuat link sendiri.")}


def _katalog_mesin_impl(args: dict, user: dict) -> dict:
    """KATALOG BERGAMBAR MESIN Weichai per-VIN — tiap GROUP mesin = satu figure
    (gambar exploded view resmi EPC Weichai + part ber-nomor balon). Reuse penuh
    pipeline katalog (epc_weichai.catalog_walk → ai_export builder source=weichai).
    Hanya unit bermesin Weichai (WP-series)."""
    rangka = (args.get("rangka") or "").strip()
    kategori = (args.get("kategori") or "").strip()
    fmt_raw = (args.get("format") or "").strip().lower()
    fmt = ("pdf" if fmt_raw in ("pdf",)
           else "excel" if fmt_raw in ("excel", "xlsx", "xls", "spreadsheet") else "")
    if not rangka:
        return {"error": "Sebutkan nomor rangka/VIN unit yang mesinnya mau dikatalogkan."}
    if not kategori:
        return {
            "found": False,
            "pilihan_kategori": ["Mesin LENGKAP (semua kelompok)", "Blok & piston",
                                 "Kepala silinder & klep", "Kruk as & bearing",
                                 "Sistem bahan bakar (injektor/pompa)", "Sistem pelumas (oli)",
                                 "Sistem pendingin (air/radiator)", "Turbocharger",
                                 "Kompresor angin", "Alternator & starter"],
            "jawaban_wajib": ("User belum menyebut bagian mesin. TANYAKAN dulu — tampilkan "
                              "'pilihan_kategori' sebagai pilihan dan minta user memilih SATU, "
                              "atau 'lengkap' untuk seluruh kelompok mesin (file lebih besar, "
                              "±2-3 menit). ⛔ JANGAN memanggil tool ini lagi sebelum user memilih, "
                              "JANGAN menebak."),
        }
    if not fmt:
        return {
            "found": False,
            "pilihan_format": ["Excel (.xlsx)", "PDF (siap cetak)"],
            "jawaban_wajib": ("Bagian mesin sudah jelas, tapi user BELUM memilih FORMAT. "
                              "TANYAKAN: mau EXCEL (.xlsx) atau PDF (siap cetak)? ⛔ JANGAN "
                              "memanggil tool ini lagi sebelum user memilih; setelah dipilih, "
                              "panggil lagi dengan 'format'='excel' atau 'pdf'."),
        }

    d = epc_weichai.catalog_walk(rangka, kategori)
    if not d.get("found"):
        err = d.get("_err")
        reason = d.get("reason")
        if err == "no_link" or reason == "no_link":
            return {"found": False, "error": ("Unit ini tidak punya link EPC Weichai (mesin "
                    "non-Weichai atau rangka salah). Katalog mesin hanya untuk unit bermesin Weichai.")}
        if err in ("no_engine", "no_order", "empty"):
            return {"found": False, "error": "EPC Weichai tak mengembalikan data mesin untuk unit ini."}
        if err == "network":
            return {"found": False, "error": "Gagal menghubungi server EPC Weichai (jaringan). Coba lagi."}
        if err == "no_category":
            tersedia = d.get("tersedia") or []
            return {"found": False,
                    "error": (d.get("message") or "Bagian mesin tak dikenal.")
                    + " Sebutkan bagian lain (blok/kepala silinder/bahan bakar/pelumas/"
                      "pendingin/turbo/kompresor/alternator) atau 'lengkap'.",
                    "kelompok_tersedia": tersedia[:40]}
        return {"found": False, "error": (d.get("message")
                or "EPC Weichai tidak mengembalikan katalog untuk unit ini.")}

    frame = d.get("frame_number") or rangka
    kat_nama = "Mesin Lengkap" if d.get("lengkap") else kategori.title()
    judul = f"Katalog {kat_nama} {frame}"
    ext = "pdf" if fmt == "pdf" else "xlsx"
    isi_sh = _boleh_isi_stok_harga(args, user)
    export_id, filename = ai_export.stash_builder(
        judul, {"kind": "katalog_mesin", "rangka": rangka, "kategori": kategori, "fmt": fmt,
                "isi_stok_harga": isi_sh}, ext=ext)
    durasi = "±2-3 menit" if d.get("lengkap") else "±1 menit"
    fmt_label = "PDF" if fmt == "pdf" else "Excel"
    return {
        "found": True, "export_id": export_id, "filename": filename, "judul": judul,
        "format": fmt_label, "frame_number": frame, "engine_model": d.get("engine_model"),
        "katalog_lengkap": bool(d.get("lengkap")),
        "jumlah_figure": d.get("jumlah_figure"), "jumlah_baris": d.get("jumlah_part"),
        "kategori_cocok": (d.get("kategori_cocok") or [])[:20],
        **({"peringatan_tidak_lengkap":
            "⚠️ Sebagian data EPC Weichai gagal diambil — katalog bisa belum lengkap; sarankan coba lagi."}
           if d.get("incomplete") else {}),
        "stok_harga_diisi": isi_sh,
        "info_stok_harga": (
            "Kolom Stok & Harga DIISI (admin meminta)." if isi_sh
            else ("Kolom Stok & Harga sengaja DIKOSONGKAN di katalog. Sebagai admin, "
                  "kamu bisa minta 'sertakan stok & harga' untuk mengisinya."
                  if _is_admin(user)
                  else "Kolom Stok & Harga sengaja DIKOSONGKAN di katalog (kebijakan).")),
        "catatan": (f"Katalog mesin {fmt_label} siap — KARTU UNDUH otomatis muncul di bawah jawabanmu. "
                    "Jawab SINGKAT: sebut jumlah figure + jumlah part + bahwa tiap figure ada GAMBAR "
                    "exploded view resmi EPC Weichai dengan nomor balon, dan UNDUHAN PERTAMA butuh "
                    f"{durasi} (menyusun gambar). Sampaikan juga sesuai 'info_stok_harga'. "
                    "⛔ JANGAN menulis daftar part/figure satu-satu, JANGAN membuat link/URL sendiri."),
    }


def _katalog_kategori_impl(args: dict, user: dict) -> dict:
    """KATALOG BERGAMBAR per kategori per-VIN — walk Atlas (epc_bom.catalog_walk,
    di-cache), lalu stash RESEP export; Excel bergambar dibangun saat kartu
    diunduh (ai_export.katalog_excel) agar chat tak menunggu render gambar."""
    rangka = (args.get("rangka") or "").strip()
    kategori = (args.get("kategori") or "").strip()
    fmt_raw = (args.get("format") or "").strip().lower()
    fmt = ("pdf" if fmt_raw in ("pdf",)
           else "excel" if fmt_raw in ("excel", "xlsx", "xls", "spreadsheet") else "")
    if not rangka:
        return {"error": "Sebutkan nomor rangka/VIN unit yang mau dikatalogkan."}
    if not kategori:
        # User belum memilih kategori → JANGAN menebak; suruh model menawarkan pilihan.
        return {
            "found": False,
            "pilihan_kategori": ["Kabin", "Mesin", "Kopling", "Transmisi", "Gardan depan",
                                 "Gardan belakang", "Kelistrikan", "Rem", "Sasis", "AC",
                                 "LENGKAP (semua kategori)"],
            "jawaban_wajib": ("User belum menyebut kategori. TANYAKAN dulu — tampilkan "
                              "daftar 'pilihan_kategori' di atas sebagai pilihan (boleh "
                              "format daftar/bullet) dan minta user memilih SATU, atau "
                              "'lengkap' untuk semua kategori sekaligus (file lebih besar, "
                              "±2-3 menit). ⛔ JANGAN memanggil tool ini lagi sebelum user "
                              "memilih, JANGAN menebak kategorinya."),
        }
    if not fmt:
        # Kategori sudah dipilih tapi FORMAT belum → tanyakan Excel atau PDF dulu.
        return {
            "found": False,
            "pilihan_format": ["Excel (.xlsx)", "PDF (siap cetak)"],
            "jawaban_wajib": ("Kategori sudah jelas, tapi user BELUM memilih FORMAT file. "
                              "TANYAKAN: mau hasilnya format EXCEL (.xlsx) atau PDF (siap "
                              "cetak/kirim)? ⛔ JANGAN memanggil tool ini lagi sebelum user "
                              "memilih format; setelah user memilih, panggil lagi dengan "
                              "argumen 'format' = 'excel' atau 'pdf'."),
        }

    d = epc_bom.catalog_walk(rangka, kategori)
    if not d.get("found"):
        err = d.get("_err")
        if err in ("token_expired", "no_token"):
            return {"found": False, "error": _EPC_TOKEN_MSG, "_token_issue": True}
        if err == "network":
            return {"found": False, "error": "Gagal menghubungi server EPC (jaringan). Coba lagi."}
        if err == "not_found":
            return {"found": False, "error": "Nomor rangka tidak ditemukan di EPC Parts Atlas "
                    "(cek ejaan VIN; hanya unit Sinotruk/HOWO/SITRAK)."}
        if err == "no_category":
            return {"found": False, "error": (d.get("message") or "Kategori tidak dikenal.") +
                    " Coba nama kategori lain (kabin/mesin/kopling/transmisi/gardan/kelistrikan/rem/sasis)."}
        return {"found": False, "error": "EPC Parts Atlas tidak mengembalikan data untuk unit ini."}

    frame = d.get("frame_number") or rangka
    if d.get("lengkap"):
        kat_nama = "Lengkap (Semua Kategori)"
    else:
        kat_nama = catalog_bom.KATEGORI_NAMA.get(d.get("kategori_kode") or "", kategori.title())
    judul = f"Katalog {kat_nama.split(' (')[0]} {frame}"
    ext = "pdf" if fmt == "pdf" else "xlsx"
    isi_sh = _boleh_isi_stok_harga(args, user)
    export_id, filename = ai_export.stash_builder(
        judul, {"kind": "katalog", "rangka": rangka, "kategori": kategori, "fmt": fmt,
                "isi_stok_harga": isi_sh}, ext=ext)
    durasi = "±2-3 menit" if d.get("lengkap") else "±1 menit"
    fmt_label = "PDF" if fmt == "pdf" else "Excel"
    return {
        "found": True, "export_id": export_id, "filename": filename, "judul": judul,
        "format": fmt_label,
        "frame_number": frame, "katalog_lengkap": bool(d.get("lengkap")),
        "jumlah_figure": d.get("jumlah_figure"), "jumlah_baris": d.get("jumlah_part"),
        "kategori_cocok": (d.get("kategori_cocok") or [])[:20],
        **({"peringatan_tidak_lengkap":
            "⚠️ Sebagian data EPC gagal diambil — katalog bisa belum lengkap; sarankan coba lagi."}
           if d.get("incomplete") else {}),
        "stok_harga_diisi": isi_sh,
        "info_stok_harga": (
            "Kolom Stok & Harga DIISI (admin meminta)." if isi_sh
            else ("Kolom Stok & Harga sengaja DIKOSONGKAN di katalog. Sebagai admin, "
                  "kamu bisa minta 'sertakan stok & harga' untuk mengisinya."
                  if _is_admin(user)
                  else "Kolom Stok & Harga sengaja DIKOSONGKAN di katalog (kebijakan).")),
        "catatan": (f"Katalog {fmt_label} siap — KARTU UNDUH otomatis muncul di bawah jawabanmu. "
                    "Jawab SINGKAT: sebut jumlah figure + jumlah part + bahwa tiap figure ada "
                    "GAMBAR exploded view resmi EPC dengan nomor balon, dan UNDUHAN PERTAMA "
                    f"butuh {durasi} (menyusun gambar). Sampaikan juga sesuai 'info_stok_harga'. "
                    "⛔ JANGAN menulis daftar part/figure satu-satu, JANGAN membuat link/URL sendiri."),
    }


def _gambar_exploded_atlas_impl(args: dict, user: dict) -> dict:
    """GAMBAR EXPLODED VIEW EPC untuk SATU PN (per-VIN): temukan figure yang memuat
    PN + NOMOR BALON-nya, siapkan PNG yang tampil INLINE di chat. Reuse Parts Atlas
    (epc_bom.exploded_figures) + render resvg (ai_export.exploded_png)."""
    rangka = (args.get("rangka") or "").strip()
    pn = (args.get("pn") or args.get("part_number") or "").strip().upper()
    kategori = (args.get("kategori") or "").strip()
    if not rangka:
        return {"error": "Sebutkan nomor rangka/VIN unit — gambar exploded view diambil "
                         "PER-VIN dari EPC (hanya Sinotruk/HOWO/SITRAK)."}
    if not pn:
        return {"error": "Sebutkan Part Number yang mau ditampilkan gambar exploded view-nya."}
    if not kategori:
        return {"found": False,
                "pilihan_kategori": ["kabin", "mesin", "kopling", "transmisi", "gardan depan",
                                     "gardan belakang", "kelistrikan", "rem", "sasis"],
                "jawaban_wajib": ("Kategori belum jelas — perlu untuk menemukan figure yang "
                                  "memuat PN ini. TENTUKAN dari jenis part-nya (bearing/hub/baut "
                                  "roda → 'gardan depan/belakang'; kampas/sepatu rem → 'rem'; "
                                  "piston/liner/klep → 'mesin'; sinkromes → 'transmisi'; part "
                                  "kabin → 'kabin'). Panggil lagi dengan 'kategori' terisi. ⛔ "
                                  "jangan menebak sembarang kategori.")}
    try:
        balon_req = int(args.get("balon")) if str(args.get("balon") or "").strip() else None
    except (TypeError, ValueError):
        balon_req = None
    d = epc_bom.exploded_figures(rangka, pn, kategori)
    if not d.get("found"):
        err = d.get("_err")
        if err in ("token_expired", "no_token"):
            return {"found": False, "error": _EPC_TOKEN_MSG, "_token_issue": True}
        if err == "network":
            return {"found": False, "error": "Gagal menghubungi server EPC (jaringan). Coba lagi."}
        if err == "not_found":
            return {"found": False, "error": "Nomor rangka tak ditemukan di EPC Parts Atlas "
                    "(cek VIN; hanya Sinotruk/HOWO/SITRAK)."}
        if err == "no_category":
            return {"found": False, "error": (d.get("message") or "Kategori tak dikenal."),
                    "saran": "Coba kategori lain: kabin/mesin/rem/gardan/kelistrikan/sasis/transmisi/kopling."}
        # not_in_category — PN tak ada di kategori itu utk unit ini
        return {"found": False,
                "error": d.get("message") or f"PN {pn} tak muncul di figure kategori "
                f"'{kategori}' untuk unit ini.",
                "saran": ("Pastikan PN & kategori cocok, atau coba kategori lain. Bila PN memang "
                          "terpasang tapi tak ber-gambar, itu part work-BOM (baut/mur) yang tak "
                          "digambar di Parts Atlas.")}
    # Bila user minta balon tertentu: cari part di balon itu (dari figure yg memuat PN).
    part_di_balon = None
    if balon_req is not None:
        for f in d["figures"]:
            hit = next((it for it in (f.get("items_ringkas") or [])
                        if it.get("balon") == balon_req), None)
            if hit:
                part_di_balon = {"balon": balon_req, "part_number": hit.get("pn") or None,
                                 "nama": hit.get("nama") or None, "figure": f.get("nama")}
                break

    # Gambar exploded view DIMINTA EKSPLISIT lewat tool ini → stash PNG utk tampil
    # INLINE di chat. (2026-07-09: pemilik minta gambar muncul saat DIMINTA.) Auto-
    # attach (uraikan_mesin/part_aus) TETAP mati — lihat _auto_exploded_gambar.
    gambar: list[dict] = []
    daftar_balon: list[dict] = []
    for f in d["figures"][:_MAX_EXPLODED_FIGURES]:   # batas figure agar tak membanjiri chat
        hl = balon_req if balon_req is not None else f.get("balon")   # balon yg disorot
        judul = f"Exploded {pn} - {f.get('nama') or kategori}"
        image_id, filename = ai_export.stash_builder(
            judul, {"kind": "exploded", "svg": f["svg"], "balon": hl}, ext="png")
        gambar.append({"image_id": image_id, "filename": filename,
                       "balon": hl, "nama_figure": f.get("nama"),
                       "kategori": f.get("kategori"), "jumlah_item": f.get("jumlah_item")})
    # Daftar balon→part figure pertama = konteks utk follow-up 'cek no N'.
    if d["figures"]:
        daftar_balon = [{"balon": it.get("balon"), "pn": it.get("pn"), "nama": it.get("nama")}
                        for it in (d["figures"][0].get("items_ringkas") or [])][:40]
    b0 = gambar[0]
    if balon_req is not None:
        catatan = (f"Gambar exploded view SIAP (tampil INLINE di bawah jawabanmu). NOMOR BALON "
                   f"{balon_req} DISOROT (kuning) di figure '{b0['nama_figure']}'. "
                   + (f"Balon {balon_req} = {part_di_balon.get('nama') or '—'}"
                      + (f" (PN {part_di_balon['part_number']})" if part_di_balon and part_di_balon.get('part_number')
                         else " — PN tak tercantum terpisah (kemungkinan termasuk dalam assembly).")
                      if part_di_balon else f"Balon {balon_req} tak ada di daftar item figure ini.")
                   + " Jawab SINGKAT (figure + isi balon); gambar sudah tampil sendiri — "
                     "⛔ JANGAN mengarang PN; JANGAN buat link/gambar/URL sendiri.")
    else:
        catatan = (f"Gambar exploded view SIAP — tampil OTOMATIS (inline) di bawah jawabanmu. "
                   f"PN {pn} = NOMOR BALON '{b0['balon']}' di figure '{b0['nama_figure']}'. "
                   "'daftar_balon_gambar' berisi SEMUA balon di gambar + part-nya — bila user lanjut "
                   "tanya 'no N itu apa'/'cek baut no N', jawab dari daftar itu DAN panggil lagi "
                   "gambar_exploded dengan 'balon'=N agar balon itu disorot. ⛔ JANGAN buat link/"
                   "gambar/URL sendiri; JANGAN sebut PN lain di luar data ini.")
    return {
        "found": True, "frame_number": d.get("frame_number"), "pn": pn, "kategori": kategori,
        "balon_disorot": balon_req, "part_di_balon": part_di_balon,
        "daftar_balon_gambar": daftar_balon,
        "jumlah_figure_cocok": len(d["figures"]), "gambar": gambar,
        "catatan": catatan,
    }


def _gambar_exploded_mesin_impl(args: dict, user: dict) -> dict:
    """GAMBAR EXPLODED VIEW MESIN Weichai untuk SATU PN (inline di chat) — padanan
    sisi Atlas untuk part internal mesin. Reuse epc_weichai.exploded_figures
    (figure=group ber-svgFileId, balon=orderNo) + render via token Weichai."""
    rangka = (args.get("rangka") or "").strip()
    pn = (args.get("pn") or args.get("part_number") or "").strip().upper()
    kategori = (args.get("kategori") or "").strip() or "lengkap"
    # Nomor balon yang MINTA disorot (mis. 'cek baut no 3 di turbo'): figure tetap
    # ditemukan via PN assembly-nya, tapi yang di-highlight = balon ini, bukan balon PN.
    try:
        balon_req = int(args.get("balon")) if str(args.get("balon") or "").strip() else None
    except (TypeError, ValueError):
        balon_req = None
    if not rangka:
        return {"error": "Sebutkan nomor rangka/VIN — gambar exploded mesin diambil PER-VIN "
                         "dari EPC Weichai."}
    if not pn:
        return {"error": "Sebutkan Part Number mesin yang mau ditampilkan gambar exploded view-nya."}

    d = epc_weichai.exploded_figures(rangka, pn, kategori)
    if not d.get("found"):
        err = d.get("_err")
        reason = d.get("reason")
        if err == "no_link" or reason == "no_link":
            return {"found": False, "error": ("Unit ini tidak punya link EPC Weichai (mesin non-Weichai "
                    "atau rangka salah). Gambar exploded mesin hanya untuk unit bermesin Weichai — "
                    "untuk part bodi/sasis/gardan Sinotruk pakai gambar_exploded.")}
        if err == "network":
            return {"found": False, "error": "Gagal menghubungi server EPC Weichai (jaringan). Coba lagi."}
        if err in ("no_engine", "no_order", "empty"):
            return {"found": False, "error": "EPC Weichai tak mengembalikan data mesin untuk unit ini."}
        if err == "no_category":
            return {"found": False, "error": (d.get("message") or "Kelompok mesin tak dikenal."),
                    "saran": "Kosongkan 'kategori' agar dicari di seluruh mesin, atau sebut kelompok lain."}
        # not_found — PN tak ada di figure mesin unit ini
        return {"found": False,
                "error": d.get("message") or f"PN {pn} tak muncul di figure mesin unit ini.",
                "saran": ("Pastikan PN memang part mesin Weichai & rangka benar. Bila part terpasang "
                          "tapi tak ber-gambar, itu tak digambar di figure EPC.")}
    # Bila user minta balon tertentu, cari part di balon itu (dari figure yg memuat PN).
    part_di_balon = None
    if balon_req is not None:
        for f in d["figures"]:
            hit = next((it for it in (f.get("items_ringkas") or [])
                        if it.get("balon") == balon_req), None)
            if hit:
                part_di_balon = {"balon": balon_req, "part_number": hit.get("pn") or None,
                                 "nama": hit.get("nama") or None, "figure": f.get("nama")}
                break

    # Gambar exploded MESIN DIMINTA EKSPLISIT → stash PNG utk tampil INLINE.
    # Auto-attach tetap mati (_auto_exploded_gambar). (2026-07-09)
    gambar: list[dict] = []
    for f in d["figures"][:_MAX_EXPLODED_FIGURES]:
        hl = balon_req if balon_req is not None else f.get("balon")   # balon yg disorot
        judul = f"Exploded mesin {pn} - {f.get('nama') or 'mesin'}"
        image_id, filename = ai_export.stash_builder(
            judul, {"kind": "exploded", "source": "weichai", "svg": f["svg"],
                    "balon": hl, "rangka": rangka}, ext="png")
        gambar.append({"image_id": image_id, "filename": filename,
                       "balon": hl, "nama_figure": f.get("nama"),
                       "kategori": f.get("kategori"), "jumlah_item": f.get("jumlah_item")})
    fig0 = d["figures"][0] if d.get("figures") else {}
    nama_fig = fig0.get("nama") or "mesin"
    daftar_balon = [{"balon": it.get("balon"), "pn": it.get("pn"), "nama": it.get("nama")}
                    for it in (fig0.get("items_ringkas") or [])][:40]
    b0 = gambar[0]
    if balon_req is not None:
        catatan = (f"Gambar exploded view MESIN SIAP — tampil INLINE. NOMOR BALON "
                   f"{balon_req} DISOROT (kuning) di figure '{nama_fig}'. "
                   + (f"Balon {balon_req} = {part_di_balon.get('nama') or '—'}"
                      + (f" (PN {part_di_balon['part_number']})" if part_di_balon and part_di_balon.get('part_number')
                         else " — PN tak tercantum terpisah (kemungkinan termasuk dalam assembly).")
                      if part_di_balon else f"Balon {balon_req} tak ada di daftar item figure ini.")
                   + " Jawab SINGKAT; gambar sudah tampil sendiri — ⛔ JANGAN mengarang PN; "
                     "JANGAN buat link/gambar sendiri.")
    else:
        catatan = (f"Gambar exploded view MESIN SIAP — tampil OTOMATIS (inline). "
                   f"PN {pn} = NOMOR BALON '{b0['balon']}' di figure '{nama_fig}'. "
                   "Jawab SINGKAT: figure apa + PN ini balon nomor berapa. ⛔ JANGAN "
                   "membuat link/gambar/URL sendiri; JANGAN sebut PN lain di luar data ini.")
    return {
        "found": True, "frame_number": d.get("frame_number"), "pn": pn,
        "balon_disorot": balon_req, "part_di_balon": part_di_balon,
        "daftar_balon_gambar": daftar_balon,
        "jumlah_figure_cocok": len(d["figures"]), "gambar": gambar,
        "catatan": catatan,
    }


# ══ ROUTER TOOL GABUNGAN Sinotruk↔Weichai (Fase 4 rombakan 2026-07-17) ══
# 4 pasang tool kembar dilebur: nama sisi Sinotruk dipertahankan + param
# `sumber` ('atlas'|'mesin'|kosong=auto). Ke-8 implementasi lama TETAP ada
# (_*_impl); `_t_*_mesin` jadi shim (test lama & leaked tool-call tetap jalan).
# Auto-fallback silang gambar/uraikan menyerang mode gagal produksi 33%
# (model salah pilih sisi Atlas vs Weichai).
