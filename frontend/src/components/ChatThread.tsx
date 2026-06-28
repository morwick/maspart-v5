"use client";

import { useEffect, useRef, useState } from "react";
import { type ChatMessage } from "@/lib/api";
import { getUser } from "@/lib/auth";
import { fmtDate } from "@/lib/order-ui";

const roleLabel: Record<string, string> = { pembeli: "Pembeli", gudang: "Gudang", admin: "Admin" };

export default function ChatThread({
  threadKey,
  load,
  send,
  emptyText = "Belum ada pesan. Mulai percakapan.",
  disabled = false,
}: {
  threadKey: string; // identitas thread aktif — reset saat berganti
  load: () => Promise<ChatMessage[]>;
  send: (body: string) => Promise<void>;
  emptyText?: string;
  disabled?: boolean;
}) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [text, setText] = useState("");
  const [sending, setSending] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const me = (getUser()?.username || "").toLowerCase();
  const listRef = useRef<HTMLDivElement>(null);
  const lastCount = useRef(0);

  useEffect(() => {
    if (disabled) {
      setMessages([]);
      return;
    }
    let alive = true;
    const run = () => load().then((m) => alive && setMessages(m)).catch(() => {});
    run();
    const id = setInterval(run, 7000);
    return () => {
      alive = false;
      clearInterval(id);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [threadKey, disabled]);

  useEffect(() => {
    if (messages.length !== lastCount.current) {
      lastCount.current = messages.length;
      const el = listRef.current;
      if (el) el.scrollTop = el.scrollHeight;
    }
  }, [messages]);

  async function doSend() {
    const body = text.trim();
    if (!body || disabled) return;
    setSending(true);
    setErr(null);
    try {
      await send(body);
      setText("");
      const m = await load();
      setMessages(m);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Gagal kirim");
    } finally {
      setSending(false);
    }
  }

  return (
    <div className="surface" style={{ overflow: "hidden", display: "flex", flexDirection: "column", height: "100%" }}>
      <div ref={listRef} style={{ flex: 1, minHeight: 320, maxHeight: "60vh", overflowY: "auto", padding: 12, display: "flex", flexDirection: "column", gap: 8, background: "var(--ink-50)" }}>
        {disabled ? (
          <div style={{ textAlign: "center", color: "var(--ink-400)", fontSize: 12.5, padding: "24px 0" }}>
            Pilih percakapan di samping.
          </div>
        ) : messages.length === 0 ? (
          <div style={{ textAlign: "center", color: "var(--ink-400)", fontSize: 12.5, padding: "24px 0" }}>{emptyText}</div>
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
          placeholder={disabled ? "Pilih percakapan…" : "Tulis pesan…"}
          value={text}
          disabled={disabled}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              doSend();
            }
          }}
        />
        <button className="btn btn-primary btn-sm" onClick={doSend} disabled={disabled || sending || !text.trim()}>
          {sending ? "…" : "Kirim"}
        </button>
      </div>
    </div>
  );
}
