-- 009_buyer_gudang.sql
-- Akun "pembeli" memilih lokasi (gudang) sendiri sebelum bisa membeli.
-- Kolom `gudang` menyimpan KEY gudang terpilih (mis. 'jakarta'), bukan label.
-- NULL = belum memilih lokasi.

ALTER TABLE users ADD COLUMN IF NOT EXISTS gudang text;

COMMENT ON COLUMN users.gudang IS
  'Key lokasi gudang terpilih untuk akun pembeli (lihat BUYER_LOCATIONS di backend/app/services/gudang.py). NULL = belum dipilih.';
