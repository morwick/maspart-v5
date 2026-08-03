-- 029_ai_chat_log_phase_ms.sql
-- Observabilitas AI: PECAH latensi giliran jadi fase-fasenya.
-- Selama ini hanya `latency_ms` (total) yang tercatat, jadi mustahil tahu
-- giliran 45 detik itu habis di MANA — menunggu model, atau menunggu tool
-- (EPC/SIMS/Accurate). Tanpa angka ini, tuning perilaku cuma tebak-tebakan.
--   model_ms : total ms menunggu panggilan model, dijumlah SELURUH panggilan
--              API giliran itu (ronde tool + retry + jawaban final + salvage)
--   tools_ms : wall-clock total ms eksekusi tool — per BLOK, bukan jumlah
--              per-tool, agar batch paralel tak dihitung berlebih
--              (latency_ms ≈ model_ms + tools_ms + overhead)
--   ttft_ms  : ms sampai potongan jawaban PERTAMA sampai ke klien; 0 = giliran
--              itu tidak streaming (kolom disiapkan sekarang, diisi kemudian)
-- Jalankan sekali di Supabase SQL Editor. Kolom lama tak berubah.

alter table ai_chat_log add column if not exists model_ms int not null default 0;
alter table ai_chat_log add column if not exists tools_ms int not null default 0;
alter table ai_chat_log add column if not exists ttft_ms  int not null default 0;
