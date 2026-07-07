-- 016_ai_chat_log.sql
-- Observabilitas Asisten AI: satu baris ringkas per GILIRAN chat untuk mengukur
-- kualitas & performa nyata (tool dipakai, ronde, latensi, guard, tool gagal,
-- outcome). Metadata saja — bukan isi rahasia. Ditriase admin di /admin/chat-log.
-- DDL identik dengan backend/app/services/ai_chat_log.py::create_table_sql().

create table if not exists ai_chat_log (
  id bigint generated always as identity primary key,
  created_at timestamptz not null default now(),
  username text,
  role text,
  question text,
  tools text,
  tools_count int not null default 0,
  rounds int not null default 0,
  latency_ms int not null default 0,
  guard_hit boolean not null default false,
  tool_failed boolean not null default false,
  reply_len int not null default 0,
  outcome text          -- ok | not_found | empty | sanitized
);

create index if not exists ai_chat_log_created_idx on ai_chat_log (created_at desc);
