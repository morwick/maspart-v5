# MASPART — Dokumentasi Project & Server

> Dokumen onboarding untuk developer / AI agent. Tujuannya: siapa pun (atau AI
> mana pun) yang membuka repo ini bisa langsung paham **apa project-nya, stack-nya,
> cara deploy, dan cara akses server**.
>
> Terakhir diverifikasi: **2026-07-03** (oleh inspeksi langsung repo lokal + SSH ke server).
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
> Update **2026-07-02**: (a) **Suite pengujian** — unit test `backend/tests/` (76 test, tanpa
> network) + **eval regresi AI** `backend/evals/` (golden questions via `chat()` nyata) — §4
> "Test & Eval". (b) **Repair kit transmisi PER-VIN** — arg `rangka`: gearbox di-resolve PERSIS
> dari EPC pabrik — §3.5.5a. (c) Fix guard: jawaban yang berhenti di [PIKIR] di-RETRY (bukan
> pesan gagal); kode seri unit (NX400HP dll) tak lagi disamarkan — §3.5.5f. (d) Umpan balik
> 👍/👎 + kartu unduh Excel banding rangka — §3.5.5g. (e) Berat/dimensi resmi SIMS + auto-berat
> ongkir — §3.5.4. (f) Seluruh pekerjaan dikomit per-fitur & di-push (branch `snapshot-clean`).
> Update **2026-07-03**: supersession part sasis Sinotruk diverifikasi **BUNTU** (endpoint ada,
> data role-gated internal — §3.5.5c; utk part MESIN Weichai SUDAH ada tool `pengganti_part`);
> `repair_kit_mesin` kasus tanpa 维修包 kini menyarankan `uraikan_mesin`; tabel tool §3.5.5
> dilengkapi 8 tool yang belum tercatat.
> Update **2026-07-03/04**: (a) **Aksesori TERPASANG di mesin** (air compressor, alternator,
> starter, turbo) dirutekan ke EPC Weichai — §3.5.5e. (b) **Export Excel DINAMIS** — tool
> `buat_excel`: "buatkan excelnya" atas data apa pun → kartu unduh gaya Claude — §3.5.5h.
> (c) **KATALOG BERGAMBAR per-VIN** — tool `katalog_kategori`: per kategori ATAU LENGKAP
> (semua kategori); Excel 1 sheet berisi daftar-isi ber-hyperlink + gambar EXPLODED VIEW
> resmi EPC (SVG d2s → PNG via resvg) + tabel No. Balon→PN + stok/harga + pelengkap Loading
> List (cakupan part terpasang 100%); tanpa kategori → asisten menawarkan pilihan — §3.5.5h.
> (d) Dockerfile backend +`fonts-liberation` (angka balon = teks Arial di SVG; tanpa font
> hilang senyap) & requirements +`resvg-py`. (e) Redeploy Coolify ternyata BISA dari CLI
> (`docker compose up -d --force-recreate` di folder service) — §5.4.
> Update **2026-07-04 (armada)**: **Banding part antar unit SATU CUSTOMER** — tool
> `banding_part_armada`: "cek kampas kopling unit PT X apakah sama semua" → populasi
> (kolom CUSTOMER + NOMOR RANGKA) → konfigurasi pabrik EPC per-VIN → kelompokkan unit
> berkonfigurasi identik → cek part via EPC Atlas pada unit WAKIL tiap kelompok (paralel)
> → verdict SAMA/BEDA dihitung SISTEM. Akses: admin/SEE_ALL (ikut populasi) — §3.5.5.
> Update **2026-07-04 (audit keamanan)**: audit menyeluruh (5 review paralel) + perbaikan
> Kritis/Tinggi/Menengah. Backend: rate-limit ambil IP dari X-Forwarded-For sisi KANAN
> (anti-spoof, `trusted_proxies`); JWT_SECRET default/kosong = FATAL di env apa pun +
> APP_ENV tak dikenal → fail-closed prod; DB down tak menaikkan privilege dari token
> (turun ke 'user'); `/api/ai/chat` + export di-rate-limit; guard formula/CSV injection di
> export Excel; password plaintext legacy di-upgrade ke bcrypt saat login; security headers
> (HSTS/nosniff/X-Frame-Options) + cap upload batch. Frontend: SRI pin Leaflet CDN; token
> ikut "Ingat saya" (sessionStorage bila tak dicentang); CSP + security headers di
> `next.config.ts`. ⚠️ **BELUM diperbaiki (butuh Anda):** rotate kunci Supabase + scrub
> `.streamlit/secrets.toml` dari commit `1be5c53` SEBELUM push `main` (service_key bocor);
> TLS `verify=False` ke EPC/SIMS (sertifikat upstream invalid — perlu pin CA). Rincian §3.5.12.
> Update **2026-07-04 (UI + fitur)**: (a) **Redesign UI "Command Center"** — sidebar gelap +
> rail lipat, header + command palette (⌘K) + toggle tema, **dark mode** penuh (token +
> remap utilitas Tailwind), **Dashboard** admin. Halaman Populasi/Harga/Compare/Batch/Cari-
> by-Foto diport ke design system. (b) 3 fitur AI: **foto part** di jawaban asisten
> (`part_pns`+PartThumbs), **log pencarian nihil** (`/admin/search-misses`), **katalog PDF**
> (asisten tanya Excel/PDF, `katalog_pdf` via reportlab). (c) Fix `uraikan_assembly` menembus
> pointer figure (sensor di dalam retarder kini muncul) + rute komponen-di-dalam-assembly.
> Update **2026-07-05 (Accurate ERP)**: integrasi **ERP Accurate Online** (stok live). **Auto-login
> SSO penuh** dari username+password (pre-login→auth.do Spring Security→SAML idp/sso+ACS→open-database),
> auto-refresh sesi saat kadaluarsa + **cooldown anti-throttle**; stok per-PN instan (`stock_for_live`)
> + **rincian per GUDANG/CABANG** (`view-itemstock-bywarehouse.do`, gaya `stok.xlsx`). Tool AI
> **`stok_accurate`**; endpoint `GET /api/parts/accurate-stock`. **Accurate = SUMBER STOK UTAMA** di
> halaman detail part & tool `detail_part`; **`stok.xlsx` = FALLBACK** bila fetch Accurate gagal (Excel
> di-export dari Accurate, data sama). Kredensial env `ACCURATE_*` (juga di Coolify). Endpoint ditemukan
> AMAN dari JS statis CDN (bukan meraba API). Lihat **§3.5.5i**.
> Update **2026-07-06**: fitur **banding BANYAK unit sekaligus** — tool `banding_rangka_massal`:
> bandingkan SATU kategori (kabin/rem/dll) ATAU SEMUA kategori antar ≥2 unit, via **daftar VIN**
> (semua user) atau **nama customer** (armada populasi, admin/`SEE_ALL`). Loading List NYATA tiap
> unit → kelompokkan unit ber-set-PN identik → verdict SERAGAM/BEDA dihitung sistem + kartu unduh
> Excel matriks. 14 unit test baru (total 130 lolos). Lihat **§3.5.5j**. Juga: `migrations/015_ai_feedback.sql`
> dibuat; server↔lokal diverifikasi identik (2 file nyasar `app/admin.py`+`app/catalog_bom.py` di server).
> Update **2026-07-06 (pencarian lebih pintar)**: cari_part **BROADEN dalam-unit** (kata inti dicari
> sendiri saat scope unit → part bernama ringkas spt `HANDLE` untuk 'handle pintu' ketemu) + **konteks
> cluster `grup_induk`+`grup_isi`** (assembly induk + TETANGGA se-grup dari penomoran, mis. `HANDLE`
> segrup `LOCK(L.H.)`,`LOCK CATCH` = handle pintu; segrup `DAMPER`,`BAR` = tuas) agar asisten memilah
> part ambigu spt teknisi baca katalog. Prompt: Rule 2/3b (scope unit, kata inti) + **Rule 3c PRINSIP
> PENALARAN KONTEKS UMUM** berlaku SEMUA tool/data — EPC Loading List (field `kategori`+breakdown), EPC
> Atlas (hierarki kategori→assembly→komponen+posisi), Weichai (GROUP mesin), populasi: WAJIB kelompokkan
> & simpulkan fungsi dari keluarga/kategori/posisi, bukan nama baris tunggal (EPC sudah bawa konteks ini;
> yg diperbaiki = model konsisten MENALAR-nya). LIVE & deployed. 6 test baru (total 136). Lihat §3.5.3 butir 4–5.

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
- **Stok / Opname** — multi-gudang, scope per cabang; plus **menu Stok** (daftar stok live
  seluruh barang Accurate, §3.5.5l)
- **Populasi** — data populasi unit
- **Orders / Pesanan / Keranjang** — alur jual-beli + pembayaran + ongkir
- **Chat** — chat order & gudang
- **Asisten AI** — chatbot (DeepSeek, OpenAI-compatible) dengan ~30 tool (katalog, EPC
  per-VIN, banding, repair kit, fault code, populasi) + guard anti-halusinasi
- **Umpan balik AI** — 👍/👎 per jawaban asisten (tabel Supabase `ai_feedback`,
  review admin di `/admin/feedback`) — §3.5.5g
- **Monitoring User** — online/offline in-memory di `/admin/monitoring` — §3.5.11
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
│   │                       #   repairkit, catalog_bom, epc, epc_bom, epc_weichai,
│   │                       #   ai_feedback, ai_export, presence, sims, dll)
│   ├── shared/         # part_compare, sims_fetcher, sims_price_fetcher (di-reuse dari versi Streamlit)
│   ├── tools/          # append_gallery.py, build_catalog_bom.py, reconcile_catalog_epc.py
│   ├── tests/          # unit test pytest (guard, sinonim, leak, catalog_bom, repairkit-rangka)
│   ├── evals/          # eval regresi Asisten AI (golden.json + run_evals.py) — lihat §4
│   ├── requirements.txt
│   ├── requirements-dev.txt  # pytest (dev only)
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
│   ├── epc_dict/cn_en.json             # kamus CN→EN swadaya dari EPC (§3.5.5c)
│   ├── epc_token.txt                   # token sesi EPC (di-gitignore; auto-refresh via SSO)
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
4. **BROADEN dalam-unit** (`_t_cari_part`, sejak 2026-07-06) — bila `unit=` di-set, pencarian
   dibuat **forgiving**: tiap KATA INTI (query+sinonim) dicari sendiri-sendiri lalu digabung.
   Menolong part yang di katalog bernama **ringkas** (mis. `HANDLE` saat user tanya `handle
   pintu`/`door handle` — part tak pernah bernama frasa penuh). Search **global tetap presisi
   per-frasa** (tak bocor antar-unit). Kata model/arah/struktural dibuang (`_BROADEN_STOP`).
5. **KONTEKS GRUP INDUK** (`part_index.assembly_parent`, sejak 2026-07-06) — hasil cari_part
   (item yang ditampilkan) diberi field **`grup_induk`** = nama ASSEMBLY tempat part berada,
   dihitung dari penomoran Shantui/Komatsu (nolkan 3 digit terakhir: `146-56-15200` →
   induk `146-56-15000` = `LOCK(L.H.)`). Memungkinkan asisten **memilah part bernama ambigu**
   — mis. `HANDLE` ber-grup `LOCK(L.H.)` = **handle pintu**, vs `HANDLE` tanpa induk = tuas.
   Prompt Rule 2/3b mengajari model men-scope `unit=`, pakai kata inti, & baca `grup_induk`.

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
- **Berat & dimensi resmi SIMS (sejak 2026-07-02)**: `sims.get_part_spec/get_part_info` =
  berat (kg), dimensi (cm), satuan, kemasan minimum per PN dari SIMS. Dipakai (a) halaman
  `part/[pn]` (blok "Spesifikasi Fisik", via `GET /api/parts/spec`), (b) **auto-berat ongkir**:
  `harga.weight_for(pn, allow_remote=True)` di `create_order` — bila kolom berat manual kosong,
  berat resmi SIMS mengisi otomatis → part tak lagi tertolak "tanpa berat".
- **Populasi**: data populasi unit (`services/populasi.py`).
- **Menu Stok (live Accurate)**: halaman `stok` menampilkan katalog stok penuh dari Accurate
  (indeks ber-cache TTL 5 mnt), terpisah dari stok.xlsx multi-gudang di atas — lihat §3.5.5l.

### 3.5.5 Asisten AI (DeepSeek, tool-calling)

`services/ai_assistant.py` — chatbot OpenAI-compatible (DeepSeek). Alur: loop tool-calling;
kamus sinonim **juga disuntikkan ke system prompt** ("KAMUS ISTILAH LAPANGAN"). Tools:

| Tool | Untuk | Akses |
|------|-------|-------|
| `cari_part` | cari PN+nama sekaligus, auto ekspansi sinonim, bisa scope `unit` | semua |
| `detail_part` | detail 1 PN (STOK **live dari Accurate** = utama, total+per gudang, `stok.xlsx` fallback; harga; spesifikasi SIMS) — tool utama pertanyaan stok 1 PN (§3.5.5i) | semua |
| `stok_accurate` | **STOK LIVE ERP Accurate** per PN: `stok_dapat_dijual` + `stok_per_gudang` (rincian per gudang/cabang, mis. 01.Jakarta/05.Makasar) (§3.5.5i) | semua |
| `info_aplikasi` | ringkasan index/stok/harga/gudang/kurs | semua |
| `daftar_unit` | daftar unit/model truk tersedia | semua |
| `cari_kode_kesalahan` | DTC/fault code Sinotruk-HOWO (ECU Bosch) via SPN+FMI / P-code / kata kunci | semua |
| `repair_kit_transmisi` | komponen repair kit per **transmisi assy** (seal kit perpak / overhaul / semua), resolve via kode model · assy PN · nama unit · **nomor rangka (gearbox PERSIS per-VIN via EPC, §3.5.5a)** | semua |
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
| `repair_kit_mesin` | **repair kit (维修包) MESIN Weichai per-VIN** — paket komponen servis/overhaul mesin dari nomor rangka; bila mesin tak punya kit terdefinisi → jujur + sarankan `uraikan_mesin` | semua |
| `pengganti_part` | **PERSAMAAN/SUPERSESSION part MESIN Weichai** — 'PN ini diganti nomor berapa?' (data 替换/ECN resmi, per PN, global) + silang stok/harga lokal | semua |
| `part_aus_dari_rangka` | **part servis/aus persis per-VIN** dari EPC Parts Atlas — auto pilih modul (rem/kopling/filter/dll); WAJIB utk part aus per-rangka, jangan cari_part lokal | semua |
| `assembly_utama_unit` | daftar **ASSEMBLY UTAMA terpasang** satu unit per-VIN ('four-assembly': kabin, gardan, mesin, transmisi, kopling — PN assy nyata) | semua |
| `banding_rangka` | **bandingkan part DUA unit via dua nomor rangka** (Loading List per-VIN) — part sama/beda per kategori; memicu kartu unduh Excel di UI (§3.5.5g) | semua |
| `banding_rangka_massal` | **bandingkan KATEGORI antar BANYAK unit (≥2) sekaligus** ("kabin semua unit PT X sama?" / daftar VIN) — via `rangka_list[]` ATAU `customer` (armada populasi); Loading List NYATA tiap VIN → kelompokkan unit ber-set-PN identik → verdict SERAGAM/BEDA + `kategori`='semua' (ringkasan semua kategori) + kartu unduh Excel matriks (§3.5.5j) | semua (mode `customer` → `admin`/`SEE_ALL`) |
| `part_termasuk_assy` | REVERSE: PN komponen kecil → **termasuk di assembly mana saja** (gearbox/kopling/gardan/mesin yang memuatnya) | semua |
| `cari_filter_shantui` | cari **FILTER alat berat SHANTUI** (hidrolik, oli, solar, udara, water separator, AC) per model excavator/dozer/roller/grader | semua |
| `buat_excel` | **EXPORT EXCEL DINAMIS** — "buatkan excelnya" atas data apa pun yg dibahas → model susun judul+kolom+baris dari hasil tool → kartu unduh gaya Claude; PN di isi file wajib grounded (anti-karangan) (2026-07-03) | semua |
| `katalog_kategori` | **KATALOG BERGAMBAR per-VIN** (§3.5.5h) — per kategori ("katalog kabin SJ346500") ATAU `semua` (LENGKAP); tanpa kategori → tawarkan pilihan. Excel 1 sheet: daftar isi hyperlink + gambar EXPLODED VIEW resmi EPC + No. Balon + stok/harga + pelengkap Loading List; dibangun saat kartu diunduh | semua |
| `cek_populasi` | data **populasi unit** (armada terdaftar: model, tipe, lokasi kerja, tahun, Euro, nopol) | `admin` / `SEE_ALL` |
| `banding_part_armada` | **banding SATU PART antar SEMUA unit milik SATU CUSTOMER/PT** ("kampas kopling PT X sama semua?") — populasi → rangka tiap unit → konfigurasi pabrik EPC per-VIN → kelompokkan → cek part EPC pada unit wakil/kelompok (paralel, maks 80 unit/5 kelompok) → verdict SAMA/BEDA dihitung sistem | `admin` / `SEE_ALL` |
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
  diuji. (Parts Atlas terstruktur & internal mesin Weichai → SUDAH, lihat §3.5.5d–e.)
- **Supersession (`partAlternateSale/replacementRelationship`) — DIVERIFIKASI BUNTU 2026-07-03:**
  endpoint ADA (`POST /api/rest/partAlternateSale/replacementRelationship`, body
  `{replacementType: [], beforeTh, afterTh}` — `replacementType` WAJIB array, kalau string →
  500) dan menjawab `success:true`, TAPI **selalu 0 baris** untuk semua PN dicoba. Bundle JS
  UI resmi menunjukkan menunya disembunyikan utk user eksternal (`notOutUser`) & filter tipe
  hanya utk `isAdminRole/isInnerRole` (`yfbgth`=研发变更 / `yfqjth`=全局 / `shqjth`=售后, admin-only)
  → data digerbang role; akun dealer SIMS melihat tabel kosong. `originalPartCode` di SIMS
  partInfo BUKAN supersession (itu nomor gambar asli, mis. `Q5280514/9`). JANGAN kejar ulang
  kecuali dapat akun internal Sinotruk.

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
- **AKSESORI TERPASANG DI MESIN juga domain Weichai** (2026-07-03): kompresor angin/air compressor,
  alternator, dinamo starter, turbo — di Atlas Sinotruk paling banter cuma PIPA/BRACKET penghubungnya.
  Kasus nyata: "air compressor unit `RJ345233`" dulu jatuh ke modul poros → cuma pipa outlet
  `YZ952536000194`, padahal Air Compressor Group lengkap ada di Weichai (Assy `1013133963`, compressor
  `1013133966`, gear `612630030032`). Fix: (a) trigger aksesori masuk `_ATLAS_MODULE_MAP` FDJ +
  `_AUS_KEYWORDS` (istilah China 空压机/发电机/起动机); (b) hasil `part_aus_dari_rangka` domain mesin
  membawa `catatan_mesin_weichai` (arahan lanjut `uraikan_mesin` bila komponen yg diminta tak ada di
  daftar), cabang kosong domain mesin → `jawaban_wajib` panggil `uraikan_mesin`; (c) prompt & deskripsi
  tool menyebut aksesori eksplisit. Test: `tests/test_atlas_routing.py`.

### 3.5.5f Guard anti-halusinasi Part Number — sejak 2026-07-01

Model kadang MENGARANG PN saat tool `found=False` (PN berurutan rapi + stok/harga palsu). Guard di
`ai_assistant.chat()`: tiap PN di jawaban WAJIB berasal dari **hasil tool turn ini** ATAU **riwayat**
(pesan user + jawaban asisten yg sudah lolos guard) — token mirip-PN diambil via `_PN_TOKEN_RE`
(huruf+angka ≥7 char; harga/qty diabaikan). PN tak bersumber = karangan → model dipaksa koreksi (maks
2×); bila tetap: SEMUA PN karangan → jawaban diganti pesan jujur "tidak ditemukan"; sebagian → PN
palsu disamarkan. Guard **selalu jalan** (termasuk follow-up tanpa panggil tool). Nomor rangka/VIN
yang user sebut otomatis ikut "grounded". Melengkapi anti-bocor tool-call (§3.5.5c).

**Perbaikan 2026-07-02** (temuan eval, commit `5c226c4`):
- **Jawaban kosong di-RETRY**: model kadang berhenti di blok `[PIKIR]` tanpa jawaban final →
  dulu user melihat "Maaf, jawabannya belum lengkap diproses". Kini `_strip_reasoning` return
  `""` dan `chat()` MEMAKSA model menulis ulang jawaban final (maks 2×,
  `_EMPTY_REPLY_CORRECTION`) sebelum jatuh ke pesan aman. Isi nalar tetap tak pernah bocor.
- **Kode seri unit tak disamarkan**: token mirip-PN yang sebenarnya NAMA SERI/UNIT katalog
  (`NX400HP`, `HOWO400`, `LZZ5EXSF`, `SG21-C6`) dulu bisa jadi "⟨PN tak terverifikasi⟩".
  Kini `_drop_unit_tokens()` (sumber `part_index.unit_models()` + `catalog_bom.list_units()`,
  cache 10 mnt, lazy) mengeluarkannya dari dugaan karangan; PN karangan asli tetap tertangkap.

### 3.5.5g Umpan balik 👍/👎 & kartu unduh Excel di jawaban asisten — sejak 2026-07-01

- **Umpan balik:** tombol 👍/👎 per jawaban asisten (`app/asisten/page.tsx`) →
  `POST /api/ai/feedback` (`services/ai_feedback.py`) → tabel Supabase **`ai_feedback`**
  (⚠️ **WAJIB dibuat manual** — belum ada file migrasi). Review admin di halaman
  **`/admin/feedback`** (ringkasan, filter rating, tandai selesai via
  `POST /api/ai/feedback/{id}/resolve`). Semua user login boleh memberi feedback;
  list/resolve khusus admin.
- **Export Excel banding rangka:** saat asisten menjawab perbandingan dua unit
  (`banding_rangka`), `chat()` mengembalikan field **`banding_exports`** → frontend
  menampilkan **kartu unduh Excel** (komponen `ExcelCard`) → `GET
  /api/ai/banding-rangka/export?rangka_1=&rangka_2=&kategori=` (`services/ai_export.py`,
  openpyxl) = perbandingan LENGKAP tanpa cap. Pola sama dgn tombol Excel repair kit
  (§3.5.5a, field `repairkit_models`).

### 3.5.5h Export Excel DINAMIS & KATALOG BERGAMBAR (exploded view) — sejak 2026-07-03

**Export Excel dinamis (`buat_excel`)** — user bilang "buatkan excelnya" atas data APA PUN
yang barusan dibahas:
- Model menyusun `judul + kolom + baris` dari HASIL TOOL percakapan → handler
  `_t_buat_excel` menyimpan payload via `ai_export.stash_export` (in-memory, TTL 24 jam,
  maks 200 entri, hilang saat restart) → metadata `excel_exports` di return `chat()` →
  frontend render kartu unduh (`AiExcelCard`, shell sama dgn `ExcelCard`) →
  `GET /api/ai/excel/{export_id}` membangun xlsx ber-styling (`generic_excel`).
- **Pagar anti-karangan**: `chat()` menyuntik set `grounded` ke args (`_grounded`) — PN di
  isi file yang tak pernah muncul dari tool/riwayat DITOLAK (kode unit dikecualikan).

**Katalog bergambar per-VIN (`katalog_kategori`)** — "berikan katalog kabin SJ346500",
"katalog lengkap unit X"; kategori: kabin/mesin/kopling/transmisi/gardan depan-belakang/
kelistrikan/rem/sasis/ac ATAU **'semua'** (katalog LENGKAP seluruh unit). **Tanpa kategori →
tool menolak + model menawarkan PILIHAN** (11 opsi) — jangan menebak.
- **Temuan EPC**: respons `part/tree/item` membawa **`d2s`** = nama file SVG **exploded view**
  figure (Creo Illustrate) & tiap item punya **`ballNum`** (nomor balon); file diunduh
  `GET /api/rest/file/<nama>` (WAJIB header Referer+UA; ekstensi diabaikan). `d3s` = 3D .pvz.
- `epc_bom.catalog_walk(rangka, kategori)`: pilih kategori top-level Atlas → walk BFS paralel
  → figures {nama, kode, svg, items:[balon, pn, qty, pengganti]}. Seleksi kategori 3 lapis:
  (1) ⚠️ skema kode EPC `ZZ-XX` ≠ kode katalog lokal (ZZ-01=kabin, ZZ-02=SASIS, ZZ-04=
  KELISTRIKAN, ZZ-05=POWERTRAIN) → prefix hanya utk kabin (`_KATALOG_ZZ_PREFIX`), lainnya
  kata kunci EN/CN (`_KATALOG_KEYWORDS`; term pendek spt 'ac' diganti frasa —
  `_KATALOG_TERM_KEYWORDS`); (2) UNION daftar figure RESMI modul UI
  (`GET /workOrder/getAcOfType?cjh&type=JSS` — kode modul dari app.js; cakupan dijamin ⊇
  pohon UI "01 Driver's cab"); (3) pelengkap **Loading List**: part terpasang kategori itu
  yang tak digambar di Atlas (baut/mur dsb) masuk seksi terakhir → cakupan part terpasang
  **100%** (SJ346500 kabin: 404/404). Mode 'semua' = semua kategori top-level, kelompok per
  bab ZZ, budget node 2000.
- **Build BERAT saat kartu DIKLIK** (bukan saat chat): `ai_export.stash_builder` menyimpan
  resep → `generic_excel` men-dispatch `katalog_excel`: unduh SVG paralel → render PNG via
  **resvg-py** (buang atribut `width/height` ber-mm; ⚠️ WAJIB **fonts-liberation** di image —
  angka balon = `<text font-family='Arial'>`, tanpa font sistem teks DIBUANG SENYAP) → 1 sheet
  "Katalog": judul + cara-baca (freeze) → DAFTAR ISI ber-hyperlink (klik nama → lompat;
  "↑ Daftar Isi" di tiap seksi) → seksi per figure: bar hijau → GAMBAR → tabel (No. Balon,
  PN mono, nama, qty/stok numerik, harga, pengganti). Bytes di-cache di entri stash → klik
  kedua instan.
- Terverifikasi live: SJ346500 kabin 73 seksi/821 part (±1 mnt); PB087964 LENGKAP **477
  figure / 5.146 part / 190 gambar / 16 MB** (walk 85s + build 101s). Dinamis utk VIN apa pun
  (Sinotruk/HOWO/SITRAK).

### 3.5.5i Integrasi ERP Accurate Online — stok live + auto-login SSO — sejak 2026-07-05

Menghubungkan MASPART ke **ERP/akunting Accurate Online** agar stok yang ditampilkan = **stok
riil pabrikan/gudang real-time**, bukan snapshot Excel. `services/accurate.py` + endpoint di
`routers/parts.py` + tool AI di `ai_assistant.py`.

- **Model akses (dibedah dari lalu-lintas web resmi, AMAN — meniru browser, tak meraba API):**
  App perusahaan di host **`iris.accurate.id`** (tiap company DB punya host/zona). Auth data =
  cookie **`JSESSIONID`** + parameter **`_dsi`** (di body). Endpoint stok:
  `POST /accurate/inventory/search-item.do` (body `_dsi,keywords,resetFilter,sp.pageSize/start/limit`)
  → `{"s":true,"d":[...],"sp":{rowCount}}`. `keywords=` memfilter server-side (lookup 1 PN instan).
  Field item: `no` (kode "NNNNNN.<PN>+suffix"), `name`, `availableToSell` (=stok dapat dijual),
  `quantity`, `unit1.name`. Contoh live: PT MAS AUTOMOBIL SEJAHTERA = **5.014 barang, 3.840 berstok**.
- **AUTO-LOGIN SSO PENUH** (`accurate.login()`) — dari **username+password** saja, tanpa captcha/2FA:
  1. `GET account.accurate.id/` (seed cookie) →
  2. `POST /pre-login.do` (account, password=`"up"+b64({v,p,d})`) → `d.permit` (challenge) →
  3. `POST /auth.do` (Spring Security `j_username`/`j_password`=`"ua"+b64({v,p,c=permit,t:null,d})`) → 302 /manage →
  4. `GET iris/accurate/open.do?uid=<uid>&product=aol` → **form SAML auto-submit** → `POST account/idp/sso`
     (SAMLRequest) → `POST iris/accurate/saml/SSO` (SAMLResponse) → sesi iris terautentikasi →
  5. `POST iris/accurate/open-database.do` (uid,product) → **`dsi` segar**; `JSESSIONID` dari cookie jar.
  Password dibungkus base64-JSON (bukan enkripsi), `d`=device-id + `uid`=uniqueId perusahaan (dari
  HTML `/manage`) → **STABIL**. Sesi di `data/accurate_session.json` (dibaca segar tiap panggil).
- **Auto-refresh + cooldown**: sesi mati (server pantul HTML SAML) → `_refresh_session` login ulang
  otomatis 1×. Bila login GAGAL (mis. Accurate throttle `errorTimeout`) → **cooldown 5 menit**
  (`_login_fail_until`): jangan hantam login lagi, pakai fallback lokal dulu (anti-abuse/anti-deteksi).
- **Stok per gudang/cabang** (`stock_full`): endpoint UI resmi `view-itemstock-bywarehouse.do`
  (param `id`,`asOfDate`) → `d.detailWarehouseData[]` {`warehouseName` mis "01.Jakarta", `balance`=qty
  terkini, `description`, `id`}. 2 panggilan (search→id → warehouse). ~20-37 gudang, gaya `stok.xlsx`.
  Endpoint ditemukan **AMAN dari JS statis** `cdn.accurate.id/.../inventory/item.js` (bukan tebak).
- **Accurate = sumber stok UTAMA**: di halaman detail part (`part/[pn]`) & tool `detail_part`,
  stok tampil dari Accurate (total+per gudang, badge "Accurate live"); **`stok.xlsx` = FALLBACK** hanya
  bila fetch Accurate gagal/tak tersedia (Excel = export Accurate → data sama). **Pembeli** tetap stok
  lokal terscope (jangan rusak alur beli/reservasi).
- **Hasil PENCARIAN juga Accurate-utama** (2026-07-05): `accurate.snapshot()` = katalog {norm_pn:
  stok,harga} dibangun di **THREAD LATAR** (stale-while-revalidate, TTL 30 mnt, ~45 dtk/4.8rb entri di
  prod) → `routers/parts._overlay_accurate` menimpa `stok`(total)+`harga` tiap baris hasil `/search` &
  `/search-name` (O(1), TAK menembak Accurate per-request). Excel = fallback (PN tak ada / snapshot
  belum siap / Accurate down). `gudang` (rincian) tetap Excel. `cari_part` (asisten) tetap Excel per-baris;
  angka live per-PN via `detail_part`/`stok_accurate`.
- **HARGA JUAL dari Accurate** (2026-07-05): field `unitPrice` (fallback `branchPrice`) di response
  item = harga jual satuan → `normalize_item.price`. **Accurate = sumber harga UTAMA** di detail part
  & `detail_part`/`stok_accurate` (field `harga_lokal`/`harga_jual`+`sumber_harga`), `harga.xlsx`
  fallback. Berlaku SEMUA peran (termasuk pembeli) → **menutup celah "stok ada tapi harga kosong →
  tak bisa dibeli"** (banyak PN punya harga di Accurate tapi kosong di `harga.xlsx`).
- **Env** (`ACCURATE_USERNAME/PASSWORD/DEVICE_ID/UID/HOST`, lihat `core/config.py`): di `.env` lokal
  & **Coolify Environment Variables** (sudah dibuat via API). ⚠️ device_id/uid spesifik akun+perusahaan.
- **Aturan aman (WAJIB):** JANGAN meraba endpoint (tebakan = 404 mencurigakan); hanya panggil endpoint
  yang dipakai browser; endpoint baru ditemukan dgn BACA JS STATIS; **jangan login berulang cepat**
  (Accurate throttle → `errorTimeout`); pakai ulang sesi. File sesi/HAR/kredensial di-`.gitignore`.

### 3.5.5j Banding BANYAK unit sekaligus (per kategori / semua kategori) — sejak 2026-07-06

Generalisasi `banding_rangka` (2 unit) & `banding_part_armada` (1 part) menjadi **banding
SATU KATEGORI (atau SEMUA kategori) antar BANYAK unit (≥2) sekaligus**. Menjawab "apakah
KABIN semua unit PT ARGCIO sama atau beda?" dan "cek 5 nomor rangka Sinotruk ini kabinnya
sama?" (kabin cuma contoh — berlaku semua kategori). Tool `banding_rangka_massal` di
`services/ai_assistant.py` (`_t_banding_rangka_massal`).

- **Dua mode input:** `rangka_list[]` (daftar VIN — **semua user**) ATAU `customer` (armada
  dari populasi — **admin/`SEE_ALL`** saja, gated di handler via `_can_populasi`). VIN
  di-dedup; dibatasi **`_MASSAL_MAX_UNITS`=15** unit (Loading List ~30 dtk/unit; sisanya
  dilaporkan `unit_terpotong`).
- **Cek NYATA tiap unit (bukan tebak konfigurasi):** ambil `epc_bom.loading_list(rangka)`
  PARALEL (6 worker), filter PN per kategori via `catalog_bom.pn_category_map()`, lalu
  **kelompokkan unit ber-SET-PN identik**. Unit yang gagal dibaca EPC dikecualikan +
  dilaporkan (`unit_gagal`); <2 unit sukses → error jujur (tak menyimpulkan). Dipilih atas
  trik grouping-konfigurasi (armada) karena **kabin tak ada di `epc.lookup`** → beda kabin
  halus hanya ketahuan dari Loading List nyata.
- **Verdict dihitung SISTEM** (bukan model): satu kelompok → `seragam=true`; >1 →
  `seragam=false` + rincian kelompok (unit mana) + `part_beda` (union−intersection, nama
  disilang `part_index.search_exact_pns`/`translate_cn`). Mode **`kategori='semua'`** (atau
  kosong) → `ringkasan_kategori` per kategori + `kategori_beda`/`kategori_seragam`.
- **Kartu unduh Excel** (pola `buat_excel`, field `excel_exports` → `AiExcelCard`, endpoint
  `GET /api/ai/excel/{id}`): mode 1 kategori = **matriks part × unit** (centang = terpasang,
  PN beda di atas); mode semua = **matriks unit × kategori** (angka = nomor kelompok; kolom
  yang semua "1" = seragam). Dibangun via `ai_export.stash_export`. **Tanpa endpoint/frontend
  baru** (reuse jalur export generik).
- **Test:** `tests/test_banding_massal.py` (14 kasus, EPC/populasi/catalog di-mock) — seragam/
  beda, dedup VIN, string dipisah koma, unit gagal dikecualikan, <2 sukses, kategori tak
  dikenal, mode semua, gating customer. Suite total **130 test** lolos.

### 3.5.5k Gambar EXPLODED VIEW satu PN — INLINE di chat — sejak 2026-07-06

Menampilkan **gambar exploded view resmi EPC untuk SATU Part Number langsung di jawaban
asisten** (bukan file unduh) + **nomor balonnya**. Contoh: *"cek bearing WG… untuk rangka
LZZ…"* → *"tampilkan gambar exploded view-nya"* → gambar figure hub assembly muncul di chat,
PN itu = No. balon N. Tool `gambar_exploded` (`_t_gambar_exploded`).

- **Cara kerja (per-VIN, reuse infra §3.5.5h):** `epc_bom.exploded_figures(rangka, pn, kategori)`
  memakai `catalog_walk` pada kategori tsb, lalu **saring figure yang salah satu item-nya
  ber-PN sama** → ambil `svg` (d2s) + `ballNum` PN itu. Render `ai_export.exploded_png`
  (unduh SVG via `fetch_file` → resvg SVG→PNG). Butuh **nomor rangka** + **kategori** (untuk
  mempersempit; ditentukan dari jenis part: bearing/hub→gardan, rem→rem, piston→mesin, dst).
- **Delivery:** tool stash builder (`kind:"exploded"`) → `generic_excel` men-dispatch ke
  `exploded_png` → **endpoint `GET /api/ai/excel/{id}` menyajikan `image/png` INLINE**
  (Content-Disposition inline). `chat()` kembalikan metadata **`exploded_images`** → frontend
  **`AiExplodedImages`** (fetch blob ber-auth → `objectURL` → `<img>` + kaption balon+PN+figure).
- **Batas:** hanya **Sinotruk/HOWO/SITRAK** (Parts Atlas per-VIN); butuh PN memang terpasang &
  ber-figure di kategori itu (part work-BOM baut/mur tak digambar). Versi **tanpa VIN** (buka
  figure langsung dari PN global) belum — endpoint katalog-standar (18080 `/struct`) belum ketemu.
- Terverifikasi live: PB087964 + `WG9761349009` (gardan) → figure "braking plate assembly",
  balon 3, PNG 132 KB ter-render. **136 test lolos** (tak ada regresi).

### 3.5.5l Menu STOK — daftar stok live seluruh barang Accurate — sejak 2026-07-06

Menu baru **Stok** (sidebar bagian **Data**, di bawah Harga) menampilkan **katalog stok
LIVE seluruh barang dari Accurate** dalam tabel, kolom mengikuti Daftar Harga: **Part Number,
Part Name, Stok, Satuan**. Dilengkapi cari (PN/nama/kode Accurate), urut (PN, nama, Stok ↑/↓),
paginasi & **export Excel**.

- **Sumber data:** `accurate.all_items()` → `refresh()` = **indeks ternormalisasi ber-cache
  TTL 5 menit** (`_INDEX_TTL`, dibagi semua user). Buka pertama (cache dingin/kadaluarsa) →
  tarik katalog penuh dari Accurate (paging ~4.8rb barang); dalam 5 menit berikutnya dilayani
  dari memori (**tak menembak Accurate**). Cari/urut/ganti-halaman diproses server-side di atas
  cache → murah. Beban maks: 1 tarik penuh per 5 menit apa pun jumlah user.
- **Backend:** `services/stok.py` (filter/urut/`display_rows`/`to_excel_bytes`) +
  `routers/stok.py`: `GET /api/stok/list` (paginasi), `GET /api/stok/list/export` (Excel),
  `POST /api/stok/refresh` (admin, `accurate.refresh(force=True)`). Kegagalan sesi/koneksi
  Accurate dikembalikan sebagai **status flag** (`configured`/`session_expired`/`error`), bukan
  HTTP error — frontend tampilkan banner seadanya (pola sama `/api/parts/accurate-stock`).
- **Izin & akses:** key menu **`stok`** di `permissions.MENU_TABS` (muncul di Menu Control,
  discope per-user). Default aktif utk admin & user tanpa baris izin. **Pembeli DITOLAK**
  (`BUYER_DENY` di `AppShell`).
- **Frontend:** `app/stok/page.tsx` (mirip sub-tab List Harga) + `getStokList`/`exportStokList`
  di `lib/api.ts`; item nav "Stok" di `AppShell` (`NAV_DATA`, key `stok`).
- **Deployed live 2026-07-06** (commit `2e5488f`, snapshot-clean). Terverifikasi: 136 test lolos,
  tsc bersih, `https://maspart.tech/stok` → 200, `/api/stok/list` → 401 (route hidup).

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
| **parts** `/api/parts` | `GET /search` (PN), `GET /search-name`, `POST /search-image`, `GET /compare`, `GET /photos`, `GET /spec` (berat/dimensi SIMS), `GET /accurate-stock` (stok live Accurate +per gudang, §3.5.5i), `GET /image-proxy`, `GET /batch-template`, `POST /batch-catalog`, `GET/POST /index/status·refresh` |
| **harga** `/api/harga` | `GET /list·/list/export·/rate·/cari`, `POST /batch·/batch/export·/refresh` |
| **stok** `/api/stok` | `GET /list·/list/export` (daftar stok live Accurate, §3.5.5l), `POST /refresh` (admin) |
| **opname** `/api/opname` | `GET /draft·/history`, `POST /draft/from-upload·/finalize`, `PUT/DELETE /draft`, `DELETE /history/{id}` |
| **populasi** `/api/populasi` | `GET ""·/export`, `POST /refresh` |
| **orders** `/api` | `POST /orders`, `GET /orders·/orders/{code}`, `POST /orders/{code}/confirm·cancel·proof`, `GET /shipping/rates`, `POST /shipping/weight`, `GET /payments/methods`, `POST /payments/webhook`, `GET/PUT /admin/orders...` |
| **buyer** `/api/buyer` | `GET /locations·/location`, `POST /location` |
| **branch** `/api/branch` | `GET /orders·/orders/count·/orders/{code}·/sales`, `PUT /orders/{code}/status` |
| **chat** `/api` | `GET/POST /orders/{code}/chat`, `/chat/buyer/threads`, `/chat/gudang/{key}`, `/chat/branch/...` |
| **geo** `/api/geo` | `GET /reverse·/search` |
| **ai** `/api/ai` | `GET /status`, `POST /chat`, `POST /feedback`, `GET /feedback` (admin), `POST /feedback/{id}/resolve` (admin), `GET /banding-rangka/export`, `GET /excel/{export_id}` (export dinamis + katalog bergambar §3.5.5h) |
| **repairkit** `/api/repairkit` | `GET /transmisi`, `GET /transmisi/export` |
| **admin** `/api/admin` | users, perms, gudang, `upload/{kind}`, `upload-catalog`, monitoring, sales, photos, `index*` (reload galeri/bulk), `catalog-bom/status·rebuild` |
| **meta** | `GET /health` |

### 3.5.9 Peta halaman frontend (Next.js App Router, `frontend/src/app/`)

`login` · `/` (search PN) · `search` · `search-image` · `compare` · `part/[pn]` · `harga` ·
`stok` (stok live Accurate, §3.5.5l) · `batch` · `opname` · `populasi` · `download` · `asisten` (AI) · `keranjang` · `pesanan` +
`pesanan/[code]` + invoice · `pilih-lokasi` · `chat` · `cabang/*` (pesanan/penjualan/chat) ·
`admin/*` (menu, users, gudang, upload, monitoring, index, foto, orders, penjualan, **feedback**).

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
  `PUBLIC_BASE_URL`, `DEEPSEEK_API_KEY/BASE_URL/MODEL`,
  `ACCURATE_USERNAME/PASSWORD/DEVICE_ID/UID/HOST` (stok live Accurate, §3.5.5i).
- **Selftest tanpa server/network**: `cd backend && python selftest.py <PN>`.
  Accurate: `python -m app.services.accurate <PN>` (butuh env/sesi Accurate).

### 3.5.11 Monitoring User (online/offline) — sejak 2026-07-01

Panel admin **Monitoring User** (`/admin/monitoring`, menu di sidebar admin) — status **online/offline**
+ aktivitas terakhir tiap user. Pelacakan **in-memory** (tanpa migrasi DB, tanpa tulis DB per request):
`services/presence.py` — `touch(username)` dipanggil di `deps.get_current_user` tiap request
terautentikasi; `mark_login` saat login. **Online = aktif ≤ 5 menit** (`ONLINE_WINDOW_SEC`). Endpoint
`GET /api/admin/monitoring` menggabung `list_users_full()` + presence → online_count/urut online dulu +
aktivitas terbaru. Frontend `admin/monitoring/page.tsx` (auto-refresh 15 dtk, filter online). Reset saat
container restart (wajar utk "siapa online sekarang"; setup 1 container backend). Menu didaftarkan di
`AppShell` `NAV_ADMIN`.

### 3.5.12 Keamanan — Audit & Hardening (2026-07-04)

Audit keamanan menyeluruh (5 review paralel: auth, injeksi/upload/SSRF, secrets/CORS/
webhook, asisten AI, frontend) + perbaikan temuan Kritis/Tinggi/Menengah. Semua fix
di bawah **sudah LIVE & terverifikasi**; tes regresi di `tests/test_security_hardening.py`
(19 kasus, bagian dari 116 unit test).

**Sudah diperbaiki (deployed):**
- **Rate-limit anti-spoof XFF** (`core/ratelimit.py`): IP klien diambil dari sisi KANAN
  `X-Forwarded-For` sesuai `trusted_proxies` (default 1, Traefik). Sebelumnya ambil hop
  kiri yang dikontrol penyerang → limiter login/webhook trivial dilewati XFF palsu.
- **JWT_SECRET wajib** (`core/config.py`): default/kosong = FATAL menolak start di
  lingkungan APA PUN; `APP_ENV` tak dikenal/kosong → fail-closed diperlakukan produksi.
- **Otorisasi fail-closed saat DB down** (`deps.py`): saat Supabase mati & cache kosong,
  role privileged (admin/pembeli) TIDAK dipercaya dari klaim token — diturunkan ke `user`.
- **Rate-limit `/api/ai/chat`** + `/banding-rangka/export` + `/excel/{id}` (cegah abuse
  biaya DeepSeek / DoS 1 worker).
- **Guard formula/CSV injection** di semua export Excel (`ai_export.py`, `catalog.py`):
  sel teks diawali `= + - @` di-escape (`'`) → ditulis sebagai teks, bukan formula/DDE.
- **Upgrade password plaintext legacy → bcrypt saat login** (`services/auth.py`), lalu
  null-kan kolom plaintext (migrasi bertahap, tak mengunci user).
- **Security headers**: API (`main.py`) HSTS + `X-Frame-Options: DENY` + nosniff +
  Referrer-Policy; frontend (`next.config.ts`) **CSP** + header keamanan (produksi).
- **SRI pin Leaflet** dari unpkg (SHA-384) — CDN/paket terkompromi/MITM tak bisa suntik JS.
- **Token ikut "Ingat saya"** (`lib/auth.ts`): `sessionStorage` bila tak dicentang.
- **Cap upload** `batch-catalog` 8 MB (anti zip-bomb/OOM).
- Menyusul: **CSP mengizinkan Google Fonts** (`fonts.googleapis.com`/`gstatic.com`) —
  memperbaiki regresi font Geist/JetBrains Mono yang sempat diblokir CSP.

**Terverifikasi AMAN (tak perlu diubah):**
- **Webhook pembayaran** bertahan berlapis: verifikasi callback key + **re-konfirmasi
  status ke gateway** + cek `order_id`+amount + idempotent + rate-limited → body "paid"
  palsu tak mempan (`routers/orders.py`, `services/payments.py`).
- **Otorisasi tool asisten** di-enforce di **dispatch handler** (tiap `_t_*` cek `user`),
  bukan sekadar disembunyikan dari daftar → `<invoke>` yang bocor/diinjeksi tetap `denied`.
- **IDOR order/cabang**: scope `username=`/`gudang=` di query Supabase (server-side).
- Total/berat/ongkir order **dihitung ulang server-side**; JWT alg dipin HS256, `exp`
  diverifikasi; tak ada `subprocess`/`eval`/`exec` atas input; output markdown asisten
  di-render aman (React, tanpa `dangerouslySetInnerHTML`); path traversal upload diblok
  (`_safe_catalog_dir`); tak ada secret di bundle frontend; `epc_token.txt` tak ter-track.

**⚠️ BELUM diperbaiki — butuh tindakan pemilik:**
- **KRITIS — kunci Supabase di git history.** Commit lokal **`1be5c53`** di branch `main`
  men-track `.streamlit/secrets.toml` berisi Supabase `url` + anon key + **`service_key`
  (bypass RLS)**. BELUM ter-push ke origin & tak ada di `snapshot-clean`. **SEBELUM push
  `main`:** rotate service_key + anon key di dashboard Supabase, lalu scrub file dari
  history (`git filter-repo`/BFG) termasuk `1be5c53`. Lihat [[jangan-push-main-secrets]].
- **TLS `verify=False` ke EPC/SIMS** (`epc.py`, `epc_bom.py`, `epc_weichai.py`): sertifikat
  upstream invalid (itu sebabnya dimatikan) — mengaktifkan verifikasi apa adanya mematikan
  fitur EPC. Perlu pin CA/sertifikat EPC (investigasi ke endpoint live).
- Temuan RENDAH (belum ditutup, dampak kecil): image-proxy mengikuti redirect (host
  allowlist sudah benar); enumerasi username via timing login; `/health` bocorkan
  `data_dir`; bcrypt truncate 72 byte.

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

# 1) UNIT TEST logika murni — cepat (<20 dtk), TANPA network/API. Jalankan tiap ubah kode.
python -m pytest tests/ -q                 # 116 test per 2026-07-04
#    Cakupan: guard anti-halusinasi PN (_extract_pns/_sanitize_ungrounded + loop chat()
#    dgn DeepSeek di-mock), anti-bocor tool-call, ekspansi sinonim, catalog_bom
#    (resolve/verdict/compare), routing modul Atlas (test_atlas_routing), export Excel
#    dinamis + stash builder + svg→png (test_buat_excel), banding part armada per
#    customer (test_banding_armada — populasi & EPC di-mock).

# 2) EVAL REGRESI Asisten AI — golden questions lewat chat() NYATA (DeepSeek + tool asli).
#    Jalankan SEBELUM deploy perubahan prompt/tool. Ada biaya API kecil per run.
python evals/run_evals.py                  # semua kasus 'lokal' (default; ~24 kasus)
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
- Setelah `build.sh`, container belum berubah sampai di-**Redeploy** — itu yang
  me-recreate container dengan image baru.
- **Redeploy BISA dari CLI** (terbukti 2026-07-03 — klik dashboard TIDAK wajib):
  container dikelola docker-compose Coolify di
  `/data/coolify/services/jmmamc7kvqr6nlev97r79j5q/` (service `backend`, `frontend`):
  ```bash
  ssh root@maspart.tech "cd /data/coolify/services/jmmamc7kvqr6nlev97r79j5q && \
    docker compose up -d --force-recreate backend"    # dan/atau frontend
  ```
  Health OK ±20-35s. Ini setara tombol Redeploy (recreate dari image `:latest` baru).
- ⚠️ Dockerfile backend WAJIB mempertahankan **`fonts-liberation`** (apt) — dipakai render
  gambar exploded view katalog (§3.5.5h); tanpa font, angka balon hilang senyap.
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
- Branch utama: `main` — ⚠️ **TERTINGGAL**: seluruh pekerjaan sejak 2026-06-28 ada di branch
  **`snapshot-clean`** (aktif, ter-push ke `origin/snapshot-clean`, dikomit per-fitur sejak
  2026-07-02). TODO: fast-forward/merge `main` ke `snapshot-clean` agar clone baru dapat kode
  terkini.
- Konvensi commit: per-fitur (`feat(epc):`, `fix(ai):`, `test:`, `docs:` …), pesan Bahasa
  Indonesia. Sebelum push perubahan AI: `python -m pytest tests/ -q` + `python evals/run_evals.py`.

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
- [x] **Suite pengujian** — SELESAI 2026-07-02: unit test `backend/tests/` (116 test murni
      per 2026-07-04) + eval regresi AI `backend/evals/` (golden questions, `run_evals.py`).
      Lihat §4.
- [x] **Audit & hardening keamanan** — SELESAI 2026-07-04 (§3.5.12), LIVE: rate-limit
      anti-spoof XFF, JWT_SECRET wajib + APP_ENV fail-closed, otorisasi fail-closed saat DB
      down, rate-limit `/api/ai/chat`, guard formula-injection Excel, upgrade password
      plaintext→bcrypt saat login, security headers + CSP + SRI Leaflet, cap upload. Webhook
      pembayaran & otorisasi tool diverifikasi AMAN. Tes: `test_security_hardening.py` (19).
- [x] **Aksesori mesin (air compressor dll) rute ke Weichai + urutan komponen utama** —
      SELESAI 2026-07-03 (§3.5.5e), LIVE (hot-swap + image). Eval `weichai-air-compressor`
      pass; checker `no_new_pn` kini kecualikan kode unit (samakan guard produksi).
- [x] **Export Excel DINAMIS (`buat_excel`)** — SELESAI 2026-07-03 (§3.5.5h), LIVE:
      "buatkan excelnya" → kartu unduh gaya Claude; PN grounded (anti-karangan);
      endpoint `GET /api/ai/excel/{id}`; frontend `AiExcelCard`.
- [x] **KATALOG BERGAMBAR per-VIN (`katalog_kategori`)** — SELESAI 2026-07-03/04 (§3.5.5h),
      LIVE: per kategori ATAU 'semua' (lengkap); exploded view resmi EPC (d2s SVG→PNG,
      resvg + fonts-liberation); 1 sheet + daftar isi hyperlink + No. Balon; cakupan
      dijamin (union modul JSS + pelengkap Loading List = 100% part terpasang); tanpa
      kategori → asisten menawarkan pilihan. Terverifikasi SJ346500 (kabin 73 seksi) &
      PB087964 (lengkap 477 figure/16 MB).
- [x] **Semua pekerjaan dikomit per-fitur & di-push** — SELESAI 2026-07-02 (10+ commit di
      `snapshot-clean` → `origin/snapshot-clean`; sebelumnya menggantung tak terkomit).
      ⚠️ Pekerjaan 2026-07-03/04 (buat_excel + katalog bergambar + font fix + evals) sudah
      LIVE di server tapi **BELUM dikomit** (ditahan atas permintaan) — komit per-fitur
      saat sudah dikonfirmasi.
- [x] **Fix guard (jawaban tertelan [PIKIR] + kode seri disamarkan)** — SELESAI 2026-07-02
      (§3.5.5f), LIVE di server (hot-swap + image + Redeploy).
- [x] **Repair kit transmisi per-VIN (arg `rangka` via EPC)** — SELESAI 2026-07-02 (§3.5.5a),
      LIVE. `repair_kit_mesin` no_kit → saran `uraikan_mesin` (2026-07-03), LIVE.
- [x] **Supersession part sasis Sinotruk** — DIVERIFIKASI BUNTU 2026-07-03 (§3.5.5c):
      endpoint ada tapi data role-gated internal; JANGAN kejar tanpa akun internal.
      (Part mesin Weichai → sudah ada `pengganti_part`.)
- [ ] **⛔ KRITIS — rotate kunci Supabase + scrub `secrets.toml` SEBELUM push `main`** — commit
      lokal `1be5c53` di `main` men-track `.streamlit/secrets.toml` berisi `service_key` (bypass
      RLS); belum ter-push. Rotate service_key + anon key di dashboard, scrub file dari history
      (filter-repo/BFG), baru merge/push (§3.5.12). JANGAN jalankan TODO merge `main` di bawah
      sebelum ini beres.
- [x] **Banding BANYAK unit sekaligus** — SELESAI & DEPLOYED 2026-07-06 (§3.5.5j): tool
      `banding_rangka_massal` (daftar VIN / customer; 1 kategori atau semua; verdict sistem + Excel
      matriks) + disambiguasi routing (kategori→tool ini, bukan `banding_part_armada`). 14 test lolos
      (total 130). Live di container prod (scp+build.sh+force-recreate, health OK). ⚠️ BELUM dikomit.
- [ ] **TLS `verify=False` ke EPC/SIMS** — sertifikat upstream invalid; perlu pin CA agar bisa
      mengaktifkan verifikasi tanpa mematikan EPC (§3.5.12).
- [ ] **Fast-forward/merge `main` ke `snapshot-clean`** — `main` di GitHub tertinggal jauh;
      clone baru dapat kode lama (lihat §7). ⚠️ Lakukan HANYA setelah scrub secrets di atas.
- [x] **`migrations/015_ai_feedback.sql`** — SELESAI: file migrasi dibuat (DDL identik dgn
      `ai_feedback.py::create_table_sql()`) agar konsisten dgn tabel lain (§3.5.5g). Jalankan
      sekali di Supabase SQL Editor bila tabel belum ada.
- [ ] **Backup otomatis `/opt/maspart/data`** — berisi upload admin + token; tidak semua di
      git. Cron tar harian / rclone.
- [~] **Migrasi password plaintext** — SEBAGIAN (2026-07-04): login via plaintext legacy kini
      di-upgrade ke bcrypt + null-kan kolom plaintext (`services/auth.py`). SISA: hash baris yang
      belum pernah login ulang lalu hapus jalur fallback (`core/security.py`).
- [x] **Foto part di jawaban asisten** — SELESAI 2026-07-04: `chat()` kembalikan `part_pns`;
      frontend `PartThumbs` (thumbnail foto SIMS via image-proxy) → klik detail part.
- [x] **Log pencarian nihil → umpan sinonim** — SELESAI 2026-07-04: `services/search_log.py`
      (`data/search_misses.json`); hook di `/search`·`/search-name`·`cari_part`; halaman admin
      `/admin/search-misses` + endpoint `GET/POST /api/admin/search-misses[/resolve]`.
- [x] **Katalog bergambar PDF (pilih Excel/PDF)** — SELESAI 2026-07-04: tool `katalog_kategori`
      arg `format`; asisten TANYA Excel atau PDF; `ai_export.katalog_pdf` (reportlab). §3.5.5h.
- [x] **Integrasi stok live ERP Accurate** — SELESAI & DEPLOYED 2026-07-05 (§3.5.5i): auto-login SSO
      penuh + auto-refresh + cooldown; stok per-PN + rincian per gudang/cabang; Accurate = sumber stok
      UTAMA (app detail part + `detail_part`), `stok.xlsx` = fallback; tool AI `stok_accurate`; endpoint
      `/api/parts/accurate-stock`; env `ACCURATE_*` (di `.env` + Coolify via API). Terverifikasi dari
      container prod. ⚠️ jangan login berulang cepat (Accurate throttle → errorTimeout).
- [ ] **(opsional) Rotasi API token Coolify** yang dipakai set env Accurate (sudah sempat dibagikan).
- [x] **(opsional) Stok Accurate di halaman HASIL PENCARIAN** — SELESAI (§3.5.5i): `snapshot()` di
      thread latar (stale-while-revalidate) → `routers/parts._overlay_accurate` menimpa stok+harga tiap
      baris `/search`·`/search-name` (O(1), non-blocking). Excel = fallback; rincian per-gudang tetap Excel.
- [ ] **Kandidat fitur berikut**: harga jual otomatis dari modal SIMS utk part tanpa harga
      (stok ada tapi tak bisa dibeli), saran restock AI (penjualan×stok), interchange otomatis
      saat habis (`pengganti_part`+stok), OCR VIN dari foto, fault code→part, penawaran/quote
      PDF, transfer antar-gudang, profil customer/armada, asisten via WhatsApp, loop
      feedback→eval, auto-refresh index setelah upload-catalog, CI GitHub Actions (pytest),
      pecah `ai_assistant.py` (±4.500 baris), ONNX pengganti torch.
- [ ] **Revoke API token Coolify** yang dipakai untuk migrasi (di dashboard →
      Keys & Tokens) setelah yakin stabil — token = kontrol penuh.
- [ ] Setelah Coolify stabil beberapa hari, pertimbangkan beresihkan fallback systemd
      (atau biarkan saja — sudah disabled, tidak mengganggu).
- [ ] Pantau RAM: VPS hanya 3.8GB; container backend memuat torch+DINOv2. Hindari
      menjalankan systemd lama + container bersamaan (double torch = risiko OOM).
- [x] **`JWT_SECRET` kuat & `APP_ENV=prod`** — TERVERIFIKASI + kini DIPAKSA: server menolak
      start bila secret default/kosong, dan APP_ENV tak dikenal diperlakukan produksi (§3.5.12).
- [ ] Rahasia (`backend/.env`, `.streamlit/secrets.toml`) jangan sampai ter-commit — lihat
      butir KRITIS di atas (`1be5c53` di `main`).
```
