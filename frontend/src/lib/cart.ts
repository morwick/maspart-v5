"use client";

import { getUser } from "./auth";

export type CartItem = {
  part_number: string;
  name: string;
  harga: string; // tampilan (mis. "Rp 600.000" atau "—")
  berat?: number; // berat per item (gram); 0/undefined = belum ditetapkan
  qty: number;
};

const EVENT = "maspart-cart-changed";

/** True bila string harga punya nilai > 0 (mis. "Rp 600.000"). "—"/"Rp 0"/"" → false. */
export function hasPrice(harga: string | null | undefined): boolean {
  if (!harga) return false;
  const digits = String(harga).replace(/[^\d]/g, "");
  return digits.length > 0 && Number(digits) > 0;
}

/** True bila berat (gram) sudah ditetapkan (> 0). */
export function hasWeight(berat: number | null | undefined): boolean {
  return typeof berat === "number" && berat > 0;
}

function key(): string {
  const u = getUser()?.username || "anon";
  return `maspart_cart_${u}`;
}

export function getCart(): CartItem[] {
  if (typeof window === "undefined") return [];
  try {
    const raw = localStorage.getItem(key());
    return raw ? (JSON.parse(raw) as CartItem[]) : [];
  } catch {
    return [];
  }
}

function save(items: CartItem[]) {
  if (typeof window === "undefined") return;
  try {
    localStorage.setItem(key(), JSON.stringify(items));
    window.dispatchEvent(new Event(EVENT));
  } catch {
    /* ignore */
  }
}

export function addToCart(item: Omit<CartItem, "qty">, qty = 1) {
  const items = getCart();
  const ex = items.find((i) => i.part_number === item.part_number);
  if (ex) ex.qty += qty;
  else items.push({ ...item, qty });
  save(items);
}

export function setQty(pn: string, qty: number) {
  const items = getCart().map((i) => (i.part_number === pn ? { ...i, qty: Math.max(1, qty) } : i));
  save(items);
}

export function removeFromCart(pn: string) {
  save(getCart().filter((i) => i.part_number !== pn));
}

export function clearCart() {
  save([]);
}

export function cartCount(): number {
  return getCart().reduce((n, i) => n + i.qty, 0);
}

/** Subscribe ke perubahan keranjang (untuk badge). Return unsubscribe. */
export function onCartChange(cb: () => void): () => void {
  if (typeof window === "undefined") return () => {};
  window.addEventListener(EVENT, cb);
  window.addEventListener("storage", cb);
  return () => {
    window.removeEventListener(EVENT, cb);
    window.removeEventListener("storage", cb);
  };
}
