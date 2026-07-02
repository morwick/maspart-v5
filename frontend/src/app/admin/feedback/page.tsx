"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import AppShell from "@/components/AppShell";
import Markdown from "@/components/Markdown";
import {
  ApiError,
  listAiFeedback,
  resolveAiFeedback,
  type AIFeedbackList,
  type AIFeedbackRow,
} from "@/lib/api";
import { clearSession, getToken, getUser } from "@/lib/auth";

type Filter = "down" | "up" | "all";

export default function AdminFeedbackPage() {
  const router = useRouter();
  const [data, setData] = useState<AIFeedbackList | null>(null);
  const [filter, setFilter] = useState<Filter>("down");
  const [onlyOpen, setOnlyOpen] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    const token = getToken();
    if (!token) return router.replace("/login");
    setLoading(true);
    setError(null);
    try {
      const d = await listAiFeedback(token, {
        rating: filter === "all" ? undefined : filter,
        onlyOpen: filter === "up" ? false : onlyOpen,
      });
      setData(d);
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        clearSession();
        return router.replace("/login");
      }
      if (err instanceof ApiError && err.status === 403) return router.replace("/search");
      setError(err instanceof Error ? err.message : "Gagal memuat umpan balik");
    } finally {
      setLoading(false);
    }
  }, [router, filter, onlyOpen]);

  useEffect(() => {
    if (getUser()?.role !== "admin") {
      router.replace("/search");
      return;
    }
    load();
  }, [router, load]);

  async function toggleResolved(row: AIFeedbackRow) {
    const token = getToken();
    if (!token) return;
    // Optimistis.
    setData((d) =>
      d
        ? { ...d, feedback: d.feedback.map((r) => (r.id === row.id ? { ...r, resolved: !r.resolved } : r)) }
        : d,
    );
    try {
      await resolveAiFeedback(token, row.id, !row.resolved);
    } catch {
      setError("Gagal memperbarui status. Muat ulang.");
      load();
    }
  }

  const s = data?.ringkasan;

  return (
    <AppShell active="/admin/feedback" title="Umpan Balik AI" sub="Jawaban yang dinilai user — bahan perbaikan asisten">
      <div className="mx-auto w-full max-w-5xl px-4 py-5 sm:px-6">
        <h2 className="mb-1 text-base font-semibold">
          Umpan Balik <span className="text-brand">Asisten AI</span>
        </h2>
        <p className="mb-4 text-sm text-zinc-500">
          Tiap 👍/👎 dari user tersimpan di sini beserta pertanyaan, jawaban, dan sumber
          data yang dipakai. Fokus ke <b>👎 yang belum ditangani</b> — itu antrean perbaikan.
        </p>

        {/* Ringkasan */}
        {s && (
          <div style={{ display: "flex", gap: 10, flexWrap: "wrap", marginBottom: 16 }}>
            <StatCard label="Total feedback" value={s.total} />
            <StatCard label="👍 Bagus" value={s.up} tone="brand" />
            <StatCard label="👎 Perlu perbaikan" value={s.down} tone="warn" />
            <StatCard label="👎 Belum ditangani" value={s.down_belum_ditangani} tone="danger" />
          </div>
        )}

        {/* Filter */}
        <div style={{ display: "flex", gap: 12, alignItems: "center", flexWrap: "wrap", marginBottom: 14 }}>
          <div className="inline-flex rounded-lg border border-zinc-300 p-0.5 text-sm">
            {(
              [
                ["down", "👎 Perlu perbaikan"],
                ["up", "👍 Bagus"],
                ["all", "Semua"],
              ] as [Filter, string][]
            ).map(([k, label]) => (
              <button
                key={k}
                onClick={() => setFilter(k)}
                className={`rounded-md px-3 py-1.5 font-medium transition-colors ${
                  filter === k ? "bg-brand text-white" : "text-zinc-600 hover:bg-zinc-100"
                }`}
              >
                {label}
              </button>
            ))}
          </div>
          {filter !== "up" && (
            <label style={{ display: "inline-flex", alignItems: "center", gap: 6, fontSize: 13, color: "var(--ink-600)" }}>
              <input type="checkbox" checked={onlyOpen} onChange={(e) => setOnlyOpen(e.target.checked)} />
              Sembunyikan yang sudah ditangani
            </label>
          )}
          <button className="btn btn-secondary btn-sm" onClick={load} disabled={loading}>
            {loading ? "Memuat…" : "Muat ulang"}
          </button>
        </div>

        {error && (
          <p className="mb-3 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700 ring-1 ring-red-100">
            {error}
          </p>
        )}

        {!data ? (
          <p className="text-sm text-zinc-500">Memuat…</p>
        ) : data.feedback.length === 0 ? (
          <div className="surface" style={{ padding: 28, textAlign: "center", color: "var(--ink-500)" }}>
            {filter === "down"
              ? "Tidak ada 👎 yang belum ditangani. 🎉"
              : "Belum ada umpan balik pada filter ini."}
          </div>
        ) : (
          <div style={{ display: "grid", gap: 12 }}>
            {data.feedback.map((row) => (
              <FeedbackCard key={row.id} row={row} onToggle={() => toggleResolved(row)} />
            ))}
          </div>
        )}
      </div>
    </AppShell>
  );
}

function StatCard({ label, value, tone }: { label: string; value: number; tone?: "brand" | "warn" | "danger" }) {
  const color =
    tone === "brand" ? "var(--brand-700)" : tone === "warn" ? "var(--warn-600)" : tone === "danger" ? "var(--danger-600)" : "var(--ink-900)";
  return (
    <div className="surface" style={{ padding: "10px 16px", minWidth: 140 }}>
      <div className="stat-label">{label}</div>
      <div className="stat-value" style={{ color }}>{value}</div>
    </div>
  );
}

function fmtDate(iso: string): string {
  try {
    return new Intl.DateTimeFormat("id-ID", {
      dateStyle: "medium",
      timeStyle: "short",
    }).format(new Date(iso));
  } catch {
    return iso;
  }
}

function FeedbackCard({ row, onToggle }: { row: AIFeedbackRow; onToggle: () => void }) {
  const [open, setOpen] = useState(false);
  const down = row.rating === "down";
  return (
    <div
      className="surface"
      style={{
        padding: 14,
        borderLeft: `3px solid ${down ? "var(--warn-600)" : "var(--brand-600)"}`,
        opacity: row.resolved ? 0.62 : 1,
      }}
    >
      <div style={{ display: "flex", alignItems: "flex-start", gap: 10 }}>
        <span style={{ fontSize: 18, lineHeight: 1 }}>{down ? "👎" : "👍"}</span>
        <div style={{ flex: 1, minWidth: 0 }}>
          {/* Pertanyaan */}
          <div style={{ fontSize: 14, fontWeight: 600, color: "var(--ink-900)" }}>
            {row.question || <span style={{ color: "var(--ink-400)" }}>(pertanyaan tidak tercatat)</span>}
          </div>
          {/* Catatan user (khusus 👎) */}
          {row.note && (
            <div
              style={{
                marginTop: 6,
                fontSize: 12.5,
                color: "var(--warn-600)",
                background: "var(--warn-50)",
                border: "1px solid #f6d9a8",
                borderRadius: 8,
                padding: "5px 10px",
              }}
            >
              Catatan user: “{row.note}”
            </div>
          )}
          {/* Jawaban (lipat) */}
          <button
            onClick={() => setOpen((v) => !v)}
            className="link"
            style={{ fontSize: 12, marginTop: 8, background: "none", border: "none", padding: 0 }}
          >
            {open ? "▾ Sembunyikan jawaban" : "▸ Lihat jawaban asisten"}
          </button>
          {open && (
            <div
              style={{
                marginTop: 6,
                fontSize: 13,
                background: "var(--ink-50)",
                border: "1px solid var(--ink-150)",
                borderRadius: 8,
                padding: "8px 12px",
                maxHeight: 360,
                overflow: "auto",
              }}
            >
              <Markdown content={row.answer || "(jawaban tidak tercatat)"} />
            </div>
          )}
          {/* Meta */}
          <div style={{ display: "flex", flexWrap: "wrap", gap: 8, alignItems: "center", marginTop: 8 }}>
            <span style={{ fontSize: 11, color: "var(--ink-500)" }}>{fmtDate(row.created_at)}</span>
            {row.username && (
              <span className="pill" style={{ height: 20, fontSize: 10.5 }}>
                {row.username}
                {row.role ? ` · ${row.role}` : ""}
              </span>
            )}
            {row.tools && (
              <span style={{ fontSize: 10.5, color: "var(--ink-400)" }}>🔧 {row.tools}</span>
            )}
          </div>
        </div>
        {/* Aksi triase */}
        <button
          className={row.resolved ? "btn btn-secondary btn-sm" : "btn btn-primary btn-sm"}
          onClick={onToggle}
          title={row.resolved ? "Tandai belum selesai" : "Tandai sudah ditangani"}
        >
          {row.resolved ? "Buka lagi" : "Sudah ditangani"}
        </button>
      </div>
    </div>
  );
}
