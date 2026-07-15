-- 023_ai_chat_log_tools_failed.sql
-- Observabilitas AI: catat TOOL MANA yang gagal per giliran (bukan cuma boolean
-- tool_failed). Memungkinkan panel "Tool paling sering gagal" + rasio gagal/pakai
-- per-tool di /admin/chat-log → perbaikan reliabilitas bertarget (tool EPC/SIMS/
-- Accurate yang bergantung server eksternal paling rawan).
--   tools_failed : daftar nama tool yang GAGAL giliran ini (comma-space, spt `tools`)
-- Jalankan sekali di Supabase SQL Editor. Kolom lama tak berubah. Baris LAMA NULL.

alter table ai_chat_log add column if not exists tools_failed text;
