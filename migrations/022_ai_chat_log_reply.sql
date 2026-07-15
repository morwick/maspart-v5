-- 022_ai_chat_log_reply.sql
-- Observabilitas AI: simpan TEKS JAWABAN AI per giliran chat (untuk monitoring —
-- pemilik bisa membaca apa yang dijawab asisten, klik baris → expand di halaman
-- /admin/chat-log). Sebelumnya hanya panjang jawaban (reply_len) yang disimpan.
--   reply : teks jawaban final asisten (di-cap ~4000 char di aplikasi)
-- Halaman & endpoint admin-only (require_admin); retensi 30 hari sudah berjalan.
-- Jalankan sekali di Supabase SQL Editor. Kolom lama tak berubah. Baris LAMA
-- ber-reply NULL (jawaban tak tersimpan sebelum migrasi ini).

alter table ai_chat_log add column if not exists reply text;
