-- ============================================================
-- 018_orders_penawaran.sql — Jejak Penawaran Accurate otomatis
-- ============================================================
-- Saat pembayaran order terverifikasi lunas, sistem otomatis membuat Penawaran
-- Penjualan di Accurate (best-effort). Kolom di bawah mencatat hasilnya di order:
--   penawaran_status : 'created' | 'skip' | 'failed'
--   penawaran_number : nomor penawaran Accurate (mis. 'MASPART-07') bila created
--   penawaran_note   : alasan skip / pesan gagal (untuk admin tindak lanjut)
-- Tanpa migrasi ini fitur TETAP membuat penawaran di Accurate, hanya pencatatan
-- nomor di order yang tertunda & idempotensi mengandalkan penjaga dalam-proses.
-- Aman dijalankan ulang.
-- ============================================================

alter table orders add column if not exists penawaran_status  text;
alter table orders add column if not exists penawaran_number  text;
alter table orders add column if not exists penawaran_note     text;
