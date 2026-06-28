"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import AppShell from "@/components/AppShell";
import ChatThread from "@/components/ChatThread";
import {
  ApiError,
  getBranchChatThreads,
  getBranchChat,
  sendBranchChat,
  type ChatThreadSummary,
} from "@/lib/api";
import { clearSession, getToken, getUser } from "@/lib/auth";
import { fmtDate } from "@/lib/order-ui";

export default function BranchChatPage() {
  const router = useRouter();
  const [threads, setThreads] = useState<ChatThreadSummary[]>([]);
  const [active, setActive] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);

  const loadThreads = useCallback(async () => {
    const token = getToken();
    if (!token) return router.replace("/login");
    if (getUser()?.role !== "user") return router.replace("/search");
    try {
      const d = await getBranchChatThreads(token);
      setThreads(d.threads);
      setActive((cur) => cur ?? d.threads[0]?.buyer_username ?? null);
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        clearSession();
        return router.replace("/login");
      }
      if (err instanceof ApiError && err.status === 403) return router.replace("/search");
      setError(err instanceof Error ? err.message : "Gagal memuat");
    } finally {
      setLoaded(true);
    }
  }, [router]);

  useEffect(() => {
    loadThreads();
    const id = setInterval(loadThreads, 15000);
    return () => clearInterval(id);
  }, [loadThreads]);

  return (
    <AppShell active="/cabang/chat" title="Chat" sub="Pertanyaan pembeli ke gudang ini">
      <div className="mx-auto w-full max-w-5xl px-4 py-5 sm:px-6">
        {error && <div className="alert alert-error" style={{ marginBottom: 16 }}>{error}</div>}

        {loaded && threads.length === 0 && !error ? (
          <div className="surface grid place-items-center" style={{ height: 200, color: "var(--ink-500)" }}>
            Belum ada chat dari pembeli.
          </div>
        ) : (
          <div className="grid gap-4" style={{ gridTemplateColumns: "minmax(200px, 260px) 1fr" }}>
            {/* Daftar pembeli */}
            <div className="surface" style={{ overflow: "hidden", height: "fit-content" }}>
              <div className="px-3 py-2.5" style={{ fontSize: 12.5, fontWeight: 600, borderBottom: "1px solid var(--ink-150)" }}>
                Pembeli
              </div>
              <div className="flex flex-col">
                {threads.map((t) => (
                  <button
                    key={t.buyer_username}
                    onClick={() => setActive(t.buyer_username)}
                    className="text-left"
                    style={{
                      padding: "9px 12px", borderBottom: "1px solid var(--ink-100)",
                      background: active === t.buyer_username ? "var(--brand-50)" : "transparent",
                    }}
                  >
                    <div style={{ fontSize: 13, fontWeight: 600, color: active === t.buyer_username ? "var(--brand-700)" : "var(--ink-800)" }}>
                      {t.buyer_username}
                    </div>
                    <div className="truncate" style={{ fontSize: 11.5, color: "var(--ink-500)" }}>{t.last}</div>
                    <div style={{ fontSize: 10, color: "var(--ink-400)" }}>{fmtDate(t.created_at)}</div>
                  </button>
                ))}
              </div>
            </div>

            {/* Thread */}
            <div style={{ minHeight: 420 }}>
              <div className="mb-2" style={{ fontSize: 13.5, fontWeight: 600 }}>
                {active ? `Pembeli ${active}` : "Pilih pembeli"}
              </div>
              <ChatThread
                threadKey={active ?? "none"}
                disabled={!active}
                load={async () => {
                  const token = getToken();
                  if (!token || !active) return [];
                  const d = await getBranchChat(token, active);
                  return d.messages;
                }}
                send={async (body) => {
                  const token = getToken();
                  if (!token || !active) return;
                  await sendBranchChat(token, active, body);
                  await loadThreads();
                }}
              />
            </div>
          </div>
        )}
      </div>
    </AppShell>
  );
}
