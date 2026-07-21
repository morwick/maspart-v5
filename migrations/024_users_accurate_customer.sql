-- ============================================================
-- 024_users_accurate_customer.sql — Tautan akun → pelanggan Accurate
-- ============================================================
-- Penawaran Accurate otomatis (saat order lunas) dulu mencocokkan CUSTOMER dari
-- `orders.recipient_name` — teks BEBAS yang diketik pembeli di form pengiriman.
-- Akun yang sama bisa menulis 'rizki' hari ini dan 'PT ARGCIO JAYA ABADI' besok,
-- sehingga penawaran kadang jadi, kadang di-skip diam-diam ('tak ditemukan' /
-- 'ambigu'), dan bisa juga menempel ke customer yang SALAH bila namanya mirip.
--
-- Kolom di bawah mengikat AKUN ke satu pelanggan Accurate, ditetapkan admin
-- SEKALI lewat menu "Pelanggan Accurate". Penawaran memakai `customer_id`
-- LANGSUNG — tak ada lagi pencarian nama saat checkout, jadi tak ada lagi
-- 'ambigu' dan tetap benar walau nama pelanggan diubah di Accurate.
--
--   accurate_customer_id   : id numerik pelanggan di Accurate (SUMBER KEBENARAN)
--   accurate_customer_name : nama saat ditautkan — HANYA untuk tampilan/audit,
--                            ⛔ jangan dipakai mencocokkan (bisa basi).
--   accurate_customer_no   : nomor pelanggan Accurate, untuk ditampilkan admin.
--
-- Tanpa migrasi ini aplikasi TETAP jalan: penautan gagal disimpan (kolom tak
-- ada) dan penawaran otomatis di-skip dengan alasan jelas — pesanan & pembayaran
-- tidak terganggu sama sekali. Aman dijalankan ulang (idempotent).
-- ============================================================

alter table users add column if not exists accurate_customer_id   bigint;
alter table users add column if not exists accurate_customer_name text;
alter table users add column if not exists accurate_customer_no   text;

-- Satu pelanggan Accurate boleh dipakai >1 akun (mis. beberapa staf satu
-- perusahaan), jadi SENGAJA tanpa unique constraint. Indeks hanya untuk
-- menjawab "akun mana saja yang tertaut ke pelanggan X".
create index if not exists idx_users_accurate_customer
  on users (accurate_customer_id)
  where accurate_customer_id is not null;
