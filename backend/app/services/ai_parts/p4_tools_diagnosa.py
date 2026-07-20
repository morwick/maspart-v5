# -*- coding: utf-8 -*-
# ai_parts/p4_tools_diagnosa.py — BAGIAN dari modul app.services.ai_assistant (SATU namespace).
# Dimuat berurutan oleh ai_assistant.py (loader) via exec — sengaja BUKAN
# submodul terpisah: monkeypatch test `ai.<attr>`, relative import, dan
# resolusi global lintas-bagian tetap bekerja persis seperti file tunggal.
# Urutan muat & pembagian: lihat _PARTS di ai_assistant.py.
from __future__ import annotations

def _t_cari_kode_kesalahan(args: dict, user: dict) -> dict:
    spn = args.get("spn")
    fmi = args.get("fmi")
    code = (args.get("code") or "").strip() or None
    query = (args.get("query") or "").strip() or None
    unit = (args.get("unit") or "").strip() or None
    try:
        spn = int(spn) if spn not in (None, "") else None
        fmi = int(fmi) if fmi not in (None, "") else None
    except (TypeError, ValueError):
        spn = fmi = None

    if spn is None and fmi is None and not code and not query and not unit:
        return {"error": "Sebutkan SPN+FMI, kode DTC, kata kunci komponen, atau unit kontrol."}

    # ── Pengaman input & tangga fallback SPN/FMI (anti "jawaban pertama meleset").
    flags: dict = {}
    if fmi is not None and fmi > 31:  # FMI valid J1939 hanya 0–31 → pasti tertukar/salah
        if spn is None or spn <= 31:
            spn, fmi = fmi, spn
            flags["spn_fmi_ditukar"] = True
        else:
            flags["fmi_di_luar_rentang"] = fmi
            fmi = None

    hits = fault_codes.search(spn=spn, fmi=fmi, code=code, query=query, limit=20)
    if spn is not None and fmi is not None and not hits:
        # Pasangan persis tak terdaftar → JANGAN kosong diam-diam: tampilkan
        # semua FMI yang ADA untuk SPN itu + tandai jujur.
        alt = fault_codes.search(spn=spn, limit=20)
        if alt:
            hits = alt
            flags["fmi_diminta_tak_terdaftar"] = fmi
            flags["fmi_tersedia"] = sorted(
                {r["fmi"] for r in alt if r.get("fmi") is not None})
    if spn is not None and not hits and not code and not query:
        flags["spn_tak_terdaftar"] = True

    hasil = []
    for r in hits:
        row = {
            "kode": r["code"],
            "spn": r["spn"],
            "fmi": r["fmi"],
            "label": r["english"],
            "deskripsi": r.get("deskripsi") or "",  # Indonesia (kamus statik)
            "deskripsi_cn": r["desc_cn"],  # teks asli China — fallback
            "lampu_mil": r["mil"],
            "lampu_svs": r["svs"],
        }
        # Jembatan → langkah perbaikan EOL (Indonesia) via kode P/U yang sama.
        try:
            rep = eol_dtc.repair_for(r["code"])
        except Exception:  # pragma: no cover
            rep = None
        if rep:
            row["perbaikan_eol"] = {
                "kode": rep["kode"], "deskripsi": rep["deskripsi"],
                "penyebab": rep["penyebab"], "perbaikan": rep["perbaikan"],
                "part_terkait": rep["part"],
            }
        hasil.append(row)

    # Database EOL CNHTC (semua unit kontrol, Bahasa Indonesia + cara perbaikan).
    try:
        eol_hits = eol_dtc.search(code=code or "", query=query or "",
                                  unit=unit or "", limit=15)
    except Exception:  # pragma: no cover
        eol_hits = []
    hasil_eol = [
        {
            "unit_kontrol": r["unit"],
            "kode": r["kode"],
            "deskripsi": r["deskripsi"],
            "penyebab": r["penyebab"],
            "perbaikan": r["perbaikan"],
            "part_terkait": r["part"],
        }
        for r in eol_hits
    ]

    # Database ABS (WABCO, ber-SPN/FMI + langkah perbaikan) & SCR gas 国V (kode P).
    # Bahasa Indonesia; menutup celah yg tak ada di tabel Bosch/EOL.
    try:
        abs_scr_hits = abs_scr_codes.search(spn=spn, fmi=fmi, code=code or "",
                                            query=query or "", unit=unit or "", limit=15)
    except Exception:  # pragma: no cover
        abs_scr_hits = []
    hasil_abs_scr = [
        {
            "sistem": r["sistem"],
            "kode": r["kode"],
            "spn": r["spn"],
            "fmi": r["fmi"],
            "blink": r["blink"],
            "komponen": r["komponen"],
            "deskripsi": r["deskripsi"],
            "penyebab": r["penyebab"],
            "reaksi": r["reaksi"],
            "perbaikan": r["perbaikan"],
            "lampu": r["lampu"],
        }
        for r in abs_scr_hits
    ]

    # Lembar diagnosa PDF resmi (data/Fault) — kartu yang bisa DIBUKA user.
    pdf_cards = _fault_pdf_cards(spn, fmi)

    # Isi TERSTRUKTUR lembar diagnosa (dtc_diagnosa, rombakan 2026-07-17) —
    # model kini ikut MEMBACA judul/penyebab/langkah kartu, bukan cuma melampirkannya.
    diagnosa_rinci: list[dict] = []
    try:
        if spn is not None:
            diagnosa_rinci = dtc_diagnosa.for_pair(spn, fmi)
        elif code:
            diagnosa_rinci = dtc_diagnosa.for_code(code)
        diagnosa_rinci = diagnosa_rinci[:2]  # kartu varian bisa >1; cap hemat token
    except Exception:  # pragma: no cover
        diagnosa_rinci = []

    catatan = (
        "'hasil' = tabel mesin Bosch. Pakai kolom 'deskripsi' (SUDAH Bahasa "
        "Indonesia); 'deskripsi_cn' hanya fallback bila 'deskripsi' kosong — "
        "baru terjemahkan sendiri. MIL=lampu check engine, SVS=lampu servis. "
        "'hasil_eol' & 'perbaikan_eol' = database EOL CNHTC resmi SEMUA unit "
        "kontrol (mesin, ABS/ESP, transmisi, BMS/VCU EV, BCM, airbag, radar, "
        "SCR) — SUDAH Bahasa Indonesia, berisi penyebab + LANGKAH PERBAIKAN + "
        "part terkait. Sajikan langkah perbaikan apa adanya, JANGAN mengarang "
        "langkah tambahan. Untuk perbaikan kritis ingatkan cek manual resmi. "
        "Kode EMS EOL berformat kode+suffix (P0100F7 = keluarga P0100)."
    )
    if flags.get("spn_fmi_ditukar"):
        catatan += (
            " ⚠️ Angka SPN/FMI dari pertanyaan tampak TERTUKAR (FMI valid hanya "
            "0–31) — sudah dikoreksi otomatis; beri tahu user koreksi ini."
        )
    if flags.get("fmi_di_luar_rentang") is not None:
        catatan += (
            f" ⚠️ FMI {flags['fmi_di_luar_rentang']} di LUAR rentang valid 0–31 — "
            "diabaikan; hasil = semua FMI utk SPN itu. Minta user cek ulang angkanya."
        )
    if flags.get("fmi_diminta_tak_terdaftar") is not None:
        catatan += (
            f" ⚠️ PENTING: pasangan SPN {spn} + FMI "
            f"{flags['fmi_diminta_tak_terdaftar']} TIDAK ADA di database — katakan "
            "itu TEGAS ke user. 'hasil' berisi FMI LAIN yang terdaftar untuk SPN "
            f"yang sama (fmi_tersedia={flags.get('fmi_tersedia')}) — sajikan sebagai "
            "KEMUNGKINAN yang dimaksud + minta user cek ulang angka FMI di scan-tool. "
            "⛔ JANGAN menyajikan arti FMI lain seolah itu jawaban pasti."
        )
    if flags.get("spn_tak_terdaftar"):
        catatan += (
            f" ⚠️ SPN {spn} TIDAK terdaftar di database mesin — sampaikan jujur. "
            "Sarankan cek ulang angka; bila kode dari ECU NON-mesin (ABS/transmisi/"
            "EV/radar), minta kode DTC-nya (P/B/U) atau gejalanya lalu cari via "
            "'code'/'query' (database EOL tidak ber-SPN)."
        )
    if (spn is not None and fmi is not None and len(hits) > 1
            and "fmi_diminta_tak_terdaftar" not in flags):
        catatan += (
            f" ⚠️ Ada {len(hits)} KODE BERBEDA untuk pasangan SPN/FMI ini — "
            "tampilkan SEMUANYA, jangan pilih salah satu."
        )
    total_kosong = (not hits and not hasil_eol and not hasil_abs_scr
                    and not pdf_cards and not diagnosa_rinci)
    if hasil_abs_scr:
        ada_abs = any(h["sistem"] == "ABS" for h in hasil_abs_scr)
        ada_scr = any(h["sistem"] == "SCR" for h in hasil_abs_scr)
        sub = " & ".join(
            s for s, ada in (("rem ABS (WABCO)", ada_abs), ("SCR gas 国V", ada_scr)) if ada)
        catatan += (
            f" 'hasil_abs_scr' = database {sub} (SUDAH Bahasa Indonesia). "
            "ABS berisi langkah perbaikan ('perbaikan'), reaksi sistem, lampu, "
            "dan Blink Code; SCR berisi penjelasan + strategi monitoring. Sajikan "
            "langkah perbaikan APA ADANYA, jangan mengarang. Untuk perbaikan kritis "
            "ingatkan cek manual resmi."
        )
    if total_kosong:
        catatan += (
            " ⚠️ TIDAK ADA yang cocok di SEMUA database (Bosch, EOL, ABS/SCR, lembar PDF) — "
            "sampaikan jujur ke user; minta cek ulang angka/kode di scan-tool, atau "
            "tanyakan GEJALA-nya lalu pakai tool diagnosa."
        )
    if (any((h.get("part_terkait") or "").strip() for h in hasil_eol)
            or any((h.get("perbaikan_eol", {}) or {}).get("part_terkait") for h in hasil)
            or any((h.get("komponen") or "").strip() for h in hasil_abs_scr)):
        catatan += (
            " Ada komponen tersangka ('part_terkait'/'komponen') — TAWARKAN ke user: "
            "cek ketersediaan & harga part itu via cari_part (sebut nama komponennya)."
        )
    if pdf_cards:
        pasang = ", ".join(f"SPN {c['spn']} FMI {c['fmi']}" for c in pdf_cards)
        catatan += (
            f" 📎 LEMBAR DIAGNOSA PDF RESMI terlampir sebagai KARTU di bawah "
            f"jawabanmu ({pasang}) — beri tahu user bisa MEMBUKANYA langsung dari "
            "kartu itu (isi: P-code, part terkait, langkah diagnosa). ⛔ JANGAN "
            "membuat link/URL sendiri."
        )
        if flags.get("spn_tak_terdaftar") or not hits:
            catatan += (
                " Database teks tidak memuat pasangan ini, TAPI lembar diagnosa "
                "resminya ADA (terlampir) — dasari jawaban dari keberadaan lembar itu."
            )
    if diagnosa_rinci:
        catatan += (
            " 'diagnosa_rinci' = ISI lembar diagnosa resmi (judul, part terkait, "
            "kondisi trigger, reaksi, penyebab bernomor, langkah troubleshooting) — "
            "sajikan langkahnya APA ADANYA (terjemahkan ke Indonesia bila English), "
            "jangan menambah langkah karangan."
        )

    return {
        "total_database": fault_codes.count(),
        "total_database_eol": eol_dtc.count(),
        "kriteria": {"spn": spn, "fmi": fmi, "code": code, "query": query, "unit": unit},
        **flags,
        "found": not total_kosong,
        "jumlah_cocok": len(hits),
        "hasil": hasil,
        "jumlah_cocok_eol": len(hasil_eol),
        "hasil_eol": hasil_eol,
        "total_database_abs_scr": abs_scr_codes.count(),
        "jumlah_cocok_abs_scr": len(hasil_abs_scr),
        "hasil_abs_scr": hasil_abs_scr,
        "diagnosa_rinci": diagnosa_rinci,
        "pdf_diagnosa": pdf_cards,
        "catatan": catatan,
    }


def _t_diagnosa(args: dict, user: dict) -> dict:
    """DIAGNOSA kerusakan/kode kesalahan — GABUNG tiga sumber:
      1) DTC lokal (fault_codes): arti kode, SPN/FMI, lampu MIL/SVS — instan.
      2) SIMS EOL AI: penyebab + langkah pemeriksaan dari manual perbaikan RESMI
         Sinotruk + kasus kerusakan pabrik (20–90 dtk; jujur bilang bila tak ada).
      3) Data kita: part tersangka → PN per-unit + stok/harga (lewat tool lain).

    Rancangan pagar (PROJECT.md): query dipertegas ('truk Sinotruk/HOWO …') melawan
    salah-tafsir istilah; jawaban SIMS TIDAK dipoles bila ia bilang 'belum terindex';
    DTC lokal selalu disertakan sebagai jangkar fakta walau SIMS gagal/timeout."""
    keluhan = (args.get("keluhan") or args.get("query") or "").strip()
    kode = (args.get("kode") or "").strip()
    try:
        spn = int(args["spn"]) if str(args.get("spn") or "").strip() else None
        fmi = int(args["fmi"]) if str(args.get("fmi") or "").strip() else None
    except (TypeError, ValueError):
        spn = fmi = None
    if not (keluhan or kode or spn is not None):
        return {"error": "Sebutkan kode kesalahan (P0645 / SPN+FMI) atau keluhan/gejalanya."}

    # 1) Jangkar fakta: DTC lokal (instan, tak pernah gagal).
    dtc: list[dict] = []
    fmi_fallback = False
    try:
        for r in fault_codes.search(spn=spn, fmi=fmi, code=kode or None,
                                    query=keluhan or None, limit=5):
            dtc.append({"kode": r["code"], "spn": r["spn"], "fmi": r["fmi"],
                        "label": r["english"],
                        "deskripsi": r.get("deskripsi") or "",
                        "deskripsi_cn": r["desc_cn"],
                        "lampu_mil": r["mil"], "lampu_svs": r["svs"]})
        # Pasangan SPN+FMI persis tak terdaftar → jangan kehilangan jangkar:
        # tampilkan FMI lain untuk SPN yang sama (ditandai jujur).
        if not dtc and spn is not None and fmi is not None:
            for r in fault_codes.search(spn=spn, limit=5):
                dtc.append({"kode": r["code"], "spn": r["spn"], "fmi": r["fmi"],
                            "label": r["english"],
                            "deskripsi": r.get("deskripsi") or "",
                            "deskripsi_cn": r["desc_cn"],
                            "lampu_mil": r["mil"], "lampu_svs": r["svs"]})
            fmi_fallback = bool(dtc)
    except Exception:
        logger.exception("fault_codes.search gagal (dilewati)")

    # 1b) DTC EOL CNHTC lokal (semua unit kontrol; Indonesia + langkah perbaikan).
    eol_rows: list[dict] = []
    try:
        for r in eol_dtc.search(code=kode, query=keluhan, limit=5):
            eol_rows.append({"unit_kontrol": r["unit"], "kode": r["kode"],
                             "deskripsi": r["deskripsi"], "penyebab": r["penyebab"],
                             "perbaikan": r["perbaikan"], "part_terkait": r["part"]})
    except Exception:
        logger.exception("eol_dtc.search gagal (dilewati)")

    # 1c) Kode rem ABS (WABCO, SPN/FMI + langkah perbaikan) & SCR gas 国V (kode P).
    abs_scr_rows: list[dict] = []
    try:
        for r in abs_scr_codes.search(spn=spn, fmi=fmi, code=kode, query=keluhan, limit=5):
            abs_scr_rows.append({"sistem": r["sistem"], "kode": r["kode"], "spn": r["spn"],
                                 "fmi": r["fmi"], "blink": r["blink"], "komponen": r["komponen"],
                                 "deskripsi": r["deskripsi"], "penyebab": r["penyebab"],
                                 "reaksi": r["reaksi"], "perbaikan": r["perbaikan"],
                                 "lampu": r["lampu"]})
    except Exception:
        logger.exception("abs_scr_codes.search gagal (dilewati)")

    # 2) SIMS EOL AI — pertanyaan DIPERTEGAS agar tak salah-tafsir istilah
    #    (evaluasi: 'rem angin' pernah ditafsir 'damper AC').
    bagian = []
    if kode:
        bagian.append(f"kode kesalahan {kode}")
    if spn is not None:
        bagian.append(f"SPN {spn}" + (f" FMI {fmi}" if fmi is not None else ""))
    if keluhan:
        bagian.append(keluhan)
    q = ("Truk Sinotruk HOWO/SITRAK: " + ", ".join(bagian) +
         ". Jelaskan definisi kerusakan, kemungkinan penyebab, dan langkah pemeriksaan/"
         "perbaikan menurut manual resmi.")
    eol = sims_eol.tanya(q)

    # Lembar diagnosa PDF resmi (kartu bisa dibuka user) bila SPN diberikan.
    pdf_cards = _fault_pdf_cards(spn, fmi)

    # Isi TERSTRUKTUR lembar diagnosa (dtc_diagnosa, rombakan 2026-07-17).
    rinci: list[dict] = []
    try:
        if spn is not None:
            rinci = dtc_diagnosa.for_pair(spn, fmi)[:2]
        elif kode:
            rinci = dtc_diagnosa.for_code(kode)[:2]
    except Exception:  # pragma: no cover
        rinci = []

    out = {
        "found": (bool(dtc) or bool(eol_rows) or bool(abs_scr_rows)
                  or bool(eol.get("found")) or bool(pdf_cards) or bool(rinci)),
        "kriteria": {"kode": kode or None, "spn": spn, "fmi": fmi, "keluhan": keluhan or None},
        "kode_kesalahan_lokal": dtc,
        "kode_kesalahan_eol": eol_rows,  # Indonesia: penyebab + langkah perbaikan resmi EOL
        "kode_kesalahan_abs_scr": abs_scr_rows,  # ABS WABCO (SPN/FMI) + SCR gas 国V (Indonesia)
        "diagnosa_rinci": rinci,  # isi kartu: judul/part/trigger/penyebab[]/langkah[]
        "pdf_diagnosa": pdf_cards,
        "total_database_dtc": fault_codes.count(),
        "sumber": ("Database DTC lokal (Bosch + EOL CNHTC ber-langkah-perbaikan) + SIMS EOL AI "
                   "(asisten diagnosa resmi Sinotruk: manual perbaikan + kasus kerusakan pabrik)."),
    }
    if pdf_cards:
        out["catatan_pdf"] = (
            "📎 Lembar diagnosa PDF RESMI terlampir sebagai kartu — beri tahu user "
            "bisa membukanya langsung. ⛔ JANGAN membuat link/URL sendiri."
        )
    if rinci:
        out["catatan_rinci"] = (
            "'diagnosa_rinci' = ISI lembar diagnosa resmi (penyebab bernomor + "
            "langkah troubleshooting) — sajikan APA ADANYA (terjemahkan ke "
            "Indonesia bila English), jangan menambah langkah karangan."
        )
    if fmi_fallback:
        out["fmi_diminta_tak_terdaftar"] = fmi
        out["catatan_dtc"] = (
            f"⚠️ Pasangan SPN {spn} + FMI {fmi} TIDAK ADA di database — sampaikan "
            "TEGAS. 'kode_kesalahan_lokal' berisi FMI LAIN untuk SPN yang sama "
            "(kemungkinan yang dimaksud); minta user cek ulang angka FMI di scan-tool."
        )
    if eol.get("found"):
        out["diagnosa_sims"] = eol["jawaban"]
        out["sims_log_id"] = eol.get("log_id")
        out["catatan"] = (
            "'diagnosa_sims' = jawaban asisten resmi Sinotruk (manual perbaikan pabrik). "
            "SAJIKAN isinya dengan bahasa Indonesia yang RAPI — terjemahan mentahnya kadang "
            "kasar (mis. 'HAWO' = HOWO, 'rem ekspresi' = exhaust brake/rem gas buang); "
            "perbaiki istilahnya TANPA mengubah maknanya. Gabungkan dengan 'kode_kesalahan_lokal' "
            "(arti kode + lampu MIL/SVS). ⛔ JANGAN menambah penyebab/langkah yang TIDAK ada di "
            "jawaban SIMS. Bila jawaban menyebut KOMPONEN yang mungkin diganti dan user menyebut "
            "NOMOR RANGKA, PANGGIL cari_part_di_unit untuk komponen itu → beri PN + stok + harga. "
            "Tutup dengan pengingat: tetap verifikasi dengan pengukuran di lapangan."
        )
    elif eol.get("kosong"):
        out["diagnosa_sims"] = None
        out["sims_tak_ada"] = eol.get("jawaban")
        out["catatan"] = (
            "SIMS EOL AI JUJUR menyatakan pengetahuannya BELUM memuat topik ini. ⛔ JANGAN "
            "mengarang penyebab/langkah perbaikan. Sampaikan apa adanya, sajikan "
            "'kode_kesalahan_lokal' bila ada (arti kode + lampu), lalu tawarkan bantuan lain "
            "(cek part di unit, hubungi gudang/teknisi)."
        )
    else:
        out["diagnosa_sims"] = None
        out["sims_error"] = eol.get("error")
        out["catatan"] = (
            "SIMS EOL AI tak bisa dihubungi/timeout — sampaikan JUJUR bahwa panduan perbaikan "
            "resmi belum bisa diambil saat ini. Tetap sajikan 'kode_kesalahan_lokal' bila ada. "
            "⛔ JANGAN mengarang penyebab/langkah perbaikan dari pengetahuan umum."
        )
    return out


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
            "found": False,
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


def _t_diagram_wiring(args: dict, user: dict) -> dict:
    if not (wiring_ref.available() or manual_media.available()):
        return {"error": "Data diagram wiring belum tersedia di server."}
    komponen = (args.get("komponen") or args.get("query") or "").strip()
    if not komponen:
        return {
            "error": "Sebutkan komponen/sensornya.",
            "diagram_tersedia": wiring_ref.labels(),
        }

    # Definisi PIN ECU (pin_ecu — K4 2026-07-17): 'pin K54 untuk apa', 'sensor X
    # di pin berapa' — fan-out tanpa tool baru; ikut walau diagram tak ketemu.
    pin_rows: list[dict] = []
    try:
        pin_rows = pin_ecu.search(komponen, limit=10)
    except Exception:  # pragma: no cover
        pin_rows = []
    pin_payload = [{"ecu": r.get("ecu") or "", "konektor": r.get("konektor") or "",
                    "pin": r["pin"], "sinyal": r["sinyal"],
                    "deskripsi": r["deskripsi"], "nilai_uji": r["nilai_uji"],
                    "warna_kabel": r.get("warna_kabel") or ""}
                   for r in pin_rows]

    # Kartu SKEMA/MANUAL PDF (skema_ref — 2026-07-18): skema pneumatik ABS 6x4,
    # skema kelistrikan HOHAN N/HOWO N MC, manual pelatihan TFT NanoBCU (ID).
    # Di-stash sbg kartu yang bisa DIBUKA user (pola _fault_pdf_cards).
    skema_cards: list[dict] = []
    try:
        for s in skema_ref.search(komponen, limit=3):
            data = skema_ref.pdf_bytes(s["file"])
            if not data:
                continue
            export_id, filename = ai_export.stash_raw(s["label"], data, s["file"])
            skema_cards.append({"export_id": export_id, "filename": filename,
                                "judul": s["label"],
                                "deskripsi": s.get("deskripsi") or ""})
    except Exception:  # pragma: no cover
        skema_cards = []

    rows = wiring_ref.search(komponen, limit=6)
    # Perpustakaan GAMBAR manual (manual_media — 2026-07-18): skema/pinout ECU
    # (Bosch MC/NBCU/NanoBCU/ZF-AMT), skema pneumatik ABS, skema kelistrikan,
    # FOTO UNIT (Shantui dozer/loader/excavator/grader) — diekstrak dari PDF/Excel
    # sumber. Ikut ditampilkan INLINE bersama diagram wiring EOL.
    media_rows: list[dict] = []
    try:
        media_rows = manual_media.search(komponen, limit=6)
    except Exception:  # pragma: no cover
        media_rows = []

    gambar = []
    for r in rows:
        data = wiring_ref.image_bytes(r["file"])
        if not data:
            continue
        label = r.get("label") or r["file"]
        image_id, filename = ai_export.stash_raw(
            f"Diagram wiring — {label}", data, f"wiring_{r['file']}")
        gambar.append({"image_id": image_id, "filename": filename,
                       "pn": label, "nama_figure": label,
                       "kategori": r.get("grp") or "Wiring"})
    for r in media_rows:
        data = manual_media.image_bytes(r["file"])
        if not data:
            continue
        label = r.get("label") or r["file"]
        image_id, filename = ai_export.stash_raw(label, data, f"media_{r['file']}")
        gambar.append({"image_id": image_id, "filename": filename,
                       "pn": label, "nama_figure": label,
                       "kategori": r.get("tipe") or "Manual"})

    if not gambar:
        logger.info("MISS diagram_wiring q=%r user=%s", komponen,
                    user.get("username") or "?")
        out = {
            "found": bool(pin_payload or skema_cards),
            "jumlah": 0,
            "catatan": (f"Tidak ada diagram/gambar cocok untuk '{komponen}'. "
                        "Diagram yang tersedia: " + "; ".join(wiring_ref.labels())),
        }
        if pin_payload:
            out["pin_ecu"] = pin_payload
            out["catatan"] += (
                " NAMUN definisi PIN ECU yang cocok ADA di 'pin_ecu' (tabel "
                "konektor resmi — kolom 'ecu' menyebut unitnya: MC/NanoBCU/NBCU/"
                "ZF-AMT): sebutkan pin, sinyal/deskripsi, warna kabel, dan nilai "
                "ujinya — terjemahkan deskripsi China bila perlu.")
        if skema_cards:
            out["pdf_skema"] = skema_cards
            out["catatan"] += (
                " 📎 Ada SKEMA/MANUAL PDF resmi yang cocok — terlampir sebagai "
                "KARTU yang bisa DIBUKA user ('pdf_skema'): sebutkan judulnya & "
                "beri tahu user bisa membukanya dari kartu. ⛔ JANGAN buat link sendiri.")
        return out
    out = {
        "found": True,
        "jumlah": len(gambar),
        "diagram": [{"label": g["pn"], "grup": g["kategori"]} for g in gambar],
        "gambar": gambar,
        "catatan": (
            "Diagram WIRING/pin SIAP — tampil OTOMATIS (inline) di bawah jawabanmu. "
            "Ini diagram koneksi/definisi pin resmi (EOL CNHTC) untuk menelusuri "
            "gangguan kabel/konektor. Jawab SINGKAT (sebut nama diagram & kegunaannya); "
            "gambar sudah tampil sendiri — ⛔ JANGAN buat link/gambar/URL sendiri."
        ),
    }
    if pin_payload:
        out["pin_ecu"] = pin_payload
        out["catatan"] += (
            " 'pin_ecu' = definisi pin konektor ECU resmi yang cocok query "
            "(kolom 'ecu': MC/NanoBCU/NBCU/ZF-AMT) — sebutkan pin/sinyal/warna "
            "kabel/nilai uji bila relevan.")
    if skema_cards:
        out["pdf_skema"] = skema_cards
        out["catatan"] += (
            " 📎 'pdf_skema' = SKEMA/MANUAL PDF resmi terkait, terlampir sebagai "
            "KARTU yang bisa DIBUKA user — sebutkan judulnya. ⛔ JANGAN buat link sendiri.")
    return out


def _t_cari_manual(args: dict, user: dict) -> dict:
    """Cari ISI MANUAL teknik (manual_teks — Fase C 2026-07-18): naratif
    troubleshooting per-gejala (ECU Bosch) + panel instrumen TFT NanoBCU. Teks
    aslinya CHINA — model menerjemahkan runtime. Gambar halaman tampil inline;
    manual TFT juga dilampirkan sebagai kartu PDF (skema_ref)."""
    if not manual_teks.available():
        return {"error": "Data isi manual teknik belum tersedia di server."}
    topik = (args.get("topik") or args.get("query") or args.get("komponen") or "").strip()
    if not topik:
        return {"error": "Sebutkan topik/gejalanya."}

    rows = manual_teks.search(topik, limit=4)
    if not rows:
        logger.info("MISS cari_manual q=%r user=%s", topik, user.get("username") or "?")
        return {
            "found": False, "jumlah": 0,
            "catatan": (f"Tidak ada isi manual yang cocok untuk '{topik}'. Untuk KODE "
                        "error SPN/FMI/P pakai cari_kode_kesalahan; untuk diagram/pin "
                        "pakai diagram_wiring."),
        }

    hasil: list[dict] = []
    gambar: list[dict] = []
    ada_tft = False
    for r in rows:
        if (r.get("sumber") or "").startswith("manual_tft"):
            ada_tft = True
        item = {
            "sumber": r.get("sumber"), "halaman": r.get("halaman"),
            "judul": r.get("judul_id") or r.get("judul"),
            # teks China APA ADANYA — model WAJIB terjemahkan ke Indonesia.
            "teks_china": (r.get("teks") or "")[:1300],
        }
        if r.get("blok"):
            item["blok_china"] = r["blok"]
        if r.get("tabel"):
            item["tabel"] = r["tabel"][:6]
        if r.get("kode"):
            item["kode"] = r["kode"]
        hasil.append(item)
        for f in (r.get("gambar_ref") or [])[:3]:
            data = manual_media.image_bytes(f)
            if not data:
                continue
            label = r.get("judul_id") or r.get("judul") or f
            image_id, filename = ai_export.stash_raw(label, data, f"media_{f}")
            gambar.append({"image_id": image_id, "filename": filename,
                           "pn": label, "nama_figure": label, "kategori": "Manual"})
            if len(gambar) >= 8:
                break

    out = {
        "found": True,
        "jumlah": len(hasil),
        "hasil": hasil,
        "catatan": (
            "Isi MANUAL teknik resmi yang cocok. ⚠️ Field 'teks_china'/'blok_china'/"
            "'tabel' aslinya BAHASA CHINA — WAJIB kamu TERJEMAHKAN ke Bahasa Indonesia "
            "saat menjawab (jangan tampilkan China mentah; JANGAN ubah angka/kode/pin/"
            "satuan). Jawab runtut: sebut topik, lalu (bila kartu gangguan) kemungkinan "
            "penyebab & langkah pemeriksaan; sebut halaman/sumbernya. ⛔ JANGAN mengarang "
            "di luar isi manual."
        ),
    }
    if gambar:
        out["gambar"] = gambar
        out["catatan"] += (" Gambar halaman manual terlampir & tampil OTOMATIS (inline) "
                           "di bawah jawabanmu — ⛔ JANGAN buat link/gambar sendiri.")
    # Kartu PDF manual TFT (ada di skema_ref) supaya user bisa buka aslinya.
    if ada_tft:
        try:
            data = skema_ref.pdf_bytes("manual_tft_nanobcu.pdf")
            if data:
                export_id, filename = ai_export.stash_raw(
                    "Manual servis instrumen TFT NanoBCU", data, "manual_tft_nanobcu.pdf")
                out["pdf_skema"] = [{"export_id": export_id, "filename": filename,
                                     "judul": "Manual servis instrumen TFT NanoBCU",
                                     "deskripsi": "Manual pelatihan panel instrumen TFT NanoBCU (sumber)"}]
                out["catatan"] += (" 📎 'pdf_skema' = manual TFT PDF sumber, terlampir sbg "
                                   "KARTU yang bisa DIBUKA user.")
        except Exception:  # pragma: no cover
            pass
    return out


# Batas gambar tool penemuan. Frontend hanya merender 6 gambar per giliran
# (akumulasi lintas semua tool), jadi alat penemuan mengambil sedikit saja —
# yang ingin melihat lengkap memakai buka_pengetahuan.
_MAX_GAMBAR_PENGETAHUAN = 3
# buka_pengetahuan = alat BACA satu bagian, biasanya dipanggil sendirian →
# boleh memakai seluruh jatah 6 gambar per giliran.
_MAX_GAMBAR_BUKA = 6


def _label_gambar(r: dict, fname: str) -> str:
    """Keterangan gambar yang dilihat USER di bawah jawaban. Pakai caption hasil
    ekstraksi bila ada; kalau tidak, provenance (berkas + halaman) yang tetap
    berguna untuk menelusuri sumbernya."""
    for g in (r.get("gambar_info") or []):
        if g.get("file") == fname and (g.get("caption") or "").strip():
            return g["caption"].strip()[:120]
    sumber = r.get("sumber") or "dokumen"
    hal = r.get("halaman") or 0
    return f"{sumber} · hal {hal}" if hal else str(sumber)


def _t_cari_pengetahuan(args: dict, user: dict) -> dict:
    """Cari PENGETAHUAN INTERNAL yang ditulis/diunggah admin (store `pengetahuan`).

    Beda dari cari_manual (manual pabrikan, dibangun offline): isi ini dikurasi
    admin lewat /admin/pengetahuan dan bisa berubah kapan saja. Role pembeli HANYA
    melihat chunk yang sengaja dipublikasikan (untuk_pembeli=True) — disaring di
    search() lalu di-recek di sini (dua lapis, sengaja).
    """
    topik = (args.get("topik") or args.get("query") or args.get("kata_kunci") or "").strip()
    if not topik:
        return {"error": "Sebutkan topik yang ingin dicari."}
    if not pengetahuan.available():
        return {"error": "Belum ada pengetahuan internal yang diindeks di server."}

    is_pembeli = (user or {}).get("role") == "pembeli"
    rows = pengetahuan.search(topik, limit=4, untuk_pembeli=is_pembeli)
    if not rows:
        logger.info("MISS cari_pengetahuan q=%r user=%s", topik,
                    (user or {}).get("username") or "?")
        return {
            "found": False, "jumlah": 0,
            "catatan": (f"Tidak ada pengetahuan internal yang cocok untuk '{topik}'. "
                        "Jangan mengarang jawabannya — bila memang tidak ada, katakan "
                        "terus terang dan sarankan menghubungi admin."),
        }

    hasil: list[dict] = []
    gambar: list[dict] = []
    ada_tabel_pdf = False
    ada_asing = False
    ada_tabel_potong = False
    for r in rows:
        if is_pembeli and not r.get("untuk_pembeli"):
            continue  # lapis kedua — jangan pernah percaya satu filter saja
        judul = r.get("judul_id") or r.get("judul")
        # Hasil TERATAS dapat kutipan penuh (itu yang menjawab pertanyaan);
        # hasil berikutnya cuma konteks pendukung → cukup potongan pendek.
        # Menghemat ~2 KB per panggilan tanpa mengurangi bahan jawaban.
        batas_isi = 1200 if not hasil else 500
        item = {
            "judul": judul,
            "sumber": r.get("sumber"),
            "halaman": r.get("halaman") or 0,
            "ringkasan": r.get("ringkasan") or "",
            "isi": (r.get("teks") or "")[:batas_isi],
        }
        # `dokumen` hanya disertakan bila beda dari judul bagian — kalau sama,
        # itu string kembar yang dibayar dua kali.
        if r.get("judul") and r.get("judul") != judul:
            item["dokumen"] = r.get("judul")
        if r.get("bahasa") in ("zh", "ja", "en"):
            item["bahasa"] = r["bahasa"]
            ada_asing = True
        if r.get("jalur"):
            item["bagian_dari"] = " › ".join(r["jalur"][-2:])
        if r.get("tabel"):
            item["tabel"] = r["tabel"][:8]
            if r.get("baris_total") and r["baris_total"] > 8:
                item["tabel_dipotong"] = f"8 dari {r['baris_total']} baris"
                ada_tabel_potong = True
            if (r.get("tipe") or "") == "pdf":
                ada_tabel_pdf = True
        if r.get("kode"):
            item["kode"] = r["kode"]
        hasil.append(item)
        # Anggaran gambar KETAT: batas nyata 6 per giliran ditegakkan frontend
        # dan diakumulasi lintas SEMUA tool, jadi alat penemuan seperti ini tak
        # boleh menelan jatah tool lain. Hasil teratas 2 gambar, sisanya 1.
        jatah = 2 if len(hasil) == 1 else 1
        for f in (r.get("gambar_ref") or [])[:jatah]:
            if len(gambar) >= _MAX_GAMBAR_PENGETAHUAN:
                break
            data = pengetahuan.image_bytes(f)
            if not data:
                continue
            image_id, filename = ai_export.stash_raw(judul, data, f"pengetahuan_{f}")
            gambar.append({
                "image_id": image_id, "filename": filename,
                # UI merender "{pn} · {nama_figure}"; `balon` SENGAJA tak diisi
                # karena kalau ada, UI mencetak "Balon …" yang menyesatkan.
                "pn": (judul or f)[:60],
                "nama_figure": _label_gambar(r, f),
                "kategori": "Pengetahuan",
            })

    if not hasil:
        return {"found": False, "jumlah": 0,
                "catatan": f"Tidak ada pengetahuan internal yang cocok untuk '{topik}'."}

    catatan = (
        "Isi ini ditulis/diunggah ADMIN MASPART (pengetahuan internal perusahaan, "
        "BUKAN katalog part). Jawab HANYA dari isi di atas dan SEBUTKAN judul "
        "dokumen + berkas/halaman sumbernya supaya user bisa menelusuri. ⛔ JANGAN "
        "mengarang di luar isi ini; bila isi ini tidak menjawab pertanyaannya, "
        "katakan terus terang."
    )
    if ada_tabel_pdf:
        catatan += (" ⚠️ Field 'tabel' dari sumber PDF adalah REKONSTRUKSI tata letak "
                    "— jangan klaim presisi kolomnya, sebut angka apa adanya.")
    # Instruksi terjemah HANYA bila memang ada isi asing — dokumen Indonesia
    # (mayoritas) tak membayar token untuk kalimat ini.
    if ada_asing:
        catatan += (" ⚠️ Bagian ber-field 'bahasa' BUKAN Bahasa Indonesia — WAJIB kamu "
                    "TERJEMAHKAN isinya ke Bahasa Indonesia saat menjawab (jangan "
                    "tampilkan teks asing mentah; JANGAN ubah angka/kode/PN/satuan).")
    if ada_tabel_potong:
        catatan += (" Tabel dipotong (lihat 'tabel_dipotong') — butuh SELURUH barisnya "
                    "panggil buka_pengetahuan(dokumen, bagian).")
    out = {"found": True, "jumlah": len(hasil), "hasil": hasil}
    if gambar:
        out["gambar"] = gambar
        catatan += (" Gambar terlampir & tampil OTOMATIS (inline) di bawah jawabanmu "
                    "— ⛔ JANGAN buat link/gambar sendiri.")
    out["catatan"] = catatan   # WAJIB key terakhir (_cap_tool_content potong tengah)
    return out


def _t_buka_pengetahuan(args: dict, user: dict) -> dict:
    """Baca SATU bagian pengetahuan internal secara UTUH.

    Pembagian peran: `cari_pengetahuan` = MENEMUKAN (banyak bagian, dipotong,
    gambar sedikit); tool ini = MEMBACA (satu bagian, teks penuh, tabel dijahit
    utuh, gambar lengkap). Teks penuh juga MENGURANGI false-positive guard
    angka, karena makin banyak angka yang ter-grounding.
    """
    dokumen = (args.get("dokumen") or args.get("judul") or "").strip()
    bagian = (args.get("bagian") or "").strip()
    hanya = (args.get("hanya") or "semua").strip().lower()
    try:
        halaman = int(args.get("halaman") or 0)
    except (TypeError, ValueError):
        halaman = 0
    if not dokumen:
        return {"error": "Sebutkan 'dokumen' — judul PERSIS dari hasil cari_pengetahuan."}
    if not pengetahuan.available():
        return {"error": "Belum ada pengetahuan internal yang diindeks di server."}

    is_pembeli = (user or {}).get("role") == "pembeli"
    res = pengetahuan.buka(dokumen, bagian, halaman, untuk_pembeli=is_pembeli)
    if "_target" not in res:
        return res                       # sudah berbentuk hasil (gagal/daftar isi)

    r, dok, daftar, isi_dok = (res["_target"], res["_dok"],
                               res["_daftar"], res["_isi_dok"])
    # Lapis kedua: jangan pernah percaya satu filter saja.
    if is_pembeli and not r.get("untuk_pembeli"):
        return {"found": False, "dokumen": dok, "bagian_tersedia": [],
                "catatan": "Bagian itu tidak tersedia."}

    out: dict = {"found": True, "dokumen": dok,
                 "bagian": r.get("judul_id") or r.get("judul"),
                 "sumber": r.get("sumber"), "halaman": r.get("halaman") or 0}
    if r.get("jalur"):
        out["jalur"] = r["jalur"]
    if r.get("bahasa") in ("zh", "ja", "en"):
        out["bahasa"] = r["bahasa"]
    if hanya != "gambar":
        if hanya != "tabel":
            out["isi"] = (r.get("teks") or "")[:4000]
        tabel, total = pengetahuan.jahit_tabel(isi_dok, r)
        if tabel:
            out["tabel"] = tabel
            out["kolom"] = r.get("kolom") or []
            out["baris_ditampilkan"] = max(len(tabel) - 1, 0)
            out["baris_total"] = total or max(len(tabel) - 1, 0)
    gambar: list[dict] = []
    if hanya != "tabel":
        for f in (r.get("gambar_ref") or [])[:_MAX_GAMBAR_BUKA]:
            data = pengetahuan.image_bytes(f)
            if not data:
                continue
            label = _label_gambar(r, f)
            image_id, filename = ai_export.stash_raw(label, data, f"pengetahuan_{f}")
            gambar.append({"image_id": image_id, "filename": filename,
                           "pn": (out["bagian"] or f)[:60], "nama_figure": label,
                           "kategori": "Pengetahuan"})
        if gambar:
            out["gambar"] = gambar
            out["gambar_keterangan"] = [
                {"keterangan": g["nama_figure"], "halaman": r.get("halaman") or 0}
                for g in gambar]
    # Daftar bagian dikembalikan juga saat SUKSES: hasil tool ronde lama diganti
    # stub oleh _trim_old_tool_messages, jadi tanpa ini model bisa lupa judul
    # yang sah saat ingin membuka bagian lain.
    out["bagian_tersedia"] = daftar

    catatan = (
        f"Isi LENGKAP bagian ini (ditulis/diunggah ADMIN MASPART). Jawab HANYA dari "
        f"isi ini, sebut judul dokumen '{dok}' + berkas/halaman sumbernya. Angka & "
        "kode salin APA ADANYA — ⛔ jangan mengonversi satuan, menjumlahkan, atau "
        "menghitung sendiri. 'bagian_tersedia' = bagian lain dokumen ini bila perlu "
        "membuka yang lain. ⛔ JANGAN mengarang di luar isi ini."
    )
    if out.get("bahasa"):
        catatan += (" ⚠️ Isi bagian ini BUKAN Bahasa Indonesia — WAJIB kamu "
                    "TERJEMAHKAN saat menjawab (angka/kode/PN/satuan apa adanya).")
    if out.get("baris_total", 0) > out.get("baris_ditampilkan", 0):
        catatan += (f" Tabel masih terpotong ({out['baris_ditampilkan']} dari "
                    f"{out['baris_total']} baris) — sebutkan itu ke user.")
    if gambar:
        catatan += (" Gambar terlampir & tampil OTOMATIS (inline) di bawah jawabanmu "
                    "— ⛔ JANGAN buat link/gambar sendiri. 'gambar_keterangan' berasal "
                    "dari teks DI SEKITAR gambar, bukan hasil membaca gambarnya.")
    out["catatan"] = catatan             # WAJIB key terakhir
    return out


def _t_jadwal_perawatan(args: dict, user: dict) -> dict:
    if not maintenance_ref.available():
        return {"error": "Data jadwal perawatan Shantui belum tersedia di server."}
    model = (args.get("model") or "").strip()
    jenis = (args.get("jenis") or "").strip()
    query = (args.get("query") or "").strip()
    jam_raw = args.get("jam")
    jam = None
    if jam_raw not in (None, ""):
        try:
            jam = int(str(jam_raw).strip().lower().replace("h", "").replace("jam", "").strip())
        except (TypeError, ValueError):
            jam = None
    rows = maintenance_ref.search(model, query, jam, jenis)
    if not rows:
        logger.info(
            "MISS jadwal_perawatan model=%r jenis=%r query=%r jam=%r user=%s",
            model or None, jenis or None, query or None, jam, user.get("username") or "?",
        )
        return {
            "found": False,
            "jumlah": 0,
            "hasil": [],
            "model_tersedia": maintenance_ref.models_by_jenis(),
            "catatan": (
                f"Tidak ada item perawatan cocok untuk model '{model or '(semua)'}' / "
                f"jenis '{jenis or '(semua)'}' / kata kunci '{query or '(semua)'}' / "
                f"jam '{jam if jam is not None else '(semua)'}'. Lihat 'model_tersedia' "
                "(dikelompokkan per jenis alat). Interval jam lazim: 250/500/1000/2000."
            ),
        }
    return {
        "model_diminta": model or "(semua)",
        "jenis_diminta": jenis or "(semua)",
        "jam_diminta": jam,
        "jumlah": len(rows),
        "hasil": [
            {
                "jenis": r["jenis"],
                "model": r["model"],
                "varian": r["varian"],
                "sistem": r["sistem"],
                "nama": r["nama"],
                "part_number_shantui": r["part_number"],
                "jumlah": r["qty"],
                "ganti_pada_jam": r["ganti_jam"],
            }
            for r in rows[:80]
        ],
        "model_tersedia": maintenance_ref.models_by_jenis(),
        "catatan": (
            "JADWAL PERAWATAN BERKALA resmi Shantui. 'ganti_pada_jam' = jam servis saat "
            "part diganti; 'part_number_shantui' = PN asli (oli/coolant = spesifikasi); "
            "'jumlah' = qty per penggantian. Model ber->1 'varian' (Euro/mesin) → sebutkan "
            "variannya. Sajikan per 'sistem'."
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
        logger.exception("populasi gagal dibaca")
        return {"error": "gagal baca data populasi (gangguan internal)"}
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


# Batas kerja banding_part_armada: unit yang di-lookup config-nya & kelompok
# konfigurasi yang di-walk part-nya (walk Atlas per unit wakil itu mahal).
_ARMADA_MAX_UNITS = 80
_ARMADA_MAX_GROUPS = 5


def _t_banding_part_armada(args: dict, user: dict) -> dict:
    """BANDING SATU PART ANTAR SEMUA UNIT SATU CUSTOMER (armada): populasi →
    rangka tiap unit → konfigurasi pabrik EPC per-VIN (murah, di-cache) →
    kelompokkan unit per konfigurasi komponen terkait → walk EPC Parts Atlas
    HANYA pada satu unit WAKIL per kelompok (unit sekelompok = konfigurasi
    komponen identik) → verdict SAMA/BEDA dihitung di kode, bukan oleh model."""
    if not _can_populasi(user):
        return {"denied": True, "error": "Data populasi unit hanya untuk admin & akun 'mas'."}
    customer = (args.get("customer") or "").strip()
    part = (args.get("part") or "").strip()
    posisi = (args.get("posisi") or "").strip()
    if not customer:
        return {"error": "Sebutkan nama customer/PT pemilik armada."}
    if not part:
        return {"error": "Sebutkan part yang mau dibandingkan (mis. 'kampas kopling')."}

    try:
        pop = populasi.units_for_customer(customer)
    except Exception as e:  # pragma: no cover
        logger.exception("populasi gagal dibaca")
        return {"error": "gagal baca data populasi (gangguan internal)"}
    if not pop.get("available"):
        return {"available": False,
                "error": "Data populasi unit belum tersedia (file populasi.xlsx belum diunggah admin)."}
    units = [u for u in (pop.get("units") or []) if u.get("rangka")]
    tanpa_rangka = max(0, (pop.get("jumlah_unit") or 0) - len(units))
    if not units:
        out = {"found": False,
               "error": f"Tidak ada unit ber-nomor-rangka untuk customer '{customer}' di data populasi."}
        if pop.get("kandidat"):
            out["kandidat_customer"] = pop["kandidat"]
            out["jawaban_wajib"] = ("Customer persis itu tidak ada. Tampilkan 'kandidat_customer' "
                                    "dan minta user memilih — JANGAN menebak sendiri.")
        return out

    terpotong_unit = len(units) > _ARMADA_MAX_UNITS
    units = units[:_ARMADA_MAX_UNITS]

    # Kolom konfigurasi EPC yang MENENTUKAN part ini (per domain query): unit
    # dengan nilai kolom-kolom ini identik = komponen terpasangnya sama.
    terms, _syn = _expand_query(part)
    ql = (part + " " + " ".join(terms)).lower()
    modules, is_axle = _atlas_modules_for(ql)
    if is_axle:
        sig_fields = ("axle_depan", "axle_tengah", "axle_belakang")
    elif "LHQ" in modules:      # kopling: ditentukan pasangan mesin+gearbox
        sig_fields = ("engine", "gearbox")
    elif "BSX" in modules:      # transmisi
        sig_fields = ("gearbox",)
    elif "CDQ" in modules:      # domain campuran (mis. 'filter' polos) → semua kolom
        sig_fields = ("engine", "gearbox", "axle_depan", "axle_tengah", "axle_belakang")
    else:                       # FDJ / mesin & aksesorinya
        sig_fields = ("engine",)

    def _cfg(u: dict):
        v = epc.lookup(u["rangka"])
        if not v.get("found"):
            return u, None
        return u, " | ".join(f"{f}: {v.get(f) or '-'}" for f in sig_fields)

    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=16) as ex:
        pairs = list(ex.map(_cfg, units))

    groups: dict[str, dict] = {}
    epc_miss: list[dict] = []
    for u, sig in pairs:
        if sig is None:
            epc_miss.append(u)
            continue
        groups.setdefault(sig, {"konfigurasi": sig, "unit": []})["unit"].append(u)

    if not groups:
        return {"found": False, "customer_cocok": pop.get("customers"),
                "jumlah_unit": len(units),
                "error": "Tidak ada satu pun rangka armada ini yang dikenali EPC Sinotruk "
                         "(mungkin bukan unit Sinotruk/HOWO/SITRAK, atau EPC sedang gagal). "
                         "Jangan menyimpulkan sama/beda."}

    glist = sorted(groups.values(), key=lambda g: -len(g["unit"]))
    dicek, dilewati = glist[:_ARMADA_MAX_GROUPS], glist[_ARMADA_MAX_GROUPS:]

    def _cek_kelompok(g: dict) -> None:
        rep = g["unit"][0]["rangka"]
        r = _t_part_aus_dari_rangka(
            {"rangka": rep, "query": part, **({"posisi": posisi} if posisi else {})}, user)
        g["rangka_wakil"] = rep
        if r.get("found"):
            rows = ((r.get("parts") or []) + (r.get("parts_depan") or [])
                    + (r.get("parts_belakang") or []) + (r.get("parts_tanpa_posisi") or []))
            g["parts"] = [{k: p.get(k) for k in ("part_number", "nama", "posisi_poros",
                                                 "qty_di_unit", "stok_total", "harga_lokal")
                           if p.get(k) is not None} for p in rows[:15]]
            g["pn_set"] = sorted({p.get("part_number") for p in rows if p.get("part_number")})
            if r.get("catatan_mesin_weichai"):
                g["catatan_mesin_weichai"] = r["catatan_mesin_weichai"]
            if r.get("peringatan_tidak_lengkap"):
                g["peringatan_tidak_lengkap"] = r["peringatan_tidak_lengkap"]
        else:
            g["error_cek_part"] = r.get("error") or "cek part EPC gagal"
            if r.get("jawaban_wajib"):
                g["jawaban_wajib"] = r["jawaban_wajib"]
        g["jumlah_unit"] = len(g["unit"])
        g["unit"] = [{"rangka": x["rangka"], "model": x.get("model"), "tahun": x.get("tahun")}
                     for x in g["unit"][:15]]

    # Walk Atlas tiap kelompok PARALEL (masing-masing puluhan detik; berurutan
    # bisa >5 menit utk armada besar). Tiap _cek_kelompok memutasi dict g-nya
    # sendiri — tidak ada state silang antar-thread.
    with ThreadPoolExecutor(max_workers=_ARMADA_MAX_GROUPS) as ex:
        list(ex.map(_cek_kelompok, dicek))

    # Verdict dihitung SISTEM dari PN hasil EPC — model tinggal menyampaikan.
    ok = [g for g in dicek if "pn_set" in g]
    verdict: dict = {}
    if ok and len(ok) == len(dicek):
        identik = len({tuple(g["pn_set"]) for g in ok}) == 1
        if identik and not dilewati and not epc_miss:
            verdict["sama_semua"] = True
            verdict["kesimpulan"] = (
                f"SAMA — seluruh armada memakai daftar PN '{part}' yang sama "
                "(semua unit berkonfigurasi identik menurut EPC, dan PN hasil cek "
                "unit wakil tiap kelompok identik).")
        elif identik:
            verdict["sama_semua"] = None
            verdict["kesimpulan"] = (
                "Kelompok yang TERCEK semuanya memakai PN sama, TAPI ada kelompok yang "
                "dilewati / unit yang tak dikenali EPC — sampaikan 'kemungkinan besar sama' "
                "dengan catatan itu, JANGAN klaim pasti semua.")
        else:
            inter = set(ok[0]["pn_set"])
            for g in ok[1:]:
                inter &= set(g["pn_set"])
            verdict["sama_semua"] = False
            verdict["pn_sama_semua_kelompok"] = sorted(inter)
            verdict["kesimpulan"] = (
                "BERBEDA antar kelompok konfigurasi — rinci per kelompok: konfigurasi, "
                "contoh unit (rangka/model/tahun), dan PN-nya ('pn_set'/'parts').")
    else:
        verdict["sama_semua"] = None
        verdict["kesimpulan"] = ("BELUM TUNTAS — sebagian kelompok gagal dicek ke EPC. Jangan "
                                 "menyimpulkan sama/beda; sebutkan kelompok yang gagal & sarankan coba lagi.")

    return {
        "found": True,
        "customer_cocok": pop.get("customers"),
        "part": part,
        "jumlah_unit_populasi": pop.get("jumlah_unit"),
        "jumlah_unit_dicek": len(units),
        "dasar_pengelompokan": ("konfigurasi pabrik per-VIN dari EPC Sinotruk; kolom penentu: "
                                + ", ".join(sig_fields)),
        "jumlah_kelompok_konfigurasi": len(glist),
        "kelompok": dicek,
        **({"kelompok_dilewati": [{"konfigurasi": g["konfigurasi"], "jumlah_unit": len(g["unit"])}
                                  for g in dilewati],
            "catatan_dilewati": (f"Hanya {_ARMADA_MAX_GROUPS} kelompok konfigurasi terbesar yang "
                                 "dicek part-nya — sebutkan ada kelompok lain yang belum tercek.")}
           if dilewati else {}),
        **({"unit_tak_dikenal_epc": [{"rangka": u["rangka"], "model": u.get("model")}
                                     for u in epc_miss[:10]],
            "jumlah_tak_dikenal_epc": len(epc_miss)} if epc_miss else {}),
        **({"jumlah_unit_tanpa_rangka": tanpa_rangka} if tanpa_rangka else {}),
        **({"peringatan": (f"Unit customer ini > {_ARMADA_MAX_UNITS}; hanya "
                           f"{_ARMADA_MAX_UNITS} pertama yang dicek.")} if terpotong_unit else {}),
        "perbandingan": verdict,
        "sumber": ("Data populasi (armada per customer) + EPC Sinotruk resmi: konfigurasi pabrik "
                   "per-VIN untuk mengelompokkan unit, lalu part dicek via EPC Parts Atlas pada "
                   "SATU unit wakil per kelompok (unit sekelompok = konfigurasi komponen identik)."),
        "catatan": ("Verdict di 'perbandingan' DIHITUNG SISTEM dari PN hasil EPC — sampaikan apa "
                    "adanya, jangan menyimpulkan sendiri. Bila BERBEDA: rinci per kelompok. "
                    "⛔ JANGAN menyebut PN di luar 'parts'/'pn_set'."),
    }


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


