-- 011_gudang_chats.sql
-- Chat pra-pesanan antara pembeli dan gudang/cabang (per pasangan gudang_key + buyer).
-- sender_role: 'pembeli' | 'gudang'.

create table if not exists gudang_chats (
  id uuid primary key default gen_random_uuid(),
  gudang_key text not null,
  buyer_username text not null,
  sender_role text not null,
  sender_username text not null,
  body text not null,
  created_at timestamptz not null default now()
);

create index if not exists gudang_chats_thread_idx on gudang_chats (gudang_key, buyer_username, created_at);
