"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import AppShell from "@/components/AppShell";
import Markdown from "@/components/Markdown";
import {
  ApiError,
  aiChat,
  aiChatImage,
  downloadBlob,
  exportRepairKit,
  getAiStatus,
  type AIChatTurn,
} from "@/lib/api";
import { clearSession, getToken } from "@/lib/auth";

type Msg = AIChatTurn & { tools?: string[]; photo?: string; repairkitModels?: string[] };

const SUGGESTIONS = [
  "Cek stok part WG9925520270",
  "Cari part rem depan",
  "Repair kit transmisi HW19709XST",
  "Gudang apa saja yang tersedia?",
];

// Kunci penyimpanan chat agar tidak hilang saat pindah menu lalu kembali.
// sessionStorage = bertahan selama tab browser terbuka (termasuk navigasi
// antar-menu & refresh), otomatis bersih saat tab ditutup.
const CHAT_KEY = "maspart_asisten_chat";

export default function AsistenPage() {
  const router = useRouter();
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [available, setAvailable] = useState<boolean | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const taRef = useRef<HTMLTextAreaElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const firstSave = useRef(true);

  useEffect(() => {
    const token = getToken();
    if (!token) return router.replace("/login");
    getAiStatus(token)
      .then((s) => setAvailable(s.available))
      .catch((err) => {
        if (err instanceof ApiError && err.status === 401) {
          clearSession();
          router.replace("/login");
          return;
        }
        setAvailable(false);
      });
  }, [router]);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [msgs, busy]);

  // Muat chat tersimpan saat halaman dibuka kembali (mis. balik dari menu lain).
  useEffect(() => {
    try {
      const raw = sessionStorage.getItem(CHAT_KEY);
      if (raw) {
        const saved = JSON.parse(raw);
        if (Array.isArray(saved) && saved.length) setMsgs(saved);
      }
    } catch {
      /* abaikan storage rusak */
    }
  }, []);

  // Simpan chat tiap berubah. Lewati simpan pertama (saat mount) agar tidak
  // menimpa data sebelum sempat dimuat. Batasi 60 pesan terakhir biar ringan.
  useEffect(() => {
    if (firstSave.current) {
      firstSave.current = false;
      return;
    }
    try {
      if (msgs.length) sessionStorage.setItem(CHAT_KEY, JSON.stringify(msgs.slice(-60)));
      else sessionStorage.removeItem(CHAT_KEY);
    } catch {
      /* storage penuh/diblokir — abaikan */
    }
  }, [msgs]);

  async function send(text: string) {
    const body = text.trim();
    if (!body || busy) return;
    const token = getToken();
    if (!token) return router.replace("/login");

    setError(null);
    const next: Msg[] = [...msgs, { role: "user", content: body }];
    setMsgs(next);
    setInput("");
    setBusy(true);
    try {
      const payload: AIChatTurn[] = next.map((m) => ({ role: m.role, content: m.content }));
      const res = await aiChat(token, payload);
      setMsgs((m) => [
        ...m,
        {
          role: "assistant",
          content: res.reply || "(tidak ada jawaban)",
          tools: res.tools_used,
          repairkitModels: res.repairkit_models,
        },
      ]);
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        clearSession();
        return router.replace("/login");
      }
      const msg = err instanceof Error ? err.message : "Gagal menghubungi asisten.";
      setError(msg);
      // Kembalikan input agar bisa coba lagi.
      setInput(body);
      setMsgs((m) => m.slice(0, -1));
    } finally {
      setBusy(false);
      taRef.current?.focus();
    }
  }

  async function sendWithPhoto(file: File) {
    if (busy) return;
    const token = getToken();
    if (!token) return router.replace("/login");
    if (!file.type.startsWith("image/")) {
      setError("File harus berupa gambar.");
      return;
    }
    setError(null);
    const caption = input.trim();
    const userText =
      caption || "📷 Tolong kenali part di foto ini (stok, harga, dipakai di unit apa).";
    const preview = URL.createObjectURL(file);
    const next: Msg[] = [...msgs, { role: "user", content: userText, photo: preview }];
    setMsgs(next);
    setInput("");
    setBusy(true);
    try {
      const payload: AIChatTurn[] = next.map((m) => ({ role: m.role, content: m.content }));
      const res = await aiChatImage(token, payload, file);
      setMsgs((m) => [
        ...m,
        {
          role: "assistant",
          content: res.reply || "(tidak ada jawaban)",
          tools: res.tools_used,
          repairkitModels: res.repairkit_models,
        },
      ]);
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        clearSession();
        return router.replace("/login");
      }
      setError(err instanceof Error ? err.message : "Gagal mengirim foto.");
      setMsgs((m) => m.slice(0, -1));
    } finally {
      setBusy(false);
      if (fileRef.current) fileRef.current.value = "";
      taRef.current?.focus();
    }
  }

  function clearChat() {
    setMsgs([]);
    setError(null);
    try {
      sessionStorage.removeItem(CHAT_KEY);
    } catch {
      /* abaikan */
    }
  }

  function onKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send(input);
    }
  }

  return (
    <AppShell active="/asisten" title="Asisten AI" sub="Tanya apa saja tentang part, stok, harga, dan pesanan">
      <div className="mx-auto flex h-full w-full max-w-3xl flex-col px-4 py-4 sm:px-6">
        {available === false && (
          <div className="alert alert-error" style={{ marginBottom: 12 }}>
            Asisten AI belum aktif. Set <code>DEEPSEEK_API_KEY</code> di <code>backend/.env</code> lalu restart backend.
          </div>
        )}

        {msgs.length > 0 && (
          <div style={{ display: "flex", justifyContent: "flex-end", marginBottom: 8 }}>
            <button
              className="btn btn-secondary btn-sm"
              onClick={clearChat}
              disabled={busy}
              style={{ fontSize: 12 }}
              title="Mulai percakapan baru"
            >
              🗑 Hapus chat
            </button>
          </div>
        )}

        {/* Area pesan */}
        <div
          ref={scrollRef}
          className="surface flex-1 overflow-auto"
          style={{ padding: 16, display: "flex", flexDirection: "column", gap: 12, minHeight: 320 }}
        >
          {msgs.length === 0 ? (
            <div className="grid flex-1 place-items-center" style={{ color: "var(--ink-500)", textAlign: "center" }}>
              <div style={{ display: "grid", gap: 14, maxWidth: 460 }}>
                <div style={{ fontSize: 30 }}>🤖</div>
                <div style={{ fontWeight: 600, color: "var(--ink-700)" }}>Halo! Saya Asisten MASPART.</div>
                <div style={{ fontSize: 13 }}>
                  Saya bisa membantu cek stok per gudang, harga (lokal & SIMS), mencari part,
                  dan menjawab soal pesanan/penjualan. Anda juga bisa kirim <b>foto part</b> (ikon 📷)
                  untuk dikenali. Coba salah satu:
                </div>
                <div style={{ display: "flex", flexWrap: "wrap", gap: 8, justifyContent: "center" }}>
                  {SUGGESTIONS.map((s) => (
                    <button
                      key={s}
                      className="btn btn-secondary btn-sm"
                      onClick={() => send(s)}
                      disabled={busy || available === false}
                      style={{ fontSize: 12 }}
                    >
                      {s}
                    </button>
                  ))}
                </div>
              </div>
            </div>
          ) : (
            msgs.map((m, i) => <Bubble key={i} m={m} />)
          )}
          {busy && (
            <div style={{ alignSelf: "flex-start", color: "var(--ink-500)", fontSize: 13, padding: "4px 2px" }}>
              <span className="dot-pulse">Asisten mengetik…</span>
            </div>
          )}
        </div>

        {error && <div className="alert alert-error" style={{ marginTop: 10 }}>{error}</div>}

        {/* Input */}
        <div className="surface" style={{ marginTop: 10, padding: 8, display: "flex", gap: 8, alignItems: "flex-end" }}>
          <input
            ref={fileRef}
            type="file"
            accept="image/*"
            style={{ display: "none" }}
            onChange={(e) => {
              const f = e.target.files?.[0];
              if (f) sendWithPhoto(f);
            }}
          />
          <button
            className="btn btn-secondary"
            title="Cari part dari foto"
            onClick={() => fileRef.current?.click()}
            disabled={busy || available === false}
            style={{ padding: "0 12px", fontSize: 18 }}
          >
            📷
          </button>
          <textarea
            ref={taRef}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={onKeyDown}
            placeholder={available === false ? "Asisten belum aktif…" : "Tulis pertanyaan… (Enter kirim, Shift+Enter baris baru)"}
            disabled={busy || available === false}
            rows={1}
            style={{
              flex: 1, resize: "none", border: "none", outline: "none", background: "transparent",
              fontSize: 14, padding: "8px 10px", maxHeight: 140, lineHeight: 1.4, color: "var(--ink-800)",
            }}
          />
          <button
            className="btn btn-primary"
            onClick={() => send(input)}
            disabled={busy || !input.trim() || available === false}
          >
            Kirim
          </button>
        </div>
      </div>
    </AppShell>
  );
}

function RepairKitDownloads({ models }: { models: string[] }) {
  const [dl, setDl] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const uniq = Array.from(new Set(models)).filter(Boolean);
  if (uniq.length === 0) return null;

  async function download(model: string) {
    const token = getToken();
    if (!token) return;
    setDl(model);
    setErr(null);
    try {
      const blob = await exportRepairKit(token, model);
      downloadBlob(blob, `repairkit_transmisi_${model}.xlsx`);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Gagal mengunduh Excel");
    } finally {
      setDl(null);
    }
  }

  return (
    <div style={{ marginTop: 8, display: "flex", flexWrap: "wrap", gap: 6, alignItems: "center" }}>
      {uniq.map((model) => (
        <button
          key={model}
          className="btn btn-secondary btn-sm"
          onClick={() => download(model)}
          disabled={dl !== null}
          style={{ fontSize: 12 }}
          title={`Unduh Excel repair kit ${model}`}
        >
          {dl === model ? "Menyiapkan…" : `⬇️ Excel ${model}`}
        </button>
      ))}
      {err && <span style={{ fontSize: 11.5, color: "var(--danger, #c0341a)" }}>{err}</span>}
    </div>
  );
}

function Bubble({ m }: { m: Msg }) {
  const isUser = m.role === "user";
  return (
    <div style={{ alignSelf: isUser ? "flex-end" : "flex-start", maxWidth: isUser ? "85%" : "94%" }}>
      <div
        style={{
          padding: "9px 13px",
          borderRadius: 14,
          borderBottomRightRadius: isUser ? 4 : 14,
          borderBottomLeftRadius: isUser ? 14 : 4,
          background: isUser ? "var(--brand-600)" : "var(--ink-50)",
          color: isUser ? "#fff" : "var(--ink-800)",
          fontSize: 14,
          lineHeight: 1.5,
          whiteSpace: isUser ? "pre-wrap" : "normal",
          wordBreak: "break-word",
          border: isUser ? "none" : "1px solid var(--ink-150)",
        }}
      >
        {isUser ? (
          <>
            {m.photo && (
              // eslint-disable-next-line @next/next/no-img-element
              <img
                src={m.photo}
                alt="foto part"
                onError={(e) => {
                  // URL blob foto bisa mati setelah refresh penuh — sembunyikan
                  // agar tidak tampil ikon gambar rusak.
                  (e.currentTarget as HTMLImageElement).style.display = "none";
                }}
                style={{ maxWidth: 200, borderRadius: 10, marginBottom: m.content ? 8 : 0, display: "block" }}
              />
            )}
            {m.content}
          </>
        ) : (
          <Markdown content={m.content} />
        )}
      </div>
      {!isUser && m.repairkitModels && m.repairkitModels.length > 0 && (
        <RepairKitDownloads models={m.repairkitModels} />
      )}
      {!isUser && m.tools && m.tools.length > 0 && (
        <div style={{ fontSize: 10.5, color: "var(--ink-400)", marginTop: 4, paddingLeft: 4 }}>
          🔧 data: {Array.from(new Set(m.tools)).join(", ")}
        </div>
      )}
    </div>
  );
}
