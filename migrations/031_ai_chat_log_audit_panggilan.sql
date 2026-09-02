-- 031_ai_chat_log_audit_panggilan.sql
-- TELEMETRI PER PANGGILAN untuk audit asisten (2026-09-02).
--
-- Kenapa perlu (audit 841 giliran, 3 Agu–2 Sep 2026):
--   * Angka token per GILIRAN menyembunyikan panggilan mana yang cache-miss.
--     Akar cache-miss 29 rb token/giliran (pesan system dinamis diangkat DeepSeek
--     ke puncak prompt) baru ketahuan dari giliran 1-panggilan yang kebetulan
--     ada — dengan rincian per panggilan, pola itu terbaca dalam sehari.
--   * Output p50 1.354 token/giliran, ±86% di antaranya blok nalar [PIKIR] +
--     JSON tool (bukan jawaban yang dilihat user). Diet nalar di ronde
--     pemanggilan tool (prompt bentuk A, maks 3 baris) hanya bisa diukur bila
--     panjang nalar per giliran tercatat.
--   * 253 dari 469 follow-up memanggil ulang tool giliran sebelumnya, tapi hanya
--     NAMA tool yang tercatat: tak bisa dibedakan panggilan identik (kandidat
--     cache lintas giliran) dari panggilan dengan argumen baru.
--
--   pikir_chars  : total char blok [PIKIR]…[/PIKIR] seluruh panggilan giliran ini.
--   calls_detail : 'prompt_tokens/cache_hit' tiap panggilan API, urut, dipisah ';'
--                  (mis. '47578/45120;52310/50944').
--   tools_args   : 'nama#digest8' sejajar kolom `tools` — sidik jari argumen
--                  pilihan model (SHA-1 JSON terurut, 8 hex), bukan argumennya.
--
-- Jalankan sekali di Supabase SQL Editor. Kolom lama tak berubah. Sebelum
-- dijalankan, backend otomatis turun ke tingkat 030 (kolom ini tak terisi).

alter table ai_chat_log add column if not exists pikir_chars int not null default 0;
alter table ai_chat_log add column if not exists calls_detail text;
alter table ai_chat_log add column if not exists tools_args text;
