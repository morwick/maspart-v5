-- 014_orders_tax_and_atomic_reserve.sql
-- 1) Simpan nilai PPN (tax) per order agar total bisa direkonsiliasi.
-- 2) Reservasi stok yang BENAR-BENAR anti-oversell: kolom expires_at + fungsi
--    RPC atomik (all-or-nothing, dikunci per part+gudang) sehingga dua order
--    bersamaan tidak bisa sama-sama lolos melebihi stok.
--
-- Jalankan di Supabase SQL editor (sekali). Aman diulang (idempotent).

-- ── 1. Kolom tax di orders ────────────────────────────────────────────
alter table orders add column if not exists tax int not null default 0;

-- ── 2. Kolom expires_at di reservasi ──────────────────────────────────
-- Reservasi yang sudah lewat expires_at TIDAK dihitung sebagai stok terpakai
-- (otomatis "lepas" tanpa perlu ada yang membuka halaman). Order yang lunas
-- akan di-commit (expires_at = null = permanen) lewat reservations.commit().
alter table stock_reservations add column if not exists expires_at timestamptz;

create index if not exists stock_res_active_idx
  on stock_reservations (part_number, gudang_label) where active;

-- ── 3. Fungsi reservasi atomik (all-or-nothing) ───────────────────────
-- p_items: jsonb array [{part, gudang, qty, stock}, ...]
-- Mengunci tiap (part,gudang) dengan advisory lock dalam satu transaksi,
-- memeriksa (stok - reservasi aktif non-kedaluwarsa) >= qty untuk SEMUA item.
-- Jika satu saja gagal → seluruh transaksi dibatalkan (return false).
create or replace function reserve_order(
  p_order_code text,
  p_items jsonb,
  p_ttl_seconds int default 86400
) returns boolean
language plpgsql
as $$
declare
  it          jsonb;
  v_part      text;
  v_gudang    text;
  v_qty       int;
  v_stock     int;
  v_reserved  int;
begin
  -- Fase 1: kunci + validasi ketersediaan untuk semua item.
  for it in select * from jsonb_array_elements(p_items)
  loop
    v_part   := upper(it->>'part');
    v_gudang := coalesce(it->>'gudang', '');
    v_qty    := coalesce((it->>'qty')::int, 0);
    v_stock  := coalesce((it->>'stock')::int, 0);
    if v_qty <= 0 then
      continue;
    end if;
    -- Kunci serial per (part,gudang) sampai transaksi selesai.
    perform pg_advisory_xact_lock(hashtextextended(v_part || '|' || v_gudang, 0));
    select coalesce(sum(qty), 0) into v_reserved
      from stock_reservations
     where active
       and upper(part_number) = v_part
       and gudang_label = v_gudang
       and (expires_at is null or expires_at > now());
    if v_reserved + v_qty > v_stock then
      return false;  -- rollback otomatis: tidak ada baris yang ter-insert
    end if;
  end loop;

  -- Fase 2: semua lolos → catat reservasi.
  for it in select * from jsonb_array_elements(p_items)
  loop
    v_qty := coalesce((it->>'qty')::int, 0);
    if v_qty <= 0 then
      continue;
    end if;
    insert into stock_reservations (order_code, part_number, gudang_label, qty, active, expires_at)
    values (
      p_order_code,
      upper(it->>'part'),
      coalesce(it->>'gudang', ''),
      v_qty,
      true,
      now() + make_interval(secs => p_ttl_seconds)
    );
  end loop;
  return true;
end;
$$;
