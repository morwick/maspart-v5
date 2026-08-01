# -*- coding: utf-8 -*-
# ai_parts/p4_tools_diagnosa.py — BAGIAN dari modul app.services.ai_assistant (SATU namespace).
# Dimuat berurutan oleh ai_assistant.py (loader) via exec — sengaja BUKAN
# submodul terpisah: monkeypatch test `ai.<attr>`, relative import, dan
# resolusi global lintas-bagian tetap bekerja persis seperti file tunggal.
# Urutan muat & pembagian: lihat _PARTS di ai_assistant.py.
from __future__ import annotations

# ── Tautan pengetahuan antar-store (2026-07-23) ──
_MAX_TERKAIT = 5


def _sisip_terkait(out: dict, ents: list, exclude: str, user: dict) -> dict:
    """Sisip `pengetahuan_terkait` (record store LAIN yang menyebut entitas yang
    sama — via knowledge_links) + 1 kalimat instruksi. INVARIAN: `catatan` tetap
    KEY TERAKHIR (aturan _cap_tool_content potong-tengah). Fail-open: gagal apa
    pun → out kembali utuh."""
    try:
        if not ents or not isinstance(out, dict):
            return out
        rows = knowledge_links.terkait(ents, exclude_store=exclude,
                                       limit=_MAX_TERKAIT,
                                       untuk_pembeli=_is_pembeli(user))
        # hanya tautan yang tool pembukanya SAH utk peran ini
        allowed = _allowed_tool_names(user)
        rows = [r for r in rows if r.get("buka", "").split("(", 1)[0] in allowed]
        if not rows:
            return out
        cat = out.pop("catatan", "") or ""
        # `ref` internal (dedup/debug) — TIDAK dikirim ke model (hemat token)
        out["pengetahuan_terkait"] = [{"store": r["store"], "judul": r["judul"],
                                       "buka": r["buka"]} for r in rows]
        out["catatan"] = (cat + " 🔗 'pengetahuan_terkait' = pengetahuan LAIN yang "
                          "menyinggung entitas sama (part/kode/model). BOLEH panggil "
                          "SATU tool lanjutan dari kolom 'buka' HANYA bila pertanyaan "
                          "user memang membutuhkannya — ⛔ jangan borong semua tautan.")
    except Exception:
        logger.exception("sisip pengetahuan_terkait gagal (dilewati)")
    return out


_SPN_MAKS_J1939 = 524287    # SPN standar J1939 = 19 bit


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

    if spn is not None and spn > _SPN_MAKS_J1939:
        # Angka mustahil (SPN J1939 = 19 bit). Dulu ini berujung nihil bisu dan
        # user tak pernah tahu bahwa masalahnya di angka yang ia ketik.
        flags["spn_di_luar_rentang"] = spn

    hits = fault_codes.search(spn=spn, fmi=fmi, code=code, query=query, limit=20)

    # Sumber KANONIK lintas-store (bosch/eol/abs/scr/kartu). Menutup tiga celah
    # sekaligus: eol_dtc.search tak punya param spn/fmi, fault_codes hanya bosch,
    # dan baris sumber="kartu" tak pernah keluar lewat proyeksi legacy.
    try:
        kanonik = dtc_codes.search_spn_fmi(spn, fmi, limit=20) if spn is not None else []
    except Exception:  # pragma: no cover
        kanonik = []
    cocok_eksak_kanonik = any(
        r.get("spn") == spn and (fmi is None or r.get("fmi") == fmi) for r in kanonik)

    if spn is not None and fmi is not None and not hits:
        # Pasangan persis tak terdaftar di tabel Bosch → JANGAN kosong diam-diam:
        # tampilkan semua FMI yang ADA untuk SPN itu + tandai jujur.
        alt = fault_codes.search(spn=spn, limit=20)
        if alt:
            hits = alt
            # ⚠️ Flag ini HANYA sah bila tak ada sumber lain yang benar-benar
            # punya pasangan persisnya. Dulu tidak dicek, sehingga SPN 520264
            # FMI 11 dijawab found=True (dari kartu PDF) SEKALIGUS diberi flag
            # "FMI 11 tak terdaftar" — model menerima dua pesan yang bertentangan.
            if not cocok_eksak_kanonik:
                flags["fmi_diminta_tak_terdaftar"] = fmi
                tersedia = sorted({r["fmi"] for r in alt if r.get("fmi") is not None})
                try:
                    tersedia = sorted(set(tersedia) | set(dtc_codes.fmi_tersedia(spn)))
                except Exception:  # pragma: no cover
                    pass
                flags["fmi_tersedia"] = tersedia
    if spn is not None and not hits and not kanonik and not code and not query:
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

    # Baris kanonik yang TIDAK terwakili sumber legacy di atas — terutama EOL
    # ber-SPN/FMI (hasil parse string kode) dan baris sumber="kartu". Tanpa ini,
    # data yang sudah kita miliki tetap tak pernah sampai ke model.
    _sudah = {(r.get("spn"), r.get("fmi"), (r.get("kode") or "").upper())
              for r in hits}
    _sudah |= {(r.get("spn"), r.get("fmi"), (r.get("kode") or "").upper())
               for r in abs_scr_hits}
    hasil_kanonik = [
        {
            "sumber": r.get("sumber"),
            "unit_kontrol": r.get("unit"),
            "kode": r.get("kode"),
            "spn": r.get("spn"),
            "fmi": r.get("fmi"),
            "deskripsi": r.get("deskripsi") or "",
            "deskripsi_cn": r.get("deskripsi_cn") or "",
            "penyebab": r.get("penyebab") or "",
            "perbaikan": r.get("perbaikan") or "",
            "part_terkait": r.get("part") or "",
        }
        for r in kanonik
        if (r.get("spn"), r.get("fmi"), (r.get("kode") or "").upper()) not in _sudah
    ][:15]

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
    if hasil_kanonik:
        catatan += (
            " 'hasil_kanonik' = baris dari store gabungan yang TIDAK ada di tabel "
            "Bosch/ABS/SCR (mis. EOL ber-SPN/FMI atau pasangan yang hanya punya "
            "lembar diagnosa PDF). Perlakukan setara: sajikan deskripsi & langkah "
            "perbaikannya apa adanya."
        )
    if flags.get("spn_di_luar_rentang") is not None:
        catatan += (
            f" ⚠️ SPN {flags['spn_di_luar_rentang']} MELEBIHI batas standar J1939 "
            f"(maksimum {_SPN_MAKS_J1939}) — angka itu hampir pasti salah ketik atau "
            "dua kode yang tergabung. Minta user membacakan ulang SPN dan FMI-nya "
            "TERPISAH dari scan-tool; jangan menebak."
        )
    total_kosong = (not hits and not hasil_eol and not hasil_abs_scr
                    and not hasil_kanonik and not pdf_cards and not diagnosa_rinci)
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

    out = {
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
        "jumlah_cocok_kanonik": len(hasil_kanonik),
        "hasil_kanonik": hasil_kanonik,
        "diagnosa_rinci": diagnosa_rinci,
        "pdf_diagnosa": pdf_cards,
        "catatan": catatan,
    }
    return _sisip_terkait(
        out, knowledge_links.entitas(dtc=code or "", spn=spn, fmi=fmi,
                                     teks=" ".join(filter(None, [query, unit]))),
        "dtc_codes", user)


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

    # 1d) Pengetahuan internal admin (kasus lapangan/materi terkurasi) — fan-out
    # server-side: terbukti di produksi model memanggil `diagnosa` SAJA untuk
    # keluhan, sehingga kasus internal yang PERSIS cocok tak pernah tersaji.
    # Hanya kecocokan meyakinkan (≥ _SKOR_LEMAH) yang menumpang; kecocokan
    # tipis cuma menambah bising pada hasil diagnosa yang sudah panjang.
    peng: dict = {}
    q_peng = " ".join(x for x in (keluhan, kode) if x)
    try:
        if q_peng and pengetahuan.available():
            berskor = pengetahuan.search_skor(
                q_peng, limit=1,
                untuk_pembeli=(user or {}).get("role") == "pembeli")
            if berskor and berskor[0][0] >= _SKOR_LEMAH:
                peng = _t_cari_pengetahuan({"topik": q_peng}, user)
    except Exception:
        logger.exception("fan-out pengetahuan di diagnosa gagal (dilewati)")

    out = {
        "found": (bool(dtc) or bool(eol_rows) or bool(abs_scr_rows)
                  or bool(eol.get("found")) or bool(pdf_cards) or bool(rinci)
                  or bool(peng.get("found"))),
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
    if peng.get("found"):
        out["pengetahuan_internal"] = peng.get("hasil") or []
        if peng.get("gambar"):
            out["gambar"] = peng["gambar"]
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
    if peng.get("found"):
        # `catatan` WAJIB tetap key terakhir (_cap_tool_content memotong tengah).
        out["catatan"] += (
            " 📚 'pengetahuan_internal' = kasus/materi INTERNAL MASPART yang cocok "
            "dengan keluhan ini — UTAMAKAN sebagai pengalaman kita sendiri, jalin ke "
            "jawaban dan SEBUT judul+sumbernya. Bagian ber-field 'bahasa' bukan "
            "Indonesia → TERJEMAHKAN saat menyajikan (angka/kode/PN apa adanya). "
            "Isi penuh: buka_pengetahuan(dokumen, bagian)."
            + (" Gambar terlampir tampil otomatis — ⛔ JANGAN buat link/gambar sendiri."
               if peng.get("gambar") else "")
        )
    return _sisip_terkait(
        out, knowledge_links.entitas(dtc=kode, spn=spn, fmi=fmi, teks=keluhan),
        "dtc_codes", user)


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
    out = {
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
    ents = []
    for r in rows[:10]:
        ents += knowledge_links.entitas(pn=r.get("part_number") or "",
                                        model=r.get("model") or "")
    return _sisip_terkait(out, list(dict.fromkeys(ents)), "filter_shantui", user)


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
        return _sisip_terkait(out, knowledge_links.entitas(teks=komponen),
                              "wiring_ref", user)
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
    return _sisip_terkait(out, knowledge_links.entitas(teks=komponen),
                          "wiring_ref", user)


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

    berskor = manual_teks.search_skor(topik, limit=4)
    if not berskor:
        logger.info("MISS cari_manual q=%r user=%s", topik, user.get("username") or "?")
        return {
            "found": False, "jumlah": 0,
            "catatan": (f"Tidak ada isi manual yang cocok untuk '{topik}'. Untuk KODE "
                        "error SPN/FMI/P pakai cari_kode_kesalahan; untuk diagram/pin "
                        "pakai diagram_wiring."),
        }

    skor_juara = berskor[0][0]
    ada_tier_lemah = False
    hasil: list[dict] = []
    gambar: list[dict] = []
    ada_tft = False
    for sc, r in berskor:
        if (r.get("sumber") or "").startswith("manual_tft"):
            ada_tft = True
        # Penyajian bertingkat sadar-skor (pola cari_pengetahuan): juara teks
        # penuh; pendukung kuat (≥0.45×juara) potongan; lemah judul-saja.
        juara = not hasil
        kuat = sc >= 0.45 * skor_juara
        batas = 1300 if juara else (500 if kuat else 0)
        item = {
            "sumber": r.get("sumber"), "halaman": r.get("halaman"),
            "judul": r.get("judul_id") or r.get("judul"),
        }
        if batas:
            # teks China APA ADANYA — model WAJIB terjemahkan ke Indonesia.
            item["teks_china"] = (r.get("teks") or "")[:batas]
            if r.get("blok"):
                item["blok_china"] = r["blok"]
            if r.get("tabel"):
                item["tabel"] = r["tabel"][:6]
        else:
            ada_tier_lemah = True
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

    catatan = (
        "Isi MANUAL teknik resmi yang cocok. ⚠️ Field 'teks_china'/'blok_china'/"
        "'tabel' aslinya BAHASA CHINA — WAJIB kamu TERJEMAHKAN ke Bahasa Indonesia "
        "saat menjawab (jangan tampilkan China mentah; JANGAN ubah angka/kode/pin/"
        "satuan). Jawab runtut: sebut topik, lalu (bila kartu gangguan) kemungkinan "
        "penyebab & langkah pemeriksaan; sebut halaman/sumbernya. ⛔ JANGAN mengarang "
        "di luar isi manual."
    )
    if skor_juara < _SKOR_LEMAH:
        catatan += (" ⚠️ Kecocokan LEMAH — kemungkinan TIDAK menjawab pertanyaan; "
                    "jangan dipaksakan, katakan bila tidak relevan.")
    if ada_tier_lemah:
        catatan += (" Hasil tanpa 'teks_china' = kecocokan tipis (judul saja); "
                    "abaikan bila judulnya tak relevan.")
    out = {"found": True, "jumlah": len(hasil), "hasil": hasil}
    if gambar:
        out["gambar"] = gambar
        catatan += (" Gambar halaman manual terlampir & tampil OTOMATIS (inline) "
                    "di bawah jawabanmu — ⛔ JANGAN buat link/gambar sendiri.")
    out["catatan"] = catatan
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
    _teks_ent = topik + " " + " ".join(
        " ".join(str(k) for k in (h.get("kode") or [])) for h in hasil[:3])
    return _sisip_terkait(out, knowledge_links.entitas(teks=_teks_ent),
                          "manual_teks", user)


# Batas gambar tool penemuan. Frontend hanya merender 6 gambar per giliran
# (akumulasi lintas semua tool), jadi alat penemuan mengambil sedikit saja —
# yang ingin melihat lengkap memakai buka_pengetahuan.
_MAX_GAMBAR_PENGETAHUAN = 3
# buka_pengetahuan = alat BACA satu bagian, biasanya dipanggil sendirian →
# boleh memakai seluruh jatah 6 gambar per giliran.
_MAX_GAMBAR_BUKA = 6
# Ambang skor juara "kecocokan lemah": di bawah ini berarti tak ada satu pun
# kecocokan kurasi (5)/frasa (20+) — paling banter beberapa kata level teks.
_SKOR_LEMAH = 10


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
    berskor = pengetahuan.search_skor(topik, limit=4, untuk_pembeli=is_pembeli)
    if not berskor:
        logger.info("MISS cari_pengetahuan q=%r user=%s", topik,
                    (user or {}).get("username") or "?")
        return {
            "found": False, "jumlah": 0,
            "catatan": (f"Tidak ada pengetahuan internal yang cocok untuk '{topik}'. "
                        "Jangan mengarang jawabannya — bila memang tidak ada, katakan "
                        "terus terang dan sarankan menghubungi admin."),
        }

    # Kata kueri (tanpa stopword) untuk memilih JENDELA isi & BARIS tabel yang
    # benar-benar relevan — bukan kepala chunk/tabel yang buta posisi.
    kata = pengetahuan.kata_kueri(topik)
    skor_juara = berskor[0][0]
    hasil: list[dict] = []
    gambar: list[dict] = []
    ada_tabel_pdf = False
    ada_asing = False
    ada_tabel_potong = False
    ada_tier_lemah = False
    ada_baris_pilihan = False
    for sc, r in berskor:
        if is_pembeli and not r.get("untuk_pembeli"):
            continue  # lapis kedua — jangan pernah percaya satu filter saja
        judul = r.get("judul_id") or r.get("judul")
        # Penyajian BERTINGKAT sadar-skor: juara dapat kutipan penuh; pendukung
        # kuat potongan pendek terarah; pendukung lemah (0.30–0.45× juara)
        # cukup judul+ringkasan — model bisa buka_pengetahuan bila relevan.
        # Lemah ≠ tak berguna: ketertelusurannya tetap ada, harganya turun.
        juara = not hasil
        kuat = sc >= 0.45 * skor_juara
        batas_isi = 1200 if juara else (400 if kuat else 0)
        item = {
            "judul": judul,
            "sumber": r.get("sumber"),
            "halaman": r.get("halaman") or 0,
        }
        isi = ""
        if batas_isi:
            isi = pengetahuan.potong_relevan(r.get("teks") or "", kata, batas_isi)
            if isi:
                item["isi"] = isi
        else:
            ada_tier_lemah = True
        # Ringkasan fallback = kepala teks — kembar dengan `isi` berjendela-awal;
        # kembar tak dibayar dua kali. Ringkasan kurasi/LLM (beda isi) tetap ikut.
        ringkas = (r.get("ringkasan") or "").strip()
        if ringkas and not (isi and isi[:200].startswith(ringkas[:60])):
            item["ringkasan"] = ringkas[:160] if not batas_isi else ringkas
        # `dokumen` hanya disertakan bila beda dari judul bagian — kalau sama,
        # itu string kembar yang dibayar dua kali.
        if r.get("judul") and r.get("judul") != judul:
            item["dokumen"] = r.get("judul")
        if r.get("bahasa") in ("zh", "ja", "en"):
            item["bahasa"] = r["bahasa"]
            ada_asing = True
        if r.get("jalur"):
            item["bagian_dari"] = " › ".join(r["jalur"][-2:])
        if r.get("tabel") and batas_isi:
            baris, n_cocok = pengetahuan.baris_relevan(r["tabel"], kata, 8)
            item["tabel"] = baris
            if n_cocok:
                ada_baris_pilihan = True
            ditampilkan = max(len(baris) - 1, 0)
            if r.get("baris_total") and r["baris_total"] > ditampilkan:
                item["tabel_dipotong"] = f"{ditampilkan} dari {r['baris_total']} baris"
                ada_tabel_potong = True
            if (r.get("tipe") or "") == "pdf":
                ada_tabel_pdf = True
        if r.get("kode"):
            kd = [str(k) for k in r["kode"]]
            # Kode yang menyinggung token kueri berangka DIDAHULUKAN, cap 6 —
            # daftar 12 kode tak relevan = token terbuang.
            digit_q = [w for w in kata if any(c.isdigit() for c in w)]
            if digit_q:
                cocok = [k for k in kd if any(q in k.lower() for q in digit_q)]
                kd = cocok + [k for k in kd if k not in cocok]
            item["kode"] = kd[:6]
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
        "Pengetahuan internal ADMIN MASPART (bukan katalog part). Jawab HANYA "
        "dari isi di atas + sebut judul & sumber/halaman. ⛔ JANGAN mengarang; "
        "bila tak menjawab pertanyaannya, katakan terus terang."
    )
    # Sinyal keyakinan: skor juara rendah = hanya 1-2 kata lemah yang cocok
    # (tak ada kecocokan kurasi/frasa) — bahan seperti ini rawan dipaksakan
    # jadi jawaban. Beri tahu model terang-terangan, itu lebih murah daripada
    # koreksi halusinasi belakangan.
    if skor_juara < _SKOR_LEMAH:
        catatan += (" ⚠️ Kecocokan LEMAH — kemungkinan TIDAK menjawab pertanyaan; "
                    "jangan dipaksakan, katakan bila tidak relevan.")
    if ada_tier_lemah:
        catatan += (" Hasil tanpa 'isi' = kecocokan tipis; bila judulnya relevan, "
                    "baca utuh via buka_pengetahuan(dokumen, bagian).")
    if ada_baris_pilihan:
        catatan += (" Baris tabel yang memuat kata kueri DIDAHULUKAN — urutan "
                    "baris bukan urutan asli dokumen penuh.")
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
    return _sisip_terkait(out, knowledge_links.entitas(teks=topik),
                          "pengetahuan", user)


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


# ══════════════════════════════════════════════════════════════════════
#  ajarkan_pengetahuan — admin MENGAJARI asisten lewat chat
# ══════════════════════════════════════════════════════════════════════
# Store-nya PERSIS store menu /admin/pengetahuan (add_dokumen + antre indexing);
# yang dihapus fitur ini cuma GESEKAN-nya (buka menu → isi form → tunggu). Store
# tipis hari ini bukan karena menunya tak ada, melainkan karena gesekan itu.
#
# ⛔ Tak ada penyimpanan diam-diam: draf DITAHAN server sampai user menekan
# kartu konfirmasi. Draf disimpan di SERVER (bukan diketik ulang model) karena
# balasan kartu adalah teks user biasa — kalau isi yang disimpan diambil dari
# argumen giliran "Simpan", model bebas menulis ulang apa pun dan yang masuk
# store bukan lagi yang dilihat & disetujui user.
_AJAR_DRAF: "dict[str, dict]" = {}
_AJAR_LOCK = threading.Lock()
_AJAR_TTL = 30 * 60.0        # draf menganggur > 30 menit: user sudah pindah topik
_AJAR_MAKS = 200             # pagar RAM (satu draf per percakapan, isinya kecil)
_AJAR_MAKS_ISI = 2000        # lebih panjang dari ini = dokumen, bukan ajaran chat
_AJAR_MAKS_JUDUL = 120
_AJAR_MAKS_TAG = 8
# Ambang "kembar" KONSERVATIF (3× ambang kecocokan lemah): salah menuduh kembar
# lebih merugikan daripada melewatkan satu — kartunya mengajak admin menimpa
# entri yang sebenarnya beda topik.
_AJAR_AMBANG_KEMBAR = 3 * _SKOR_LEMAH

# Angka yang BERGERAK (stok/harga) haram masuk store: pengetahuan tak punya
# tanggal kedaluwarsa, jadi angka hari ini akan dibacakan asisten berbulan-bulan
# kemudian sebagai fakta. Stok & harga sudah punya sumber hidup sendiri (indeks
# Accurate/SIMS). Angka TEKNIS statis (torsi, kapasitas oli, ukuran) justru
# ISI pengetahuan yang paling berguna — karena itu polanya menargetkan KATA
# stok/harga/mata uang yang berdekatan dengan angka, bukan angka apa pun.
_AJAR_ANGKA_HARAM = (
    re.compile(r"\brp\.?\s*\d", re.I),
    re.compile(r"(?:\b(?:cny|rmb|yuan|usd)\b\s*\d|\d\s*\b(?:cny|rmb|yuan|usd)\b)", re.I),
    re.compile(r"(?:¥\s*\d|\d\s*¥|\$\s*\d)"),
    # Jendela 40 char DALAM SATU KALIMAT ([^.\n]): "Stok minimal cucuk per di
    # gudang 5 pcs" menaruh angkanya 29 char dari katanya. Kalimat yang menyebut
    # stok/harga DAN sebuah angka sekaligus hampir selalu angka yang bergerak —
    # ditolak dengan pesan yang menawarkan versi tanpa angka, bukan buntu.
    re.compile(r"(?:\bstok\w*\b[^.\n]{0,40}\d|\d[^.\n]{0,40}\bstok\w*\b)", re.I),
    re.compile(r"(?:\bharga\w*\b[^.\n]{0,40}\d|\d[^.\n]{0,40}\bharga\w*\b)", re.I),
)
# Rujukan GANTUNG: entri pengetahuan dibaca berbulan-bulan kemudian TANPA
# percakapan ini, jadi "yang barusan"/"di atas" menunjuk ke ruang kosong.
_AJAR_GANTUNG = re.compile(
    r"\b(?:yang\s+barusan|barusan|tadi|di\s+atas|tersebut\s+di\s+atas|"
    r"seperti\s+di\s+atas|yang\s+tadi|yang\s+saya\s+sebut\s+tadi)\b", re.I)


def _ajar_kunci(user: dict, args: dict) -> str:
    """Kunci draf-tertunda: username|conversation_id. `_cid` DITITIPKAN server
    (pola `_q_user`/`_sheet_id`), BUKAN argumen model — kalau model boleh memilih
    kuncinya, ia bisa menyimpan draf percakapan orang lain. Klien lama tanpa
    conversation_id jatuh ke kunci per-user: fiturnya tetap jalan, hanya draf
    antar-tab jadi satu (dan draf memang cuma satu per user pada satu waktu)."""
    return f"{(user or {}).get('username') or ''}|{str(args.get('_cid') or '').strip()}"


def _ajar_sapu(now: float) -> None:
    """Buang draf kedaluwarsa. Dipanggil di dalam _AJAR_LOCK."""
    for k in [k for k, d in _AJAR_DRAF.items() if now - d.get("t", 0.0) > _AJAR_TTL]:
        _AJAR_DRAF.pop(k, None)


def _ajar_set_draf(kunci: str, draf: dict) -> None:
    """Satu draf per percakapan — draf baru MENIMPA yang lama (itulah alur
    'Perbaiki dulu': user mengoreksi, model menyusun draf revisi)."""
    with _AJAR_LOCK:
        now = time.monotonic()
        _ajar_sapu(now)
        if kunci not in _AJAR_DRAF and len(_AJAR_DRAF) >= _AJAR_MAKS:
            _AJAR_DRAF.pop(next(iter(_AJAR_DRAF)), None)
        _AJAR_DRAF[kunci] = {**draf, "t": now}


def _ajar_get_draf(kunci: str) -> dict | None:
    with _AJAR_LOCK:
        _ajar_sapu(time.monotonic())
        d = _AJAR_DRAF.get(kunci)
        return dict(d) if d else None


def _ajar_hapus_draf(kunci: str) -> None:
    with _AJAR_LOCK:
        _AJAR_DRAF.pop(kunci, None)


def _ajar_cari_kembar(judul: str, isi: str) -> dict | None:
    """Entri lama yang kemungkinan membahas hal SAMA. Best-effort: store rusak /
    belum ada indeks tak boleh mematikan pengajaran."""
    try:
        if not pengetahuan.available():
            return None
        berskor = pengetahuan.search_skor(f"{judul} {isi[:300]}", limit=3)
    except Exception:
        logger.exception("cek kembar pengetahuan gagal (dilewati)")
        return None
    if not berskor or berskor[0][0] < _AJAR_AMBANG_KEMBAR:
        return None
    skor, r = berskor[0]
    return {"dok_id": r.get("dok_id") or "", "skor": round(skor, 1),
            "judul": r.get("judul_id") or r.get("judul") or "(tanpa judul)"}


def _ajar_istilah(args: dict) -> tuple[dict | None, str]:
    """Deteksi & validasi PEMETAAN ISTILAH ("simpang empat = universal joint").

    Model mengisi `istilah_trigger`/`istilah_keywords` saat ajarannya murni
    penerjemahan istilah. Kenapa penting dipisah: catatan pengetahuan hanya
    BAHAN BACAAN model — mesin pencarian part (expand_query) cuma membaca
    KAMUS SINONIM. Istilah yang nyasar jadi catatan = "cari simpang empat"
    tetap nihil selamanya. Return (istilah|None, catatan_utk_pengantar).

    Validasi mengikuti pelajaran kamus 2026-07-29 (jangan ditemukan ulang):
    trigger cocok FRASA UTUH → maksimal 3 kata; trigger satu-kata generik
    MERUSAK (expand_query menyuntik keyword tiap grup cocok) → satu-kata
    minimal 4 huruf."""
    def _daftar(v) -> list[str]:
        if isinstance(v, str):
            v = re.split(r"[;,]", v)
        return [" ".join(str(x).split()).lower() for x in (v or []) if str(x).strip()]

    trig = _daftar(args.get("istilah_trigger") or args.get("istilah"))[:4]
    kw = _daftar(args.get("istilah_keywords") or args.get("istilah_keyword"))[:4]
    if not trig or not kw:
        return None, ""
    for t in trig:
        kata = t.split()
        if len(kata) > 3 or (len(kata) == 1 and len(t) < 4):
            return None, ""
    # Istilah yang SUDAH ada di kamus jangan ditawarkan lagi — dua entri utk
    # trigger yang sama membuat ekspansi pencarian tak terprediksi. Beri tahu
    # jujur pemetaan lamanya; sisanya biar admin urus di menu Kamus Sinonim.
    try:
        lama = {str(t).lower(): e for e in sinonim.entries()
                for t in (e.get("triggers") or [])}
    except Exception:
        lama = {}
    sudah = [t for t in trig if t in lama]
    if sudah:
        e = lama[sudah[0]]
        return None, (f"ℹ️ Istilah **{sudah[0]}** SUDAH ada di Kamus Sinonim "
                      f"(→ {', '.join(e.get('keywords') or [])}) — tidak ditawarkan "
                      "ulang; edit lewat menu Kamus Sinonim bila pemetaannya keliru.")
    return {"triggers": trig, "keywords": kw}, ""


def _ajar_maksud(args: dict, user: dict) -> tuple[dict | None, str]:
    """Deteksi & validasi RUTE MAKSUD ("kalau user minta gambar teknis, itu
    maksudnya exploded view").

    Beda tipis tapi penting dari _ajar_istilah: pemetaan ISTILAH menjawab "kata
    apa yang dicari di katalog" (→ Kamus Sinonim, mempengaruhi expand_query),
    RUTE MAKSUD menjawab "TOOL mana yang dipakai" (→ maksud.json, disuntik ke
    prompt tiap giliran). Sebelum store ini ada, ajaran jenis kedua tak punya
    rumah: jadi catatan = tak pernah terbaca (model tak memanggil
    cari_pengetahuan untuk permintaan gambar), jadi sinonim = salah tujuan &
    mengotori pencarian part.

    Return (rute|None, catatan_utk_pengantar). Rute yang TIDAK lolos validasi
    mengembalikan None + catatan jujur — alurnya tetap jalan sebagai catatan
    biasa, bukan buntu."""
    frasa = maksud._norm_frasa(args.get("maksud_frasa") or args.get("frasa"))[:maksud.MAKS_FRASA]
    tool = " ".join(str(args.get("maksud_tool") or args.get("tool") or "").split())
    if not frasa or not tool:
        return None, ""
    # Nama tool WAJIB nyata. Model kerap menyebut tool dengan nama karangan yang
    # masuk akal ('tampilkan_gambar'); tanpa cek ini rutenya tersimpan rapi lalu
    # tak pernah bisa dipatuhi — gagal senyap.
    if tool not in _allowed_tool_names(user):
        return None, (f"ℹ️ Rute tidak dibuat: tool **{tool}** tidak ada. Sebut nama "
                      "tool yang benar-benar kamu punya (lihat daftar alatmu) bila "
                      "user memang ingin mengarahkan sebuah istilah ke alat tertentu.")
    try:
        maksud._validasi_frasa(frasa)
    except ValueError as err:
        return None, f"ℹ️ Rute tidak dibuat: {err}"
    try:
        lama = next(filter(None, (maksud.cari_frasa(f) for f in frasa)), None)
    except Exception:  # pragma: no cover — store rusak jangan mematikan pengajaran
        logger.exception("cek rute maksud lama gagal (dilewati)")
        lama = None
    if lama:
        return None, (f"ℹ️ Frasa **{frasa[0]}** SUDAH punya rute ke "
                      f"**{lama.get('tool')}** — tidak ditawarkan ulang; ubah lewat "
                      "menu Rute Maksud bila pemetaannya keliru.")
    return {"frasa": frasa, "tool": tool,
            "catatan": " ".join(str(args.get("maksud_catatan") or "").split())}, ""


def _ajar_teks_draf(draf: dict) -> str:
    """Bubble jawaban yang DILIHAT user. Wajib berdiri sendiri & lengkap: inilah
    satu-satunya kesempatan user memeriksa isi sebelum masuk store (jalur kartu
    `_tanya` membuang teks tulisan model, lihat _tanya_pengantar di chat loop)."""
    baris = ["📝 **Draf pengetahuan** (belum tersimpan):", "",
             f"**Judul:** {draf['judul']}", "", draf["isi"]]
    if draf.get("kata_kunci"):
        baris += ["", "**Kata kunci:** " + ", ".join(draf["kata_kunci"])]
    rute = draf.get("maksud")
    if rute:
        frasa = ", ".join(f"*{f}*" for f in rute["frasa"])
        baris += ["",
                  "🎯 Ini **rute maksud** — dua pilihan tempat simpan:",
                  f"- **Rute Maksud**: mulai sekarang, kalau user menyebut {frasa} "
                  f"asisten langsung memakai alat **{rute['tool']}**. (Disarankan)",
                  "- **Catatan biasa**: asisten hanya bisa MENJELASKAN artinya bila "
                  "kebetulan membuka pengetahuan; pilihan alatnya tidak berubah."]
    ist = draf.get("istilah")
    if ist and not rute:
        baris += ["",
                  "🔀 Ini **pemetaan istilah** — dua pilihan tempat simpan:",
                  f"- **Kamus Sinonim**: pencarian part ikut mengerti — "
                  f"cari *{ist['triggers'][0]}* langsung menemukan "
                  f"*{ist['keywords'][0]}*. (Disarankan)",
                  "- **Catatan biasa**: asisten hanya bisa MENJELASKAN artinya; "
                  "pencarian part tidak berubah."]
    mirip = draf.get("mirip")
    if mirip:
        baris += ["", f"⚠️ Mirip dengan entri lama **{mirip['judul']}** — pilih "
                      "memperbarui entri itu atau menyimpan ini sebagai entri baru."]
    for k in ("catatan_maksud", "catatan_istilah"):
        if draf.get(k):
            baris += ["", draf[k]]
    return "\n".join(baris)


def _ajar_kartu(draf: dict) -> list[dict]:
    # Rute maksud paling dulu: ajaran "kalau user minta X pakai alat Y" salah
    # tempat di mana pun kecuali store rute — jadi kalau terdeteksi, itulah
    # tawaran utamanya (istilah & kembar-catatan tak relevan lagi).
    if draf.get("maksud"):
        opsi = ["Simpan ke Rute Maksud", "Jadi catatan saja", "Batal"]
    # Pemetaan istilah menang atas deteksi kembar-catatan: istilah memang
    # seharusnya TIDAK jadi catatan, jadi opsi perbarui-catatan tak relevan.
    elif draf.get("istilah"):
        opsi = ["Simpan ke Kamus Sinonim", "Jadi catatan saja", "Batal"]
    elif draf.get("mirip"):
        opsi = ["Perbarui entri lama", "Simpan sebagai entri baru", "Batal"]
    else:
        opsi = ["Simpan", "Perbaiki dulu", "Batal"]
    return [{"teks": "Simpan pengetahuan ini?", "opsi": opsi}]


def _ajar_antre(dok_id: str) -> str:
    """Antre indexing. Return catatan tambahan ('' bila mulus) — entri yang
    tersimpan tapi gagal diantre TETAP harus dilaporkan jujur, bukan dianggap
    aktif padahal chunk-nya belum ada."""
    try:
        pengetahuan_index.antre(dok_id)
        return ""
    except Exception:  # pragma: no cover
        logger.exception("antre indexing pengetahuan gagal untuk %s", dok_id)
        return (" ⚠️ Entri TERSIMPAN tapi antrean indexing gagal dijalankan — "
                "sampaikan ke user agar admin menekan 'Indeks ulang' di menu "
                "Pengetahuan AI supaya entri ini bisa dicari.")


def _t_ajarkan_pengetahuan(args: dict, user: dict) -> dict:
    """Admin MENGAJARI asisten lewat chat: susun draf → kartu konfirmasi → simpan.

    Satu tool ber-`aksi` (bukan 4 tool terpisah) supaya model tak perlu memilih
    di antara tool yang mirip, dan supaya seluruh alur punya satu tempat validasi.

    Gerbang di-recek DI SINI meski `_run_tool` sudah menyaring lewat
    `_allowed_tool_names` — dua lapis, pola yang sama dengan paket garansi.
    """
    if not _can_mengajar(user):
        logger.warning("ajarkan_pengetahuan ditolak untuk %s/%s",
                       (user or {}).get("role"), (user or {}).get("username"))
        return {"denied": True,
                "error": ("Akun ini tidak berhak menambah pengetahuan asisten. "
                          "Sampaikan jujur ke user & arahkan minta admin "
                          "mencentang 'Mengajari Pengetahuan (chat)' di Menu Control.")}

    aksi = (args.get("aksi") or args.get("action") or "draf").strip().lower()
    # Sinonim yang wajar dari model — jangan gagalkan alur hanya karena kata.
    aksi = {"save": "simpan", "simpan_sebagai_baru": "simpan_baru",
            "baru": "simpan_baru", "new": "simpan_baru", "update": "perbarui",
            "cancel": "batal", "draft": "draf", "buat": "draf",
            "kamus": "simpan_kamus", "simpan_ke_kamus": "simpan_kamus",
            "simpan_sinonim": "simpan_kamus",
            "maksud": "simpan_maksud", "rute": "simpan_maksud",
            "simpan_rute": "simpan_maksud", "simpan_ke_rute": "simpan_maksud",
            "simpan_ke_maksud": "simpan_maksud"}.get(aksi, aksi)
    kunci = _ajar_kunci(user, args)
    username = (user or {}).get("username") or ""

    # ── batal ────────────────────────────────────────────────────────
    if aksi == "batal":
        ada = _ajar_get_draf(kunci) is not None
        _ajar_hapus_draf(kunci)
        return {"found": True, "dibatalkan": True,
                "catatan": ("Draf dibuang, TIDAK ada yang disimpan. Konfirmasi "
                            "singkat ke user lalu lanjutkan percakapan biasa."
                            if ada else
                            "Tidak ada draf tertunda — tak ada yang perlu dibatalkan. "
                            "Sampaikan singkat & lanjutkan percakapan biasa.")}

    # ── simpan_maksud: rute frasa → TOOL, bukan catatan ──────────────
    # Satu-satunya jalur yang mengubah PEMILIHAN TOOL tanpa deploy. Rute masuk
    # blok prompt dinamis (_maksud_subset_block) pada giliran berikutnya yang
    # menyebut frasa itu — jadi efeknya langsung, tanpa restart & tanpa indexing.
    if aksi == "simpan_maksud":
        draf = _ajar_get_draf(kunci)
        if not draf:
            return {"found": False,
                    "catatan": ("Tidak ada draf tertunda (kedaluwarsa >30 menit?). "
                                "⛔ JANGAN mengaku sudah menyimpan. Minta user "
                                "mengulang ajarannya.")}
        rute = draf.get("maksud")
        if not rute:
            return {"found": False,
                    "catatan": ("Draf ini BUKAN rute maksud (tidak ada pasangan "
                                "frasa→tool), jadi tak bisa masuk Rute Maksud. "
                                "Tawarkan menyimpannya sebagai catatan (aksi='simpan').")}
        try:
            e = maksud.add(rute["frasa"], rute["tool"], catatan=rute.get("catatan") or "",
                           oleh=username, tools_sah=_allowed_tool_names(user))
        except ValueError as err:
            _ajar_hapus_draf(kunci)
            return {"found": False,
                    "catatan": (f"Rute ditolak: {err} Sampaikan apa adanya ke user.")}
        _ajar_hapus_draf(kunci)
        logger.info("rute maksud via chat oleh=%s %r -> %s",
                    username, e["frasa"], e["tool"])
        return {"found": True, "tersimpan_maksud": True,
                "frasa": e["frasa"], "tool": e["tool"],
                "catatan": ("Rute TERSIMPAN & langsung aktif — mulai giliran "
                            "berikutnya, permintaan yang menyebut frasa itu "
                            "diarahkan ke tool tersebut (berlaku untuk SEMUA staf). "
                            "Konfirmasi singkat ke user; rute bisa diubah/dihapus "
                            "di menu Rute Maksud.")}

    # ── simpan_kamus: pemetaan istilah → KAMUS SINONIM, bukan catatan ─
    # Jalur tulisnya jalur lama yang teruji (sinonim.add — dipakai menu admin &
    # loop belajar); reload otomatis per-mtime → pencarian langsung mengerti.
    if aksi == "simpan_kamus":
        draf = _ajar_get_draf(kunci)
        if not draf:
            return {"found": False,
                    "catatan": ("Tidak ada draf tertunda (kedaluwarsa >30 menit?). "
                                "⛔ JANGAN mengaku sudah menyimpan. Minta user "
                                "mengulang 'catat: …'-nya.")}
        ist = draf.get("istilah")
        if not ist:
            return {"found": False,
                    "catatan": ("Draf ini BUKAN pemetaan istilah (tidak ada pasangan "
                                "trigger→keyword), jadi tak bisa masuk Kamus Sinonim. "
                                "Tawarkan menyimpannya sebagai catatan (aksi='simpan').")}
        try:
            e = sinonim.add("", ist["triggers"], ist["keywords"])
        except ValueError as err:
            _ajar_hapus_draf(kunci)
            return {"found": False,
                    "catatan": (f"Kamus menolak: {err} Sampaikan apa adanya ke user "
                                "— entri serupa sudah ada, edit lewat menu Kamus "
                                "Sinonim bila pemetaannya perlu diubah.")}
        _ajar_hapus_draf(kunci)
        logger.info("istilah masuk kamus via chat oleh=%s %r -> %r",
                    username, e["triggers"], e["keywords"])
        return {"found": True, "tersimpan_kamus": True,
                "triggers": e["triggers"], "keywords": e["keywords"],
                "catatan": ("Istilah TERSIMPAN ke Kamus Sinonim & langsung aktif — "
                            "SEMUA pencarian part kini mengerti istilah itu (chat, "
                            "menu Cari Part, di mana pun). Konfirmasi singkat ke "
                            "user; entri bisa diedit di menu Kamus Sinonim.")}

    # ── simpan / simpan_baru / perbarui: WAJIB ada draf tersimpan ────
    if aksi in ("simpan", "simpan_baru", "perbarui"):
        draf = _ajar_get_draf(kunci)
        if not draf:
            return {"found": False,
                    "catatan": ("Tidak ada draf tertunda (belum dibuat atau sudah "
                                "kedaluwarsa >30 menit). ⛔ JANGAN mengaku sudah "
                                "menyimpan apa pun. Katakan jujur ke user bahwa "
                                "drafnya kedaluwarsa dan minta ia mengulang "
                                "kalimat 'catat: …'-nya, lalu panggil tool ini "
                                "lagi dengan aksi='draf'.")}
        # ⛔ Argumen `judul`/`isi` pada aksi ini DIABAIKAN — yang disimpan persis
        # draf yang tadi DILIHAT & DISETUJUI user.
        if aksi == "perbarui":
            mirip = draf.get("mirip") or {}
            dok_id = mirip.get("dok_id") or ""
            if not dok_id:
                return {"found": False,
                        "catatan": ("Draf ini tidak menunjuk entri lama mana pun, "
                                    "jadi tak ada yang bisa diperbarui. Tanyakan ke "
                                    "user apakah mau disimpan sebagai entri BARU "
                                    "(aksi='simpan_baru').")}
            try:
                d = pengetahuan.update_dokumen(
                    dok_id, judul=draf["judul"], deskripsi=draf["isi"][:160],
                    tag=draf["kata_kunci"], teks_admin=draf["isi"],
                    pakai_ai=False, asal="chat",
                    status="antre",
                    progres={"langkah": "Menunggu giliran", "kini": 0,
                             "total": 0, "persen": 0})
            except KeyError:
                _ajar_hapus_draf(kunci)
                return {"found": False,
                        "catatan": ("Entri lama itu sudah TIDAK ADA (mungkin baru "
                                    "dihapus admin). Sampaikan jujur; user bisa "
                                    "mengulang ajarannya sebagai entri baru.")}
            catatan_antre = _ajar_antre(dok_id)
            _ajar_hapus_draf(kunci)
            logger.info("pengetahuan DIPERBARUI via chat id=%s oleh=%s", dok_id, username)
            return {"found": True, "tersimpan": True, "diperbarui": True,
                    "id": dok_id, "judul": d.get("judul"),
                    "catatan": ("Entri LAMA berhasil diperbarui dengan isi draf & "
                                "diantre indeks ulang; aktif dalam hitungan detik. "
                                "Konfirmasi singkat ke user (sebut judulnya). ⛔ "
                                "Jangan mengarang isi lain." + catatan_antre)}

        try:
            d = pengetahuan.add_dokumen(
                draf["judul"], deskripsi=draf["isi"][:160], tag=draf["kata_kunci"],
                untuk_pembeli=False,      # default aman: internal dulu
                pakai_ai=False,           # judul+kata kunci sudah dikurasi giliran ini;
                                          # pengayaan ulang = bakar saldo & memperlambat
                oleh=username, teks_admin=draf["isi"], asal="chat")
        except ValueError as e:
            return {"error": f"Draf ditolak store: {e}"}
        catatan_antre = _ajar_antre(d["id"])
        _ajar_hapus_draf(kunci)
        logger.info("pengetahuan BARU via chat id=%s oleh=%s judul=%r",
                    d["id"], username, d["judul"])
        return {"found": True, "tersimpan": True, "id": d["id"], "judul": d["judul"],
                "catatan": ("Pengetahuan TERSIMPAN & diantre indexing; aktif dalam "
                            "hitungan detik dan bisa dipakai semua staf lewat "
                            "cari_pengetahuan. Konfirmasi singkat ke user (sebut "
                            "judulnya + boleh diubah/hapus di menu Pengetahuan AI). "
                            "⛔ Jangan mengarang isi lain." + catatan_antre)}

    # ── draf (default) ───────────────────────────────────────────────
    judul = " ".join(str(args.get("judul") or args.get("title") or "").split())
    isi = str(args.get("isi") or args.get("teks") or args.get("konten") or "").strip()
    kk = args.get("kata_kunci") or args.get("tag") or args.get("keywords") or []
    if isinstance(kk, str):
        kk = re.split(r"[;,]", kk)
    kata_kunci = pengetahuan.clean_list(kk, _AJAR_MAKS_TAG)

    if not judul or not isi:
        return {"error": ("Draf butuh 'judul' (singkat, mudah dicari) DAN 'isi' "
                          "(kalimat lengkap). Susun sendiri dari kalimat user — "
                          "jangan tanyakan ke user hal yang sudah ia tulis.")}
    if len(judul) > _AJAR_MAKS_JUDUL:
        judul = judul[:_AJAR_MAKS_JUDUL].rstrip()
    if len(isi) > _AJAR_MAKS_ISI:
        return {"error": (f"Isi terlalu panjang ({len(isi)} char, batas "
                          f"{_AJAR_MAKS_ISI}). Untuk materi sepanjang itu arahkan "
                          "user mengunggah berkasnya di menu Pengetahuan AI — di "
                          "sana isinya dipecah & diindeks per bagian. Atau ajarkan "
                          "satu poin saja yang paling penting.")}
    gabung = f"{judul}\n{isi}"
    if any(p.search(gabung) for p in _AJAR_ANGKA_HARAM):
        return {"error": ("Draf memuat angka STOK/HARGA. Angka seperti itu BERUBAH "
                          "tiap hari sedangkan pengetahuan tersimpan permanen — "
                          "asisten akan membacakannya sebagai fakta berbulan-bulan "
                          "kemudian. Stok & harga sudah punya sumber hidup sendiri "
                          "(indeks Accurate/SIMS). Jelaskan ini ke user & tawarkan "
                          "menyimpan versi TANPA angka stok/harga. Angka TEKNIS "
                          "statis (torsi, kapasitas oli, ukuran, interval servis) "
                          "justru BOLEH.")}
    if _AJAR_GANTUNG.search(gabung):
        return {"error": ("Draf memuat rujukan GANTUNG ('yang barusan', 'di atas', "
                          "'tadi'). Entri ini nanti dibaca TANPA percakapan ini, "
                          "jadi rujukan itu menunjuk ruang kosong. Tulis ULANG "
                          "drafnya berdiri sendiri: sebut nama part/istilah/kondisinya "
                          "secara eksplisit, lalu panggil tool ini lagi.")}

    istilah, catatan_istilah = _ajar_istilah(args)
    rute, catatan_maksud = _ajar_maksud(args, user)
    draf = {"judul": judul, "isi": isi, "kata_kunci": kata_kunci,
            "mirip": _ajar_cari_kembar(judul, isi),
            "istilah": istilah, "catatan_istilah": catatan_istilah,
            "maksud": rute, "catatan_maksud": catatan_maksud}
    _ajar_set_draf(kunci, draf)
    logger.info("draf pengetahuan disusun oleh=%s judul=%r kembar=%s istilah=%s rute=%s",
                username, judul, bool(draf["mirip"]), bool(istilah), bool(rute))
    return {
        "found": True,
        # Sentinel internal (prefiks `_`) yang dibaca chat loop: kartu konfirmasi,
        # teks draf yang di-prepend ke reply, dan pembebasan pagar anti ping-pong
        # (kartu konfirmasi atas aksi EKSPLISIT admin ≠ interogasi).
        "_tanya": _ajar_kartu(draf),
        "_tanya_pengantar": _ajar_teks_draf(draf),
        "_tanya_bebas_pagar": True,
    }


_GAP_MAKS_TAMPIL = 8


def _t_topik_gagal(args: dict, user: dict) -> dict:
    """TOPIK yang berulang GAGAL dijawab (penambang `ai_belajar`) → tawaran ajar.

    Menyambung dua pipa yang sudah ada: gap miner (mendeteksi kebolongan dari
    log produksi, ambang ≥3 kegagalan) dan ajarkan_pengetahuan (menambalnya).
    Dulu daftarnya diam di endpoint admin yang jarang dibuka — lingkarannya
    tidak pernah menutup: gagal → terdeteksi → ... berhenti di situ.

    Gerbang sama dengan mengajar (_can_mengajar): daftar kegagalan adalah
    peta kelemahan asisten — cukup dilihat orang yang bisa memperbaikinya."""
    if not _can_mengajar(user):
        return {"denied": True,
                "error": ("Akun ini tidak berhak melihat topik gagal / mengajar. "
                          "Arahkan minta admin mencentang 'Mengajari Pengetahuan "
                          "(chat)' di Menu Control.")}

    aksi = (args.get("aksi") or "daftar").strip().lower()
    aksi = {"list": "daftar", "resolve": "tandai_selesai", "selesai": "tandai_selesai",
            "hapus": "bukan_gap", "dismiss": "bukan_gap"}.get(aksi, aksi)

    if aksi in ("tandai_selesai", "bukan_gap"):
        topik = " ".join(str(args.get("topik") or "").split()).lower()
        if not topik:
            return {"error": "Sebutkan 'topik' persis seperti di daftar."}
        ok = ai_belajar.resolve_gap(topik)
        if not ok:
            sisa = [g.get("topik") for g in ai_belajar.gaps()][:_GAP_MAKS_TAMPIL]
            return {"found": False,
                    "topik_tersisa": sisa,
                    "catatan": (f"Topik '{topik}' tidak ada di daftar (mungkin sudah "
                                "ditandai / ejaannya beda). Cocokkan dengan "
                                "'topik_tersisa' lalu coba lagi — jangan menebak.")}
        label = ("ditandai SELESAI (sudah diajarkan)" if aksi == "tandai_selesai"
                 else "ditandai BUKAN gap (datanya memang tidak ada)")
        return {"found": True, "dihapus": topik,
                "catatan": (f"Topik '{topik}' {label} dan dikeluarkan dari daftar. "
                            "Konfirmasi singkat ke user.")}

    # ── daftar (default) ─────────────────────────────────────────────
    rows = sorted(ai_belajar.gaps(), key=lambda g: -(g.get("jumlah") or 0))
    if not rows:
        return {"found": False, "jumlah": 0,
                "catatan": ("Tidak ada topik yang berulang gagal saat ini — kabar "
                            "baik, sampaikan apa adanya. Daftar ini diisi otomatis "
                            "dari log (pertanyaan gagal ≥3×), jadi kosong = memang "
                            "belum ada pola kegagalan berulang.")}
    tampil = [{"topik": g.get("topik"), "jumlah": g.get("jumlah"),
               "contoh_pertanyaan": g.get("contoh")} for g in rows[:_GAP_MAKS_TAMPIL]]
    return {
        "found": True, "jumlah": len(rows), "ditampilkan": len(tampil),
        "topik": tampil,
        "catatan": (
            "Topik yang BERULANG gagal dijawab (dari log produksi, urut tersering). "
            "Sajikan ringkas bernomor + contoh pertanyaannya, lalu tawarkan "
            "mengajarkan salah satunya. Bila user memilih topik: susun DRAF dari "
            "'contoh_pertanyaan' + data tool yang relevan (cari_part/pengetahuan/"
            "manual), lalu panggil ajarkan_pengetahuan aksi='draf' — alur kartu "
            "konfirmasinya sama. SETELAH entri tersimpan utk sebuah topik, panggil "
            "topik_gagal aksi='tandai_selesai' dgn topik itu. Bila user bilang "
            "datanya memang tidak ada → aksi='bukan_gap'. ⛔ Jangan mengarang topik "
            "di luar daftar ini."
        ),
    }


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
    out = {
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
    ents = knowledge_links.entitas(model=model)
    for r in rows[:10]:
        for e in knowledge_links.entitas(pn=r.get("part_number") or ""):
            if e not in ents:
                ents.append(e)
    return _sisip_terkait(out, ents, "jadwal_perawatan", user)


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


# ═══════════════════════════════════════════════════════════════════════
#  GARANSI & KLAIM SIMS (DMS repair order) — 2026-07-22
#  Gerbang: _can_garansi (Menu Control 'ai_garansi'; admin selalu, pembeli
#  tidak pernah). Spec hanya ditawarkan bila boleh (p2) + _run_tool menolak
#  terpusat (p7); cek di handler = lapis ketiga (defense in depth).
# ═══════════════════════════════════════════════════════════════════════
_GARANSI_DENIED = {"error": "Data garansi & klaim SIMS tidak tersedia untuk akun Anda."}

# Foto klaim: kategori paling informatif didahulukan (kerusakan & bongkar);
# vin/nameplate/odometer hanya pelengkap administratif.
_FOTO_KLAIM_PRIORITAS = ("faultStartPhoto_file", "detachPhoto_file",
                        "allCarPhoto_file", "odometerPhoto_file",
                        "nameplatePhoto_file", "vinPhoto_file")
_MAX_FOTO_KLAIM = 4


def _t_cek_garansi(args: dict, user: dict) -> dict:
    """Status garansi + spek + nomor seri komponen SATU unit (SIMS DMS)."""
    if not _can_garansi(user):
        return dict(_GARANSI_DENIED)
    if not sims_warranty.available():
        return {"error": "Koneksi SIMS belum siap di server."}
    rangka = (args.get("rangka") or args.get("frame") or "").strip()
    if not rangka:
        return {"error": "Sebutkan nomor rangka (VIN penuh atau frame number)."}
    info = sims_warranty.info_unit(rangka)
    if not info:
        return {"found": False,
                "rangka": rangka,
                "catatan": ("Unit tidak ditemukan di SIMS untuk frame "
                            f"'{sims_warranty.frame_dari_rangka(rangka)}'. Cek ulang "
                            "nomor rangka; sampaikan jujur bila tetap tidak ada.")}
    g = info.get("garansi") or {}
    out = {"found": True, **info}
    status = ("MASIH AKTIF" if g.get("masih_aktif")
              else "SUDAH BERAKHIR" if g.get("masih_aktif") is False else "TIDAK DIKETAHUI")
    out["catatan"] = (
        f"Garansi {status}"
        + (f" — sisa {g['sisa_hari']} hari" if g.get("masih_aktif") else "")
        + ". Sumber: SIMS DMS resmi Sinotruk (data pabrik per-unit). "
        "'sisa_hari' & status dihitung server — sebut APA ADANYA, jangan hitung ulang. "
        "Nomor seri komponen (mesin/gearbox/gardan) = bawaan pabrik unit ini; berguna "
        "untuk klaim & pencarian part presisi. Untuk riwayat klaimnya panggil "
        "riwayat_klaim(rangka)."
    )
    return out


def _t_riwayat_klaim(args: dict, user: dict) -> dict:
    """Daftar work order klaim garansi — per unit, per no WO, atau terbaru.

    Saringan DURASI dihitung DI SINI, bukan oleh model: "WO yang lebih dari 72
    jam" butuh menyisir SEMUA klaim (semua_klaim, cache 10 mnt) lalu membanding
    angka — menyuruh model membuka halaman demi halaman itu boros token, kena
    rem anti-loop, dan perbandingannya rawan karang. Pola yang sama dgn
    hitung_part: angka dari data, saring/urut milik Python."""
    if not _can_garansi(user):
        return dict(_GARANSI_DENIED)
    if not sims_warranty.available():
        return {"error": "Koneksi SIMS belum siap di server."}
    rangka = (args.get("rangka") or args.get("vin") or "").strip()
    no_wo = (args.get("no_wo") or args.get("roNo") or "").strip()
    try:
        halaman = max(int(args.get("halaman") or 1), 1)
    except (TypeError, ValueError):
        halaman = 1

    def _angka(k):
        v = args.get(k)
        if v in (None, ""):
            return None
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    durasi_min = _angka("durasi_min_jam")
    durasi_maks = _angka("durasi_maks_jam")
    if durasi_min is not None or durasi_maks is not None:
        return _riwayat_klaim_saring_durasi(rangka, durasi_min, durasi_maks,
                                            _angka("limit"))

    d = sims_warranty.daftar_klaim(vin=rangka, ro_no=no_wo, halaman=halaman)
    if d is None:
        return {"error": "SIMS tidak merespons — coba lagi sebentar lagi."}
    rows = d.get("klaim") or []
    if not rows:
        return {"found": False, "total": d.get("total") or 0,
                "catatan": ("Tidak ada klaim yang cocok"
                            + (f" untuk '{rangka or no_wo}'" if (rangka or no_wo) else "")
                            + ". Sampaikan apa adanya — jangan mengarang riwayat.")}
    out = {"found": True, "total": d.get("total"), "halaman": d.get("halaman"),
           "klaim": rows}
    out["catatan"] = (
        "Riwayat klaim garansi dari SIMS DMS (dealer PT MAS). 'status' sudah "
        "berlabel Indonesia — sebut apa adanya. Isi lengkap satu WO (part+jasa+"
        "nilai+foto) panggil detail_klaim(no_wo). Total di 'total'; halaman "
        "berikut via parameter halaman. Nama/HP pelapor = data internal, "
        "tampilkan seperlunya."
    )
    return out


_SARING_DURASI_DEFAULT = 10   # "sebutkan 10 saja" adalah permintaan lapangan khasnya
_SARING_DURASI_MAKS = 50


def _riwayat_klaim_saring_durasi(rangka: str, durasi_min: float | None,
                                 durasi_maks: float | None,
                                 limit: float | None) -> dict:
    """Klaim tersaring durasi, urut TERLAMA dulu (yang bermasalah di atas)."""
    d = sims_warranty.semua_klaim(vin=rangka)
    if d is None:
        return {"error": "SIMS tidak merespons — coba lagi sebentar lagi."}
    semua = d.get("klaim") or []
    tanpa_durasi = 0
    cocok: list[dict] = []
    for k in semua:
        dj = k.get("durasi_jam")
        if not isinstance(dj, (int, float)):
            tanpa_durasi += 1          # WO belum selesai/terisi — bukan 0 jam!
            continue
        if durasi_min is not None and dj < durasi_min:
            continue
        if durasi_maks is not None and dj > durasi_maks:
            continue
        cocok.append(k)
    cocok.sort(key=lambda k: float(k["durasi_jam"]), reverse=True)
    n = int(limit) if limit and limit > 0 else _SARING_DURASI_DEFAULT
    n = min(n, _SARING_DURASI_MAKS)

    syarat = []
    if durasi_min is not None:
        syarat.append(f"≥ {durasi_min:g} jam")
    if durasi_maks is not None:
        syarat.append(f"≤ {durasi_maks:g} jam")
    label_syarat = " dan ".join(syarat)
    if not cocok:
        return {"found": False, "jumlah_cocok": 0,
                "jumlah_diperiksa": len(semua),
                "jumlah_tanpa_durasi": tanpa_durasi,
                "catatan": (f"Tidak ada WO berdurasi {label_syarat} dari "
                            f"{len(semua)} klaim yang diperiksa. "
                            f"{tanpa_durasi} WO tanpa data durasi (belum "
                            "selesai/terisi) TIDAK ikut dinilai — sebutkan itu, "
                            "jangan mengarang.")}
    return {
        "found": True,
        "jumlah_cocok": len(cocok),
        "jumlah_diperiksa": len(semua),
        "jumlah_tanpa_durasi": tanpa_durasi,
        "ditampilkan": len(cocok[:n]),
        "klaim": cocok[:n],
        "catatan": (
            f"WO berdurasi {label_syarat}: {len(cocok)} dari {len(semua)} klaim "
            f"(sudah DISARING & DIURUTKAN server, terlama dulu; ditampilkan "
            f"{len(cocok[:n])}). {tanpa_durasi} WO tanpa data durasi tak ikut "
            "dinilai — sebut angka-angka ini apa adanya, jangan menghitung "
            "ulang. Detail satu WO → detail_klaim(no_wo)."
        ),
    }


def _t_detail_klaim(args: dict, user: dict) -> dict:
    """Isi lengkap SATU work order klaim: part+jasa+nilai CNY, alur persetujuan,
    kebijakan tarif, dan foto klaim (inline)."""
    if not _can_garansi(user):
        return dict(_GARANSI_DENIED)
    if not sims_warranty.available():
        return {"error": "Koneksi SIMS belum siap di server."}
    no_wo = (args.get("no_wo") or args.get("roNo") or args.get("wo") or "").strip()
    if not no_wo:
        return {"error": "Sebutkan nomor work order (mis. RIDZ00526xxxxx)."}
    d = sims_warranty.daftar_klaim(ro_no=no_wo, page_size=3)
    rec = next((x for x in (d or {}).get("klaim") or []
                if (x.get("no_wo") or "").upper() == no_wo.upper()), None)
    if not rec:
        return {"found": False,
                "catatan": f"WO '{no_wo}' tidak ditemukan di SIMS. Cek ulang nomornya."}
    ro_id = str(rec.get("ro_id") or "")
    wo = sims_warranty.detail_wo(ro_id) or {}
    parts = [sims_warranty.format_part(p) for p in (wo.get("parts") or [])]
    jasa = [sims_warranty.format_jasa(j) for j in (wo.get("labours") or [])]
    total = []
    for a in (wo.get("amountList") or []):
        total.append({
            "tahap": sims_warranty.status_label(a.get("auditType")),
            "mata_uang": a.get("currencyCode"),
            "part_cny": a.get("partAmount"), "jasa_cny": a.get("labourAmount"),
            "oli_cny": a.get("oilAmount"), "admin_cny": a.get("partMgtAmount"),
            "total_cny": a.get("totalAmount"),
        })
    jejak = sims_warranty.jejak_audit(ro_id)
    out = {
        "found": True,
        "no_wo": rec.get("no_wo"), "frame": rec.get("frame"),
        "tanggal": rec.get("tanggal"), "km": rec.get("km"),
        "status": rec.get("status"),
        "gejala": wo.get("faultContent") or rec.get("gejala"),
        "tindakan": wo.get("handleMethod") or rec.get("tindakan"),
        "lokasi_kejadian": wo.get("faultAddress"),
        "pelapor": {"nama": wo.get("reportName"), "hp": wo.get("reportPhone")},
        "part_diklaim": parts,
        "jasa_diklaim": jasa,
        "oli_diklaim": len(wo.get("oils") or []),
        "biaya_tambahan": len(wo.get("additionals") or []),
        "total_per_tahap": total,
    }
    if jejak:
        akhir = jejak[-1]
        out["alur_persetujuan"] = {
            "tahap_kini": akhir.get("tahap_berikut") or akhir.get("tahap"),
            "menunggu": akhir.get("menunggu"),
            "riwayat": jejak[-4:],
        }
    pol = sims_warranty.kebijakan(ro_id)
    if pol:
        out["kebijakan_garansi"] = pol

    # Foto klaim → kartu gambar inline (kanal 'gambar' + _TOOLS_GAMBAR_INLINE).
    gambar = []
    fm = (sims_warranty.ringkas_audit(ro_id) or {}).get("fileMap") or {}
    for kat in _FOTO_KLAIM_PRIORITAS:
        for f in (fm.get(kat) or []):
            if len(gambar) >= _MAX_FOTO_KLAIM:
                break
            fid = str(f.get("fileUploadInfoId") or "")
            data = sims_warranty.foto_bytes(fid) if fid else None
            if not data:
                continue
            image_id, filename = ai_export.stash_raw(
                no_wo, data, f"klaim_{fid}.jpg")
            label = {"faultStartPhoto_file": "Foto kerusakan",
                     "detachPhoto_file": "Foto pembongkaran",
                     "allCarPhoto_file": "Foto unit",
                     "odometerPhoto_file": "Foto odometer",
                     "nameplatePhoto_file": "Foto nameplate",
                     "vinPhoto_file": "Foto VIN"}.get(kat, "Foto klaim")
            gambar.append({"image_id": image_id, "filename": filename,
                           "pn": rec.get("no_wo"), "nama_figure": label,
                           "kategori": "Klaim"})
        if len(gambar) >= _MAX_FOTO_KLAIM:
            break
    if gambar:
        out["gambar"] = gambar

    catatan = (
        "Detail klaim garansi SIMS DMS. ⚠️ Semua nilai uang dalam CNY (modal "
        "klaim pabrik) — JANGAN diubah ke rupiah kecuali user minta konversi. "
        "part_diklaim/jasa_diklaim KOSONG = WO belum dikerjakan mekanik "
        "(normal untuk status Order dibuka/Kerja ditugaskan), bukan error. "
        "'alur_persetujuan.menunggu' = nama yang sedang memegang klaim."
    )
    if gambar:
        catatan += (" Foto klaim terlampir & tampil OTOMATIS di bawah jawaban — "
                    "⛔ JANGAN buat link/gambar sendiri.")
    out["catatan"] = catatan   # WAJIB key terakhir (_cap_tool_content)
    return out


# ── Garansi tambahan (2026-07-22): cek massal, Excel riwayat, rekap ──
_GARANSI_MAKS_MASSAL = 200     # baris cek garansi massal per proses
_GARANSI_MAKS_EXCEL = 2000     # baris export riwayat klaim


def _t_sheet_garansi_massal(args: dict, user: dict) -> dict:
    """Cek status garansi BANYAK unit dari Excel (kolom VIN/rangka) → Excel hasil."""
    if not _can_garansi(user):
        return dict(_GARANSI_DENIED)
    if not sims_warranty.available():
        return {"error": "Koneksi SIMS belum siap di server."}
    parsed = ai_sheet.get_sheet(args.get("_sheet_id", ""), user.get("username", ""))
    if not parsed:
        return {"found": False, "error": "Tidak ada file Excel terlampir (atau kedaluwarsa). "
                                         "Minta user mengunggah Excel berisi kolom nomor rangka/VIN."}
    headers = parsed.get("headers") or []
    body = parsed.get("_body") or []
    minta = (args.get("kolom_rangka") or "").strip()
    i_frame = (ai_sheet._cari_kolom(headers, minta) if minta else None)
    if i_frame is None:
        for k in ("no rangka", "rangka", "vin", "frame", "chassis"):
            i_frame = ai_sheet._cari_kolom(headers, k)
            if i_frame is not None:
                break
    if i_frame is None:
        return {"found": False,
                "catatan": f"Kolom nomor rangka/VIN tidak terdeteksi (header: {headers}). "
                           "Minta user menyebut nama kolomnya."}

    frames: list[str] = []
    for r in body:
        v = (r[i_frame] if i_frame < len(r) else "").strip()
        if v:
            frames.append(v)
    frames = list(dict.fromkeys(frames))[:_GARANSI_MAKS_MASSAL]
    if not frames:
        return {"found": False, "catatan": "Tidak ada nomor rangka di Excel."}

    kolom = ["No", "Frame", "Model", "Brand", "Emisi", "Garansi Mulai",
             "Garansi Berakhir", "Masih Aktif", "Sisa Hari", "% Terpakai",
             "No Mesin", "No Gearbox"]
    baris: list[list] = []
    ketemu = 0
    for i, rk in enumerate(frames, start=1):
        info = sims_warranty.info_unit(rk)
        if not info:
            baris.append([str(i), sims_warranty.frame_dari_rangka(rk),
                          "TIDAK ADA DI SIMS", "", "", "", "", "", "", "", "", ""])
            continue
        ketemu += 1
        g = info.get("garansi") or {}
        komp = info.get("komponen") or {}
        aktif = ("Ya" if g.get("masih_aktif") else
                 "Tidak" if g.get("masih_aktif") is False else "?")
        baris.append([
            str(i), info.get("frame") or "", info.get("unit") or "",
            info.get("brand") or "", info.get("emisi") or "",
            g.get("mulai") or "", g.get("berakhir") or "", aktif,
            ai_export.ke_angka(g.get("sisa_hari") if g.get("sisa_hari") is not None else ""),
            ai_export.ke_angka(g.get("persen_terpakai") if g.get("persen_terpakai") is not None else ""),
            (komp.get("mesin") or {}).get("no_seri") or "",
            (komp.get("gearbox") or {}).get("no_seri") or "",
        ])
    judul = f"Status Garansi ({len(frames)} unit)"
    export_id, filename = ai_export.stash_export(judul, kolom, baris)
    return {"found": True, "export_id": export_id, "filename": filename, "judul": judul,
            "jumlah_baris": len(baris), "ketemu": ketemu, "tak_ada": len(frames) - ketemu,
            "catatan": ("File Excel status garansi siap — kartu unduh muncul OTOMATIS di bawah. "
                        f"Jawab SINGKAT ({len(frames)} unit dicek, {ketemu} ketemu, "
                        f"{len(frames) - ketemu} tak ada di SIMS). ⛔ JANGAN tulis ulang tabel & "
                        "JANGAN buat link sendiri.")}


def _t_excel_riwayat_klaim(args: dict, user: dict) -> dict:
    """Export daftar klaim garansi ke Excel (opsional filter unit/status)."""
    if not _can_garansi(user):
        return dict(_GARANSI_DENIED)
    if not sims_warranty.available():
        return {"error": "Koneksi SIMS belum siap di server."}
    rangka = (args.get("rangka") or args.get("vin") or "").strip()
    status_f = (args.get("status") or "").strip().lower()
    d = sims_warranty.semua_klaim(vin=rangka)
    if d is None:
        return {"error": "SIMS tidak merespons — coba lagi sebentar lagi."}
    klaim = d.get("klaim") or []
    if status_f:
        klaim = [k for k in klaim if status_f in (k.get("status") or "").lower()]
    if not klaim:
        return {"found": False, "catatan": "Tidak ada klaim yang cocok dengan filter."}
    klaim = klaim[:_GARANSI_MAKS_EXCEL]

    kolom = ["No", "No WO", "Frame", "Tanggal", "KM", "Gejala", "Tindakan",
             "Status", "Durasi (jam)", "Pelapor", "Mekanik", "Tanggal Audit"]
    baris = []
    for i, k in enumerate(klaim, start=1):
        baris.append([
            str(i), k.get("no_wo") or "", k.get("frame") or "", k.get("tanggal") or "",
            ai_export.ke_angka(k.get("km") if k.get("km") is not None else ""),
            k.get("gejala") or "", k.get("tindakan") or "", k.get("status") or "",
            ai_export.ke_angka(k.get("durasi_jam") if k.get("durasi_jam") is not None else ""),
            k.get("pelapor") or "", k.get("mekanik") or "", k.get("tanggal_audit") or "",
        ])
    judul = "Riwayat Klaim" + (f" — {rangka}" if rangka else " (semua)")
    export_id, filename = ai_export.stash_export(judul, kolom, baris)
    return {"found": True, "export_id": export_id, "filename": filename, "judul": judul,
            "jumlah_baris": len(baris), "total_klaim": d.get("total"),
            "catatan": ("File Excel riwayat klaim siap — kartu unduh muncul OTOMATIS di bawah. "
                        "Jawab SINGKAT (judul + jumlah klaim). Nilai CNY tidak ada di daftar ini "
                        "(pakai detail_klaim untuk nilai per WO). ⛔ JANGAN tulis ulang tabel.")}


def _t_rekap_klaim(args: dict, user: dict) -> dict:
    """Ringkasan/statistik klaim garansi (per status, per model, gejala tersering)."""
    if not _can_garansi(user):
        return dict(_GARANSI_DENIED)
    if not sims_warranty.available():
        return {"error": "Koneksi SIMS belum siap di server."}
    rangka = (args.get("rangka") or args.get("vin") or "").strip()
    d = sims_warranty.semua_klaim(vin=rangka)
    if d is None:
        return {"error": "SIMS tidak merespons — coba lagi sebentar lagi."}
    klaim = d.get("klaim") or []
    if not klaim:
        return {"found": False, "catatan": "Tidak ada klaim untuk direkap."}

    from collections import Counter
    per_status: Counter = Counter()
    per_model: Counter = Counter()
    gejala: Counter = Counter()
    durasi: list[float] = []
    tanggal: list[str] = []
    for k in klaim:
        per_status[k.get("status") or "?"] += 1
        g = (k.get("gejala") or "").strip()
        if g:
            gejala[g.lower()] += 1
        dj = k.get("durasi_jam")
        if isinstance(dj, (int, float)):
            durasi.append(float(dj))
        if k.get("tanggal"):
            tanggal.append(k["tanggal"])
        # model dari daftar_klaim tidak ada 'model' langsung; frame → grup awal
        fr = (k.get("frame") or "")[:2]
        if fr:
            per_model[fr] += 1

    out = {
        "found": True,
        "total_klaim": d.get("total"),
        "rentang_tanggal": ({"dari": min(tanggal), "sampai": max(tanggal)}
                            if tanggal else None),
        "per_status": [{"status": s, "jumlah": n}
                       for s, n in per_status.most_common()],
        "gejala_tersering": [{"gejala": g, "jumlah": n}
                             for g, n in gejala.most_common(15)],
        "rata_durasi_jam": (round(sum(durasi) / len(durasi), 1) if durasi else None),
        "unit_dari_rangka": rangka or None,
    }
    # Agregasi NILAI CNY — BOUNDED: hanya bila difilter per unit ATAU klaim
    # cukup sedikit (≤_NILAI_MAKS_WO); buka tiap WO (auditDetail) lambat.
    nilai_ket = ("Nilai CNY TIDAK dihitung (klaim terlalu banyak — buka tiap WO "
                 "lambat). Filter per unit (rangka) atau minta detail_klaim untuk "
                 "nilai per WO.")
    if rangka or len(klaim) <= sims_warranty._NILAI_MAKS_WO:
        ro_ids = [k.get("ro_id") for k in klaim if k.get("ro_id")]
        nv = sims_warranty.nilai_klaim(ro_ids)
        out["nilai_total_cny"] = nv.get("total_cny")
        out["nilai_dari_wo"] = nv.get("jumlah_wo")
        nilai_ket = (f"nilai_total_cny = jumlah biaya {nv.get('jumlah_wo')} WO (CNY, "
                     "totalAmount audit tertinggi/WO)"
                     + ("; DIPOTONG ke batas — belum semua WO dihitung." if nv.get("terpotong")
                        else ".") + " Nilai CNY apa adanya (jangan ubah ke rupiah kecuali diminta).")
    out["catatan"] = (
        "Rekap klaim garansi SIMS DMS armada. Angka dari daftar klaim (queryRepairOrder). "
        f"⚠️ {nilai_ket} 'gejala_tersering' = teks keluhan apa adanya (belum dikelompokkan "
        "makna). Sajikan ringkas & jujur."
    )
    return out


