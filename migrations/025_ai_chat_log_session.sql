-- 025_ai_chat_log_session.sql
-- Observabilitas AI: kaitkan giliran-giliran yang berasal dari SATU percakapan.
-- Tanpa ini tiap baris ai_chat_log berdiri sendiri, sehingga kelas bug terbesar
-- asisten — KEGAGALAN FOLLOW-UP ("harganya berapa?" setelah BOM per-VIN, "kalau
-- yang belakang?" setelah tabel armada) — tidak bisa direkonstruksi sama sekali:
-- yang terlihat hanya satu pertanyaan pendek tanpa konteks, dan penyebabnya
-- (rangka aktif tertukar, PN kehilangan grounding) mustahil dilacak.
--   session_id : id percakapan dari klien (conversation_id), NULL utk klien lama
-- Jalankan sekali di Supabase SQL Editor. Kolom lama tak berubah. Baris LAMA NULL.
-- Kode tetap jalan bila migrasi ini BELUM dijalankan (log_turn turun tingkat).

alter table ai_chat_log add column if not exists session_id text;

create index if not exists ai_chat_log_session_idx on ai_chat_log (session_id);
