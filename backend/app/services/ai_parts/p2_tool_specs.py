# -*- coding: utf-8 -*-
# ai_parts/p2_tool_specs.py — BAGIAN dari modul app.services.ai_assistant (SATU namespace).
# Dimuat berurutan oleh ai_assistant.py (loader) via exec — sengaja BUKAN
# submodul terpisah: monkeypatch test `ai.<attr>`, relative import, dan
# resolusi global lintas-bagian tetap bekerja persis seperti file tunggal.
# Urutan muat & pembagian: lihat _PARTS di ai_assistant.py.
from __future__ import annotations

# ── Gerbang konteks RANGKA (2026-08-09) ─────────────────────────────────
# Ukuran produksi: blok TETAP tiap panggilan API = 77 spec tool (24.691 tok) +
# system prompt (17.569 tok) ≈ 44.400 token, dan itu dibayar SETIAP RONDE tool.
# Giliran 8 ronde di log nyata memakai 480.084 token masuk — mayoritas beban
# tetap ini, bukan isi percakapan. 67% waktu giliran Agustus habis di MODEL
# (3.829 dtk) vs tool (1.854 dtk), jadi memangkas prompt = memangkas latensi.
#
# Yang disembunyikan HANYA tool yang parameter WAJIB-nya 'rangka' — mustahil
# dipanggil tanpa nomor rangka, jadi menawarkannya di percakapan tanpa rangka
# adalah token murni terbuang. Daftarnya DITURUNKAN dari spec (bukan ditulis
# tangan) supaya tool per-VIN baru ikut otomatis dan tak ada daftar basi.
_RANGKA_KATA_RE = re.compile(
    r"(rangka|\bvin\b|\bframe\b|chassis|no\.?\s*unit|unitnya|unit\s+(ini|itu|saya)"
    r"|truk\s*(ini|saya)|mobil\s*(ini|saya))", re.I)
_VIN_LONGGAR_RE = re.compile(r"\bL[A-HJ-NPR-Z0-9]{16}\b|\b[A-Z]{2}\d{6}\b")


def _konteks_rangka(messages: list[dict] | None) -> bool:
    """Apakah percakapan ini punya konteks NOMOR RANGKA?

    ⚠️ SENGAJA PEMURAH. Salah-TAMPIL cuma memboroskan token; salah-SEMBUNYI
    menyembunyikan alat yang dibutuhkan — kerusakan yang jauh lebih mahal. Maka
    dipakai regex VIN/frame TANPA penyaringan PN (beda dari _rangka_candidates
    yang membuang PN katalog), kata kunci rangka/VIN/unit, dan jejak tool
    per-VIN yang sudah pernah dipanggil di percakapan ini. SEMUA peran pesan
    dibaca — rangka kerap hanya ada di hasil tool / jawaban asisten sebelumnya.

    ⛔ Ini TIDAK mengubah izin eksekusi: _allowed_tool_names sengaja memanggil
    _tool_specs TANPA history, jadi bila model tetap memanggil tool per-VIN
    (mis. mengingat dari giliran sebelumnya) panggilan itu TETAP dijalankan.
    """
    if messages is None:
        return True                      # tanpa konteks → jangan gerbangi apa pun
    for m in messages or []:
        isi = (m or {}).get("content")
        if not isinstance(isi, str) or not isi:
            continue
        if _VIN_LONGGAR_RE.search(isi.upper()) or _RANGKA_KATA_RE.search(isi):
            return True
    return False


def _saring_pervin(specs: list[dict], history: list[dict] | None) -> list[dict]:
    """Buang tool yang parameter WAJIB-nya 'rangka' bila percakapan tak
    menyinggung rangka sama sekali.

    Sengaja LANGKAH TERPISAH, bukan parameter _tool_specs: puluhan tes (dan
    jalur _allowed_tool_names) memakai _tool_specs dengan 2 argumen, dan
    menambah parameter di sana memutus semuanya sekaligus — tanda bahwa
    tanda tangan itu memang antarmuka yang dipakai luas. Di sini penyaringan
    jadi keputusan pemanggil, bukan sifat tersembunyi dari daftar spec.
    """
    if _konteks_rangka(history):
        return specs

    def _wajib_rangka(s: dict) -> bool:
        # Bentuk spec TIDAK diasumsikan rapi: penyaring ini berdiri di jalur
        # panas chat() — spec cacat harus lolos apa adanya, bukan menjatuhkan
        # giliran user. (Tanpa ini, satu spec tanpa kunci 'function' = KeyError
        # di tengah percakapan.)
        try:
            fn = (s or {}).get("function") or {}
            return "rangka" in ((fn.get("parameters") or {}).get("required") or [])
        except Exception:
            return False

    return [s for s in specs if not _wajib_rangka(s)]


def _tool_specs(user: dict, sheet_id: str = "") -> list[dict]:
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
                    "total, stok per gudang, harga jual lokal, dan UNIT/MODEL sumber; "
                    "plus 'stok_lokal_tambahan' = barang STOK GUDANG di luar katalog "
                    "(aftermarket/merek lain, mis. alternator regulator, kaca spion "
                    "aftermarket) yang cocok kata kunci; tiap part juga bisa membawa "
                    "field 'pengganti' = PN PERSAMAAN/pengganti resmi (supersession) bila "
                    "ada — sebutkan ke user, terutama bila stok aslinya kosong. Gunakan untuk 'apakah ada', "
                    "'stok berapa', 'cari part X', 'ada berapa <part> di stok'. "
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
                        # ⛔ JANGAN hidupkan lagi parameter 'mode' (pn|nama): handler
                        # _t_cari_part TIDAK pernah membacanya — pencarian selalu
                        # menyisir PN dan nama sekaligus. Spec-nya dulu malah
                        # menyebut 'pn' sebagai default sementara deskripsi tool
                        # bilang "tak perlu menentukan mode": model diberi tuas mati
                        # yang berbunyi seolah bisa mempersempit hasil.
                        "query": {"type": ["string", "array"], "items": {"type": "string"},
                                  "description": "PN atau kata kunci nama part (mis. 'injector'). ARRAY = banyak istilah SEKALIGUS, maks 20 (hasil dilabeli per istilah) — jangan panggil berulang."},
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
                    "Detail Part Number persis: nama, STOK (utama dari ERP Accurate, "
                    "disinkron berkala — total + rincian per gudang; fallback Excel bila Accurate "
                    "tak tersedia; lihat field 'sumber_stok'), harga jual lokal, dan SPESIFIKASI "
                    "fisik resmi (berat kg, dimensi cm, satuan, merek). Ini tool utama untuk "
                    "pertanyaan stok/berat/dimensi per PN. "
                    "⭐ BANYAK PN sekaligus: isi 'part_number' dengan ARRAY berisi SEMUA PN yang "
                    "user sebut/tempel — SATU panggilan menjawab semuanya."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "part_number": {"type": ["string", "array"], "items": {"type": "string"},
                                        "description": "Part Number lengkap/persis. BOLEH ARRAY berisi SEMUA PN yang user sebut (maks 100) — kirim sekaligus, jangan dipecah jadi beberapa panggilan."},
                        "dimensi": {"type": "boolean", "description": "true → sertakan dimensi P×L×T (cm) dari SIMS. Hanya bila user menanyakan ukuran/dimensi (lambat; dibatasi 40 PN pertama). Berat SELALU disertakan tanpa argumen ini."},
                        "excel": {"type": "boolean", "description": "true → hasil banyak PN juga dijadikan file Excel unduhan."},
                    },
                    "required": ["part_number"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "cek_massal_part",
                "description": (
                    "BANYAK Part Number sekaligus — nama + stok (total & per gudang) + harga "
                    "+ BERAT tertagih (kg) tiap PN; PN yang tidak ada ditandai jujur. Setara "
                    "detail_part dengan 'part_number' berbentuk ARRAY. dimensi=true bila user "
                    "menanyakan UKURAN (lambat, 40 PN pertama); excel=true untuk file unduhan; "
                    "hasil bisa langsung jadi buat_penawaran. ⛔ 'MASSAL' = banyak PN, SATU "
                    "pertanyaan stok/harga — BUKAN satu part di BANYAK RANGKA/UNIT ('injector "
                    "untuk 5 VIN ini sama tidak?' → cek_massal_part_rangka / "
                    "cek_massal_part_mesin). Tool ini tak tahu unit apa pun."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "daftar_pn": {
                            "type": "array", "items": {"type": "string"},
                            "description": "Daftar Part Number (boleh juga satu string dipisah baris/koma).",
                        },
                        "dimensi": {
                            "type": "boolean",
                            "description": ("true → sertakan dimensi P×L×T (cm) dari SIMS. Pakai HANYA "
                                            "bila user menanyakan ukuran/dimensi: sumbernya lambat "
                                            "(diambil per part) dan dibatasi 40 PN pertama. Berat "
                                            "SELALU disertakan tanpa perlu argumen ini."),
                        },
                        "excel": {"type": "boolean", "description": "true → hasil juga jadi file Excel unduhan."},
                    },
                    "required": ["daftar_pn"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "stok_accurate",
                "description": (
                    "Rincian MENTAH satu Part Number di indeks ERP Accurate: "
                    "'stok_dapat_dijual', 'kuantitas', satuan, tipe barang, kode & nama "
                    "Accurate, 'stok_per_gudang' (qty per gudang/cabang). "
                    "⚠️ SUMBER STOKNYA SAMA PERSIS dengan detail_part — indeks Accurate "
                    "yang sama, angka yang sama. Ini BUKAN sumber kedua dan TIDAK bisa "
                    "dipakai untuk 'membandingkan'/'cross-check' stok: dua angka yang "
                    "berbeda tak akan pernah muncul, dan memanggil keduanya untuk PN yang "
                    "sama hanya membakar waktu. Pakai HANYA bila: (a) user minta rincian "
                    "mentah Accurate (kode/nama/tipe barang di Accurate, qty per gudang "
                    "apa adanya), atau (b) detail_part gagal/tidak mengembalikan stok. "
                    "Untuk pertanyaan stok biasa → detail_part (1 PN) / cek_massal_part "
                    "(≥2 PN) — keduanya membaca indeks Accurate yang sama, dan "
                    "detail_part baru turun ke Excel bila Accurate memang tak tersedia "
                    "(lihat field 'sumber_stok' di hasilnya)."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "part_number": {"type": "string", "description": "Part Number persis untuk dicek di Accurate."},
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
                    "KODE KESALAHAN / DTC truk Sinotruk-HOWO (kamus lokal, INSTAN): SPN+FMI, "
                    "kode DTC (P/B/U atau hex EV), atau kata kunci → arti gangguan + PENYEBAB + "
                    "LANGKAH PERBAIKAN resmi EOL (Bahasa Indonesia) + part terkait + lampu "
                    "MIL/SVS. Cakupan SEMUA unit kontrol: mesin (EMS), transmisi (TCU/ZF/AMT), "
                    "rem ABS/ESP/EBS, EV (BMS/VCU/MCU), BCM, airbag (ACU), radar/kamera ADAS, "
                    "SCR/AdBlue, dll — filter dengan 'unit' bila user menyebutnya. Termasuk tabel "
                    "rem ABS WABCO (SPN/FMI + Blink Code + langkah perbaikan) & SCR gas 国V (kode P) "
                    "— pakai unit='ABS' atau 'SCR'. ⛔ Untuk "
                    "KELUHAN/GEJALA bebas tanpa kode (mis. 'RPM tidak mau naik', 'asap hitam') "
                    "→ pakai tool `diagnosa` (asisten perbaikan resmi Sinotruk yang menalar). "
                    "URUTAN BAKU bila KODE-nya diketahui (termasuk 'apa penyebab kode X'): "
                    "cari_kode_kesalahan DULU — instan & sudah memuat penyebab + langkah "
                    "perbaikan resmi; `diagnosa` (20–90 dtk) hanya bila kamus lokal nihil "
                    "atau user minta penalaran lebih dalam."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "spn": {"type": "integer", "description": "Nomor SPN (Suspect Parameter Number)."},
                        "fmi": {"type": "integer", "description": "Nomor FMI (Failure Mode Identifier)."},
                        "code": {"type": "string", "description": "Kode DTC, mis. 'P0410', 'B1117', '18FFAAF3'. Kode pendek otomatis cocok keluarga (P0100 → P0100F7)."},
                        "query": {"type": "string", "description": "Kata kunci Indonesia bila kode tak diketahui, mis. 'tekanan rail', 'radar terhalang', 'tegangan sel'."},
                        "unit": {"type": "string", "description": "Filter unit kontrol (opsional): EMS, TCU, TCUZF, ESP, ABS, BMS, VCU, MCU, BCM, ACU, IFC, SCR, dll."},
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "diagram_wiring",
                "description": (
                    "DIAGRAM WIRING / definisi PIN sensor & aktuator mesin Bosch dan "
                    "sistem SCR/AdBlue truk Sinotruk (55 diagram resmi EOL) — gambar "
                    "tampil INLINE di chat. Pakai saat user minta diagram/skema kabel/"
                    "pin/konektor komponen, atau saat menelusuri gangguan KABEL/KONEKTOR "
                    "dari kode error (langkah perbaikan sering berbunyi 'periksa "
                    "rangkaian/kabel'). Contoh komponen: pedal gas (APP), sensor tekanan "
                    "rail, sensor suhu coolant, MAF/HFM, boost, turbo, EGR, DPF, kipas "
                    "radiator, katup dosis AdBlue/SCR, konektor OBD, jaringan CAN, "
                    "sensor kecepatan (VSS), camshaft, relay starter, cruise control. "
                    "JUGA menampilkan (dari manual pabrikan): SKEMA/PINOUT ECU Bosch "
                    "(MC National V, NBCU, NanoBCU, ZF-AMT — konektor & letak pin), "
                    "SKEMA PNEUMATIK REM ABS (traktor/rigid, WABCO), SKEMA KELISTRIKAN "
                    "HOHAN/HOWO N, dan FOTO UNIT alat berat Shantui per-model "
                    "(bulldozer/loader/excavator/grader, mis. 'foto SD16', 'gambar unit DH17')."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "komponen": {
                            "type": "string",
                            "description": "Nama komponen/sensor, mis. 'pedal gas', 'tekanan rail', 'coolant', 'adblue', 'OBD', 'kipas radiator'.",
                        },
                    },
                    "required": ["komponen"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "cari_manual",
                "description": (
                    "Cari ISI MANUAL teknik resmi (prosa & tabel) — untuk pertanyaan "
                    "'CARA/BAGAIMANA', arti indikator, nilai/kalibrasi, atau LANGKAH "
                    "troubleshooting sebuah GEJALA (bukan kode error). Dua sumber: "
                    "(1) manual servis ECU Bosch mesin MC — kartu gangguan per-gejala "
                    "(kondisi pemicu, kemungkinan penyebab, langkah pemeriksaan kabel/"
                    "konektor, tes setelah perbaikan); (2) manual instrumen TFT NanoBCU "
                    "— panel/dashboard, arti lampu indikator, tabel nilai sensor (rpm/"
                    "suhu air/tekanan oli), kalibrasi, kasus gangguan panel. Jawaban = "
                    "isi manual (⚠️ teks aslinya BAHASA CHINA — TERJEMAHKAN ke Indonesia "
                    "saat menjawab; jangan ubah angka/kode/pin) + gambar halaman tampil "
                    "INLINE + kartu PDF sumber. Pakai utk 'cara servis panel tft', 'arti "
                    "lampu X di dashboard', 'nilai sensor tekanan oli', 'cara cek gejala "
                    "cruise control macet'. Untuk KODE error SPN/FMI/P pakai cari_kode_kesalahan; "
                    "utk diagram/pin pakai diagram_wiring; untuk DIAGNOSA penyebab & langkah "
                    "PERBAIKAN gejala kompleks (asisten pabrik) pakai diagnosa."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "topik": {
                            "type": "string",
                            "description": "Topik/gejala dalam Bahasa Indonesia, mis. 'lampu indikator panel tft', 'nilai sensor tekanan oli', 'cruise control tombol macet', 'kalibrasi jarum rpm'.",
                        },
                    },
                    "required": ["topik"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "manual_unit",
                "description": (
                    "⭐ DAFTAR SERVICE MANUAL / buku perbaikan resmi milik SATU UNIT dari "
                    "NOMOR RANGKA (EPC Sinotruk), dicocokkan ke konfigurasi NYATA unit "
                    "(transmisi, gardan, kelistrikan): manual gearbox (mis. ZF Ecosplit), "
                    "buku gardan, skema kelistrikan per seri, lembar spesifikasi. Pakai utk "
                    "'apa saja service manual unit X', 'ada buku perbaikan rangka X', "
                    "'kirimkan manual servis truk X'. Hanya MENDAFTAR; PDF menyusul lewat "
                    "manual_unit_file setelah user memilih. Prosedur per GEJALA → cari_manual; "
                    "diagram pin/konektor → diagram_wiring."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "rangka": {
                            "type": "string",
                            "description": "Nomor rangka: VIN penuh atau frame number — KIRIM APA ADANYA dari user.",
                        },
                    },
                    "required": ["rangka"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "manual_unit_file",
                "description": (
                    "KIRIM BERKAS PDF satu service manual unit sbg kartu yang bisa dibuka "
                    "user. Panggil HANYA sesudah manual_unit DAN user meminta berkasnya "
                    "('kirim nomor 2', 'minta PDF manual gardan belakang'). ⛔ JANGAN "
                    "memborong semua dokumen — satu berkas bisa puluhan MB."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "rangka": {
                            "type": "string",
                            "description": "Nomor rangka unit, sama dengan yang dipakai di manual_unit.",
                        },
                        "nomor": {
                            "type": "integer",
                            "description": "Nomor urut dokumen dari daftar manual_unit ('nomor 2' → 2).",
                        },
                        "judul": {
                            "type": "string",
                            "description": "Bila user menyebut nama, bukan nomor — mis. 'gardan belakang', 'ZF', 'kelistrikan'.",
                        },
                    },
                    "required": ["rangka"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "cari_pengetahuan",
                "description": (
                    "Cari PENGETAHUAN INTERNAL MASPART yang ditulis/diunggah ADMIN: "
                    "kebijakan & prosedur perusahaan (retur, garansi, klaim, pengiriman, "
                    "pembayaran), panduan & catatan teknis internal, informasi produk di "
                    "luar katalog, serta ISI BERKAS yang diunggah admin (PDF/Excel/Word/"
                    "CSV/TXT) termasuk tabel dan gambar penjelasnya. Pakai bila pertanyaan "
                    "menyangkut ATURAN/PROSEDUR/KEBIJAKAN MASPART, atau hal yang tidak "
                    "dilayani tool katalog/stok/harga/manual pabrikan. Jawaban WAJIB "
                    "bersumber dari isi yang dikembalikan tool ini dan MENYEBUT judul "
                    "dokumen + berkas/halaman sumbernya. Untuk part/stok/harga pakai "
                    "cari_part; untuk kode error SPN/FMI/P pakai cari_kode_kesalahan; "
                    "untuk manual teknik pabrikan pakai cari_manual."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "topik": {
                            "type": "string",
                            "description": "Topik dalam Bahasa Indonesia, mis. 'prosedur retur barang', 'syarat klaim garansi', 'kebijakan ongkos kirim', 'panduan pemasangan'.",
                        },
                    },
                    "required": ["topik"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "diagnosa",
                "description": (
                    "⭐ DIAGNOSA KERUSAKAN — pakai untuk 'kenapa …', 'bagaimana cara "
                    "memperbaiki', atau KELUHAN/GEJALA truk ('RPM terkunci 1500', "
                    "'rem angin lemah', 'asap hitam'). "
                    "URUTAN BAKU bila KODE-nya diketahui (termasuk 'apa penyebab kode X'): "
                    "cari_kode_kesalahan DULU — instan & sudah memuat penyebab + langkah "
                    "perbaikan resmi; tool ini (20–90 dtk) hanya bila kamus lokal nihil "
                    "atau user minta penalaran lebih dalam. "
                    "Menggabungkan ASISTEN PERBAIKAN RESMI "
                    "SINOTRUK (SIMS EOL AI: manual perbaikan pabrik + kasus kerusakan nyata) "
                    "dengan kamus DTC lokal (arti kode + lampu MIL/SVS). Jawabannya memuat "
                    "definisi kerusakan, kemungkinan penyebab, dan langkah pemeriksaan. "
                    "⏳ Butuh 20–90 detik (pabrik menalar) — WAJAR; jangan ulangi panggilan. "
                    "⚠️ Bila SIMS menyatakan pengetahuannya belum memuat topik itu, sampaikan "
                    "JUJUR — ⛔ JANGAN mengarang penyebab/langkah dari pengetahuan umum. "
                    "Bila jawabannya menyebut komponen yang perlu diganti DAN user menyebut "
                    "nomor rangka, lanjutkan dengan cari_part_di_unit → PN + stok + harga. "
                    "(Untuk ISI MANUAL statis — arti lampu indikator, nilai/kalibrasi sensor, "
                    "tabel, langkah baca-manual — pakai cari_manual, BUKAN diagnosa.)"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "kode": {"type": "string", "description": "Kode kesalahan bila ada (mis. 'P0645')."},
                        "spn": {"type": "integer", "description": "SPN bila disebut user."},
                        "fmi": {"type": "integer", "description": "FMI bila disebut user."},
                        "keluhan": {"type": "string", "description": "Gejala/keluhan apa adanya dari user (mis. 'mesin RPM terkunci di 1500, tidak bisa naik')."},
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
                "name": "jadwal_perawatan",
                "description": (
                    "JADWAL PERAWATAN BERKALA (service/maintenance table) alat berat "
                    "SHANTUI — dozer, loader, EXCAVATOR, motor GRADER, & ROLLER. Contoh "
                    "model: dozer SD16/SD22/SD32/DH17, loader L36-B5/L55-B5/L68K-B5, "
                    "excavator SE60W/SE135W/SE215W/SE375W, grader SG15-B6/SG19-B6, roller "
                    "SR10-B6. Mengembalikan item yang DIGANTI tiap interval jam kerja "
                    "(50/100/250/500/…/2000/3000 jam): nama servis, NOMOR PART SHANTUI, "
                    "kuantitas, dan pada jam berapa saja diganti — per sistem (mesin, "
                    "transmisi/konverter, hidrolik, gardan, rem). Tiap hasil ditandai "
                    "'jenis' alat & 'varian' (emisi Euro II/III atau kode mesin WP6H) "
                    "karena satu kode model bisa punya beberapa varian. Pakai untuk "
                    "'part apa diganti saat servis 500 jam SE215W', 'nomor part filter "
                    "solar grader SG15-B6', 'jadwal servis 1000 jam dozer DH17', 'servis "
                    "berkala semua excavator'. ⛔ JANGAN pakai cari_part untuk ini."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "model": {
                            "type": "string",
                            "description": "Kode model unit Shantui, mis. SD22, SD16, SE215W, SG15-B6, L36-B5, DH17. Kosong = semua model.",
                        },
                        "jenis": {
                            "type": "string",
                            "description": "Jenis alat (opsional): dozer, loader, excavator, grader, atau roller. Berguna untuk 'jadwal servis semua excavator'. Kosong = semua jenis.",
                        },
                        "query": {
                            "type": "string",
                            "description": "Kata kunci jenis part/servis (opsional), mis. 'oli', 'solar', 'udara', 'hidrolik', 'coolant', 'transmisi', 'rem'. Kosong = semua item.",
                        },
                        "jam": {
                            "type": "integer",
                            "description": "Interval jam servis (opsional), mis. 250, 500, 1000, 2000, 3000 — hanya item yang diganti pada jam itu. Kosong = semua interval.",
                        },
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "tipe_unit_shantui",
                "description": (
                    "KATALOG RESMI EPC SHANTUI (alat berat: excavator, bulldozer, "
                    "loader, roller, grader, dll) — daftar semua TIPE/VARIAN untuk "
                    "sebuah kode model. Contoh: 'SE75' → 8 tipe (SE75-9, SE75-9B, "
                    "SE75-9W1, SE75-9W3, SE75-9W4, SE75-10, SE75-10W, SE75-G). Pakai "
                    "untuk 'ada berapa tipe SE75', 'varian SD22 apa saja', 'tipe "
                    "excavator SE215'. Membedakan varian sebenarnya dari model lain yg "
                    "kebetulan berawalan sama (SE75 vs SE750). Tiap tipe punya 'rootCode' "
                    "= identitas untuk lookup part (part_shantui). ⛔ Ini KATALOG PART "
                    "asli, BEDA dari cari_filter_shantui (filter) & jadwal_perawatan "
                    "(servis)."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "model": {
                            "type": "string",
                            "description": "Kode model Shantui, mis. 'SE75', 'SD22', 'L36', 'SR26'. Boleh sebagian.",
                        },
                    },
                    "required": ["model"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "part_shantui",
                "description": (
                    "DAFTAR PART satu TIPE unit Shantui dari katalog EPC resmi. Tanpa "
                    "'subsistem' → daftar ASSEMBLY utama tipe itu (mis. 83 assembly); "
                    "dengan 'subsistem' → ISI PART figure (nomor balon, Part Number, "
                    "nama, qty). Pakai untuk 'part mesin SE75-9W1', 'apa saja assembly "
                    "SE215W', 'bedanya SE75-9W1 vs SE75-9W3' (bandingkan per subsistem). "
                    "Sebut TIPE yang spesifik (pakai tipe_unit_shantui dulu bila hanya "
                    "tahu kode dasar). ⛔ JANGAN mengarang PN; harga/stok tidak di sini."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "tipe": {
                            "type": "string",
                            "description": "Tipe unit SPESIFIK, mis. 'SE75-9W1', 'SD22-C5', 'SE215W'. Bukan sekadar 'SE75'.",
                        },
                        "subsistem": {
                            "type": "string",
                            "description": "Opsional. Subsistem untuk melihat ISI part: kata kunci EN ('engine', 'main pump', 'main valve', 'hydraulic', 'track', 'cab', 'boom', 'arm', 'bucket', 'swing motor', 'travel motor', 'fuel', 'cooling', 'counterweight') atau kode YY ('03','24','45','61'). Kosong = daftar assembly saja.",
                        },
                    },
                    "required": ["tipe"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "cari_part_shantui",
                "description": (
                    "Cari NOMOR PART Shantui di katalog EPC resmi — global (semua alat "
                    "berat) atau dalam satu tipe. Balikkan nama part + berat/dimensi bila "
                    "ada. Pakai untuk 'part apa nomor 60070-03-00084', 'cek PN ... di "
                    "Shantui'. ⛔ harga/stok LOKAL tidak di sini (pakai tool stok/harga)."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "pn": {"type": "string", "description": "Nomor part Shantui yang dicari."},
                        "tipe": {
                            "type": "string",
                            "description": "Opsional. Batasi ke satu tipe unit (mis. 'SE75-9W1'). Kosong = cari global.",
                        },
                    },
                    "required": ["pn"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "gambar_exploded_shantui",
                "description": (
                    "Tampilkan GAMBAR EXPLODED VIEW alat berat SHANTUI (inline di chat, "
                    "label INGGRIS) untuk satu TIPE unit — disaring per subsistem "
                    "('engine'/'hydraulic'/'track'/'cab'/'boom'/kode YY) dan/atau PN "
                    "tertentu. Bisa menyorot nomor balon. Pakai untuk 'gambar exploded "
                    "mesin SE75-9W1', 'tampilkan diagram hidrolik SE215W', 'exploded "
                    "view part 60070-03-00084'. Gambar muncul sendiri; ⛔ jangan buat "
                    "link/gambar sendiri."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "tipe": {
                            "type": "string",
                            "description": "Tipe unit SPESIFIK, mis. 'SE75-9W1', 'SE215W'.",
                        },
                        "subsistem": {
                            "type": "string",
                            "description": "Opsional. Subsistem figure (kata kunci EN atau kode YY). Kosong = beberapa figure pertama tipe itu.",
                        },
                        "pn": {
                            "type": "string",
                            "description": "Opsional. Hanya figure yang MEMUAT PN ini (balonnya ikut disorot).",
                        },
                        "balon": {
                            "type": "integer",
                            "description": "Opsional. Nomor balon yang mau DISOROT kuning di gambar.",
                        },
                    },
                    "required": ["tipe"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "part_fast_moving",
                "description": (
                    "PART FAST MOVING / part aus rutin per MODEL unit Sinotruk "
                    "(HOWO/SITRAK/HOMAN) — filter, kampas/sepatu rem, kopling, "
                    "bearing & seal roda, belt, karet/rubber mount — TURUNAN katalog "
                    "EPC dari beberapa unit sampel se-model + data populasi, lengkap "
                    "dengan stok & harga lokal. Pakai untuk: 'part fast moving NX400', "
                    "'part yang sering diganti SITRAK C7H', 'part apa yang perlu "
                    "distok untuk armada HOWO 6X4', 'sparepart wajib sedia model X'. "
                    "Terima label pasaran (NX400 = NX + 400 HP), jenis (HOWO NX 8X4), "
                    "atau kode model pabrik (ZZ…). Hasil ambigu → tanyakan pilihan ke "
                    "user. "
                    "⭐ BISA PER NOMOR RANGKA, SATU ATAU BANYAK sekaligus: 'part fast "
                    "moving untuk unit LZZ…, LZZ…' → isi 'rangka' dengan SEMUA VIN yang "
                    "user sebut (jangan dipecah jadi beberapa panggilan). Model tiap "
                    "unit dicari sendiri dari EPC; unit se-model digabung jadi SATU "
                    "daftar, unit beda model jadi daftar gabungan yang menandai slot "
                    "mana dipakai SEMUA model (prioritas stok). "
                    "⚠️ Hasilnya tetap level MODEL (basis unit sampel, untuk perencanaan "
                    "stok/penawaran) — untuk PN pasti milik satu unit tertentu tetap "
                    "part_aus_dari_rangka/cari_part_di_unit."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "model": {
                            "type": "string",
                            "description": "Model/jenis unit sesuai ucapan user, mis. 'NX400', 'HOWO NX 6X4', 'SITRAK C7H', atau kode model 'ZZ3257V404JF1'. Kosongkan bila memakai 'rangka'.",
                        },
                        "rangka": {
                            "type": "array",
                            "description": ("Nomor rangka/VIN unit — SEMUA yang disebut user "
                                            "dalam SATU panggilan (maks 20). Dipakai bila user "
                                            "menyebut unitnya, bukan nama model."),
                            "items": {"type": "string"},
                        },
                        "kategori": {
                            "type": "string",
                            "description": "Opsional, saring satu kategori: filter | rem | kopling | bearing_seal | belt | karet. Kosong = semua.",
                        },
                        "excel": {
                            "type": "boolean",
                            "description": ("true bila user minta file/daftar lengkap (khusus "
                                            "jalur 'rangka' beda model — daftar chat dipotong "
                                            "60 slot, Excel memuat semuanya)."),
                        },
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "info_part",
                "description": (
                    "PENGETAHUAN MENDALAM sebuah part / KELUARGA part: fungsi part itu, "
                    "sistem & sub-sistemnya, gejala umum bila rusak, contoh PN katalog, "
                    "plus tautan ke jadwal perawatan/filter/manual/DTC yang menyebutnya. "
                    "Pakai untuk pertanyaan PEMAHAMAN: 'apa fungsi X', 'X itu bagian apa', "
                    "'kalau X rusak gejalanya apa', 'bedanya X dan Y'. ⛔ Untuk STOK/HARGA "
                    "tetap cari_part/detail_part; part per-UNIT tetap cek EPC via rangka."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "nama": {"type": "string", "description": "Nama part/keluarga (Indonesia/Inggris) — mis. 'filter oli', 'release bearing', 'kampas rem'."},
                        "pn": {"type": "string", "description": "Opsional: PN konkret — sistem mencari keluarganya dari nama katalog PN itu."},
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "tanya_user",
                "description": (
                    "BERTANYA BALIK ke user dengan PILIHAN — muncul sebagai kartu "
                    "bernomor yang bisa diklik (user juga tetap bisa mengetik bebas). "
                    "⚠️ Memanggil tool ini MENGAKHIRI giliranmu: jangan digabung "
                    "dengan tool lain, dan jangan menulis jawaban setelahnya.\n"
                    "PAKAI bila jawabanmu akan BERBEDA ARAH tergantung info yang "
                    "belum kamu punya DAN info itu tak bisa didapat dari tool — mis. "
                    "posisi (depan/belakang), unit yang mana, tujuan (cuma tanya vs "
                    "mau beli), atau gejala mana yang dialami.\n"
                    "⛔ JANGAN dipakai: (1) untuk hal yang bisa dicari sendiri lewat "
                    "tool (stok/harga/BOM/kode kesalahan) — kerjakan dulu; (2) sebelum "
                    "mencoba satu tool pun, kecuali data wajibnya memang belum ada "
                    "(mis. part per-unit tanpa nomor rangka); (3) dua giliran "
                    "berturut-turut; (4) untuk hal yang tak mengubah tindakanmu. "
                    "Bertanya BUKAN pengganti bekerja — kalau ragu tapi masih ada "
                    "yang bisa dicoba, coba dulu.\n"
                    "⛔ JANGAN membuat opsi 'Lainnya'/'Lewati'/'Terserah' — tampilan "
                    "sudah menyediakannya."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "pertanyaan": {
                            "type": "array",
                            "description": "1-3 pertanyaan. Biasanya CUKUP SATU.",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "teks": {"type": "string", "description": "Pertanyaannya, singkat & jelas (maks ~120 char) — mis. 'Kampas rem posisi mana?'."},
                                    "opsi": {
                                        "type": "array", "items": {"type": "string"},
                                        "description": "2-4 pilihan SINGKAT (maks ~60 char each) — mis. ['Depan','Belakang','Belum tahu, cek dari rangka'].",
                                    },
                                },
                                "required": ["teks", "opsi"],
                            },
                        },
                    },
                    "required": ["pertanyaan"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "foto_resmi_part",
                "description": (
                    "FOTO RESMI SIMS untuk 1–3 Part Number → tampil INLINE di jawaban. "
                    "⛔ HANYA saat user MEMINTA foto/gambar secara eksplisit — mis. "
                    "'perlihatkan/tampilkan fotonya', 'ada fotonya?', 'PN X bentuknya "
                    "seperti apa', 'kirim gambar part ini'. Foto TIDAK auto-nempel di "
                    "tiap cek part: ⛔ JANGAN memanggil tool ini atas inisiatif sendiri "
                    "saat menyebut PN, menyajikan hasil cari_part/detail_part, atau saat "
                    "kamu merasa user perlu memastikan bentuknya — TUNGGU user meminta. "
                    "⛔ Jangan pula menawarkannya berulang di akhir jawaban. "
                    "⛔ Bukan sumber stok/harga (pakai cari_part/detail_part)."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "part_number": {
                            "type": ["string", "array"], "items": {"type": "string"},
                            "description": "1–3 PN — array ATAU string dipisah koma/baris.",
                        },
                        "maks_per_part": {
                            "type": "integer",
                            "description": "Jumlah foto per PN (1–2, default 2).",
                        },
                    },
                    "required": ["part_number"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "repair_kit_transmisi",
                "description": (
                    "REPAIR KIT / perpak TRANSMISI (gearbox) per model — SEAL KIT (oil seal "
                    "+ gasket + O-ring) dan opsional OVERHAUL (bearing + synchronizer + snap "
                    "ring). Model dikenali dari kode (HW19709, ZF16S2531TO, 8JS85), PN "
                    "gearbox assy, ATAU nama UNIT ('HOWO-371'). ⭐ Ada NOMOR RANGKA → isi "
                    "'rangka': sistem menanyakan gearbox PERSIS unit itu ke EPC (dua unit "
                    "'sama' bisa beda gearbox). Untuk 'repair kit/perpak/seal kit/paking "
                    "transmisi', 'apa saja diganti saat overhaul gearbox'. Kosongkan "
                    "'transmisi' & 'rangka' = daftar model. REPAIR KIT MESIN Weichai: "
                    "sumber='mesin' + 'rangka'."
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
                            "description": "Nomor rangka/VIN unit (bila user menyebutnya) — gearbox di-resolve PERSIS dari EPC Sinotruk per-VIN, mengalahkan 'transmisi'. WAJIB utk sumber='mesin'.",
                        },
                        "tingkat": {
                            "type": "string",
                            "enum": ["seal_kit", "overhaul", "semua"],
                            "description": "'seal_kit' = perpak (seal+gasket+O-ring, default), 'overhaul' = bearing+synchronizer+snap ring, 'semua' = keduanya.",
                        },
                        "sumber": {
                            "type": "string",
                            "enum": ["transmisi", "mesin"],
                            "description": "'transmisi' (default) = repair kit gearbox; 'mesin' = repair kit MESIN Weichai per-VIN.",
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
                    "saja isi dalam HW19709XST201136', 'komponen gardan PN ini'. "
                    "⛔ PAKAI uraikan_assembly (BUKAN ini) bila: user menyebut NOMOR RANGKA/"
                    "VIN, butuh STOK/HARGA komponen, atau menyebut assembly via NAMA/istilah "
                    "lapangan (v-stay/thrust rod/tie rod). isi_assy = komposisi KATALOG dari "
                    "PN assy, TANPA konteks unit & tanpa stok/harga."
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
                    "Daftar part satu KATEGORI per-MODEL truk dari sheet katalog (mis. "
                    "'NX400 6X4') — perkiraan per-model, BUKAN per-unit. ⛔ Bila user "
                    "menyebut NOMOR RANGKA/VIN (part PASTI unit itu), pakai part_aus_dari_"
                    "rangka / cari_part_di_unit / bom_dari_rangka; untuk KATALOG bergambar "
                    "unit → katalog_kategori. Kategori: kabin, mesin, kopling, transmisi, "
                    "gardan/axle, kelistrikan, rem, sasis, karoseri, dll. Contoh: 'part REM "
                    "apa saja di NX400?', 'komponen kelistrikan V7X400'."
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
                "name": "kategori_massal_part",
                "description": (
                    "⭐ KATEGORI BARANG untuk BANYAK Part Number sekaligus (SATU panggilan, "
                    "instan, tanpa jaringan) — menjawab 'PN ini masuk kategori barang apa?', "
                    "'mana dari daftar ini yang termasuk BARANG MESIN?', 'manakah yang produk "
                    "WEICHAI / bukan HOWO?'. Kategorinya BUKAN tafsiran dari nama part: "
                    "diambil dari SHEET katalog resmi tempat PN itu benar-benar terdaftar "
                    "(01 Kabin, 02 Mesin/Powertrain, 03 Aksesori powertrain, 04 Kopling, "
                    "05 Transmisi, 06 Poros penumpu/depan, 07 Poros penggerak/belakang, "
                    "08 Kelistrikan, 09 Rem, 10 Sasis, 11 Lainnya, 12 Karoseri) plus FILE "
                    "katalognya (katalog MESIN Weichai vs katalog unit Sinotruk/HOWO vs "
                    "Shantui). Hasilnya: kategori per PN + 'ringkasan' berisi daftar PN "
                    "mesin / aksesori_mesin / bukan_mesin / tidak_diketahui, siap dijawab "
                    "langsung. ⛔ WAJIB dipakai untuk pertanyaan kategori/'barang mesin'/"
                    "'produk Weichai' — jangan menebak dari nama part (tebakan nama terbukti "
                    "salah: 'bushing suspensi' ternyata terdaftar di sheet Kabin). "
                    "PN yang tak ada di katalog dikembalikan 'tidak_diketahui' — sampaikan "
                    "apa adanya. Set excel=true bila user minta filenya. "
                    "⛔ Ini TIDAK memberi stok/harga/berat — untuk itu cek_massal_part."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "daftar_pn": {
                            "type": "array", "items": {"type": "string"},
                            "description": "Daftar Part Number (boleh juga satu string dipisah baris/koma).",
                        },
                        "excel": {"type": "boolean", "description": "true → hasil juga jadi file Excel unduhan."},
                    },
                    "required": ["daftar_pn"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "spek_mesin",
                "description": (
                    "⭐ SPESIFIKASI MESIN Weichai unit tertentu dari NOMOR RANGKA (VIN) "
                    "atau NOMOR MESIN — data resmi pabrik untuk konfigurasi mesin unit ITU, "
                    "bukan angka umum seri. Mengembalikan: KAPASITAS OLI MESIN (liter) + "
                    "grade olinya, model & seri mesin, DAYA (kW), standar EMISI (Euro II/"
                    "China V dll), bahan bakar, pabrik perakit, DAN 'part_pabrik' = PN "
                    "FILTER PILIHAN PABRIK untuk konfigurasi itu (filter oli, filter solar "
                    "halus & kasar, paket perawatan, paket gasket, part inti). "
                    "WAJIB dipakai untuk: 'berapa liter oli mesin unit ini', 'oli mesinnya "
                    "pakai apa/berapa', 'spesifikasi mesin unit X', 'berapa daya/HP mesinnya', "
                    "'euro berapa', dan 'filter oli/solar yang cocok untuk unit ini'. "
                    "⛔ JANGAN menjawab kapasitas oli dari ingatan/perkiraan — angka ini "
                    "berbeda per konfigurasi mesin. ⛔ Tool ini TIDAK memuat INTERVAL servis "
                    "(km/jam): bila user menanyakannya, katakan tak tercatat di data ini. "
                    "Hanya untuk unit bermesin WEICHAI (bukan mesin MC/Sinotruk sendiri); "
                    "unit non-Weichai akan dijawab 'tidak punya link EPC Weichai'."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "rangka": {"type": "string", "description": "Nomor rangka/VIN unit (boleh 8 digit akhir)."},
                        "no_mesin": {"type": "string", "description": "Nomor mesin (serial), bila user menyebut ini alih-alih rangka."},
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "jadwal_servis_truk",
                "description": (
                    "⭐ JADWAL SERVIS berbasis KILOMETER + KAPASITAS CAIRAN resmi pabrik "
                    "(CNHTC) untuk TRUK Sinotruk. Menjawab: 'servis 40.000 km apa saja', "
                    "'berapa liter oli mesin/coolant/oli gardan/oli transmisi', 'minyak "
                    "kopling berapa', 'oli power steering apa', 'kapan ganti oli gardan'. "
                    "Isi terverifikasi: oli mesin CH-4 15W/40 23 L; COOLANT 40-45 L "
                    "(ASTM D3306, konsentrasi 40-60%); oli transmisi GL-5 85W/90 12-12,5 L "
                    "ganti 60.000 km; gardan MCY13 18 L (poros tengah) & 14,5 L (belakang); "
                    "power steering ATF III 5 L; minyak kopling DOT-3/4 0,5 L. "
                    "Isi 'km' untuk daftar pekerjaan pada jarak tempuh itu, dan/atau "
                    "'cairan' untuk kapasitas saja (terima istilah Indonesia: 'oli gardan', "
                    "'air radiator', 'kopling'). "
                    "⛔ CAKUPAN TERBATAS pada HOWO 371HP (gardan MCY13) — JANGAN "
                    "digeneralisasi ke NX/SITRAK/V7X/HOMAN; sebutkan batas ini saat "
                    "menjawab. ⛔ Untuk ALAT BERAT Shantui pakai jadwal_perawatan "
                    "(berbasis JAM), bukan tool ini. ⛔ Kapasitas oli MESIN per unit "
                    "bermesin Weichai lebih presisi lewat spek_mesin (per nomor mesin)."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "km": {"type": "integer", "description": "Jarak tempuh (mis. 40000). Kosong = hanya kapasitas cairan."},
                        "cairan": {"type": "string", "description": "Opsional: 'coolant', 'oli gardan', 'transmisi', 'kopling', 'steering'. Kosong = semua."},
                        "gardan": {"type": "string", "description": "Opsional: MODEL gardan unit (MCY11/MCY13/AC16/AC26/HW16) untuk kapasitas oli gardan yang TEPAT. Model gardan ada di hasil cek_kendaraan."},
                        "torsi": {"type": "boolean", "description": "true → sertakan torsi mur roda resmi."},
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "part_aus_katalog",
                "description": (
                    "⭐ DAFTAR PART RAWAN RUSAK / habis pakai / perawatan sebuah UNIT atau "
                    "MODEL — dari KOLOM KETERANGAN katalog resmi pabrik ('Wearing parts' = "
                    "易损件, 'Consumable parts', '保养件'), BUKAN tafsiran nama part. Pakai "
                    "bila user minta DAFTARNYA tanpa menyebut part tertentu: 'part apa saja "
                    "yang aus/mudah rusak di unit ini', 'daftar part cepat habis', 'part "
                    "consumable/perawatan model X'. "
                    "Dengan 'rangka' → PALING PRESISI: daftar part yang BENAR-BENAR "
                    "terpasang di unit itu (BOM pabrik EPC) disilang cap katalog, lengkap "
                    "dengan qty per unit. Dengan 'unit' saja → daftar per MODEL. "
                    "Kelompoknya ⛔ JANGAN digabung: RAWAN RUSAK (易损件 — termasuk PECAH & "
                    "MATI: kaca depan, saklar, lampu) vs HABIS PAKAI (ban, karet, wiper) vs "
                    "PERAWATAN vs 'sering dipakai after-sales' (fast moving — ⛔ BUKAN "
                    "penanda rusak). ⚠️ Katalog TIDAK mencap semua part: kampas rem/kopling/"
                    "filter sering tak muncul — ⛔ jangan menjanjikan daftar ini lengkap; "
                    "untuk part itu pakai part_aus_dari_rangka. "
                    "⛔ BEDA dari part_aus_dari_rangka: yang itu untuk SATU part spesifik "
                    "pada SATU VIN dengan posisi depan/belakang (mis. 'kampas rem depan "
                    "unit X') dan menelusuri EPC; yang INI daftar borongan dari katalog."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "unit": {"type": "string", "description": "Nama unit/model (mis. 'NX360', 'HOWO-380')."},
                        "rangka": {"type": "string", "description": "Alternatif: nomor rangka; dipetakan ke model lewat populasi."},
                        "kelompok": {
                            "type": "string",
                            "enum": ["aus", "habis_pakai", "perawatan", "sering_dipakai"],
                            "description": "Opsional: batasi ke satu kelompok saja.",
                        },
                        "sertakan_penanda_model_lain": {
                            "type": "boolean",
                            "description": ("Hanya bila user MEMINTA daftar diperluas. Default "
                                            "penanda diambil dari katalog model unit itu saja; "
                                            "menyalakan ini menambahkan part yang dicap katalog "
                                            "model LAIN — daftarnya jadi jauh lebih panjang dan "
                                            "kurang tepat (pemantik rokok/relay bisa ikut)."),
                        },
                    },
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
                        "rangka": {"type": ["string", "array"], "items": {"type": "string"}, "description": "VIN penuh (mis. LZZ5DMSD5RT108966) atau frame 8 digit (mis. RT108966). ARRAY = banyak unit SEKALIGUS, maks 20 — jangan panggil berulang."},
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
                "name": "cari_part_di_unit",
                "description": (
                    "⭐ JALUR UTAMA saat user menyebut NOMOR RANGKA + NAMA PART ('kampas "
                    "rem SJ346500'). Pencarian nama DI KATALOG EPC UNIT ITU: cepat (~1-2 "
                    "dtk), menjangkau part TERSEMBUNYI di dalam assembly (yang dilewatkan "
                    "bom_dari_rangka). Istilah lapangan ID otomatis diterjemahkan. Tiap PN "
                    "ber-'di_dalam_assembly' + stok/harga. Beberapa varian (DEPAN vs "
                    "BELAKANG) → sebutkan SEMUA, bedakan via assembly induk (pemisahan "
                    "posisi eksplisit: part_aus_dari_rangka). ⚠️ Hasil kosong auto-eskalasi "
                    "TELITI; hasil ada tapi part diminta tak termuat (cuma bracket/baut) → "
                    "panggil ulang teliti=true (sisir semua baris; pertama ~1 mnt). "
                    "⭐ BEBERAPA part / istilah alternatif → kirim SEMUA di kata_kunci "
                    "(array) dalam SATU panggilan; ⛔ JANGAN panggil tool ini berulang "
                    "per istilah."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "rangka": {"type": ["string", "array"], "items": {"type": "string"}, "description": "Nomor rangka/VIN unit. ARRAY = banyak unit SEKALIGUS, maks 20 — jangan panggil berulang."},
                        "kata_kunci": {"type": ["string", "array"], "items": {"type": "string"}, "description": "Nama part yang dicari — KIRIM istilah user APA ADANYA (kamus sinonim lapangan diterapkan otomatis di server; ⛔ JANGAN terjemahkan/tebak padanan Inggris sendiri sebelum mencoba mentahnya). Istilah Indonesia/Inggris/PN sama-sama boleh — mis. 'kampas rem', 'filter oli'. BOLEH ARRAY berisi beberapa part/istilah sekaligus (dicari sekali jalan, hasil dilabeli per istilah) — mis. ['handle retarder','tuas retarder'] atau ['filter oli','filter solar']."},
                        "teliti": {"type": "boolean", "description": "true = sisir SEMUA baris part list pohon unit (lambat pencarian pertama, cakupan penuh). Pakai saat hasil mode cepat tidak memuat part yang diminta."},
                    },
                    "required": ["rangka", "kata_kunci"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "filter_unit",
                "description": (
                    "⭐ DAFTAR FILTER LENGKAP satu unit dari NOMOR RANGKA — "
                    "ELEMENT/cartridge yang benar-benar diganti saat servis (BUKAN "
                    "housing/assembly), terkelompok per jenis (oli mesin, solar "
                    "halus/kasar, udara, power steering, AC kabin, urea, gardan, "
                    "transmisi) + varian pemasok + stok/harga. Unit bermesin Weichai "
                    "(WP): element oli & solar mesin otomatis diambil dari EPC "
                    "Weichai. Pakai untuk: 'cek filter <rangka>', 'filter apa saja di "
                    "unit ini', 'daftar filter servis unit'. ⛔ Pertanyaan filter "
                    "MENYELURUH jangan cari_part_di_unit (indeks cepatnya terbukti "
                    "memberi housing/assembly, bukan element); cari_part_di_unit tetap "
                    "utk part non-filter & SATU istilah spesifik. ⚠️ Bukan "
                    "part_fast_moving (itu level MODEL). Panggilan pertama per unit "
                    "±1 mnt (indeks dibangun), berikutnya instan."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "rangka": {"type": "string", "description": "Nomor rangka/VIN unit."},
                    },
                    "required": ["rangka"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "part_kolong",
                "description": (
                    "⭐ DAFTAR PART KOLONG (undercarriage/bagian bawah) satu unit dari "
                    "NOMOR RANGKA — terkelompok per SISTEM: rem, setir, transmisi/"
                    "transfer case/kopling, gardan, kopel, suspensi & shock absorber, "
                    "roda & ban, rangka/chassis, tangki BBM & knalpot, dudukan mesin — "
                    "plus stok/harga. Pengelompokan memakai KATEGORI RESMI pohon unit di "
                    "EPC (bukan tebakan dari nama part). Pakai untuk: 'cek part kolong "
                    "<rangka>', 'part bawah/sasis unit ini', 'daftar part suspensi & rem "
                    "unit ini', dan permintaan Excel-nya (excel=true). Bisa dipersempit "
                    "lewat 'sistem'. ⛔ Pertanyaan kolong MENYELURUH jangan dijawab dari "
                    "bom_dari_rangka (list DATAR tanpa kelompok; PN struktural di Loading "
                    "List kerap assembly usang) maupun cari_part_di_unit (itu untuk SATU "
                    "istilah spesifik). ⚠️ Isi dalam gardan/transmisi (kampas rem, tromol, "
                    "hub, bearing roda, seal, as roda, brake chamber) TIDAK ADA di EPC "
                    "per-VIN — tool menandainya 'assembly beli-jadi'; sampaikan batas itu "
                    "apa adanya, JANGAN dikarang."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "rangka": {"type": "string", "description": "Nomor rangka/VIN unit."},
                        "sistem": {
                            "type": ["string", "array"], "items": {"type": "string"},
                            "description": "Opsional. Persempit ke sistem tertentu: rem, setir, transmisi, gardan, kopel, suspensi, roda, rangka, bbm_knalpot, dudukan. KOSONGKAN untuk semua sistem kolong.",
                        },
                        "sertakan_baut": {"type": "boolean", "description": "true = ikutkan baut/mur/ring/paku keling. Default disembunyikan dari daftar (jumlahnya tetap dilaporkan) supaya daftar belanja terbaca."},
                        "excel": {"type": "boolean", "description": "true = buat file Excel (kartu unduh) berisi SELURUH baris termasuk pengencang."},
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
                    "Daftar PART (BOM pabrik/Loading List) SATU unit dari NOMOR RANGKA/VIN "
                    "— EPC Sinotruk resmi, PERSIS unit itu. Pakai utk 'part apa saja di "
                    "unit rangka X', ringkasan & 'kategori_breakdown' (jumlah part per "
                    "kategori unit INI; arg 'kategori' = daftar satu kategori). Filter "
                    "kata_kunci (istilah ID/EN/PN). Tiap part disilangkan stok & harga. "
                    "HANYA Sinotruk/HOWO/SITRAK. ⛔ BUKAN utk mencari SATU nama part "
                    "('kampas rem rangka X') — list DATAR bisa 0 padahal part ada di dalam "
                    "assembly → pakai cari_part_di_unit. ⚠️ Utk PN assembly STRUKTURAL "
                    "(pegas daun/suspensi/bracket/poros/rem) UTAMAKAN part_aus_dari_rangka "
                    "(Atlas terstruktur); Loading List kadang memuat PN assembly usang."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "rangka": {"type": ["string", "array"], "items": {"type": "string"}, "description": "VIN penuh atau frame 8 digit (mis. SJ346500). ARRAY = banyak unit SEKALIGUS, maks 20 — jangan panggil berulang."},
                        "kata_kunci": {"type": "string", "description": "Opsional. Saring part berdasar nama/PN (mis. 'injector', 'oil seal', 'WG9')."},
                        "kategori": {"type": "string", "description": "Opsional. Saring ke satu kategori untuk unit ini (mis. 'kabin', 'rem', 'transmisi', 'kelistrikan', 'sasis'). Untuk 'berapa/part apa di <kategori> unit ini'."},
                        "sisi": {"type": "string", "enum": ["kanan", "kiri", "depan", "belakang", "atas", "bawah"],
                                 "description": "Opsional. Isi bila user minta SISI tertentu (mis. 'spion KANAN') — sistem memfilter dari penanda RH/LH/FRONT/REAR di nama part. Tiap hasil juga punya field 'posisi' bila terdeteksi."},
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
                "name": "banding_rangka_massal",
                "description": (
                    "BANDINGKAN PART BANYAK UNIT (≥2) sekaligus — 'apakah KABIN semua unit PT X "
                    "SAMA?', 'bandingkan rem unit A, B, C'. Input: daftar rangka (rangka_list) "
                    "ATAU nama customer/PT (armada dari populasi; admin/'mas' saja). 'kategori' "
                    "= satu kategori (kabin/rem/mesin/transmisi/kopling/kelistrikan/sasis/"
                    "gardan) atau 'semua' untuk ringkasan. Membandingkan SET PART NYATA tiap "
                    "unit (Loading List per-VIN), mengelompokkan unit ber-set identik, verdict "
                    "SERAGAM/BEDA dihitung SISTEM + kartu Excel. ⛔ Beda dari banding_rangka "
                    "(2 unit) & banding_part_armada (1 part). ⛔ Jangan menyimpulkan sama/beda "
                    "dari kode model. HANYA Sinotruk/HOWO/SITRAK."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "rangka_list": {
                            "type": "array", "items": {"type": "string"},
                            "description": "Daftar nomor rangka/VIN unit yang mau dibandingkan (>=2). "
                                           "Pakai ini bila user menyebut/menempel beberapa VIN.",
                        },
                        "customer": {
                            "type": "string",
                            "description": "Alternatif rangka_list: nama customer/PT (mis. 'PT ARGCIO') "
                                           "— unit diambil dari data populasi. Admin/'mas' saja.",
                        },
                        "kategori": {
                            "type": "string",
                            "description": "Kategori yang dibandingkan (mis. 'kabin', 'rem', 'mesin', "
                                           "'transmisi', 'kelistrikan', 'sasis', 'gardan'). Isi 'semua' "
                                           "untuk ringkasan SELURUH kategori.",
                        },
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "part_aus_dari_rangka",
                "description": (
                    "PART persis SATU unit dari NOMOR RANGKA/VIN via EPC PARTS ATLAS "
                    "(terstruktur resmi; modul dipilih otomatis): POROS/REM dipisah "
                    "DEPAN/BELAKANG (kampas, sepatu rem, baut/mur roda, hub, bearing, "
                    "seal), MESIN/POWERTRAIN (injector, rail, piston, klep, turbo, filter), "
                    "KOPLING, GEARBOX. WAJIB utk part aus/poros ber-POSISI per-VIN. ⛔ Bila "
                    "rangka ada, JANGAN jawab dari bom_dari_rangka (datar, tanpa posisi) "
                    "atau cari_part (per-model). Isi 'posisi' bila user minta satu sisi. "
                    "HANYA Sinotruk/HOWO/SITRAK."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "rangka": {"type": ["string", "array"], "items": {"type": "string"}, "description": "VIN penuh atau frame 8 digit. ARRAY = banyak unit SEKALIGUS, maks 12 — jangan panggil berulang."},
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
                    "Butuh NOMOR RANGKA (per-VIN). (Tanpa rangka & hanya butuh komposisi "
                    "katalog dari PN assy tanpa stok/harga → isi_assy.) "
                    "DUA SISI dalam satu tool (param 'sumber'): 'atlas' (default) = EPC Parts "
                    "Atlas Sinotruk/HOWO/SITRAK; 'mesin' = EPC WEICHAI utk PART MESIN unit "
                    "bermesin Weichai (WP12/WP13): part internal (blok, kruk as, piston, ring, "
                    "liner, kepala silinder, klep, noken, pompa oli/air, injector) DAN aksesori "
                    "menempel di mesin (kompresor angin, alternator, starter, turbocharger, pompa "
                    "injeksi, flywheel) — semua itu TIDAK ADA di EPC Sinotruk. Utk sumber='mesin': "
                    "isi 'assembly' dgn komponen mesin yg dicari (atau kosongkan utk daftar group "
                    "mesin). Auto: bila assembly tak ketemu di Atlas, sistem otomatis mencoba sisi "
                    "mesin Weichai (lihat 'sumber_dipakai')."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "rangka": {"type": "string", "description": "Nomor rangka: VIN penuh atau frame number 8 digit."},
                        "assembly": {"type": ["string", "array"], "items": {"type": "string"}, "description": "Assembly yang diurai — PN (mis. 'AZ000052000229') atau nama/istilah (mis. 'v stay', 'thrust rod'; sumber='mesin': 'piston', 'injector'). ARRAY = banyak assembly SEKALIGUS, maks 8 — jangan panggil berulang."},
                        "sumber": {"type": "string", "enum": ["atlas", "mesin"], "description": "Sisi EPC: 'atlas' (default, sasis/bodi Sinotruk) atau 'mesin' (EPC Weichai, part internal mesin). Kosongkan = atlas + auto-fallback mesin."},
                    },
                    "required": ["rangka", "assembly"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "turunan_assembly",
                "description": (
                    "TELUSURI TURUNAN (komponen di dalam) sebuah assembly dari PN-nya, LINTAS "
                    "MODEL — dipakai saat uraikan_assembly per-VIN GAGAL karena di pohon VIN itu "
                    "assembly hanya muncul UTUH tanpa rincian (leaf), padahal model LAIN memuat "
                    "breakdown-nya. Sistem mencari PN assembly ini secara global ke SEMUA model, "
                    "lalu mengambil daftar komponen dari model pertama yang punya rinciannya "
                    "(disilang stok/harga lokal + atribusi model sumber). Pakai untuk 'assembly "
                    "WG9925477132 isinya apa saja' saat unit user tak punya rinciannya. ⛔ Butuh PN "
                    "assembly (bukan nama). Bila user menyebut RANGKA + nama assembly, coba "
                    "uraikan_assembly DULU; tool ini fallback saat itu tak beranak."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "pn": {"type": "string", "description": "PN assembly yang mau ditelusuri turunannya (mis. 'WG9925477132')."},
                    },
                    "required": ["pn"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "part_dari_mesin",
                "description": (
                    "CARI PART DI MESIN WEICHAI langsung dari NOMOR MESIN (serial engine, mis. "
                    "'4P24B000713') — TANPA perlu nomor rangka/VIN. Untuk 'carikan starter untuk "
                    "no engine 4P24B000713', 'part injector mesin nomor X', 'BOM mesin dari nomor "
                    "engine Y'. Beda dari uraikan_mesin (yang butuh RANGKA unit Sinotruk): tool ini "
                    "masuk EPC Weichai LANGSUNG dari serial mesinnya. Tanpa 'part' → daftar GROUP "
                    "mesin + model engine (mis. WP4G130E22); dengan 'part' → komponen yang cocok + "
                    "stok/harga lokal. Hanya untuk mesin merek WEICHAI (WP/WD/WP-series). ⛔ JANGAN "
                    "mengarang PN."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "no_mesin": {"type": ["string", "array"], "items": {"type": "string"}, "description": "Serial engine Weichai (mis. '4P24B000713'). ARRAY = banyak mesin SEKALIGUS, maks 20 — jangan panggil berulang."},
                        "part": {"type": "string", "description": "Opsional: nama komponen yang dicari (mis. 'starter', 'injector', 'piston', 'filter oli'). Kosong = daftar group mesin."},
                    },
                    "required": ["no_mesin"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "cek_massal_part_mesin",
                "description": (
                    "SATU PART (mis. 'starter', 'alternator', 'filter oli') × BANYAK NOMOR "
                    "MESIN Weichai sekaligus. Mesin ber-konfigurasi sama diproses sekali; "
                    "deteksi PENGGANTI otomatis → 'pn_order_terkini' (PN resmi terbaru untuk "
                    "dipesan) + silang stok/harga lokal; excel=true untuk kartu unduh."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "daftar_no_mesin": {"type": ["string", "array"], "items": {"type": "string"}, "description": "Daftar nomor mesin (array ATAU string dipisah baris/koma) — mis. ['4P24B000713','4P25G002767']."},
                        "part": {"type": "string", "description": "Komponen yang dicek di semua mesin (mis. 'starter', 'alternator', 'filter oli')."},
                        "excel": {"type": "boolean", "description": "true = buat file Excel (kartu unduh) berisi tabel per nomor mesin."},
                    },
                    "required": ["daftar_no_mesin", "part"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "cek_massal_part_rangka",
                "description": (
                    "SATU PART × BANYAK NOMOR RANGKA (VIN) sekaligus — EPC Sinotruk Atlas "
                    "per-VIN, untuk 'cek injector untuk rangka A, B, C, …'. Unit "
                    "ber-konfigurasi sama diproses sekali; deteksi pengganti otomatis "
                    "('pn_order_terkini') + silang stok/harga lokal; excel=true untuk kartu "
                    "unduh. Istilah Indonesia diterjemahkan otomatis. ⛔ Jangan tertukar: "
                    "cek_massal_part = banyak PN tanpa unit; cek_massal_part_mesin = daftar "
                    "NOMOR MESIN Weichai; yang INI = satu komponen × banyak unit."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "daftar_rangka": {"type": ["string", "array"], "items": {"type": "string"}, "description": "Daftar nomor rangka/VIN (array ATAU string dipisah baris/koma) — mis. ['LZZ1BG3H0SJ398963','LZZ1BG3H1SJ398969']."},
                        "part": {"type": "string", "description": "NAMA komponen yang dicek di semua unit — SATU komponen saja, istilah user apa adanya (mis. 'injector', 'kampas rem', 'filter oli'); istilah lapangan Indonesia diterjemahkan otomatis di server. ⚠️ Pencariannya lewat indeks NAMA katalog EPC per-unit: mengisi Part Number di sini bukan jalur yang dirancang (PN unit lain belum tentu terindeks sbg kata cari). Punya DAFTAR PN dan ingin stok/harganya → cek_massal_part."},
                        "excel": {"type": "boolean", "description": "true = buat file Excel (kartu unduh) berisi tabel per nomor rangka."},
                    },
                    "required": ["daftar_rangka", "part"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "spek_massal_rangka",
                "description": (
                    "SPEK/KONFIGURASI BANYAK UNIT dari daftar NOMOR RANGKA — model, seri, "
                    "gerak (4×2/6×2/6×4), jenis, emisi, rem/ABS, mesin, gearbox, gardan (EPC "
                    "getVehicleConfig); excel=true utk kartu unduh. ⚠️ SPEK, bukan daftar "
                    "part (itu cek_massal_part_rangka/bom_dari_rangka)."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "daftar_rangka": {"type": ["string", "array"], "items": {"type": "string"}, "description": "Daftar nomor rangka/VIN (array ATAU string dipisah baris/koma)."},
                        "excel": {"type": "boolean", "description": "true = buat file Excel (kartu unduh)."},
                    },
                    "required": ["daftar_rangka"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "banding_konfigurasi_rangka",
                "description": (
                    "BANDINGKAN KONFIGURASI/SPESIFIKASI banyak unit (≥2 nomor rangka): field "
                    "mana yang SAMA di semua unit, mana yang BERBEDA (model/gerak/jenis/rem/"
                    "gearbox/gardan/dll), dan unit dikelompokkan per konfigurasi identik. "
                    "Untuk 'apa perbedaan unit-unit ini' saat maksudnya SPEK. ⚠️ PELENGKAP "
                    "banding_rangka_massal: yang itu membandingkan SET PART nyata (pakai itu "
                    "utk 'partnya sama/beda?'); yang INI membandingkan spesifikasi — spek "
                    "sama ≠ part pasti sama. Set excel=true utk kartu unduh."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "daftar_rangka": {"type": ["string", "array"], "items": {"type": "string"}, "description": "Daftar nomor rangka/VIN, minimal 2 (array ATAU string dipisah baris/koma)."},
                        "excel": {"type": "boolean", "description": "true = buat file Excel (kartu unduh)."},
                    },
                    "required": ["daftar_rangka"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "pengganti_part",
                "description": (
                    "PERSAMAAN/PENGGANTI (supersession) part — jawab 'PN ini diganti nomor berapa?', "
                    "'part X sudah diskontinu, gantinya apa?', 'persamaan PN Y'. Cek DUA sumber resmi "
                    "sekaligus (global by PN, tak perlu rangka): (1) SIMS Sinotruk/HOWO — tabel "
                    "penggantian part SASIS/bodi (17rb+ relasi, dua-arah: PN lama→baru & sebaliknya); "
                    "(2) EPC Weichai 替换/ECN untuk part MESIN. Mengembalikan 'digantikan_oleh' (PN "
                    "pengganti baru) + 'menggantikan' (PN lama), disilang ke stok/harga lokal supaya "
                    "tahu mana yang ready. Berlaku untuk PN SASIS Sinotruk (HD/WG/AZ/LZ…) MAUPUN PN "
                    "mesin Weichai (numerik)."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "part_number": {"type": ["string", "array"], "items": {"type": "string"}, "description": "PN yang dicek penggantinya (mis. 'FG7101204246+001/1', '1000076563'). ARRAY = banyak PN SEKALIGUS, maks 30 — jangan panggil berulang."},
                        "rangka": {"type": "string", "description": "Opsional. Nomor rangka unit (untuk mengaktifkan sesi Weichai bila mengecek part mesin)."},
                    },
                    "required": ["part_number"],
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
                        "part_number": {"type": ["string", "array"], "items": {"type": "string"}, "description": "PN yang dicek dipakai di unit/model apa (mis. AZ1646901003). ARRAY = banyak PN SEKALIGUS, maks 20 — jangan panggil berulang."},
                    },
                    "required": ["part_number"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "katalog_kategori",
                "description": (
                    "KATALOG PART BERGAMBAR (exploded view) satu KATEGORI untuk SATU unit dari "
                    "NOMOR RANGKA — panggil saat user minta 'berikan/buatkan katalog <kategori> "
                    "<rangka>', 'katalog kabin unit X', 'catalog rem + gambar', 'buku part "
                    "transmisi unit ini'. Menyusun SEMUA part kategori itu per-figure, LENGKAP "
                    "dengan gambar exploded view resmi EPC + nomor balon + stok/harga lokal, "
                    "menjadi FILE EXCEL (kartu unduh muncul otomatis). Kategori: kabin, mesin, "
                    "kopling, transmisi, gardan depan/belakang, kelistrikan, rem, sasis, dll. "
                    "Kolom Stok & Harga SELALU KOSONG di file (default) — hanya terisi bila "
                    "ADMIN secara eksplisit meminta stok/harga disertakan. Proses ±1 menit — "
                    "HANYA untuk permintaan KATALOG/buku part; pertanyaan part biasa pakai tool "
                    "lain. Hanya unit Sinotruk/HOWO/SITRAK."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "rangka": {"type": "string", "description": "Nomor rangka: VIN penuh atau frame number 8 digit."},
                        "kategori": {"type": "string", "description": "Kategori yang mau dikatalogkan (mis. 'kabin', 'rem', 'transmisi', 'gardan belakang', 'kelistrikan', 'ac') — ATAU 'semua' untuk KATALOG LENGKAP seluruh kategori unit. HANYA diisi bila user MENYEBUTNYA; bila user belum menyebut kategori, KOSONGKAN (tool akan menyuruhmu menawarkan pilihan) — JANGAN menebak."},
                        "format": {"type": "string", "enum": ["excel", "pdf"], "description": "Format file hasil: 'excel' (.xlsx) atau 'pdf' (siap cetak). HANYA diisi bila user SUDAH memilih; bila belum, KOSONGKAN (tool akan menyuruhmu menanyakan Excel atau PDF) — JANGAN menebak/mengasumsikan."},
                        "sertakan_stok_harga": {"type": "boolean", "description": "Isi TRUE HANYA bila user (yang seorang ADMIN) secara eksplisit minta stok & harga ikut diisi di katalog. Default kosong/false = kolom Stok/Harga dibiarkan KOSONG. Untuk user non-admin, tetap KOSONG walau diminta (sistem menahannya). JANGAN set true tanpa permintaan eksplisit."},
                        "sumber": {"type": "string", "enum": ["atlas", "mesin"], "description": "'atlas' (default) = katalog bodi/sasis Sinotruk (Parts Atlas). 'mesin' = KATALOG MESIN Weichai per-VIN ('katalog mesin <rangka>', 'buku part mesin'): part internal mesin per-kelompok (blok, kepala silinder, kruk as, bahan bakar, pelumas, pendingin, turbo, kompresor, alternator/starter); kategori diisi kelompok mesin itu atau 'lengkap'. Hanya unit bermesin Weichai."},
                    },
                    "required": ["rangka"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "gambar_exploded",
                "description": (
                    "TAMPILKAN GAMBAR EXPLODED VIEW untuk SATU Part Number (gambar muncul INLINE di "
                    "jawaban chat, bukan file unduh) — panggil saat user minta 'tampilkan/lihat "
                    "gambar exploded view part ini', 'gambar/skema part <PN>', 'GAMBAR TEKNIS "
                    "part ini' (istilah lapangan: 'gambar teknis' = exploded view), 'part ini nomor balon "
                    "berapa di gambar'. ⚠️ Kecuali yang diminta gambar KABEL/PIN/KONEKTOR/rangkaian "
                    "listrik — itu diagram_wiring, bukan tool ini. "
                    "Menemukan FIGURE resmi EPC (Parts Atlas per-VIN) yang memuat "
                    "PN itu + NOMOR BALON-nya, lalu menyajikan gambarnya + daftar balon→part figure "
                    "itu. Gambar hanya muncul saat DIMINTA lewat tool ini (tidak auto-nempel di tiap "
                    "cek part). Yang WAJIB hanya PN. ADA nomor rangka → jalur per-VIN "
                    "(paling tepat: figure milik unit itu + daftar balon→part). TANPA "
                    "rangka → tetap JALAN lewat figure LINTAS-MODEL (figure EPC mana pun "
                    "yang memuat PN itu) — ⛔ JANGAN menolak & JANGAN mewajibkan user "
                    "menyebut VIN dulu; cukup sampaikan peringatan lintas-model dari "
                    "hasil tool, lalu tawarkan cek per-VIN bila unitnya spesifik. "
                    "'kategori' hanya dipakai di jalur per-VIN (mempersempit figure). "
                    "DUA SISI dalam satu tool (param 'sumber'): 'atlas' (default) = part BODI/"
                    "SASIS/GARDAN/REM/KABIN Sinotruk (Parts Atlas); 'mesin' = part INTERNAL "
                    "MESIN unit bermesin Weichai (piston, liner, klep, injektor, kruk as, "
                    "turbo — figure EPC Weichai, kategori = kelompok mesin: blok/bahan bakar/"
                    "pelumas/pendingin/turbo, kosong = seluruh mesin). Auto: bila PN tak "
                    "ketemu di Atlas, sistem otomatis mencoba sisi mesin (lihat 'sumber_dipakai')."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "rangka": {"type": "string", "description": "OPSIONAL. Nomor rangka/VIN unit bila diketahui → gambar per-VIN (paling tepat + daftar balon→part). KOSONG = figure lintas-model (tetap jalan; user TAK perlu ditanya VIN lebih dulu)."},
                        "pn": {"type": ["string", "array"], "items": {"type": "string"}, "description": "Part Number untuk MENEMUKAN figure-nya (part yg sedang dibahas). BOLEH ARRAY berisi beberapa PN (maks 4) atau string dipisah ';' — SEMUA diproses dalam SATU panggilan, status per-PN di 'per_pn'; ⛔ JANGAN memanggil tool ini berulang kali per-PN. 'kategori' berlaku utk semua PN; 'balon' hanya utk mode 1 PN."},
                        "kategori": {"type": "string", "description": "Kategori figure untuk mempersempit pencarian: tentukan dari JENIS part (bearing/hub/baut roda → 'gardan depan'/'gardan belakang'; kampas/sepatu rem → 'rem'; piston/liner/klep → 'mesin'; sinkromes/garpu → 'transmisi'; part kabin → 'kabin'; kelistrikan → 'kelistrikan'). Bila belum yakin, KOSONGKAN (tool akan meminta ditentukan). Utk sumber='mesin' boleh kosong = cari di seluruh kelompok mesin."},
                        "balon": {"type": "integer", "description": "OPSIONAL. Bila user minta menyorot NOMOR BALON tertentu di gambar (mis. 'cek baut no 3', 'balon 5 itu apa'), isi nomornya — sistem menyorot balon itu (kuning) di figure yang memuat 'pn' + melaporkan part di balon itu. KOSONG = sorot balon PN-nya sendiri."},
                        "sumber": {"type": "string", "enum": ["atlas", "mesin"], "description": "Sisi EPC: 'atlas' (default, bodi/sasis Sinotruk) atau 'mesin' (EPC Weichai, part internal mesin unit bermesin Weichai). Kosongkan = atlas + auto-fallback mesin."},
                    },
                    "required": ["pn"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "buat_excel",
                "description": (
                    "BUAT FILE EXCEL (kartu unduh) untuk TABEL KECIL AD-HOC dari data yang "
                    "SUDAH dibahas. Isi 'baris' WAJIB disalin PERSIS dari hasil tool "
                    "percakapan ini — ⛔ PN yang tak pernah muncul dari tool DITOLAK; belum "
                    "ada datanya → panggil tool datanya dulu. ⛔ Data BESAR pakai tool "
                    "server: BOM per-rangka→excel_bom_rangka; stok kategori→"
                    "excel_stok_gudang; katalog bergambar→katalog_kategori/katalog_mesin; "
                    "armada→banding_rangka_massal; Excel UNGGAHAN user→sheet_isi_*. Kartu "
                    "unduh muncul OTOMATIS — jawab singkat, jangan tulis ulang tabel/link."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "judul": {"type": "string", "description": "Judul file/tabel, spesifik (mis. 'Part Air Compressor Unit RJ345233')."},
                        "kolom": {"type": "array", "items": {"type": "string"}, "description": "Judul kolom berurutan (mis. ['No','Part Number','Nama Part','Qty','Stok','Harga'])."},
                        "baris": {"type": "array", "items": {"type": "array", "items": {"type": "string"}}, "description": "Baris data; tiap baris = array string seurut 'kolom'. Salin PERSIS dari hasil tool."},
                    },
                    "required": ["judul", "kolom", "baris"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "excel_bom_rangka",
                "description": (
                    "EXCEL BOM/DAFTAR PART per-NOMOR RANGKA yang dibangun SERVER secara LENGKAP "
                    "(ribuan baris pun utuh, data langsung dari EPC — bukan salinan model). Panggil "
                    "saat user minta 'excel BOM unit X', 'export daftar part rangka X ke excel', "
                    "'excel part rem unit X lengkap dengan stok dan harganya'. Bisa difilter satu "
                    "kategori (kabin/rem/transmisi/…) ATAU kata kunci part; kosongkan keduanya "
                    "untuk BOM lengkap. Set dengan_stok/dengan_harga=true bila user menyebut ingin "
                    "stok/harga ikut (kolom otomatis disembunyikan bila peran user tak berhak). "
                    "⛔ BUKAN untuk katalog BERGAMBAR (itu katalog_kategori) & BUKAN pengganti "
                    "bom_dari_rangka untuk MENJAWAB pertanyaan — ini khusus MEMBUAT FILE. Setelah "
                    "sukses kartu unduh muncul otomatis; jawab singkat tanpa menulis ulang tabel."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "rangka": {"type": "string", "description": "Nomor rangka/VIN unit."},
                        "kategori": {"type": "string", "description": "Opsional: SATU kategori (kabin, rem, transmisi, kelistrikan, mesin, …)."},
                        "kata_kunci": {"type": "string", "description": "Opsional: filter kata kunci part (mis. 'filter', 'kampas rem')."},
                        "dengan_stok": {"type": "boolean", "description": "Sertakan kolom stok total + rincian per-gudang (indeks Accurate)."},
                        "dengan_harga": {"type": "boolean", "description": "Sertakan kolom harga (hanya tampil untuk peran yang berhak)."},
                    },
                    "required": ["rangka"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "excel_stok_gudang",
                "description": (
                    "EXCEL DAFTAR STOK per KATEGORI part yang dibangun SERVER secara LENGKAP dari "
                    "indeks Accurate (jawaban chat stok_gudang dipangkas 40 baris — file ini TIDAK). "
                    "Panggil saat user minta 'excel semua filter yang ready di Jakarta', 'export "
                    "stok kampas rem semua gudang ke excel', 'daftar stok kopling + harga dalam "
                    "excel'. `gudang` kosong = SEMUA gudang (ada kolom rincian per-gudang). "
                    "dengan_harga=true bila user ingin harga (hanya untuk peran yang berhak). "
                    "⛔ Bukan untuk pembeli. ⛔ Untuk MENJAWAB pertanyaan stok di chat tetap pakai "
                    "stok_gudang — tool ini khusus MEMBUAT FILE. Kartu unduh muncul otomatis."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "kata_kunci": {"type": "string", "description": "Kategori/nama part (mis. 'kampas rem', 'filter oli', 'kopling')."},
                        "gudang": {"type": "string", "description": "Opsional: satu gudang (mis. 'Jakarta'); kosong = semua gudang."},
                        "dengan_harga": {"type": "boolean", "description": "Sertakan kolom harga (hanya tampil untuk peran yang berhak)."},
                    },
                    "required": ["kata_kunci"],
                },
            },
        },
    ]

    # HITUNG deterministik (total/urut/filter harga) — hanya ditawarkan ke yang
    # berhak melihat harga (harga gate); model TAK menghitung sendiri.
    if _boleh_harga(user):
        specs.append({
            "type": "function",
            "function": {
                "name": "hitung_part",
                "description": (
                    "HITUNG PASTI (dihitung SISTEM, bukan kamu) atas beberapa part yang SUDAH ada "
                    "di percakapan: TOTAL harga (dengan qty per item), urutkan TERMURAH/TERMAHAL, "
                    "atau saring (harga maksimum/minimum, hanya yang ready). Panggil ini SETIAP "
                    "kali user minta 'totalnya berapa', 'kalau ambil N pcs', 'urutkan termurah', "
                    "'yang di bawah X', 'yang ready saja'. ⛔ JANGAN menghitung/mengurutkan harga "
                    "sendiri di kepala — rawan salah; tool ini pakai harga OTORITATIF Accurate. "
                    "Kirim hanya Part Number yang sudah muncul dari tool/percakapan (PN karangan "
                    "ditolak). Hasilnya: rincian per item + subtotal + TOTAL + item tanpa harga."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "items": {
                            "type": "array",
                            "description": "Part yang dihitung. Tiap elemen {pn, qty}. qty kosong = 1.",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "pn": {"type": "string", "description": "Part Number (persis dari hasil tool)."},
                                    "qty": {"type": "integer", "description": "Jumlah pcs (default 1)."},
                                },
                                "required": ["pn"],
                            },
                        },
                        "urutkan": {"type": "string", "enum": ["termurah", "termahal"],
                                    "description": "Opsional: urutkan hasil berdasarkan harga."},
                        "harga_maks": {"type": "integer", "description": "Opsional: hanya harga ≤ nilai ini (mis. 1000000)."},
                        "harga_min": {"type": "integer", "description": "Opsional: hanya harga ≥ nilai ini."},
                        "hanya_ready": {"type": "boolean", "description": "Opsional: hanya part yang stoknya > 0."},
                    },
                    "required": ["items"],
                },
            },
        })

    # Garansi & Klaim SIMS (DMS) — gerbang Menu Control 'ai_garansi':
    # admin selalu; staf bila dicentang; pembeli tidak pernah (fail-closed).
    if _can_garansi(user):
        specs.append({
            "type": "function",
            "function": {
                "name": "cek_garansi",
                "description": (
                    "⭐ CEK STATUS GARANSI satu unit dari NOMOR RANGKA (SIMS DMS resmi "
                    "Sinotruk, data pabrik per-unit): masa garansi CNHTC & dealer, "
                    "MASIH AKTIF atau tidak + sisa hari, % masa terpakai, spesifikasi "
                    "(model, Euro, tipe pakai, tanggal jual/keluar pabrik), NOMOR SERI "
                    "ASLI komponen (mesin/gearbox/gardan depan-tengah-belakang) beserta "
                    "modelnya, dan jumlah servis per komponen. Terima VIN penuh maupun "
                    "frame number — konversi otomatis di server."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "rangka": {"type": "string", "description": "Nomor rangka: VIN penuh atau frame number — KIRIM APA ADANYA dari user."},
                    },
                    "required": ["rangka"],
                },
            },
        })
        specs.append({
            "type": "function",
            "function": {
                "name": "riwayat_klaim",
                "description": (
                    "DAFTAR WORK ORDER KLAIM GARANSI (SIMS DMS dealer): per unit "
                    "(rangka), per nomor WO, atau terbaru bila tanpa filter. Tiap baris: "
                    "no WO, tanggal, km saat rusak, GEJALA kerusakan, tindakan, status "
                    "pekerjaan (label Indonesia), nopol, pelapor, durasi jam. Pakai untuk "
                    "'unit X pernah klaim apa saja', 'klaim garansi terbaru', 'status WO "
                    "RIDZxxx sampai mana'. SARINGAN DURASI: 'WO yang lebih dari 72 jam' → "
                    "durasi_min_jam=72; server menyisir SEMUA klaim, menyaring & "
                    "mengurutkan terlama-dulu — ⛔ JANGAN saring/banding durasi sendiri "
                    "dari halaman-halaman. Untuk isi lengkap satu WO pakai detail_klaim."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "rangka": {"type": "string", "description": "Opsional: nomor rangka unit (VIN/frame)."},
                        "no_wo": {"type": "string", "description": "Opsional: nomor work order persis (mis. RIDZ0052607123)."},
                        "halaman": {"type": "integer", "description": "Halaman hasil (default 1). Diabaikan saat memakai saringan durasi."},
                        "durasi_min_jam": {"type": "number", "description": "Opsional: hanya WO berdurasi ≥ ini (jam). 'lebih dari 72 jam' → 72."},
                        "durasi_maks_jam": {"type": "number", "description": "Opsional: hanya WO berdurasi ≤ ini (jam)."},
                        "limit": {"type": "integer", "description": "Jumlah baris saat saringan durasi (default 10, maks 50). 'sebutkan 10 saja' → 10."},
                    },
                },
            },
        })
        specs.append({
            "type": "function",
            "function": {
                "name": "detail_klaim",
                "description": (
                    "ISI LENGKAP SATU work order klaim garansi dari nomor WO: PART yang "
                    "diklaim (PN, nama, qty, harga CNY, jenis ganti/perbaiki, penanggung "
                    "jawab), JASA (kode, jam kuota, tarif, total), total biaya per tahap "
                    "audit, ALUR PERSETUJUAN (tahap kini + sedang menunggu siapa), "
                    "kebijakan tarif garansi, dan FOTO klaim (kerusakan/pembongkaran/unit) "
                    "yang tampil inline. Pakai saat user menyebut nomor WO / minta detail "
                    "satu klaim dari hasil riwayat_klaim."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "no_wo": {"type": "string", "description": "Nomor work order (mis. RIDZ0052607123)."},
                    },
                    "required": ["no_wo"],
                },
            },
        })
        specs.append({
            "type": "function",
            "function": {
                "name": "excel_riwayat_klaim",
                "description": (
                    "EXPORT EXCEL daftar riwayat KLAIM garansi (semua atau per unit/status). "
                    "Pakai saat user minta 'excel riwayat klaim', 'daftar klaim garansi dalam "
                    "Excel', 'klaim unit X ke Excel'. Kolom: no WO, unit, tanggal, km, gejala, "
                    "tindakan, status, durasi, pelapor. (Nilai CNY tidak di daftar — pakai "
                    "detail_klaim untuk nilai per WO.)"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "rangka": {"type": "string", "description": "Opsional: nomor rangka/VIN untuk hanya klaim unit itu."},
                        "status": {"type": "string", "description": "Opsional filter status (mis. selesai, pending, dibatalkan)."},
                    },
                },
            },
        })
        specs.append({
            "type": "function",
            "function": {
                "name": "rekap_klaim",
                "description": (
                    "REKAP/STATISTIK klaim garansi armada: total klaim, jumlah per STATUS "
                    "(selesai/pending/dibatalkan/dst), GEJALA tersering, rentang tanggal, "
                    "rata-rata durasi. Pakai untuk 'berapa klaim selesai/pending', 'kerusakan "
                    "apa yang paling sering', 'ringkasan klaim garansi'. (Nilai CNY total tidak "
                    "dihitung — untuk nilai per klaim pakai detail_klaim.)"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "rangka": {"type": "string", "description": "Opsional: rekap hanya untuk satu unit (VIN/frame)."},
                    },
                },
            },
        })

        specs.append({
            "type": "function",
            "function": {
                "name": "kasus_serupa",
                "description": (
                    "⭐ KELUHAN → PART yang NYATA-NYATA dipasang, dari 1.785 klaim "
                    "garansi armada sendiri (klaim dibatalkan sudah dibuang). PANGGIL "
                    "saat user menyebut GEJALA/KERUSAKAN dan ingin tahu part apa yang "
                    "biasanya diganti: 'dudukan karet suspensi patah ganti apa', 'aki "
                    "soak', 'rem blong', 'stabilizer patah'. Balasan: part_disarankan "
                    "(PN, berapa KALI dipasang, KM saat rusak biasanya, mode kegagalan, "
                    "harga CNY), mode gagal tersering, biaya median, dan contoh WO nyata. "
                    "Ini BUKTI LAPANGAN, bukan katalog — beda dari cari_part (katalog) "
                    "dan part_fast_moving (laris jualan per model). Bisa juga diisi PN "
                    "langsung untuk melihat riwayat kerusakan part itu. ⚠️ Tetap cocokkan "
                    "ke unit/VIN sebelum memesan."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "gejala": {
                            "type": "string",
                            "description": "Keluhan/gejala apa adanya dari user (Bahasa Indonesia boleh), atau satu PN.",
                        },
                    },
                    "required": ["gejala"],
                },
            },
        })

    # Mengajari pengetahuan lewat chat — gerbang Menu Control 'ai_mengajar':
    # admin selalu; staf bila dicentang; pembeli tidak pernah (fail-closed).
    if _can_mengajar(user):
        specs.append({
            "type": "function",
            "function": {
                "name": "ajarkan_pengetahuan",
                "description": (
                    "⭐ MENYIMPAN PENGETAHUAN BARU ke store pengetahuan internal, "
                    "lewat percakapan. PANGGIL bila user bermaksud MENGAJARI kamu / "
                    "menitipkan informasi: 'catat ya …', 'ingat ini …', 'ajarkan/"
                    "tambahkan ke pengetahuanmu …', 'mulai sekarang kalau ada yang "
                    "tanya X jawabnya Y', 'simpan info ini'.\n"
                    "ALUR WAJIB 2 LANGKAH: (1) aksi='draf' — kamu SUSUN sendiri "
                    "judul + isi + kata_kunci dari kalimat user, server menahannya "
                    "dan menampilkan kartu konfirmasi; (2) user membalas kartu itu "
                    "sebagai teks biasa → panggil lagi: balasan 'Simpan' atau "
                    "'Simpan sebagai entri baru' → aksi='simpan'/'simpan_baru'; "
                    "'Simpan ke Kamus Sinonim' → aksi='simpan_kamus'; 'Simpan ke Rute "
                    "Maksud' → aksi='simpan_maksud'; 'Jadi catatan "
                    "saja' → aksi='simpan'; 'Perbarui entri lama' → aksi='perbarui'; "
                    "'Perbaiki dulu' + koreksinya → aksi='draf' lagi dengan draf "
                    "REVISI; 'Batal' → aksi='batal'.\n"
                    "RUTE MAKSUD (frasa → ALAT): bila yang diajarkan adalah ISTILAH "
                    "yang menentukan ALAT MANA yang harus kamu pakai — 'kalau user "
                    "minta <istilah>, itu maksudnya <hal yang dikerjakan tool X>', "
                    "'istilah <A> di sini artinya <B>' di mana B adalah pekerjaan "
                    "salah satu toolmu — ISI JUGA maksud_frasa + maksud_tool pada "
                    "aksi='draf'. maksud_tool WAJIB nama tool yang BENAR-BENAR ada di "
                    "daftar alatmu (⛔ jangan mengarang nama tool). Kartu akan "
                    "menawarkan menyimpannya sebagai RUTE, dan sejak itu permintaan "
                    "yang memuat frasa tsb otomatis diarahkan ke tool itu untuk SEMUA "
                    "staf. Bedakan dari pemetaan istilah biasa: rute mengubah ALAT "
                    "yang dipakai, kamus sinonim hanya mengubah KATA yang dicari.\n"
                    "PEMETAAN ISTILAH: bila yang diajarkan MURNI penerjemahan istilah "
                    "('simpang empat itu universal joint', 'kalau user bilang X "
                    "maksudnya Y'), ISI JUGA istilah_trigger (istilah lapangan, 1-3 "
                    "kata) + istilah_keywords (kata kunci katalog Inggris) pada "
                    "aksi='draf' — kartu akan menawarkan menyimpannya ke KAMUS "
                    "SINONIM supaya PENCARIAN PART ikut mengerti istilah itu "
                    "(catatan biasa hanya bahan bacaanmu, pencarian tidak berubah).\n"
                    "ISI DRAF WAJIB BERDIRI SENDIRI: entri ini akan dibaca "
                    "berbulan-bulan kemudian TANPA percakapan ini — ⛔ jangan pakai "
                    "'yang barusan/di atas/tadi', sebut nama part/istilah/kondisinya "
                    "eksplisit, tulis kalimat utuh (bukan potongan chat). ⛔ JANGAN "
                    "memasukkan angka STOK/HARGA (berubah tiap hari; sudah ada "
                    "sumber hidupnya) — angka teknis statis (torsi, kapasitas, "
                    "ukuran, interval) BOLEH.\n"
                    "⛔ JANGAN PERNAH mengaku sudah mencatat/menyimpan/mengingat "
                    "sesuatu bila tool ini belum mengembalikan tersimpan=true. "
                    "Tanpa itu, TIDAK ADA yang tersimpan."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "aksi": {
                            "type": "string",
                            "enum": ["draf", "simpan", "simpan_baru", "simpan_kamus", "simpan_maksud", "perbarui", "batal"],
                            "description": "Langkah alur. Default 'draf'. Aksi selain 'draf' memakai draf yang DITAHAN server — judul/isi tak perlu dikirim ulang.",
                        },
                        "judul": {"type": "string", "description": "aksi='draf': judul singkat & mudah dicari (mis. 'Istilah lapangan: cucuk per')."},
                        "isi": {"type": "string", "description": "aksi='draf': isi pengetahuannya, kalimat lengkap yang berdiri sendiri (maks 2000 char)."},
                        "kata_kunci": {
                            "type": "array", "items": {"type": "string"},
                            "description": "aksi='draf': 2-8 istilah pencarian (istilah LAPANGAN yang dipakai user + padanan resminya).",
                        },
                        "istilah_trigger": {
                            "type": "array", "items": {"type": "string"},
                            "description": "HANYA bila pemetaan istilah murni: istilah LAPANGAN-nya (1-3 kata per item, mis. ['simpang empat']).",
                        },
                        "istilah_keywords": {
                            "type": "array", "items": {"type": "string"},
                            "description": "Pasangan istilah_trigger: kata kunci KATALOG (Inggris) tujuannya (mis. ['universal joint']).",
                        },
                        "maksud_frasa": {
                            "type": "array", "items": {"type": "string"},
                            "description": "HANYA bila ajarannya menentukan ALAT: frasa yang dipakai user (1-4 kata per item). ⛔ Jangan kata generik sendirian ('gambar', 'part', 'cek') — akan ditolak karena membajak percakapan lain.",
                        },
                        "maksud_tool": {
                            "type": "string",
                            "description": "Pasangan maksud_frasa: nama tool yang harus dipakai saat frasa itu muncul. WAJIB nama tool yang ADA di daftar alatmu — nama karangan ditolak.",
                        },
                        "maksud_catatan": {
                            "type": "string",
                            "description": "Opsional, maks 160 char: satu kalimat pembeda yang ikut ditampilkan ke dirimu nanti (mis. 'maksudnya exploded view, bukan foto part'). Sebut juga pengecualiannya bila ada.",
                        },
                    },
                },
            },
        })
        specs.append({
            "type": "function",
            "function": {
                "name": "topik_gagal",
                "description": (
                    "TOPIK yang BERULANG GAGAL kamu jawab (dari log produksi, "
                    "ambang ≥3 kegagalan) — bahan untuk DIAJARKAN. Panggil bila "
                    "user bertanya 'apa yang sering gagal kamu jawab', 'apa yang "
                    "perlu diajarkan', 'topik apa yang bolong', atau saat user "
                    "menanggapi tawaran belajar di layar pembuka. Alur: tampilkan "
                    "daftar → user pilih topik → susun draf dari contoh "
                    "pertanyaannya (+ tool data relevan) via ajarkan_pengetahuan "
                    "aksi='draf' → setelah TERSIMPAN, panggil tool ini lagi "
                    "aksi='tandai_selesai'. User bilang datanya memang tak ada → "
                    "aksi='bukan_gap'."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "aksi": {
                            "type": "string",
                            "enum": ["daftar", "tandai_selesai", "bukan_gap"],
                            "description": "Default 'daftar'. tandai_selesai = topik sudah diajarkan; bukan_gap = disingkirkan (data memang tidak ada).",
                        },
                        "topik": {"type": "string", "description": "Wajib utk tandai_selesai/bukan_gap: topik PERSIS seperti di daftar."},
                    },
                },
            },
        })

    # Telematics / GPS armada (Sinotruk Fleet Service) — ADMIN-ONLY (bukan key
    # Menu Control): pelacakan real-time + operasi tulis ganti nama.
    if _is_admin(user):
        specs.append({
            "type": "function",
            "function": {
                "name": "permintaan_tak_terlayani",
                "description": (
                    "ADMIN. PERMINTAAN YANG TIDAK BISA DILAYANI — part yang DICARI user "
                    "di aplikasi tapi tidak ada di katalog maupun stok lokal, dan sampai "
                    "sekarang masih tidak ada. Bahan keputusan PEMBELIAN/pengadaan. "
                    "Panggil untuk: 'part apa yang sering dicari tapi tidak kita punya', "
                    "'apa yang perlu kita stok', 'permintaan yang tidak terlayani', "
                    "'pencarian nihil', 'barang apa yang dicari pelanggan tapi kosong'. "
                    "Hasilnya SUDAH dipisah: 'permintaan_tak_terlayani' (tak ada apa pun "
                    "yang mirip = permintaan sungguhan) vs 'kemungkinan_salah_ketik' (ada "
                    "PN mirip di katalog). ⛔ JANGAN usulkan membeli yang di kelompok "
                    "salah ketik. ⛔ 'berapa_kali' = jumlah PENCARIAN, bukan jumlah order "
                    "— jangan menyebutnya permintaan barang/PO. Query yang ternyata kini "
                    "sudah ketemu otomatis dibuang (jumlahnya dilaporkan)."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "limit": {"type": "integer",
                                  "description": "Berapa baris teratas (default 40)."},
                        "min_kejadian": {"type": "integer",
                                         "description": "Abaikan yang dicari lebih jarang dari ini (default 1)."},
                        "maks_umur_hari": {"type": "integer",
                                           "description": "Abaikan permintaan lebih tua dari ini (default 60 hari)."},
                    },
                },
            },
        })
        specs.append({
            "type": "function",
            "function": {
                "name": "lihat_unit_armada",
                "description": (
                    "⭐ LACAK ARMADA via GPS/TELEMATICS (Sinotruk Fleet Service): posisi & "
                    "status real-time (Jalan/Berhenti/Offline), km, level BBM, flag RUSAK. "
                    "Tiga mode: (1) param 'unit' = SATU unit dari frame/VIN → detail + "
                    "NAMA/label-nya (pakai untuk 'cek nama unit X', 'unit X di fleet "
                    "mana'); (2) tanpa filter = ringkasan armada (total, online%, per FLEET, "
                    "jumlah rusak); (3) filter 'fleet'/'status'/'hanya_rusak'. ⛔ Data GPS "
                    "live — BUKAN spesifikasi katalog (cek_kendaraan) & BUKAN populasi "
                    "(cek_populasi)."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "unit": {"type": ["string", "array"], "items": {"type": "string"}, "description": "Opsional: frame/VIN unit (mis. SJ398956). ARRAY = banyak unit SEKALIGUS, maks 30 — jangan panggil berulang."},
                        "fleet": {"type": "string", "description": "Opsional: nama fleet/organisasi (mis. MAS, JNT). Kosong = semua."},
                        "status": {"type": "string", "description": "Opsional filter status: jalan / berhenti / offline."},
                        "hanya_rusak": {"type": "boolean", "description": "Opsional: hanya unit yang ditandai rusak."},
                    },
                },
            },
        })
        specs.append({
            "type": "function",
            "function": {
                "name": "excel_unit_armada",
                "description": (
                    "EXPORT EXCEL daftar unit armada (semua unit atau per fleet) — LENGKAP "
                    "dengan Frame, VIN, model, engine, gearbox, km, fleet, status GPS, BBM, "
                    "flag rusak. Pakai saat user minta 'excel semua unit' / 'daftar unit "
                    "per fleet dalam Excel'. Kolom Fleet membedakan bila tanpa filter."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "fleet": {"type": "string", "description": "Opsional: nama fleet. Kosong = semua unit (kolom Fleet membedakan)."},
                    },
                },
            },
        })
        specs.append({
            "type": "function",
            "function": {
                "name": "terakhir_online",
                "description": (
                    "⭐ KAPAN UNIT TERAKHIR ONLINE / kirim data GPS. Dua mode: (1) param "
                    "'unit' (frame/VIN) = SATU unit → jam terakhir kirim data + jedanya "
                    "('3 jam lalu'), status, alamat lokasi terakhir, kecepatan/rpm/suhu "
                    "air, jam mesin, km & BBM, kekuatan sinyal GSM + jumlah satelit; "
                    "(2) TANPA 'unit' = seluruh armada DIURUT dari yang PALING LAMA tak "
                    "mengirim data — untuk 'unit mana yang GPS-nya mati', 'unit yang "
                    "lama tidak online'. Saring dengan 'lebih_dari_hari' (mis. 7 = yang "
                    "sudah >7 hari diam) dan/atau 'fleet'. ⛔ Unit tanpa stempel waktu "
                    "= TIDAK TERBACA, bukan 'baru online' — sebutkan apa adanya."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "unit": {"type": "string", "description": "Opsional: frame/VIN satu unit. Kosong = seluruh armada."},
                        "fleet": {"type": "string", "description": "Opsional (mode armada): saring per nama fleet."},
                        "lebih_dari_hari": {"type": "number", "description": "Opsional (mode armada): hanya unit yang sudah diam lebih dari N hari."},
                    },
                },
            },
        })
        specs.append({
            "type": "function",
            "function": {
                "name": "ganti_nama_unit",
                "description": (
                    "⚠️ UBAH NAMA/LABEL unit di server Sinotruk (OPERASI TULIS, PERMANEN). "
                    "WAJIB 2 langkah: panggil DULU tanpa konfirmasi → tampilkan pratinjau "
                    "(nama lama→baru) dan MINTA PERSETUJUAN user; hanya setelah user setuju "
                    "panggil lagi dengan konfirmasi=true untuk eksekusi. ⛔ JANGAN pernah "
                    "langsung konfirmasi=true tanpa user menyetujui."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "cjh": {"type": "string", "description": "Frame/cjh (atau VIN) unit yang diganti namanya. Untuk BANYAK unit pakai 'daftar'."},
                        "nama_baru": {"type": "string", "description": "Nama/label baru untuk unit."},
                        "daftar": {"type": "array",
                                   "items": {"type": "object",
                                             "properties": {"cjh": {"type": "string"},
                                                            "nama_baru": {"type": "string"}},
                                             "required": ["cjh", "nama_baru"]},
                                   "description": "BANYAK unit sekaligus (maks 50): [{cjh, nama_baru}, …]. Pratinjau & konfirmasi berlaku untuk SELURUH daftar — jangan panggil berulang."},
                        "konfirmasi": {"type": "boolean", "description": "true HANYA setelah user menyetujui pratinjau. Default false = pratinjau."},
                    },
                },
            },
        })
        specs.append({
            "type": "function",
            "function": {
                "name": "daftarkan_unit",
                "description": (
                    "⚠️ DAFTARKAN/MASUKKAN unit BARU ke telematics/GPS Sinotruk (OPERASI "
                    "TULIS, PERMANEN — menambah data). Butuh VIN penuh + SERIAL perangkat "
                    "GPS (sbh) yang terpasang di unit. WAJIB 2 langkah: panggil DULU tanpa "
                    "konfirmasi → pratinjau (VIN, frame, serial, apakah sudah terdaftar) & "
                    "MINTA PERSETUJUAN; setelah user setuju baru konfirmasi=true. ⛔ Serial "
                    "GPS tak bisa ditebak — harus dari user. Untuk BANYAK unit sekaligus "
                    "dari Excel pakai sheet_daftar_unit."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "vin": {"type": "string", "description": "VIN penuh 17 karakter unit baru."},
                        "sbh": {"type": "string", "description": "Serial perangkat GPS (sbh) yang terpasang di unit."},
                        "km": {"type": "integer", "description": "Kilometer saat pendaftaran (default 0)."},
                        "euro2": {"type": "boolean", "description": "true bila unit Euro 2 (default false)."},
                        "konfirmasi": {"type": "boolean", "description": "true HANYA setelah user menyetujui pratinjau."},
                    },
                    "required": ["vin", "sbh"],
                },
            },
        })
        specs.append({
            "type": "function",
            "function": {
                "name": "masukkan_unit_fleet",
                "description": (
                    "⚠️ MASUKKAN/PINDAHKAN unit ke FLEET (organisasi) di telematics Sinotruk "
                    "(OPERASI TULIS). Butuh unit (frame/VIN) + nama fleet tujuan. WAJIB 2 "
                    "langkah: tanpa konfirmasi → pratinjau (fleet sekarang → fleet tujuan) & "
                    "MINTA PERSETUJUAN; setelah user setuju baru konfirmasi=true. Untuk "
                    "BANYAK unit dari Excel pakai sheet_masukkan_fleet."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "unit": {"type": ["string", "array"], "items": {"type": "string"}, "description": "Frame/cjh atau VIN unit. ARRAY = banyak unit ke fleet yang SAMA, maks 50; pratinjau & konfirmasi berlaku untuk seluruh daftar — jangan panggil berulang."},
                        "fleet": {"type": "string", "description": "Nama fleet/organisasi tujuan (mis. JNT, PALEMBANG)."},
                        "konfirmasi": {"type": "boolean", "description": "true HANYA setelah user menyetujui pratinjau."},
                    },
                    "required": ["unit", "fleet"],
                },
            },
        })
        specs.append({
            "type": "function",
            "function": {
                "name": "daftar_fleet",
                "description": (
                    "DAFTAR FLEET/ORGANISASI yang TERSEDIA di telematics Sinotruk — nama, "
                    "fleet induk (sarang), dan jumlah unit tiap fleet, dari pohon resmi "
                    "server. Pakai untuk 'fleet apa saja yang ada', 'ada organisasi apa "
                    "di GPS', 'unit ini mau dimasukkan ke fleet mana saja pilihannya'. "
                    "⭐ Panggil ini DULU sebelum masukkan_unit_fleet/sheet_masukkan_fleet "
                    "bila user menyebut nama fleet yang belum pasti ada. ⛔ Beda dari "
                    "lihat_unit_armada (itu unit + GPS live; fleet kosong tak muncul di "
                    "sana). ⛔ JANGAN mengarang nama fleet — sebut hanya yang ada di hasil."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "cari": {"type": "string", "description": "Opsional: saring nama fleet yang mengandung teks ini. Kosong = semua."},
                    },
                },
            },
        })
        specs.append({
            "type": "function",
            "function": {
                "name": "buat_fleet",
                "description": (
                    "⚠️ BUAT FLEET/organisasi BARU di telematics Sinotruk (OPERASI TULIS, "
                    "menambah struktur). Butuh nama fleet; opsional 'induk' (fleet induk, "
                    "default organisasi utama). WAJIB 2 langkah: tanpa konfirmasi → "
                    "pratinjau (nama + induk + apakah sudah ada) & MINTA PERSETUJUAN; "
                    "setelah user setuju baru konfirmasi=true. Setelah fleet dibuat, unit "
                    "dimasukkan lewat masukkan_unit_fleet."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "nama": {"type": "string", "description": "Nama fleet baru."},
                        "induk": {"type": "string", "description": "Opsional: nama fleet induk. Kosong = organisasi utama (akar)."},
                        "konfirmasi": {"type": "boolean", "description": "true HANYA setelah user menyetujui pratinjau."},
                    },
                    "required": ["nama"],
                },
            },
        })

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
                            "type": ["string", "array"], "items": {"type": "string"},
                            "description": (
                                "Kata kunci. Boleh beberapa kata — SEMUA harus muncul "
                                "(mis. 'NX360 2022', 'HOWO Jakarta'). Kosongkan untuk "
                                "melihat ringkasan seluruh populasi. ARRAY = banyak "
                                "model/customer SEKALIGUS, maks 20 — jangan panggil berulang."
                            ),
                        },
                    },
                },
            },
        })
        specs.append({
            "type": "function",
            "function": {
                "name": "banding_part_armada",
                "description": (
                    "BANDINGKAN SATU PART ANTAR SEMUA UNIT MILIK SATU CUSTOMER/PT "
                    "(armada) — panggil saat user tanya 'apakah <part> SAMA untuk semua "
                    "unit PT X?', 'cek kampas kopling unit PT Y sama semua atau beda?'. "
                    "Otomatis: data populasi → nomor rangka tiap unit → konfigurasi "
                    "pabrik EPC per-VIN → kelompokkan unit berkonfigurasi identik → cek "
                    "part via EPC Parts Atlas pada unit WAKIL tiap kelompok → verdict "
                    "SAMA/BEDA dihitung SISTEM (bukan tebakan). Hanya unit Sinotruk/"
                    "HOWO/SITRAK yang dikenali EPC. JANGAN menjawab pertanyaan seperti "
                    "ini dgn cek_populasi lalu menebak dari nama model. ⛔ Tool ini untuk "
                    "SATU PART AUS spesifik (kampas kopling/rem, filter, hub, bearing). "
                    "Bila user tanya soal KATEGORI utuh (KABIN, mesin, transmisi, "
                    "kelistrikan, sasis, gardan) armada → pakai banding_rangka_massal, "
                    "BUKAN tool ini."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "customer": {"type": "string", "description": "Nama customer/PT persis seperti user menyebutnya (mis. 'PT ARGCIO')."},
                        "part": {"type": "string", "description": "Part yang dibandingkan (mis. 'kampas kopling', 'kampas rem', 'filter oli')."},
                        "posisi": {"type": "string", "description": "Opsional, khusus part poros/rem: 'depan' atau 'belakang'."},
                    },
                    "required": ["customer", "part"],
                },
            },
        })

    # Stok per-GUDANG: daftar part 1 kategori yg READY di satu gudang. Mengungkap
    # rincian antar-gudang → TIDAK diberikan ke pembeli.
    if not _is_pembeli(user):
        specs.append({
            "type": "function",
            "function": {
                "name": "stok_gudang",
                "description": (
                    "DAFTAR PART yang stoknya READY (tersedia, qty>0) DI SATU GUDANG "
                    "tertentu, disaring per kata kunci/kategori. Panggil untuk pola 'cek "
                    "stok part <kategori> yang ready di <gudang>', 'part <X> apa saja yang "
                    "ada di gudang <Y>', 'kopling yang ready di Palembang', 'filter oli "
                    "stok di Jakarta', 'lampu apa saja ready di Medan'. Otomatis: (1) "
                    "perluas kata kunci/kategori ke sub-part (mis. 'kopling' → driven "
                    "disc, matahari/pressure plate, drek laher/release bearing, garpu, "
                    "master/booster, rumah kopling); (2) filter HANYA part yg stoknya >0 "
                    "DI GUDANG itu (sumber: stok Accurate, sinkron berkala). Mengembalikan daftar {part_number, part_name, "
                    "stok_di_gudang (qty di gudang itu), stok_total, harga}. BEDA dari "
                    "cari_part (stok TOTAL semua gudang, bukan 1 gudang) & detail_part "
                    "(hanya 1 PN). Nama gudang boleh bebas ('palembang', 'jakarta', "
                    "'makasar', 'medan') — sistem mencocokkan ke gudang resmi."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "kata_kunci": {"type": "string", "description": "Kategori/part yg dicari (mis. 'kopling', 'kampas rem', 'filter oli', 'lampu')."},
                        "gudang": {"type": "string", "description": "Nama gudang tujuan (mis. 'Palembang', 'Jakarta', 'Makasar', 'Medan')."},
                        "unit": {"type": "string", "description": "Opsional. Batasi ke unit/model tertentu (mis. 'NX360')."},
                    },
                    "required": ["kata_kunci", "gudang"],
                },
            },
        })

    # Stok TERTAHAN reservasi: menjelaskan SELISIH stok Accurate vs yang bisa dibeli.
    # Admin selalu; staf lain bila diberi centang 'ai_stok_admin' (Menu Control tab
    # Asisten AI) — membuka kode pesanan & identitas penahan LINTAS CABANG.
    if _can_stok_admin(user):
        specs.append({
            "type": "function",
            "function": {
                "name": "stok_tertahan",
                "description": (
                    "AKSES TERBATAS (diberikan admin lewat Menu Control). MENJELASKAN SELISIH antara stok Accurate dan stok yang bisa dibeli: "
                    "berapa yang sedang DITAHAN reservasi pesanan aktif, di gudang mana, "
                    "dan oleh PESANAN MANA (kode + status pesanan). Panggil untuk pola "
                    "'kenapa stok <PN> tinggal 1 padahal Accurate 3', 'stok ini ditahan "
                    "siapa/pesanan apa', 'kenapa part ini tidak bisa dibeli padahal ada "
                    "stoknya', 'reservasi aktif di gudang <X>', 'stok yang lagi ditahan'. "
                    "Stok yang bisa dibeli = stok Accurate − reservasi aktif; tool ini "
                    "membongkar bagian 'reservasi aktif' itu. Tanpa part_number: daftar "
                    "SEMUA reservasi aktif (boleh disaring per gudang). BEDA dari "
                    "stok_accurate (stok mentah Accurate, tak tahu reservasi) & "
                    "stok_gudang (daftar part ready per kategori di 1 gudang)."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "part_number": {"type": "string", "description": "Opsional. PN yang ditanyakan (mis. 'WG9725190070'). Kosongkan untuk melihat semua reservasi aktif."},
                        "gudang": {"type": "string", "description": "Opsional. Batasi ke satu gudang (mis. 'Palembang', 'Jakarta')."},
                    },
                },
            },
        })

    # Pemeriksaan operasional pesanan (uang, pembukuan, lintas cabang) — admin
    # selalu; staf lain bila diberi centang 'ai_pesanan_bermasalah'.
    if _can_pesanan_bermasalah(user):
        specs.append({
            "type": "function",
            "function": {
                "name": "pesanan_bermasalah",
                "description": (
                    "AKSES TERBATAS (diberikan admin lewat Menu Control). PEMERIKSAAN PESANAN yang butuh perhatian, dikelompokkan: "
                    "(1) uang_perlu_dicek = dibayar setelah pesanan batal / nominal tak cocok "
                    "→ UANG NYATA yang menunggu REFUND atau konfirmasi; (2) penawaran_gagal = "
                    "pesanan lunas tapi Penawaran Accurate gagal dibuat → tak masuk pembukuan; "
                    "(3) lunas_belum_dikirim = sudah lunas >N hari tapi belum dikirim; "
                    "(4) bayar_macet = lewat tenggat bayar tapi belum lunas/batal (gateway tak "
                    "bisa ditanya → periksa manual). Panggil untuk 'ada pesanan bermasalah?', "
                    "'cek pesanan yang perlu ditindak', 'ada yang perlu refund?', 'pesanan "
                    "nyangkut', 'pesanan lunas yang belum dikirim'. Laporkan APA ADANYA & "
                    "dahulukan yang menyangkut uang."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "hari_macet": {"type": "integer", "description": "Opsional (default 3). Pesanan lunas dianggap 'belum dikirim' bila lebih dari sekian hari."},
                    },
                },
            },
        })
    # Dipisah dari blok pesanan_bermasalah: kemampuannya beda key (ai_stok_admin,
    # satu paket dengan stok_tertahan — sama-sama data reservasi stok).
    if _can_stok_admin(user):
        specs.append({
            "type": "function",
            "function": {
                "name": "alternatif_ready",
                "description": (
                    "AKSES TERBATAS (diberikan admin lewat Menu Control). PART HABIS → CARIKAN GANTINYA YANG SIAP KIRIM. Ambil PN "
                    "persamaan/pengganti resmi (SIMS Sinotruk utk sasis + EPC Weichai utk "
                    "mesin), lalu SARING hanya yang stoknya BENAR-BENAR ready (stok Accurate − "
                    "reservasi aktif > 0, di gudang yang bisa mengirim) & sebut gudangnya. "
                    "Panggil untuk 'part ini kosong, ada gantinya yang ready?', 'stok habis "
                    "adakah alternatif', 'pengganti yang bisa langsung dikirim'. BEDA dari "
                    "pengganti_part (daftar pengganti resmi APA ADANYA, tanpa saring stok "
                    "siap-kirim) — tool ini untuk MENYELAMATKAN PENJUALAN. ⛔ Jangan mengarang "
                    "PN: hanya yang muncul di hasil."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "part_number": {"type": "string", "description": "PN yang habis/ditanyakan."},
                        "gudang": {"type": "string", "description": "Opsional. Batasi ke gudang tertentu (mis. 'Palembang')."},
                        "rangka": {"type": "string", "description": "Opsional. Nomor rangka/VIN — memperkaya data pengganti part MESIN (Weichai)."},
                    },
                    "required": ["part_number"],
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
                    "Cek harga MODAL part dari sumber SIMS secara live. Satuannya CNY "
                    "(yuan) — itu mata uang aslinya, sajikan apa adanya. ⛔ JANGAN "
                    "mengonversi ke rupiah kecuali user eksplisit memintanya; harga JUAL "
                    "rupiah datang dari Accurate (detail_part/cari_part), BUKAN dari kurs. "
                    "Gunakan saat user minta harga modal/SIMS."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "part_number": {"type": "string", "description": "Part Number yang dicek harganya."},
                        "konversi_idr": {
                            "type": "boolean",
                            "description": ("true HANYA bila user eksplisit minta harga SIMS "
                                            "dalam rupiah/dikonversi/'berapa kalau di-rupiah-kan'. "
                                            "Default false = CNY apa adanya."),
                        },
                    },
                    "required": ["part_number"],
                },
            },
        })

    # Penawaran Penjualan Accurate — admin selalu; staf lain bila diberi centang
    # 'ai_penawaran' (memuat harga jual & mengikat perusahaan). Nomor WAJIB manual.
    if _can_penawaran(user):
        specs.append({
            "type": "function",
            "function": {
                "name": "buat_penawaran",
                "description": (
                    "Buat Penawaran Penjualan (Sales Quotation) di Accurate untuk seorang "
                    "pelanggan berisi daftar barang, lalu hasilkan PDF resmi Accurate yang "
                    "bisa diunduh/dikirim. AKSES TERBATAS (diberikan admin lewat Menu Control).\n"
                    "⛔ NOMOR dibuat OTOMATIS oleh sistem = 'MASPART-01', 'MASPART-02', dst. "
                    "JANGAN minta/menetapkan nomor ke user; penomoran otomatis Accurate TIDAK "
                    "PERNAH dipakai.\n"
                    "⛔ Sistem HANYA mengatur KUANTITAS tiap part & membuat penawaran — tidak "
                    "mengubah apa pun yang lain. HARGA memakai harga jual Accurate apa adanya "
                    "(JANGAN menetapkan/menawar harga).\n"
                    "Pelanggan dicocokkan dari nama (Accurate mencocokkan sebagian, mis. 'cio' "
                    "→ PT ARGCIO JAYA ABADI). Bila BANYAK pelanggan cocok (mis. 'jaya'), tool "
                    "mengembalikan daftar kandidat — TANYAKAN ke user mana yang dimaksud, "
                    "jangan menebak. Tiap barang dari Part Number (harus ada di Accurate)."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "pelanggan": {"type": "string",
                                      "description": "Nama pelanggan (dicari di Accurate; pencocokan sebagian)."},
                        "barang": {
                            "type": "array",
                            "description": "Daftar barang penawaran (hanya Part Number & kuantitas).",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "part_number": {"type": "string", "description": "Part Number barang (harus ada di Accurate)."},
                                    "qty": {"type": "number", "description": "Kuantitas."},
                                },
                                "required": ["part_number", "qty"],
                            },
                        },
                        "tanggal": {"type": "string",
                                    "description": "Tanggal dd/mm/yyyy (opsional; default hari ini)."},
                        "catatan": {"type": "string", "description": "Keterangan (opsional)."},
                    },
                    "required": ["pelanggan", "barang"],
                },
            },
        })

    # Permintaan Barang (Purchase Requisition) — TULIS ke Accurate, ADMIN saja.
    if _is_admin(user):
        specs.append({
            "type": "function",
            "function": {
                "name": "buat_permintaan_barang",
                "description": (
                    "Buat PERMINTAAN BARANG (Purchase Requisition) di Accurate — dokumen "
                    "permintaan stok ke bagian pembelian. Dipakai saat user minta 'masukkan "
                    "ke permintaan barang Accurate', 'buatkan permintaan barang', 'request "
                    "stok part ini ke pembelian' — termasuk melanjutkan daftar yang BARU "
                    "SAJA tampil di chat (mis. hasil part_fast_moving): ambil PN-nya PERSIS "
                    "dari daftar itu.\n"
                    "⚠️ OPERASI TULIS & PERMANEN. WAJIB 2 LANGKAH: panggil DULU tanpa "
                    "konfirmasi → tampilkan PRATINJAU (nomor, tanggal, daftar PN+qty) dan "
                    "MINTA PERSETUJUAN user; hanya setelah user setuju panggil lagi dengan "
                    "konfirmasi=true. ⛔ JANGAN langsung konfirmasi=true.\n"
                    "⛔ NOMOR dibuat sistem = 'PERMINTAAN-01', 'PERMINTAAN-02', dst — jangan "
                    "minta nomor ke user. QTY: pakai yang user sebut; yang tak disebut diisi "
                    "1 dan itu DIBERITAHUKAN di pratinjau. Kolom wajib Accurate (Sektor, No "
                    "Unit, Kts Jkt) diisi sistem: 'MASPART', 'STOK', dan stok Jakarta saat "
                    "ini. HARGA tidak diisi (urusan bagian pembelian).\n"
                    "⛔ Asisten HANYA bisa MEMBUAT: tak bisa mengubah, menghapus, atau "
                    "menyetujui dokumen. PN yang tak ada di Accurate → SELURUH permintaan "
                    "dibatalkan (jangan ganti dengan PN mirip)."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "barang": {
                            "type": "array",
                            "description": ("Daftar barang yang diminta — PN diambil PERSIS "
                                            "dari hasil tool/daftar di percakapan ini."),
                            "items": {
                                "type": "object",
                                "properties": {
                                    "part_number": {"type": "string", "description": "Part Number (harus ada di Accurate)."},
                                    "qty": {"type": "number", "description": "Kuantitas diminta. Kosong = 1."},
                                },
                                "required": ["part_number"],
                            },
                        },
                        "konfirmasi": {
                            "type": "boolean",
                            "description": ("true HANYA setelah user menyetujui pratinjau. "
                                            "Default false = pratinjau."),
                        },
                        "nomor": {"type": "string",
                                  "description": "Ikutkan 'nomor_diusulkan' dari pratinjau saat konfirmasi."},
                        "tanggal": {"type": "string",
                                    "description": "Tanggal dd/mm/yyyy (opsional; default hari ini)."},
                        "sektor": {"type": "string",
                                   "description": "Kolom wajib 'Sektor'. Kosong = MASPART."},
                        "no_unit": {"type": "string",
                                    "description": "Kolom wajib 'No Unit'. Kosong = STOK (permintaan pengisian stok)."},
                        "catatan": {"type": "string", "description": "Keterangan dokumen (opsional)."},
                    },
                    "required": ["barang"],
                },
            },
        })

    # Template Excel kosong — TANPA lampiran (semua peran). User isi PN+Qty lalu
    # unggah lagi untuk diolah/dijadikan penawaran.
    specs.append({
        "type": "function",
        "function": {
            "name": "template_excel_part",
            "description": (
                "Buat TEMPLATE Excel KOSONG untuk daftar/permintaan part (kolom: No, "
                "Part Number, Nama Part, Qty, Keterangan). Pakai saat user minta 'kasih "
                "template', 'format excel buat pesan part', 'contoh file daftar part', atau "
                "belum punya file dan mau menyusun permintaan. User isi PN & Qty lalu unggah "
                "lagi untuk diproses (isi stok/harga/status, atau jadikan penawaran)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "dengan_contoh": {"type": "boolean",
                                      "description": "Sertakan 1 baris contoh (default true)."},
                },
            },
        },
    })

    # Pembaca satu bagian pengetahuan — HANYA ditawarkan bila store-nya memang
    # berisi. Spec dikirim di SETIAP panggilan API, jadi instalasi yang belum
    # punya pengetahuan tak perlu membayar tokennya sama sekali.
    if pengetahuan.available():
        specs.append({
            "type": "function",
            "function": {
                "name": "buka_pengetahuan",
                "description": (
                    "Buka SATU bagian pengetahuan internal MASPART secara UTUH: teks "
                    "penuh tanpa dipotong, TABEL LENGKAP semua barisnya, gambar bagian "
                    "itu, plus daftar bagian lain di dokumen yang sama. Pakai SETELAH "
                    "cari_pengetahuan ketika jawaban butuh isi lengkap — seluruh langkah "
                    "prosedur, seluruh baris tabel (syarat/tarif/spesifikasi), atau "
                    "gambar penjelas bagian tertentu. 'dokumen' & 'bagian' WAJIB disalin "
                    "PERSIS dari hasil cari_pengetahuan; jangan mengarang judul. Judul "
                    "salah → tool mengembalikan daftar yang sah, pilih dari daftar itu. "
                    "Kosongkan 'bagian' untuk melihat daftar isi dokumen."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "dokumen": {"type": "string",
                                    "description": "Judul dokumen PERSIS seperti field 'dokumen'/'judul' pada hasil cari_pengetahuan."},
                        "bagian": {"type": "string",
                                   "description": "Judul bagian PERSIS seperti field 'judul' pada hasil cari_pengetahuan. Kosong → daftar isi dokumen."},
                        "halaman": {"type": "integer",
                                    "description": "Alternatif 'bagian': nomor halaman sumber."},
                        "hanya": {"type": "string", "enum": ["semua", "tabel", "gambar"],
                                  "description": "Batasi isi yang dikembalikan. Default 'semua'."},
                    },
                    "required": ["dokumen"],
                },
            },
        })

    # Excel unggahan user — tool ini HANYA ada bila ada file terlampir di
    # percakapan ini. Tanpa lampiran, model tak melihatnya sama sekali.
    if sheet_id:
        specs.append({
            "type": "function",
            "function": {
                "name": "sheet_ringkasan",
                "description": (
                    "Baca isi file Excel yang BARU diunggah user di chat ini: nama sheet, "
                    "jumlah baris/kolom, nama tiap kolom beserta PERAN yang terdeteksi "
                    "(part_number/part_name/stok/qty/harga/lain), berapa Part Number yang "
                    "dikenal katalog, dan beberapa baris contoh. Kini juga: fill-rate & contoh "
                    "nilai tiap kolom, jumlah PN tak dikenal, serta SHEET LAIN di workbook "
                    "('sheet_lain_detail'). Panggil ini lebih dulu bila user bertanya 'isinya "
                    "apa' atau sebelum mengisi kolom."
                ),
                "parameters": {"type": "object", "properties": {}},
            },
        })
        specs.append({
            "type": "function",
            "function": {
                "name": "sheet_pilih_sheet",
                "description": (
                    "Pindah SHEET AKTIF pada file Excel yang sudah diunggah (workbook multi-sheet). "
                    "Default hanya sheet PERTAMA yang aktif & bisa diisi; bila user memaksudkan tab "
                    "lain (lihat 'sheet_lain_detail' di sheet_ringkasan), panggil ini dengan nama "
                    "sheet-nya. Setelah pindah, kolom & isi tab itu bisa langsung diisi seperti biasa "
                    "(sheet_id tetap sama). File tunggal / terlalu besar tak bisa pindah — minta user "
                    "mengunggah ulang tab yang diinginkan."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "nama_sheet": {
                            "type": "string",
                            "description": "Nama sheet/tab tujuan (persis seperti di 'sheet_lain_detail').",
                        },
                    },
                    "required": ["nama_sheet"],
                },
            },
        })
        # Pilihan isi kolom: harga_sims HANYA ditawarkan ke admin/SEE_ALL.
        pilihan = [ai_sheet.ISI_STOK, ai_sheet.ISI_NAMA, ai_sheet.ISI_HARGA_ACCURATE,
                   ai_sheet.ISI_HARGA_LOKAL,
                   ai_sheet.ISI_PENGGANTI, ai_sheet.ISI_CROSS_REF, ai_sheet.ISI_BERAT,
                   ai_sheet.ISI_DIMENSI, ai_sheet.ISI_PEMENUHAN]
        ket_sims = (" 'harga_accurate'=harga JUAL rupiah dari indeks Accurate ('harga_lokal' "
                    "= alias lama, sama persis); 'pengganti'=PN pengganti (supersession); "
                    "'cross_ref'=cross-reference "
                    "merek filter (Fleetguard/Donaldson/dll); 'berat'=berat tertagih kg; "
                    "'dimensi'=ukuran P×L×T cm; 'rencana_pemenuhan'=gudang mana bisa penuhi qty.")
        if _can_sims(user):
            pilihan.append(ai_sheet.ISI_HARGA_SIMS)
            ket_sims += (" 'harga_sims'=harga MODAL SIMS live, diisi dalam CNY apa adanya "
                         "(⛔ jangan set konversi_idr kecuali user minta rupiah; harga JUAL "
                         "rupiah = 'harga_lokal' dari Accurate) — khusus admin.")
        specs.append({
            "type": "function",
            "function": {
                "name": "sheet_isi_kolom",
                "description": (
                    "SATU-SATUNYA alat untuk MENGISI Excel unggahan user: kolom data MASPART "
                    "(stok/harga/nama/pengganti/…), penanda status & rekap, DAN gambar (foto "
                    "part + gambar teknis/exploded) — semuanya ke SATU file Excel yang bisa "
                    "diunduh. Dipakai saat user minta 'tambahkan stok', 'isikan nama & harga', "
                    "'stok gudang Jakarta dan Pekanbaru', 'lengkapi PN pengganti', 'tambah cross "
                    "reference filter', 'isi berat/dimensi', 'gudang mana bisa penuhi', 'isikan "
                    "fotonya', 'isikan gambar exploded view/gambar teknisnya'. "
                    "⛔ PENTING: apa pun yang user minta dalam satu permintaan — beberapa data, "
                    "beberapa gudang, foto, gambar teknis — masukkan SEMUANYA dalam SATU "
                    "panggilan ('kolom' untuk data + 'gambar' untuk foto/exploded) → hasilnya "
                    "SATU file berisi semua kolom bersebelahan. JANGAN memanggil tool ini "
                    "berkali-kali dalam satu giliran; panggil lebih dari sekali HANYA bila user "
                    "eksplisit minta filenya DIPISAH. "
                    "Baris yang Part Number-nya tak ditemukan dibiarkan KOSONG. "
                    "Set 'tandai_status'=true untuk kolom Status + WARNA baris (hijau ready/merah "
                    "kosong-kurang/kuning tak-ketemu-atau-ada-pengganti) + saran 'mungkin maksud'; "
                    "'rekap'=true untuk blok RINGKASAN (jumlah, subtotal, PPN, berat, ongkir). "
                    "Boleh dipanggil HANYA dengan tandai_status/rekap/gambar (tanpa 'kolom'). "
                    "⛔ GAMBAR: dicocokkan lewat PART NUMBER, TIDAK PERNAH lewat nama part "
                    "(pencarian nama di SIMS 'mengandung kata' → gambar part LAIN). Untuk "
                    "gambar='exploded' WAJIB TANYA DULU apakah user punya nomor rangka/VIN: ADA "
                    "→ isi 'rangka' (figure unit itu sendiri, paling tepat); TIDAK ADA → "
                    "lintas_model=true (figure EPC mana pun yang memuat PN itu — peringatan "
                    "lintas-model WAJIB disampaikan). Tanpa keduanya tool hanya mengembalikan "
                    "perintah bertanya. ⚠️ 'exploded' LAMBAT: maks 60 PN per-VIN / 25 PN lintas "
                    "model & unduhan pertama butuh beberapa menit (sampaikan ke user)."
                    + ket_sims
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "kolom": {
                            "type": "array",
                            "description": ("Daftar kolom yang akan diisi — SEMUA masuk ke file "
                                            "yang sama. Contoh: [{isi:'stok',gudang:'Jakarta'}, "
                                            "{isi:'stok',gudang:'Pekanbaru'}, {isi:'harga_accurate'}]."),
                            "items": {
                                "type": "object",
                                "properties": {
                                    "isi": {"type": "string", "enum": pilihan,
                                            "description": "Data untuk kolom ini."},
                                    "gudang": {
                                        "type": "string",
                                        "description": ("KHUSUS isi='stok': nama gudang (mis. "
                                                        "'Jakarta') → kolom stok gudang itu. "
                                                        "Kosongkan untuk stok TOTAL semua gudang."),
                                    },
                                    "nama_kolom": {
                                        "type": "string",
                                        "description": ("Opsional: nama header atau huruf kolom "
                                                        "('D') tujuan. Kosong = nama otomatis."),
                                    },
                                },
                                "required": ["isi"],
                            },
                        },
                        "kolom_pn": {
                            "type": "string",
                            "description": ("Kolom sumber Part Number. Kosongkan bila sudah "
                                            "terdeteksi otomatis (lihat sheet_ringkasan)."),
                        },
                        "tandai_status": {
                            "type": "boolean",
                            "description": ("Tambah kolom Status + WARNAI baris (ready/kosong/"
                                            "tak-ketemu) + saran 'mungkin maksud' PN mirip."),
                        },
                        "rekap": {
                            "type": "boolean",
                            "description": ("Tambah blok RINGKASAN di bawah tabel (jumlah item, "
                                            "total qty, subtotal+PPN [harga hanya admin], berat, ongkir)."),
                        },
                        "qty_kolom": {
                            "type": "string",
                            "description": ("Kolom Qty (untuk status 'kurang', rencana pemenuhan, "
                                            "total & subtotal). Kosong = deteksi otomatis."),
                        },
                        "kode_pos_tujuan": {
                            "type": "string",
                            "description": ("Kode pos tujuan (opsional, utk estimasi ongkir di rekap)."),
                        },
                        "konversi_idr": {
                            "type": "boolean",
                            "description": ("KHUSUS isi='harga_sims'. true HANYA bila user "
                                            "eksplisit minta harga SIMS dikonversi ke rupiah. "
                                            "Default false = kolom diisi CNY apa adanya."),
                        },
                        "gambar": {
                            "type": "array",
                            "description": ("Gambar yang ikut ditempel ke file yang SAMA: "
                                            "'foto' = foto FISIK part resmi SIMS; 'exploded' = "
                                            "GAMBAR TEKNIS/exploded view EPC ber-nomor balon. "
                                            "Isi keduanya bila user minta dua-duanya."),
                            "items": {"type": "string",
                                      "enum": [ai_sheet.JENIS_FOTO, ai_sheet.JENIS_EXPLODED]},
                        },
                        "rangka": {
                            "type": "string",
                            "description": ("KHUSUS gambar='exploded': nomor rangka/VIN unit — "
                                            "isi HANYA bila user menyebutkannya. ⛔ Jangan menebak."),
                        },
                        "lintas_model": {
                            "type": "boolean",
                            "description": ("KHUSUS gambar='exploded': true HANYA setelah user "
                                            "menyatakan TIDAK punya nomor rangka."),
                        },
                        "jumlah_foto": {
                            "type": "integer",
                            "description": "KHUSUS gambar='foto': foto per part (1-3). Kosong = 2.",
                        },
                    },
                },
            },
        })
        if _can_penawaran(user):
            specs.append({
                "type": "function",
                "function": {
                    "name": "sheet_jadi_penawaran",
                    "description": (
                        "Jadikan Excel unggahan (kolom Part Number + Qty) → PENAWARAN "
                        "Penjualan Accurate resmi + PDF (kartu unduh). AKSES TERBATAS "
                        "(diberikan admin lewat Menu Control). PN & Qty "
                        "dibaca dari file; harga = harga jual Accurate apa adanya; nomor = "
                        "MASPART-NN. ⛔ PN yang tak ada di Accurate → penawaran DIBATALKAN + "
                        "daftar PN (JANGAN pakai saran/pengganti untuk penawaran). Butuh nama "
                        "pelanggan (>1 cocok → minta user pilih). Pakai saat user minta 'buatkan "
                        "penawaran dari file ini', 'jadikan quotation', 'SQ dari daftar ini'."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "pelanggan": {"type": "string",
                                          "description": "Nama pelanggan (dicocokkan di Accurate)."},
                            "kolom_pn": {"type": "string",
                                         "description": "Kolom Part Number (opsional; default deteksi)."},
                            "kolom_qty": {"type": "string",
                                          "description": "Kolom Qty (opsional; default deteksi)."},
                            "baris_bermasalah": {
                                "type": "string", "enum": ["batal", "lewati"],
                                "description": ("Baris qty kosong/tak valid: 'batal' (default, "
                                                "penawaran dibatalkan + daftar) atau 'lewati'."),
                            },
                            "tanggal": {"type": "string",
                                        "description": "Tanggal dd/mm/yyyy (opsional; default hari ini)."},
                            "catatan": {"type": "string", "description": "Keterangan (opsional)."},
                        },
                        "required": ["pelanggan"],
                    },
                },
            })
        specs.append({
            "type": "function",
            "function": {
                "name": "sheet_isi_part_number",
                "description": (
                    "KEBALIKAN sheet_isi_kolom: Excel unggahan berisi kolom NAMA part (bukan PN) → "
                    "carikan Part Number-nya lalu hasilkan Excel baru. Dipakai saat user minta "
                    "'isikan part numbernya dari nama ini', 'lengkapi PN yang belum ada untuk unit "
                    "RJ...'. WAJIB nomor rangka/VIN: PN dicocokkan HANYA dari BOM unit itu "
                    "(deterministik) — tanpa rangka, satu nama cocok ke banyak PN. Bila file sudah "
                    "punya kolom Part Number, HANYA sel KOSONG yang diisi (PN yang sudah ada TAK "
                    "ditimpa). Nama yang cocok UNIK diisi; yang ambigu (>1 PN) atau tak ada di BOM "
                    "DIBIARKAN KOSONG (tak ditebak)."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "rangka": {
                            "type": "string",
                            "description": "Nomor rangka/VIN unit — sumber daftar part (BOM) untuk "
                                           "mencari PN. WAJIB.",
                        },
                        "kolom_nama": {
                            "type": "string",
                            "description": ("Kolom sumber NAMA part — nama header atau huruf kolom "
                                            "Excel. Kosongkan bila sudah terdeteksi otomatis "
                                            "(lihat sheet_ringkasan)."),
                        },
                        "kolom_tujuan": {
                            "type": "string",
                            "description": ("Kolom untuk menaruh Part Number — nama header atau "
                                            "huruf kolom. Kosongkan → kolom baru 'Part Number "
                                            "(EPC)' ditambahkan di ujung."),
                        },
                    },
                    "required": ["rangka"],
                },
            },
        })
        specs.append({
            "type": "function",
            "function": {
                "name": "sheet_cek_qty",
                "description": (
                    "Isi & VALIDASI kolom Qty (jumlah) file Excel dari BOM unit (per nomor "
                    "rangka). Dipakai saat user minta 'cek jumlahnya benar tidak', 'isikan qty "
                    "dari unit', 'validasi qty'. Untuk tiap baris ber-Part Number: sel Qty yang "
                    "KOSONG diisi dengan jumlah terpasang di unit (dari BOM), dan bila qty yang "
                    "DITULIS user BEDA dari BOM ditandai di kolom 'Cek Qty' (qty user TAK "
                    "ditimpa). WAJIB nomor rangka/VIN."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "rangka": {
                            "type": "string",
                            "description": "Nomor rangka/VIN unit — sumber jumlah (qty) per part.",
                        },
                        "kolom_qty": {
                            "type": "string",
                            "description": ("Kolom Qty — nama header atau huruf kolom. Kosongkan "
                                            "bila terdeteksi otomatis; bila tak ada, kolom 'Qty' "
                                            "baru dibuat."),
                        },
                        "kolom_pn": {
                            "type": "string",
                            "description": "Kolom sumber Part Number. Kosongkan bila terdeteksi otomatis.",
                        },
                    },
                    "required": ["rangka"],
                },
            },
        })
        # Cek garansi MASSAL dari Excel (kolom VIN/rangka) → Excel hasil.
        # Gerbang ai_garansi (sama dg tool garansi lain), READ-only.
        if _can_garansi(user):
            specs.append({
                "type": "function",
                "function": {
                    "name": "sheet_garansi_massal",
                    "description": (
                        "CEK STATUS GARANSI BANYAK unit sekaligus dari Excel lampiran (kolom "
                        "nomor rangka/VIN) → Excel hasil (aktif/sisa hari/% terpakai + spek "
                        "per unit). Pakai saat user minta 'cek garansi semua unit di file ini', "
                        "'audit garansi armada dari Excel'."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "kolom_rangka": {"type": "string", "description": "Kolom nomor rangka/VIN. Kosongkan bila terdeteksi otomatis."},
                        },
                    },
                },
            })
        # Isi nama unit MASSAL ke telematics dari Excel (frame → nama). ADMIN-ONLY
        # + operasi TULIS 2 langkah (pratinjau lalu konfirmasi).
        if _is_admin(user):
            specs.append({
                "type": "function",
                "function": {
                    "name": "sheet_isi_nama_telematik",
                    "description": (
                        "ISI NAMA UNIT MASSAL ke telematics/GPS dari file Excel yang "
                        "dilampirkan (kolom nomor rangka + kolom nama). ⚠️ OPERASI TULIS "
                        "ke server Sinotruk, PERMANEN & massal. WAJIB 2 langkah: panggil "
                        "DULU tanpa konfirmasi → tampilkan PRATINJAU (berapa unit akan "
                        "berubah: nama lama→baru, berapa sudah sama, berapa frame tak ada "
                        "di telematics) dan MINTA PERSETUJUAN user; hanya setelah user "
                        "setuju panggil lagi konfirmasi=true untuk menerapkan. ⛔ JANGAN "
                        "langsung konfirmasi=true."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "kolom_rangka": {"type": "string", "description": "Kolom nomor rangka/frame. Kosongkan bila terdeteksi otomatis."},
                            "kolom_nama": {"type": "string", "description": "Kolom nama/label baru. Kosongkan bila terdeteksi otomatis."},
                            "konfirmasi": {"type": "boolean", "description": "true HANYA setelah user menyetujui pratinjau. Default false = pratinjau."},
                        },
                    },
                },
            })
            specs.append({
                "type": "function",
                "function": {
                    "name": "sheet_daftar_unit",
                    "description": (
                        "DAFTARKAN unit BARU MASSAL ke telematics/GPS dari Excel lampiran "
                        "(kolom VIN + serial GPS/sbh; opsional km & euro2). ⚠️ OPERASI TULIS "
                        "PERMANEN & massal. WAJIB 2 langkah: tanpa konfirmasi → PRATINJAU "
                        "(berapa unit BARU akan didaftar, berapa sudah terdaftar) & MINTA "
                        "PERSETUJUAN; setelah user setuju baru konfirmasi=true. ⛔ JANGAN "
                        "langsung konfirmasi=true."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "kolom_vin": {"type": "string", "description": "Kolom VIN. Kosongkan bila terdeteksi otomatis."},
                            "kolom_sbh": {"type": "string", "description": "Kolom serial perangkat GPS (sbh). Kosongkan bila terdeteksi otomatis."},
                            "kolom_km": {"type": "string", "description": "Opsional: kolom kilometer awal."},
                            "kolom_euro2": {"type": "string", "description": "Opsional: kolom penanda Euro 2."},
                            "konfirmasi": {"type": "boolean", "description": "true HANYA setelah user menyetujui pratinjau. Default false = pratinjau."},
                        },
                    },
                },
            })
            specs.append({
                "type": "function",
                "function": {
                    "name": "sheet_masukkan_fleet",
                    "description": (
                        "MASUKKAN unit ke FLEET MASSAL dari Excel lampiran (kolom unit "
                        "frame/VIN + kolom nama fleet). ⚠️ OPERASI TULIS. WAJIB 2 langkah: "
                        "tanpa konfirmasi → PRATINJAU (berapa unit akan dipindah, berapa "
                        "unit/fleet tak ditemukan) & MINTA PERSETUJUAN; setelah setuju baru "
                        "konfirmasi=true. ⛔ JANGAN langsung konfirmasi=true."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "kolom_unit": {"type": "string", "description": "Kolom unit (frame/VIN). Kosongkan bila terdeteksi otomatis."},
                            "kolom_fleet": {"type": "string", "description": "Kolom nama fleet tujuan. Kosongkan bila terdeteksi otomatis."},
                            "konfirmasi": {"type": "boolean", "description": "true HANYA setelah user menyetujui pratinjau. Default false = pratinjau."},
                        },
                    },
                },
            })

    # ── Gating pembeli (rombakan 3b 2026-07-17): tool INTERNAL tidak
    # ditawarkan ke akun pembeli — hemat token spec + model tak tergoda
    # memanggil tool yang handler-nya toh menolak/di luar kebutuhan pembeli.
    # (_DISPATCH tetap utuh; ini hanya penawaran spec.)
    if role == "pembeli":
        _GATED_PEMBELI = {"excel_stok_gudang", "banding_rangka_massal", "banding_rangka",
                          # sensus/banding internal utk staf — pembeli tetap punya jalur
                          # per-VIN (bom_dari_rangka/part_aus/cari_part_di_unit, EPC-first)
                          "daftar_transmisi_assy", "banding_assy", "banding_kategori",
                          "spek_massal_rangka", "banding_konfigurasi_rangka"}
        specs = [s for s in specs if s["function"]["name"] not in _GATED_PEMBELI]

    # ── Gating STOK (Menu Control 'Kolom Stok'): tool yang SELURUH gunanya
    # adalah stok tak ditawarkan ke staf yang izinnya dimatikan — sia-sia
    # dipanggil karena _strip_stok di _run_tool akan mengosongkan hasilnya,
    # dan _allowed_tool_names ikut menolak eksekusinya (diturunkan dari sini).
    if not _boleh_stok(user):
        _GATED_STOK = {"stok_accurate", "stok_gudang", "stok_tertahan",
                       "alternatif_ready", "excel_stok_gudang"}
        specs = [s for s in specs if s["function"]["name"] not in _GATED_STOK]

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


