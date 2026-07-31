-- ============================================================
-- 027_users_gudang_kelola.sql — Gudang yang boleh DIKELOLA sebuah akun
-- ============================================================
-- Fitur "Rak & Kartu Stok": staf gudang mengisi kode rak + foto kartu stok per
-- (part × gudang). MELIHAT terbuka untuk semua staf internal; MENULIS harus
-- dipagari — orang Jakarta tak boleh mengubah rak Balikpapan.
--
-- Sengaja TANPA role baru: 88 pemeriksaan role di kode tak disentuh sama
-- sekali. Kewenangan tulis = isi kolom ini. Terisi = "admin gudang" untuk
-- gudang-gudang tersebut; NULL/kosong = staf biasa (menu Rak tak muncul,
-- semua endpoint tulis 403).
--
-- ⚠️ JANGAN dikira sama dengan `users.gudang` (migrasi 009). Itu KEY lokasi
-- pilihan PEMBELI ('jakarta', huruf kecil, satu nilai) dan dipakai jalur
-- belanja. Kolom di bawah adalah LABEL PENUH gudang dan boleh BANYAK —
-- Jakarta saja punya 4 gudang ('01.Jakarta', '06.B80 H1', ...).
--
-- Format: label penuh dipisah koma-spasi, mis.
--   '01.Jakarta, 06.B80 H1'
-- Label WAJIB persis seperti `warehouseName` Accurate ('NN.Nama') karena
-- pencocokan gudang di seluruh aplikasi (accurate.gudang_breakdown, per_gudang,
-- gudang_config) memakai nama itu apa adanya. `locName` versi pendek hanya
-- untuk tampilan pembeli — ⛔ jangan disimpan di sini.
--
-- Tanpa migrasi ini aplikasi TETAP jalan: pembacaan kolom kena 42703 dan
-- backend memperlakukannya sebagai "tak ada yang punya gudang kelola" (fitur
-- dorman) — tidak ada error yang menjatuhkan login/permissions.
-- Aman dijalankan ulang (idempotent).
-- ============================================================

alter table users add column if not exists gudang_kelola text;

comment on column users.gudang_kelola is
  'Label PENUH gudang yang boleh ditulis akun ini (Rak & Kartu Stok), dipisah koma-spasi, mis. "01.Jakarta, 06.B80 H1". NULL/kosong = bukan pengelola gudang. BUKAN users.gudang (itu key lokasi pembeli).';
