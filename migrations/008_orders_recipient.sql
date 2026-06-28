-- ============================================================
-- 008_orders_recipient.sql — Alamat penerima untuk pengiriman
-- ============================================================
-- Menyimpan tujuan kirim per pesanan (nama, HP, alamat lengkap, kode pos).
-- Kode pos dipakai untuk hitung ongkir (RajaOngkir). Aman dijalankan ulang.
-- ============================================================

alter table orders add column if not exists recipient_name    text;
alter table orders add column if not exists recipient_phone   text;
alter table orders add column if not exists recipient_address  text;
alter table orders add column if not exists recipient_postal   text;
