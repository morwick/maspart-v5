"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import AppShell from "@/components/AppShell";
import { ApiError, getBranchOrders, setBranchOrderStatus, type OrderSummary } from "@/lib/api";
import { clearSession, getToken, getUser } from "@/lib/auth";
import { ORDER_STATUS, rp, fmtDate } from "@/lib/order-ui";

type TabKey = "all" | "belum" | "diproses" | "dikirim" | "selesai" | "batal";
const TABS: { key: TabKey; label: string; match: (s: string) => boolean }[] = [
  { key: "all", label: "Semua", match: () => true },
  { key: "belum", label: "Belum Bayar", match: (s) => s === "menunggu_pembayaran" || s === "menunggu_verifikasi" },
  { key: "diproses", label: "Perlu Dikirim", match: (s) => s === "diproses" },
  { key: "dikirim", label: "Dikirim", match: (s) => s === "dikirim" },
  { key: "selesai", label: "Selesai", match: (s) => s === "selesai" },
  { key: "batal", label: "Dibatalkan", match: (s) => s === "batal" },
];

export default function BranchOrdersPage() {
  const router = useRouter();
  const [orders, setOrders] = useState<OrderSummary[]>([]);
  const [branch, setBranch] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [tab, setTab] = useState<TabKey>("diproses");
  const [q, setQ] = useState("");
  const [busyCode, setBusyCode] = useState<string | null>(null);

  const load = useCallback(async () => {
    const token = getToken();
    if (!token) return router.replace("/login");
    if (getUser()?.role !== "user") return router.replace("/search");
    try {
      const d = await getBranchOrders(token);
      setOrders(d.orders);
      setBranch(d.branch);
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
    load();
  }, [load]);

  const counts = useMemo(() => {
    const c: Record<string, number> = {};
    for (const t of TABS) c[t.key] = orders.filter((o) => t.match(o.status)).length;
    return c;
  }, [orders]);

  const filtered = useMemo(() => {
    const tabDef = TABS.find((t) => t.key === tab) ?? TABS[0];
    const term = q.trim().toLowerCase();
    return orders
      .filter((o) => tabDef.match(o.status))
      .filter(
        (o) =>
          !term ||
          o.order_code.toLowerCase().includes(term) ||
          (o.username || "").toLowerCase().includes(term),
      );
  }, [orders, tab, q]);

  async function quickStatus(code: string, status: string) {
    const token = getToken();
    if (!token) return;
    setBusyCode(code);
    setError(null);
    try {
      await setBranchOrderStatus(token, code, status);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Gagal ubah status");
    } finally {
      setBusyCode(null);
    }
  }

  return (
    <AppShell
      active="/cabang/pesanan"
      title="Pesanan Masuk"
      sub={branch ? `Gudang ${branch} — kelola, proses & kirim pesanan` : "Pesanan untuk gudang ini"}
    >
      <div className="mx-auto w-full max-w-6xl px-4 py-5 sm:px-6">
        {error && <div className="alert alert-error" style={{ marginBottom: 16 }}>{error}</div>}

        {/* Ringkasan jumlah per status */}
        <div className="mb-4 grid gap-2.5" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(130px, 1fr))" }}>
          {TABS.filter((t) => t.key !== "all").map((t) => (
            <button
              key={t.key}
              onClick={() => setTab(t.key)}
              className="surface surface-pad text-left"
              style={{ border: tab === t.key ? "1.5px solid var(--brand-600)" : "1px solid var(--ink-150)" }}
            >
              <div className="stat-label">{t.label}</div>
              <div className="stat-value">{counts[t.key] ?? 0}</div>
            </button>
          ))}
        </div>

        {/* Tab + pencarian */}
        <div className="mb-3 flex flex-wrap items-center gap-2">
          <div className="tabs">
            {TABS.map((t) => (
              <button key={t.key} className={"tab" + (tab === t.key ? " active" : "")} onClick={() => setTab(t.key)}>
                {t.label}
                {counts[t.key] ? <span style={{ marginLeft: 6, color: "var(--ink-400)" }}>({counts[t.key]})</span> : null}
              </button>
            ))}
          </div>
          <span className="grow" />
          <input
            className="input"
            style={{ height: 34, maxWidth: 240 }}
            placeholder="Cari kode / pemesan…"
            value={q}
            onChange={(e) => setQ(e.target.value)}
          />
        </div>

        {loaded && filtered.length === 0 && !error ? (
          <div className="surface grid place-items-center" style={{ height: 200, color: "var(--ink-500)" }}>
            {orders.length === 0 ? "Belum ada pesanan masuk." : "Tidak ada pesanan pada filter ini."}
          </div>
        ) : (
          <div className="surface" style={{ overflow: "auto" }}>
            <table className="tbl">
              <thead>
                <tr>
                  <th>Kode</th>
                  <th>Pemesan</th>
                  <th className="num">Total</th>
                  <th>Status</th>
                  <th>Tanggal</th>
                  <th style={{ width: 150 }}>Aksi</th>
                </tr>
              </thead>
              <tbody>
                {filtered.map((o) => {
                  const stt = ORDER_STATUS[o.status] || { label: o.status, pill: "" };
                  const busy = busyCode === o.order_code;
                  return (
                    <tr
                      key={o.order_code}
                      style={{ cursor: "pointer" }}
                      onClick={() => router.push(`/cabang/pesanan/${encodeURIComponent(o.order_code)}`)}
                    >
                      <td className="pn">{o.order_code}</td>
                      <td>{o.username}</td>
                      <td className="num mono">{rp(o.total)}</td>
                      <td><span className={"pill " + stt.pill}>{stt.label}</span></td>
                      <td style={{ color: "var(--ink-500)" }}>{fmtDate(o.created_at)}</td>
                      <td onClick={(e) => e.stopPropagation()}>
                        {o.status === "diproses" ? (
                          <button className="btn btn-primary btn-sm" disabled={busy} onClick={() => quickStatus(o.order_code, "dikirim")}>
                            {busy ? "…" : "Kirim"}
                          </button>
                        ) : o.status === "dikirim" ? (
                          <button className="btn btn-secondary btn-sm" disabled={busy} onClick={() => quickStatus(o.order_code, "selesai")}>
                            {busy ? "…" : "Selesai"}
                          </button>
                        ) : (
                          <button
                            className="btn btn-ghost btn-sm"
                            onClick={() => router.push(`/cabang/pesanan/${encodeURIComponent(o.order_code)}`)}
                          >
                            Detail
                          </button>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </AppShell>
  );
}
