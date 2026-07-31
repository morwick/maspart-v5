-- ============================================================
-- 028_rak_gudang.sql — Lokasi RAK + foto KARTU STOK per (part × gudang)
-- ============================================================
-- Sistem selama ini tahu BERAPA stoknya (indeks Accurate) tapi tidak tahu DI
-- MANA barangnya, jadi staf tetap keliling gudang mencari. Tabel ini menyimpan
-- kode rak yang diisi manual + foto kartu stok terbaru sebagai bukti visual.
--
-- ⛔ BUKAN sumber kebenaran ANGKA stok. Angka tetap dari Accurate; foto kartu
-- hanya dipandang manusia (tanpa OCR, keputusan pemilik).
--
-- Kunci = (pn_key, gudang), satu baris per pasangan: satu part memang bisa ada
-- di banyak gudang sekaligus. Barang yang terpecah di beberapa rak dalam SATU
-- gudang ditulis apa adanya di kolom teks bebas ('A-12 & C-03') — pemilik
-- sengaja menolak tabel rak terpisah supaya input tetap secepat mengetik.
--
--   pn_key   : PN yang sudah dikanonikkan PEMAAF-SUFFIX (lihat
--              services/rak.pn_key → accurate.index_key). Katalog & EPC memakai
--              PN ber-suffix varian ('WG9525160004/2') sementara Accurate
--              menyimpan PN dasar ('WG9525160004'); tanpa kanonisasi, rak yang
--              sudah diisi tak pernah ketemu lagi saat dicari dari sisi lain.
--   pn_input : PN persis seperti yang DIKETIK/di-upload staf — hanya untuk
--              tampilan & audit. ⛔ jangan dipakai mencocokkan.
--   gudang   : LABEL PENUH gudang ('01.Jakarta'), sama dengan warehouseName
--              Accurate. ⛔ bukan locName pendek milik tampilan pembeli.
--   foto_url : URL publik foto kartu stok TERBARU (tanpa riwayat — keputusan
--              pemilik: yang dibutuhkan cuma yang terakhir).
--   foto_path: path objek di bucket 'part-photos'. Wajib disimpan supaya foto
--              LAMA bisa dihapus saat diganti — kalau tidak, tiap penggantian
--              meninggalkan sampah permanen di Storage.
--
-- Aman dijalankan ulang (idempotent).
-- ============================================================

create table if not exists rak_gudang (
  id         bigint generated always as identity primary key,
  pn_key     text not null,
  pn_input   text,
  gudang     text not null,
  rak        text not null,
  catatan    text,
  foto_url   text,
  foto_path  text,
  updated_by text,
  updated_at timestamptz not null default now(),
  unique (pn_key, gudang)
);

-- Halaman detail part menanyakan "part ini ada di rak mana saja" → filter pn_key.
create index if not exists rak_gudang_pn_idx on rak_gudang (pn_key);
-- Menu Rak & Kartu Stok menanyakan sebaliknya: "isi gudang X" → filter gudang.
create index if not exists rak_gudang_gudang_idx on rak_gudang (gudang);
