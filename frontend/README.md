# MASPART Frontend — Next.js (Fase 2)

Frontend Next.js (App Router + TypeScript + Tailwind v4) untuk MASPART, menembak
backend FastAPI (`../backend`). Fase 2 ini membuktikan jalur penuh
**Next.js → FastAPI**: login (JWT) + Search Part Number.

## Setup

```bash
cd frontend
npm install
cp .env.local.example .env.local   # Windows: copy .env.local.example .env.local
# set NEXT_PUBLIC_API_BASE (default http://127.0.0.1:8000)
```

## Menjalankan (dev)

Pastikan backend FastAPI jalan dulu (lihat `../backend/README.md`):

```bash
# terminal 1 — backend
cd ../backend && uvicorn app.main:app --reload

# terminal 2 — frontend
cd frontend && npm run dev
```

Buka <http://localhost:3000> → diarahkan ke `/login`. Setelah login, ke `/search`.

> Login butuh Supabase terkonfigurasi di backend (`backend/.env`). Tanpa itu,
> backend mengembalikan 401 untuk semua login.

## Struktur

```
src/
├── app/
│   ├── layout.tsx        # layout root (tanpa Google Font → build offline-friendly)
│   ├── page.tsx          # redirect ke /search atau /login berdasarkan token
│   ├── login/page.tsx    # form login → POST /api/auth/login → simpan JWT
│   └── search/page.tsx   # guard token + GET /api/parts/search + tabel hasil
└── lib/
    ├── api.ts            # klien fetch (login, searchParts, getMe) + tipe
    └── auth.ts           # token + user di localStorage
```

## Build produksi

```bash
npm run build && npm run start
```

## Catatan

- Token JWT disimpan di `localStorage` (sederhana untuk Fase 2). Untuk produksi,
  pertimbangkan cookie httpOnly via route handler agar lebih aman dari XSS.
- Saat token kedaluwarsa, request search yang kena 401 otomatis logout → /login.
- Deploy frontend nanti ke Vercel; backend FastAPI di host terpisah (mis. Railway/
  Fly/VPS), set `NEXT_PUBLIC_API_BASE` ke URL backend produksi + CORS di backend.
