# -*- coding: utf-8 -*-
# ai_parts/p2_tool_specs.py — BAGIAN dari modul app.services.ai_assistant (SATU namespace).
# Dimuat berurutan oleh ai_assistant.py (loader) via exec — sengaja BUKAN
# submodul terpisah: monkeypatch test `ai.<attr>`, relative import, dan
# resolusi global lintas-bagian tetap bekerja persis seperti file tunggal.
# Urutan muat & pembagian: lihat _PARTS di ai_assistant.py.
from __future__ import annotations

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
                    "Ambil detail SATU Part Number persis: nama, STOK (utama dari ERP Accurate, "
                    "disinkron berkala — total + rincian per gudang; fallback Excel bila Accurate "
                    "tak tersedia; lihat field 'sumber_stok'), harga jual lokal, dan SPESIFIKASI "
                    "fisik resmi (berat kg, dimensi cm, satuan, merek). Ini tool utama untuk "
                    "pertanyaan stok/berat/dimensi SATU PN. "
                    "⛔ HANYA untuk satu PN: bila user menyebut 2 PN atau lebih — termasuk untuk "
                    "berat/dimensi — pakai cek_massal_part (SATU panggilan). Memanggil tool ini "
                    "berulang untuk banyak PN akan DITOLAK sistem setelah beberapa kali, dan "
                    "sisanya tidak akan pernah terjawab."
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
                "name": "cek_massal_part",
                "description": (
                    "⭐ CEK BANYAK Part Number SEKALIGUS dalam SATU panggilan — nama + stok "
                    "(total & per gudang) + harga + BERAT tertagih (kg) tiap PN. WAJIB pakai "
                    "ini saat user menyebut/menempel ≥2 PN ('cek PN ini semua', daftar PN, "
                    "dan JUGA saat user minta BERAT/DIMENSI beberapa part) — ⛔ JANGAN "
                    "memanggil detail_part berulang: sistem akan MENOLAKnya setelah beberapa "
                    "kali dan sisa PN tak akan terjawab. PN yang tidak ada ditandai jujur. "
                    "Set dimensi=true bila user memang menanyakan UKURAN/DIMENSI (agak lambat, "
                    "dibatasi 40 PN pertama). Set excel=true untuk file Excel unduhan. "
                    "Hasil bisa langsung dijadikan penawaran (buat_penawaran) bila user mau."
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
                    "Stok dari sistem akunting/ERP Accurate untuk satu Part Number persis "
                    "(disinkron berkala dari Accurate): 'stok_dapat_dijual' + 'stok_per_gudang' "
                    "(rincian kuantitas per gudang/cabang, mis. 01.Jakarta, 05.Makasar). Pakai "
                    "bila user tanya stok di Accurate, stok per cabang/gudang, atau "
                    "untuk membandingkan stok Accurate vs stok katalog lokal. Ini SUMBER "
                    "TAMBAHAN, tidak menggantikan stok gudang lokal dari detail_part."
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
                    "→ pakai tool `diagnosa` (asisten perbaikan resmi Sinotruk yang menalar)."
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
                    "⭐ DIAGNOSA KERUSAKAN — pakai untuk 'kenapa …', 'apa penyebab kode X', "
                    "'bagaimana cara memperbaiki', atau KELUHAN/GEJALA truk ('RPM terkunci 1500', "
                    "'rem angin lemah', 'asap hitam'). Menggabungkan ASISTEN PERBAIKAN RESMI "
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
                    "user. Level MODEL utk perencanaan stok/penawaran; unit SPESIFIK "
                    "(ada nomor rangka) tetap part_aus_dari_rangka/cari_part_di_unit."
                ),
                "parameters": {
                    "type": "object",
                    "required": ["model"],
                    "properties": {
                        "model": {
                            "type": "string",
                            "description": "Model/jenis unit sesuai ucapan user, mis. 'NX400', 'HOWO NX 6X4', 'SITRAK C7H', atau kode model 'ZZ3257V404JF1'.",
                        },
                        "kategori": {
                            "type": "string",
                            "description": "Opsional, saring satu kategori: filter | rem | kopling | bearing_seal | belt | karet. Kosong = semua.",
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
                    "gearbox', dll. Kosongkan 'transmisi' & 'rangka' untuk daftar model. "
                    "Utk REPAIR KIT MESIN Weichai ('repair kit mesin unit X', 'paket servis/"
                    "overhaul mesin'): isi sumber='mesin' + 'rangka' (hanya unit bermesin "
                    "Weichai; disilang stok/harga lokal)."
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
                        "rangka": {"type": "string", "description": "Nomor rangka/VIN unit."},
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
                        "rangka": {"type": "string", "description": "Nomor rangka: VIN penuh atau frame number 8 digit (mis. SJ346500)."},
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
                    "BANDINGKAN PART BANYAK UNIT (>=2) SEKALIGUS — untuk 'apakah KABIN semua "
                    "unit PT X SAMA atau beda?', 'cek 5 nomor rangka ini kabinnya sama semua?', "
                    "'bandingkan rem unit A, B, C'. Input: DAFTAR nomor rangka (rangka_list) ATAU "
                    "nama customer/PT (customer — armada dari populasi, admin/'mas' saja). Isi "
                    "'kategori' (kabin/rem/mesin/transmisi/kopling/kelistrikan/sasis/gardan) untuk "
                    "SATU kategori, atau 'semua' untuk RINGKASAN semua kategori (mana yang seragam/"
                    "beda). Membandingkan SET PART NYATA tiap unit (Loading List per-VIN), "
                    "MENGELOMPOKKAN unit ber-set identik, verdict SERAGAM/BEDA dihitung SISTEM + "
                    "kartu unduh Excel. ⛔ Beda dari banding_rangka (HANYA 2 unit) & "
                    "banding_part_armada (SATU part saja). ⛔ JANGAN menyimpulkan sama/beda dari "
                    "kode model — itu menebak. HANYA Sinotruk/HOWO/SITRAK."
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
                        "assembly": {"type": "string", "description": "Assembly/komponen yang mau diurai — PN assembly (mis. 'AZ000052000229') atau nama/istilah (mis. 'v stay', 'thrust rod'; sumber='mesin': 'piston', 'injector', 'air compressor')."},
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
                        "no_mesin": {"type": "string", "description": "Nomor mesin / serial engine Weichai (mis. '4P24B000713')."},
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
                    "CEK SATU PART (mis. 'starter', 'alternator', 'filter oli') di BANYAK NOMOR "
                    "MESIN Weichai SEKALIGUS — versi massal part_dari_mesin. Pakai saat user "
                    "memberi DAFTAR nomor engine dan menanyakan satu komponen ('cek starter untuk "
                    "no engine A, B, C, …'). Efisien: mesin ber-konfigurasi sama diproses sekali. "
                    "Otomatis mendeteksi PENGGANTI (supersession) → memberi 'pn_order_terkini' "
                    "(PN resmi terbaru untuk dipesan) + silang stok/harga lokal. Set excel=true "
                    "untuk kartu unduh Excel. ⛔ JANGAN panggil part_dari_mesin berulang per nomor."
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
                    "CEK SATU PART (mis. 'injector', 'kampas rem', 'filter oli') di BANYAK NOMOR "
                    "RANGKA (VIN) SEKALIGUS — jalur EPC Sinotruk Atlas per-VIN. PASANGAN dari "
                    "cek_massal_part_mesin: yang itu untuk daftar NOMOR MESIN (Weichai), yang INI "
                    "untuk daftar NOMOR RANGKA (unit Sinotruk/HOWO/SITRAK — termasuk yang bermesin "
                    "MC). Pakai saat user memberi DAFTAR VIN dan menanyakan satu komponen ('cek "
                    "injector untuk rangka A, B, C, …'). Efisien: unit ber-konfigurasi sama "
                    "diproses sekali. Otomatis deteksi pengganti (supersession) → 'pn_order_terkini' "
                    "+ silang stok/harga lokal. Set excel=true untuk kartu unduh Excel. Istilah "
                    "Indonesia diterjemahkan otomatis. ⛔ JANGAN panggil cari_part_di_unit berulang."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "daftar_rangka": {"type": ["string", "array"], "items": {"type": "string"}, "description": "Daftar nomor rangka/VIN (array ATAU string dipisah baris/koma) — mis. ['LZZ1BG3H0SJ398963','LZZ1BG3H1SJ398969']."},
                        "part": {"type": "string", "description": "Komponen yang dicek di semua unit (mis. 'injector', 'kampas rem', 'filter oli')."},
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
                    "SPESIFIKASI/KONFIGURASI BANYAK UNIT sekaligus dari daftar NOMOR RANGKA — "
                    "model, seri, gerak (4×2/6×2/6×4), jenis (cargo/tractor/dump), emisi, "
                    "rem/ABS, mesin, gearbox, gardan (EPC resmi getVehicleConfig). Untuk 'apa "
                    "spek unit-unit ini' / pendataan armada. Set excel=true utk kartu unduh. "
                    "⚠️ Ini SPEK, bukan daftar part — utk part per unit pakai "
                    "cek_massal_part_rangka/bom_dari_rangka."
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
                        "part_number": {"type": "string", "description": "Part Number yang mau dicek penggantinya (mis. 'FG7101204246+001/1' atau '1000076563')."},
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
                        "part_number": {"type": "string", "description": "Part Number yang mau dicek dipakai di unit/model apa (mis. AZ1646901003)."},
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
                        "unit": {"type": "string", "description": "Opsional: frame/VIN SATU unit untuk cek detail & namanya (mis. SJ398956)."},
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
                        "cjh": {"type": "string", "description": "Frame/cjh (atau VIN) unit yang diganti namanya."},
                        "nama_baru": {"type": "string", "description": "Nama/label baru untuk unit."},
                        "konfirmasi": {"type": "boolean", "description": "true HANYA setelah user menyetujui pratinjau. Default false = pratinjau."},
                    },
                    "required": ["cjh", "nama_baru"],
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
                        "unit": {"type": "string", "description": "Frame/cjh atau VIN unit."},
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
                    "Isi SATU ATAU BEBERAPA kolom pada Excel unggahan user memakai data MASPART, "
                    "lalu hasilkan SATU file Excel yang bisa diunduh. Dipakai saat user minta "
                    "'tambahkan stok', 'isikan nama & harga', 'stok gudang Jakarta dan Pekanbaru', "
                    "'lengkapi PN pengganti', 'tambah cross reference filter', 'isi berat/dimensi', "
                    "'gudang mana bisa penuhi'. ⛔ PENTING: bila user minta beberapa data/gudang "
                    "sekaligus, masukkan SEMUANYA sebagai elemen 'kolom' dalam SATU panggilan → "
                    "hasilnya SATU file dengan kolom-kolom bersebelahan. JANGAN memanggil tool ini "
                    "berkali-kali. Baris yang Part Number-nya tak ditemukan dibiarkan KOSONG. "
                    "Set 'tandai_status'=true untuk kolom Status + WARNA baris (hijau ready/merah "
                    "kosong-kurang/kuning tak-ketemu-atau-ada-pengganti) + saran 'mungkin maksud'; "
                    "'rekap'=true untuk blok RINGKASAN (jumlah, subtotal, PPN, berat, ongkir). "
                    "Boleh dipanggil HANYA dengan tandai_status/rekap (tanpa 'kolom')." + ket_sims
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
                "name": "sheet_isi_foto",
                "description": (
                    "Tempelkan FOTO part resmi SIMS ke Excel unggahan user (default 2 foto per "
                    "part, di kolom baru paling kanan), lalu hasilkan file Excel yang bisa "
                    "diunduh. Dipakai saat user minta 'isikan fotonya', 'tambahkan gambar part', "
                    "'lengkapi dengan foto'. Foto dicocokkan lewat PART NUMBER. ⛔ Foto TIDAK "
                    "bisa dicari lewat NAMA part: pencarian nama di SIMS bersifat 'mengandung "
                    "kata' dan mengembalikan part LAIN (mis. nama 'Radiator' memunculkan PIPA "
                    "radiator) — memasang foto dari nama berarti memasang foto yang SALAH. Bila "
                    "file tak punya kolom Part Number, katakan itu apa adanya & minta kolom PN; "
                    "JANGAN menebak lewat nama. Part yang memang tak punya foto di SIMS ditandai "
                    "'-' dan tidak dikarang."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "jumlah": {
                            "type": "integer",
                            "description": "Foto per part (1-3). Kosong = 2.",
                        },
                        "kolom_pn": {
                            "type": "string",
                            "description": ("Kolom sumber Part Number. Kosongkan bila sudah "
                                            "terdeteksi otomatis (lihat sheet_ringkasan)."),
                        },
                    },
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


