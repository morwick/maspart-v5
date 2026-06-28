# MASPART API — Backend FastAPI (Fase 1)

Langkah pertama migrasi MASPART dari Streamlit ke **FastAPI (backend) + Next.js (frontend)**.

Fase 1 ini membungkus dua hal sebagai REST API, **tanpa mengganggu app Streamlit yang sudah jalan**:

- **Auth** — login → JWT (verifikasi password identik dengan Streamlit: bcrypt + legacy)
- **Search Part Number** — logika `search_part_number` diekstrak murni (tanpa `st.*`)

## Struktur

```
backend/
├── requirements.txt
├── .env.example          # salin ke .env lalu isi
└── app/
    ├── main.py           # FastAPI app + CORS + /health
    ├── deps.py           # get_current_user (JWT), require_admin
    ├── schemas.py        # model request/response
    ├── core/
    │   ├── config.py     # baca .env (Supabase, JWT, DATA_DIR, CORS)
    │   └── security.py   # verify_password + JWT
    ├── services/
    │   ├── supabase_client.py  # REST ke tabel users
    │   ├── auth.py             # authenticate()
    │   └── part_index.py       # index Excel + parse stok/harga + search (logika inti)
    └── routers/
        ├── auth.py       # POST /api/auth/login, GET /api/auth/me
        └── parts.py      # GET /api/parts/search, /index/status, POST /index/refresh
```

## Setup

```bash
cd backend
pip install -r requirements.txt
cp .env.example .env        # Windows: copy .env.example .env
# isi SUPABASE_URL, SUPABASE_KEY, JWT_SECRET. DATA_DIR default = ../data
```

> Nilai Supabase sama persis dengan blok `[supabase]` di `.streamlit/secrets.toml`.

## Menjalankan

```bash
cd backend
uvicorn app.main:app --reload
```

- API: <http://127.0.0.1:8000>
- Dokumentasi interaktif (Swagger): <http://127.0.0.1:8000/docs>
- Health: <http://127.0.0.1:8000/health>

## Endpoint

| Method | Path | Auth | Keterangan |
|---|---|---|---|
| GET | `/health` | — | status server + apakah Supabase terkonfigurasi |
| POST | `/api/auth/login` | — | `{username, password}` → `{access_token, user}` |
| GET | `/api/auth/me` | Bearer | info user dari token |
| GET | `/api/parts/search?q=` | Bearer | cari part number (substring, uppercase) |
| GET | `/api/parts/index/status` | Bearer | statistik index |
| POST | `/api/parts/index/refresh` | Bearer (admin) | bangun ulang index |

Contoh:

```bash
TOKEN=$(curl -s -X POST localhost:8000/api/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"xxxx"}' | python -c "import sys,json;print(json.load(sys.stdin)['access_token'])")

curl -s "localhost:8000/api/parts/search?q=WG16" -H "Authorization: Bearer $TOKEN"
```

## Self-test (tanpa server / tanpa network)

Menguji logika index + search langsung pada file di `../data`:

```bash
cd backend
python selftest.py            # atau: python selftest.py WG16
```

## Catatan migrasi

- Logika di `services/part_index.py` & `core/security.py` **disalin/diekstrak** dari
  `app.py` / `supabase.py` tanpa `st.*`, sehingga nanti **Streamlit pun bisa
  memanggil service yang sama** (sumber kebenaran tunggal, tanpa duplikasi).
- Folder `data/stok`, `data/harga`, `data/populasi` **dikecualikan** dari index
  part (di-load terpisah sebagai lookup), berbeda dari walk lama yang mengindeks
  semua — ini menghindari entri sampah pada hasil pencarian.
- Index dibangun di memori (lazy) + bisa di-refresh via endpoint admin. Disk cache
  (pickle) seperti di Streamlit bisa ditambahkan nanti sebagai optimasi.
```
