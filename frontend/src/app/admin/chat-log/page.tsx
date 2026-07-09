"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import AppShell from "@/components/AppShell";
import {
  ApiError,
  deleteChatLog,
  getChatLog,
  type ChatLogRow,
  type ChatLogSummary,
} from "@/lib/api";
import { clearSession, getToken, getUser } from "@/lib/auth";

function Stat({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="surface" style={{ padding: 14, minWidth: 130, flex: 1 }}>
      <div className="stat-label">{label}</div>
      <div className="stat-value" style={{ fontSize: 22 }}>{value}</div>
      {hint && <div style={{ fontSize: 11.5, color: "var(--ink-500)" }}>{hint}</div>}
    </div>
  );
}

export default function ChatLogPage() {
  const router = useRouter();
  const [summary, setSummary] = useState<ChatLogSummary | null>(null);
  const [rows, setRows] = useState<ChatLogRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const load = useCallback(async () => {
    const token = getToken();
    if (!token) return router.replace("/login");
    setLoading(true);
    setError(null);
    try {
      const d = await getChatLog(token, 200);
      setSummary(d.ringkasan);
      setRows(d.log);
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        clearSession();
        return router.replace("/login");
      }
      if (err instanceof ApiError && err.status === 403) return router.replace("/search");
      setError(err instanceof Error ? err.message : "Gagal memuat");
    } finally {
      setLoading(false);
    }
  }, [router]);

  const hapus = useCallback(async (beforeDays?: number) => {
    const token = getToken();
    if (!token) return router.replace("/login");
    const pesan = beforeDays
      ? `Hapus log yang lebih tua dari ${beforeDays} hari?`
      : "Hapus SEMUA log observabilitas AI? Tindakan ini tidak bisa dibatalkan.";
    if (!window.confirm(pesan)) return;
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const r = await deleteChatLog(token, beforeDays);
      setNotice(`${r.dihapus >= 0 ? r.dihapus.toLocaleString("id-ID") : "Sejumlah"} baris dihapus.`);
      await load();
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        clearSession();
        return router.replace("/login");
      }
      setError(err instanceof Error ? err.message : "Gagal menghapus");
    } finally {
      setBusy(false);
    }
  }, [router, load]);

  useEffect(() => {
    if (getUser()?.role !== "admin") {
      router.replace("/search");
      return;
    }
    load();
  }, [router, load]);

  const empty = !loading && summary && summary.total === 0;

  return (
    <AppShell active="/admin/chat-log" title="Observabilitas AI" sub="Metrik tiap giliran chat asisten">
      <div className="mx-auto w-full max-w-5xl px-4 py-6 sm:px-7">
        <div className="flex items-center gap-3" style={{ marginBottom: 14 }}>
          <p style={{ fontSize: 13.5, color: "var(--ink-500)", flex: 1 }}>
            Ringkasan dari maksimal 1000 giliran chat terakhir — pakai untuk memantau latensi,
            seberapa sering guard anti-halusinasi menyala, dan tool mana yang paling dipakai.
          </p>
          <button className="btn btn-secondary btn-sm" onClick={load} disabled={loading || busy}>
            {loading ? "Memuat…" : "↻ Muat ulang"}
          </button>
          <button className="btn btn-secondary btn-sm" onClick={() => hapus(30)} disabled={loading || busy}
                  title="Hapus baris yang lebih tua dari 30 hari">
            🧹 Hapus &gt;30 hari
          </button>
          <button className="btn btn-danger btn-sm" onClick={() => hapus()} disabled={loading || busy}
                  title="Hapus SEMUA log">
            {busy ? "Menghapus…" : "🗑️ Hapus semua"}
          </button>
        </div>

        <p style={{ fontSize: 12, color: "var(--ink-500)", marginTop: -6, marginBottom: 12 }}>
          Log &gt;30 hari juga terhapus <b>otomatis</b> (retensi harian).
        </p>

        {notice && <div className="alert alert-success" style={{ marginBottom: 14 }}>{notice}</div>}
        {error && <div className="alert alert-error" style={{ marginBottom: 14 }}>{error}</div>}

        {empty && (
          <div className="surface grid place-items-center" style={{ height: 160, color: "var(--ink-500)", fontSize: 13.5, textAlign: "center", padding: 20 }}>
            Belum ada data. Pastikan tabel <code className="mono">ai_chat_log</code> sudah dibuat di
            Supabase (migrations/016_ai_chat_log.sql), lalu coba beberapa percakapan di Asisten.
          </div>
        )}

        {summary && summary.total > 0 && (
          <>
            <div className="flex flex-wrap gap-3" style={{ marginBottom: 14 }}>
              <Stat label="Giliran tercatat" value={summary.total.toLocaleString("id-ID")} />
              <Stat label="Latensi p50" value={`${((summary.latensi_ms?.p50 ?? 0) / 1000).toFixed(1)}s`}
                    hint={`p90 ${((summary.latensi_ms?.p90 ?? 0) / 1000).toFixed(1)}s · maks ${((summary.latensi_ms?.maks ?? 0) / 1000).toFixed(1)}s`} />
              <Stat label="Guard menyala" value={`${summary.guard_rasio_persen ?? 0}%`}
                    hint={`${summary.guard_menyala ?? 0} giliran`} />
              <Stat label="Tool gagal" value={`${summary.tool_gagal_rasio_persen ?? 0}%`}
                    hint={`${summary.tool_gagal ?? 0} giliran`} />
            </div>

            <div className="flex flex-wrap gap-3" style={{ marginBottom: 22 }}>
              <div className="surface" style={{ padding: 14, flex: 1, minWidth: 240 }}>
                <div className="stat-label" style={{ marginBottom: 8 }}>Tool tersering</div>
                {(summary.tool_tersering ?? []).map(([t, n]) => (
                  <div key={t} className="flex items-center justify-between" style={{ fontSize: 12.5, padding: "2px 0" }}>
                    <span className="mono">{t}</span>
                    <span style={{ color: "var(--ink-500)" }}>{n}</span>
                  </div>
                ))}
              </div>
              <div className="surface" style={{ padding: 14, flex: 1, minWidth: 240 }}>
                <div className="stat-label" style={{ marginBottom: 8 }}>Outcome jawaban</div>
                {Object.entries(summary.outcome ?? {}).map(([o, n]) => (
                  <div key={o} className="flex items-center justify-between" style={{ fontSize: 12.5, padding: "2px 0" }}>
                    <span>{o}</span>
                    <span style={{ color: "var(--ink-500)" }}>{n}</span>
                  </div>
                ))}
              </div>
            </div>
          </>
        )}

        {rows.length > 0 && (
          <div className="surface" style={{ overflow: "auto" }}>
            <table className="tbl">
              <thead>
                <tr>
                  <th>Waktu</th>
                  <th>Pertanyaan</th>
                  <th style={{ width: 120 }}>Tool</th>
                  <th className="num" style={{ width: 70 }}>Ronde</th>
                  <th className="num" style={{ width: 80 }}>Latensi</th>
                  <th style={{ width: 90 }}>Outcome</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.id}>
                    <td style={{ fontSize: 11.5, color: "var(--ink-500)", whiteSpace: "nowrap" }}>
                      {new Date(r.created_at).toLocaleString("id-ID", { dateStyle: "short", timeStyle: "short" })}
                    </td>
                    <td style={{ maxWidth: 320, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={r.question || ""}>
                      {r.question || "—"}
                      {r.guard_hit && <span className="pill" style={{ marginLeft: 6, height: 18, fontSize: 10, padding: "0 6px" }}>guard</span>}
                      {r.tool_failed && <span className="pill" style={{ marginLeft: 4, height: 18, fontSize: 10, padding: "0 6px" }}>tool gagal</span>}
                    </td>
                    <td style={{ fontSize: 11, color: "var(--ink-600)" }} title={r.tools || ""}>
                      {r.tools_count > 0 ? `${r.tools_count} tool` : "—"}
                    </td>
                    <td className="num mono">{r.rounds}</td>
                    <td className="num mono">{(r.latency_ms / 1000).toFixed(1)}s</td>
                    <td style={{ fontSize: 11.5 }}>{r.outcome || "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </AppShell>
  );
}
