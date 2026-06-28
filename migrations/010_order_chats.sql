-- 010_order_chats.sql
-- Chat per pesanan antara pembeli dan gudang/cabang pengirim.
-- sender_role: 'pembeli' | 'gudang' | 'admin'.

create table if not exists order_chats (
  id uuid primary key default gen_random_uuid(),
  order_code text not null,
  sender_username text not null,
  sender_role text not null,
  body text not null,
  created_at timestamptz not null default now()
);

create index if not exists order_chats_code_idx on order_chats (order_code, created_at);
