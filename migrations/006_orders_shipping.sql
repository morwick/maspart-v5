-- ============================================================
-- 006_orders_shipping.sql — Kolom ekspedisi & ongkir untuk orders
-- ============================================================
-- Jalankan HANYA jika tabel `orders` sudah dibuat dari 005 versi LAMA
-- (tanpa kolom ongkir). Aman dijalankan berulang (IF NOT EXISTS).
-- Kalau 005 yang dijalankan sudah versi terbaru (sudah ada kolom ini),
-- migrasi ini tidak mengubah apa-apa.
-- ============================================================

alter table orders add column if not exists shipping_cost   bigint not null default 0;
alter table orders add column if not exists courier         text;
alter table orders add column if not exists courier_service text;
alter table orders add column if not exists weight_grams    int not null default 0;
