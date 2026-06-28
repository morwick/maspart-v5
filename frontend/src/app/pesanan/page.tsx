"use client";

import { useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import AppShell from "@/components/AppShell";
import { ApiError, getMyOrders, type OrderSummary } from "@/lib/api";
import { clearSession, getToken, getUser } from "@/lib/auth";
import { ORDER_STATUS, rp, fmtDate } from "@/lib/order-ui";

type TabKey = "all" | "belum" | "diproses" | "dikirim" | "selesai" | "batal";
const TABS: { key: TabKey; label: string; match: (s: string) => boolean }[] = [
  { key: "all", label: "Semua", match: () => true },
  { key: "belum", label: "Belum Bayar", match: (s) => s === "menunggu_pembayaran" || s === "menunggu_verifikasi" },
  { key: "diproses", label: "Diproses", match: (s) => s === "diproses" },
  { key: "dikirim", label: "Dikirim", match: (s) => s === "dikirim" },
  { key: "selesai", label: "Selesai", match: (s) => s === "selesai" },
  { key: "batal", label: "Dibatalkan", match: (s) => s === "batal" },
];

export default function PesananPage() {
  const router = useRouter();
  const [orders, setOrders] = useState<OrderSummary[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [tab, setTab] = useState<TabKey>("all");
  const [q, setQ] = useState("");

  useEffect(() => {
    const token = getToken();
    if (!token) {
      router.replace("/login");
      return;
    }
    // Halaman "Pesanan Saya" hanya untuk pembeli.
    if (getUser()?.role !== "pembeli") {
      router.replace("/search");
      return;
    }
    getMyOrders(token)
      .then((d) => setOrders(d.orders))
      .catch((err) => {
        if (err instanceof ApiError && err.status === 401) {
          clearSession();
          return router.replace("/login");
        }
        setError(err instanceof Error ? err.message : "Gagal memuat pesanan");
      })
      .finally(() => setLoaded(true));
  }, [router]);

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
      .filter((o) => !term || o.order_code.toLowerCase().includes(term) || (o.gudang || "").toLowerCase().includes(term));
  }, [orders, tab, q]);

  return (
    <AppShell active="/pesanan" title="Pesanan Saya" sub="Riwayat & status pesananmu">
      <div className="mx-auto w-full max-w-5xl px-4 py-5 sm:px-6">
        {error && <div className="alert alert-error" style={{ marginBottom: 16 }}>{error}</div>}

        {/* Tab status + pencarian */}
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
            style={{ height: 34, maxWidth: 220 }}
            placeholder="Cari kode pesanan…"
            value={q}
            onChange={(e) => setQ(e.target.value)}
          />
        </div>

        {loaded && filtered.length === 0 && !error ? (
          <div className="surface grid place-items-center" style={{ height: 200, color: "var(--ink-500)" }}>
            {orders.length === 0 ? "Belum ada pesanan." : "Tidak ada pesanan pada filter ini."}
          </div>
        ) : (
          <div className="surface" style={{ overflow: "auto" }}>
            <table className="tbl">
              <thead>
                <tr>
                  <th>Kode</th>
                  <th>Dikirim dari</th>
                  <th className="num">Total</th>
                  <th>Status</th>
                  <th>Tanggal</th>
                  <th style={{ width: 110 }}>Aksi</th>
                </tr>
              </thead>
              <tbody>
                {filtered.map((o) => {
                  const st = ORDER_STATUS[o.status] || { label: o.status, pill: "" };
                  const belumBayar = o.status === "menunggu_pembayaran";
                  const lunas = ["diproses", "dikirim", "selesai"].includes(o.status);
                  return (
                    <tr
                      key={o.order_code}
                      style={{ cursor: "pointer" }}
                      onClick={() => router.push(`/pesanan/${encodeURIComponent(o.order_code)}`)}
                    >
                      <td className="pn">{o.order_code}</td>
                      <td>{o.gudang || "—"}</td>
                      <td className="num mono">{rp(o.total)}</td>
                      <td><span className={"pill " + st.pill}>{st.label}</span></td>
                      <td style={{ color: "var(--ink-500)" }}>{fmtDate(o.created_at)}</td>
                      <td onClick={(e) => e.stopPropagation()}>
                        <div className="flex items-center justify-end gap-1">
                          {lunas && (
                            <button
                              className="btn btn-ghost btn-sm"
                              title="Cetak Invoice"
                              onClick={() => router.push(`/pesanan/${encodeURIComponent(o.order_code)}/invoice`)}
                            >
                              🧾
                            </button>
                          )}
                          <button
                            className={"btn btn-sm " + (belumBayar ? "btn-primary" : "btn-ghost")}
                            onClick={() => router.push(`/pesanan/${encodeURIComponent(o.order_code)}`)}
                          >
                            {belumBayar ? "Bayar" : o.status === "dikirim" ? "Lacak" : "Detail"}
                          </button>
                        </div>
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
