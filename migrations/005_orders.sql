-- ============================================================
-- 005_orders.sql — Pesanan internal per cabang (keranjang → order)
-- ============================================================
-- Fitur penjualan/order internal: akun cabang membuat pesanan dari
-- keranjang, bayar transfer manual + upload bukti, admin verifikasi
-- & ubah status. Harga diambil dari harga.xlsx (dihitung di backend).
--
-- Jalankan di Supabase → SQL Editor (sekali). Backend pakai service_key,
-- jadi RLS dimatikan (akses dikontrol di API: cabang lihat order sendiri,
-- admin lihat semua).
--
-- Status alur: menunggu_pembayaran → menunggu_verifikasi → diproses
--              → dikirim → selesai | batal
-- ============================================================

create table if not exists orders (
  id           uuid primary key default gen_random_uuid(),
  order_code   text unique not null,                      -- mis. PO-7K3QF2
  username     text not null,                             -- pemesan (akun cabang)
  gudang       text,                                      -- cabang (mis. Jakarta)
  note         text,
  subtotal     bigint not null default 0,
  shipping_cost bigint not null default 0,                -- ongkir
  total        bigint not null default 0,                 -- subtotal + ongkir
  courier         text,                                   -- mis. jne
  courier_service text,                                   -- mis. REG
  weight_grams    int not null default 0,                 -- berat kiriman (gram)
  status       text not null default 'menunggu_pembayaran',
  payment_proof_url text,                                 -- URL bukti transfer
  created_at   timestamptz default now(),
  updated_at   timestamptz default now()
);
create index if not exists idx_orders_user   on orders (username);
create index if not exists idx_orders_status on orders (status);

create table if not exists order_items (
  id          uuid primary key default gen_random_uuid(),
  order_id    uuid not null references orders(id) on delete cascade,
  part_number text not null,
  name        text,
  price       bigint not null default 0,                  -- harga satuan saat order (snapshot)
  qty         int    not null default 1,
  line_total  bigint not null default 0
);
create index if not exists idx_order_items_order on order_items (order_id);

-- Backend memakai service_key → matikan RLS (akses dikontrol di API layer).
alter table orders       disable row level security;
alter table order_items  disable row level security;
