"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import AppShell from "@/components/AppShell";
import ChatThread from "@/components/ChatThread";
import {
  ApiError,
  getBuyerLocations,
  getBuyerChatThreads,
  getBuyerGudangChat,
  sendBuyerGudangChat,
} from "@/lib/api";
import { clearSession, getToken, getUser } from "@/lib/auth";

type Conv = { key: string; label: string };

export default function ChatPage() {
  const router = useRouter();
  const [convs, setConvs] = useState<Conv[]>([]);
  const [active, setActive] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    const token = getToken();
    if (!token) return router.replace("/login");
    if (getUser()?.role !== "pembeli") {
      router.replace("/search");
      return;
    }
    const pre = new URLSearchParams(window.location.search).get("gudang");
    const myGudang = getUser()?.gudang ?? null;

    Promise.all([getBuyerLocations(token), getBuyerChatThreads(token)])
      .then(([locs, threads]) => {
        const labelOf = new Map(locs.locations.map((l) => [l.key, l.label]));
        // Gudang relevan saja: yang dari tombol detail part (pre) + riwayat chat.
        // Lokasi pembeli hanya dipakai sebagai default bila tak ada konteks & belum ada chat.
        const keys: string[] = [];
        const push = (k?: string | null) => {
          if (k && !keys.includes(k)) keys.push(k);
        };
        push(pre);
        threads.threads.forEach((t) => push(t.gudang_key));
        if (keys.length === 0) push(myGudang);
        const list = keys.map((k) => ({ key: k, label: labelOf.get(k) ?? k }));
        setConvs(list);
        setActive(pre && keys.includes(pre) ? pre : list[0]?.key ?? null);
      })
      .catch((err) => {
        if (err instanceof ApiError && err.status === 401) {
          clearSession();
          return router.replace("/login");
        }
        if (err instanceof ApiError && err.status === 403) return router.replace("/search");
        setError(err instanceof Error ? err.message : "Gagal memuat");
      })
      .finally(() => setLoaded(true));
  }, [router]);

  const activeLabel = convs.find((c) => c.key === active)?.label ?? "";

  return (
    <AppShell active="/chat" title="Chat" sub="Tanya ketersediaan & konfirmasi ke gudang sebelum memesan">
      <div className="mx-auto w-full max-w-5xl px-4 py-5 sm:px-6">
        {error && <div className="alert alert-error" style={{ marginBottom: 16 }}>{error}</div>}

        {loaded && convs.length === 0 && !error ? (
          <div className="surface grid place-items-center" style={{ height: 200, color: "var(--ink-500)", gap: 8, textAlign: "center", padding: 16 }}>
            <div>Belum ada percakapan.</div>
            <div style={{ fontSize: 12.5 }}>Buka detail sebuah part lalu klik <b>💬 Chat Gudang</b> untuk menanyakan ketersediaan stok.</div>
          </div>
        ) : (
          <div className="grid gap-4" style={{ gridTemplateColumns: "minmax(180px, 240px) 1fr" }}>
            {/* Daftar percakapan */}
            <div className="surface" style={{ overflow: "hidden", height: "fit-content" }}>
              <div className="px-3 py-2.5" style={{ fontSize: 12.5, fontWeight: 600, borderBottom: "1px solid var(--ink-150)" }}>
                Gudang
              </div>
              <div className="flex flex-col">
                {convs.map((c) => (
                  <button
                    key={c.key}
                    onClick={() => setActive(c.key)}
                    className="text-left"
                    style={{
                      padding: "9px 12px", fontSize: 13, borderBottom: "1px solid var(--ink-100)",
                      background: active === c.key ? "var(--brand-50)" : "transparent",
                      color: active === c.key ? "var(--brand-700)" : "var(--ink-700)",
                      fontWeight: active === c.key ? 600 : 400,
                    }}
                  >
                    {c.label}
                  </button>
                ))}
              </div>
            </div>

            {/* Thread */}
            <div style={{ minHeight: 420 }}>
              <div className="mb-2" style={{ fontSize: 13.5, fontWeight: 600 }}>
                {activeLabel ? `Gudang ${activeLabel}` : "Pilih gudang"}
              </div>
              <ChatThread
                threadKey={active ?? "none"}
                disabled={!active}
                emptyText="Belum ada pesan. Tanyakan ketersediaan stok ke gudang ini."
                load={async () => {
                  const token = getToken();
                  if (!token || !active) return [];
                  const d = await getBuyerGudangChat(token, active);
                  return d.messages;
                }}
                send={async (body) => {
                  const token = getToken();
                  if (!token || !active) return;
                  await sendBuyerGudangChat(token, active, body);
                }}
              />
            </div>
          </div>
        )}
      </div>
    </AppShell>
  );
}
