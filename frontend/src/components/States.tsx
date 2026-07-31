"use client";

import type { ReactNode } from "react";

/* Bentuk baku "sedang memuat" & "belum ada data".
   Dulu tiap halaman menuliskannya sendiri — ada yang "Memuat…" abu-abu di tengah
   kotak 160px, ada yang teks 12,5px terselip di baris meta, ada yang tak ada
   sama sekali. Tiga kejadian yang sama terlihat seperti tiga aplikasi berbeda. */

/* ── Empty state ──
   Aturannya: SELALU beri jalan keluar. Layar kosong tanpa saran aksi membuat
   user mengira aplikasinya rusak, bukan mengira pencariannya yang meleset. */
export function EmptyState({
  icon,
  title,
  sub,
  action,
}: {
  icon?: ReactNode;
  title: string;
  sub?: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div className="empty-state">
      {icon && <div className="empty-ic">{icon}</div>}
      <div className="empty-title">{title}</div>
      {sub && <div className="empty-sub">{sub}</div>}
      {action && <div style={{ marginTop: 6 }}>{action}</div>}
    </div>
  );
}

/* Ikon bawaan untuk empty state — stroke 1.6, sejalan dgn ikon sidebar. */
export const EmptyIcon = {
  search: (
    <svg width={22} height={22} viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth={1.6} strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <circle cx="11" cy="11" r="7" />
      <path d="m20 20-3.5-3.5" />
    </svg>
  ),
  box: (
    <svg width={22} height={22} viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth={1.6} strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <path d="M21 8v8l-9 5-9-5V8l9-5z" />
      <path d="m3 8 9 5 9-5M12 13v8" />
    </svg>
  ),
  doc: (
    <svg width={22} height={22} viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth={1.6} strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z" />
      <path d="M14 3v5h5M9 13h6M9 17h4" />
    </svg>
  ),
};

/* ── Skeleton tabel ──
   Meniru BENTUK tabel yang akan datang (tinggi baris = --row-h) supaya tata
   letak tidak melompat saat data tiba. Lebar tiap sel divariasikan agar terbaca
   sebagai teks, bukan sebagai batang seragam. */
const LEBAR = ["62%", "84%", "48%", "71%", "56%", "78%"];

export function TableSkeleton({
  rows = 8,
  cols = 4,
}: {
  rows?: number;
  cols?: number;
}) {
  return (
    <div aria-hidden style={{ padding: "0 var(--row-px)" }}>
      {Array.from({ length: rows }).map((_, r) => (
        <div
          key={r}
          style={{
            display: "flex",
            gap: 24,
            alignItems: "center",
            height: "var(--row-h)",
            borderBottom: "1px solid var(--ink-100)",
          }}
        >
          {Array.from({ length: cols }).map((_, c) => (
            <div key={c} style={{ flex: 1, minWidth: 0 }}>
              <div
                className="skeleton"
                style={{ height: 10, width: LEBAR[(r + c * 2) % LEBAR.length] }}
              />
            </div>
          ))}
        </div>
      ))}
    </div>
  );
}

/* Skeleton baris bebas — untuk blok non-tabel (kartu, panel detail). */
export function LineSkeleton({ lines = 3, width }: { lines?: number; width?: string }) {
  return (
    <div aria-hidden style={{ display: "grid", gap: 9 }}>
      {Array.from({ length: lines }).map((_, i) => (
        <div
          key={i}
          className="skeleton"
          style={{ height: 11, width: width || LEBAR[i % LEBAR.length] }}
        />
      ))}
    </div>
  );
}
