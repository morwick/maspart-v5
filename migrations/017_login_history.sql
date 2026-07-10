-- 017_login_history.sql
-- Riwayat login: satu baris per login (kapan, siapa, IP, user-agent).
-- Dipakai panel /admin/monitoring untuk mendeteksi AKUN DIPAKAI RAMAI-RAMAI:
-- satu akun yang login dari banyak IP / banyak perangkat dalam 30 hari terakhir.
--
-- ⚠️ TABEL INI SUDAH ADA di Supabase produksi (warisan aplikasi Streamlit lama).
-- Skema di bawah SENGAJA menyalin skema lama itu, supaya `create table if not
-- exists` aman dijalankan: pada DB lama ia tidak melakukan apa-apa, pada DB baru
-- ia membuat tabel yang identik. JANGAN mengubah nama kolom di sini tanpa juga
-- mengubah backend/app/services/login_history.py.
--
-- Catatan kolom:
--   • `device` TIDAK disimpan — diturunkan dari `user_agent` saat dibaca
--     (login_history.device_label), jadi tak ada data ganda yang bisa basi.
--   • `role` TIDAK disimpan — peran user diambil dari roster `users`.
--   • `success` = login berhasil? Ringkasan "dipakai ramai" hanya menghitung
--     login BERHASIL; percobaan gagal bukan bukti pemakaian.
--
-- PRIVASI: IP & user-agent = data pribadi. Hanya admin yang bisa melihat lewat
-- endpoint /api/admin/monitoring (require_admin). Retensi 90 hari (purge_old()).

create table if not exists login_history (
  id bigint generated always as identity primary key,
  created_at timestamptz not null default now(),
  username text not null,
  success boolean not null default true,
  reason text,
  ip_address text,
  user_agent text
);

create index if not exists login_history_created_idx on login_history (created_at desc);
create index if not exists login_history_user_idx on login_history (username, created_at desc);
