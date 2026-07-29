// Klien API tipis untuk backend FastAPI MASPART.

import { setLogoutReason } from "./auth";

export const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE?.replace(/\/$/, "") || "http://127.0.0.1:8001";

export type UserOut = { username: string; role: string; gudang?: string | null };

export type TokenResponse = {
  access_token: string;
  token_type: string;
  expires_in: number;
  user: UserOut;
};

export type PartResult = {
  file: string;
  path: string;
  sheet: string;
  part_number: string;
  part_name: string;
  quantity: string;
  stok: string;
  harga: string;
  gudang: Record<string, number>;
  excel_row: number;
  source?: string; // "" = database lokal, "sims" = nama diambil dari SIMS
  berat?: number;  // berat per item (gram); 0/undefined = belum ditetapkan
};

export type SaranPart = {
  part_number: string;
  part_name: string;
};

export type SearchResponse = {
  term: string;
  count: number;
  page: number;
  page_size: number;
  total_pages: number;
  results: PartResult[];
  saran?: SaranPart[]; // "mungkin maksud Anda" — hanya saat 0 hasil
};

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function parseError(res: Response): Promise<string> {
  let msg = `HTTP ${res.status}`;
  try {
    const data = await res.json();
    if (typeof data?.detail === "string") msg = data.detail;
    else if (Array.isArray(data?.detail))
      msg = data.detail.map((d: { msg?: string }) => d.msg).join(", ");
  } catch {
    /* ignore */
  }
  // 401 karena akun dipakai di perangkat lain → simpan alasannya supaya halaman
  // login bisa menjelaskan, bukan sekadar melempar user tanpa keterangan.
  // (auth.ts hanya mengimpor TYPE dari sini, jadi tak ada siklus saat runtime.)
  if (res.status === 401 && /perangkat lain/i.test(msg)) {
    setLogoutReason(msg);
  }
  return msg;
}

/** Gambar SIMS pakai http://. Saat situs dibuka via HTTPS, browser memblokir gambar
 *  http (mixed content). Lewatkan ke proxy backend (same-origin) agar tetap tampil. */
export function partImageUrl(url: string): string {
  if (typeof url === "string" && url.startsWith("http://")) {
    return `${API_BASE}/api/parts/image-proxy?url=${encodeURIComponent(url)}`;
  }
  // Foto galeri belajar (Cari by Foto) — disajikan backend dari data/learned_photos.
  if (typeof url === "string" && url.startsWith("learned://")) {
    return `${API_BASE}/api/parts/learned-photo/${encodeURIComponent(url.slice("learned://".length))}`;
  }
  return url;
}

export async function login(username: string, password: string): Promise<TokenResponse> {
  const res = await fetch(`${API_BASE}/api/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  });
  if (!res.ok) throw new ApiError(res.status, await parseError(res));
  return res.json();
}

export type SearchMode = "pn" | "name";

export async function searchParts(
  q: string,
  token: string,
  mode: SearchMode = "pn",
  page = 1,
  pageSize = 20,
): Promise<SearchResponse> {
  const path = mode === "name" ? "search-name" : "search";
  const qs = new URLSearchParams({
    q,
    page: String(page),
    page_size: String(pageSize),
  });
  const res = await fetch(`${API_BASE}/api/parts/${path}?${qs}`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!res.ok) throw new ApiError(res.status, await parseError(res));
  return res.json();
}

export type ImageMatch = {
  part_number: string;
  part_name: string;
  sims_url: string;
  similarity: number;
  raw_similarity: number;
  n_matches: number;
  n_strong: number;
  boost: number;
  distance: number;
  stok: string;
  harga: string;
  tersedia: boolean;
};

export type ImageSearchResponse = {
  count: number;
  results: ImageMatch[];
  galeri_total: number;
  galeri_parts: number;
  pesan?: string | null;
};

export async function searchByImage(
  file: File,
  token: string,
  opts: { topK?: number; threshold?: number; useTta?: boolean } = {},
): Promise<ImageSearchResponse> {
  const qs = new URLSearchParams({
    top_k: String(opts.topK ?? 12),
    threshold: String(opts.threshold ?? 0.3),
    use_tta: String(opts.useTta ?? true),
  });
  const form = new FormData();
  form.append("file", file);
  const res = await fetch(`${API_BASE}/api/parts/search-image?${qs}`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}` },
    body: form,
  });
  if (!res.ok) throw new ApiError(res.status, await parseError(res));
  return res.json();
}

export type ImageLearnResponse = {
  ok: boolean;
  pn: string;
  duplikat: boolean;
  galeri_total: number;
};

/** GALERI BELAJAR (admin): konfirmasi "foto ini = PN X" → foto diindeks ke galeri
 *  sehingga pencarian foto lapangan serupa berikutnya makin akurat. */
export async function learnImageMatch(
  file: File,
  pn: string,
  token: string,
): Promise<ImageLearnResponse> {
  const form = new FormData();
  form.append("file", file);
  form.append("pn", pn);
  const res = await fetch(`${API_BASE}/api/parts/search-image/learn`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}` },
    body: form,
  });
  if (!res.ok) throw new ApiError(res.status, await parseError(res));
  return res.json();
}

// ── Bandingkan 2 Part ───────────────────────────────────────────────
export type CompareBest = {
  shape_score: number;
  color_score: number;
  name_score: number | null;
  overall: number;
  verdict: string;
  color: string;
  i: number;
  j: number;
};

export type CompareResponse = {
  pn1: string;
  pn2: string;
  name1: string;
  name2: string;
  urls1: string[];
  urls2: string[];
  best: CompareBest | null;
  error: string | null;
};

export async function comparePartsApi(
  token: string,
  pn1: string,
  pn2: string,
): Promise<CompareResponse> {
  const qs = new URLSearchParams({ pn1, pn2 });
  const res = await fetch(`${API_BASE}/api/parts/compare?${qs}`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!res.ok) throw new ApiError(res.status, await parseError(res));
  return res.json();
}

// ── Pesanan (order internal) ────────────────────────────────────────
export type OrderItemDetail = {
  part_number: string;
  name: string;
  price: number;
  qty: number;
  line_total: number;
};
export type OrderSummary = {
  order_code: string;
  username: string;
  gudang: string;
  total: number;
  status: string;
  payment_proof_url?: string | null;
  created_at: string;
};
export type OrderDetail = OrderSummary & {
  note?: string | null;
  gudang_lat?: number | null;
  gudang_lon?: number | null;
  gudang_pic?: string | null;
  subtotal: number;
  tax?: number | null;        // komponen PPN 12% yang SUDAH termasuk dalam subtotal
  shipping_cost?: number;
  courier?: string | null;
  courier_service?: string | null;
  tracking_no?: string | null;
  weight_grams?: number;
  payment_method?: string;
  payment_ref?: string | null;
  payment_channel?: string | null;
  payment_va?: string | null;
  payment_qr?: string | null;
  payment_url?: string | null;
  payment_expiry?: string | null;
  paid_at?: string | null;
  recipient_name?: string | null;
  recipient_phone?: string | null;
  recipient_address?: string | null;
  recipient_postal?: string | null;
  fulfill_gudang?: string | null;     // gudang FISIK pengirim (beda dari `gudang` = cabang pemroses)
  payment_note?: string | null;       // mis. dibayar setelah order batal → perlu refund
  penawaran_status?: string | null;   // created | skip | failed (Penawaran Accurate otomatis)
  penawaran_number?: string | null;   // mis. 'MASPART-07'
  penawaran_note?: string | null;     // alasan skip / pesan gagal
  items: OrderItemDetail[];
};

export type PaymentInfo = {
  ref?: string;
  channel?: string;
  va?: string | null;
  qr?: string | null;
  url?: string | null;
  expiry?: string | null;
  status?: string;
};

export type PaymentChannel = { code: string; label: string };

export async function getPaymentMethods(
  token: string,
): Promise<{ gateway_available: boolean; channels: PaymentChannel[] }> {
  const res = await fetch(`${API_BASE}/api/payments/methods`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!res.ok) throw new ApiError(res.status, await parseError(res));
  return res.json();
}

export async function getPaymentStatus(
  token: string,
  code: string,
): Promise<{ status: string; paid: boolean; gateway_status?: string; error?: string }> {
  const res = await fetch(`${API_BASE}/api/orders/${encodeURIComponent(code)}/payment/status`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!res.ok) throw new ApiError(res.status, await parseError(res));
  return res.json();
}

export type ShippingRate = {
  courier: string;
  courier_name: string;
  service: string;
  price: number;
  etd: string;
};

export type GeoPlace = { lat: number; lon: number; address: string; postal: string; display_name: string };

export async function geoReverse(token: string, lat: number, lon: number): Promise<GeoPlace> {
  const qs = new URLSearchParams({ lat: String(lat), lon: String(lon) });
  const res = await fetch(`${API_BASE}/api/geo/reverse?${qs}`, { headers: { Authorization: `Bearer ${token}` } });
  if (!res.ok) throw new ApiError(res.status, await parseError(res));
  return res.json();
}

export async function geoSearch(token: string, q: string): Promise<{ results: (GeoPlace & { label: string })[] }> {
  const res = await fetch(`${API_BASE}/api/geo/search?q=${encodeURIComponent(q)}`, { headers: { Authorization: `Bearer ${token}` } });
  if (!res.ok) throw new ApiError(res.status, await parseError(res));
  return res.json();
}

export async function getCartWeight(
  token: string,
  items: { part_number: string; qty: number }[],
): Promise<{ weight_grams: number; default_item_grams: number }> {
  const res = await fetch(`${API_BASE}/api/shipping/weight`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
    body: JSON.stringify({ items }),
  });
  if (!res.ok) throw new ApiError(res.status, await parseError(res));
  return res.json();
}

export type CartGudang = {
  items: {
    part_number: string;
    gudang: string;          // gudang pengirim ('' bila tak ada stok)
    harga: number;           // harga TERKINI dari server (yang akan ditagih)
    harga_display: string;
    berat: number;
    bisa_dibeli: boolean;
    alasan: string;          // 'harga belum tersedia' | 'berat belum ditetapkan' | 'stok habis'
  }[];
  utama: string;      // gudang pengirim utama (asal ongkir)
  multi: boolean;     // keranjang terpecah ke >1 gudang → lebih dari satu paket
};

/** Keadaan terkini tiap item keranjang menurut SERVER (gudang pengirim, harga, berat,
 *  bisa dibeli atau tidak). Keranjang di browser menyimpan harga saat part dimasukkan —
 *  bisa basi — jadi halaman keranjang wajib menyegarkan dirinya dari sini. */
export async function getCartGudang(
  token: string,
  items: { part_number: string; qty: number }[],
): Promise<CartGudang> {
  const res = await fetch(`${API_BASE}/api/cart/gudang`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
    body: JSON.stringify({ items }),
  });
  if (!res.ok) throw new ApiError(res.status, await parseError(res));
  return res.json();
}

export async function getShippingRates(
  token: string,
  weightGrams: number,
  value = 0,
  destPostal = "",
  items: { part_number: string; qty: number }[] = [],
): Promise<{ rates: ShippingRate[]; error: string | null; available: boolean }> {
  // POST: item keranjang dikirim agar server menghitung ongkir dari gudang PEMENUH
  // (bukan sekadar gudang pilihan pembeli) → ongkir preview = ongkir saat order.
  const res = await fetch(`${API_BASE}/api/shipping/rates`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
    body: JSON.stringify({ weight_grams: weightGrams, value, dest_postal: destPostal, items }),
  });
  if (!res.ok) throw new ApiError(res.status, await parseError(res));
  return res.json();
}

export async function createOrder(
  token: string,
  body: {
    note?: string;
    items: { part_number: string; qty: number; name?: string }[];
    courier?: string;
    courier_service?: string;
    shipping_cost?: number;
    weight_grams?: number;
    payment_method?: string;
    payment_channel?: string;
    recipient_name?: string;
    recipient_phone?: string;
    recipient_address?: string;
    recipient_postal?: string;
  },
): Promise<{ order_code: string; total: number; status: string; payment_method?: string; payment?: PaymentInfo }> {
  const res = await fetch(`${API_BASE}/api/orders`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new ApiError(res.status, await parseError(res));
  return res.json();
}

export async function getMyOrders(token: string): Promise<{ orders: OrderSummary[] }> {
  const res = await fetch(`${API_BASE}/api/orders`, { headers: { Authorization: `Bearer ${token}` } });
  if (!res.ok) throw new ApiError(res.status, await parseError(res));
  return res.json();
}

export async function getOrder(token: string, code: string): Promise<OrderDetail> {
  const res = await fetch(`${API_BASE}/api/orders/${encodeURIComponent(code)}`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!res.ok) throw new ApiError(res.status, await parseError(res));
  return res.json();
}

export async function confirmOrder(token: string, code: string): Promise<{ ok: boolean }> {
  const res = await fetch(`${API_BASE}/api/orders/${encodeURIComponent(code)}/confirm`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!res.ok) throw new ApiError(res.status, await parseError(res));
  return res.json();
}

export async function cancelOrder(token: string, code: string): Promise<{ ok: boolean }> {
  const res = await fetch(`${API_BASE}/api/orders/${encodeURIComponent(code)}/cancel`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!res.ok) throw new ApiError(res.status, await parseError(res));
  return res.json();
}

export async function uploadProof(token: string, code: string, file: File): Promise<{ url: string }> {
  const form = new FormData();
  form.append("file", file);
  const res = await fetch(`${API_BASE}/api/orders/${encodeURIComponent(code)}/proof`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}` },
    body: form,
  });
  if (!res.ok) throw new ApiError(res.status, await parseError(res));
  return res.json();
}

export async function getAdminOrders(token: string): Promise<{ orders: OrderSummary[] }> {
  const res = await fetch(`${API_BASE}/api/admin/orders`, { headers: { Authorization: `Bearer ${token}` } });
  if (!res.ok) throw new ApiError(res.status, await parseError(res));
  return res.json();
}

export async function getAdminOrder(token: string, code: string): Promise<OrderDetail> {
  const res = await fetch(`${API_BASE}/api/admin/orders/${encodeURIComponent(code)}`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!res.ok) throw new ApiError(res.status, await parseError(res));
  return res.json();
}

export async function setOrderStatus(token: string, code: string, status: string): Promise<void> {
  const res = await fetch(`${API_BASE}/api/admin/orders/${encodeURIComponent(code)}/status`, {
    method: "PUT",
    headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
    body: JSON.stringify({ status }),
  });
  if (!res.ok) throw new ApiError(res.status, await parseError(res));
}

// ── Harga ───────────────────────────────────────────────────────────
export type HargaListResponse = {
  total: number;
  total_filtered: number;
  page: number;
  page_size: number;
  total_pages: number;
  rows: Record<string, string>[];
};

export type CariHargaResult = {
  pn: string;
  cny: number | null;
  idr: number | null;
  rate: number;
  note: string | null;
};

export type BatchHargaRow = {
  pn: string;
  cny: number | null;
  idr: number | null;
  note: string | null;
  status: string;
};

export type BatchHargaResponse = {
  rate: number;
  count: number;
  found: number;
  results: BatchHargaRow[];
};

export async function getHargaList(
  token: string,
  opts: { q?: string; sort?: string; page?: number; pageSize?: number } = {},
): Promise<HargaListResponse> {
  const qs = new URLSearchParams({
    q: opts.q ?? "",
    sort: opts.sort ?? "pn",
    page: String(opts.page ?? 1),
    page_size: String(opts.pageSize ?? 50),
  });
  const res = await fetch(`${API_BASE}/api/harga/list?${qs}`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!res.ok) throw new ApiError(res.status, await parseError(res));
  return res.json();
}

export async function exportHargaList(
  token: string,
  opts: { q?: string; sort?: string } = {},
): Promise<Blob> {
  const qs = new URLSearchParams({ q: opts.q ?? "", sort: opts.sort ?? "pn" });
  const res = await fetch(`${API_BASE}/api/harga/list/export?${qs}`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!res.ok) throw new ApiError(res.status, await parseError(res));
  return res.blob();
}

export async function cariHarga(
  token: string,
  pn: string,
  refresh = false,
): Promise<CariHargaResult> {
  const qs = new URLSearchParams({ pn, refresh: String(refresh) });
  const res = await fetch(`${API_BASE}/api/harga/cari?${qs}`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!res.ok) throw new ApiError(res.status, await parseError(res));
  return res.json();
}

export async function batchHarga(token: string, text: string): Promise<BatchHargaResponse> {
  const res = await fetch(`${API_BASE}/api/harga/batch`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
    body: JSON.stringify({ text }),
  });
  if (!res.ok) throw new ApiError(res.status, await parseError(res));
  return res.json();
}

export async function exportBatchHarga(
  token: string,
  rate: number,
  rows: BatchHargaRow[],
): Promise<Blob> {
  const res = await fetch(`${API_BASE}/api/harga/batch/export`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
    body: JSON.stringify({ rate, rows }),
  });
  if (!res.ok) throw new ApiError(res.status, await parseError(res));
  return res.blob();
}

// ── Stok (Accurate live) ────────────────────────────────────────────
export type StokListResponse = {
  configured?: boolean;
  session_expired?: boolean;
  error?: boolean;
  reason?: string;
  total: number;
  total_filtered: number;
  page: number;
  page_size: number;
  total_pages: number;
  rows: Record<string, string>[];
};

export async function getStokList(
  token: string,
  opts: { q?: string; sort?: string; page?: number; pageSize?: number } = {},
): Promise<StokListResponse> {
  const qs = new URLSearchParams({
    q: opts.q ?? "",
    sort: opts.sort ?? "pn",
    page: String(opts.page ?? 1),
    page_size: String(opts.pageSize ?? 50),
  });
  const res = await fetch(`${API_BASE}/api/stok/list?${qs}`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!res.ok) throw new ApiError(res.status, await parseError(res));
  return res.json();
}

export async function exportStokList(
  token: string,
  opts: { q?: string; sort?: string } = {},
): Promise<Blob> {
  const qs = new URLSearchParams({ q: opts.q ?? "", sort: opts.sort ?? "pn" });
  const res = await fetch(`${API_BASE}/api/stok/list/export?${qs}`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!res.ok) throw new ApiError(res.status, await parseError(res));
  return res.blob();
}

// ── Populasi Unit ───────────────────────────────────────────────────
export type PopulasiResponse = {
  columns: string[];
  filter_options: Record<string, string[]>;
  total: number;
  total_filtered: number;
  page: number;
  page_size: number;
  total_pages: number;
  rows: Record<string, string>[];
};

export async function getPopulasi(
  token: string,
  opts: {
    q?: string;
    filters?: Record<string, string>;
    page?: number;
    pageSize?: number;
    sort?: string;
    dir?: "asc" | "desc";
  } = {},
): Promise<PopulasiResponse> {
  const qs = new URLSearchParams({
    q: opts.q ?? "",
    page: String(opts.page ?? 1),
    page_size: String(opts.pageSize ?? 50),
  });
  if (opts.filters && Object.keys(opts.filters).length)
    qs.set("filters", JSON.stringify(opts.filters));
  if (opts.sort) {
    qs.set("sort", opts.sort);
    qs.set("dir", opts.dir ?? "asc");
  }
  const res = await fetch(`${API_BASE}/api/populasi?${qs}`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!res.ok) throw new ApiError(res.status, await parseError(res));
  return res.json();
}

export async function getPopulasiKolom(
  token: string,
  opts: {
    q?: string;
    filters?: Record<string, string>;
    sort?: string;
    dir?: "asc" | "desc";
    kolom?: string; // kosong = kolom nomor rangka
  } = {},
): Promise<{ kolom: string | null; jumlah: number; values: string[] }> {
  const qs = new URLSearchParams({ q: opts.q ?? "" });
  if (opts.filters && Object.keys(opts.filters).length)
    qs.set("filters", JSON.stringify(opts.filters));
  if (opts.sort) {
    qs.set("sort", opts.sort);
    qs.set("dir", opts.dir ?? "asc");
  }
  if (opts.kolom) qs.set("kolom", opts.kolom);
  const res = await fetch(`${API_BASE}/api/populasi/kolom?${qs}`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!res.ok) throw new ApiError(res.status, await parseError(res));
  return res.json();
}

export async function exportPopulasi(
  token: string,
  opts: { q?: string; filters?: Record<string, string> } = {},
): Promise<Blob> {
  const qs = new URLSearchParams({ q: opts.q ?? "" });
  if (opts.filters && Object.keys(opts.filters).length)
    qs.set("filters", JSON.stringify(opts.filters));
  const res = await fetch(`${API_BASE}/api/populasi/export?${qs}`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!res.ok) throw new ApiError(res.status, await parseError(res));
  return res.blob();
}

// ── Repair Kit Transmisi ────────────────────────────────────────────
export type RepairKitModel = {
  model: string;
  tipe: string;
  jumlah_seal_kit: number;
  jumlah_overhaul_tambahan: number;
  unit: string[];
};

export async function getRepairKitModels(
  token: string,
): Promise<{ available: boolean; models: RepairKitModel[] }> {
  const res = await fetch(`${API_BASE}/api/repairkit/transmisi`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!res.ok) throw new ApiError(res.status, await parseError(res));
  return res.json();
}

/** Unduh Excel repair kit transmisi. `model` kosong = semua model. */
export async function exportRepairKit(token: string, model = ""): Promise<Blob> {
  const qs = new URLSearchParams({ model });
  const res = await fetch(`${API_BASE}/api/repairkit/transmisi/export?${qs}`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!res.ok) throw new ApiError(res.status, await parseError(res));
  return res.blob();
}

// ── Batch Download (katalog Excel) ──────────────────────────────────
export async function fetchBatchTemplate(token: string): Promise<Blob> {
  const res = await fetch(`${API_BASE}/api/parts/batch-template`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!res.ok) throw new ApiError(res.status, await parseError(res));
  return res.blob();
}

export async function buildBatchCatalog(
  token: string,
  opts: { text?: string; file?: File | null; columns?: string[] },
): Promise<Blob> {
  const form = new FormData();
  if (opts.file) form.append("file", opts.file);
  else form.append("text", opts.text ?? "");
  if (opts.columns && opts.columns.length) form.append("columns", opts.columns.join(","));
  const res = await fetch(`${API_BASE}/api/parts/batch-catalog`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}` },
    body: form,
  });
  if (!res.ok) throw new ApiError(res.status, await parseError(res));
  return res.blob();
}

/** Picu unduhan blob sebagai file di browser. */
export function downloadBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

export type PartPhotos = { part_number: string; photos: string[]; source: string };

export async function getPartPhotos(pn: string, token: string): Promise<PartPhotos> {
  const res = await fetch(`${API_BASE}/api/parts/photos?pn=${encodeURIComponent(pn)}`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!res.ok) throw new ApiError(res.status, await parseError(res));
  return res.json();
}

// ── Spesifikasi fisik part (berat/dimensi resmi SIMS) ───────────────
export type PartSpec = {
  berat_bersih_kg?: number;
  berat_kirim_kg?: number;
  dimensi_cm?: string;
  satuan?: string;
  kemasan_minimum?: number;
  merek?: string;
};
export type PartSpecResponse = { part_number: string; spec: PartSpec; berat_gram: number };

export async function getPartSpec(pn: string, token: string): Promise<PartSpecResponse> {
  const res = await fetch(`${API_BASE}/api/parts/spec?pn=${encodeURIComponent(pn)}`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!res.ok) throw new ApiError(res.status, await parseError(res));
  return res.json();
}

// ── Stok live Accurate (kolom tambahan) ─────────────────────────────
export type AccurateStock = {
  configured: boolean;
  found?: boolean;
  session_expired?: boolean;
  error?: boolean;
  reason?: string;
  stock?: {
    available_to_sell: number;
    quantity: number;
    unit: string;
    name: string;
    no: string;
    item_type: string;
    harga?: number;
    per_gudang?: { gudang: string; deskripsi: string; qty: number; gudang_id?: number }[];
  };
};

export async function getAccurateStock(pn: string, token: string): Promise<AccurateStock> {
  const res = await fetch(`${API_BASE}/api/parts/accurate-stock?pn=${encodeURIComponent(pn)}`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!res.ok) throw new ApiError(res.status, await parseError(res));
  return res.json();
}

// ── Permissions (menu + kolom + sub-tab harga) ──────────────────────
export type MyPermissions = {
  menus: string[];
  columns: string[];
  harga_subtabs: string[];
  role: string;
  branch?: string | null; // label gudang bila akun cabang
  can_price?: boolean; // boleh ekspor kolom harga di Batch (admin & akun 'mas')
};

export async function getMyPermissions(token: string): Promise<MyPermissions> {
  const res = await fetch(`${API_BASE}/api/auth/permissions`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!res.ok) throw new ApiError(res.status, await parseError(res));
  return res.json();
}

/** 'sesi' bukan izin melainkan PEMBATASAN (mis. hanya 1 perangkat);
 *  'asisten' = kemampuan Asisten AI elevated (default kosong, centang MEMBERI). */
export type PermKind = "menu" | "column" | "harga" | "sesi" | "asisten";

export type PermOverview = {
  kind: string;
  all_keys: Record<string, string>;
  always: string[];
  default: string[];
  permissions: Record<string, string[]>;
  users: { username: string; role: string }[];
};

export async function getPermOverview(token: string, kind: PermKind): Promise<PermOverview> {
  const res = await fetch(`${API_BASE}/api/admin/perms/${kind}`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!res.ok) throw new ApiError(res.status, await parseError(res));
  return res.json();
}

export async function setPerm(
  token: string,
  kind: PermKind,
  username: string,
  keys: string[],
): Promise<void> {
  const res = await fetch(`${API_BASE}/api/admin/perms/${kind}`, {
    method: "PUT",
    headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
    body: JSON.stringify({ username, keys }),
  });
  if (!res.ok) throw new ApiError(res.status, await parseError(res));
}

export async function resetPerm(
  token: string,
  kind: PermKind,
  username: string,
): Promise<void> {
  const res = await fetch(
    `${API_BASE}/api/admin/perms/${kind}/${encodeURIComponent(username)}`,
    { method: "DELETE", headers: { Authorization: `Bearer ${token}` } },
  );
  if (!res.ok) throw new ApiError(res.status, await parseError(res));
}

export type AdminUser = {
  username: string;
  role: string;
  is_active: boolean;
  created_at?: string;
};

export async function listUsers(token: string): Promise<{ users: AdminUser[] }> {
  const res = await fetch(`${API_BASE}/api/admin/users`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!res.ok) throw new ApiError(res.status, await parseError(res));
  return res.json();
}

export async function createUser(
  token: string,
  body: { username: string; password: string; role: string },
): Promise<void> {
  const res = await fetch(`${API_BASE}/api/admin/users`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new ApiError(res.status, await parseError(res));
}

export async function updateUser(
  token: string,
  username: string,
  body: { role?: string; password?: string; is_active?: boolean },
): Promise<void> {
  const res = await fetch(`${API_BASE}/api/admin/users/${encodeURIComponent(username)}`, {
    method: "PUT",
    headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new ApiError(res.status, await parseError(res));
}

export async function deleteUser(token: string, username: string): Promise<void> {
  const res = await fetch(`${API_BASE}/api/admin/users/${encodeURIComponent(username)}`, {
    method: "DELETE",
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!res.ok) throw new ApiError(res.status, await parseError(res));
}

// ── Admin: Foto Part ────────────────────────────────────────────────
export type AdminPhoto = {
  id: string;
  file_name: string;
  storage_url: string;
  file_size?: number;
  created_at?: string;
};

export async function listAdminPhotos(token: string, pn: string): Promise<{ photos: AdminPhoto[] }> {
  const res = await fetch(`${API_BASE}/api/admin/photos?pn=${encodeURIComponent(pn)}`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!res.ok) throw new ApiError(res.status, await parseError(res));
  return res.json();
}

export async function uploadPhoto(token: string, pn: string, file: File): Promise<void> {
  const form = new FormData();
  form.append("pn", pn);
  form.append("file", file);
  const res = await fetch(`${API_BASE}/api/admin/photos`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}` },
    body: form,
  });
  if (!res.ok) throw new ApiError(res.status, await parseError(res));
}

export async function deletePhoto(token: string, id: string): Promise<void> {
  const res = await fetch(`${API_BASE}/api/admin/photos/${encodeURIComponent(id)}`, {
    method: "DELETE",
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!res.ok) throw new ApiError(res.status, await parseError(res));
}

// ── Admin: Image Index ──────────────────────────────────────────────
export type IndexStatusInfo = {
  torch: boolean;
  model_ready: boolean;
  total_indexed: number;
  gallery_local?: boolean;
};
export type ReloadGalleryResult = {
  ok: boolean;
  total: number;
  path: string | null;
  error: string | null;
};
export type IndexResult = {
  pn: string;
  found: number;
  already: number;
  indexed: number;
  failed: number;
  error: string | null;
};

export async function getIndexStatus(token: string): Promise<IndexStatusInfo> {
  const res = await fetch(`${API_BASE}/api/admin/index/status`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!res.ok) throw new ApiError(res.status, await parseError(res));
  return res.json();
}

export async function reloadGallery(token: string): Promise<ReloadGalleryResult> {
  const res = await fetch(`${API_BASE}/api/admin/index/reload-gallery`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!res.ok) throw new ApiError(res.status, await parseError(res));
  return res.json();
}

export type CatalogBomStatus = { available: boolean; unit: number; kategori: number };
export type CatalogBomRebuildResult = {
  ok: boolean;
  file_katalog_dipindai: number;
  unit_berkategori: number;
  kategori: number;
  assy_terindeks: number;
  total_baris_part: number;
  ukuran_kb: number;
};

export async function getCatalogBomStatus(token: string): Promise<CatalogBomStatus> {
  const res = await fetch(`${API_BASE}/api/admin/catalog-bom/status`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!res.ok) throw new ApiError(res.status, await parseError(res));
  return res.json();
}

export async function rebuildCatalogBom(token: string): Promise<CatalogBomRebuildResult> {
  const res = await fetch(`${API_BASE}/api/admin/catalog-bom/rebuild`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!res.ok) throw new ApiError(res.status, await parseError(res));
  return res.json();
}

export async function indexPart(token: string, pn: string, reindex = false): Promise<IndexResult> {
  const res = await fetch(`${API_BASE}/api/admin/index`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
    body: JSON.stringify({ pn, reindex }),
  });
  if (!res.ok) throw new ApiError(res.status, await parseError(res));
  return res.json();
}

export async function indexBulk(
  token: string,
  text: string,
  reindex = false,
): Promise<{ total_indexed: number; results: IndexResult[] }> {
  const res = await fetch(`${API_BASE}/api/admin/index/bulk`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
    body: JSON.stringify({ text, reindex }),
  });
  if (!res.ok) throw new ApiError(res.status, await parseError(res));
  return res.json();
}

// ── Stok Opname ──────────────────────────────────────────────────────
export type OpnameItem = {
  qty_sistem: number | null;
  qty_fisik: number | null;
  note: string;
  part_name: string;
};
export type OpnameSession = {
  session_id: string;
  items: Record<string, OpnameItem>;
  source_file?: string | null;
  source_filename?: string | null;
  finalized_at?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
  username?: string | null;
  [k: string]: unknown;
};

export async function getOpnameDraft(token: string): Promise<{ draft: OpnameSession | null }> {
  const res = await fetch(`${API_BASE}/api/opname/draft`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!res.ok) throw new ApiError(res.status, await parseError(res));
  return res.json();
}

export async function getOpnameHistory(token: string): Promise<{ history: OpnameSession[] }> {
  const res = await fetch(`${API_BASE}/api/opname/history`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!res.ok) throw new ApiError(res.status, await parseError(res));
  return res.json();
}

export async function opnameFromUpload(token: string, file: File): Promise<{ session: OpnameSession }> {
  const form = new FormData();
  form.append("file", file);
  const res = await fetch(`${API_BASE}/api/opname/draft/from-upload`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}` },
    body: form,
  });
  if (!res.ok) throw new ApiError(res.status, await parseError(res));
  return res.json();
}

export async function saveOpnameDraft(
  token: string,
  session: OpnameSession,
): Promise<{ ok: boolean; updated_at?: string }> {
  const res = await fetch(`${API_BASE}/api/opname/draft`, {
    method: "PUT",
    headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
    body: JSON.stringify(session),
  });
  if (!res.ok) throw new ApiError(res.status, await parseError(res));
  return res.json();
}

export async function finalizeOpname(token: string, session: OpnameSession): Promise<{ ok: boolean }> {
  const res = await fetch(`${API_BASE}/api/opname/finalize`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
    body: JSON.stringify(session),
  });
  if (!res.ok) throw new ApiError(res.status, await parseError(res));
  return res.json();
}

export async function deleteOpnameDraft(token: string): Promise<{ ok: boolean }> {
  const res = await fetch(`${API_BASE}/api/opname/draft`, {
    method: "DELETE",
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!res.ok) throw new ApiError(res.status, await parseError(res));
  return res.json();
}

// ── User Monitoring ──────────────────────────────────────────────────
export type MonitoringUser = {
  username: string;
  role: string;
  online: boolean;
  is_active: boolean;
  last_login_at?: string | null;
  last_active_at?: string | null;
  /** IP & perangkat login terakhir. */
  last_ip?: string | null;
  last_device?: string | null;
  /** Jaringan unik (/64 utk IPv6) dalam `share_days` hari — bukan alamat unik. */
  ip_count?: number;
  /** Alamat unik (IPv6 memutar alamatnya sendiri, jadi ini bisa jauh lebih besar). */
  alamat_count?: number;
  device_count?: number;
  login_count?: number;
  ips?: string[];
  devices?: string[];
  /** SINYAL (bukan vonis) akun dipakai beramai-ramai. */
  kemungkinan_dipakai_ramai?: boolean;
};
export type MonitoringActivity = {
  created_at?: string | null;
  username: string;
  action: string;
  target?: string | null;
  ip?: string | null;
  device?: string | null;
};
export type MonitoringData = {
  online_count: number;
  total_users: number;
  online_window_minutes?: number;
  share_days?: number;
  share_ip_min?: number;
  share_device_min?: number;
  /** false = tabel login_history belum dibuat di Supabase. */
  riwayat_tersedia?: boolean;
  users: MonitoringUser[];
  recent_activity: MonitoringActivity[];
};

export async function getMonitoring(token: string): Promise<MonitoringData> {
  const res = await fetch(`${API_BASE}/api/admin/monitoring`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!res.ok) throw new ApiError(res.status, await parseError(res));
  return res.json();
}

export type LoginHistoryRow = {
  id: number;
  created_at: string;
  username: string;
  role?: string | null;
  ip?: string | null;
  device?: string | null;
};

/** Riwayat login mentah 1 user (atau semua bila username kosong). */
export async function getLoginHistory(
  token: string,
  username = "",
  limit = 200,
): Promise<{ jumlah: number; riwayat: LoginHistoryRow[] }> {
  const q = new URLSearchParams({ limit: String(limit) });
  if (username) q.set("username", username);
  const res = await fetch(`${API_BASE}/api/admin/monitoring/login-history?${q}`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!res.ok) throw new ApiError(res.status, await parseError(res));
  return res.json();
}

/** DDL tabel login_history — ditampilkan bila tabel belum dibuat. */
export async function getMonitoringSql(token: string): Promise<{ sql: string }> {
  const res = await fetch(`${API_BASE}/api/admin/monitoring/sql`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!res.ok) throw new ApiError(res.status, await parseError(res));
  return res.json();
}

export type UploadKind = "stok" | "harga" | "populasi";

export async function uploadDataset(
  token: string,
  kind: UploadKind,
  file: File,
): Promise<{ ok: boolean; size: number }> {
  const form = new FormData();
  form.append("file", file);
  const res = await fetch(`${API_BASE}/api/admin/upload/${kind}`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}` },
    body: form,
  });
  if (!res.ok) throw new ApiError(res.status, await parseError(res));
  return res.json();
}

// ── Admin: Upload KATALOG (Excel per unit → folder /data) ───────────
export async function getCatalogFolders(token: string): Promise<{ folders: string[] }> {
  const res = await fetch(`${API_BASE}/api/admin/catalog/folders`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!res.ok) throw new ApiError(res.status, await parseError(res));
  return res.json();
}

// ── Pencarian Nihil (umpan sinonim) ──
export type SearchMiss = {
  query: string;
  count: number;
  modes?: string[];
  sources?: string[];
  last?: number;
};
export async function getSearchMisses(
  token: string,
): Promise<{ total: number; jumlah: number; misses: SearchMiss[] }> {
  const res = await fetch(`${API_BASE}/api/admin/search-misses`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!res.ok) throw new ApiError(res.status, await parseError(res));
  return res.json();
}
export async function resolveSearchMiss(token: string, query: string): Promise<{ ok: boolean }> {
  const res = await fetch(`${API_BASE}/api/admin/search-misses/resolve`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
    body: JSON.stringify({ query }),
  });
  if (!res.ok) throw new ApiError(res.status, await parseError(res));
  return res.json();
}

// ── Observabilitas Asisten AI (ai_chat_log) ──
export type ChatLogRow = {
  id: number;
  created_at: string;
  username?: string;
  role?: string;
  question?: string;
  tools?: string;
  tools_count: number;
  rounds: number;
  latency_ms: number;
  guard_hit: boolean;
  tool_failed: boolean;
  reply_len: number;
  outcome?: string;
  // Biaya token DeepSeek giliran ini (jumlah semua panggilan API-nya).
  // 0/undefined pada baris lama sebelum migrasi 021.
  tokens_in?: number;
  tokens_out?: number;
  tokens_cache_hit?: number;
  api_calls?: number;
  // Teks jawaban AI giliran ini (migrasi 022). undefined pada baris lama.
  reply?: string;
  // Nama tool yang GAGAL giliran ini (migrasi 023, comma-space). undefined pada baris lama.
  tools_failed?: string;
};
export type ChatLogSummary = {
  total: number;
  latensi_ms?: { p50: number; p90: number; maks: number };
  guard_menyala?: number;
  guard_rasio_persen?: number;
  tool_gagal?: number;
  tool_gagal_rasio_persen?: number;
  tool_tersering?: [string, number][];
  // [nama, jml_gagal, rasio_gagal_persen, jml_nf, jml_err] — tool paling sering
  // gagal; nf = lookup jujur nihil, err = error/infra. Ringkasan lama tanpa
  // elemen 4-5 (undefined saat destructuring — di-guard di UI).
  tool_gagal_tersering?: [string, number, number, number?, number?][];
  // Rincian total kegagalan per jenis; "legacy" = baris lama tanpa suffix jenis.
  tool_gagal_rincian?: { nf?: number; err?: number; legacy?: number };
  outcome?: Record<string, number>;
  token?: {
    giliran_terukur: number;
    total_in: number;
    total_out: number;
    rata2_in: number;
    rata2_out: number;
    cache_hit_persen: number;
  };
};
export async function getChatLog(
  token: string,
  limit = 200,
): Promise<{ ringkasan: ChatLogSummary; log: ChatLogRow[] }> {
  const res = await fetch(`${API_BASE}/api/admin/chat-log?limit=${limit}`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!res.ok) throw new ApiError(res.status, await parseError(res));
  return res.json();
}
// Hapus log observabilitas. beforeDays > 0 = hapus yang lebih tua dari N hari;
// tanpa arg = hapus SEMUA. Return jumlah baris terhapus.
export async function deleteChatLog(
  token: string,
  beforeDays?: number,
): Promise<{ ok: boolean; dihapus: number }> {
  const qs = beforeDays && beforeDays > 0 ? `?before_days=${beforeDays}` : "";
  const res = await fetch(`${API_BASE}/api/admin/chat-log${qs}`, {
    method: "DELETE",
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!res.ok) throw new ApiError(res.status, await parseError(res));
  return res.json();
}

// ── Usulan Sinonim OTOMATIS (LLM belajar dari pencarian nihil) ──
// generate → LLM memetakan istilah lapangan gagal → keyword EN (divalidasi ke
// katalog nyata di backend); approve → langsung masuk kamus & dipakai asisten.
export type SinonimUsulan = {
  id: string;
  query: string;
  count_miss: number;
  grup: string;
  triggers: string[];
  keywords: string[];
  keywords_dibuang?: string[];
  confidence: number;
  alasan?: string;
  status: string;
  catatan_apply?: string;
};
export async function getSinonimUsulan(
  token: string,
): Promise<{ jumlah: number; usulan: SinonimUsulan[] }> {
  const res = await fetch(`${API_BASE}/api/admin/sinonim/usulan`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!res.ok) throw new ApiError(res.status, await parseError(res));
  return res.json();
}
export async function generateSinonimUsulan(
  token: string,
  limit = 10,
  autoApprove = false,
): Promise<{ dibuat: number; auto_disetujui: number; usulan: SinonimUsulan[]; catatan?: string; error?: string }> {
  const res = await fetch(`${API_BASE}/api/admin/sinonim/usulan/generate`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
    body: JSON.stringify({ limit, auto_approve: autoApprove }),
  });
  if (!res.ok) throw new ApiError(res.status, await parseError(res));
  return res.json();
}
export async function approveSinonimUsulan(
  token: string,
  id: string,
): Promise<{ ok: boolean; usulan: SinonimUsulan }> {
  const res = await fetch(`${API_BASE}/api/admin/sinonim/usulan/approve`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
    body: JSON.stringify({ id }),
  });
  if (!res.ok) throw new ApiError(res.status, await parseError(res));
  return res.json();
}
export async function rejectSinonimUsulan(
  token: string,
  id: string,
): Promise<{ ok: boolean; usulan: SinonimUsulan }> {
  const res = await fetch(`${API_BASE}/api/admin/sinonim/usulan/reject`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
    body: JSON.stringify({ id }),
  });
  if (!res.ok) throw new ApiError(res.status, await parseError(res));
  return res.json();
}

// ── Kamus Sinonim (istilah lapangan → kata kunci katalog) ──
// Perubahan langsung dipakai asisten AI (reload per-mtime, tanpa restart).
export type SinonimEntry = {
  grup: string;
  triggers: string[];
  keywords: string[];
};
export async function getSinonim(
  token: string,
): Promise<{ jumlah: number; entries: SinonimEntry[] }> {
  const res = await fetch(`${API_BASE}/api/admin/sinonim`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!res.ok) throw new ApiError(res.status, await parseError(res));
  return res.json();
}
export async function addSinonim(token: string, entry: SinonimEntry): Promise<{ ok: boolean }> {
  const res = await fetch(`${API_BASE}/api/admin/sinonim`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
    body: JSON.stringify(entry),
  });
  if (!res.ok) throw new ApiError(res.status, await parseError(res));
  return res.json();
}
export async function updateSinonim(
  token: string,
  index: number,
  entry: SinonimEntry,
): Promise<{ ok: boolean }> {
  const res = await fetch(`${API_BASE}/api/admin/sinonim/${index}`, {
    method: "PUT",
    headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
    body: JSON.stringify(entry),
  });
  if (!res.ok) throw new ApiError(res.status, await parseError(res));
  return res.json();
}
export async function deleteSinonim(token: string, index: number): Promise<{ ok: boolean }> {
  const res = await fetch(`${API_BASE}/api/admin/sinonim/${index}`, {
    method: "DELETE",
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!res.ok) throw new ApiError(res.status, await parseError(res));
  return res.json();
}

export type CatalogUploadResult = {
  ok: boolean;
  saved: { path: string; size: number }[];
  count: number;
  errors: { file: string; error: string }[];
  refresh_warning?: string;
};

export async function uploadCatalog(
  token: string,
  subdir: string,
  files: File[],
): Promise<CatalogUploadResult> {
  const form = new FormData();
  form.append("subdir", subdir);
  for (const f of files) form.append("files", f);
  const res = await fetch(`${API_BASE}/api/admin/upload-catalog`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}` },
    body: form,
  });
  if (!res.ok) throw new ApiError(res.status, await parseError(res));
  return res.json();
}

// ── Admin: Pengetahuan Asisten AI ───────────────────────────────────
// Admin menulis/mengunggah pengetahuan internal; server mengindeksnya di latar
// (status di-polling) lalu asisten memakainya lewat tool `cari_pengetahuan`.
export type PengetahuanBerkas = {
  nama: string;
  nama_simpan?: string;
  ukuran?: number;
  ext?: string;
};

export type PengetahuanProgres = {
  langkah: string;
  kini: number;
  total: number;
  persen: number;
};

export type PengetahuanDok = {
  id: string;
  judul: string;
  deskripsi: string;
  tag: string[];
  berkas: PengetahuanBerkas[];
  untuk_pembeli: boolean;
  pakai_ai: boolean;
  aktif: boolean;
  status: "antre" | "proses" | "selesai" | "selesai_sebagian" | "gagal";
  progres: PengetahuanProgres;
  jumlah_chunk: number;
  pengayaan: string;
  error: string;
  oleh?: string;
  perlu_reindex?: boolean;
};

export type PengetahuanChunk = {
  id: string;
  dok_id: string;
  judul: string;
  judul_id: string;
  kata_kunci: string[];
  ringkasan: string;
  teks: string;
  tabel: string[][];
  gambar_ref: string[];
  sumber: string;
  halaman: number;
  tipe: string;
  untuk_pembeli: boolean;
  dicari: boolean;
  kode: string[];
  // Hasil ekstraksi V2 — opsional, chunk skema lama tidak punya.
  bahasa?: string;
  jalur?: string[];
  kolom?: string[];
  baris_total?: number;
  gambar_info?: { file: string; caption: string; halaman: number }[];
  kurasi?: boolean;
  skema?: number;
};

async function pgGet<T>(token: string, path: string): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!res.ok) throw new ApiError(res.status, await parseError(res));
  return res.json();
}

async function pgSend<T>(token: string, path: string, method: string, body?: unknown): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    method,
    headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!res.ok) throw new ApiError(res.status, await parseError(res));
  return res.json();
}

export async function getPengetahuan(
  token: string,
): Promise<{ jumlah: number; jumlah_chunk: number; dokumen: PengetahuanDok[] }> {
  return pgGet(token, "/api/admin/pengetahuan");
}

export async function getPengetahuanDetail(
  token: string,
  id: string,
): Promise<{ dokumen: PengetahuanDok; chunk: PengetahuanChunk[] }> {
  return pgGet(token, `/api/admin/pengetahuan/${id}`);
}

export async function getPengetahuanStatus(
  token: string,
  id: string,
): Promise<Pick<PengetahuanDok, "id" | "status" | "progres" | "jumlah_chunk" | "pengayaan" | "error">> {
  return pgGet(token, `/api/admin/pengetahuan/${id}/status`);
}

export async function addPengetahuan(
  token: string,
  body: {
    judul: string;
    deskripsi?: string;
    teks?: string;
    tabel?: string[][];
    tag?: string;
    untuk_pembeli?: boolean;
    pakai_ai?: boolean;
    files?: File[];
  },
): Promise<{ ok: boolean; id: string; status: string }> {
  const form = new FormData();
  form.append("judul", body.judul);
  form.append("deskripsi", body.deskripsi || "");
  form.append("teks", body.teks || "");
  if (body.tabel?.length) form.append("tabel_json", JSON.stringify(body.tabel));
  form.append("tag", body.tag || "");
  form.append("untuk_pembeli", String(!!body.untuk_pembeli));
  form.append("pakai_ai", String(body.pakai_ai !== false));
  for (const f of body.files || []) form.append("files", f);
  const res = await fetch(`${API_BASE}/api/admin/pengetahuan`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}` }, // JANGAN set Content-Type
    body: form,
  });
  if (!res.ok) throw new ApiError(res.status, await parseError(res));
  return res.json();
}

export async function reindexPengetahuan(
  token: string,
  id: string,
): Promise<{ ok: boolean; id: string; status: string }> {
  return pgSend(token, `/api/admin/pengetahuan/${id}/reindex`, "POST", {});
}

export async function updatePengetahuan(
  token: string,
  id: string,
  patch: Partial<Pick<PengetahuanDok, "judul" | "deskripsi" | "tag" | "untuk_pembeli" | "aktif" | "pakai_ai">>,
): Promise<{ ok: boolean; dokumen: PengetahuanDok }> {
  return pgSend(token, `/api/admin/pengetahuan/${id}`, "PATCH", patch);
}

export async function updatePengetahuanChunk(
  token: string,
  dokId: string,
  seq: string,
  patch: { judul_id?: string; kata_kunci?: string[]; dicari?: boolean },
): Promise<{ ok: boolean; chunk: PengetahuanChunk }> {
  return pgSend(token, `/api/admin/pengetahuan/${dokId}/chunk/${seq}`, "PATCH", patch);
}

export async function deletePengetahuan(
  token: string,
  id: string,
): Promise<{ ok: boolean }> {
  return pgSend(token, `/api/admin/pengetahuan/${id}`, "DELETE");
}

export async function cariPengetahuan(
  token: string,
  q: string,
): Promise<{ jumlah: number; hasil: PengetahuanChunk[] }> {
  return pgSend(token, "/api/admin/pengetahuan/cari", "POST", { q });
}

// ── Admin: Laporan Penjualan ────────────────────────────────────────
export type SalesRecap = {
  summary: { total_orders: number; paid_orders: number; omzet: number; items_sold: number };
  by_status: Record<string, { count: number; omzet: number }>;
  by_gudang: { gudang: string; count: number; omzet: number }[];
  by_month: { month: string; count: number; omzet: number }[];
  top_parts: { part_number: string; name: string; qty: number; omzet: number }[];
};

export async function getSalesRecap(token: string): Promise<SalesRecap> {
  const res = await fetch(`${API_BASE}/api/admin/sales`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!res.ok) throw new ApiError(res.status, await parseError(res));
  return res.json();
}

// Rekap penjualan khusus cabang (discoped ke gudang akun cabang).
export async function getBranchSales(token: string): Promise<SalesRecap> {
  const res = await fetch(`${API_BASE}/api/branch/sales`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!res.ok) throw new ApiError(res.status, await parseError(res));
  return res.json();
}

// ── Admin: Lokasi Gudang ────────────────────────────────────────────
export type AdminGudang = {
  label: string;
  display: string;
  lat: number | null;
  lon: number | null;
  selectable: boolean;
  key: string | null;
  origin_postal: string;
  pic: string;
  can_ship: boolean;   // boleh jadi gudang PENGIRIM pesanan online
  nearest: string[];
};

export async function getAdminGudang(token: string): Promise<{ gudang: AdminGudang[] }> {
  const res = await fetch(`${API_BASE}/api/admin/gudang`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!res.ok) throw new ApiError(res.status, await parseError(res));
  return res.json();
}

export async function saveAdminGudang(
  token: string,
  items: { label: string; lat: number | null; lon: number | null; selectable: boolean; key: string | null; pic: string; origin_postal: string; can_ship: boolean }[],
): Promise<void> {
  const res = await fetch(`${API_BASE}/api/admin/gudang`, {
    method: "PUT",
    headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
    body: JSON.stringify({ items }),
  });
  if (!res.ok) throw new ApiError(res.status, await parseError(res));
}

// ── Lacak resi (manifest kurir) ─────────────────────────────────────────────
// ⛔ BUKAN pemesanan pengiriman: resi tetap dibuat gerai ekspedisi lalu diketik
// admin cabang. Ini hanya membacakan perjalanan paket dari kurir.
export type TrackingStep = { waktu: string; keterangan: string; lokasi: string };
export type TrackingResult = {
  ada_resi: boolean;
  resi?: string;
  kurir?: string;
  delivered?: boolean;
  status?: string;
  penerima?: string;
  waktu_terima?: string;
  layanan?: string;
  riwayat?: TrackingStep[];
  error?: string;
};

export async function getOrderTracking(code: string, token: string): Promise<TrackingResult> {
  const res = await fetch(`${API_BASE}/api/orders/${encodeURIComponent(code)}/tracking`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!res.ok) throw new ApiError(res.status, await parseError(res));
  return res.json();
}

// ── Tautan akun → pelanggan Accurate (dipakai penawaran otomatis) ───────────
export type AccurateCustomer = { id: number; no: string; name: string; address?: string };
export type TautPelangganRow = {
  username: string;
  role: string;
  is_active: boolean;
  customer_id: number | null;
  customer_name: string;
  customer_no: string;
};

export async function getTautPelanggan(
  token: string,
): Promise<{ siap: boolean; users: TautPelangganRow[]; error?: string }> {
  const res = await fetch(`${API_BASE}/api/admin/pelanggan-accurate`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!res.ok) throw new ApiError(res.status, await parseError(res));
  return res.json();
}

export async function cariPelangganAccurate(
  token: string,
  q: string,
): Promise<{ configured: boolean; customers: AccurateCustomer[] }> {
  const res = await fetch(
    `${API_BASE}/api/admin/pelanggan-accurate/cari?q=${encodeURIComponent(q)}`,
    { headers: { Authorization: `Bearer ${token}` } },
  );
  if (!res.ok) throw new ApiError(res.status, await parseError(res));
  return res.json();
}

export async function tautPelanggan(
  token: string,
  body: { username: string; customer_id: number | null; customer_name?: string; customer_no?: string },
): Promise<void> {
  const res = await fetch(`${API_BASE}/api/admin/pelanggan-accurate`, {
    method: "PUT",
    headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new ApiError(res.status, await parseError(res));
}

// ── Config aplikasi mobile (versi APK + feature-flag, tanpa rebuild APK) ────
// Bentuknya cerminan GET /api/app/meta yang dipanggil aplikasi tiap kali dibuka.
export type AppMetaVersion = {
  latest_code: number;   // legacy: aplikasi ≤2.1.3 membandingkan versionCode
  latest_name: string;   // versi APK terbaru, mis. "2.1.4" — sumber banding utama
  min_code: number;      // legacy
  min_name: string;      // versi minimum; isi + force=true → update dipaksa
  download_url: string;
  force: boolean;
};
export type AppConfigResponse = {
  version: AppMetaVersion;
  config: Record<string, unknown>;
};

export async function getAdminAppConfig(token: string): Promise<AppConfigResponse> {
  const res = await fetch(`${API_BASE}/api/admin/app-config`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!res.ok) throw new ApiError(res.status, await parseError(res));
  return res.json();
}

export async function saveAdminAppConfig(
  token: string,
  body: { version?: AppMetaVersion; config?: Record<string, unknown> },
): Promise<AppConfigResponse> {
  const res = await fetch(`${API_BASE}/api/admin/app-config`, {
    method: "PUT",
    headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new ApiError(res.status, await parseError(res));
  return res.json();
}

export async function getMe(token: string): Promise<UserOut> {
  const res = await fetch(`${API_BASE}/api/auth/me`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!res.ok) throw new ApiError(res.status, await parseError(res));
  return res.json();
}

// ── Cabang: pesanan masuk ───────────────────────────────────────────
export async function getBranchOrders(token: string): Promise<{ branch: string; orders: OrderSummary[] }> {
  const res = await fetch(`${API_BASE}/api/branch/orders`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!res.ok) throw new ApiError(res.status, await parseError(res));
  return res.json();
}

export async function getBranchOrdersCount(token: string): Promise<{ count: number; branch: string }> {
  const res = await fetch(`${API_BASE}/api/branch/orders/count`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!res.ok) throw new ApiError(res.status, await parseError(res));
  return res.json();
}

export async function getBranchOrder(token: string, code: string): Promise<OrderDetail> {
  const res = await fetch(`${API_BASE}/api/branch/orders/${encodeURIComponent(code)}`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!res.ok) throw new ApiError(res.status, await parseError(res));
  return res.json();
}

export async function setBranchOrderStatus(token: string, code: string, status: string, trackingNo?: string): Promise<void> {
  const res = await fetch(`${API_BASE}/api/branch/orders/${encodeURIComponent(code)}/status`, {
    method: "PUT",
    headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
    body: JSON.stringify({ status, tracking_no: trackingNo }),
  });
  if (!res.ok) throw new ApiError(res.status, await parseError(res));
}

// ── Chat pesanan (pembeli ↔ gudang) ─────────────────────────────────
export type ChatMessage = {
  sender_username: string;
  sender_role: string; // 'pembeli' | 'gudang' | 'admin'
  body: string;
  created_at: string;
};

export async function getOrderChat(
  token: string,
  code: string,
): Promise<{ role: string; gudang: string; buyer: string; messages: ChatMessage[] }> {
  const res = await fetch(`${API_BASE}/api/orders/${encodeURIComponent(code)}/chat`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!res.ok) throw new ApiError(res.status, await parseError(res));
  return res.json();
}

export async function sendOrderChat(token: string, code: string, body: string): Promise<void> {
  const res = await fetch(`${API_BASE}/api/orders/${encodeURIComponent(code)}/chat`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
    body: JSON.stringify({ body }),
  });
  if (!res.ok) throw new ApiError(res.status, await parseError(res));
}

// Chat pra-pesanan: pembeli ↔ gudang
export type BuyerChatThread = { gudang_key: string; last: string; created_at: string };
export async function getBuyerChatThreads(token: string): Promise<{ threads: BuyerChatThread[] }> {
  const res = await fetch(`${API_BASE}/api/chat/buyer/threads`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!res.ok) throw new ApiError(res.status, await parseError(res));
  return res.json();
}

export async function getBuyerGudangChat(token: string, key: string): Promise<{ messages: ChatMessage[] }> {
  const res = await fetch(`${API_BASE}/api/chat/gudang/${encodeURIComponent(key)}`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!res.ok) throw new ApiError(res.status, await parseError(res));
  return res.json();
}

export async function sendBuyerGudangChat(token: string, key: string, body: string): Promise<void> {
  const res = await fetch(`${API_BASE}/api/chat/gudang/${encodeURIComponent(key)}`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
    body: JSON.stringify({ body }),
  });
  if (!res.ok) throw new ApiError(res.status, await parseError(res));
}

export type ChatThreadSummary = { buyer_username: string; last: string; created_at: string };
export async function getBranchChatThreads(token: string): Promise<{ threads: ChatThreadSummary[] }> {
  const res = await fetch(`${API_BASE}/api/chat/branch/threads`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!res.ok) throw new ApiError(res.status, await parseError(res));
  return res.json();
}

export async function getBranchChat(token: string, buyer: string): Promise<{ messages: ChatMessage[] }> {
  const res = await fetch(`${API_BASE}/api/chat/branch/${encodeURIComponent(buyer)}`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!res.ok) throw new ApiError(res.status, await parseError(res));
  return res.json();
}

export async function sendBranchChat(token: string, buyer: string, body: string): Promise<void> {
  const res = await fetch(`${API_BASE}/api/chat/branch/${encodeURIComponent(buyer)}`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
    body: JSON.stringify({ body }),
  });
  if (!res.ok) throw new ApiError(res.status, await parseError(res));
}

// ── Asisten AI (DeepSeek) ───────────────────────────────────────────
export type AIChatTurn = { role: "user" | "assistant"; content: string };
export type AIPhotoCandidate = {
  part_number: string;
  part_name: string;
  similarity: number;
  sims_url: string;
};
export type AIBandingExport = {
  rangka_1: string;
  rangka_2: string;
  kategori: string;
  kategori_nama: string;
};
export type AIExcelExport = {
  id: string;
  filename: string;
  judul: string;
  jumlah_baris: number;
};
/** Gambar exploded view untuk 1 PN → tampil INLINE di jawaban asisten. */
export type AIExplodedImage = {
  id: string;
  pn?: string;
  balon?: number | string | null;
  nama_figure?: string;
  kategori?: string;
};
export type AIChatResult = {
  reply: string;
  tools_used: string[];
  photo_candidates?: AIPhotoCandidate[];
  /** Model transmisi yg dibahas → tampilkan tombol unduh Excel repair kit. */
  repairkit_models?: string[];
  /** Perbandingan rangka → kartu unduh Excel hasil perbandingan. */
  banding_exports?: AIBandingExport[];
  /** Export generik (tool buat_excel) → kartu unduh Excel dinamis. */
  excel_exports?: AIExcelExport[];
  /** Gambar exploded view (tool gambar_exploded) → tampil inline di jawaban. */
  exploded_images?: AIExplodedImage[];
  /** PN yang disebut asisten (grounded) → tampilkan thumbnail foto part. */
  part_pns?: string[];
  /** Lampiran Excel: id sheet di server (kirim lagi di giliran berikutnya). */
  sheet_id?: string;
  /** Ringkasan isi Excel yang baru diunggah. */
  sheet?: AISheetSummary;
};

/** Ringkasan Excel unggahan user — kolom + peran yang dikenali server. */
export type AISheetSummary = {
  filename: string;
  sheet: string;
  sheet_lain: string[];
  /** Ringkasan tab lain di workbook (nama + header + perkiraan baris). */
  sheet_lain_detail?: { nama: string; header?: string[]; jumlah_baris?: number; kosong?: boolean }[];
  catatan_sheet?: string;
  jumlah_baris: number;
  jumlah_kolom: number;
  kolom: { nama: string; peran: string; terisi?: number; contoh_nilai?: string[] }[];
  kolom_part_number: string | null;
  part_number_dikenal_di_katalog: number;
  part_number_tidak_dikenal?: number;
  contoh_baris: string[][];
  terpotong: boolean;
};

/** Unduh Excel hasil perbandingan part dua unit (banding_rangka). */
export async function exportBandingRangka(
  token: string,
  p: { rangka_1: string; rangka_2: string; kategori?: string },
): Promise<Blob> {
  const qs = new URLSearchParams({
    rangka_1: p.rangka_1,
    rangka_2: p.rangka_2,
    kategori: p.kategori || "",
  });
  const res = await fetch(`${API_BASE}/api/ai/banding-rangka/export?${qs}`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!res.ok) throw new ApiError(res.status, await parseError(res));
  return res.blob();
}

/** Unduh Excel generik yang dibuat asisten via tool buat_excel (per export id). */
export async function exportAiExcel(token: string, id: string): Promise<Blob> {
  const res = await fetch(`${API_BASE}/api/ai/excel/${encodeURIComponent(id)}`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!res.ok) throw new ApiError(res.status, await parseError(res));
  return res.blob();
}

// `allowed` false = menu "Asisten AI" dimatikan admin untuk akun ini (Menu Control),
// beda dari asisten yang memang belum dikonfigurasi di server.
// ── Cek kecocokan part di unit pembeli (per nomor rangka, sumber EPC) ────────
export type CekUnitResult = {
  checked: boolean;
  error?: string;          // gagal MENGECEK (EPC down / rangka tak dikenal)
  cocok?: boolean;
  frame_number?: string;
  part_number?: string;
  pesan?: string;          // saat TIDAK cocok — jujur, jangan menebak
  nama?: string;
  istilah_lapangan?: string | null;
  qty?: string | number;
  kategori?: string | null;
  lokasi?: string | null;  // nama figure exploded view yang memuat part ini
  balon?: number | null;
  image_id?: string | null;
  penjelasan?: string;     // kalimat siap tampil ("✅ Cocok — ... kampas rem ...")
};

export async function cekPartDiUnit(token: string, partNumber: string, rangka: string): Promise<CekUnitResult> {
  const res = await fetch(`${API_BASE}/api/parts/cek-unit`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
    body: JSON.stringify({ part_number: partNumber, rangka }),
  });
  if (!res.ok) throw new ApiError(res.status, await parseError(res));
  return res.json();
}

/** PNG exploded view fitur cek-unit — endpoint terpisah dari /api/ai/excel yang
 *  digembok izin menu Asisten AI. */
export async function getPartExploded(token: string, id: string): Promise<Blob> {
  const res = await fetch(`${API_BASE}/api/parts/exploded/${encodeURIComponent(id)}`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!res.ok) throw new ApiError(res.status, await parseError(res));
  return res.blob();
}

export async function getAiStatus(
  token: string,
): Promise<{ available: boolean; allowed?: boolean; perbaikan?: boolean }> {
  const res = await fetch(`${API_BASE}/api/ai/status`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!res.ok) throw new ApiError(res.status, await parseError(res));
  return res.json();
}

// `conversationId` = id percakapan (UUID buatan klien, di-reset saat "hapus
// obrolan"). Server memakainya untuk MEMORI SESI: PN/angka hasil tool + nomor
// rangka/mesin giliran lalu, supaya follow-up "harganya berapa?" tidak dianggap
// karangan oleh guard. Opsional — tanpa itu asisten tetap jalan, hanya pelupa.
export async function aiChat(
  token: string,
  messages: AIChatTurn[],
  sheetId?: string,
  conversationId?: string,
): Promise<AIChatResult> {
  const res = await fetch(`${API_BASE}/api/ai/chat`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
    body: JSON.stringify({
      messages,
      sheet_id: sheetId || "",
      conversation_id: conversationId || "",
    }),
  });
  if (!res.ok) throw new ApiError(res.status, await parseError(res));
  return res.json();
}

/**
 * Versi STREAMING /chat: `onProgress(label)` dipanggil tiap event STATUS langkah
 * ("Mencari di EPC…", "Menyusun jawaban…"); resolve dgn hasil AKHIR (sudah tersaring
 * guard). Fallback ke aiChat bila stream tak didukung/gagal di tengah.
 */
export async function aiChatStream(
  token: string,
  messages: AIChatTurn[],
  sheetId: string | undefined,
  onProgress: (label: string) => void,
  conversationId?: string,
): Promise<AIChatResult> {
  const res = await fetch(`${API_BASE}/api/ai/chat-stream`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
    body: JSON.stringify({
      messages,
      sheet_id: sheetId || "",
      conversation_id: conversationId || "",
    }),
  });
  if (!res.ok || !res.body) throw new ApiError(res.status, await parseError(res));

  const reader = res.body.getReader();
  const dec = new TextDecoder();
  let buf = "";
  let result: AIChatResult | null = null;
  let errMsg: string | null = null;

  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    const parts = buf.split("\n\n");
    buf = parts.pop() ?? "";
    for (const part of parts) {
      const line = part.split("\n").find((l) => l.startsWith("data:"));
      if (!line) continue;
      let ev: { type: string; label?: string; result?: AIChatResult; message?: string };
      try {
        ev = JSON.parse(line.slice(5).trim());
      } catch {
        continue;
      }
      if (ev.type === "progress" && ev.label) onProgress(ev.label);
      else if (ev.type === "done" && ev.result) result = ev.result;
      else if (ev.type === "error") errMsg = ev.message || "Asisten AI gagal merespons.";
    }
  }
  if (errMsg) throw new Error(errMsg);
  if (!result) throw new Error("Aliran jawaban berakhir tanpa hasil.");
  return result;
}

// ── Umpan balik Asisten AI (👍/👎) ──────────────────────────────────
export type AIFeedbackInput = {
  rating: "up" | "down";
  question: string;
  answer: string;
  tools?: string[];
  note?: string;
  context?: AIChatTurn[];
};

export async function submitAiFeedback(token: string, fb: AIFeedbackInput): Promise<{ ok: boolean }> {
  const res = await fetch(`${API_BASE}/api/ai/feedback`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
    body: JSON.stringify(fb),
  });
  if (!res.ok) throw new ApiError(res.status, await parseError(res));
  return res.json();
}

export type AIFeedbackRow = {
  id: number;
  created_at: string;
  username: string | null;
  role: string | null;
  rating: "up" | "down";
  question: string | null;
  answer: string | null;
  tools: string | null;
  note: string | null;
  resolved: boolean;
};
export type AIFeedbackList = {
  ringkasan: { total: number; up: number; down: number; down_belum_ditangani: number };
  jumlah: number;
  feedback: AIFeedbackRow[];
};

export async function listAiFeedback(
  token: string,
  opts?: { rating?: "up" | "down"; onlyOpen?: boolean },
): Promise<AIFeedbackList> {
  const qs = new URLSearchParams();
  if (opts?.rating) qs.set("rating", opts.rating);
  if (opts?.onlyOpen) qs.set("only_open", "true");
  const res = await fetch(`${API_BASE}/api/ai/feedback?${qs}`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!res.ok) throw new ApiError(res.status, await parseError(res));
  return res.json();
}

export async function resolveAiFeedback(
  token: string,
  id: number,
  resolved = true,
): Promise<{ ok: boolean }> {
  const res = await fetch(
    `${API_BASE}/api/ai/feedback/${id}/resolve?resolved=${resolved}`,
    { method: "POST", headers: { Authorization: `Bearer ${token}` } },
  );
  if (!res.ok) throw new ApiError(res.status, await parseError(res));
  return res.json();
}

// Chat dengan LAMPIRAN EXCEL: server membaca kolomnya, asisten bisa mengisi
// stok/nama/harga lalu mengeluarkan Excel baru. Balasan memuat `sheet_id` yang
// harus dikirim ulang di giliran berikutnya agar file tetap terlampir.
export async function aiChatSheet(
  token: string,
  messages: AIChatTurn[],
  file: File,
  conversationId?: string,
): Promise<AIChatResult> {
  const form = new FormData();
  form.append("messages", JSON.stringify(messages));
  form.append("file", file);
  form.append("conversation_id", conversationId || "");
  const res = await fetch(`${API_BASE}/api/ai/chat-sheet`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}` },
    body: form,
  });
  if (!res.ok) throw new ApiError(res.status, await parseError(res));
  return res.json();
}

// ── Pembeli: lokasi gudang ──────────────────────────────────────────
export type BuyerLocation = { key: string; label: string };

export async function getBuyerLocations(token: string): Promise<{ locations: BuyerLocation[] }> {
  const res = await fetch(`${API_BASE}/api/buyer/locations`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!res.ok) throw new ApiError(res.status, await parseError(res));
  return res.json();
}

export async function getBuyerLocation(token: string): Promise<{ key: string | null; label: string | null }> {
  const res = await fetch(`${API_BASE}/api/buyer/location`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!res.ok) throw new ApiError(res.status, await parseError(res));
  return res.json();
}

export async function setBuyerLocation(token: string, key: string): Promise<{ ok: boolean; key: string; label: string }> {
  const res = await fetch(`${API_BASE}/api/buyer/location`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
    body: JSON.stringify({ key }),
  });
  if (!res.ok) throw new ApiError(res.status, await parseError(res));
  return res.json();
}

// ── Pembeli: etalase belanja (/toko) ────────────────────────────────
export type TokoProduct = {
  part_number: string;
  name: string;
  harga: number;
  harga_display: string;
  berat: number;
  foto: string | null;
  kategori: string[];
  ready: boolean;
  stok: number;
  gudang: string;
};
export type TokoKategori = { key: string; label: string; count: number };
export type TokoHome = {
  lokasi: string | null;
  total_produk: number;
  kategori: TokoKategori[];
  terlaris: TokoProduct[];
  unggulan: TokoProduct[];
};
export type TokoCatalog = {
  items: TokoProduct[];
  count: number;
  page: number;
  page_size: number;
  total_pages: number;
  lokasi: string | null;
};

export async function getTokoHome(token: string): Promise<TokoHome> {
  const res = await fetch(`${API_BASE}/api/buyer/home`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!res.ok) throw new ApiError(res.status, await parseError(res));
  return res.json();
}

export async function getTokoCatalog(
  token: string,
  opts: { q?: string; kategori?: string; sort?: string; ready?: boolean; page?: number; pageSize?: number } = {},
): Promise<TokoCatalog> {
  const qs = new URLSearchParams({
    q: opts.q ?? "",
    kategori: opts.kategori ?? "",
    sort: opts.sort ?? "relevan",
    ready: String(opts.ready ?? false),
    page: String(opts.page ?? 1),
    page_size: String(opts.pageSize ?? 24),
  });
  const res = await fetch(`${API_BASE}/api/buyer/catalog?${qs}`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!res.ok) throw new ApiError(res.status, await parseError(res));
  return res.json();
}
