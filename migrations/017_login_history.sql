-- 017_login_history.sql
-- Riwayat login: satu baris per login berhasil (kapan, siapa, IP, perangkat).
-- Dipakai panel /admin/monitoring untuk mendeteksi AKUN DIPAKAI RAMAI-RAMAI:
-- satu akun yang login dari banyak IP / banyak perangkat dalam 30 hari terakhir.
--
-- Jalankan sekali di Supabase → SQL Editor.
-- DDL identik dengan backend/app/services/login_history.py::create_table_sql().
--
-- PRIVASI: IP & user-agent = data pribadi. Hanya admin yang bisa melihat lewat
-- endpoint /api/admin/monitoring (require_admin). Retensi 90 hari (purge_old()).

create table if not exists login_history (
  id bigint generated always as identity primary key,
  created_at timestamptz not null default now(),
  username text not null,
  role text,
  ip text,
  device text,          -- mis. 'Chrome di Windows' (hasil parsing user_agent)
  user_agent text
);

create index if not exists login_history_created_idx on login_history (created_at desc);
create index if not exists login_history_user_idx on login_history (username, created_at desc);
