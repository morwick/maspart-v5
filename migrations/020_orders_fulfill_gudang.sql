-- ============================================================
-- 020_orders_fulfill_gudang.sql — Gudang FISIK pengirim pesanan
-- ============================================================
-- Kolom `gudang` yang sudah ada berisi CABANG PEMROSES (sub-gudang '06.B80 H1'
-- dikelola cabang Jakarta → tertulis 'Jakarta'). Kolom baru ini mencatat gudang
-- fisik tempat barang benar-benar diambil, yang juga menjadi titik ASAL ongkir.
-- Ditampilkan ke pembeli ("Dikirim dari Gudang X") & di detail order admin.
--
-- Tanpa migrasi ini order TETAP dibuat normal (patch-nya best-effort); hanya
-- keterangan gudang pengirim yang belum muncul. Aman dijalankan ulang.
-- ============================================================

alter table orders add column if not exists fulfill_gudang text;
