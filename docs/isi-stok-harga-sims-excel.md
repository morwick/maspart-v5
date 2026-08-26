# Isi kolom Stok & Harga SIMS ke Excel via API produksi

> **Sejak 2026-08-25 ini bisa langsung lewat ASISTEN AI.** Lampirkan Excel-nya di chat
> lalu minta "isikan stok dan harga" — asisten menulis isian **ke file itu sendiri**
> (`ai_export.isi_di_tempat`): format, rumus, baris kop, sheet lain tidak diubah, kolom
> baru ditambahkan di kanan, sel berumus & sel yang sudah berisi tidak ditimpa.
> Cara manual di bawah tetap berguna untuk file di luar chat (>10 MB / >5.000 baris)
> atau bila ingin mengendalikan sendiri nomor kolomnya.

Cara mengisi kolom **Stok** (Accurate) dan **Harga SIMS** (CNY) ke sebuah file Excel
(misalnya daftar rencana kirim) memakai **API produksi maspart.tech**, bukan login
langsung ke Accurate/SIMS dari mesin lokal.

## Kenapa lewat API, bukan login langsung

Akun Accurate dan SIMS **single-session**. Login dari proses lain (mis. script lokal)
akan saling menendang sesi dengan backend produksi — pernah kejadian nyata (lihat
`PROJECT.md` §"login-logout Accurate 5×/24 jam"). Jadi jangan pernah pakai kredensial
`ACCURATE_*` / `SIMS_USERNAME` dari `backend/.env` atau `backend/shared/sims_price_fetcher.py`
langsung dari laptop. Selalu lewat endpoint REST produksi — server produksi yang
memegang sesi, script cuma jadi klien HTTP biasa.

## Endpoint yang dipakai

| Kebutuhan | Endpoint | Sumber data | Catatan |
|---|---|---|---|
| Login | `POST /api/auth/login` `{username, password}` | — | ⚠️ Akun **single-device**: login ini akan **logout** sesi app/APK yang sedang aktif dengan akun yang sama. |
| Stok | `GET /api/parts/accurate-stock?pn=<PN>` | Indeks Accurate (cache, sinkron 3×/hari) | **Baca cache saja, TIDAK pernah login live ke Accurate** — aman dipanggil ratusan kali. |
| Harga SIMS | `POST /api/harga/batch` `{part_numbers: [...]}` | Live fetch SIMS oleh server produksi | Maks **300 PN per panggilan** (`_MAX_BATCH` di `backend/app/routers/harga.py`); hasil `cny` = harga MODAL apa adanya, JANGAN dikonversi sendiri ke rupiah. |

`Harga SIMS` hanya terisi (tidak di-mask jadi `None`) untuk akun **admin/SEE_ALL**
(izin `ai_harga_sims` / `boleh_harga`, lihat `backend/app/services/permissions.py`).

## Langkah

1. **Baca PN unik dari file sumber** (kolom Part Number, buang baris kosong,
   `.strip().upper()`, dedup).
2. **Login** → simpan `access_token`.
3. **Fetch Stok** per-PN (boleh paralel beberapa worker, endpoint ini cache-only jadi
   aman) → `available_to_sell` dari `resp["stock"]`. Bila `found: false` → PN memang
   tidak punya kartu barang di Accurate (bukan error) → biarkan kosong.
4. **Fetch Harga SIMS** lewat `/api/harga/batch`, dipecah per 300 PN (bisa lebih kecil
   spy ada progres, mis. 100/batch). Ambil `cny` dari tiap hasil; `status != "ok"` →
   biarkan kosong.
5. **Tulis balik ke Excel** dengan `openpyxl` (load workbook ASLI tanpa `data_only`,
   supaya format/kolom lain tak berubah), isi kolom Stok & Harga SIMS per baris
   berdasar PN di baris itu, `save()` ke file baru (jangan timpa file asli).
6. Laporkan jujur: berapa baris terisi vs kosong, dan daftar PN yang tak ketemu —
   jangan mengarang nilai untuk baris yang tak ketemu.

## Script referensi

Simpan sebagai 3 file kecil (lebih gampang di-retry per tahap daripada satu script
panjang — fetch harga SIMS live untuk ratusan PN bisa makan beberapa menit).

**`fetch_stok.py`** — isi Stok (paralel, cache-only jadi aman banyak worker):

```python
import json, time, requests
from concurrent.futures import ThreadPoolExecutor, as_completed

BASE = "https://maspart.tech"
TOKEN = requests.post(f"{BASE}/api/auth/login",
                       json={"username": "...", "password": "..."},
                       timeout=20).json()["access_token"]
HEAD = {"Authorization": f"Bearer {TOKEN}"}

pns = json.load(open("pns.json", encoding="utf-8"))  # list PN unik, upper-case

def fetch_one(pn):
    for attempt in range(3):
        try:
            r = requests.get(f"{BASE}/api/parts/accurate-stock",
                              params={"pn": pn}, headers=HEAD, timeout=20)
            r.raise_for_status()
            return pn, r.json()
        except Exception as e:
            if attempt == 2:
                return pn, {"error": str(e)}
            time.sleep(1)

results = {}
with ThreadPoolExecutor(max_workers=8) as ex:
    futs = {ex.submit(fetch_one, pn): pn for pn in pns}
    for fut in as_completed(futs):
        pn, data = fut.result()
        results[pn] = data

json.dump(results, open("stok_results.json", "w", encoding="utf-8"),
          ensure_ascii=False, indent=1)
```

**`fetch_harga.py`** — isi Harga SIMS (sekuensial per-batch 100 PN, live SIMS jadi
lebih lambat — jangan diparalelkan di sisi klien, server sudah paralel di dalam):

```python
import json, requests

BASE = "https://maspart.tech"
TOKEN = requests.post(f"{BASE}/api/auth/login",
                       json={"username": "...", "password": "..."},
                       timeout=20).json()["access_token"]
HEAD = {"Authorization": f"Bearer {TOKEN}"}

pns = json.load(open("pns.json", encoding="utf-8"))
all_results = {}
for i in range(0, len(pns), 100):
    chunk = pns[i:i + 100]
    r = requests.post(f"{BASE}/api/harga/batch", json={"part_numbers": chunk},
                       headers=HEAD, timeout=600)
    r.raise_for_status()
    for row in r.json().get("results", []):
        all_results[row["pn"]] = row

json.dump(all_results, open("harga_results.json", "w", encoding="utf-8"),
          ensure_ascii=False, indent=1)
```

**`fill_excel.py`** — tulis balik ke file Excel (kolom H = Stok, kolom I = Harga
SIMS, kolom C = Part Number — sesuaikan nomor kolom dengan file sumber):

```python
import json
import openpyxl

SRC = r"path\ke\file_sumber.xlsx"
DST = r"path\ke\file_sumber - terisi.xlsx"

stok_map = json.load(open("stok_results.json", encoding="utf-8"))
harga_map = json.load(open("harga_results.json", encoding="utf-8"))

wb = openpyxl.load_workbook(SRC)     # TANPA data_only → format/formula lain aman
ws = wb["Sheet1"]

for r in range(2, ws.max_row + 1):
    pn = str(ws.cell(r, 3).value or "").strip().upper()
    if not pn:
        continue
    sinfo = stok_map.get(pn) or {}
    if sinfo.get("found"):
        ws.cell(r, 8).value = sinfo["stock"]["available_to_sell"]
    else:
        ws.cell(r, 8).value = ""
    hinfo = harga_map.get(pn) or {}
    if hinfo.get("status") == "ok" and hinfo.get("cny") is not None:
        ws.cell(r, 9).value = hinfo["cny"]
    else:
        ws.cell(r, 9).value = ""

wb.save(DST)
```

Jalankan dengan Python venv backend (sudah punya `requests`/`openpyxl`):

```
backend/venv/Scripts/python.exe fetch_stok.py
backend/venv/Scripts/python.exe fetch_harga.py
backend/venv/Scripts/python.exe fill_excel.py
```

## Contoh hasil (2026-08-24)

File `05.RENCANA KIRIM KE POMALA&SCM.xlsx`, 416 baris / 408 PN unik, akun `admin`:

- Stok: **300/416** baris terisi — 115 PN unik memang tidak punya kartu barang di
  Accurate (bukan error, `found: false` murni).
- Harga SIMS: **414/416** baris terisi (kurs saat itu ±Rp 2.638,52/CNY, nilai
  disimpan **dalam CNY apa adanya**) — 2 PN tak ketemu di SIMS.

## Batasan yang perlu diingat

- `/api/harga/batch` maksimum 300 PN per panggilan (`_MAX_BATCH`).
- Login lewat `/api/auth/login` akan **logout paksa** sesi app/APK aktif akun yang
  sama (kebijakan single-device, `session_policy.py`) — beri tahu pemilik akun dulu.
- `harga_sims` (CNY) hanya terisi untuk akun admin/SEE_ALL; akun lain akan dapat
  `cny: null` walau `status: "ok"`.
- Jangan pernah login langsung ke Accurate/SIMS dari script lokal — selalu lewat
  endpoint di atas.
