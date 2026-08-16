-- 030_ai_chat_log_diulang.sql
-- SINYAL MUTU IMPLISIT: apakah user MENGULANG pertanyaan yang sama di percakapan
-- yang sama.
--
-- Kenapa perlu (audit 1.189 giliran, 17 Jul–16 Agu 2026): tabel `ai_feedback`
-- menerima NOL baris dalam 30 hari. Tombol 👍/👎-nya ada dan berfungsi, tapi
-- praktis tak pernah diklik — jadi tak ada satu pun sinyal kebenaran tentang mutu
-- jawaban. Padahal 26% jawaban (315/1.189) bernada negatif dan tak ada cara
-- membedakan negatif yang BENAR dari negatif PALSU (kelas bug terburuk asisten,
-- lihat 28fe4c0).
--
-- Sinyal ini gratis dan tidak menunggu kemurahan hati user: bila orang mengetik
-- ulang pertanyaan yang sama persis di sesi yang sama, jawaban pertama hampir
-- pasti tidak memuaskan. Di 30 hari itu terjadi 112 kali — sepuluh kali lipat
-- lebih banyak dari jumlah feedback eksplisit yang pernah masuk (nol).
--
--   diulang : true bila pertanyaan (ternormalisasi) SUDAH pernah ditanyakan
--             lebih dulu dalam session_id yang sama.
--
-- ⚠️ Bukan pengganti 👍/👎, melainkan penopangnya: `diulang` menunjuk giliran
-- yang LAYAK DIPERIKSA, bukan memvonis jawabannya salah.
-- Jalankan sekali di Supabase SQL Editor. Kolom lama tak berubah.

alter table ai_chat_log add column if not exists diulang boolean not null default false;

create index if not exists ai_chat_log_diulang_idx on ai_chat_log (diulang)
  where diulang;
