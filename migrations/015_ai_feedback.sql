-- 015_ai_feedback.sql
-- Umpan balik 👍/👎 Asisten AI (§3.5.5g). Jawaban yang di-👎 (beserta pertanyaan,
-- tool yang dipakai, dan catatan user) tersimpan sebagai ANTREAN PERBAIKAN untuk
-- ditriase admin di /admin/feedback. DDL identik dengan
-- backend/app/services/ai_feedback.py::create_table_sql() (satu sumber kebenaran).

create table if not exists ai_feedback (
  id bigint generated always as identity primary key,
  created_at timestamptz not null default now(),
  username text,
  role text,
  rating text not null check (rating in ('up','down')),
  question text,
  answer text,
  tools text,
  note text,
  context jsonb,
  resolved boolean not null default false
);

create index if not exists ai_feedback_created_idx on ai_feedback (created_at desc);
create index if not exists ai_feedback_rating_idx on ai_feedback (rating);
