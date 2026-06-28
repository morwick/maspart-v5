// Helper tampilan status & format untuk fitur Pesanan.
export const ORDER_STATUS: Record<string, { label: string; pill: string }> = {
  menunggu_pembayaran: { label: "Menunggu Pembayaran", pill: "pill-warn" },
  menunggu_verifikasi: { label: "Menunggu Verifikasi", pill: "pill-info" },
  diproses: { label: "Diproses", pill: "pill-info" },
  dikirim: { label: "Dikirim", pill: "pill-info" },
  selesai: { label: "Selesai", pill: "pill-brand" },
  batal: { label: "Batal", pill: "pill-danger" },
};

export const ORDER_FLOW = ["diproses", "dikirim", "selesai"]; // langkah admin setelah verifikasi

// Tahapan progres pesanan (untuk stepper). `done` = milestone sudah tercapai.
export function orderProgress(status: string): { label: string; done: boolean }[] {
  const paid = ["diproses", "dikirim", "selesai"].includes(status);
  return [
    { label: "Dibayar", done: paid },
    { label: "Diproses", done: paid },
    { label: "Dikirim", done: ["dikirim", "selesai"].includes(status) },
    { label: "Selesai", done: status === "selesai" },
  ];
}

export const rp = (n: number) => "Rp " + (n || 0).toLocaleString("id-ID");

// PPN 11% (ditambahkan di atas subtotal). Harus sama dengan backend (orders.PPN_RATE).
export const PPN_RATE = 0.11;
export const ppnOf = (subtotal: number) => Math.round((subtotal || 0) * PPN_RATE);

export const fmtDate = (s?: string | null) => {
  if (!s) return "—";
  // Tambahkan 'Z' hanya bila timestamp belum punya info zona waktu
  // (tanpa 'Z' dan tanpa offset +hh:mm / -hh:mm). Supabase mengembalikan
  // timestamptz dengan offset +00:00 → jangan ditambah 'Z' (jadi invalid).
  const hasTz = /[zZ]$/.test(s) || /[+-]\d{2}:?\d{2}$/.test(s);
  const d = new Date(hasTz ? s : s + "Z");
  return isNaN(d.getTime()) ? "—" : d.toLocaleString("id-ID");
};
