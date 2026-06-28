// Pembuat invoice PDF sisi-klien (jsPDF) — sekali klik langsung terunduh,
// tanpa dialog cetak browser. Teks tetap bisa diseleksi (vektor, bukan gambar).
import { jsPDF } from "jspdf";
import autoTable from "jspdf-autotable";
import type { OrderDetail } from "@/lib/api";
import { fmtDate } from "@/lib/order-ui";

const rupiah = (n: number) => "Rp " + (n || 0).toLocaleString("id-ID");

function payLabel(o: OrderDetail): string {
  if (o.payment_method === "manual") return "Transfer Manual";
  const ch = (o.payment_channel || "").toLowerCase();
  if (!ch) return "—";
  if (ch === "qris") return "QRIS";
  if (ch.startsWith("va_")) return "Virtual Account " + ch.slice(3).toUpperCase();
  return ch.toUpperCase();
}

// Warna (RGB) selaras dengan design system MasPart.
const BRAND: [number, number, number] = [2, 106, 14]; // --brand-700
const INK900: [number, number, number] = [15, 20, 17];
const INK600: [number, number, number] = [83, 91, 86];
const INK400: [number, number, number] = [158, 165, 160];
const LINE: [number, number, number] = [225, 228, 225]; // --ink-200

export function downloadInvoicePdf(o: OrderDetail): void {
  const doc = new jsPDF({ unit: "mm", format: "a4" });
  const W = doc.internal.pageSize.getWidth();
  const M = 14; // margin kiri/kanan
  const right = W - M;
  let y = 18;

  const ppn = Math.max(0, o.total - (o.subtotal || 0) - (o.shipping_cost || 0));
  const courierLabel = o.courier
    ? o.courier.toUpperCase() + (o.courier_service ? " " + o.courier_service : "")
    : "—";

  // ── Header ─────────────────────────────────────────────
  doc.setFont("helvetica", "bold");
  doc.setFontSize(22);
  doc.setTextColor(...BRAND);
  doc.text("MASPART", M, y);
  doc.setFont("helvetica", "normal");
  doc.setFontSize(9);
  doc.setTextColor(...INK600);
  doc.text("Penyedia Suku Cadang", M, y + 5);

  doc.setFont("helvetica", "bold");
  doc.setFontSize(18);
  doc.setTextColor(...INK900);
  doc.text("INVOICE", right, y, { align: "right" });
  doc.setFont("helvetica", "normal");
  doc.setFontSize(10);
  doc.setTextColor(...INK600);
  doc.text(`No. ${o.order_code}`, right, y + 6, { align: "right" });
  doc.setFont("helvetica", "bold");
  doc.setFontSize(10);
  doc.setTextColor(...BRAND);
  doc.text("LUNAS", right, y + 11, { align: "right" });
  // Titik status digambar sebagai lingkaran vektor (font bawaan jsPDF tidak
  // mendukung karakter bullet ● → akan tampil rusak).
  const lunasW = doc.getTextWidth("LUNAS");
  doc.setFillColor(...BRAND);
  doc.circle(right - lunasW - 1.8, y + 11 - 1.1, 0.9, "F");

  y += 18;
  doc.setDrawColor(...LINE);
  doc.line(M, y, right, y);
  y += 7;

  // ── Meta pembayaran (3 kolom) ──────────────────────────
  const col = (W - 2 * M) / 3;
  const metas: [string, string][] = [
    ["Tanggal Pesanan", fmtDate(o.created_at)],
    ["Tanggal Bayar", o.paid_at ? fmtDate(o.paid_at) : "—"],
    ["Metode Pembayaran", payLabel(o)],
  ];
  metas.forEach(([k, v], i) => {
    const x = M + i * col;
    doc.setFont("helvetica", "normal");
    doc.setFontSize(8);
    doc.setTextColor(...INK400);
    doc.text(k, x, y);
    doc.setFont("helvetica", "bold");
    doc.setFontSize(9.5);
    doc.setTextColor(...INK900);
    doc.text(v, x, y + 4.5);
  });
  y += 13;

  // ── Penjual ↔ Penerima ─────────────────────────────────
  const colR = M + (W - 2 * M) / 2;
  const recipient = [
    o.recipient_name || o.username || "—",
    o.recipient_phone || "",
    [o.recipient_address, o.recipient_postal ? `(${o.recipient_postal})` : ""].filter(Boolean).join(" "),
  ].filter(Boolean);
  const sender = [`Gudang ${o.gudang || "—"}`, o.gudang_pic ? `PIC: ${o.gudang_pic}` : ""].filter(Boolean);

  const party = (x: number, head: string, lines: string[], maxW: number) => {
    doc.setFont("helvetica", "bold");
    doc.setFontSize(8);
    doc.setTextColor(...INK400);
    doc.text(head, x, y);
    let yy = y + 5;
    lines.forEach((ln, idx) => {
      doc.setFont("helvetica", idx === 0 ? "bold" : "normal");
      doc.setFontSize(idx === 0 ? 10 : 9);
      doc.setTextColor(...(idx === 0 ? INK900 : INK600));
      const wrapped = doc.splitTextToSize(ln, maxW);
      doc.text(wrapped, x, yy);
      yy += wrapped.length * 4.5;
    });
    return yy;
  };
  const yL = party(M, "DIKIRIM DARI", sender, col - 4);
  const yR = party(colR, "DITERIMA OLEH", recipient, W - colR - M);
  y = Math.max(yL, yR) + 4;

  // ── Tabel item ─────────────────────────────────────────
  autoTable(doc, {
    startY: y,
    margin: { left: M, right: M },
    head: [["No", "Part Number", "Nama Barang", "Harga", "Qty", "Total"]],
    body: o.items.map((it, i) => [
      String(i + 1),
      it.part_number,
      it.name,
      rupiah(it.price),
      String(it.qty),
      rupiah(it.line_total),
    ]),
    styles: { fontSize: 8.5, cellPadding: 2, textColor: INK900, lineColor: LINE, lineWidth: 0.1 },
    headStyles: { fillColor: [243, 245, 243], textColor: INK600, fontStyle: "bold" },
    columnStyles: {
      0: { halign: "right", cellWidth: 10 },
      3: { halign: "right" },
      4: { halign: "right", cellWidth: 12 },
      5: { halign: "right" },
    },
    theme: "grid",
  });

  const finalY = (doc as unknown as { lastAutoTable?: { finalY: number } }).lastAutoTable?.finalY;
  y = (finalY ?? y) + 8;

  // ── Ringkasan total (rata kanan) ───────────────────────
  const boxW = 75;
  const xK = right - boxW;
  const trow = (label: string, val: string, grand = false) => {
    doc.setFont("helvetica", grand ? "bold" : "normal");
    doc.setFontSize(grand ? 11 : 9.5);
    doc.setTextColor(...(grand ? INK900 : INK600));
    doc.text(label, xK, y);
    doc.setTextColor(...(grand ? BRAND : INK900));
    doc.text(val, right, y, { align: "right" });
    y += grand ? 7 : 5.5;
  };
  trow("Subtotal Produk", rupiah(o.subtotal));
  if (ppn > 0) trow("PPN (11%)", rupiah(ppn));
  trow(`Ongkos Kirim${o.courier ? ` (${courierLabel})` : ""}`, o.shipping_cost ? rupiah(o.shipping_cost) : "—");
  doc.setDrawColor(...LINE);
  doc.line(xK, y - 2, right, y - 2);
  y += 1.5;
  trow("TOTAL PEMBAYARAN", rupiah(o.total), true);

  // ── Pengiriman + footer ────────────────────────────────
  y += 4;
  doc.setDrawColor(...LINE);
  doc.line(M, y, right, y);
  y += 6;
  doc.setFont("helvetica", "normal");
  doc.setFontSize(9);
  doc.setTextColor(...INK600);
  doc.text(`Jasa Kirim: ${courierLabel}`, M, y);
  if (o.tracking_no) doc.text(`No. Resi: ${o.tracking_no}`, right, y, { align: "right" });

  y += 8;
  doc.setFont("helvetica", "italic");
  doc.setFontSize(8);
  doc.setTextColor(...INK400);
  doc.text("Invoice ini sah dan diproses oleh komputer.", W / 2, y, { align: "center" });

  doc.save(`Invoice-${o.order_code}.pdf`);
}
