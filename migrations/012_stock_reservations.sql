-- 012_stock_reservations.sql
-- Ledger reservasi stok (anti-oversell). Stok tersedia = stok Excel − reservasi aktif.
-- Order baru menambah reservasi; pesanan batal melepasnya; upload stok baru mereset.

create table if not exists stock_reservations (
  id uuid primary key default gen_random_uuid(),
  order_code text not null,
  part_number text not null,
  gudang_label text not null,
  qty int not null,
  active boolean not null default true,
  created_at timestamptz not null default now()
);

create index if not exists stock_res_part_idx on stock_reservations (part_number, gudang_label);
create index if not exists stock_res_order_idx on stock_reservations (order_code);
