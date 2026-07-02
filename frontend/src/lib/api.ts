// Klien API tipis untuk backend FastAPI MASPART.

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

export type SearchResponse = {
  term: string;
  count: number;
  page: number;
  page_size: number;
  total_pages: number;
  results: PartResult[];
};

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function parseError(res: Response): Promise<string> {
  try {
    const data = await res.json();
    if (typeof data?.detail === "string") return data.detail;
    if (Array.isArray(data?.detail)) return data.detail.map((d: { msg?: string }) => d.msg).join(", ");
  } catch {
    /* ignore */
  }
  return `HTTP ${res.status}`;
}

/** Gambar SIMS pakai http://. Saat situs dibuka via HTTPS, browser memblokir gambar
 *  http (mixed content). Lewatkan ke proxy backend (same-origin) agar tetap tampil. */
export function partImageUrl(url: string): string {
  if (typeof url === "string" && url.startsWith("http://")) {
    return `${API_BASE}/api/parts/image-proxy?url=${encodeURIComponent(url)}`;
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
};

export type ImageSearchResponse = { count: number; results: ImageMatch[] };

export async function searchByImage(
  file: File,
  token: string,
  opts: { topK?: number; threshold?: number; useTta?: boolean } = {},
): Promise<ImageSearchResponse> {
  const qs = new URLSearchParams({
    top_k: String(opts.topK ?? 12),
    threshold: String(opts.threshold ?? 0.3),
    use_tta: String(opts.useTta ?? false),
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

export async function getShippingRates(
  token: string,
  weightGrams: number,
  value = 0,
  destPostal = "",
): Promise<{ rates: ShippingRate[]; error: string | null; available: boolean }> {
  const qs = new URLSearchParams({ weight_grams: String(weightGrams), value: String(value) });
  if (destPostal) qs.set("dest_postal", destPostal);
  const res = await fetch(`${API_BASE}/api/shipping/rates?${qs}`, {
    headers: { Authorization: `Bearer ${token}` },
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
  opts: { text?: string; file?: File | null },
): Promise<Blob> {
  const form = new FormData();
  if (opts.file) form.append("file", opts.file);
  else form.append("text", opts.text ?? "");
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

// ── Permissions (menu + kolom + sub-tab harga) ──────────────────────
export type MyPermissions = {
  menus: string[];
  columns: string[];
  harga_subtabs: string[];
  role: string;
  branch?: string | null; // label gudang bila akun cabang
};

export async function getMyPermissions(token: string): Promise<MyPermissions> {
  const res = await fetch(`${API_BASE}/api/auth/permissions`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!res.ok) throw new ApiError(res.status, await parseError(res));
  return res.json();
}

export type PermKind = "menu" | "column" | "harga";

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
};
export type MonitoringActivity = {
  created_at?: string | null;
  username: string;
  action: string;
  target?: string | null;
};
export type MonitoringData = {
  online_count: number;
  total_users: number;
  online_window_minutes?: number;
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
  items: { label: string; lat: number | null; lon: number | null; selectable: boolean; key: string | null; pic: string }[],
): Promise<void> {
  const res = await fetch(`${API_BASE}/api/admin/gudang`, {
    method: "PUT",
    headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
    body: JSON.stringify({ items }),
  });
  if (!res.ok) throw new ApiError(res.status, await parseError(res));
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
export type AIChatResult = {
  reply: string;
  tools_used: string[];
  photo_candidates?: AIPhotoCandidate[];
  /** Model transmisi yg dibahas → tampilkan tombol unduh Excel repair kit. */
  repairkit_models?: string[];
  /** Perbandingan rangka → kartu unduh Excel hasil perbandingan. */
  banding_exports?: AIBandingExport[];
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

export async function getAiStatus(token: string): Promise<{ available: boolean }> {
  const res = await fetch(`${API_BASE}/api/ai/status`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!res.ok) throw new ApiError(res.status, await parseError(res));
  return res.json();
}

export async function aiChat(token: string, messages: AIChatTurn[]): Promise<AIChatResult> {
  const res = await fetch(`${API_BASE}/api/ai/chat`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
    body: JSON.stringify({ messages }),
  });
  if (!res.ok) throw new ApiError(res.status, await parseError(res));
  return res.json();
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

// Chat dengan FOTO part: foto dikenali via Cari-by-Foto lalu AI menjelaskan.
export async function aiChatImage(
  token: string,
  messages: AIChatTurn[],
  file: File,
): Promise<AIChatResult> {
  const form = new FormData();
  form.append("messages", JSON.stringify(messages));
  form.append("file", file);
  const res = await fetch(`${API_BASE}/api/ai/chat-image`, {
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
