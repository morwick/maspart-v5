-- ============================================================
-- 019_orders_payment_note.sql — Catatan pembayaran bermasalah
-- ============================================================
-- Dipakai saat pembayaran MASUK di gateway untuk order yang sudah BATAL (mis.
-- pembeli membayar tepat sebelum batas 24 jam, sementara order keburu auto-batal).
-- Order tidak dihidupkan kembali (stok sudah dilepas), tapi kejadiannya dicatat di
-- sini + log peringatan supaya admin bisa refund / konfirmasi ke pembeli.
--
-- Tanpa migrasi ini fitur TETAP jalan: kejadian tercatat di log backend, hanya
-- penandanya yang tak muncul di halaman detail order admin.
-- Aman dijalankan ulang.
-- ============================================================

alter table orders add column if not exists payment_note text;
