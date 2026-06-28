"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import AppShell from "@/components/AppShell";
import { ApiError, getBranchOrder, setBranchOrderStatus, type OrderDetail } from "@/lib/api";
import { clearSession, getToken, getUser } from "@/lib/auth";
import { ORDER_STATUS, rp, fmtDate } from "@/lib/order-ui";
import OrderStepper from "@/components/OrderStepper";
import OrderChat from "@/components/OrderChat";

export default function BranchOrderDetailPage() {
  const router = useRouter();
  const params = useParams<{ code: string }>();
  const code = decodeURIComponent(Array.isArray(params.code) ? params.code[0] : params.code ?? "");

  const [order, setOrder] = useState<OrderDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [resi, setResi] = useState("");

  const load = useCallback(async () => {
    const token = getToken();
    if (!token) return router.replace("/login");
    if (getUser()?.role !== "user") return router.replace("/search");
    try {
      setOrder(await getBranchOrder(token, code));
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
    load();
  }, [load]);

  async function changeStatus(s: string, trackingNo?: string) {
    const token = getToken();
    if (!token) return;
    if (s === "batal" && !window.confirm("Batalkan pesanan ini?")) return;
    setBusy(true);
    setError(null);
    try {
      await setBranchOrderStatus(token, code, s, trackingNo);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Gagal ubah status");
    } finally {
      setBusy(false);
    }
  }

  const st = order ? ORDER_STATUS[order.status] || { label: order.status, pill: "" } : null;
  const itemCount = order?.items?.reduce((n, it) => n + (it.qty || 0), 0) ?? 0;

  return (
    <AppShell
      active="/cabang/pesanan"
      title={order ? order.order_code : code}
      sub={st ? st.label : "Detail pesanan"}
      actions={<Link href="/cabang/pesanan" className="btn btn-secondary btn-sm">← Pesanan Masuk</Link>}
    >
      <div className="mx-auto w-full max-w-5xl px-4 py-5 sm:px-6">
        {error && <div className="alert alert-error" style={{ marginBottom: 16 }}>{error}</div>}

        {!order ? (
          <div className="surface grid place-items-center" style={{ height: 200, color: "var(--ink-500)" }}>
            {loaded ? "Pesanan tidak ditemukan." : "Memuat…"}
          </div>
        ) : (
          <>
            {/* Stepper progres */}
            <div className="surface surface-pad mb-4">
              <OrderStepper status={order.status} />
            </div>

            <div className="grid gap-4 md:grid-cols-[1fr_320px]">
              {/* Items + info */}
              <div className="surface" style={{ overflow: "hidden" }}>
                <div className="flex flex-wrap items-center gap-2 px-4 py-3" style={{ borderBottom: "1px solid var(--ink-150)" }}>
                  <span className="mono" style={{ fontWeight: 600 }}>{order.order_code}</span>
                  <span className={"pill " + (st?.pill || "")}>{st?.label}</span>
                  <span className="grow" />
                  <span style={{ fontSize: 12, color: "var(--ink-500)" }}>{itemCount} item · {order.username}</span>
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

              {/* Aksi pesanan (kontekstual) */}
              <div className="surface surface-pad flex flex-col gap-2" style={{ height: "fit-content" }}>
                <div style={{ fontSize: 14, fontWeight: 600 }}>Proses Pesanan</div>

                {(order.status === "menunggu_pembayaran" || order.status === "menunggu_verifikasi") && (
                  <div className="alert" style={{ background: "var(--info-50)", color: "var(--info-600)", border: "1px solid #c4dceb", fontSize: 12.5 }}>
                    Menunggu pembayaran pembeli. Siapkan barang & proses setelah lunas.
                  </div>
                )}

                {order.status === "diproses" && (
                  <>
                    <div style={{ fontSize: 12.5, color: "var(--ink-500)" }}>
                      Pembayaran lunas. Siapkan & kemas barang, isi nomor resi lalu tandai dikirim.
                    </div>
                    <label className="block" style={{ fontSize: 12, color: "var(--ink-600)" }}>
                      Nomor resi {order.courier ? `(${order.courier.toUpperCase()})` : ""}
                      <input
                        className="input mono"
                        style={{ height: 34, marginTop: 4 }}
                        value={resi}
                        placeholder="mis. JX1234567890"
                        onChange={(e) => setResi(e.target.value)}
                      />
                    </label>
                    <button onClick={() => changeStatus("dikirim", resi.trim() || undefined)} disabled={busy} className="btn btn-primary" style={{ width: "100%" }}>
                      {busy ? "Memproses…" : "🚚 Tandai Dikirim"}
                    </button>
                    <button onClick={() => changeStatus("batal")} disabled={busy} className="btn btn-danger btn-sm">Batalkan pesanan</button>
                  </>
                )}

                {order.status === "dikirim" && (
                  <>
                    <div style={{ fontSize: 12.5, color: "var(--ink-500)" }}>
                      Sudah dikirim{order.tracking_no ? ` · resi ${order.tracking_no}` : ""}. Tandai selesai setelah barang diterima pembeli.
                    </div>
                    <button onClick={() => changeStatus("selesai")} disabled={busy} className="btn btn-primary" style={{ width: "100%" }}>
                      ✓ Tandai Selesai
                    </button>
                  </>
                )}

                {order.status === "selesai" && (
                  <div className="alert alert-success">Pesanan selesai. Terima kasih!</div>
                )}
                {order.status === "batal" && (
                  <div className="alert alert-error">Pesanan dibatalkan.</div>
                )}
              </div>
            </div>

            <div className="mt-4">
              <OrderChat code={code} title={`Chat dengan Pembeli ${order.username}`} />
            </div>
          </>
        )}
      </div>
    </AppShell>
  );
}
