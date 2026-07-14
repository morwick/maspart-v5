-- 021_ai_chat_log_tokens.sql
-- Observabilitas AI: catat BIAYA TOKEN DeepSeek per giliran chat.
-- Satu giliran = beberapa panggilan API (ronde tool + retry + jawaban final);
-- angka di sini = JUMLAH seluruh panggilan giliran itu, dari field `usage`
-- respons DeepSeek (gratis, tak perlu panggilan tambahan).
--   tokens_in        : prompt_tokens total (input yang ditagih)
--   tokens_out       : completion_tokens total (output — tarif termahal)
--   tokens_cache_hit : bagian tokens_in yang kena prompt-cache (≈1/10 harga)
--   api_calls        : berapa kali DeepSeek dipanggil dalam giliran itu
-- Jalankan sekali di Supabase SQL Editor. Kolom lama tak berubah.

alter table ai_chat_log add column if not exists tokens_in        int not null default 0;
alter table ai_chat_log add column if not exists tokens_out       int not null default 0;
alter table ai_chat_log add column if not exists tokens_cache_hit int not null default 0;
alter table ai_chat_log add column if not exists api_calls        int not null default 0;
