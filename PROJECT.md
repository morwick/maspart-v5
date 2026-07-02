# MASPART — Dokumentasi Project & Server

> Dokumen onboarding untuk developer / AI agent. Tujuannya: siapa pun (atau AI
> mana pun) yang membuka repo ini bisa langsung paham **apa project-nya, stack-nya,
> cara deploy, dan cara akses server**.
>
> Terakhir diverifikasi: **2026-06-29** (oleh inspeksi langsung repo lokal + SSH ke server).
> Ditambah **§3.5 — Cara Kerja Aplikasi (deep-dive fungsional)** pada 2026-06-25 agar AI/dev
> langsung paham domain, alur data, logika pencarian + sinonim, AI tools, API & frontend.
> Update **2026-06-27**: tambah fitur **Repair Kit Transmisi** (data + tool AI + endpoint +
> tombol download Excel di Admin) — lihat §3.5.5a.
> Update **2026-06-29**: tambah fitur **Catalog BOM** — Asisten AI bisa **bandingkan isi part
> per KATEGORI antar unit & per PN ASSY** (4 tool AI baru: `banding_assy`/`isi_assy`/
> `banding_kategori`/`isi_kategori`) — lihat §3.5.5b. Menggantikan `transmisi_bom.json` lama.
> Update **2026-06-29 (EPC)**: integrasi **EPC Sinotruk resmi** — BOM pabrik PERSIS per-VIN
> (`cek_kendaraan`, `bom_dari_rangka` + breakdown kategori per-unit), **reverse PN→unit**
> (`unit_dari_part`), dan **auto-refresh token EPC** via SSO SimsCloud (tanpa captcha/manual).
> Lihat **§3.5.5c**.
> Update **2026-07-01**: (a) **Pohon KATEGORI EPC per-VIN + dekomposisi ASSEMBLY** — tool
> `kategori_unit` (semua kategori/assembly unit + turunannya) & `uraikan_assembly` (isi/komponen
> 1 assembly, mis. karet/seal v-stay) — §3.5.5d. (b) **Integrasi EPC WEICHAI** untuk part INTERNAL
> MESIN (unit bermesin Weichai) — tool `uraikan_mesin`, rantai SSO+BOM otomatis per-VIN — §3.5.5e.
> (c) **Guard anti-halusinasi PN** (jawaban dipatok ke data tool/riwayat; PN karangan diblokir/
> diganti "tidak ditemukan", termasuk follow-up) — §3.5.5f. (d) Menu admin **Monitoring User**
> (online/offline in-memory) — §3.5.11.

---

## 1. Ringkasan Project

**MASPART** adalah aplikasi web internal untuk **katalog & manajemen suku cadang
(spare part) alat berat / truk** (merek **Shantui, Sinotruk, Weichai**). Awalnya
dibangun dengan **Streamlit**, sekarang sedang/sudah dimigrasi ke arsitektur
**FastAPI (backend) + Next.js (frontend)**.

Fitur utama (berdasarkan halaman frontend & router backend):

- **Auth / login** (JWT, bcrypt) dengan peran admin & cabang/gudang
- **Search Part Number** — cari part dari index Excel
- **Cari by Foto** (`search-image`) — pencocokan gambar part pakai model **DINOv2** (torch) dari galeri CSV lokal
- **Compare** — bandingkan 2 part
- **Harga** — lookup & batch harga
- **Stok / Opname** — multi-gudang, scope per cabang
- **Populasi** — data populasi unit
- **Orders / Pesanan / Keranjang** — alur jual-beli + pembayaran + ongkir
- **Chat** — chat order & gudang
- **Asisten AI** — chatbot (DeepSeek, OpenAI-compatible)
- **Repair Kit Transmisi** — daftar komponen repair kit per **transmisi assy** (seal kit
  perpak + overhaul tambahan); ditanyakan ke **Asisten AI** dan tombol **unduh Excel muncul
  langsung di jawaban chat** untuk model yang dibahas
- **Admin panel** — users, gudang, upload, monitoring, image index, penjualan

---

## 2. Struktur Repo

```
maspart-main/
├── backend/            # FastAPI (Python)
│   ├── app/
│   │   ├── main.py         # entrypoint FastAPI (CORS, /health)
│   │   ├── deps.py         # auth dependency (JWT, require_admin)
│   │   ├── schemas.py
│   │   ├── core/           # config.py (env), security.py (JWT/bcrypt), ratelimit.py
│   │   ├── routers/        # auth, parts, harga, opname, orders, branch, buyer,
│   │   │                   #   chat, geo, ai, admin, populasi, repairkit
│   │   └── services/       # logika bisnis (part_index, catalog, gudang, harga,
│   │                       #   orders, payments, shipping, image_search, ai_assistant,
│   │                       #   repairkit, dll)
│   ├── shared/         # part_compare, sims_fetcher, sims_price_fetcher (di-reuse dari versi Streamlit)
│   ├── tools/          # append_gallery.py
│   ├── requirements.txt
│   ├── railway.toml    # config deploy Railway (alternatif)
│   ├── .env.example    # template env — SALIN ke .env
│   └── selftest.py     # test logika index+search tanpa server/network
│
├── frontend/           # Next.js 16 + React 19 + Tailwind 4 (TypeScript)
│   └── src/app/        # App Router: login, search, search-image, compare, harga,
│                       #   opname, populasi, orders/pesanan, keranjang, chat, asisten,
│                       #   batch, download, pilih-lokasi, cabang/*, admin/*
│
├── data/               # DATA APLIKASI (Excel part per merek + galeri + config)
│   ├── Shantui/  Sinotruk/  Wechai/   # file .xlsx katalog part
│   ├── embeddings.parquet              # embedding untuk cari-by-foto
│   ├── part_image_index_rows.csv       # index gambar part
│   ├── gudang_config.json
│   ├── sinonim/sinonim.json
│   ├── repairkit/transmisi.json        # repair kit per model transmisi assy (§3.5.5a)
│   ├── catalog_bom.json                # BOM per unit×kategori + assy_index (§3.5.5b, ~7.5MB)
│   └── manuals/                        # PDF manual
│
├── migrations/         # SQL migrations (Supabase/Postgres) 003..014
├── deploy/             # script & config deploy VPS (lihat §5)
├── .streamlit/         # config.toml + secrets.toml (warisan versi Streamlit)
└── .devcontainer/
```

> **Catatan:** `backend/venv/`, `frontend/node_modules/`, dan `.cache/*.pkl`
> (cache embedding) TIDAK perlu di-commit / dibangun ulang di server.

---

## 3. Tech Stack

| Layer    | Teknologi |
|----------|-----------|
| Backend  | Python, **FastAPI**, Uvicorn, Pydantic-settings, PyJWT, bcrypt |
| ML/Foto  | **torch + torchvision (CPU)**, DINOv2, Pillow, numpy (cari-by-foto) |
| Data     | pandas, openpyxl (baca Excel), file CSV/parquet lokal |
| Frontend | **Next.js 16.2.7**, React 19, TypeScript 5, Tailwind CSS 4, jsPDF |
| Database | **Supabase** (Postgres + Storage, remote) — tabel `users`, dll |
| Eksternal| RajaOngkir/Komerce (ongkir), Payment API Komerce (pembayaran), DeepSeek (AI) |

---

## 3.5 Cara Kerja Aplikasi (deep-dive untuk AI/Developer)

> Bagian ini menjelaskan **bagaimana aplikasi bekerja secara fungsional** supaya AI
> mana pun yang membaca repo ini langsung paham alur data, domain, dan konvensi —
> tanpa harus menelusuri semua file dulu.

### 3.5.1 Peran (role) & Autentikasi

Auth pakai **JWT Bearer** (header `Authorization: Bearer <token>`), expire **720 menit
(12 jam)**. Login memverifikasi password via **bcrypt** (dengan fallback kolom `password`
plaintext legacy) terhadap tabel **`users` di Supabase**. Setelah login, role di-*re-check*
dari DB tiap **30 detik** (cache di `deps.py`), jadi akun yang dinonaktifkan/diturunkan
role-nya otomatis ditolak ≤30 dtk (fail-open ke klaim token bila Supabase down).

| Role | Arti | Dependency | Hak khas |
|------|------|------------|----------|
| `admin` | Pengelola penuh | `require_admin` | semua data & gudang, panel admin, harga SIMS/modal |
| `pembeli` | Buyer (belanja) | `require_buyer`, `require_buyer_ready` | wajib **pilih lokasi gudang** dulu sebelum bisa beli |
| `user` (cabang) | Akun gudang/cabang | `require_branch` (`branch_label`) | stok & pesanan **discoped ke gudangnya** |
| akun `SEE_ALL` (`{"mas"}`) | Super-viewer | — | lihat **semua gudang** + akses **harga SIMS (modal/CNY)** |

Pemetaan akun→gudang ada di `services/gudang.py` (`ACCOUNT_GUDANG`, mis. `jakarta →
01.Jakarta`) + bisa diatur admin via `gudang_config`. `SEE_ALL_ACCOUNTS = {"mas"}`.

### 3.5.2 Model data katalog (paling penting)

- **Brand**: `Shantui/`, `Sinotruk/`, `Wechai/` (Weichai) di `data/`.
- **1 file `.xlsx` = 1 unit/model truk.** Nama unit (`simple_name`) diambil dari nama file
  setelah `" - "` (mis. `... - NX360 6X4 (LZZ1BLSG).xlsx` → unit `NX360 6X4 (LZZ1BLSG)`).
  Folder induk = kategori (mis. `Sinotruk/NX360HP`).
- **Kolom Excel part** dibaca dari `usecols=[1,3,4]` → **B=Part Number, D=Part Name,
  E=Quantity** (lihat `services/part_index.py::_process_file`).
- Index dibangun **in-memory, lazy, thread-safe**; di-cache ke disk `.cache/<hash>.pkl`
  (hash = path+size+mtime). Rebuild via `POST /api/parts/index/refresh` atau panel admin.
- Subfolder `stok/`, `harga/`, `populasi/` **bukan** data part (di-load terpisah).

### 3.5.3 Logika pencarian part

1. **Per Part Number** (`search_part_number`): match **substring** PN (uppercase),
   dedup per unit.
2. **Per Nama** (`search_part_name`): query dipecah jadi kata; tiap kata dicocokkan ke
   token kata nama part (substring dua arah), lalu **difilter** harus memuat frasa penuh.
3. **Ekspansi sinonim** (`ai_assistant._expand_query`) — inti fitur "paham bahasa
   bengkel": baca **`data/sinonim/sinonim.json`** (dibaca **segar tiap panggil**, tanpa
   restart), cocokkan **trigger** sebagai kata/frasa utuh, lalu tambahkan **keyword
   katalog (Inggris)** sebagai istilah cari tambahan. Contoh: `kampas kopling` → `driven
   disc, driven plate`; `dinamo cas` → `generator`.

**Format `sinonim.json`** = list of `{grup, triggers[], keywords[]}`:
- `triggers` = istilah lapangan/slang Indonesia (mis. `seher`, `laher`, `gardan reduksi`).
- `keywords` = kata kunci **persis seperti di katalog** (mis. `transmission shaft`).
- ⚠️ **Aturan emas:** sebelum menambah `keywords`, **verifikasi string-nya benar-benar
  muncul di nama part katalog** (substring), kalau tidak hasil pencarian = 0. Pakai
  istilah lapangan (serapan Belanda/Inggris) sebagai `triggers`, BUKAN terjemahan literal.
  Saat ini **259 entri** (terakhir diperluas 2026-06-25 dengan referensi 41 katalog Sinotruk).

### 3.5.4 Stok, harga, populasi

- **Stok**: `data/stok/stok.xlsx` (atau Supabase Storage). Dua format didukung: *single-total*
  lama & *multi-gudang* (header `Kode Barang`, kolom per gudang `01.Jakarta` dst + `Total`).
  Key PN = buang prefix `000001.`, uppercase. Disimpan `{PN: total}` + `{PN: {gudang: qty}}`.
- **Scope cabang**: akun cabang lihat stok gudangnya saja; bila kosong → gudang **terdekat
  yang masih ada stok** (haversine pakai koordinat di `gudang_config`). Admin/SEE_ALL → semua.
- **Harga**: `data/harga/harga.xlsx` → `{PN: "Rp x"}`. Plus **harga SIMS live** (CNY→IDR
  pakai kurs terkini) khusus admin/SEE_ALL (`services/sims.py`, `harga.py`).
- **Populasi**: data populasi unit (`services/populasi.py`).

### 3.5.5 Asisten AI (DeepSeek, tool-calling)

`services/ai_assistant.py` — chatbot OpenAI-compatible (DeepSeek). Alur: loop tool-calling;
kamus sinonim **juga disuntikkan ke system prompt** ("KAMUS ISTILAH LAPANGAN"). Tools:

| Tool | Untuk | Akses |
|------|-------|-------|
| `cari_part` | cari PN+nama sekaligus, auto ekspansi sinonim, bisa scope `unit` | semua |
| `detail_part` | detail 1 PN (stok total/per gudang, harga) | semua |
| `info_aplikasi` | ringkasan index/stok/harga/gudang/kurs | semua |
| `daftar_unit` | daftar unit/model truk tersedia | semua |
| `cari_kode_kesalahan` | DTC/fault code Sinotruk-HOWO (ECU Bosch) via SPN+FMI / P-code / kata kunci | semua |
| `repair_kit_transmisi` | komponen repair kit per **transmisi assy** (seal kit perpak / overhaul / semua), resolve via kode model · assy PN · nama unit | semua |
| `daftar_transmisi_assy` | daftar LENGKAP & pasti semua transmisi/gearbox assy di katalog (anti-undercount) | semua |
| `banding_assy` | **bandingkan ISI DALAM 2 PN assembly** (transmisi/kopling/gardan/mesin/kabin) → part sama/beda + % + verdict (§3.5.5b) | semua |
| `isi_assy` | isi dalam (BOM lengkap) 1 part assembly per PN | semua |
| `banding_kategori` | **bandingkan 1 KATEGORI antar 2 unit** (mis. rem NX400 vs V7X400) → part sama/beda + % + verdict (§3.5.5b) | semua |
| `isi_kategori` | daftar part 1 kategori untuk 1 unit (mis. "part rem di NX400") | semua |
| `cek_kendaraan` | spesifikasi/konfigurasi unit dari NOMOR RANGKA/VIN (gearbox/axle/engine/Euro) — EPC resmi (§3.5.5c) | semua |
| `bom_dari_rangka` | **BOM pabrik PERSIS per-VIN** dari EPC + `kategori_breakdown` (jumlah part per kategori unit ini) + filter `kategori`/`kata_kunci` (§3.5.5c) | semua |
| `unit_dari_part` | **REVERSE: PN → daftar model/unit yang memakainya** (EPC global, lintas semua model) (§3.5.5c) | semua |
| `kategori_unit` | **pohon KATEGORI EPC per-VIN** — semua kategori/assembly unit + turunannya (drill berlapis) (§3.5.5d) | semua |
| `uraikan_assembly` | **urai 1 ASSEMBLY → komponennya** (karet/seal/pin dari v-stay dll), per PN/nama, per-VIN (§3.5.5d) | semua |
| `uraikan_mesin` | **part INTERNAL MESIN Weichai per-VIN** (piston/kruk as/liner/cylinder head…) — EPC Weichai auto-SSO (§3.5.5e) | semua |
| `pesanan_saya`, `detail_pesanan` | pesanan milik buyer | `pembeli` |
| `rekap_penjualan`, `daftar_pesanan` | rekap & daftar pesanan (cabang auto-scoped) | `admin` / cabang |
| `harga_sims` | harga modal SIMS live (CNY→IDR) | `admin` / `SEE_ALL` |

**Fault codes**: `services/fault_codes.json` (diekstrak dari manual PDF). Field: `code,
english, desc_cn` (deskripsi Bahasa China → AI menerjemahkan), `spn, fmi, mil, svs`.

**Pemahaman transmisi assy**: system prompt (`domain_block`) mengajari AI bahwa PN
bergaya **`HW<5digit>...`** (mis. `HW19709XST201136`, `HW15710AC254082`) adalah **gearbox/
transmisi assembly** (变速器), bukan part kecil — supaya AI tahu menawarkan repair kit-nya.

### 3.5.5a Repair Kit Transmisi (data, AI tool, export Excel) — sejak 2026-06-27

Fitur untuk menjawab "apa saja isi repair kit transmisi X?" dan mengunduhnya sebagai Excel.

- **Data:** `data/repairkit/transmisi.json` — **12 model** gearbox (HOWO `HW…`, ZF `ZF16S2531TO`,
  Fast `8JS85TE`), total ~1087 PN. Disusun otomatis dari sheet **`05变速箱 Gearbox`** tiap
  unit (40 unit unik dari 41 file Sinotruk). Dibaca **segar tiap panggil** (seperti sinonim) →
  edit JSON langsung aktif **tanpa rebuild/restart** (cukup `scp` ke `/opt/maspart/data/repairkit/`).
- **Struktur per model:** `{model, tipe, unit[], assy_pn[], jumlah_seal_kit,
  jumlah_overhaul_tambahan, seal_kit:{oil_seal/gasket/o_ring}, overhaul_tambahan:{bearing/
  synchronizer/snap_ring}}`. **Bertingkat:** *seal kit (perpak)* = oil seal+gasket+O-ring;
  *overhaul tambahan* = bearing+synchronizer+snap ring. PN "virtual"/assy tidak bocor ke
  komponen (sudah divalidasi: 0 kontaminasi antar-model).
- **Service:** `services/repairkit.py` — `find(query)` (resolve via kode model · assy PN ·
  awalan · nama unit), `kit(entry, tingkat)`, `to_excel_bytes(model=None)` (workbook: sheet
  **Ringkasan** + 1 sheet komponen per model; kolom Tingkat/Kategori/Part Number/Nama).
- **Router:** `routers/repairkit.py` (prefix `/api/repairkit`) — `GET /transmisi` (daftar
  model), `GET /transmisi/export?model=` (unduh xlsx; `model` kosong = semua). Keduanya butuh login.
- **AI tool:** `repair_kit_transmisi` (lihat tabel §3.5.5). Saat tool ini dipanggil,
  `ai_assistant.chat()` mengembalikan field tambahan **`repairkit_models: [..]`** (kode model
  yang dibahas) di hasil chat.
- **Resolve per-VIN via EPC (sejak 2026-07-02):** argumen opsional **`rangka`** — bila user
  menyebut nomor rangka/VIN, tool bertanya ke EPC config (`epc.lookup`, port 18080 publik)
  gearbox PERSIS unit itu, lalu kit di-resolve dari kode itu (mengalahkan tebakan dari nama
  unit; dua unit "sama" bisa beda gearbox). `gearboxModelCode` EPC = string deskriptif China
  (mis. `HW25712XST变速箱+HW50直联式取力器(带液力缓速器)`) → kode model diambil dari token
  Latin di AWAL string (`_gearbox_from_rangka`; bagian `+…取力器` = PTO, bukan gearbox).
  Hasil memuat `resolusi_epc` (gearbox terpasang + sumber); EPC gagal → fallback perilaku
  lama + catatan jujur "perkiraan per-model". Isi kit tetap dari data lokal terkurasi
  (EPC tidak punya konsep repair kit).
- **Frontend:** tombol **⬇️ Excel `<model>`** tampil **di dalam balasan Asisten AI** (komponen
  `RepairKitDownloads` di `app/asisten/page.tsx`) tiap kali `repairkit_models` terisi —
  klik → `exportRepairKit(token, model)` (di `lib/api.ts`) → unduh xlsx. **Tidak ada halaman
  admin terpisah** (sengaja dihapus; download menyatu dengan alur tanya-jawab asisten).

### 3.5.5b Catalog BOM — bandingkan isi part per KATEGORI & per ASSY — sejak 2026-06-29

Fitur agar Asisten AI **paham isi part tiap kategori tiap unit** dan bisa menjawab "apakah
isi A dan B sama/beda?". Generalisasi dari fitur transmisi (§3.5.5a) ke **semua kategori**.

- **Sumber & data:** tiap file unit Sinotruk/HOWO punya **12 sheet = 12 KATEGORI** (`01驾驶室
  Driver's cab` … `12上装`). Build script `backend/tools/build_catalog_bom.py` memindai SEMUA
  sheet berawalan 2-digit (`^\d{2}`) tiap unit → menghasilkan **`data/catalog_bom.json`** (~7.5MB):
  `{kategori: {kode→nama}, units: {unit→{kategori: {kode→{assy_pn, jumlah, parts[]}}}}, assy_index}`.
  Saat ini **40 unit · 12 kategori · ~108k baris part · 123 PN assy terindeks**. ⚠️ Kategori
  bernomor **hanya ada di truk Sinotruk/HOWO**; brand lain (Shantui/Sany/Wechai) pakai sheet
  tunggal tanpa nomor → tidak masuk fitur ini. Baris pertama tiap sheet "assembly" (kode
  01/02/04/05/06/07) = **PN assy** kategori itu → masuk `assy_index`.
- **Service:** `services/catalog_bom.py` — **di-cache per-mtime** (file besar; parse sekali,
  reload otomatis bila `scp` ulang). Fungsi: `resolve_kategori` (sinonim lapangan ID: rem,
  kopling, gardan depan=06/belakang=07, kelistrikan, sasis, kabin, mesin, karoseri…),
  `resolve_unit`, `compare_units(u1,u2,kat)`, `category_parts(u,kat)`, `resolve_assy`,
  `compare_assy(pn1,pn2)`, `assy_detail(pn)`. Verdict **terkalibrasi** via Jaccard: `identik` /
  `praktis_identik` (≥95%) / `sangat_mirip` (≥75%) / `mirip_satu_keluarga` (≥45%) / `berbeda`.
- **Opsi B (unit patokan):** bila 1 PN assy dipakai banyak unit (isi sedikit beda karena versi
  katalog), perbandingan memakai **SATU unit patokan = yang part-nya terlengkap** (bukan union)
  → adil 1-unit-lawan-1-unit. Field `unit_patokan` diekspos agar AI bisa sebut "menurut katalog
  unit Y". Noise ~10-30 part antar-versi diingatkan di prompt agar tak salah simpul.
- **AI tools (4):** `banding_assy`, `isi_assy`, `banding_kategori`, `isi_kategori` (lihat §3.5.5).
  Diajarkan di `domain_block` kapan pakai yang mana (2 PN assy → `banding_assy`; kategori antar
  2 unit → `banding_kategori`). Contoh nyata: `HW19709XST201136` vs `HW19709XST237036` → 250 part
  sama, 67% → "mirip satu keluarga 9-speed"; **rem** NX400 vs V7X400 → 11.6% → "berbeda" (wajar).
- **Tanpa endpoint REST baru** (murni tool AI). Update data-saja = `scp data/catalog_bom.json`
  ke `/opt/maspart/data/` (cache mtime auto-reload, tanpa rebuild). **Menggantikan** fitur
  `transmisi_bom.json` lama (file + tool `banding_transmisi`/`isi_transmisi` dihapus; logikanya
  pindah ke `catalog_bom.py` sbg satu sumber kebenaran).

### 3.5.5c Integrasi EPC Sinotruk (BOM per-VIN, reverse PN→unit, auto-token) — sejak 2026-06-29

Integrasi ke **EPC Sinotruk resmi** agar Asisten menjawab dari data pabrik PERSIS per-unit
(bukan asumsi katalog per-model). HANYA unit Sinotruk/HOWO/SITRAK/HOMAN.

- **Dua portal EPC:**
  - **Port 18080** (`services/epc.py`, tool `cek_kendaraan`) — endpoint config **publik tanpa
    token**. Hanya KONFIGURASI unit (model engine/gearbox/axle/Euro), bukan part.
  - **Port 7001** (`services/epc_bom.py`) — base `http://epc.sinotruk.com:7001/api/rest`,
    **butuh token** (`header token: Bearer <hex>`, disimpan `data/epc_token.txt`, dibaca segar
    tiap panggil). Dipakai `bom_dari_rangka` & `unit_dari_part`.
- **`bom_dari_rangka(rangka, kata_kunci?, kategori?)`** — `otherDoc/loadingList?vin=<frame>` =
  **Loading List / 工单BOM** (work-order BOM) = part yang BENAR-BENAR terpasang saat unit dirakit
  (per-VIN). Tiap PN disilang ke katalog lokal (nama Inggris + stok + harga via `part_index`).
  Hasil **selalu** memuat `kategori_breakdown` (jumlah part per kategori 01..12 PERSIS unit ini —
  via `catalog_bom.pn_category_map()`); arg `kategori` memfilter daftar per kategori. Pakai untuk
  "berapa/part apa di kabin unit ini" (angka exact-unit, bukan per-model `isi_kategori`).
- **`unit_dari_part(part_number)`** (REVERSE) — `epc_bom.reverse_part`: `home/match/part?t=global&
  k=<pn>` (validasi + nama Inggris) → `home/reverse/part?t=global&v=<pn>&k=<pn>` (daftar model
  kendaraan yang memakai PN). Lintas SEMUA model EPC (lebih lengkap dari `varian_unit` lokal).
- **⚠️ DUA DATABASE EPC BERBEDA** (sumber bingung "PN salah"): Loading List (7001) = part fisik
  per-VIN; **Parts Atlas terstruktur (18080 `/struct`)** = katalog standar model — *database
  berbeda*, sebagian PN work-BOM tak terindeks di Parts Atlas → search di sana bisa "暂无数据"
  walau PN itu benar terpasang. **Keputusan: pakai Loading List** (paling presisi per-VIN) + field
  `sumber` & prompt menjelaskan beda ini agar tak dikira PN salah.
- **🔑 AUTO-REFRESH TOKEN EPC (tanpa manusia, tanpa captcha)** — `epc_bom.refresh_token()` +
  `_get_auto()`: saat token mati (`_err` token_expired/no_token; dikenali via code `110025`
  "Not has role!" ATAU message "Login expired!" lewat `_TOKEN_ERR_RE`), sistem **login SIMS
  otomatis** (`shared/sims_fetcher` — captcha SimsCloud tak diverifikasi server) → **tukar token
  via SSO 云桥/yunqiao**: `GET :7001/api/integrate/getUserInfoByIcmcpToken?icmcpToken=<JWT SIMS>&
  sysCode=intl` → `data.token` = token EPC → tulis ke `data/epc_token.txt` → retry. Endpoint &
  `sysCode=intl` ini sama yang dipakai tombol "EPC" di SimsCloud (`simscloud.cnhtcerp.com:8082`).
  **Tak perlu refresh token manual lagi**; fallback manual (`scp` token) tetap ada bila SIMS down.
- **Anti-bocor tool-call**: bila model menulis pemanggilan tool sebagai TEKS (markup `<invoke>`/
  `<parameter>`) bukan via field `tool_calls` API, `ai_assistant` mem-parse & MENJALANKANNYA
  (`_parse_leaked_tool_calls`) + strip markup di semua jalur reply (`_strip_tool_markup`).
- **Belum diintegrasi:** report part aus/servis (`report/wearingParts`) → data kosong utk unit
  diuji; supersession (`partAlternateSale/replacementRelationship`). (Parts Atlas terstruktur &
  internal mesin Weichai → SUDAH, lihat §3.5.5d–e.)

### 3.5.5d Pohon KATEGORI EPC per-VIN + Dekomposisi ASSEMBLY — sejak 2026-07-01

Agar Asisten **paham SEMUA kategori/assembly sebuah unit BESERTA turunannya** (sub-assembly
berlapis) dan bisa **menguraikan 1 PN assembly jadi komponennya** (persis view "Spare Part List"
bergambar di EPC). Sumber: **EPC Parts Atlas 7001** (`part/tree/node` + `part/tree/item`), per-VIN,
memakai endpoint yang sama & stabil dgn `part_aus_dari_rangka`. Di `services/epc_bom.py` +
tool di `ai_assistant.py`.

- **`kategori_unit(rangka[, kategori])`** — tanpa `kategori`: daftar LENGKAP kategori tingkat-atas
  unit (mis. 117 assembly: gardan, transmisi, mesin, kabin, rem…). Dengan `kategori`: buka kategori
  itu → turunan (sub-kategori) + part langsung + stok/harga lokal. Bisa drill berlapis. Backing:
  `category_top` (node@rootId, cache), `category_open` (drill 1 level, cache), `resolve_category`
  (cocok nama EN/CN + sinonim; index tumbuh tiap buka).
- **`uraikan_assembly(rangka, assembly)`** — assembly disebut via **PN** (mis. `AZ000052000229`)
  atau **nama/istilah** ('v stay', 'thrust rod'). Walk SELURUH node pohon unit SEKALI (`_walk_all_nodes`,
  paralel+cache), temukan node assembly, ambil `_atlas_items` = komponennya (disilang stok/harga).
  Contoh: V-type thrust rod → 11 komponen (karet/球面销 `WG9725529213`, seal, dudukan…). Berlaku
  utk SEMUA assembly ber-turunan di pohon unit (~278/284 node pd unit uji). Aturan domain: pertanyaan
  komponen-DALAM-assembly (karet/bos/seal/pin dari X) DILARANG dijawab dgn PN assembly-nya.

### 3.5.5e Integrasi EPC WEICHAI — part INTERNAL MESIN per-VIN (OTOMATIS) — sejak 2026-07-01

Unit Sinotruk bermesin **Weichai** (mis. WP12/WP13): part internal mesin (blok, kruk as, piston,
ring, liner, cylinder head, klep, injector…) **TIDAK ada di EPC Sinotruk** (berhenti di level engine
assembly) — ada di **EPC Weichai terpisah** (`epc-cloud.weichai.com`). Service `services/epc_weichai.py`
menempuh SELURUH jembatan SSO + BOM **otomatis, cukup dari nomor rangka** (token Weichai auto-mint,
tanpa file):

1. `getParam(type=frameNo, code=<frame>)` [Sinotruk `:18080`, header token Sinotruk — sama & auto-refresh spt epc_bom] → `{param}` (parms terenkripsi)
2. `checkJumpParams(jumpParams=<parms>)` [`epc-cloud/Api/integration-api/…/externalepc`, Authorization `Weichai null` — ini proses login] → `{accessToken (token Weichai), serialCode (nomor mesin)}`
3. `getOrderNumber(serialNumber=<serial>)` [`…/business-api/…/etl-install-bom-header`] → `{dhhNumber (order), id (=root)}`
4. `findBomTree(dhhNumber)` → ~50 GROUP mesin · `findBomList(dhhNumber, dhhId)` → part tiap group (nama EN)

- **Tool AI `uraikan_mesin(rangka[, part])`** — tanpa `part`: daftar GROUP mesin; dengan `part`
  (piston/liner/kruk as/cylinder head/injector…): komponen + stok/harga lokal. Hanya unit bermesin
  Weichai (kalau bukan, tool balas apa adanya). Terbukti: unit `SJ346500` → WP12S400E201, 50 group,
  339 part; "piston" → Piston `1000076563`, Piston Ring Set `612600030054`, dst.
- Bridge & BOM di-cache per-frame. Auth Weichai `Authorization: Weichai <token>` + `tenant-id:1`.
  Aturan domain: internal mesin unit Weichai WAJIB `uraikan_mesin`, DILARANG `part_aus`/`bom_dari_rangka`.

### 3.5.5f Guard anti-halusinasi Part Number — sejak 2026-07-01

Model kadang MENGARANG PN saat tool `found=False` (PN berurutan rapi + stok/harga palsu). Guard di
`ai_assistant.chat()`: tiap PN di jawaban WAJIB berasal dari **hasil tool turn ini** ATAU **riwayat**
(pesan user + jawaban asisten yg sudah lolos guard) — token mirip-PN diambil via `_PN_TOKEN_RE`
(huruf+angka ≥7 char; harga/qty diabaikan). PN tak bersumber = karangan → model dipaksa koreksi (maks
2×); bila tetap: SEMUA PN karangan → jawaban diganti pesan jujur "tidak ditemukan"; sebagian → PN
palsu disamarkan. Guard **selalu jalan** (termasuk follow-up tanpa panggil tool). Nomor rangka/VIN
yang user sebut otomatis ikut "grounded". Melengkapi anti-bocor tool-call (§3.5.5c).

### 3.5.6 Cari by Foto

`services/image_search.py` — embedding **DINOv2-base** (torch CPU). Galeri dari **CSV lokal**
`data/part_image_index_rows.csv` ATAU **Supabase RPC `match_part_images`** (pgvector).
Hasil diagregasi per `part_number` + confidence boost. Foto part di-proxy via
`/api/parts/image-proxy` & sumber SIMS (`services/sims.py`).

### 3.5.7 E-commerce (orders/pembayaran/ongkir/chat)

- **Ongkir**: RajaOngkir/Komerce (`services/shipping.py`). **Pembayaran**: Payment API
  Komerce (`services/payments.py`), mode sandbox/prod. **Webhook**: `POST /api/payments/webhook`
  (rate-limited). **Reservasi stok**: `services/reservations.py`.
- **Chat**: chat per-pesanan, thread buyer↔gudang, thread cabang↔buyer (`services/chat.py`).
- Skema DB (orders, shipping, payment, recipient, reservations, tax) → `migrations/003..014`.

### 3.5.8 Peta endpoint API (per router, prefix `/api`)

| Router (prefix) | Endpoint utama |
|---|---|
| **auth** `/api/auth` | `POST /login`, `GET /me`, `GET /permissions` |
| **parts** `/api/parts` | `GET /search` (PN), `GET /search-name`, `POST /search-image`, `GET /compare`, `GET /photos`, `GET /image-proxy`, `GET /batch-template`, `POST /batch-catalog`, `GET/POST /index/status·refresh` |
| **harga** `/api/harga` | `GET /list·/list/export·/rate·/cari`, `POST /batch·/batch/export·/refresh` |
| **opname** `/api/opname` | `GET /draft·/history`, `POST /draft/from-upload·/finalize`, `PUT/DELETE /draft`, `DELETE /history/{id}` |
| **populasi** `/api/populasi` | `GET ""·/export`, `POST /refresh` |
| **orders** `/api` | `POST /orders`, `GET /orders·/orders/{code}`, `POST /orders/{code}/confirm·cancel·proof`, `GET /shipping/rates`, `POST /shipping/weight`, `GET /payments/methods`, `POST /payments/webhook`, `GET/PUT /admin/orders...` |
| **buyer** `/api/buyer` | `GET /locations·/location`, `POST /location` |
| **branch** `/api/branch` | `GET /orders·/orders/count·/orders/{code}·/sales`, `PUT /orders/{code}/status` |
| **chat** `/api` | `GET/POST /orders/{code}/chat`, `/chat/buyer/threads`, `/chat/gudang/{key}`, `/chat/branch/...` |
| **geo** `/api/geo` | `GET /reverse·/search` |
| **ai** `/api/ai` | `GET /status`, `POST /chat` |
| **repairkit** `/api/repairkit` | `GET /transmisi`, `GET /transmisi/export` |
| **admin** `/api/admin` | users, perms, gudang, `upload/{kind}`, `upload-catalog`, monitoring, sales, photos, `index*` (reload galeri/bulk) |
| **meta** | `GET /health` |

### 3.5.9 Peta halaman frontend (Next.js App Router, `frontend/src/app/`)

`login` · `/` (search PN) · `search` · `search-image` · `compare` · `part/[pn]` · `harga` ·
`batch` · `opname` · `populasi` · `download` · `asisten` (AI) · `keranjang` · `pesanan` +
`pesanan/[code]` + invoice · `pilih-lokasi` · `chat` · `cabang/*` (pesanan/penjualan/chat) ·
`admin/*` (menu, users, gudang, upload, monitoring, index, foto, orders, penjualan).

### 3.5.10 Konvensi & "jebakan" yang WAJIB diketahui AI

- **Decoupled dari Streamlit**: tidak ada `st.*` di `backend/`. Logika lama di-reuse via
  `backend/shared/` (part_compare, sims_fetcher, sims_price_fetcher).
- **`DATA_DIR` default `../data`** (relatif ke `backend/`). Di produksi = bind-mount
  `/opt/maspart/data` → `/app/data:rw`.
- **Sinonim dibaca segar tiap query** → edit `sinonim.json` langsung aktif **tanpa restart**.
  Sebaliknya **index katalog di-cache** → setelah ganti file Excel, refresh via
  `POST /api/parts/index/refresh` atau panel admin (Image Index → Reload).
- **Deploy data-saja = `scp` ke `/opt/maspart/data/...`** (TANPA rebuild/redeploy, lihat
  §5.4). **Deploy kode** = `deploy/coolify/push.sh` + klik **Redeploy** di Coolify.
- **Rahasia** (`backend/.env`, `.streamlit/secrets.toml`) **jangan ter-commit**; di produksi
  dikelola sebagai **Coolify Environment Variables**.
- **Env vars backend** (lihat `core/config.py`): `APP_ENV`, `SUPABASE_URL/KEY/SERVICE_KEY/
  TABLE/DATA_BUCKET`, `JWT_SECRET/ALGORITHM/EXPIRE_MINUTES`, `DATA_DIR`, `IMAGE_INDEX_CSV`,
  `CORS_ORIGINS`, `RAJAONGKIR_API_KEY`, `PAYMENT_API_KEY/SANDBOX/CALLBACK_SECRET/BASE_URL`,
  `PUBLIC_BASE_URL`, `DEEPSEEK_API_KEY/BASE_URL/MODEL`.
- **Selftest tanpa server/network**: `cd backend && python selftest.py <PN>`.

### 3.5.11 Monitoring User (online/offline) — sejak 2026-07-01

Panel admin **Monitoring User** (`/admin/monitoring`, menu di sidebar admin) — status **online/offline**
+ aktivitas terakhir tiap user. Pelacakan **in-memory** (tanpa migrasi DB, tanpa tulis DB per request):
`services/presence.py` — `touch(username)` dipanggil di `deps.get_current_user` tiap request
terautentikasi; `mark_login` saat login. **Online = aktif ≤ 5 menit** (`ONLINE_WINDOW_SEC`). Endpoint
`GET /api/admin/monitoring` menggabung `list_users_full()` + presence → online_count/urut online dulu +
aktivitas terbaru. Frontend `admin/monitoring/page.tsx` (auto-refresh 15 dtk, filter online). Reset saat
container restart (wajar utk "siapa online sekarang"; setup 1 container backend). Menu didaftarkan di
`AppShell` `NAV_ADMIN`.

---

## 4. Menjalankan Lokal (Development)

### Backend
```bash
cd backend
python -m venv venv
# Windows: venv\Scripts\activate   |  Linux/Mac: source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # lalu isi SUPABASE_URL, SUPABASE_KEY, JWT_SECRET, dll
uvicorn app.main:app --reload
# API:    http://127.0.0.1:8000
# Swagger: http://127.0.0.1:8000/docs
# Health:  http://127.0.0.1:8000/health
```

### Frontend
```bash
cd frontend
cp .env.local.example .env.local     # set NEXT_PUBLIC_API_BASE ke URL backend
npm install
npm run dev                          # http://localhost:3000
```

### Test & Eval (sejak 2026-07-02)

```bash
cd backend
pip install -r requirements-dev.txt        # pytest

# 1) UNIT TEST logika murni — cepat (<2 dtk), TANPA network/API. Jalankan tiap ubah kode.
python -m pytest tests/ -q
#    Cakupan: guard anti-halusinasi PN (_extract_pns/_sanitize_ungrounded + loop chat()
#    dgn DeepSeek di-mock), anti-bocor tool-call, ekspansi sinonim, catalog_bom
#    (resolve/verdict/compare).

# 2) EVAL REGRESI Asisten AI — golden questions lewat chat() NYATA (DeepSeek + tool asli).
#    Jalankan SEBELUM deploy perubahan prompt/tool. Ada biaya API kecil per run.
python evals/run_evals.py                  # semua kasus 'lokal' (default; ~22 kasus)
python evals/run_evals.py --net            # + kasus EPC/Weichai (butuh jaringan EPC)
python evals/run_evals.py --only guard     # subset via substring id
python evals/run_evals.py --list           # daftar kasus tanpa API
#    Kasus di evals/golden.json — cek tool yang wajib terpakai, substring jawaban,
#    PN wajib/haram, dan 'no_new_pn' (uji guard). ATURAN EMAS menambah kasus:
#    verifikasi anchor (PN/unit/istilah) benar-benar ada di data. Hasil detail run
#    terakhir: evals/last_run.json (di-gitignore).
```

### Env penting (backend/.env)
`APP_ENV` (dev/prod), `SUPABASE_URL/KEY/SERVICE_KEY`, `JWT_SECRET` (WAJIB 32+ char
acak di prod), `JWT_EXPIRE_MINUTES` (720 = 12 jam), `DATA_DIR` (default `../data`),
`CORS_ORIGINS`, `RAJAONGKIR_API_KEY`, `PAYMENT_API_KEY/SANDBOX/CALLBACK_SECRET`,
`PUBLIC_BASE_URL`, `DEEPSEEK_API_KEY/MODEL`.

> Nilai rahasia asli (Supabase dll) ada di `backend/.env` (TIDAK di-commit) dan
> mirip blok `[supabase]` di `.streamlit/secrets.toml`.

---

## 5. Server & Deploy

### 5.1 Akses SSH

| Item | Nilai |
|------|-------|
| Host | **maspart.tech** |
| User | **root** |
| Auth | **SSH key** (sudah ter-setup di mesin lokal ini — login **tanpa password**) |
| Port | 22 (default) |

```bash
ssh root@maspart.tech
# contoh command non-interaktif:
ssh root@maspart.tech "docker ps && df -h /"
```

> Domain: **maspart.tech** (sudah resolve & melayani HTTP/HTTPS via Traefik).

### 5.2 Kondisi server saat ini (terverifikasi 2026-06-25)

> **PENTING (update 2026-06-25):** MASPART sudah **DIMIGRASI ke Coolify**. Trafik
> `maspart.tech` sekarang dilayani **container Docker yang dikelola Coolify**, BUKAN
> lagi systemd+nginx. Lihat §5.4. Bagian di bawah ini (systemd) kini jadi **fallback
> rollback** yang di-*disable* (tidak auto-start), bukan jalur aktif.

#### 5.2a Setup LAMA (systemd+nginx) — sekarang FALLBACK, di-disable

- OS: **Ubuntu** (Linux), disk `/dev/sda1` 48G — terpakai ~22% (sisa 38G), sehat.
- Dulu **MASPART berjalan langsung di host via systemd + Nginx** (sekarang nonaktif):
  - **Backend** FastAPI/uvicorn → `127.0.0.1:8001`, service **`maspart-backend`** = active.
    `https://maspart.tech/health` → `{"status":"ok","supabase_configured":true,"data_dir":"/opt/maspart/data"}`
  - **Frontend** Next.js → `127.0.0.1:3000`, service **`maspart-frontend`** = active.
  - **Routing aktual (terverifikasi):**
  ```
  Internet → Traefik :443/:80 (container coolify-proxy, TLS Let's Encrypt via acme.json)
     → file dynamic /data/coolify/proxy/dynamic/maspart.yaml :
           Host(maspart.tech|www) → service maspart-svc → http://172.16.1.1:8090
     → nginx :8090 (systemd, di host)  → /api, /health → 127.0.0.1:8001 (backend)
                                        → /             → 127.0.0.1:3000 (frontend)
  ```
  Jadi Traefik-nya Coolify SUDAH jadi pintu depan + TLS, tapi MASPART-nya sendiri
  BUKAN aplikasi Coolify — dia systemd+nginx, disambung lewat file Traefik manual.
- **Kode aplikasi ada di `/opt/maspart`** (backend, frontend, data, deploy, migrations).
- Arsip deploy: `/root/maspart-deploy.tar.gz` (~137 MB).
- **Coolify v4.1.2 juga terpasang** di server (stack container `coolify-*`: Traefik v3.6,
  Postgres 15, Redis 7, dll, data di `/data/coolify`) — **TAPI MASPART TIDAK di-deploy
  lewat Coolify.** Coolify berdiri sendiri / belum dipakai untuk app ini. Container
  `coolify-*` yang muncul di `docker ps` adalah milik Coolify, bukan MASPART.

> Catatan: karena MASPART jalan sebagai proses systemd (bukan container), dia **tidak
> muncul di `docker ps`**. Cek statusnya dengan `systemctl status maspart-backend
> maspart-frontend`, bukan lewat Docker.

### 5.3 Metode deploy

**A. AKTIF — Coolify (Docker Compose)** ✅ ini yang dipakai server **sejak 2026-06-25**.
Detail lengkap + cara deploy ada di **§5.4** di bawah. Singkatnya: 2 container
(backend+frontend) dikelola Coolify, di belakang Traefik (TLS Let's Encrypt).

**B. FALLBACK — Manual VPS (Nginx + systemd)** — dipakai sebelum migrasi, sekarang
*disabled* (jadi jalur rollback). Terdokumentasi di **`deploy/DEPLOY.md`**. Arsitektur:
Nginx (80/443) → FastAPI uvicorn (127.0.0.1:8001) + Next.js (127.0.0.1:3000) via systemd.
File pendukung di folder `deploy/`:
- `setup-vps.sh` — pasang python/node/nginx + swap 2GB
- `maspart-backend.service`, `maspart-frontend.service` — unit systemd (masih ada, disabled)
- `nginx-maspart.conf` — config Nginx
- `redeploy.sh` — script redeploy (era systemd)
- `traefik-maspart.yaml` — config Traefik (varian alternatif, tidak dipakai)
- `DEPLOY.md` — panduan lengkap era systemd

**C. Railway** — `backend/railway.toml` ada (builder nixpacks), sebagai alternatif
hosting backend. `start: uvicorn app.main:app --host 0.0.0.0 --port $PORT`.

---

### 5.4 Setup AKTIF sekarang: Coolify (Docker Compose) — sejak 2026-06-25

MASPART kini jalan sebagai **Coolify Service** (tipe Docker Compose), 2 container:

```
Internet → Traefik :443/:80 (coolify-proxy, TLS Let's Encrypt)
   → label Traefik di container (docker provider):
       /api/*, /health  → container backend-<uuid>:8001   (priority 20)
       sisanya          → container frontend-<uuid>:3000  (priority 10)
   → http→https redirect via middleware
```

- **Coolify Service uuid:** `jmmamc7kvqr6nlev97r79j5q` (project "My first project" → env `production`).
- **Image** di-*build di server* (bukan dari registry): `maspart-backend:latest`,
  `maspart-frontend:latest`. Dockerfile ada di `deploy/coolify/`.
- **Kredensial / env:** dikelola sebagai **Coolify Environment Variables** (20 var:
  SUPABASE, JWT, PAYMENT, DEEPSEEK, dll) — edit di dashboard → service maspart →
  Environment Variables. TIDAK lagi mount file `.env` host. (File `/opt/maspart/backend/.env`
  masih ada sebagai backup & sumber rollback systemd.)
- **Bind-mount (WAJIB ada, JANGAN hapus):**
  - `/opt/maspart/data` → `/app/data:rw` (katalog + galeri foto 344MB). **read-write**
    karena admin bisa upload file katalog via UI (menu Upload Data → "Upload Katalog")
    yang menulis langsung ke folder ini.
- Container `restart: unless-stopped` → auto-start saat reboot.
- File `deploy/coolify/`: `backend.Dockerfile`, `frontend.Dockerfile`,
  `docker-compose.yaml`, `cutover.sh`, `rollback.sh`, plus `*.dockerignore`.

> ⚠️ **`/opt/maspart` TIDAK boleh dihapus** — ia jadi sumber bind-mount data & .env,
> sekaligus build-context image. Hapus = app rusak.

#### 5.4a CARA DEPLOY (update kode) — MANUAL, dipicu sendiri

> **Prinsip:** auto-deploy SENGAJA tidak diaktifkan (build di VPS 3.8GB berisiko +
> ingin kontrol kapan deploy). Deploy selalu manual. **Tidak perlu API token.**
> Catatan: `/opt/maspart` di server BUKAN git repo → update kode = KIRIM FILE dari
> komputer lokal (scp), bukan `git pull`.

**Script bantu (di `deploy/coolify/`):**
| Script | Jalan di | Fungsi |
|---|---|---|
| `push.sh [backend\|frontend]` | LOKAL (Git Bash) | kirim kode ke server + rebuild image |
| `build.sh [backend\|frontend]` | SERVER (root) | rebuild image saja |
| `rollback.sh` | SERVER (root) | balik ke systemd lama |
| `cutover.sh` | SERVER (root) | (arsip) systemd → container, dipakai saat migrasi awal |

**── Cara GAMPANG (rekomendasi): 1 perintah + 1 klik ──**
```bash
# DI KOMPUTER LOKAL (Git Bash), dari root repo:
cd "/d/Project Python/maspart-main (PROJECT V5)/maspart-main"
bash deploy/coolify/push.sh            # backend + frontend
#   atau: bash deploy/coolify/push.sh backend     (salah satu saja)
#   atau: bash deploy/coolify/push.sh frontend
```
Lalu **DI BROWSER:** Coolify → **Projects → My first project → production →
service "maspart" → tombol REDEPLOY**. Selesai.

**── Cara MANUAL (setara, per-langkah) ──**
```bash
# 1) LOKAL — kirim folder yang berubah:
scp -r "<repo>/backend/app"  root@maspart.tech:/opt/maspart/backend/    # jika backend berubah
scp -r "<repo>/frontend/src" root@maspart.tech:/opt/maspart/frontend/   # jika frontend berubah
# 2) SERVER — rebuild image:
ssh root@maspart.tech "bash /opt/maspart/deploy/coolify/build.sh"       # atau build.sh backend|frontend
# 3) BROWSER — Coolify → service "maspart" → REDEPLOY
```

**── Update DATA saja (galeri/katalog, TANPA ubah kode) ──**
Tidak perlu rebuild/redeploy. Cukup kirim file ke folder data:
```bash
scp -r "<repo>/data/<merek>/<file>.xlsx" root@maspart.tech:/opt/maspart/data/<merek>/
```
Kalau perlu refresh index di memori: login admin → menu **Image Index** → **Reload**.

**── Cek status / logs ──**
```bash
ssh root@maspart.tech 'docker ps --filter name=jmmamc7kvqr6nlev97r79j5q'
ssh root@maspart.tech 'docker logs --tail 50 backend-jmmamc7kvqr6nlev97r79j5q'
# atau lewat dashboard Coolify → service maspart → Logs
```

#### 5.4b ROLLBACK ke systemd lama (kalau versi Coolify bermasalah)
```bash
ssh root@maspart.tech 'bash /opt/maspart/deploy/coolify/rollback.sh'
# inti: stop container → kembalikan /root/maspart.yaml.bak ke dynamic/ →
#       systemctl start maspart-backend maspart-frontend nginx
```
Backup route lama: `/root/maspart.yaml.bak`. Unit systemd masih ada (hanya
*disabled*). Untuk balik PERMANEN ke systemd: `systemctl enable --now maspart-backend
maspart-frontend nginx` + biarkan rollback.sh mengembalikan routing.

#### 5.4c Catatan operasional
- **JANGAN** menjalankan systemd lama + container Coolify bersamaan (dua-duanya muat
  torch → risiko OOM di RAM 3.8GB). rollback.sh sudah otomatis mematikan salah satu.
- Setelah `build.sh`, container belum berubah sampai kamu klik **Redeploy** — itu yang
  me-recreate container dengan image baru.
- API token Coolify TIDAK diperlukan untuk operasi sehari-hari (semua via SSH + tombol
  dashboard). Token hanya dipakai sekali saat migrasi & sudah sebaiknya di-revoke.

#### 5.4d Deploy cepat 1 file kode (hot-deploy) + fakta penting build cache

> Kasus nyata (2026-06-25): mengubah **1 file backend** (`app/services/ai_assistant.py`)
> dan ingin langsung live tanpa repot. Ada 2 fakta yang membuat ini gampang & aman:

**FAKTA 1 — `build.sh` itu RINGAN selama `requirements.txt` tidak berubah.**
Dockerfile (`deploy/coolify/backend.Dockerfile`) memasang torch di layer terpisah
SEBELUM `COPY . ./`. Jadi saat hanya kode yang berubah, Docker me-*reuse* layer torch
(CACHED, tidak unduh ~2GB lagi) dan cuma menjalankan ulang `COPY . ./` → **build
selesai dalam hitungan detik**, RAM aman. Bukti di log: `pip install torch ... CACHED`,
`COPY . ./ DONE 0.1s`. ⇒ Peringatan "build berat di VPS 3.8GB" HANYA berlaku kalau
`requirements.txt` berubah atau cache di-prune.

**FAKTA 2 — beda IMAGE vs CONTAINER yang jalan.**
- **Redeploy** (Coolify) = *recreate container dari IMAGE* `maspart-backend:latest`.
  Ia TIDAK menyalin file-per-file; ia buang container lama, bikin baru dari image.
- Jadi kalau kamu hanya mengubah file di dalam container yang jalan (hot-swap) TANPA
  rebuild image, **Redeploy berikutnya akan menimpanya** (balik ke isi image lama).

**Resep deploy 1 file kode (live cepat + durable):**
```bash
# DI LOKAL (Git Bash):
C=backend-jmmamc7kvqr6nlev97r79j5q
F=app/services/ai_assistant.py            # path relatif di dalam backend/
# 1) kirim source ke server (jadi sumber build & masa depan)
scp "backend/$F" root@maspart.tech:/opt/maspart/backend/$F
# 2) (LIVE SEKARANG, opsional) hot-swap ke container yg jalan + restart (~10-20s down)
ssh root@maspart.tech "docker cp /opt/maspart/backend/$F \$C:/app/$F && docker restart $C"
# 3) (DURABLE) bakar ke image — RINGAN karena torch ke-cache
ssh root@maspart.tech "bash /opt/maspart/deploy/coolify/build.sh backend"
```
- Langkah 2 = biar langsung live tanpa nunggu apa-apa (hot-swap; hilang bila Redeploy).
- Langkah 3 = biar **permanen / anti-hilang saat Redeploy** (image ikut update).
- Lakukan **langkah 1 + 3 minimal**; langkah 2 cuma untuk "live detik ini". Kalau tak
  buru-buru: cukup 1 + 3, lalu klik **Redeploy** sekali (aman, image sudah benar).

**Verifikasi cepat:**
```bash
# image baru sudah berisi kode baru?
ssh root@maspart.tech 'docker run --rm maspart-backend:latest python3 -c "import sys;sys.path.insert(0,\"/app\");from app.services import ai_assistant as a;print(hasattr(a,\"_relevansi\"))"'
# container yg jalan?
ssh root@maspart.tech 'docker exec backend-jmmamc7kvqr6nlev97r79j5q python3 -c "import sys;sys.path.insert(0,\"/app\");from app.services import ai_assistant as a;print(hasattr(a,\"_relevansi\"))"'
# health (dari DALAM container; port 8001 TIDAK terekspos ke host pada setup Coolify)
ssh root@maspart.tech 'docker exec backend-jmmamc7kvqr6nlev97r79j5q python3 -c "import urllib.request;print(urllib.request.urlopen(\"http://127.0.0.1:8001/health\",timeout=10).read().decode())"'
```

> Ringkas: **Redeploy SELALU aman ASALKAN image sudah di-`build.sh` lebih dulu.** Setelah
> build, isi image = kode terbaru, jadi recreate container tidak menghilangkan apa pun.

## 6. Database & Migrations

- Database utama: **Supabase** (Postgres remote). Auth user, orders, harga, dll.
- File SQL migrasi ada di `migrations/` (003 s/d 014): batch harga, orders +
  shipping/payment/recipient, buyer gudang, order/gudang chats, stock reservations,
  order tracking, tax + atomic reserve.
- Coolify juga menjalankan Postgres-nya sendiri (`coolify-db`) — itu DB internal
  Coolify, **bukan** DB aplikasi MASPART (yang pakai Supabase).

---

## 7. Git

- Remote: **https://github.com/morwick/maspart-v5.git** (`origin`)
- Branch utama: `main`

---

## 8. Quick Reference / Cheatsheet

```bash
# --- SSH ke server ---
ssh root@maspart.tech

# --- Cek status di server ---
ssh root@maspart.tech "docker ps"                 # container yang jalan
ssh root@maspart.tech "df -h / && uptime"         # disk & uptime
ssh root@maspart.tech "ls -la /opt/maspart"       # kode aplikasi
# Dashboard Coolify: http://maspart.tech:8000

# --- Lokal: backend ---
cd backend && uvicorn app.main:app --reload       # :8000  (/docs untuk Swagger)
python selftest.py WG16                            # test search tanpa server

# --- Lokal: frontend ---
cd frontend && npm run dev                         # :3000

# --- DEPLOY update kode (lihat detail §5.4a) ---
bash deploy/coolify/push.sh                        # lokal: kirim kode + rebuild image
#   lalu di browser: Coolify -> service "maspart" -> REDEPLOY
ssh root@maspart.tech 'bash /opt/maspart/deploy/coolify/rollback.sh'   # rollback ke systemd
```

---

## 9. Hal yang Perlu Diperhatikan / TODO

- [x] **App MASPART running di server** — terverifikasi 2026-06-25: backend `/health`
      OK, frontend Next.js 200, `maspart-backend`/`maspart-frontend` (systemd) active,
      di-serve Nginx. (Bukan via Docker/Coolify — jangan cari di `docker ps`.)
- [x] **Migrasi ke Coolify** — SELESAI 2026-06-25 (lihat §5.4). maspart.tech kini
      dilayani container Coolify; systemd lama jadi fallback (disabled).
- [x] **Fitur Repair Kit Transmisi** — SELESAI 2026-06-27 (§3.5.5a): data 12 model,
      tool AI `repair_kit_transmisi`, endpoint `/api/repairkit/*`, dan tombol **Download Excel
      di dalam jawaban Asisten AI** (via field `repairkit_models`). Backend live + kedua image
      (backend/frontend) sudah di-`build.sh`. **Perlu klik Redeploy** di Coolify agar frontend live.
- [x] **Fitur Catalog BOM (banding part per kategori & per assy)** — SELESAI 2026-06-29
      (§3.5.5b): data `data/catalog_bom.json` (40 unit×12 kategori, 123 assy), service
      `catalog_bom.py`, 4 tool AI (`banding_assy`/`isi_assy`/`banding_kategori`/`isi_kategori`).
      Backend live (hot-swap) + image backend sudah di-`build.sh` & terverifikasi. Menggantikan
      `transmisi_bom.json` lama. Tanpa endpoint/ frontend baru → Redeploy tidak wajib.
- [x] **Integrasi EPC Sinotruk** — SELESAI 2026-06-29 (§3.5.5c): `cek_kendaraan` (config
      18080), `bom_dari_rangka` (Loading List per-VIN + `kategori_breakdown` exact-unit),
      `unit_dari_part` (reverse PN→model), **auto-refresh token EPC via SSO SimsCloud**
      (云桥, sysCode=intl — tanpa captcha/manual), deteksi "Login expired!", anti-bocor
      tool-call. Backend live (hot-swap) + image di-`build.sh`. Tanpa frontend baru → Redeploy
      tidak wajib. Catatan: Loading List ≠ Parts Atlas terstruktur (database EPC berbeda).
- [ ] **Revoke API token Coolify** yang dipakai untuk migrasi (di dashboard →
      Keys & Tokens) setelah yakin stabil — token = kontrol penuh.
- [ ] Setelah Coolify stabil beberapa hari, pertimbangkan beresihkan fallback systemd
      (atau biarkan saja — sudah disabled, tidak mengganggu).
- [ ] Pantau RAM: VPS hanya 3.8GB; container backend memuat torch+DINOv2. Hindari
      menjalankan systemd lama + container bersamaan (double torch = risiko OOM).
- [ ] Pastikan `JWT_SECRET` di server kuat (32+ char acak) & `APP_ENV=prod`.
- [ ] Rahasia (`backend/.env`, `.streamlit/secrets.toml`) jangan sampai ter-commit.
```
