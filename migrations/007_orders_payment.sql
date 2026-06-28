-- ============================================================
-- 007_orders_payment.sql — Pembayaran otomatis (Payment API Komerce)
-- ============================================================
-- Menambah kolom untuk pembayaran via gateway (Virtual Account / QRIS)
-- di samping transfer manual yang sudah ada. Aman dijalankan berulang.
--
-- payment_method  : 'manual' (transfer + bukti) | 'gateway' (VA/QRIS otomatis)
-- payment_ref     : ID transaksi dari Komerce (untuk cek status & webhook)
-- payment_channel : kurir/bank/metode mis. 'qris', 'va_bca', 'va_bni'
-- payment_va      : nomor Virtual Account (kalau VA)
-- payment_qr      : string/URL QRIS (kalau QRIS)
-- payment_url     : halaman bayar (kalau gateway memberi redirect URL)
-- payment_expiry  : batas waktu bayar
-- paid_at         : waktu pembayaran terkonfirmasi
-- payment_raw     : payload mentah terakhir dari gateway (audit/debug)
-- ============================================================

alter table orders add column if not exists payment_method  text not null default 'manual';
alter table orders add column if not exists payment_ref      text;
alter table orders add column if not exists payment_channel  text;
alter table orders add column if not exists payment_va       text;
alter table orders add column if not exists payment_qr       text;
alter table orders add column if not exists payment_url      text;
alter table orders add column if not exists payment_expiry   timestamptz;
alter table orders add column if not exists paid_at          timestamptz;
alter table orders add column if not exists payment_raw      jsonb;

create index if not exists idx_orders_payment_ref on orders (payment_ref);
