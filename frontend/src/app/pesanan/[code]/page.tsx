"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import AppShell from "@/components/AppShell";
import { ApiError, cancelOrder, confirmOrder, geoReverse, getOrder, getPaymentStatus, uploadProof, type OrderDetail } from "@/lib/api";
import { clearSession, getToken, getUser } from "@/lib/auth";
import { ORDER_STATUS, rp, fmtDate } from "@/lib/order-ui";
import OrderStepper from "@/components/OrderStepper";
import OrderChat from "@/components/OrderChat";

export default function OrderDetailPage() {
  const router = useRouter();
  const params = useParams<{ code: string }>();
  const code = decodeURIComponent(Array.isArray(params.code) ? params.code[0] : params.code ?? "");

  const [order, setOrder] = useState<OrderDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [senderPlace, setSenderPlace] = useState<string | null>(null);

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

  // Ubah koordinat gudang pengirim → nama lokasi (reverse geocode, di-cache).
  useEffect(() => {
    const lat = order?.gudang_lat;
    const lon = order?.gudang_lon;
    if (lat == null || lon == null) return;
    const token = getToken();
    if (!token) return;
    const key = `maspart_geo_${lat},${lon}`;
    try {
      const cached = sessionStorage.getItem(key);
      if (cached) {
        setSenderPlace(cached);
        return;
      }
    } catch {
      /* ignore */
    }
    let alive = true;
    geoReverse(token, lat, lon)
      .then((p) => {
        const name = p.address || p.display_name || "";
        if (alive && name) {
          setSenderPlace(name);
          try {
            sessionStorage.setItem(key, name);
          } catch {
            /* ignore */
          }
        }
      })
      .catch(() => {});
    return () => {
      alive = false;
    };
  }, [order?.gudang_lat, order?.gudang_lon]);

  const [checking, setChecking] = useState(false);
  // Aksi pembeli: konfirmasi terima / batal / upload bukti. Nilai = aksi yang berjalan.
  const [busy, setBusy] = useState<"confirm" | "cancel" | "proof" | null>(null);

  const doConfirm = useCallback(async () => {
    const token = getToken();
    if (!token) return;
    if (!window.confirm("Konfirmasi bahwa barang sudah Anda terima? Pesanan akan ditandai selesai.")) return;
    setBusy("confirm");
    setError(null);
    try {
      await confirmOrder(token, code);
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Gagal mengonfirmasi penerimaan.");
    } finally {
      setBusy(null);
    }
  }, [code, load]);

  const doCancel = useCallback(async () => {
    const token = getToken();
    if (!token) return;
    if (!window.confirm("Batalkan pesanan ini? Pastikan Anda BELUM melakukan pembayaran — membatalkan setelah transfer dapat membuat dana tertahan.")) return;
    setBusy("cancel");
    setError(null);
    try {
      await cancelOrder(token, code);
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Gagal membatalkan pesanan.");
    } finally {
      setBusy(null);
    }
  }, [code, load]);

  const doUploadProof = useCallback(async (file: File) => {
    const token = getToken();
    if (!token) return;
    setBusy("proof");
    setError(null);
    try {
      await uploadProof(token, code, file);
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Gagal mengunggah bukti.");
    } finally {
      setBusy(null);
    }
  }, [code, load]);

  const checkPayment = useCallback(async () => {
    const token = getToken();
    if (!token) return;
    setChecking(true);
    try {
      const r = await getPaymentStatus(token, code);
      if (r.paid) await load();
    } catch {
      /* abaikan */
    } finally {
      setChecking(false);
    }
  }, [code, load]);

  // Auto-poll status saat pembayaran gateway masih menunggu.
  useEffect(() => {
    if (order?.payment_method !== "gateway" || order?.status !== "menunggu_pembayaran") return;
    const id = setInterval(checkPayment, 8000);
    return () => clearInterval(id);
  }, [order?.payment_method, order?.status, checkPayment]);

  const st = order ? ORDER_STATUS[order.status] || { label: order.status, pill: "" } : null;

  return (
    <AppShell
      active="/pesanan"
      title={order ? order.order_code : code}
      sub={st ? st.label : "Detail pesanan"}
      actions={
        <div className="flex items-center gap-2">
          {order && ["diproses", "dikirim", "selesai"].includes(order.status) && (
            <Link
              href={`/pesanan/${encodeURIComponent(order.order_code)}/invoice`}
              className="btn btn-primary btn-sm"
            >
              🧾 Cetak Invoice
            </Link>
          )}
          <Link href="/pesanan" className="btn btn-secondary btn-sm">← Pesanan Saya</Link>
        </div>
      }
    >
      <div className="mx-auto w-full max-w-4xl px-4 py-5 sm:px-6">
        {error && <div className="alert alert-error" style={{ marginBottom: 16 }}>{error}</div>}

        {!order ? (
          <div className="surface grid place-items-center" style={{ height: 200, color: "var(--ink-500)" }}>
            {loaded ? "Pesanan tidak ditemukan." : "Memuat…"}
          </div>
        ) : (
          <>
            {/* Stepper progres pesanan */}
            <div className="surface surface-pad mb-4">
              <OrderStepper status={order.status} />
            </div>

            <div className="grid gap-4 md:grid-cols-[1fr_320px]">
            {/* Items */}
            <div className="surface" style={{ overflow: "hidden" }}>
              <div className="flex items-center gap-2 px-4 py-3" style={{ borderBottom: "1px solid var(--ink-150)" }}>
                <span className="mono" style={{ fontWeight: 600 }}>{order.order_code}</span>
                <span className={"pill " + (st?.pill || "")}>{st?.label}</span>
                <span className="grow" />
                <span style={{ fontSize: 12, color: "var(--ink-500)" }}>{order.gudang}</span>
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
              {order.tracking_no && (
                <div className="px-4 py-3" style={{ borderTop: "1px solid var(--ink-150)", fontSize: 13, color: "var(--ink-700)" }}>
                  <div style={{ fontWeight: 600, marginBottom: 2 }}>🚚 Pengiriman</div>
                  <div className="flex items-center gap-2">
                    <span>No. Resi:</span>
                    <b className="mono">{order.tracking_no}</b>
                    {order.courier && <span style={{ color: "var(--ink-500)" }}>({order.courier.toUpperCase()}{order.courier_service ? " " + order.courier_service : ""})</span>}
                    <button className="btn btn-secondary btn-sm" onClick={() => navigator.clipboard?.writeText(order.tracking_no || "")}>Salin</button>
                  </div>
                  <a
                    className="link"
                    style={{ fontSize: 12 }}
                    href={`https://cekresi.com/?noresi=${encodeURIComponent(order.tracking_no)}`}
                    target="_blank"
                    rel="noopener noreferrer"
                  >
                    Lacak paket →
                  </a>
                </div>
              )}
              {order.gudang && (
                <div className="px-4 py-3" style={{ borderTop: "1px solid var(--ink-150)", fontSize: 13, color: "var(--ink-700)" }}>
                  <div style={{ fontWeight: 600, marginBottom: 2 }}>📦 Lokasi Pengirim</div>
                  <div>Gudang <b>{order.gudang}</b></div>
                  {order.gudang_lat != null && order.gudang_lon != null && (
                    <div style={{ color: "var(--ink-500)", fontSize: 12, marginTop: 2 }}>
                      Lokasi: {senderPlace || "memuat lokasi…"}{" · "}
                      <a
                        className="link"
                        href={`https://www.google.com/maps/search/?api=1&query=${order.gudang_lat},${order.gudang_lon}`}
                        target="_blank"
                        rel="noopener noreferrer"
                      >
                        Lihat di peta →
                      </a>
                    </div>
                  )}
                  {order.gudang_pic && (
                    <div style={{ fontSize: 12, marginTop: 2 }}>
                      PIC Gudang: <a className="link mono" href={`tel:${order.gudang_pic.replace(/[^\d+]/g, "")}`}>{order.gudang_pic}</a>
                    </div>
                  )}
                  <div style={{ color: "var(--ink-500)", fontSize: 12 }}>
                    Dipilih otomatis sesuai ketersediaan stok.
                  </div>
                </div>
              )}
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
              <div className="px-4 py-2" style={{ fontSize: 11.5, color: "var(--ink-400)" }}>
                Dibuat {fmtDate(order.created_at)} · oleh {order.username}
              </div>
            </div>

            {/* Pembayaran */}
            <div className="surface surface-pad flex flex-col gap-3" style={{ height: "fit-content" }}>
              <div style={{ fontSize: 14, fontWeight: 600 }}>Pembayaran</div>

              {/* GATEWAY: VA / QRIS otomatis */}
              {order.payment_method === "gateway" && order.status === "menunggu_pembayaran" && (
                <>
                  <div style={{ fontSize: 13, color: "var(--ink-700)", lineHeight: 1.6 }}>
                    Bayar <b className="mono">{rp(order.total)}</b>
                    {order.payment_channel ? ` via ${order.payment_channel.toUpperCase()}` : ""}:
                  </div>

                  {order.payment_va && (
                    <div className="rounded-lg p-3" style={{ background: "var(--ink-50)" }}>
                      <div style={{ fontSize: 11.5, color: "var(--ink-500)" }}>Nomor Virtual Account</div>
                      <div className="flex items-center justify-between gap-2">
                        <b className="mono" style={{ fontSize: 18 }}>{order.payment_va}</b>
                        <button className="btn btn-secondary btn-sm" onClick={() => navigator.clipboard?.writeText(order.payment_va || "")}>Salin</button>
                      </div>
                    </div>
                  )}

                  {order.payment_qr && (
                    <div className="rounded-lg p-3 text-center" style={{ background: "var(--ink-50)" }}>
                      <div className="mb-2" style={{ fontSize: 11.5, color: "var(--ink-500)" }}>Scan QRIS</div>
                      {/^https?:\/\//.test(order.payment_qr) ? (
                        // eslint-disable-next-line @next/next/no-img-element
                        <img src={order.payment_qr} alt="QRIS" style={{ width: 200, height: 200, objectFit: "contain", margin: "0 auto" }} />
                      ) : (
                        <code style={{ fontSize: 10, wordBreak: "break-all", color: "var(--ink-600)" }}>{order.payment_qr}</code>
                      )}
                    </div>
                  )}

                  {order.payment_url && (
                    <a href={order.payment_url} target="_blank" rel="noopener noreferrer" className="btn btn-primary" style={{ width: "100%" }}>
                      Buka Halaman Pembayaran →
                    </a>
                  )}

                  {order.payment_expiry && (
                    <div style={{ fontSize: 11.5, color: "var(--ink-500)" }}>Batas bayar: {fmtDate(order.payment_expiry)}</div>
                  )}

                  <button onClick={checkPayment} disabled={checking} className="btn btn-secondary btn-sm">
                    {checking ? "Mengecek…" : "Cek Status Pembayaran"}
                  </button>
                  <div className="alert" style={{ background: "var(--info-50)", color: "var(--info-600)", border: "1px solid #c4dceb", fontSize: 12 }}>
                    Status diperbarui otomatis setelah pembayaran masuk.
                  </div>
                </>
              )}

              {order.status === "menunggu_verifikasi" && (
                <div className="alert" style={{ background: "var(--info-50)", color: "var(--info-600)", border: "1px solid #c4dceb" }}>
                  Menunggu verifikasi pembayaran.
                </div>
              )}

              {["diproses", "dikirim", "selesai"].includes(order.status) && (
                <div className="alert alert-success">Pembayaran terverifikasi. Status: {st?.label}.</div>
              )}
              {order.status === "batal" && <div className="alert alert-error">Pesanan dibatalkan.</div>}

              {/* Aksi pembeli: konfirmasi terima barang (saat dikirim) */}
              {order.status === "dikirim" && (
                <button className="btn btn-primary" onClick={doConfirm} disabled={busy !== null}>
                  {busy === "confirm" ? "Memproses…" : "✓ Konfirmasi Terima Barang"}
                </button>
              )}

              {/* Upload bukti transfer + batalkan (saat belum lunas) */}
              {["menunggu_pembayaran", "menunggu_verifikasi"].includes(order.status) && (
                <>
                  {order.payment_proof_url && (
                    <a className="link" style={{ fontSize: 12 }} href={order.payment_proof_url} target="_blank" rel="noopener noreferrer">
                      Lihat bukti yang sudah dikirim →
                    </a>
                  )}
                  <label className="btn btn-secondary btn-sm" style={{ cursor: busy ? "default" : "pointer" }}>
                    {busy === "proof" ? "Mengunggah…" : "📎 Upload Bukti Transfer"}
                    <input
                      type="file"
                      accept="image/*,.pdf"
                      hidden
                      disabled={busy !== null}
                      onChange={(e) => {
                        const f = e.target.files?.[0];
                        if (f) doUploadProof(f);
                        e.target.value = "";
                      }}
                    />
                  </label>
                  <button
                    className="btn btn-ghost btn-sm"
                    style={{ color: "var(--danger-600)" }}
                    onClick={doCancel}
                    disabled={busy !== null}
                  >
                    {busy === "cancel" ? "Membatalkan…" : "Batalkan Pesanan"}
                  </button>
                </>
              )}
            </div>
            </div>

            <div className="mt-4">
              <OrderChat code={code} title={`Chat dengan Gudang ${order.gudang || ""}`} />
            </div>
          </>
        )}
      </div>
    </AppShell>
  );
}
