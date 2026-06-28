"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { ApiError, getOrder, type OrderDetail } from "@/lib/api";
import { clearSession, getToken, getUser } from "@/lib/auth";
import { rp, fmtDate } from "@/lib/order-ui";
import { downloadInvoicePdf } from "@/lib/invoice-pdf";

// Invoice hanya sah setelah pesanan LUNAS (sudah dibayar & terverifikasi).
const PAID = ["diproses", "dikirim", "selesai"];

// Label metode pembayaran untuk ditampilkan di invoice.
function payLabel(o: OrderDetail): string {
  if (o.payment_method === "manual") return "Transfer Manual";
  const ch = (o.payment_channel || "").toLowerCase();
  if (!ch) return "—";
  if (ch === "qris") return "QRIS";
  if (ch.startsWith("va_")) return "Virtual Account " + ch.slice(3).toUpperCase();
  return ch.toUpperCase();
}

export default function InvoicePage() {
  const router = useRouter();
  const params = useParams<{ code: string }>();
  const code = decodeURIComponent(Array.isArray(params.code) ? params.code[0] : params.code ?? "");

  const [order, setOrder] = useState<OrderDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);

  const load = useCallback(async () => {
    const token = getToken();
    if (!token) return router.replace("/login");
    if (getUser()?.role !== "pembeli") return router.replace("/search");
    try {
      setOrder(await getOrder(token, code));
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        clearSession();
        return router.replace("/login");
      }
      setError(err instanceof Error ? err.message : "Gagal memuat pesanan");
    } finally {
      setLoaded(true);
    }
  }, [router, code]);

  useEffect(() => {
    load();
  }, [load]);

  const ppn = order ? Math.max(0, order.total - (order.subtotal || 0) - (order.shipping_cost || 0)) : 0;
  const paid = order ? PAID.includes(order.status) : false;
  const courierLabel = order?.courier
    ? order.courier.toUpperCase() + (order.courier_service ? " " + order.courier_service : "")
    : "—";

  return (
    <div style={{ minHeight: "100vh", background: "var(--canvas)" }} className="inv-page">
      {/* Toolbar (tidak ikut tercetak) */}
      <div
        className="no-print"
        style={{
          position: "sticky",
          top: 0,
          zIndex: 10,
          display: "flex",
          alignItems: "center",
          gap: 8,
          padding: "10px 16px",
          background: "var(--paper)",
          borderBottom: "1px solid var(--ink-150)",
        }}
      >
        <Link href={`/pesanan/${encodeURIComponent(code)}`} className="btn btn-secondary btn-sm">
          ← Kembali
        </Link>
        <span className="grow" />
        {order && paid && (
          <>
            <button className="btn btn-secondary btn-sm" onClick={() => window.print()}>
              🖨 Cetak
            </button>
            <button className="btn btn-primary btn-sm" onClick={() => downloadInvoicePdf(order)}>
              ⬇ Download PDF
            </button>
          </>
        )}
      </div>

      <div style={{ maxWidth: 820, margin: "0 auto", padding: "24px 16px" }}>
        {error && <div className="alert alert-error" style={{ marginBottom: 16 }}>{error}</div>}

        {!order ? (
          <div className="surface grid place-items-center" style={{ height: 200, color: "var(--ink-500)" }}>
            {loaded ? "Pesanan tidak ditemukan." : "Memuat…"}
          </div>
        ) : !paid ? (
          <div className="surface surface-pad" style={{ color: "var(--ink-700)" }}>
            <div style={{ fontWeight: 600, marginBottom: 6 }}>Invoice belum tersedia</div>
            <div style={{ fontSize: 14, color: "var(--ink-600)" }}>
              Invoice baru bisa dicetak setelah pesanan <b>lunas</b> (pembayaran terverifikasi). Status saat ini belum
              memenuhi.
            </div>
            <Link
              href={`/pesanan/${encodeURIComponent(code)}`}
              className="btn btn-secondary btn-sm"
              style={{ marginTop: 12 }}
            >
              ← Lihat detail pesanan
            </Link>
          </div>
        ) : (
          /* ── Lembar invoice ─────────────────────────────────────── */
          <div className="inv-sheet">
            {/* Header */}
            <div className="inv-head">
              <div>
                <div style={{ fontSize: 26, fontWeight: 800, color: "var(--brand-700)", letterSpacing: -0.5 }}>
                  MASPART
                </div>
                <div style={{ fontSize: 12, color: "var(--ink-500)" }}>Penyedia Suku Cadang</div>
              </div>
              <div style={{ textAlign: "right" }}>
                <div style={{ fontSize: 22, fontWeight: 700, color: "var(--ink-900)", letterSpacing: 1 }}>INVOICE</div>
                <div style={{ fontSize: 13, color: "var(--ink-600)" }}>
                  No. <b className="mono">{order.order_code}</b>
                </div>
                <div style={{ marginTop: 4 }}>
                  <span className="pill pill-brand">● LUNAS</span>
                </div>
              </div>
            </div>

            <div className="inv-rule" />

            {/* Meta pembayaran */}
            <div className="inv-meta">
              <div>
                <span className="inv-k">Tanggal Pesanan</span>
                <span className="inv-v">{fmtDate(order.created_at)}</span>
              </div>
              <div>
                <span className="inv-k">Tanggal Bayar</span>
                <span className="inv-v">{order.paid_at ? fmtDate(order.paid_at) : "—"}</span>
              </div>
              <div>
                <span className="inv-k">Metode Pembayaran</span>
                <span className="inv-v">{payLabel(order)}</span>
              </div>
            </div>

            {/* Penjual ↔ Penerima */}
            <div className="inv-parties">
              <div>
                <div className="inv-party-h">DIKIRIM DARI</div>
                <div style={{ fontWeight: 600 }}>Gudang {order.gudang || "—"}</div>
                {order.gudang_pic && (
                  <div style={{ fontSize: 12.5, color: "var(--ink-600)" }}>PIC: {order.gudang_pic}</div>
                )}
              </div>
              <div>
                <div className="inv-party-h">DITERIMA OLEH</div>
                <div style={{ fontWeight: 600 }}>{order.recipient_name || order.username || "—"}</div>
                {order.recipient_phone && (
                  <div style={{ fontSize: 12.5, color: "var(--ink-600)" }}>{order.recipient_phone}</div>
                )}
                {order.recipient_address && (
                  <div style={{ fontSize: 12.5, color: "var(--ink-600)" }}>
                    {order.recipient_address}
                    {order.recipient_postal ? ` (${order.recipient_postal})` : ""}
                  </div>
                )}
              </div>
            </div>

            {/* Items */}
            <table className="tbl inv-tbl">
              <thead>
                <tr>
                  <th style={{ width: 36 }} className="num">No</th>
                  <th>Part Number</th>
                  <th>Nama Barang</th>
                  <th className="num">Harga</th>
                  <th className="num">Qty</th>
                  <th className="num">Total</th>
                </tr>
              </thead>
              <tbody>
                {order.items.map((it, i) => (
                  <tr key={i}>
                    <td className="num">{i + 1}</td>
                    <td className="pn">{it.part_number}</td>
                    <td>{it.name}</td>
                    <td className="num mono">{rp(it.price)}</td>
                    <td className="num">{it.qty}</td>
                    <td className="num mono">{rp(it.line_total)}</td>
                  </tr>
                ))}
              </tbody>
            </table>

            {/* Ringkasan */}
            <div className="inv-totals">
              <div className="inv-trow">
                <span>Subtotal Produk</span>
                <span className="mono">{rp(order.subtotal)}</span>
              </div>
              {ppn > 0 && (
                <div className="inv-trow">
                  <span>PPN (11%)</span>
                  <span className="mono">{rp(ppn)}</span>
                </div>
              )}
              <div className="inv-trow">
                <span>Ongkos Kirim{order.courier ? ` (${courierLabel})` : ""}</span>
                <span className="mono">{order.shipping_cost ? rp(order.shipping_cost) : "—"}</span>
              </div>
              <div className="inv-trow inv-grand">
                <span>TOTAL PEMBAYARAN</span>
                <span className="mono">{rp(order.total)}</span>
              </div>
            </div>

            <div className="inv-rule" />

            {/* Pengiriman */}
            <div className="inv-ship">
              <span>
                Jasa Kirim: <b>{courierLabel}</b>
              </span>
              {order.tracking_no && (
                <span>
                  No. Resi: <b className="mono">{order.tracking_no}</b>
                </span>
              )}
            </div>

            <div className="inv-foot">Invoice ini sah dan diproses oleh komputer.</div>
          </div>
        )}
      </div>
    </div>
  );
}
