-- 013_orders_tracking.sql
-- Nomor resi pengiriman per pesanan (diisi cabang saat menandai "Dikirim").

ALTER TABLE orders ADD COLUMN IF NOT EXISTS tracking_no text;
