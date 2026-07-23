# -*- coding: utf-8 -*-
# ai_parts/p5_tools_epc.py — BAGIAN dari modul app.services.ai_assistant (SATU namespace).
# Dimuat berurutan oleh ai_assistant.py (loader) via exec — sengaja BUKAN
# submodul terpisah: monkeypatch test `ai.<attr>`, relative import, dan
# resolusi global lintas-bagian tetap bekerja persis seperti file tunggal.
# Urutan muat & pembagian: lihat _PARTS di ai_assistant.py.
from __future__ import annotations

def _repair_kit_transmisi_impl(args: dict, user: dict) -> dict:
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
            "catatan": "'unit_tercatat' = unit ber-DATA repair kit — BUKAN daftar lengkap "
                       "unit ber-transmisi. Unit di luar daftar BISA tetap punya gearbox: "
                       "JANGAN klaim 'tidak punya transmisi' dari daftar ini; cek "
                       "cari_part(query='transmisi', unit=…). Sebut model/PN/unit utk "
                       "melihat kit-nya.",
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
    # PN dari EPC kerap ber-suffix varian ('WG9525160004/2') sementara indeks kita
    # menyimpan PN dasarnya → rows_for_pns mencocokkan dengan pemaaf (kalau tidak,
    # part tampil 'stok —' padahal ADA).
    local = part_index.rows_for_pns(pns)

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
    local = part_index.rows_for_pns(all_pns)   # pemaaf suffix varian EPC ('…/2')

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
    saran_fuzzy: list[dict] = []
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
        if not matched:
            # Fallback belajar: (a) saran fuzzy dari isi unit INI (toleran typo),
            # (b) catat istilah tak dikenal ke log 'Pencarian Nihil' → bahan
            # usulan sinonim otomatis (ai_sinonim_learn). Hanya dicatat bila
            # kamus sinonim TIDAK mengenali istilahnya (celah kamus, bukan data).
            saran_fuzzy = _bom_mungkin_maksud(parts, local, terms)
            if not matched_syn:
                try:
                    search_log.record_miss(kata, "bom", "asisten_bom")
                except Exception:
                    pass

    # Filter SISI deterministik (user minta 'yang kanan/kiri/depan/belakang'):
    # dari penanda posisi di NAMA part, bukan tafsiran model.
    sisi = (args.get("sisi") or "").strip().lower()
    catatan_sisi = None
    if sisi and matched:
        if sisi in ("kanan", "kiri", "depan", "belakang", "atas", "bawah"):
            sided = [p for p in matched if sisi in _parse_posisi(
                local.get(p["pn"], {}).get("part_name"), p.get("nama_cn"))]
            if sided:
                matched = sided
                catatan_sisi = (f"Difilter sisi '{sisi}' berdasar penanda posisi di nama part "
                                "(RH/LH/FRONT/REAR atau 右/左/前/后).")
            else:
                catatan_sisi = (f"Tidak ada part dengan penanda sisi '{sisi}' pada namanya — "
                                "SEMUA kandidat ditampilkan. Sampaikan ke user bahwa sisi tidak "
                                "bisa dipastikan dari nama part; JANGAN mengarang sisi.")

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
        # Posisi (kanan/kiri/depan/belakang) dideteksi Python dari nama —
        # pakai field ini saat user minta sisi tertentu, jangan menafsir sendiri.
        pos = _parse_posisi(eng, p.get("nama_cn"))
        if pos:
            out["posisi"] = pos
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
    if catatan_sisi:
        base["catatan_sisi"] = catatan_sisi

    # ASSEMBLY STRUKTURAL (pegas daun/suspensi): PN assembly di Loading List bisa
    # USANG/generik (kasus nyata: WG9114520140 di LL vs WG9525520641 di Atlas —
    # ground truth screenshot EPC user). Arahan teks saja TIDAK cukup (model pernah
    # mengabaikannya) → ambil PN assembly Atlas DETERMINISTIK di sini dan sajikan
    # sebagai data otoritatif dalam respons yang sama.
    _kl = (kata + " " + kategori).lower()
    if any(k in _kl for k in ("pegas daun", "per daun", "leaf spring", "plate spring",
                              "suspensi", "suspension", "pegas", "spring", "per assy")):
        atlas_assy: list[dict] = []
        try:
            tr = epc_bom.atlas_find_in_tree(
                rangka, ["plate spring assembly", "板簧", "钢板弹簧", "leaf spring"])
            if tr.get("found"):
                for p in (tr.get("parts") or []):
                    nm = " ".join((p.get("nama") or p.get("nama_cn") or "").split())
                    atlas_assy.append({"part_number": p.get("pn"), "nama": nm})
        except Exception:
            logger.exception("atlas assy utk pegas gagal (dilewati)")
        if atlas_assy:
            base["pn_assembly_atlas_otoritatif"] = atlas_assy[:15]
            base["peringatan_assembly_atlas"] = (
                "⛔⛔ PN ASSEMBLY pegas daun WAJIB dari 'pn_assembly_atlas_otoritatif' di atas "
                "(diambil dari PARTS ATLAS = persis tampilan EPC web — SUDAH disediakan, tak "
                "perlu tool lain). PN assembly pegas dari Loading List (mis. yang berpola "
                "generik) USANG untuk unit ini — JANGAN disajikan sebagai PN assembly utama. "
                "Loading List hanya untuk baut/bracket pelengkap.")
        else:
            base["peringatan_assembly_atlas"] = (
                "⚠️ PN assembly pegas daun TIDAK ditemukan di Parts Atlas unit ini. JANGAN "
                "sajikan PN assembly dari Loading List sebagai kepastian — sampaikan bahwa "
                "assembly-nya tidak ketemu di Atlas dan tampilkan hanya komponen pelengkap "
                "(bracket/baut) apa adanya. JANGAN mengarang.")

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
        if saran_fuzzy:
            base["mungkin_maksud"] = saran_fuzzy
            base["catatan_saran"] = (
                "0 hasil persis, tapi ada part unit ini yang NAMANYA MIRIP query "
                "(lihat 'mungkin_maksud'). Tawarkan ke user: 'mungkin maksud Anda …?' — "
                "JANGAN langsung menjawab tidak ada.")
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
        "catatan": ("identik=FALSE → WAJIB sebutkan part beda (hanya_di_rangka_1/_2). ⚠️ "
                    "'jumlah_beda'=TOTAL dua sisi; per sisi pakai 'jumlah_hanya_di_rangka_1/_2'. "
                    "daftar_lengkap=true → semua sudah di list. ⛔ JANGAN menyimpulkan sama/"
                    "beda dari kode model — pakai angka PART ini; PN & qty apa adanya. "
                    "📎 Kartu 'Unduh Excel' (lengkap) otomatis muncul — sebutkan singkat."),
    }


# Batas unit yang di-fetch Loading List-nya untuk banding massal (tiap call ke
# server China ~30 dtk; ambil paralel tapi tetap dibatasi agar tak menggantung).
_MASSAL_MAX_UNITS = 15


def _t_banding_rangka_massal(args: dict, user: dict) -> dict:
    """BANDINGKAN PART BANYAK UNIT (>=2) sekaligus — via DAFTAR nomor rangka ATAU
    nama CUSTOMER (armada). Untuk 'apakah kabin semua unit PT X sama?' / 'cek 5 VIN
    ini kabinnya sama atau beda?'. Ambil Loading List NYATA tiap VIN (paralel,
    dibatasi), filter per kategori, lalu KELOMPOKKAN unit ber-SET-PN identik →
    verdict SERAGAM/BEDA dihitung SISTEM (bukan tebakan dari kode model). Mode
    'semua' kategori → ringkasan kategori mana yang seragam & mana yang beda.
    Membangun kartu unduh Excel (matriks). HANYA unit Sinotruk/HOWO/SITRAK (EPC)."""
    # ── 1) Kumpulkan daftar unit (mode daftar VIN atau mode customer) ──
    raw_list = args.get("rangka_list") or args.get("rangka") or args.get("rangka_daftar") or []
    if isinstance(raw_list, str):
        raw_list = [x for x in re.split(r"[\s,;]+", raw_list) if x]
    customer = (args.get("customer") or "").strip()
    kategori = (args.get("kategori") or "").strip()

    vins: list[dict] = []
    sumber_unit = ""
    terpotong = 0
    total_customer = None
    customer_cocok = None

    if raw_list:
        seen: set[str] = set()
        for r in raw_list:
            rr = str(r).strip()
            if rr and rr.upper() not in seen:
                seen.add(rr.upper())
                vins.append({"rangka": rr})
        sumber_unit = "daftar nomor rangka yang disebut user"
    elif customer:
        if not _can_populasi(user):
            return {"denied": True,
                    "error": "Banding armada per CUSTOMER hanya untuk admin & akun 'mas'. "
                             "User lain bisa memberi DAFTAR nomor rangka langsung (rangka_list)."}
        try:
            pop = populasi.units_for_customer(customer)
        except Exception:  # pragma: no cover
            logger.exception("populasi gagal dibaca")
            return {"error": "gagal baca data populasi (gangguan internal)"}
        if not pop.get("available"):
            return {"available": False,
                    "error": "Data populasi unit belum tersedia (populasi.xlsx belum diunggah admin)."}
        punits = [u for u in (pop.get("units") or []) if u.get("rangka")]
        if not punits:
            out = {"found": False,
                   "error": f"Tidak ada unit ber-nomor-rangka untuk customer '{customer}' di populasi."}
            if pop.get("kandidat"):
                out["kandidat_customer"] = pop["kandidat"]
                out["jawaban_wajib"] = ("Customer persis itu tidak ada. Tampilkan 'kandidat_customer' "
                                        "dan minta user memilih — JANGAN menebak sendiri.")
            return out
        seen = set()
        for u in punits:
            k = (u.get("rangka") or "").upper()
            if k and k not in seen:
                seen.add(k)
                vins.append({"rangka": u["rangka"], "model": u.get("model"), "tahun": u.get("tahun")})
        total_customer = pop.get("jumlah_unit")
        customer_cocok = pop.get("customers")
        sumber_unit = "data populasi (armada per customer)"
    else:
        return {"error": "Sebutkan DAFTAR nomor rangka (rangka_list) ATAU nama customer/PT."}

    if len(vins) > _MASSAL_MAX_UNITS:
        terpotong = len(vins) - _MASSAL_MAX_UNITS
        vins = vins[:_MASSAL_MAX_UNITS]
    if len(vins) < 2:
        return {"error": "Perlu MINIMAL 2 unit untuk dibandingkan (beri >=2 nomor rangka, "
                         "atau customer dengan >=2 unit ber-rangka)."}

    # ── 2) Resolusi kategori ──
    semua_kat = kategori.lower() in ("", "semua", "all", "lengkap", "semua kategori")
    code = None
    kat_nama = "SEMUA kategori"
    if not semua_kat:
        code = catalog_bom.resolve_kategori(kategori) if catalog_bom.available() else None
        if not code:
            return {"found": False,
                    "error": f"Kategori '{kategori}' tak dikenal (mis. kabin, rem, transmisi, mesin, "
                             "kopling, kelistrikan, sasis, gardan). Atau sebut 'semua' untuk "
                             "ringkasan SEMUA kategori."}
        kat_nama = catalog_bom.KATEGORI_NAMA.get(code, kategori)

    # ── 3) Ambil Loading List tiap unit (paralel, dibatasi) ──
    from concurrent.futures import ThreadPoolExecutor

    def _fetch(v: dict):
        return v, epc_bom.loading_list(v["rangka"])

    with ThreadPoolExecutor(max_workers=6) as ex:
        fetched = list(ex.map(_fetch, vins))

    ok: list[tuple[dict, dict]] = []
    gagal: list[dict] = []
    token_issue = False
    for v, ll in fetched:
        if ll.get("found") and not ll.get("partial"):
            ok.append(({**v, "frame_number": ll.get("frame_number") or v["rangka"]}, ll))
        else:
            err = ll.get("_err")
            if err in ("token_expired", "no_token"):
                token_issue = True
            gagal.append({"rangka": v["rangka"], "alasan": (
                "token EPC" if err in ("token_expired", "no_token")
                else "jaringan EPC" if err == "network"
                else "data EPC tidak lengkap" if ll.get("partial")
                else "tidak ditemukan di EPC (cek VIN; hanya Sinotruk/HOWO/SITRAK)")})
    if token_issue and not ok:
        return {"found": False, "error": _EPC_TOKEN_MSG, "_token_issue": True}
    if len(ok) < 2:
        return {"found": False, "jumlah_unit_diminta": len(vins), "unit_gagal": gagal,
                "error": "Kurang dari 2 unit yang berhasil dibaca Loading List-nya — tak bisa "
                         "dibandingkan. Cek nomor rangka / coba lagi (EPC bisa lambat)."}

    _pncat = catalog_bom.pn_category_map() if catalog_bom.available() else {}

    def _catcode(pn: str) -> str:
        return (_pncat.get(catalog_bom._norm(pn)) or {}).get("kategori") or "00"

    # Per unit: {kode_kategori: set(PN)} + {PN: baris part} (utk nama/qty).
    units: list[tuple[dict, dict, dict]] = []
    for v, ll in ok:
        bycat: dict[str, set] = {}
        pmap: dict[str, dict] = {}
        for p in ll.get("parts", []):
            pn = p.get("pn")
            if not pn:
                continue
            bycat.setdefault(_catcode(pn), set()).add(pn)
            pmap[pn] = p
        units.append((v, bycat, pmap))

    frames = [u[0]["frame_number"] for u in units]

    def _pmap_get(pn: str) -> dict | None:
        for _, _, pmap in units:
            if pn in pmap:
                return pmap[pn]
        return None

    def _nama_lokal(pns) -> dict:
        localn: dict[str, str] = {}
        for r in part_index.search_exact_pns(list(pns)):
            pn = (r.get("part_number") or "").upper()
            if pn and pn not in localn:
                localn[pn] = r.get("part_name") or ""
        return localn

    def _analyze(c: str):
        """→ (glist[(frozenset, [idx])] urut kelompok terbesar, seragam, set_beda)."""
        groups: dict[frozenset, list[int]] = {}
        for idx, (_v, bycat, _pm) in enumerate(units):
            groups.setdefault(frozenset(bycat.get(c, set())), []).append(idx)
        glist = sorted(groups.items(), key=lambda kv: -len(kv[1]))
        sets = [set(k) for k, _ in glist]
        union = set().union(*sets) if sets else set()
        inter = set(sets[0]) if sets else set()
        for s in sets[1:]:
            inter &= s
        return glist, (len(glist) == 1), (union - inter)

    def _detail(c: str, cap: int = 40) -> dict:
        glist, seragam, beda = _analyze(c)
        localn = _nama_lokal(beda)

        def _row(pn: str) -> dict:
            p = _pmap_get(pn)
            en = localn.get(pn) or (epc_bom.translate_cn(p.get("nama_cn")) if p else None)
            return {"part_number": pn,
                    "nama": " ".join((en or (p.get("nama_cn") if p else "") or "").split()),
                    "kelompok_yang_punya": [gi + 1 for gi, (k, _) in enumerate(glist) if pn in k]}

        kelompok = [{
            "kelompok": gi + 1,
            "jumlah_unit": len(idxs),
            "jumlah_part": len(k),
            "unit": [{"rangka": units[i][0]["frame_number"],
                      **({"model": units[i][0].get("model")} if units[i][0].get("model") else {})}
                     for i in idxs][:15],
        } for gi, (k, idxs) in enumerate(glist)]
        return {
            "kategori_kode": c,
            "kategori": catalog_bom.KATEGORI_NAMA.get(c, c),
            "seragam": seragam,
            "jumlah_kelompok": len(glist),
            "kelompok": kelompok,
            "jumlah_part_beda": len(beda),
            "part_beda": [_row(pn) for pn in sorted(beda)[:cap]],
            "part_beda_terpotong": max(0, len(beda) - cap),
        }

    meta_unit = {
        "jumlah_unit_dibanding": len(units),
        "unit": [{"rangka": v["frame_number"],
                  **({"model": v.get("model")} if v.get("model") else {})} for v, _, _ in units],
        "sumber_unit": sumber_unit,
        **({"customer_cocok": customer_cocok} if customer_cocok else {}),
        **({"jumlah_unit_populasi": total_customer} if total_customer else {}),
        **({"unit_gagal": gagal, "catatan_gagal": (
            "Unit ini gagal dibaca dari EPC — TIDAK ikut dibandingkan; sebutkan ke user.")} if gagal else {}),
        **({"unit_terpotong": terpotong, "catatan_terpotong": (
            f"Unit melebihi batas {_MASSAL_MAX_UNITS}; hanya {_MASSAL_MAX_UNITS} pertama yang dicek.")}
           if terpotong else {}),
    }
    sumber = ("EPC Loading List per-VIN — membandingkan SET PART NYATA tiap unit, "
              "BUKAN tebakan dari kemiripan kode model/spesifikasi.")

    # ── 4a) Mode SATU kategori ──
    if code:
        d = _detail(code)
        # Excel: matriks part x unit (centang = part terpasang di unit itu).
        allpn = sorted(set().union(*[bycat.get(code, set()) for _, bycat, _ in units]) or set())
        _, _, beda_set = _analyze(code)
        allpn.sort(key=lambda pn: (pn not in beda_set, pn))  # yang BEDA di atas
        localn = _nama_lokal(allpn)
        kolom = ["Part Number", "Nama"] + frames
        baris: list[list[str]] = []
        for pn in allpn:
            p = _pmap_get(pn)
            en = localn.get(pn) or (epc_bom.translate_cn(p.get("nama_cn")) if p else None)
            nama = " ".join((en or (p.get("nama_cn") if p else "") or "").split())
            baris.append([pn, nama] + ["v" if pn in bycat.get(code, set()) else ""
                                       for _, bycat, _ in units])
        judul = f"Banding {kat_nama} - {len(units)} unit"
        export_id, filename = ai_export.stash_export(judul, kolom, baris)
        verdict = ("SERAGAM — semua unit yang dicek memakai daftar PN kategori ini yang sama."
                   if d["seragam"] else
                   "BERBEDA — ada unit dengan set PN kategori ini yang berbeda; rinci per kelompok.")
        return {
            "found": True,
            "mode": "satu_kategori",
            "kategori": kat_nama,
            **meta_unit,
            "seragam": d["seragam"],
            "jumlah_kelompok": d["jumlah_kelompok"],
            "kelompok": d["kelompok"],
            "jumlah_part_beda": d["jumlah_part_beda"],
            "part_beda": d["part_beda"],
            "part_beda_terpotong": d["part_beda_terpotong"],
            "perbandingan": {"seragam": d["seragam"], "kesimpulan": verdict},
            "export_id": export_id, "filename": filename, "judul": judul, "jumlah_baris": len(baris),
            "sumber": sumber,
            "catatan": ("Verdict DIHITUNG SISTEM — sampaikan apa adanya. seragam=true → semua unit "
                        "sama untuk kategori ini. seragam=FALSE → sebutkan berapa KELOMPOK, unit "
                        "mana di tiap kelompok, dan contoh part yang beda (part_beda). ⛔ JANGAN "
                        "menyimpulkan dari kode model/spesifikasi. ⛔ JANGAN sebut PN di luar data "
                        "ini. 📎 Kartu unduh Excel (matriks part x unit) otomatis muncul di bawah "
                        "jawaban — beri tahu user singkat."),
        }

    # ── 4b) Mode SEMUA kategori (ringkasan) ──
    codes_present = sorted({c for _, bycat, _ in units for c in bycat if c != "00"})
    ringkasan = []
    catgroupnum: dict[str, dict] = {}
    for c in codes_present:
        glist, seragam, beda = _analyze(c)
        catgroupnum[c] = {k: i + 1 for i, (k, _) in enumerate(glist)}
        ringkasan.append({
            "kategori_kode": c,
            "kategori": catalog_bom.KATEGORI_NAMA.get(c, c),
            "seragam": seragam,
            "jumlah_kelompok": len(glist),
            "jumlah_part_beda": len(beda),
        })
    kategori_beda = [r for r in ringkasan if not r["seragam"]]
    kategori_seragam = [r for r in ringkasan if r["seragam"]]

    # Excel: matriks unit x kategori (angka = nomor kelompok; kolom yang semua '1' = seragam).
    kolom = ["Unit (rangka)", "Model"] + [catalog_bom.KATEGORI_NAMA.get(c, c) for c in codes_present]
    baris = []
    for v, bycat, _ in units:
        row = [v["frame_number"], v.get("model") or ""]
        for c in codes_present:
            row.append(str(catgroupnum[c][frozenset(bycat.get(c, set()))]))
        baris.append(row)
    judul = f"Banding SEMUA kategori - {len(units)} unit"
    export_id, filename = ai_export.stash_export(judul, kolom, baris)

    verdict = ("SEMUA kategori SERAGAM di seluruh unit yang dicek." if not kategori_beda else
               "ADA kategori yang BERBEDA antar unit: "
               + ", ".join(r["kategori"] for r in kategori_beda) + ".")
    return {
        "found": True,
        "mode": "semua_kategori",
        **meta_unit,
        "seragam_semua": (not kategori_beda),
        "kategori_beda": kategori_beda,
        "kategori_seragam": kategori_seragam,
        "ringkasan_kategori": ringkasan,
        "perbandingan": {"seragam": (not kategori_beda), "kesimpulan": verdict},
        "export_id": export_id, "filename": filename, "judul": judul, "jumlah_baris": len(baris),
        "sumber": sumber,
        "catatan": ("Verdict DIHITUNG SISTEM. Sebutkan kategori mana SERAGAM & mana BEDA "
                    "(kategori_beda). Untuk melihat PART yang beda di satu kategori, user bisa "
                    "minta banding kategori itu spesifik (mis. 'rinci kabinnya'). ⛔ JANGAN "
                    "menyimpulkan dari kode model. 📎 Kartu unduh Excel (matriks unit x kategori; "
                    "angka = nomor kelompok, kolom yang semua '1' = seragam) muncul di bawah jawaban."),
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
    # AKSESORI TERPASANG DI MESIN — di EPC Weichai punya group sendiri; di Atlas
    # Sinotruk paling banter cuma pipa/bracket penghubungnya. Key frasa SPESIFIK
    # ('air compressor', bukan 'kompresor' polos) agar 'kompresor ac' tak ikut.
    "air compressor": ["air compressor", "空压机"],
    "alternator": ["alternator", "发电机"],
    "dinamo ampere": ["alternator", "发电机"],
    "dinamo starter": ["starter", "starting motor", "起动机"],
    "starter": ["starter", "starting motor", "起动机"],
    "turbo": ["turbocharger", "supercharger", "增压器"],
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
      "kepala silinder",
      # aksesori terpasang di mesin (kompresor angin, alternator, starter):
      "air compressor", "kompresor angin", "kompresor rem", "空压机",
      "alternator", "dinamo ampere", "发电机", "starter", "起动机"],
     ("FDJ", "FDJFJ"), False),
    (["clutch", "kopling", "离合器", "压盘", "matahari kopling", "dekrup", "plat kopling"],
     ("LHQ",), False),
    (["gearbox", "transmisi", "persneling", "perseneling", "变速器", "synchronizer",
      "sincromes", "同步器", "shift fork", "garpu persneling", "拨叉"],
     ("BSX",), False),
    # PEGAS DAUN/SUSPENSI: hidup di modul Chassis>Suspension (BUKAN poros) — part-nya
    # ditemukan lewat perluasan pohon (atlas_find_in_tree), bukan walk CDQ/QDQ.
    # is_axle=False PENTING: tanpa ini query pegas dianggap poros → posisi palsu +
    # auto-gambar nyasar ke figure gardan 'Drive device' (kasus nyata PJ306941).
    (["pegas daun", "per daun", "leaf spring", "plate spring", "板簧", "钢板弹簧",
      "pegas", "suspensi", "suspension", "shock absorber", "stabilizer",
      # token tunggal juga (model kerap query EN pendek 'spring'/'leaf') — tanpa
      # ini jatuh ke domain poros → posisi palsu + auto-gambar gardan nyasar:
      "spring", "leaf", "钢板"],
     ("CDQ", "QDQ"), False),

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


def _t_cari_part_di_unit(args: dict, user: dict) -> dict:
    """CARI PART DI SATU UNIT lewat PENCARIAN NAMA EPC per-kendaraan (match/part
    t=car) — JALUR UTAMA saat user menyebut nomor rangka + nama part.

    Kenapa ini yang utama: satu panggilan per kata kunci (~1 dtk) menjangkau SELURUH
    katalog unit, termasuk part yang TERSEMBUNYI di dalam assembly. Loading List
    (bom_dari_rangka) MELEWATKANNYA — kampas rem 'kampas rem SJ346500' hasilnya 0
    di sana, padahal AZ450045000042 (depan) & AZ450045000024 (belakang) memang
    terpasang. Walk Atlas (part_aus_dari_rangka) menemukannya tapi 18-22 dtk dan
    hanya untuk domain yang terpetakan (poros/mesin/kopling/gearbox).

    EPC hanya paham nama INGGRIS/Mandarin → istilah lapangan diterjemahkan lewat
    kamus sinonim dulu. Tiap PN disilangkan ke inventori lokal (stok/harga) dan
    diberi assembly INDUK (reverse) agar konteks pemasangannya jelas."""
    rangka = (args.get("rangka") or "").strip()
    kata_raw = args.get("kata_kunci") or args.get("query") or ""
    # MULTI-ISTILAH (2026-07-23): log produksi 30 hari — model memanggil tool ini
    # 3-4× beruntun dalam SATU giliran (beberapa part sekaligus / istilah
    # alternatif) = ronde & latensi terbuang (giliran terlambat 87-148 dtk).
    # Terima ARRAY atau string berpemisah ';'/',' : semua istilah diekspansi &
    # dicari SEKALI jalan, tiap hasil dilabeli istilah asalnya.
    if isinstance(kata_raw, (list, tuple)):
        kata_list = [str(x).strip() for x in kata_raw if str(x).strip()]
    else:
        kata_list = [p.strip() for p in re.split(r"[;,]", str(kata_raw)) if p.strip()]
    kata_list = list(dict.fromkeys(kata_list))[:6]
    kata = "; ".join(kata_list)
    if not rangka:
        return {"error": "Sebutkan nomor rangka (VIN atau frame number)."}
    if not kata:
        return {"error": "Sebutkan part yang dicari (mis. 'kampas rem', 'cross joint')."}

    # Istilah lapangan → keyword katalog EN/CN (EPC tak paham 'kampas') — per
    # istilah lalu digabung utk SATU pencarian. Query asli tetap disertakan
    # (mungkin sudah bahasa Inggris / PN).
    kws: list[str] = []
    matched_syn: list[str] = []
    _kw2istilah: dict[str, str] = {}
    for _ist in kata_list:
        terms, ms = _expand_query(_ist)
        matched_syn += ms
        kk = [t for t in dict.fromkeys(terms) if t and len(t.strip()) >= 3]
        # Kata kategori PAYUNG ('kopling','rem') tak diekspansi sinonim (trigger-nya
        # semua frasa spt 'kampas kopling') → 'kopling' polos melewatkan hampir semua
        # sub-part. Tambal dgn keyword keluarga penuh, HANYA saat sinonim TAK kena
        # (kalau kena, keyword-nya sudah presisi — jangan diperlebar & banjiri hasil).
        if not ms:
            for kw in _umbrella_keywords(_ist):
                if kw and len(kw.strip()) >= 3 and kw not in kk:
                    kk.append(kw)
        # Buang keyword generik tunggal (bolt/nut/...) bila ada keyword spesifik —
        # tanpa ini 'baut roda' membanjiri hasil dgn ratusan 'bolt' tak relevan.
        for kw in _tekan_generik(kk)[:12]:
            _kw2istilah.setdefault(kw.lower(), _ist)
            if kw not in kws:
                kws.append(kw)
    kws = kws[:12 if len(kata_list) == 1 else 24]

    # Mode TELITI: sisir SEMUA baris part list pohon unit. Perlu karena indeks
    # home/match/part TIDAK mencakup figure mesin MC — kasus nyata NJ248278:
    # 'ECU' 202V25803-7915 di figure MC07H common rail tak pernah keluar di match
    # (hanya 'ECU bracket'), padahal nyata terpasang. Lambat pada pencarian
    # PERTAMA per unit (~30-60 dtk, buka ratusan part list) lalu cache 1 jam.
    # Indeks item unit SUDAH siap (RAM/disk) → langsung jalur lengkap: instan,
    # cakupan penuh, SATU ronde tool, tanpa panggilan reverse per-PN. Indeks cepat
    # EPC (match) hanya dipakai selagi indeks lengkap belum terbangun.
    index_ready = epc_bom.items_index_ready(rangka)
    mode_teliti = bool(args.get("teliti")) or index_ready
    auto_teliti = False
    hasil: list[dict] = []
    if not mode_teliti:
        # Mulai bangun indeks lengkap DI LATAR — giliran/eskalasi berikut tinggal pakai.
        epc_bom.warm_items_index(rangka)
        d = epc_bom.search_in_unit(rangka, kws)
        err = d.get("_err")
        if err in ("token_expired", "no_token"):
            return {"found": False, "error": _EPC_TOKEN_MSG, "_token_issue": True}
        if err == "network":
            return {"found": False, "error": "Gagal menghubungi server EPC (jaringan). Coba lagi."}
        if err in ("not_found", "input"):
            return {"found": False, "error": "Nomor rangka tak ditemukan di EPC "
                                             "(cek VIN; hanya Sinotruk/HOWO/SITRAK)."}
        hasil = d.get("hasil") or []
        if not hasil:
            mode_teliti = auto_teliti = True   # match nihil → langsung sisir pohon
        elif epc_bom.items_index_ready(rangka):
            # Indeks lengkap unit KEBETULAN sudah siap (dipanaskan prefetch/giliran
            # sebelumnya, ATAU selesai dibangun selama pencarian cepat tadi). Sisir
            # LENGKAP sekarang: instan + cakupan penuh → hilangkan ketergantungan pada
            # model untuk mengulang teliti=true (dulu 'soft note'). Fast match tak lagi
            # untung bila indeks siap. index_ready dipakai utk label 'instan' di bawah.
            mode_teliti = True
            index_ready = True

    if mode_teliti:
        d = epc_bom.search_items_in_unit(rangka, kws)
        err = d.get("_err")
        if err in ("token_expired", "no_token"):
            return {"found": False, "error": _EPC_TOKEN_MSG, "_token_issue": True}
        if err == "network":
            return {"found": False, "error": "Gagal menghubungi server EPC (jaringan). Coba lagi."}
        if err in ("not_found", "input"):
            return {"found": False, "error": "Nomor rangka tak ditemukan di EPC "
                                             "(cek VIN; hanya Sinotruk/HOWO/SITRAK)."}
        hasil = d.get("hasil") or []
    frame = d.get("frame_number") or rangka
    if not hasil:
        # UMPAN BALIK KAMUS: jalur per-VIN (jalur UTAMA) kini ikut menyuplai loop
        # belajar sinonim. Hanya bila istilah TAK dikenali kamus (yang dikenali
        # tapi 0 hasil = data unit memang tak punya, bukan celah kamus). Best-effort.
        if not matched_syn:
            try:
                search_log.record_miss(kata, "unit", "asisten_unit")
            except Exception:
                pass
        return {
            "found": False, "frame_number": frame, "kata_kunci": kata,
            "kata_kunci_dicari": kws[:8],
            "sudah_mode_teliti": True,   # match + sisir seluruh pohon sama-sama nihil
            "error": f"Tidak ada part '{kata}' di katalog EPC unit {frame}.",
            "jawaban_wajib": ("Sampaikan JUJUR bahwa EPC unit ini tak punya part itu dengan "
                              "istilah tsb (sudah disisir SELURUH baris katalog unit). ⛔ JANGAN "
                              "mengarang PN. Boleh tawarkan: coba istilah lain / nama Inggris, "
                              "atau cek kategori lewat bom_dari_rangka."),
        }

    # Silang ke inventori lokal (nama katalog + stok + harga) — pola tool per-VIN lain.
    pns = [h["pn"] for h in hasil]
    # PN dari EPC kerap ber-suffix varian ('WG9525160004/2') sementara indeks kita
    # menyimpan PN dasarnya → rows_for_pns mencocokkan dengan pemaaf (kalau tidak,
    # part tampil 'stok —' padahal ADA).
    local = part_index.rows_for_pns(pns)
    boleh_harga = _boleh_harga(user)

    parts: list[dict] = []
    for h in hasil[:40]:
        pn = h["pn"]
        lr = local.get(pn, {})
        row = {
            "part_number": pn,
            "nama": " ".join((lr.get("part_name") or h.get("nama") or "").split()),
            "cocok_kata_kunci": h.get("kata_kunci"),
            "ada_di_inventori": bool(lr),
        }
        if h.get("pasok") == "stop":
            row["status_pasok"] = "STOP — tidak dipasok pabrik lagi (discontinued)"
        if len(kata_list) > 1:
            row["untuk_istilah"] = _kw2istilah.get((h.get("kata_kunci") or "").lower())
        # Assembly INDUK: hasil mode teliti SUDAH membawanya (dari node pohon yang
        # dibuka); hasil match perlu reverse — hanya beberapa PN teratas agar cepat.
        asm = h.get("dari_assembly") or {}
        if asm:
            row["di_dalam_assembly"] = asm.get("nama") or None
            row["assembly_pn"] = asm.get("pn") or None
        elif len(parts) < 8:
            try:
                rv = epc_bom.reverse_find_in_unit(rangka, pn)
                inst = (rv.get("instances") or [])
                if inst:
                    row["di_dalam_assembly"] = inst[0].get("parent_nama") or None
                    row["assembly_pn"] = inst[0].get("parent_pn") or None
                    row["jumlah_posisi"] = len({i.get("parent_pn") for i in inst if i.get("parent_pn")})
            except Exception:
                pass
        if lr:
            row["stok_total"] = lr.get("stok")
            row["stok_per_gudang"] = lr.get("gudang") or {}
            if boleh_harga:
                row["harga_lokal"] = lr.get("harga")
        parts.append(row)

    if _is_pembeli(user):
        for row in parts:
            row.pop("stok_per_gudang", None)

    note = None
    if matched_syn:
        note = (f"Istilah lapangan '{', '.join(dict.fromkeys(matched_syn))}' diterjemahkan ke "
                f"kata kunci katalog EPC: {', '.join(k for k in kws if k.lower() != kata.lower())}.")
    out = {
        "found": True, "frame_number": frame, "kata_kunci": kata,
        "kata_kunci_dicari": kws[:8], "catatan_sinonim": note,
        "jumlah_part": len(hasil), "parts": parts,
        "mode": ("teliti (sisir SEMUA baris part list pohon unit"
                 + (", otomatis karena pencarian cepat nihil)" if auto_teliti
                    else ", indeks unit sudah siap — instan)" if index_ready else ")"))
                if mode_teliti else "cepat (indeks pencarian EPC match/part)",
        "sumber": ("EPC per-unit — " + ("sisiran SELURUH baris katalog unit (pohon Atlas)."
                   if mode_teliti else
                   "indeks pencarian match/part t=car (cepat, cakupan luas).")),
        "catatan": ("PN di 'parts' PERSIS untuk unit ini (dari EPC). Jawab sebagai DAFTAR "
                    "ringkas (PN + nama + assembly induk bila ada + stok). Bila ada beberapa "
                    "varian (mis. kampas DEPAN vs BELAKANG), SEBUTKAN semuanya & jelaskan "
                    "bedanya lewat 'di_dalam_assembly' — JANGAN pilih satu diam-diam. "
                    "⛔ JANGAN mengarang PN di luar daftar ini."
                    + (" ⚠️ Part ber-'status_pasok' STOP = discontinued pabrik — SEBUTKAN "
                       "itu & sarankan cek pengganti (pengganti_part)."
                       if any(p.get("status_pasok") for p in parts) else "")),
    }
    if len(kata_list) > 1:
        # Istilah yang TIDAK menemukan satu part pun disebut eksplisit — model
        # wajib jujur per istilah, bukan menyimpulkan dari campuran hasil.
        _ketemu = {p0.get("untuk_istilah") for p0 in parts if p0.get("untuk_istilah")}
        _tanpa = [i for i in kata_list if i not in _ketemu]
        if _tanpa:
            out["istilah_tanpa_hasil"] = _tanpa
            out["catatan_istilah_nihil"] = (
                "Istilah berikut TIDAK menemukan part di unit ini: "
                + ", ".join(_tanpa) + ". Sampaikan JUJUR per istilah — "
                "⛔ JANGAN mengarang PN untuk istilah nihil.")
    if not mode_teliti:
        # Indeks match TIDAK meliput semua figure (mesin MC absen). Kalau part yang
        # DIMINTA user tak ada di daftar (yang keluar cuma kerabatnya — bracket/baut),
        # model wajib mengulang dengan teliti=true, BUKAN menyimpulkan tidak ada.
        out["catatan_cakupan"] = (
            "Hasil ini dari INDEKS pencarian cepat EPC yang TIDAK meliput semua figure "
            "(mis. part internal mesin MC kerap absen). Bila part yang DIMINTA user tidak "
            "ada di daftar (misal yang muncul hanya bracket/baut-nya), JANGAN simpulkan "
            "tidak ada — panggil ulang cari_part_di_unit dengan teliti=true. Indeks "
            "lengkap unit sudah mulai dibangun di latar (±1 menit), jadi panggilan "
            "ulangnya cepat."
        )
    if mode_teliti and d.get("incomplete"):
        out["peringatan"] = ("Sebagian node pohon gagal dibuka — hasil mungkin belum lengkap; "
                             "part yang tak ketemu belum tentu tidak ada.")
    return out


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
    kws = _tekan_generik(kws)

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
        if "FDJ" in modules:
            # Domain MESIN kosong di Atlas ≠ part tak ada: unit bermesin Weichai
            # menyimpan komponen mesinnya di EPC Weichai (Atlas berhenti di assembly).
            return {"found": False, "frame_number": res.get("frame_number"),
                    "order_no": res.get("order_no"), "_atlas": True,
                    "error": f"Part '{query}' tidak ketemu di EPC Parts Atlas Sinotruk unit ini.",
                    "jawaban_wajib": (
                        "⛔ JANGAN simpulkan part tidak ada / 'terintegrasi di engine assembly'. "
                        "Atlas Sinotruk berhenti di ENGINE ASSEMBLY — komponen mesin & aksesori "
                        "yang menempel di mesin (kompresor angin, alternator, starter, turbo, "
                        "piston, dll) pada unit bermesin WEICHAI ada di EPC Weichai. WAJIB "
                        f"panggil uraikan_mesin(rangka, part='{query}') SEKARANG sebelum "
                        "menjawab. JANGAN mengarang PN.")}
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
    # PN dari EPC kerap ber-suffix varian ('WG9525160004/2') sementara indeks kita
    # menyimpan PN dasarnya → rows_for_pns mencocokkan dengan pemaaf (kalau tidak,
    # part tampil 'stok —' padahal ADA).
    local = part_index.rows_for_pns(pns)

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
        "sumber": ("EPC Parts Atlas resmi per-VIN (+'sumber_baris' = pelengkap dari "
                   "Loading List) — bukan katalog per-model, bukan tebakan."),
        "catatan": ("posisi_poros dari Atlas PASTI (DEPAN=Driven axle 06, BELAKANG=Drive "
                    "axle 07); baris 'sumber_baris' posisinya TIDAK dipisah — sebut apa "
                    "adanya, JANGAN mengarang posisi. 'part_pengganti' = pengganti resmi "
                    "EPC. Baris 'di_dalam_assembly' = komponen di dalam assembly (yang "
                    "biasa dibeli saat servis) — JANGAN dihilangkan, kelompokkan di bawah "
                    "induknya. ⛔⛔ PN WAJIB DARI DAFTAR INI SAJA; bila EPC hanya punya "
                    "varian per-sisi/per-lembar, sebut apa adanya — JANGAN menggantinya "
                    "dengan PN 'assembly utuh' dari katalog/ingatan (bisa SALAH utk unit ini)."),
        "terpotong_walk": res.get("terpotong", False),
        **({"peringatan_tidak_lengkap":
            "⚠️ Penelusuran EPC belum tuntas (sebagian data gagal diambil/terpotong) — "
            "daftar ini bisa BELUM lengkap. Sebut PN yang ada, tapi JANGAN klaim 'cuma ini' "
            "atau 'tidak ada yang lain'; sarankan cek ulang sebentar."}
           if res.get("incomplete") else {}),
    }

    # OTOMATIS: kartu GAMBAR EXPLODED VIEW part utama (best-effort) — konsisten dgn
    # uraikan_mesin, supaya tiap cek part per-VIN Sinotruk juga langsung disertai
    # gambar. Kategori diturunkan dari domain modul Atlas (+ posisi utk poros);
    # multi-domain (mis. 'filter') dilewati agar tak walk kategori berat.
    _main = all_parts[0] if all_parts else None
    # CEK RELEVANSI sebelum auto-gambar: nama part utama HARUS memuat salah satu
    # kata yang dicari. Tanpa ini, query yang cuma nyerempet (mis. 'spring' kena
    # spring pin di gardan) menempelkan gambar figure yang TAK relevan dgn niat
    # user (kasus nyata: tanya pegas daun, gambar 'Drive device' gardan ikut).
    _relevan = False
    if _main:
        _hay = ((_main.get("nama") or "") + " " + (_main.get("nama_cn") or "")).lower()
        _relevan = any(k.lower() in _hay for k in kws if k)
    if _main and _relevan:
        # posisi → kategori 'gardan' HANYA utk domain poros sungguhan (is_axle).
        # Domain non-axle (pegas/suspensi dst) yang part-nya kebetulan dari walk
        # CDQ/QDQ tetap TANPA kategori → tak ada gambar gardan nyasar.
        _g, _db, _nf = _auto_exploded_gambar(
            rangka, _main["pn"], "sinotruk",
            _sino_exploded_kat(modules, _main.get("posisi") if is_axle else None))
    else:
        _g, _db, _nf = [], [], ""
    base["gambar"] = _g
    if _g:
        base["daftar_balon_gambar"] = _db
        base["nama_figure_gambar"] = _nf
        base["catatan_gambar"] = (
            f"GAMBAR exploded view part utama sudah OTOMATIS tampil (inline) di bawah jawabanmu "
            f"(figure '{_nf}'). 'daftar_balon_gambar' = SEMUA balon di gambar + part-nya; bila user "
            "lanjut tanya 'no N itu apa'/'cek baut no N', jawab dari daftar itu DAN panggil "
            "gambar_exploded(rangka, pn=<PN part utama>, kategori, balon=N) agar balon N disorot. "
            "Sebut gambarnya ada; JANGAN buat link/gambar sendiri.")

    # NON-POROS (mesin/kopling/gearbox): posisi tak relevan → daftar datar seperti biasa.
    if not is_axle:
        base["jumlah"] = len(all_parts)
        base["parts"] = [_row(p) for p in all_parts]
        base["peringatan_posisi"] = (
            "Part ini BUKAN di modul poros (mesin/kopling/gearbox) → tidak ada pemisahan "
            "depan/belakang. Sebut apa adanya.")
        if "FDJ" in modules:
            base["catatan_mesin_weichai"] = (
                "⚠️ Atlas Sinotruk berhenti di ENGINE ASSEMBLY. Untuk unit bermesin WEICHAI, "
                "komponen mesin & aksesori yang menempel di mesin (kompresor angin/air "
                "compressor, alternator, starter, turbocharger, pompa, piston, dll) TIDAK ada "
                "di Atlas — daftar di atas bisa hanya PIPA/BRACKET penghubungnya. Bila komponen "
                "yang DIMINTA user sendiri belum ada di daftar ini, WAJIB panggil "
                "uraikan_mesin(rangka, part) untuk mengambilnya dari EPC Weichai — JANGAN "
                "menyimpulkan 'terintegrasi di engine assembly' atau berhenti di sini.")
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
        "⚠️ KRITIS: 'parts_depan' (driven axle) ≠ 'parts_belakang' (drive axle) — "
        "depan & belakang BIASANYA BEDA PN. ATURAN MUTLAK: jawab posisi tertentu "
        "HANYA dari grup posisi itu; DILARANG menyalin PN grup lain / menjawab dari "
        "ingatan. Tanpa sebutan sisi → tampilkan keduanya. Bilang 'sama' HANYA bila "
        "PN muncul di kedua grup. 'parts_tanpa_posisi' = Loading List (posisi tak "
        "dipisah) — jangan diklaim milik satu sisi.")
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


def _uraikan_assembly_impl(args: dict, user: dict) -> dict:
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
        out = {"found": False, "frame_number": res.get("frame_number"), "error": msg,
               "_incomplete": bool(res.get("incomplete"))}
        # Bila assembly disebut via PN tapi tak beranak di VIN ini → sarankan
        # jalur LINTAS MODEL (turunan_assembly) yang mencari rincian di model lain.
        if pn and not res.get("incomplete"):
            out["saran_lintas_model"] = (
                f"Assembly PN {pn} mungkin hanya muncul UTUH di unit ini tanpa "
                "rincian. Coba tool turunan_assembly(pn='" + pn + "') untuk "
                "menelusuri turunannya dari model lain yang memuat breakdown-nya.")
        return out

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
        if c.get("pasok") == "stop":
            row["status_pasok"] = "STOP — tidak dipasok pabrik lagi (discontinued)"
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


def _t_turunan_assembly(args: dict, user: dict) -> dict:
    """TELUSURI TURUNAN (komponen) sebuah assembly PN dari MODEL MANA PUN yang
    punya rinciannya — jalur GLOBAL EPC. Untuk kasus: assembly hanya muncul
    sebagai leaf tanpa anak di pohon VIN target (uraikan_assembly per-VIN gagal),
    padahal model lain memuat breakdown-nya. Menyilang komponen ke stok/harga
    lokal + atribusi model sumbernya."""
    pn = (args.get("pn") or args.get("part_number") or args.get("assembly") or "").strip().upper()
    if not pn:
        return {"error": "Sebutkan PN assembly yang mau ditelusuri turunannya."}
    if not _PN_LIKE_RE.match(pn):
        return {"error": ("Tool ini butuh PN assembly (mis. WG9925477132), bukan nama. "
                          "Untuk cari per nama+VIN pakai uraikan_assembly.")}

    res = epc_bom.assembly_components_global(pn)
    err = res.get("_err")
    if err in ("token_expired", "no_token"):
        return {"found": False, "error": _EPC_TOKEN_MSG, "_token_issue": True}
    if err == "network":
        return {"found": False, "error": "Gagal menghubungi server EPC (jaringan). Coba lagi."}
    if err:
        return {"found": False, "error": "EPC tidak mengembalikan data untuk PN ini."}

    if not res.get("found"):
        n_model = res.get("jumlah_model_pemakai") or 0
        if n_model:
            return {"found": False, "part_number": pn,
                    "jumlah_model_pemakai": n_model,
                    "figure_dicoba": res.get("figure_dicoba"),
                    "error": (f"PN {pn} dipakai {n_model} baris/model, tapi pada "
                              f"{res.get('figure_dicoba')} figure teratas yang dicek "
                              "TIDAK ada breakdown komponen (assembly ini leaf di "
                              "semua figure itu — mungkin memang tak diurai EPC). "
                              "⛔ JANGAN mengarang isinya."),
                    "catatan": "Bisa jadi assembly ini tak punya rincian di EPC; sampaikan jujur."}
        return {"found": False, "part_number": pn,
                "error": f"PN {pn} tidak ditemukan di EPC (cek PN; hanya Sinotruk/HOWO/SITRAK)."}

    comps = res.get("komponen") or []
    pns = [c["pn"] for c in comps]
    local: dict[str, dict] = {}
    for r in part_index.rows_for_pns(pns).items():
        p, row = r
        local[p.upper()] = row
    boleh_harga = _boleh_harga(user)
    rows: list[dict] = []
    for c in comps:
        lr = local.get(c["pn"], {})
        row = {
            "part_number": c["pn"],
            "nama": " ".join((lr.get("part_name") or c.get("nama") or c.get("nama_cn") or "").split()),
            "nama_china": c.get("nama_cn") or None,
            "qty_di_assembly": c.get("qty"),
            "balon": c.get("balon"),
            "ada_di_inventori": bool(lr),
        }
        if c.get("pasok") == "stop":
            row["status_pasok"] = "STOP — tidak dipasok pabrik lagi (discontinued)"
        if c.get("pengganti"):
            row["part_pengganti"] = c["pengganti"]
        if lr:
            row["stok_total"] = lr.get("stok")
            if boleh_harga:
                row["harga_lokal"] = lr.get("harga")
            row["stok_per_gudang"] = lr.get("gudang") or {}
        rows.append(row)
    if _is_pembeli(user):
        for row in rows:
            row.pop("stok_per_gudang", None)

    return {
        "found": True,
        "part_number": pn,
        "assembly_nama": (res.get("assembly") or {}).get("nama"),
        "sumber_model": res.get("sumber_model"),
        "figure_pn": res.get("figure_pn"),
        "figure_nama": res.get("figure_nama"),
        "jumlah_model_pemakai": res.get("jumlah_model_pemakai"),
        "figure_unik_dicek": res.get("figure_dicoba"),
        "jumlah_komponen": len(rows),
        "komponen": rows,
        "sumber": ("EPC global — rincian assembly ini diambil dari MODEL LAIN yang "
                   "memuat breakdown-nya (VIN asal Anda mungkin hanya punya assembly "
                   "utuh tanpa turunan). Komponen disilang stok/harga lokal."),
        "catatan": ("Ini TURUNAN assembly di atas, ditemukan dari model "
                    f"'{res.get('sumber_model') or '?'}'. SEBUTKAN bahwa rincian ini "
                    "dari model lain (asumsi kompatibel karena PN assembly sama), "
                    "sarankan verifikasi bila kritis. Tampilkan PN + nama + qty + "
                    "balon + stok/harga. ⛔ JANGAN sebut PN assembly sbg komponen; "
                    "⛔ JANGAN mengarang PN di luar daftar."),
    }


def _uraikan_mesin_impl(args: dict, user: dict) -> dict:
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
    return _format_mesin_bom(res, part, user, rangka)


def _t_part_dari_mesin(args: dict, user: dict) -> dict:
    """CARI PART DI MESIN WEICHAI LANGSUNG DARI NOMOR MESIN (serial engine) — TANPA
    VIN/rangka. Untuk 'carikan starter untuk no engine 4P24B000713'. Tanpa 'part' →
    daftar GROUP mesin; dengan 'part' → komponen cocok + stok/harga lokal. Order
    di-resolve via getOrderNumber(serialNumber=<no>) memakai token account-level."""
    no_mesin = (args.get("no_mesin") or args.get("no_engine")
                or args.get("serial") or args.get("nomor_mesin") or "").strip()
    if not no_mesin:
        return {"error": "Sebutkan NOMOR MESIN (serial engine, mis. 4P24B000713)."}
    part = (args.get("part") or args.get("query") or "").strip()
    if part:
        terms, _syn = _expand_query(part)
        match_terms = [part] + [t for t in terms if t]
        ql = (part + " " + " ".join(terms)).lower()
        for dom, extra in _AUS_KEYWORDS.items():
            if dom in ql:
                match_terms += extra
        res = epc_weichai.find_parts_by_no(no_mesin, match_terms)
    else:
        res = epc_weichai.engine_bom_by_no(no_mesin)
    return _format_mesin_bom(res, part, user, "")


_MAX_MASSAL_MESIN = 60
_ANCILLARY_MESIN = ("pipe", "hose", "bracket", "clamp", "bolt", "washer", "gasket",
                    "tube", "joint", "connector", "支架", "管", "screw", "nut")


def _parse_daftar_mesin(v) -> list[str]:
    """daftar nomor mesin (list ATAU string dipisah baris/koma/spasi/;) → uppercase,
    dedup, urut asli. Serial engine biasanya alfanumerik ≥6 char."""
    if isinstance(v, (list, tuple)):
        toks = [str(x) for x in v]
    else:
        toks = re.split(r"[\s,;]+", str(v or ""))
    out: list[str] = []
    for t in toks:
        p = t.strip().upper()
        if len(p) >= 6 and p not in out:
            out.append(p)
    return out


def _pick_main_hit(hits: list[dict], part: str) -> dict | None:
    """Dari hits satu mesin, pilih komponen UTAMA (bukan pipa/bracket penyerta)."""
    if not hits:
        return None
    pl = part.lower()

    def _rank(h):
        nm = (h.get("nama") or "").lower()
        anc = any(w in nm for w in _ANCILLARY_MESIN)
        phrase = 0 if nm.startswith(pl) else (1 if pl in nm else 2)
        return (anc, phrase, len(nm))
    return sorted(hits, key=_rank)[0]


def _t_cek_massal_part_mesin(args: dict, user: dict) -> dict:
    """CEK SATU PART (mis. 'starter') di BANYAK NOMOR MESIN Weichai sekaligus (1
    panggilan) — versi massal part_dari_mesin. Efisien: mesin ber-konfigurasi sama
    diproses sekali. Deteksi PENGGANTI (supersession) → rekomendasi PN order terkini;
    silang stok/harga lokal; opsi Excel."""
    nos = _parse_daftar_mesin(args.get("daftar_no_mesin") or args.get("no_mesin")
                              or args.get("daftar") or args.get("engines"))
    part = (args.get("part") or args.get("query") or "").strip()
    if not nos:
        return {"error": "Sebutkan daftar NOMOR MESIN (pisah baris/koma)."}
    if not part:
        return {"error": "Sebutkan PART yang dicek (mis. 'starter', 'alternator', 'filter oli')."}
    dipotong = len(nos) > _MAX_MASSAL_MESIN
    nos = nos[:_MAX_MASSAL_MESIN]

    terms, _syn = _expand_query(part)
    match_terms = [part] + [t for t in terms if t]
    ql = (part + " " + " ".join(terms)).lower()
    for dom, extra in _AUS_KEYWORDS.items():
        if dom in ql:
            match_terms += extra

    res = epc_weichai.find_part_massal(nos, match_terms)
    if res.get("_err") == "no_session":
        return {"found": False, "error": ("Sesi EPC Weichai belum aktif. Coba lagi sebentar "
                                          "(token sedang disiapkan), atau cek satu unit bermesin "
                                          "Weichai dulu.")}
    per = res.get("per_engine") or {}

    # PN utama per mesin + kumpulan PN unik utama.
    main_pn: dict[str, dict] = {}
    pn_utama_unik: set[str] = set()
    for no in nos:
        e = per.get(no) or {}
        if e.get("found"):
            mh = _pick_main_hit(e.get("hits") or [], part)
            if mh:
                main_pn[no] = mh
                pn_utama_unik.add(mh["pn"])

    # SUPERSESSION: utk tiap PN utama unik, cari pengganti TERBARU (rekomendasi order).
    order_pn: dict[str, str] = {}   # pn_epc -> pn_order (terkini)
    for pn in pn_utama_unik:
        try:
            rp = epc_weichai.replace_part(pn)
            cand = rp.get("digantikan_oleh") or []
            # ambil yg tanggal terbaru bila ada; kalau tidak, PN itu sendiri.
            if cand:
                cand_sorted = sorted(cand, key=lambda x: str(x.get("tanggal") or ""), reverse=True)
                order_pn[pn] = cand_sorted[0].get("pn") or pn
            else:
                order_pn[pn] = pn
        except Exception:
            order_pn[pn] = pn

    # Silang stok/harga lokal utk PN utama + PN order.
    boleh_harga = _boleh_harga(user)
    all_pns = list(pn_utama_unik | set(order_pn.values()))
    local = part_index.rows_for_pns(all_pns) if all_pns else {}
    local = {k.upper(): v for k, v in local.items()}

    def _stok(pn):
        return (local.get(pn.upper()) or {}).get("stok")

    def _harga(pn):
        return (local.get(pn.upper()) or {}).get("harga") if boleh_harga else None

    hasil: list[dict] = []
    tak_ada: list[str] = []
    for no in nos:
        e = per.get(no) or {}
        if not e.get("found"):
            tak_ada.append(no)
            hasil.append({"no_mesin": no, "found": False,
                          "catatan": ("nomor mesin tak ada di EPC Weichai" if e.get("reason") == "no_order"
                                      else f"tidak ada '{part}' di BOM mesin ini")})
            continue
        mh = main_pn.get(no) or {}
        pn = mh.get("pn")
        opn = order_pn.get(pn, pn)
        row = {"no_mesin": no, "found": True, "model": e.get("model"),
               "part": mh.get("nama") or part, "pn_epc": pn,
               "group": mh.get("group")}
        if opn and opn != pn:
            row["pn_order_terkini"] = opn
            row["catatan_pn"] = f"PN {pn} sudah digantikan {opn} — pesan pakai {opn}."
        row["stok_lokal"] = _stok(opn or pn)
        if boleh_harga:
            row["harga_lokal"] = _harga(opn or pn)
        hasil.append(row)

    ketemu = len(nos) - len(tak_ada)
    out: dict = {
        "found": ketemu > 0, "part_dicari": part, "jumlah_mesin": len(nos),
        "ketemu": ketemu, "tak_ada": len(tak_ada),
        "konfigurasi_unik": res.get("orders_unik"),
        "pn_starter_unik": sorted(pn_utama_unik),
        "hasil": hasil,
    }

    if args.get("excel"):
        kolom = ["No", "Nomor Mesin", "Model", "Part", "PN (EPC)", "PN Order Terkini"]
        if boleh_harga:
            kolom += ["Harga"]
        kolom += ["Stok Lokal", "Keterangan"]
        baris: list[list] = []
        for i, r in enumerate(hasil, start=1):
            if r.get("found"):
                base = [str(i), r["no_mesin"], r.get("model") or "", r.get("part") or "",
                        r.get("pn_epc") or "", r.get("pn_order_terkini") or r.get("pn_epc") or ""]
                if boleh_harga:
                    base += [r.get("harga_lokal") if r.get("harga_lokal") is not None else "—"]
                base += [ai_export.ke_angka(r.get("stok_lokal")) if r.get("stok_lokal") not in (None, "") else "—",
                         r.get("catatan_pn") or ""]
            else:
                base = [str(i), r["no_mesin"], "", part, "", ""]
                if boleh_harga:
                    base += ["—"]
                base += ["—", r.get("catatan") or "tidak ditemukan"]
            baris.append(base)
        export_id, filename = ai_export.stash_export(
            f"Cek {part} — {len(nos)} nomor mesin", kolom, baris)
        out["export_id"] = export_id
        out["filename"] = filename
        out["jumlah_baris"] = len(baris)

    catatan = (f"Cek '{part}' di {len(nos)} nomor mesin dalam SATU panggilan. {ketemu} "
               f"ketemu, {len(tak_ada)} tidak. {res.get('orders_unik')} konfigurasi unik. "
               "Bila SATU part muncul dalam beberapa PN (mis. karena supersession), "
               "'pn_order_terkini' = PN resmi terbaru untuk dipesan — UTAMAKAN itu. "
               "Sebut jujur mesin yang tak ketemu. ⛔ JANGAN mengarang PN.")
    if dipotong:
        catatan = f"⚠️ Daftar dipotong ke {_MAX_MASSAL_MESIN} nomor pertama. " + catatan
    if out.get("export_id"):
        catatan += " File Excel siap — kartu unduh muncul OTOMATIS; JANGAN tulis ulang tabel."
    out["catatan"] = catatan
    return out


def _t_cek_massal_part_rangka(args: dict, user: dict) -> dict:
    """CEK SATU PART (mis. 'injector') di BANYAK NOMOR RANGKA sekaligus (1 panggilan)
    — jalur EPC Sinotruk Atlas per-VIN. Pasangan cek_massal_part_mesin (yang untuk
    NOMOR MESIN Weichai); ini untuk NOMOR RANGKA (unit Sinotruk/HOWO, mesin MC dll).
    Efisien: unit ber-konfigurasi (order) sama diproses sekali. Deteksi pengganti
    (SIMS) → pn_order_terkini; silang stok/harga lokal; opsi Excel."""
    rgs = _parse_daftar_mesin(args.get("daftar_rangka") or args.get("rangka")
                              or args.get("daftar") or args.get("vins"))
    part = (args.get("part") or args.get("query") or "").strip()
    if not rgs:
        return {"error": "Sebutkan daftar NOMOR RANGKA (VIN, pisah baris/koma)."}
    if not part:
        return {"error": "Sebutkan PART yang dicek (mis. 'injector', 'kampas rem', 'filter oli')."}
    dipotong = len(rgs) > _MAX_MASSAL_MESIN
    rgs = rgs[:_MAX_MASSAL_MESIN]

    # istilah lapangan ID → nama katalog EN (EPC hanya paham EN/CN) — WAJIB.
    terms, _syn = _expand_query(part)
    kws = [t for t in dict.fromkeys([part] + [t for t in terms if t]) if t and len(t) >= 3]

    res = epc_bom.find_part_massal_rangka(rgs, kws)
    per = res.get("per_rangka") or {}
    if not per:
        return {"found": False, "error": "Tidak ada rangka valid untuk dicek."}
    # token EPC bermasalah utk SEMUA → jujur.
    if all((v.get("_err") in ("token_expired", "no_token")) for v in per.values()):
        return {"found": False, "error": _EPC_TOKEN_MSG, "_token_issue": True}

    main_pn: dict[str, dict] = {}
    pn_unik: set[str] = set()
    for r in rgs:
        e = per.get(r) or {}
        if e.get("found"):
            mh = _pick_main_hit(e.get("hits") or [], part)
            if mh:
                main_pn[r] = mh
                pn_unik.add(mh["pn"])

    # SUPERSESSION (SIMS partEquivalentQuery, part Sinotruk) → PN order terkini.
    order_pn: dict[str, str] = {}
    for pn in pn_unik:
        try:
            eq = sims.equivalents_for(pn) or {}
            cand = [x.get("pn") for x in (eq.get("digantikan_oleh") or []) if x.get("pn")]
            order_pn[pn] = cand[0] if cand else pn
        except Exception:
            order_pn[pn] = pn

    boleh_harga = _boleh_harga(user)
    all_pns = list(pn_unik | set(order_pn.values()))
    local = {k.upper(): v for k, v in (part_index.rows_for_pns(all_pns) if all_pns else {}).items()}

    hasil: list[dict] = []
    tak_ada: list[str] = []
    for r in rgs:
        e = per.get(r) or {}
        if not e.get("found"):
            tak_ada.append(r)
            why = ("nomor rangka tak ditemukan di EPC" if e.get("_err") in ("not_found", "input")
                   else f"tidak ada '{part}' di katalog unit ini")
            hasil.append({"rangka": r, "found": False, "catatan": why})
            continue
        mh = main_pn.get(r) or {}
        pn = mh.get("pn")
        opn = order_pn.get(pn, pn)
        lr = local.get((opn or pn or "").upper(), {})
        row = {"rangka": r, "found": True, "part": mh.get("nama") or part, "pn_epc": pn}
        if opn and opn != pn:
            row["pn_order_terkini"] = opn
            row["catatan_pn"] = f"PN {pn} punya pengganti {opn} — utamakan {opn}."
        row["stok_lokal"] = lr.get("stok")
        if boleh_harga:
            row["harga_lokal"] = lr.get("harga")
        hasil.append(row)

    ketemu = len(rgs) - len(tak_ada)
    out: dict = {
        "found": ketemu > 0, "part_dicari": part, "jumlah_rangka": len(rgs),
        "ketemu": ketemu, "tak_ada": len(tak_ada),
        "konfigurasi_unik": res.get("order_unik"),
        "pn_unik": sorted(pn_unik), "hasil": hasil,
    }

    if args.get("excel"):
        kolom = ["No", "Nomor Rangka", "Part", "PN (EPC)", "PN Order Terkini"]
        if boleh_harga:
            kolom += ["Harga"]
        kolom += ["Stok Lokal", "Keterangan"]
        baris: list[list] = []
        for i, r in enumerate(hasil, start=1):
            if r.get("found"):
                base = [str(i), r["rangka"], r.get("part") or "", r.get("pn_epc") or "",
                        r.get("pn_order_terkini") or r.get("pn_epc") or ""]
                if boleh_harga:
                    base += [r.get("harga_lokal") if r.get("harga_lokal") not in (None, "") else "—"]
                base += [ai_export.ke_angka(r.get("stok_lokal")) if r.get("stok_lokal") not in (None, "") else "—",
                         r.get("catatan_pn") or ""]
            else:
                base = [str(i), r["rangka"], part, "", ""]
                if boleh_harga:
                    base += ["—"]
                base += ["—", r.get("catatan") or "tidak ditemukan"]
            baris.append(base)
        export_id, filename = ai_export.stash_export(
            f"Cek {part} — {len(rgs)} rangka", kolom, baris)
        out["export_id"] = export_id
        out["filename"] = filename
        out["jumlah_baris"] = len(baris)

    catatan = (f"Cek '{part}' di {len(rgs)} nomor rangka dalam SATU panggilan (⛔ JANGAN "
               f"cari_part_di_unit berulang). {ketemu} ketemu, {len(tak_ada)} tidak. "
               f"{res.get('order_unik')} konfigurasi unik. 'pn_order_terkini' = PN "
               "pengganti resmi bila ada — utamakan itu utk order. Sebut jujur yang tak "
               "ketemu. ⛔ JANGAN mengarang PN.")
    if dipotong:
        catatan = f"⚠️ Daftar dipotong ke {_MAX_MASSAL_MESIN} rangka pertama. " + catatan
    if out.get("export_id"):
        catatan += " File Excel siap — kartu unduh muncul OTOMATIS; JANGAN tulis ulang tabel."
    out["catatan"] = catatan
    return out


# ── Spesifikasi/konfigurasi unit dari EPC getVehicleConfig (2026-07-23) ──
# Field terpilih (dari 56 field respons) + label Indonesia. Urutan = urutan sajian.
_CFG_FIELDS = [
    ("modelCode", "model"), ("seriesName", "seri"), ("driveMode", "gerak"),
    ("useType", "jenis"), ("discharge", "emisi"), ("breakMode", "rem"),
    ("engineModelCode", "mesin"), ("gearboxModelCode", "gearbox"),
    ("axleFrontModelCode", "gardan_depan"), ("axleMidModelCode", "gardan_tengah"),
    ("axleRearModelCode", "gardan_belakang"), ("cabModelCode", "kabin"),
    ("wheelBase", "jarak_sumbu"), ("tyreSpec", "ban"), ("springType", "pegas"),
]
_CFG_TR = {"载货车": "Cargo", "牵引车": "Tractor (kepala)", "自卸车": "Dump",
           "搅拌车": "Mixer", "欧V": "Euro V", "欧IV": "Euro IV", "欧III": "Euro III",
           "欧II": "Euro II", "发动机": "", "变速箱": "", "自调臂": " self-adjusting arm",
           "前轴": " front axle", "驱动桥": " drive axle", "中桥": " middle axle",
           "后桥": " rear axle", "(鼓)": " (drum)", "(盘)": " (disc)",
           "法兰式取力器": " PTO (power take-off)"}


def _tr_cfg(v) -> str:
    s = " ".join(str(v or "").split())
    for k, e in _CFG_TR.items():
        s = s.replace(k, e)
    if s == "normal":            # breakMode 'normal' = tanpa ABS
        s = "tanpa ABS"
    return " ".join(s.split())


def _configs_rangka(rangkas: list[str]) -> dict:
    """Ambil konfigurasi EPC (getVehicleConfig) BANYAK rangka paralel →
    {rangka: {label: nilai}} ({} bila rangka tak dikenal EPC)."""
    from concurrent.futures import ThreadPoolExecutor

    def _one(r):
        d = epc.get_config(epc_bom._frame(r)) or {}
        return r, {lbl: _tr_cfg(d.get(f)) for f, lbl in _CFG_FIELDS
                   if str(d.get(f) or "").strip()}
    out: dict = {}
    with ThreadPoolExecutor(max_workers=6) as ex:
        for r, cfg in ex.map(_one, rangkas):
            out[r] = cfg
    return out


def _t_spek_massal_rangka(args: dict, user: dict) -> dict:
    """SPESIFIKASI BANYAK UNIT sekaligus dari daftar NOMOR RANGKA — model, gerak
    (4×2/6×4), jenis, emisi, rem/ABS, mesin, gearbox, gardan, dst (EPC resmi
    getVehicleConfig). Untuk 'apa spek unit-unit ini' / data armada. Opsi Excel."""
    rgs = _parse_daftar_mesin(args.get("daftar_rangka") or args.get("rangka")
                              or args.get("daftar") or args.get("vins"))
    if not rgs:
        return {"error": "Sebutkan daftar NOMOR RANGKA (VIN, pisah baris/koma)."}
    dipotong = len(rgs) > _MAX_MASSAL_MESIN
    rgs = rgs[:_MAX_MASSAL_MESIN]
    cfgs = _configs_rangka(rgs)

    hasil: list[dict] = []
    tak_ada: list[str] = []
    for r in rgs:
        cfg = cfgs.get(r) or {}
        if not cfg:
            tak_ada.append(r)
            hasil.append({"rangka": r, "found": False,
                          "catatan": "rangka tak dikenal EPC (cek nomornya)"})
        else:
            hasil.append({"rangka": r, "found": True, **cfg})

    ketemu = len(rgs) - len(tak_ada)
    labels = [lbl for _f, lbl in _CFG_FIELDS
              if any(lbl in (cfgs.get(r) or {}) for r in rgs)]
    out: dict = {"found": ketemu > 0, "jumlah_rangka": len(rgs), "ketemu": ketemu,
                 "tak_ada": len(tak_ada), "hasil": hasil}

    if args.get("excel"):
        kolom = ["No", "Nomor Rangka"] + [l.replace("_", " ").title() for l in labels]
        baris = []
        for i, h in enumerate(hasil, start=1):
            baris.append([str(i), h["rangka"]]
                         + [(h.get(l) or ("—" if h.get("found") else "tak dikenal EPC"))
                            for l in labels])
        export_id, filename = ai_export.stash_export(
            f"Spesifikasi {len(rgs)} unit", kolom, baris)
        out["export_id"], out["filename"] = export_id, filename
        out["jumlah_baris"] = len(baris)

    catatan = (f"Spesifikasi {len(rgs)} unit dari EPC resmi (getVehicleConfig). "
               "Sajikan ringkas per unit / kelompokkan yang sama. ⚠️ Ini KONFIGURASI "
               "(spek), BUKAN daftar part — utk banding PART pakai banding_rangka_massal; "
               "spek sama ≠ part pasti sama. ⛔ JANGAN mengarang nilai.")
    if dipotong:
        catatan = f"⚠️ Daftar dipotong ke {_MAX_MASSAL_MESIN} rangka pertama. " + catatan
    if out.get("export_id"):
        catatan += " File Excel siap — kartu unduh muncul OTOMATIS."
    out["catatan"] = catatan
    return out


def _t_banding_konfigurasi_rangka(args: dict, user: dict) -> dict:
    """BANDINGKAN KONFIGURASI/SPESIFIKASI (bukan part) BANYAK unit via daftar
    NOMOR RANGKA: field mana SAMA di semua unit, mana BERBEDA, dan unit
    terkelompok per konfigurasi identik. Pelengkap banding_rangka_massal (yang
    membandingkan SET PART). Opsi Excel."""
    rgs = _parse_daftar_mesin(args.get("daftar_rangka") or args.get("rangka_list")
                              or args.get("rangka") or args.get("daftar"))
    if len(rgs) < 2:
        return {"error": "Perlu MINIMAL 2 nomor rangka untuk dibandingkan."}
    dipotong = len(rgs) > _MAX_MASSAL_MESIN
    rgs = rgs[:_MAX_MASSAL_MESIN]
    cfgs = _configs_rangka(rgs)
    valid = {r: c for r, c in cfgs.items() if c}
    gagal = [r for r in rgs if not cfgs.get(r)]
    if len(valid) < 2:
        return {"found": False,
                "error": ("Kurang dari 2 rangka yang dikenal EPC — tak bisa "
                          f"dibandingkan. Tak dikenal: {', '.join(gagal) or '-'}")}

    labels = [lbl for _f, lbl in _CFG_FIELDS
              if any(lbl in c for c in valid.values())]
    sama: dict = {}
    beda: list[str] = []
    for l in labels:
        vals = {c.get(l) or "" for c in valid.values()}
        if len(vals) == 1:
            sama[l] = next(iter(vals))
        else:
            beda.append(l)

    # kelompokkan unit ber-konfigurasi identik (atas field yang BERBEDA)
    kel_map: dict = {}
    for r in rgs:
        c = valid.get(r)
        if not c:
            continue
        key = tuple(c.get(l) or "" for l in beda)
        kel_map.setdefault(key, []).append(r)
    kelompok = []
    for i, (key, units) in enumerate(
            sorted(kel_map.items(), key=lambda kv: -len(kv[1]))):
        kelompok.append({"kelompok": chr(65 + i), "jumlah_unit": len(units),
                         "unit": units,
                         "konfigurasi": {l: v for l, v in zip(beda, key) if v}})
    kel_of = {u: k["kelompok"] for k in kelompok for u in k["unit"]}

    out: dict = {
        "found": True, "jumlah_rangka": len(rgs),
        "dikenal_epc": len(valid), "tak_dikenal": gagal,
        "spek_sama_semua_unit": sama,
        "field_berbeda": beda,
        "jumlah_kelompok": len(kelompok),
        "kelompok": kelompok,
    }

    if args.get("excel"):
        kolom = ["No", "Nomor Rangka", "Kelompok"] + \
                [l.replace("_", " ").title() for l in beda]
        baris = []
        for i, r in enumerate(rgs, start=1):
            c = valid.get(r) or {}
            baris.append([str(i), r, kel_of.get(r, "—")]
                         + [(c.get(l) or ("—" if c else "tak dikenal EPC"))
                            for l in beda])
        baris.append(["", "", "", *([""] * len(beda))])
        for l, v in sama.items():
            baris.append(["", "SAMA di semua unit:", l.replace("_", " ").title(),
                          v, *([""] * max(0, len(beda) - 1))])
        export_id, filename = ai_export.stash_export(
            f"Banding konfigurasi {len(rgs)} unit", kolom, baris)
        out["export_id"], out["filename"] = export_id, filename
        out["jumlah_baris"] = len(baris)

    catatan = (f"Banding KONFIGURASI {len(valid)} unit: {len(sama)} field SAMA, "
               f"{len(beda)} BERBEDA → {len(kelompok)} kelompok konfigurasi. "
               "Sajikan: yang sama dulu (ringkas), lalu perbedaan per kelompok "
               "(sebut unit anggotanya). ⚠️ Ini SPESIFIKASI — spek sama ≠ part "
               "pasti sama; utk kepastian PART pakai banding_rangka_massal. "
               "⛔ JANGAN mengarang nilai.")
    if dipotong:
        catatan = f"⚠️ Daftar dipotong ke {_MAX_MASSAL_MESIN} rangka pertama. " + catatan
    if out.get("export_id"):
        catatan += " File Excel siap — kartu unduh muncul OTOMATIS."
    out["catatan"] = catatan
    return out


def _format_mesin_bom(res: dict, part: str, user: dict, rangka: str) -> dict:
    """Bentuk hasil BOM mesin Weichai (dipakai jalur per-VIN & per-NOMOR-MESIN):
    daftar group (tanpa part) atau komponen cocok + silang stok/harga (dgn part)."""
    if not res.get("found"):
        reason = res.get("reason")
        if reason in ("no_link", "no_engine", "no_order"):
            return {"found": False,
                    "error": (res.get("message") or "Unit ini bukan bermesin Weichai / tak ada data mesin di EPC Weichai.")
                             + " (Fitur ini hanya untuk unit Sinotruk yang mesinnya Weichai, mis. WP-series.)"}
        return {"found": False, "error": res.get("message") or "Gagal mengambil BOM mesin Weichai. Coba lagi."}

    eng = res.get("engine") or {}
    engine_info = {"model_mesin": eng.get("nama"),
                   "nomor_mesin": eng.get("nomor_mesin") or eng.get("model"),
                   "order": eng.get("order")}
    if eng.get("model") and eng.get("model") != eng.get("nomor_mesin"):
        engine_info["kode_model"] = eng.get("model")   # mis. WP4G130E22 (jalur by-no)

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

    # Urutkan: KOMPONEN UTAMANYA dulu, baris penyerta (pipa/selang/bracket/gear-nya)
    # belakangan. Kasus nyata: cari 'air compressor' → model menonjolkan 'Compressor
    # Air-outlet Assembly' (bagian intercooler) sbg kompresornya & melewatkan
    # 'Air Compressor Assembly'. Heuristik: nama diawali frasa dicari > memuat frasa
    # > sisanya; kata penyerta (pipe/hose/bracket/…) selalu turun.
    _ANCILLARY = ("pipe", "hose", "bracket", "clamp", "bolt", "washer", "gasket",
                  "tube", "joint", "connector", "支架", "管")
    pl = part.lower()

    def _rank(r: dict) -> tuple:
        nm = (r.get("nama") or "").lower()
        ancillary = any(w in nm for w in _ANCILLARY)
        if nm.startswith(pl):
            phrase = 0
        elif pl in nm:
            phrase = 1
        else:
            phrase = 2
        return (ancillary, phrase, len(nm))

    rows.sort(key=_rank)

    # OTOMATIS: sertakan kartu GAMBAR EXPLODED VIEW untuk komponen UTAMA (baris
    # teratas) — supaya tiap 'cek part mesin' langsung disertai gambar di bawah
    # jawaban (permintaan pemilik). Dipersempit dgn istilah 'part' (cepat).
    # daftar_balon = konteks balon→part figure agar asisten paham follow-up 'cek no N'.
    gambar, daftar_balon, nama_figure_utama = _auto_exploded_gambar(
        rangka, rows[0]["part_number"], "weichai", part)

    note = (f"Daftar sudah DIURUTKAN: baris teratas = komponen '{part}' itu SENDIRI "
            "(assembly/unit utuhnya); baris berisi pipe/hose/bracket/gear = part PENYERTA. "
            "Saat menjawab, SEBUT komponen utamanya DULU dengan PN-nya — ⛔ JANGAN "
            "menyebut pipa/bracket/penyerta sebagai komponen utamanya. Tampilkan SEMUA "
            "baris (utama + penyerta) dengan PN + nama + group + stok/harga. "
            "⛔ JANGAN mengarang PN/stok/harga.")
    if gambar:
        note += (f" GAMBAR exploded view komponen utama SUDAH otomatis tampil (inline) di bawah "
                 f"jawabanmu (figure '{nama_figure_utama}'). 'daftar_balon_gambar' berisi SEMUA "
                 "nomor balon di gambar itu + part-nya — INGAT ini: bila user lanjut bertanya 'no N "
                 "itu apa' / 'cek baut no N', jawab dari daftar itu (balon→part) DAN panggil "
                 "gambar_exploded_mesin(rangka, pn=<PN komponen utama ini>, balon=N) agar balon N "
                 "disorot di gambar. Cukup sebut gambarnya ada; JANGAN buat link/gambar sendiri.")
    return {
        "found": True, "mesin": engine_info, "dicari": part, "pn": (rows[0]["part_number"] if rows else None),
        "jumlah_cocok": len(rows), "komponen": rows, "gambar": gambar,
        "daftar_balon_gambar": daftar_balon,
        "nama_figure_gambar": nama_figure_utama,
        "sumber": ("EPC Weichai resmi — komponen internal mesin PERSIS unit ini (disilang stok/harga "
                   "katalog lokal). Sistem terpisah dari EPC Sinotruk."),
        "catatan": note,
    }


def _t_pengganti_part(args: dict, user: dict) -> dict:
    """PERSAMAAN/PENGGANTI (supersession) part — 'PN lama X diganti PN baru Y'. DUA
    sumber resmi digabung: SIMS partEquivalentQuery (Sinotruk/HOWO SASIS, tabel 17k
    baris, global by PN) + EPC Weichai 替换/ECN (part MESIN). Silang PN pengganti ke
    stok/harga lokal supaya tahu mana yang ready."""
    pn = (args.get("part_number") or args.get("pn") or "").strip()
    if not pn:
        return {"error": "Sebutkan Part Number yang mau dicek penggantinya."}
    rangka = (args.get("rangka") or "").strip()

    diganti: list[dict] = []   # PN pengganti (part baru)
    lama: list[dict] = []      # PN lama yang digantikan
    seen_d: set[str] = set()
    seen_m: set[str] = set()

    def _add(dst: list, seen: set, pn_: str, nama=None, **extra) -> None:
        k = "".join((pn_ or "").upper().split())
        if not pn_ or k in seen:
            return
        seen.add(k)
        dst.append({"pn": pn_, "nama": nama, **extra})

    # 1) SIMS (Sinotruk/HOWO sasis) — INDEKS in-memory dulu (instan, tabel penuh
    #    17rb baris, pemaaf PN dasar); query live per-PN hanya FALLBACK saat
    #    indeks belum siap. (Fix produksi 2026-07-17: query live rentan
    #    timeout/sesi kedaluwarsa → tool tercatat gagal 45% giliran.)
    try:
        if sims.equivalents_count() > 0:
            sres = sims.equivalents_for(pn) or {}
        else:
            sres = sims.get_part_equivalents(pn)
    except Exception:
        sres = {}
    for x in (sres.get("digantikan_oleh") or []):
        _add(diganti, seen_d, x.get("pn"), x.get("nama"), sumber="SIMS")
    for x in (sres.get("menggantikan") or []):
        _add(lama, seen_m, x.get("pn"), x.get("nama"), sumber="SIMS")

    # 2) EPC Weichai (part MESIN) — data 替换/ECN.
    try:
        wres = epc_weichai.replace_part(pn, rangka)
    except Exception:
        wres = {}
    if wres.get("found"):
        for x in (wres.get("digantikan_oleh") or []):
            _add(diganti, seen_d, x.get("pn"), None, tanggal=x.get("tanggal"), tipe=x.get("tipe"), sumber="Weichai")
        for x in (wres.get("menggantikan") or []):
            _add(lama, seen_m, x.get("pn"), None, tanggal=x.get("tanggal"), tipe=x.get("tipe"), sumber="Weichai")

    if not diganti and not lama:
        try:
            search_log.record_miss(pn, "pn", "pengganti_part")
        except Exception:
            pass
        out = {"found": False, "part_number": pn,
               "error": "Tidak ada data persamaan/pengganti untuk PN ini (dicek SIMS Sinotruk & EPC Weichai)."}
        # Tetap beri info PN yang DITANYA (katalog/stok) supaya jawaban berguna:
        # "tak ada supersession" ≠ "part tak dikenal" — sering PN-nya masih aktif.
        try:
            lr = part_index.rows_for_pns([pn]).get(pn.upper())
            if lr:
                out["info_part_ditanya"] = {
                    "nama": " ".join((lr.get("part_name") or "").split()) or None,
                    "stok_total": lr.get("stok"), "harga_lokal": lr.get("harga"),
                    "catatan": ("PN ini ADA di katalog/stok lokal — kemungkinan masih "
                                "aktif dipakai (tidak ada catatan penggantian resmi)."),
                }
        except Exception:
            pass
        return out

    # Silang PN pengganti/lama ke katalog lokal — PEMAAF varian suffix/pemisah
    # (dulu search_exact_pns → pengganti ready bisa tercap 'belum ada di katalog').
    all_pn = [x["pn"] for x in diganti] + [x["pn"] for x in lama]
    local = part_index.rows_for_pns(all_pn)

    def _acc_stok(pn_: str) -> dict:
        """Stok/harga langsung dari indeks Accurate (index_key pemaaf) — utk
        pengganti yang tak ada di katalog Excel tapi DISTOK gudang."""
        if not accurate.available():
            return {}
        try:
            acc = accurate.stock_full(pn_)
        except accurate.AccurateError:
            return {}
        if not acc:
            return {}
        out = {"stok_total": f"{acc['available_to_sell']:.0f} {acc.get('unit') or ''}".strip()}
        if acc.get("price"):
            out["harga_lokal"] = "Rp " + f"{int(acc['price']):,}".replace(",", ".")
        return out

    def _row(x: dict) -> dict:
        lr = local.get((x["pn"] or "").upper(), {})
        row = {"part_number": x["pn"],
               "nama": x.get("nama") or " ".join((lr.get("part_name") or "").split()) or None,
               "sumber": x.get("sumber")}
        if x.get("tanggal"):
            row["tanggal"] = x["tanggal"]
        if x.get("tipe"):
            row["tipe"] = x["tipe"]
        if lr:
            row["stok_total"] = lr.get("stok")
            row["harga_lokal"] = lr.get("harga")
            row["ada_di_katalog"] = True
        else:
            acc = _acc_stok(x["pn"] or "")
            if acc:
                row.update(acc)
                row["sumber_stok"] = "Accurate (sinkron berkala)"
            else:
                row["catatan"] = "belum ada di katalog/stok lokal"
        return row

    return {
        "found": True, "part_number": pn,
        "digantikan_oleh": [_row(x) for x in diganti],
        "menggantikan": [_row(x) for x in lama],
        "sumber": sorted({x.get("sumber") for x in (diganti + lama) if x.get("sumber")}),
        "catatan": ("'digantikan_oleh' = PN PENGGANTI (part baru) — sarankan ini bila PN yang "
                    "ditanya diskontinu/kosong stok; cek 'stok_total' mana yang ready. "
                    "'menggantikan' = PN LAMA yang digantikan part ini. 'sumber' SIMS = data "
                    "resmi Sinotruk/HOWO (sasis); Weichai = part mesin. ⛔ JANGAN mengarang PN — "
                    "hanya yang ADA di hasil ini."),
    }


def _repair_kit_mesin_impl(args: dict, user: dict) -> dict:
    """REPAIR KIT (维修包) mesin Weichai per-VIN — paket komponen servis/overhaul mesin,
    disilang stok/harga lokal."""
    rangka = (args.get("rangka") or "").strip()
    if not rangka:
        return {"error": "Sebutkan nomor rangka/VIN unit-nya."}
    res = epc_weichai.repair_kit(rangka)
    if not res.get("found"):
        reason = res.get("reason")
        if reason == "no_kit":
            # Mesin Weichai valid tapi pabrik tak mendefinisikan 维修包 utk mesin ini —
            # jangan buntu: komponennya tetap bisa diuraikan per bagian.
            return {"found": False, "error": res.get("message") or
                    "Mesin unit ini tidak punya repair kit terdefinisi di EPC Weichai.",
                    "saran": "Sampaikan apa adanya, lalu TAWARKAN menguraikan mesin per "
                             "bagian via tool uraikan_mesin (rangka sama) — mis. piston/"
                             "ring, liner, cylinder head, gasket — agar user tetap dapat "
                             "daftar komponen servisnya."}
        if reason in ("no_link", "no_engine", "no_order"):
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


_EXCEL_MAX_ROWS = 1000


# ═══════════════════════════════════════════════════════════════════════
#  TELEMATICS / GPS ARMADA (Sinotruk Fleet Service) — 2026-07-22
#  ⛔ ADMIN-ONLY (bukan key Menu Control — sesuai permintaan pemilik, tak bisa
#  didelegasikan). Data GPS real-time, BUKAN spesifikasi EPC / populasi.
#  ganti_nama_unit = satu-satunya operasi TULIS: wajib konfirmasi 2 langkah.
# ═══════════════════════════════════════════════════════════════════════
_TELE_DENIED = {"error": "Fitur pelacakan armada hanya untuk admin."}
_TELE_OFF = {"error": "Koneksi telematics belum dikonfigurasi di server "
                      "(kredensial TELEMATICS_* belum diisi)."}
_TELE_MAX_TABEL = 60   # baris unit yang disajikan ke model (Excel = lengkap)


def _t_lihat_unit_armada(args: dict, user: dict) -> dict:
    """Daftar/ringkasan unit armada + status GPS live. Mencakup: SATU unit
    (param `unit` = frame/VIN → termasuk NAMANYA), semua unit, atau per fleet."""
    if not _is_admin(user):
        return dict(_TELE_DENIED)
    if not telematics.available():
        return dict(_TELE_OFF)

    # Lookup SATU unit spesifik (frame/cjh/VIN/nama) — jawab "cek nama unit X",
    # "unit X ada di fleet mana". Tanpa ini model menebak dari daftar cap-60.
    target = (args.get("unit") or args.get("frame") or args.get("cjh")
              or args.get("vin") or "").strip()
    if target:
        rec = telematics.cari_unit(target)
        if not rec:
            return {"found": False, "dicari": target,
                    "catatan": (f"Unit '{target}' tidak ditemukan di daftar telematics/GPS. "
                                "Sampaikan jujur; unit mungkin belum dipasang perangkat GPS. "
                                "Jangan mengarang nama/status.")}
        loc = telematics.lokasi_semua()
        u = telematics.rangkum_unit(rec, loc.get(rec.get("cjh")))
        return {
            "found": True, "unit": u,
            "nama": u.get("nama") or "(belum diberi nama/label)",
            "catatan": ("Data GPS/telematics 1 unit. 'nama' = label/carNumber di "
                        "telematics (bisa berbeda dari nopol). Sebut apa adanya; "
                        "'(belum diberi nama)' bila kosong. Untuk mengubahnya pakai "
                        "ganti_nama_unit (butuh konfirmasi)."),
        }

    fleet = (args.get("fleet") or "").strip()
    status_f = (args.get("status") or "").strip().lower()
    hanya_rusak = bool(args.get("hanya_rusak"))

    d = telematics.semua_unit(fleet=fleet)
    if d is None:
        return {"error": "Telematics tidak merespons — coba lagi sebentar lagi."}
    recs = d.get("records") or []
    if not recs:
        return {"found": False,
                "catatan": (f"Tidak ada unit untuk fleet '{fleet}'." if fleet
                            else "Tidak ada unit terdaftar di telematics.")}
    loc = telematics.lokasi_semua()
    unit = [telematics.rangkum_unit(r, loc.get(r.get("cjh"))) for r in recs]
    if status_f:
        unit = [u for u in unit
                if status_f in (str(u.get("status_gps") or "").lower())]
    if hanya_rusak:
        unit = [u for u in unit if u.get("rusak")]

    out: dict = {
        "found": True,
        "total_cocok": len(unit),
        "total_armada": d.get("total"),
        "fleet_filter": fleet or None,
    }
    if not fleet and not status_f and not hanya_rusak:
        # Tampilan ringkasan armada penuh: breakdown per fleet + hitung rusak.
        out["per_fleet"] = telematics.fleet_breakdown(recs)
        out["jumlah_rusak"] = sum(1 for u in unit if u.get("rusak"))
    dipotong = len(unit) > _TELE_MAX_TABEL
    out["unit"] = unit[:_TELE_MAX_TABEL]
    out["catatan"] = (
        "Data GPS/telematics armada (Sinotruk Fleet Service) — real-time posisi & "
        "status, BUKAN spesifikasi katalog EPC (untuk itu pakai cek_kendaraan) & "
        "BUKAN populasi internal (cek_populasi). 'rusak'=unit ditandai bermasalah "
        "oleh sistem. km/BBM dari GPS."
        + (f" ⚠️ {len(unit)} unit cocok, hanya {_TELE_MAX_TABEL} ditampilkan — "
           "untuk daftar LENGKAP tawarkan excel_unit_armada." if dipotong else "")
    )
    return out


def _t_ganti_nama_unit(args: dict, user: dict) -> dict:
    """⚠️ WRITE (2 langkah): ubah nama/label unit di server Sinotruk."""
    if not _is_admin(user):
        return dict(_TELE_DENIED)
    if not telematics.available():
        return dict(_TELE_OFF)
    target = (args.get("cjh") or args.get("unit") or "").strip()
    nama_baru = (args.get("nama_baru") or args.get("nama") or "").strip()
    if not target or not nama_baru:
        return {"error": "Sebutkan unit (frame/cjh) DAN nama baru."}
    rec = telematics.cari_unit(target)
    if not rec:
        return {"found": False,
                "catatan": f"Unit '{target}' tidak ditemukan di telematics. Cek ulang frame/VIN."}
    nama_lama = (rec.get("carNumber") or "").strip() or "(belum ada nama)"
    cjh = rec.get("cjh")
    if not args.get("konfirmasi"):
        # LANGKAH 1 — pratinjau, JANGAN tulis. Model wajib minta persetujuan user.
        return {
            "perlu_konfirmasi": True,
            "pratinjau": {
                "frame": cjh, "model": rec.get("model"),
                "fleet": telematics._fleet_names(rec),
                "nama_sekarang": nama_lama, "nama_baru": nama_baru,
            },
            "catatan": (f"⚠️ KONFIRMASI DULU ke user: ubah nama unit {cjh} "
                        f"({rec.get('model')}) dari '{nama_lama}' menjadi "
                        f"'{nama_baru}'? Operasi ini MENGUBAH data di server Sinotruk "
                        "dan permanen. JANGAN eksekusi sampai user menyetujui — bila "
                        "setuju, panggil ganti_nama_unit lagi dengan konfirmasi=true."),
        }
    # LANGKAH 2 — user sudah setuju → eksekusi.
    hasil = telematics.ganti_nama(cjh, nama_baru)
    if hasil is None:
        return {"found": False,
                "catatan": f"Gagal mengubah nama unit {cjh} — server telematics menolak/timeout. "
                           "Sampaikan jujur, jangan klaim berhasil."}
    return {"found": True, "berhasil": True, "frame": cjh,
            "nama_lama": nama_lama, "nama_baru": (hasil.get("carNumber") or nama_baru),
            "catatan": f"✅ Nama unit {cjh} berhasil diubah menjadi "
                       f"'{hasil.get('carNumber') or nama_baru}'."}


def _t_excel_unit_armada(args: dict, user: dict) -> dict:
    """EXPORT EXCEL daftar unit armada (semua / per fleet) dibangun DI SERVER."""
    if not _is_admin(user):
        return dict(_TELE_DENIED)
    if not telematics.available():
        return dict(_TELE_OFF)
    fleet = (args.get("fleet") or "").strip()
    d = telematics.semua_unit(fleet=fleet)
    if d is None:
        return {"error": "Telematics tidak merespons — coba lagi sebentar lagi."}
    recs = d.get("records") or []
    if not recs:
        return {"found": False, "catatan": f"Tidak ada unit untuk fleet '{fleet}'." if fleet
                else "Tidak ada unit terdaftar."}
    loc = telematics.lokasi_semua()
    kolom = ["No", "Frame", "VIN", "Nama", "Model", "Brand", "Engine",
             "Gearbox", "Penggerak", "Ban", "KM", "Fleet", "Status GPS",
             "BBM %", "Rusak"]
    baris: list[list] = []
    for i, r in enumerate(recs[:_EXCEL_MAX_ROWS], start=1):
        u = telematics.rangkum_unit(r, loc.get(r.get("cjh")))
        baris.append([
            str(i), u.get("frame") or "", u.get("vin") or "", u.get("nama") or "",
            u.get("model") or "", u.get("brand") or "", u.get("engine") or "",
            u.get("gearbox") or "", u.get("penggerak") or "", u.get("ban") or "",
            ai_export.ke_angka(u.get("km") or ""), ", ".join(u.get("fleet") or []),
            u.get("status_gps") or "", ai_export.ke_angka(u.get("bbm_persen") if u.get("bbm_persen") is not None else ""),
            "Ya" if u.get("rusak") else "",
        ])
    judul = "Unit Armada" + (f" — {fleet}" if fleet else " (semua)")
    export_id, filename = ai_export.stash_export(judul, kolom, baris)
    return {"found": True, "export_id": export_id, "filename": filename,
            "judul": judul, "jumlah_baris": len(baris), "fleet": fleet or None,
            "catatan": ("File Excel armada siap — kartu unduh muncul OTOMATIS di bawah. "
                        "Jawab SINGKAT (judul + jumlah unit + fleet bila ada). "
                        "⛔ JANGAN tulis ulang isi tabel & JANGAN membuat link sendiri.")}


_TELE_MAX_RENAME = 500     # pagar batas baris rename massal per giliran


def _t_sheet_isi_nama_telematik(args: dict, user: dict) -> dict:
    """Isi NAMA unit MASSAL ke telematics dari Excel (frame→nama). ADMIN-ONLY,
    TULIS 2 langkah (pratinjau lalu konfirmasi)."""
    if not _is_admin(user):
        return dict(_TELE_DENIED)
    if not telematics.available():
        return dict(_TELE_OFF)
    parsed = ai_sheet.get_sheet(args.get("_sheet_id", ""), user.get("username", ""))
    if not parsed:
        return {"found": False, "error": "Tidak ada file Excel terlampir (atau kedaluwarsa). "
                                         "Minta user mengunggah Excel berisi kolom nomor rangka & nama."}
    headers = parsed.get("headers") or []
    body = parsed.get("_body") or []

    # Kolom rangka & nama: dari arahan user, atau deteksi header umum.
    def _kolom(minta: str, kandidat: list[str]) -> int | None:
        if (minta or "").strip():
            return ai_sheet._cari_kolom(headers, minta)
        for k in kandidat:
            i = ai_sheet._cari_kolom(headers, k)
            if i is not None:
                return i
        return None
    i_frame = _kolom(args.get("kolom_rangka"), ["no rangka", "rangka", "frame", "chassis", "cjh", "vin"])
    i_nama = _kolom(args.get("kolom_nama"), ["nama", "name", "label", "carnumber", "nomor lambung"])
    if i_frame is None or i_nama is None:
        return {"found": False,
                "catatan": ("Kolom nomor rangka dan/atau nama tidak terdeteksi di Excel "
                            f"(header: {headers}). Minta user menyebut nama kolomnya.")}

    # Pasangan (frame, nama) dari file.
    pasangan: list[tuple[str, str]] = []
    for r in body:
        frame = (r[i_frame] if i_frame < len(r) else "").strip()
        nama = (r[i_nama] if i_nama < len(r) else "").strip()
        if frame and nama:
            pasangan.append((frame, nama))
    if not pasangan:
        return {"found": False, "catatan": "Tidak ada baris berisi rangka + nama di Excel."}
    if len(pasangan) > _TELE_MAX_RENAME:
        return {"found": False,
                "catatan": f"Terlalu banyak baris ({len(pasangan)}) — batas {_TELE_MAX_RENAME} "
                           "per proses. Pecah filenya."}

    # Peta unit telematics (SEKALI tarik) — cocok via cjh atau vin.
    d = telematics.semua_unit()
    if d is None:
        return {"error": "Telematics tidak merespons — coba lagi sebentar lagi."}
    peta: dict[str, dict] = {}
    for rec in (d.get("records") or []):
        for k in (rec.get("cjh"), rec.get("vin")):
            if k:
                peta.setdefault(k.strip().upper(), rec)

    berubah: list[dict] = []
    sama = 0
    tak_ada: list[str] = []
    for frame, nama in pasangan:
        rec = peta.get(frame.strip().upper())
        if not rec:
            tak_ada.append(frame)
            continue
        cur = (rec.get("carNumber") or "").strip()
        if cur == nama:
            sama += 1
        else:
            berubah.append({"cjh": rec.get("cjh"), "nama_lama": cur or "(kosong)",
                            "nama_baru": nama})

    if not args.get("konfirmasi"):
        # LANGKAH 1 — pratinjau, TIDAK menulis.
        return {
            "perlu_konfirmasi": bool(berubah),
            "ringkasan": {"total_baris": len(pasangan), "akan_berubah": len(berubah),
                          "sudah_sama": sama, "tak_ada_di_telematics": len(tak_ada)},
            "contoh_perubahan": berubah[:15],
            "contoh_tak_ada": tak_ada[:15],
            "catatan": (
                (f"⚠️ KONFIRMASI DULU ke user: terapkan {len(berubah)} perubahan nama ke "
                 "telematics (server Sinotruk, PERMANEN)? "
                 f"{sama} sudah sama (dilewati), {len(tak_ada)} frame tak ada di telematics "
                 "(dilewati). Bila user setuju, panggil sheet_isi_nama_telematik lagi dengan "
                 "konfirmasi=true.")
                if berubah else
                (f"Tidak ada yang perlu diubah: {sama} unit sudah bernama sesuai Excel, "
                 f"{len(tak_ada)} frame tak ada di telematics. Sampaikan apa adanya.")
            ),
        }

    # LANGKAH 2 — user setuju → terapkan hanya yang BERUBAH.
    berhasil = 0
    gagal: list[str] = []
    for item in berubah:
        res = telematics.ganti_nama(item["cjh"], item["nama_baru"])
        if res is not None:
            berhasil += 1
        else:
            gagal.append(item["cjh"])
    return {
        "found": True, "selesai": True,
        "diterapkan": berhasil, "gagal": len(gagal), "dilewati_sama": sama,
        "dilewati_tak_ada": len(tak_ada), "contoh_gagal": gagal[:10],
        "catatan": (f"✅ {berhasil} nama unit diterapkan ke telematics"
                    + (f", {len(gagal)} gagal (server menolak — sampaikan jujur)" if gagal else "")
                    + f". {sama} sudah sama & {len(tak_ada)} tak ada di telematics dilewati."),
    }


def _t_daftarkan_unit(args: dict, user: dict) -> dict:
    """⚠️ WRITE (2 langkah): DAFTARKAN unit baru ke telematics (VIN + serial GPS)."""
    if not _is_admin(user):
        return dict(_TELE_DENIED)
    if not telematics.available():
        return dict(_TELE_OFF)
    vin = (args.get("vin") or args.get("rangka") or "").strip().upper()
    sbh = (args.get("sbh") or args.get("serial_gps") or args.get("gps") or "").strip()
    if not vin or not sbh:
        return {"error": "Butuh VIN unit DAN serial perangkat GPS (sbh). Keduanya wajib."}
    try:
        km = int(args.get("km") or args.get("mileage") or 0)
    except (TypeError, ValueError):
        km = 0
    euro2 = bool(args.get("euro2"))
    frame = telematics.frame_dari_rangka(vin)
    sudah = telematics.cari_unit(vin) or telematics.cari_unit(frame)

    if not args.get("konfirmasi"):
        # LANGKAH 1 — pratinjau, TIDAK menulis.
        return {
            "perlu_konfirmasi": True,
            "pratinjau": {"vin": vin, "frame": frame, "serial_gps": sbh,
                          "km_awal": km, "euro2": euro2,
                          "sudah_terdaftar": bool(sudah)},
            "catatan": (
                (f"⚠️ Unit VIN {vin} (frame {frame}) SUDAH ADA di telematics — "
                 "mendaftarkan ulang mungkin ditolak/duplikat. Konfirmasi ke user "
                 "apakah tetap lanjut."
                 if sudah else
                 f"⚠️ KONFIRMASI DULU ke user: daftarkan unit BARU VIN {vin} "
                 f"(frame {frame}) dengan perangkat GPS serial {sbh}, km awal {km}, "
                 f"Euro2={euro2}? Ini MENAMBAH data permanen di server Sinotruk.")
                + " Bila user setuju, panggil daftarkan_unit lagi dengan konfirmasi=true. "
                "⛔ Pastikan serial GPS benar — tak bisa ditebak."),
        }
    # LANGKAH 2 — user setuju → daftarkan.
    hasil = telematics.daftarkan(sbh, vin, km, euro2)
    if hasil is None:
        return {"found": False,
                "catatan": f"Gagal mendaftarkan VIN {vin} — server telematics menolak/timeout "
                           "(cek serial GPS & VIN, mungkin sudah terdaftar). Sampaikan jujur, "
                           "jangan klaim berhasil."}
    return {"found": True, "berhasil": True, "vin": vin,
            "frame": hasil.get("cjh") or frame, "serial_gps": sbh,
            "catatan": f"✅ Unit VIN {vin} (frame {hasil.get('cjh') or frame}) berhasil "
                       "didaftarkan ke telematics/GPS."}


def _t_sheet_daftar_unit(args: dict, user: dict) -> dict:
    """⚠️ WRITE (2 langkah): DAFTARKAN unit MASSAL dari Excel (VIN + serial GPS)."""
    if not _is_admin(user):
        return dict(_TELE_DENIED)
    if not telematics.available():
        return dict(_TELE_OFF)
    parsed = ai_sheet.get_sheet(args.get("_sheet_id", ""), user.get("username", ""))
    if not parsed:
        return {"found": False, "error": "Tidak ada file Excel terlampir (atau kedaluwarsa). "
                                         "Minta user mengunggah Excel berisi VIN & serial GPS."}
    headers = parsed.get("headers") or []
    body = parsed.get("_body") or []

    def _kolom(minta, kandidat):
        if (minta or "").strip():
            return ai_sheet._cari_kolom(headers, minta)
        for k in kandidat:
            i = ai_sheet._cari_kolom(headers, k)
            if i is not None:
                return i
        return None
    i_vin = _kolom(args.get("kolom_vin"), ["vin", "no rangka", "rangka", "frame", "chassis"])
    i_sbh = _kolom(args.get("kolom_sbh"), ["sbh", "serial", "gps", "terminal", "box", "imei"])
    i_km = _kolom(args.get("kolom_km"), ["km", "mileage", "kilometer", "odometer"])
    i_e2 = _kolom(args.get("kolom_euro2"), ["euro2", "euro 2", "eu2"])
    if i_vin is None or i_sbh is None:
        return {"found": False,
                "catatan": ("Kolom VIN dan/atau serial GPS (sbh) tidak terdeteksi "
                            f"(header: {headers}). Minta user menyebut nama kolomnya.")}

    baris: list[dict] = []
    for r in body:
        vin = (r[i_vin] if i_vin < len(r) else "").strip().upper()
        sbh = (r[i_sbh] if i_sbh < len(r) else "").strip()
        if not vin or not sbh:
            continue
        km = 0
        if i_km is not None and i_km < len(r):
            try:
                km = int(float(r[i_km])) if r[i_km] else 0
            except (TypeError, ValueError):
                km = 0
        euro2 = False
        if i_e2 is not None and i_e2 < len(r):
            euro2 = str(r[i_e2]).strip().lower() in ("1", "true", "ya", "yes", "euro2")
        baris.append({"vin": vin, "sbh": sbh, "km": km, "euro2": euro2})
    if not baris:
        return {"found": False, "catatan": "Tidak ada baris berisi VIN + serial GPS di Excel."}
    if len(baris) > _TELE_MAX_RENAME:
        return {"found": False,
                "catatan": f"Terlalu banyak baris ({len(baris)}) — batas {_TELE_MAX_RENAME}. Pecah filenya."}

    # Cek mana yang SUDAH terdaftar (peta ditarik sekali).
    d = telematics.semua_unit()
    ada_set = set()
    if d is not None:
        for rec in (d.get("records") or []):
            for k in (rec.get("cjh"), rec.get("vin")):
                if k:
                    ada_set.add(k.strip().upper())
    baru = [b for b in baris
            if telematics.frame_dari_rangka(b["vin"]) not in ada_set
            and b["vin"] not in ada_set]
    sudah = len(baris) - len(baru)

    if not args.get("konfirmasi"):
        return {
            "perlu_konfirmasi": bool(baru),
            "ringkasan": {"total_baris": len(baris), "akan_didaftar": len(baru),
                          "sudah_terdaftar": sudah},
            "contoh": [{"vin": b["vin"], "serial_gps": b["sbh"], "km": b["km"]} for b in baru[:15]],
            "catatan": (
                (f"⚠️ KONFIRMASI DULU ke user: daftarkan {len(baru)} unit BARU ke "
                 f"telematics (server Sinotruk, PERMANEN)? {sudah} sudah terdaftar (dilewati). "
                 "Pastikan serial GPS tiap unit benar. Bila setuju, panggil sheet_daftar_unit "
                 "lagi dengan konfirmasi=true.")
                if baru else
                f"Semua {sudah} unit di Excel sudah terdaftar di telematics — tidak ada yang perlu ditambah."
            ),
        }
    # LANGKAH 2 — daftarkan yang baru.
    berhasil = 0
    gagal: list[str] = []
    for b in baru:
        res = telematics.daftarkan(b["sbh"], b["vin"], b["km"], b["euro2"])
        if res is not None:
            berhasil += 1
        else:
            gagal.append(b["vin"])
    return {"found": True, "selesai": True, "didaftar": berhasil, "gagal": len(gagal),
            "dilewati_sudah_ada": sudah, "contoh_gagal": gagal[:10],
            "catatan": (f"✅ {berhasil} unit didaftarkan ke telematics"
                        + (f", {len(gagal)} gagal (server menolak — cek serial/VIN)" if gagal else "")
                        + f". {sudah} sudah terdaftar dilewati.")}


def _org_ids(rec: dict) -> list[int]:
    return [o["id"] for o in (rec.get("organizations") or []) if o.get("id")]


def _t_buat_fleet(args: dict, user: dict) -> dict:
    """⚠️ WRITE (2 langkah): buat FLEET/organisasi baru di telematics."""
    if not _is_admin(user):
        return dict(_TELE_DENIED)
    if not telematics.available():
        return dict(_TELE_OFF)
    nama = (args.get("nama") or args.get("fleet") or "").strip()
    if not nama:
        return {"error": "Sebutkan nama fleet yang mau dibuat."}
    induk = (args.get("induk") or args.get("parent") or "").strip()
    parent = None
    if induk:
        parent = telematics.cari_fleet(induk)
        if not parent:
            return {"found": False, "catatan": f"Fleet induk '{induk}' tidak ditemukan."}
        if parent.get("ambigu"):
            return {"found": False, "ambigu": parent["ambigu"],
                    "catatan": f"Nama induk '{induk}' cocok ke beberapa: {parent['ambigu']}. Pertegas."}
    # Cek nama fleet sudah ada (di bawah induk yang sama, atau di mana pun bila induk kosong).
    pid = (parent or {}).get("id")
    fleets = telematics.daftar_fleet()
    duplikat = [f for f in fleets if (f.get("nama") or "").strip().lower() == nama.lower()
                and (pid is None or f.get("parent_id") == pid)]

    if not args.get("konfirmasi"):
        return {"perlu_konfirmasi": True,
                "pratinjau": {"nama_fleet": nama,
                              "induk": (parent or {}).get("nama") or "(akar/utama)",
                              "sudah_ada": bool(duplikat)},
                "catatan": (
                    (f"⚠️ Fleet bernama '{nama}' SUDAH ADA — membuat lagi akan duplikat. "
                     "Konfirmasi ke user apakah tetap lanjut."
                     if duplikat else
                     f"⚠️ KONFIRMASI DULU ke user: buat fleet baru '{nama}' di bawah "
                     f"'{(parent or {}).get('nama') or 'organisasi utama'}'? Ini menambah "
                     "struktur di server Sinotruk.")
                    + " Bila setuju, panggil buat_fleet lagi dengan konfirmasi=true."),
                }
    hasil = telematics.buat_fleet(nama, pid)
    if not hasil:
        return {"found": False,
                "catatan": f"Gagal membuat fleet '{nama}' (server menolak/timeout). Sampaikan jujur."}
    return {"found": True, "berhasil": True, "fleet": nama,
            "id_fleet": hasil.get("id"), "induk": (parent or {}).get("nama") or "utama",
            "catatan": f"✅ Fleet '{nama}' berhasil dibuat"
                       + (f" (id {hasil['id']})" if hasil.get("id") else "")
                       + ". Unit bisa dimasukkan via masukkan_unit_fleet / sheet_masukkan_fleet."}


def _t_masukkan_unit_fleet(args: dict, user: dict) -> dict:
    """⚠️ WRITE (2 langkah): masukkan/pindahkan SATU unit ke fleet."""
    if not _is_admin(user):
        return dict(_TELE_DENIED)
    if not telematics.available():
        return dict(_TELE_OFF)
    unit = (args.get("unit") or args.get("cjh") or args.get("vin") or "").strip()
    fleet = (args.get("fleet") or args.get("organisasi") or "").strip()
    if not unit or not fleet:
        return {"error": "Sebutkan unit (frame/VIN) DAN fleet tujuan."}
    rec = telematics.cari_unit(unit)
    if not rec:
        return {"found": False, "catatan": f"Unit '{unit}' tidak ditemukan di telematics."}
    tgt = telematics.cari_fleet(fleet)
    if not tgt:
        return {"found": False, "catatan": f"Fleet '{fleet}' tidak ditemukan. Sebutkan nama fleet yang ada."}
    if tgt.get("ambigu"):
        return {"found": False, "ambigu": tgt["ambigu"],
                "catatan": f"Nama fleet '{fleet}' cocok ke beberapa: {tgt['ambigu']}. Minta user pertegas."}
    cjh = rec.get("cjh")
    org_lama = [o.get("organizationName") for o in (rec.get("organizations") or [])]

    if not args.get("konfirmasi"):
        return {"perlu_konfirmasi": True,
                "pratinjau": {"unit": cjh, "model": rec.get("model"),
                              "fleet_sekarang": org_lama,
                              "fleet_tujuan": tgt["nama"]},
                "catatan": (f"⚠️ KONFIRMASI DULU ke user: masukkan unit {cjh} "
                            f"({rec.get('model')}) ke fleet '{tgt['nama']}'? "
                            "Ini mengubah pengelompokan di server Sinotruk. Bila setuju, "
                            "panggil masukkan_unit_fleet lagi dengan konfirmasi=true.")}
    ok = telematics.masukkan_ke_fleet(tgt["id"],
                                      [{"cjh": cjh, "organizationIds": _org_ids(rec)}])
    if not ok:
        return {"found": False,
                "catatan": f"Gagal memasukkan unit {cjh} ke fleet '{tgt['nama']}' "
                           "(server menolak/timeout). Sampaikan jujur."}
    return {"found": True, "berhasil": True, "unit": cjh, "fleet": tgt["nama"],
            "catatan": f"✅ Unit {cjh} berhasil dimasukkan ke fleet '{tgt['nama']}'."}


def _t_sheet_masukkan_fleet(args: dict, user: dict) -> dict:
    """⚠️ WRITE (2 langkah): masukkan unit ke fleet MASSAL dari Excel (unit → fleet)."""
    if not _is_admin(user):
        return dict(_TELE_DENIED)
    if not telematics.available():
        return dict(_TELE_OFF)
    parsed = ai_sheet.get_sheet(args.get("_sheet_id", ""), user.get("username", ""))
    if not parsed:
        return {"found": False, "error": "Tidak ada file Excel terlampir (atau kedaluwarsa). "
                                         "Minta user mengunggah Excel berisi kolom unit & fleet."}
    headers = parsed.get("headers") or []
    body = parsed.get("_body") or []

    def _kolom(minta, kandidat):
        if (minta or "").strip():
            return ai_sheet._cari_kolom(headers, minta)
        for k in kandidat:
            i = ai_sheet._cari_kolom(headers, k)
            if i is not None:
                return i
        return None
    i_unit = _kolom(args.get("kolom_unit"), ["no rangka", "rangka", "unit", "frame", "cjh", "vin"])
    i_fleet = _kolom(args.get("kolom_fleet"), ["fleet", "organisasi", "organization", "grup", "group"])
    if i_unit is None or i_fleet is None:
        return {"found": False,
                "catatan": (f"Kolom unit dan/atau fleet tidak terdeteksi (header: {headers}). "
                            "Minta user menyebut nama kolomnya.")}

    pasangan = []
    for r in body:
        u = (r[i_unit] if i_unit < len(r) else "").strip()
        f = (r[i_fleet] if i_fleet < len(r) else "").strip()
        if u and f:
            pasangan.append((u, f))
    if not pasangan:
        return {"found": False, "catatan": "Tidak ada baris berisi unit + fleet di Excel."}
    if len(pasangan) > _TELE_MAX_RENAME:
        return {"found": False, "catatan": f"Terlalu banyak baris ({len(pasangan)}) — batas {_TELE_MAX_RENAME}."}

    # Peta unit & fleet ditarik SEKALI.
    d = telematics.semua_unit()
    peta_unit = {}
    for rec in ((d or {}).get("records") or []):
        for k in (rec.get("cjh"), rec.get("vin")):
            if k:
                peta_unit.setdefault(k.strip().upper(), rec)
    fleets = telematics.daftar_fleet()
    peta_fleet = {}
    for x in fleets:
        if x.get("nama"):
            peta_fleet.setdefault(x["nama"].strip().lower(), x)

    rencana = []          # (rec, fleet_dict)
    unit_hilang, fleet_hilang = [], []
    for u, f in pasangan:
        rec = peta_unit.get(u.strip().upper())
        if not rec:
            unit_hilang.append(u); continue
        fl = peta_fleet.get(f.strip().lower())
        if not fl:
            fleet_hilang.append(f); continue
        rencana.append((rec, fl))

    if not args.get("konfirmasi"):
        return {"perlu_konfirmasi": bool(rencana),
                "ringkasan": {"total_baris": len(pasangan), "akan_dipindah": len(rencana),
                              "unit_tak_ada": len(unit_hilang), "fleet_tak_ada": len(fleet_hilang)},
                "contoh": [{"unit": rec.get("cjh"), "fleet": fl["nama"]} for rec, fl in rencana[:15]],
                "contoh_fleet_tak_ada": list(dict.fromkeys(fleet_hilang))[:10],
                "catatan": ((f"⚠️ KONFIRMASI DULU ke user: pindahkan {len(rencana)} unit ke fleet "
                             "masing-masing (server Sinotruk)? "
                             f"{len(unit_hilang)} unit & {len(fleet_hilang)} fleet tak ditemukan (dilewati). "
                             "Bila setuju, panggil sheet_masukkan_fleet lagi dengan konfirmasi=true.")
                            if rencana else
                            "Tidak ada yang bisa dipindah: unit/fleet di Excel tak cocok dengan telematics.")}
    # Terapkan — kelompokkan per fleet tujuan (1 panggilan per fleet).
    per_fleet: dict = {}
    for rec, fl in rencana:
        per_fleet.setdefault(fl["id"], {"nama": fl["nama"], "cars": []})["cars"].append(
            {"cjh": rec.get("cjh"), "organizationIds": _org_ids(rec)})
    berhasil = 0
    gagal_fleet = []
    for fid, grp in per_fleet.items():
        if telematics.masukkan_ke_fleet(fid, grp["cars"]):
            berhasil += len(grp["cars"])
        else:
            gagal_fleet.append(grp["nama"])
    return {"found": True, "selesai": True, "dipindah": berhasil,
            "fleet_gagal": gagal_fleet[:10], "dilewati_unit_tak_ada": len(unit_hilang),
            "dilewati_fleet_tak_ada": len(fleet_hilang),
            "catatan": (f"✅ {berhasil} unit dipindah ke fleet-nya"
                        + (f"; fleet gagal: {gagal_fleet}" if gagal_fleet else "")
                        + f". Dilewati: {len(unit_hilang)} unit & {len(fleet_hilang)} fleet tak ditemukan.")}


