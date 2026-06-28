"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { getOrderChat, sendOrderChat, type ChatMessage } from "@/lib/api";
import { getToken, getUser } from "@/lib/auth";
import { fmtDate } from "@/lib/order-ui";

const roleLabel: Record<string, string> = { pembeli: "Pembeli", gudang: "Gudang", admin: "Admin" };

export default function OrderChat({ code, title }: { code: string; title: string }) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [text, setText] = useState("");
  const [sending, setSending] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const me = (getUser()?.username || "").toLowerCase();
  const listRef = useRef<HTMLDivElement>(null);
  const lastCount = useRef(0);

  const load = useCallback(async () => {
    const token = getToken();
    if (!token) return;
    try {
      const d = await getOrderChat(token, code);
      setMessages(d.messages);
    } catch {
      /* abaikan polling error */
    }
  }, [code]);

  useEffect(() => {
    load();
    const id = setInterval(load, 7000);
    return () => clearInterval(id);
  }, [load]);

  // Auto-scroll ke bawah saat ada pesan baru.
  useEffect(() => {
    if (messages.length !== lastCount.current) {
      lastCount.current = messages.length;
      const el = listRef.current;
      if (el) el.scrollTop = el.scrollHeight;
    }
  }, [messages]);

  async function send() {
    const token = getToken();
    const body = text.trim();
    if (!token || !body) return;
    setSending(true);
    setErr(null);
    try {
      await sendOrderChat(token, code, body);
      setText("");
      await load();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Gagal kirim");
    } finally {
      setSending(false);
    }
  }

  return (
    <div className="surface" style={{ overflow: "hidden", display: "flex", flexDirection: "column" }}>
      <div className="px-4 py-2.5" style={{ fontSize: 13, fontWeight: 600, borderBottom: "1px solid var(--ink-150)" }}>
        💬 {title}
      </div>

      <div ref={listRef} style={{ maxHeight: 300, overflowY: "auto", padding: 12, display: "flex", flexDirection: "column", gap: 8, background: "var(--ink-50)" }}>
        {messages.length === 0 ? (
          <div style={{ textAlign: "center", color: "var(--ink-400)", fontSize: 12.5, padding: "24px 0" }}>
            Belum ada pesan. Mulai percakapan.
          </div>
        ) : (
          messages.map((m, i) => {
            const mine = m.sender_username.toLowerCase() === me;
            return (
              <div key={i} style={{ alignSelf: mine ? "flex-end" : "flex-start", maxWidth: "82%" }}>
                <div
                  style={{
                    padding: "7px 10px", borderRadius: 10, fontSize: 13, lineHeight: 1.45,
                    background: mine ? "var(--brand-600)" : "var(--paper)",
                    color: mine ? "#fff" : "var(--ink-800)",
                    border: mine ? "none" : "1px solid var(--ink-150)",
                    whiteSpace: "pre-wrap", wordBreak: "break-word",
                  }}
                >
                  {m.body}
                </div>
                <div style={{ fontSize: 10, color: "var(--ink-400)", marginTop: 2, textAlign: mine ? "right" : "left" }}>
                  {mine ? "Anda" : roleLabel[m.sender_role] || m.sender_role} · {fmtDate(m.created_at)}
                </div>
              </div>
            );
          })
        )}
      </div>

      {err && <div className="alert alert-error" style={{ margin: 10, marginBottom: 0 }}>{err}</div>}

      <div className="flex items-center gap-2 p-3" style={{ borderTop: "1px solid var(--ink-150)" }}>
        <input
          className="input"
          style={{ flex: 1, height: 36 }}
          placeholder="Tulis pesan…"
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              send();
            }
          }}
        />
        <button className="btn btn-primary btn-sm" onClick={send} disabled={sending || !text.trim()}>
          {sending ? "…" : "Kirim"}
        </button>
      </div>
    </div>
  );
}
