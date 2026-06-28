"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import AppShell from "@/components/AppShell";
import { ApiError, getAdminOrder, setOrderStatus, type OrderDetail } from "@/lib/api";
import { clearSession, getToken, getUser } from "@/lib/auth";
import { ORDER_STATUS, rp, fmtDate } from "@/lib/order-ui";

export default function AdminOrderDetailPage() {
  const router = useRouter();
  const params = useParams<{ code: string }>();
  const code = decodeURIComponent(Array.isArray(params.code) ? params.code[0] : params.code ?? "");

  const [order, setOrder] = useState<OrderDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [loaded, setLoaded] = useState(false);

  const load = useCallback(async () => {
    const token = getToken();
    if (!token) return router.replace("/login");
    try {
      setOrder(await getAdminOrder(token, code));
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
  }, [router, code]);

  useEffect(() => {
    if (getUser()?.role !== "admin") {
      router.replace("/search");
      return;
    }
    load();
  }, [router, load]);

  async function changeStatus(s: string) {
    const token = getToken();
    if (!token) return;
    setBusy(true);
    setError(null);
    try {
      await setOrderStatus(token, code, s);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Gagal ubah status");
    } finally {
      setBusy(false);
    }
  }

  const st = order ? ORDER_STATUS[order.status] || { label: order.status, pill: "" } : null;
  const isPdf = (order?.payment_proof_url || "").toLowerCase().endsWith(".pdf");

  return (
    <AppShell
      active="/admin/orders"
      title={order ? order.order_code : code}
      sub={st ? st.label : "Detail pesanan"}
      actions={<Link href="/admin/orders" className="btn btn-secondary btn-sm">← Pesanan</Link>}
    >
      <div className="mx-auto w-full max-w-5xl px-4 py-5 sm:px-6">
        {error && <div className="alert alert-error" style={{ marginBottom: 16 }}>{error}</div>}

        {!order ? (
          <div className="surface grid place-items-center" style={{ height: 200, color: "var(--ink-500)" }}>
            {loaded ? "Pesanan tidak ditemukan." : "Memuat…"}
          </div>
        ) : (
          <div className="grid gap-4 md:grid-cols-[1fr_320px]">
            {/* Items + info */}
            <div className="surface" style={{ overflow: "hidden" }}>
              <div className="flex flex-wrap items-center gap-2 px-4 py-3" style={{ borderBottom: "1px solid var(--ink-150)" }}>
                <span className="mono" style={{ fontWeight: 600 }}>{order.order_code}</span>
                <span className={"pill " + (st?.pill || "")}>{st?.label}</span>
                <span className="grow" />
                <span style={{ fontSize: 12, color: "var(--ink-500)" }}>{order.username} · {order.gudang}</span>
              </div>
              <table className="tbl">
                <thead>
                  <tr>
                    <th>Part Number</th>
                    <th>Nama</th>
                    <th className="num">Harga</th>
                    <th className="num">Qty</th>
                    <th className="num">Subtotal</th>
                  </tr>
                </thead>
                <tbody>
                  {order.items.map((it, i) => (
                    <tr key={i}>
                      <td className="pn">{it.part_number}</td>
                      <td>{it.name}</td>
                      <td className="num mono">{rp(it.price)}</td>
                      <td className="num">{it.qty}</td>
                      <td className="num mono">{rp(it.line_total)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <div className="px-4 py-3" style={{ borderTop: "1px solid var(--ink-150)" }}>
                <div className="flex justify-between" style={{ fontSize: 13 }}>
                  <span style={{ color: "var(--ink-500)" }}>Subtotal</span>
                  <span className="mono">{rp(order.subtotal)}</span>
                </div>
                {order.total - (order.subtotal || 0) - (order.shipping_cost || 0) > 0 && (
                  <div className="flex justify-between" style={{ fontSize: 13, marginTop: 4 }}>
                    <span style={{ color: "var(--ink-500)" }}>PPN (11%)</span>
                    <span className="mono">{rp(order.total - (order.subtotal || 0) - (order.shipping_cost || 0))}</span>
                  </div>
                )}
                <div className="flex justify-between" style={{ fontSize: 13, marginTop: 4 }}>
                  <span style={{ color: "var(--ink-500)" }}>
                    Ongkir{order.courier ? ` (${order.courier.toUpperCase()}${order.courier_service ? " " + order.courier_service : ""})` : ""}
                  </span>
                  <span className="mono">{order.shipping_cost ? rp(order.shipping_cost) : "—"}</span>
                </div>
                <div className="flex justify-between" style={{ marginTop: 6 }}>
                  <span style={{ fontWeight: 600 }}>Total</span>
                  <span className="mono" style={{ fontSize: 16, fontWeight: 700, color: "var(--brand-700)" }}>{rp(order.total)}</span>
                </div>
              </div>
              {(order.recipient_name || order.recipient_address) && (
                <div className="px-4 py-3" style={{ borderTop: "1px solid var(--ink-150)", fontSize: 13, color: "var(--ink-700)" }}>
                  <div style={{ fontWeight: 600, marginBottom: 2 }}>📍 Penerima</div>
                  <div>{order.recipient_name}{order.recipient_phone ? ` · ${order.recipient_phone}` : ""}</div>
                  <div style={{ color: "var(--ink-500)" }}>{order.recipient_address}{order.recipient_postal ? ` (${order.recipient_postal})` : ""}</div>
                </div>
              )}
              {order.note && (
                <div className="px-4 py-3" style={{ borderTop: "1px solid var(--ink-150)", fontSize: 13, color: "var(--ink-600)" }}>
                  Catatan: {order.note}
                </div>
              )}
              <div className="px-4 py-2" style={{ fontSize: 11.5, color: "var(--ink-400)" }}>Dibuat {fmtDate(order.created_at)}</div>
            </div>

            {/* Verifikasi & status */}
            <div className="flex flex-col gap-4">
              <div className="surface surface-pad">
                <div className="mb-2" style={{ fontSize: 14, fontWeight: 600 }}>Bukti Transfer</div>
                {order.payment_proof_url ? (
                  isPdf ? (
                    <a href={order.payment_proof_url} target="_blank" rel="noopener noreferrer" className="link">Buka bukti (PDF) →</a>
                  ) : (
                    <a href={order.payment_proof_url} target="_blank" rel="noopener noreferrer">
                      {/* eslint-disable-next-line @next/next/no-img-element */}
                      <img src={order.payment_proof_url} alt="bukti" className="w-full rounded-lg ring-1" style={{ maxHeight: 280, objectFit: "contain", borderColor: "var(--ink-200)" }} />
                    </a>
                  )
                ) : (
                  <div style={{ fontSize: 13, color: "var(--ink-400)" }}>Belum ada bukti transfer.</div>
                )}
              </div>

              <div className="surface surface-pad flex flex-col gap-2">
                <div style={{ fontSize: 14, fontWeight: 600 }}>Ubah Status</div>
                {order.status === "menunggu_verifikasi" && (
                  <button onClick={() => changeStatus("diproses")} disabled={busy} className="btn btn-primary" style={{ width: "100%" }}>
                    ✓ Verifikasi & Proses
                  </button>
                )}
                <div className="flex flex-wrap gap-2">
                  <button onClick={() => changeStatus("diproses")} disabled={busy} className="btn btn-secondary btn-sm">Diproses</button>
                  <button onClick={() => changeStatus("dikirim")} disabled={busy} className="btn btn-secondary btn-sm">Dikirim</button>
                  <button onClick={() => changeStatus("selesai")} disabled={busy} className="btn btn-secondary btn-sm">Selesai</button>
                  <button onClick={() => changeStatus("batal")} disabled={busy} className="btn btn-danger btn-sm">Batal</button>
                </div>
              </div>
            </div>
          </div>
        )}
      </div>
    </AppShell>
  );
}
