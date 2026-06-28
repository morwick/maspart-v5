"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import AppShell from "@/components/AppShell";
import { ApiError, getAdminOrders, type OrderSummary } from "@/lib/api";
import { clearSession, getToken, getUser } from "@/lib/auth";
import { ORDER_STATUS, rp, fmtDate } from "@/lib/order-ui";

export default function AdminOrdersPage() {
  const router = useRouter();
  const [orders, setOrders] = useState<OrderSummary[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    const token = getToken();
    if (!token) return router.replace("/login");
    if (getUser()?.role !== "admin") {
      router.replace("/search");
      return;
    }
    getAdminOrders(token)
      .then((d) => setOrders(d.orders))
      .catch((err) => {
        if (err instanceof ApiError && err.status === 401) {
          clearSession();
          return router.replace("/login");
        }
        setError(err instanceof Error ? err.message : "Gagal memuat");
      })
      .finally(() => setLoaded(true));
  }, [router]);

  const pending = orders.filter((o) => o.status === "menunggu_verifikasi").length;

  return (
    <AppShell active="/admin/orders" title="Pesanan" sub="Kelola & verifikasi pesanan dari cabang">
      <div className="mx-auto w-full max-w-6xl px-4 py-5 sm:px-6">
        {error && <div className="alert alert-error" style={{ marginBottom: 16 }}>{error}</div>}
        {pending > 0 && (
          <div className="mb-3" style={{ fontSize: 13, color: "var(--ink-600)" }}>
            <span className="pill pill-warn pill-dot">{pending} menunggu verifikasi</span>
          </div>
        )}

        {loaded && orders.length === 0 && !error ? (
          <div className="surface grid place-items-center" style={{ height: 200, color: "var(--ink-500)" }}>
            Belum ada pesanan.
          </div>
        ) : (
          <div className="surface" style={{ overflow: "hidden" }}>
            <table className="tbl">
              <thead>
                <tr>
                  <th>Kode</th>
                  <th>Pemesan</th>
                  <th>Cabang</th>
                  <th className="num">Total</th>
                  <th>Status</th>
                  <th>Bukti</th>
                  <th>Tanggal</th>
                </tr>
              </thead>
              <tbody>
                {orders.map((o) => {
                  const st = ORDER_STATUS[o.status] || { label: o.status, pill: "" };
                  return (
                    <tr
                      key={o.order_code}
                      style={{ cursor: "pointer" }}
                      onClick={() => router.push(`/admin/orders/${encodeURIComponent(o.order_code)}`)}
                    >
                      <td className="pn">{o.order_code}</td>
                      <td>{o.username}</td>
                      <td>{o.gudang || "—"}</td>
                      <td className="num mono">{rp(o.total)}</td>
                      <td><span className={"pill " + st.pill}>{st.label}</span></td>
                      <td>{o.payment_proof_url ? <span className="pill pill-info">ada</span> : <span style={{ color: "var(--ink-400)" }}>—</span>}</td>
                      <td style={{ color: "var(--ink-500)" }}>{fmtDate(o.created_at)}</td>
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
