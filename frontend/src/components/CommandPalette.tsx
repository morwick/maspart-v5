"use client";

import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useRouter } from "next/navigation";

export type PaletteItem = {
  label: string;
  href: string;
  hint?: string;
  icon?: ReactNode;
};

const SearchIcon = (
  <svg width={16} height={16} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.6} strokeLinecap="round" strokeLinejoin="round">
    <circle cx="11" cy="11" r="7" />
    <path d="m20 20-3.5-3.5" />
  </svg>
);
const ArrowIcon = (
  <svg width={14} height={14} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round">
    <path d="M5 12h14M13 6l6 6-6 6" />
  </svg>
);

export default function CommandPalette({
  open,
  onClose,
  items,
}: {
  open: boolean;
  onClose: () => void;
  items: PaletteItem[];
}) {
  const router = useRouter();
  const [q, setQ] = useState("");
  const [active, setActive] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (open) {
      setQ("");
      setActive(0);
      // fokus setelah animasi buka
      setTimeout(() => inputRef.current?.focus(), 20);
    }
  }, [open]);

  // Baris hasil: navigasi terfilter + (bila ada query) aksi "cari di part".
  const rows = useMemo(() => {
    const term = q.trim().toLowerCase();
    const nav = items.filter((it) => !term || it.label.toLowerCase().includes(term));
    const out: (PaletteItem & { action?: () => void })[] = [...nav];
    if (term) {
      out.unshift({
        label: `Cari "${q.trim()}" di part`,
        href: `/search?q=${encodeURIComponent(q.trim())}`,
        hint: "Enter",
        icon: SearchIcon,
      });
    }
    return out;
  }, [q, items]);

  useEffect(() => {
    if (active >= rows.length) setActive(Math.max(0, rows.length - 1));
  }, [rows.length, active]);

  if (!open) return null;

  function go(i: number) {
    const row = rows[i];
    if (!row) return;
    onClose();
    router.push(row.href);
  }

  function onKey(e: React.KeyboardEvent) {
    if (e.key === "Escape") {
      e.preventDefault();
      onClose();
    } else if (e.key === "ArrowDown") {
      e.preventDefault();
      setActive((a) => Math.min(a + 1, rows.length - 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActive((a) => Math.max(a - 1, 0));
    } else if (e.key === "Enter") {
      e.preventDefault();
      go(active);
    }
  }

  return (
    <div className="cmdk-backdrop" onClick={onClose}>
      <div className="cmdk-panel" onClick={(e) => e.stopPropagation()}>
        <div style={{ display: "flex", alignItems: "center", gap: 10, padding: "12px 16px", borderBottom: "1px solid var(--ink-150)" }}>
          <span style={{ display: "inline-flex", color: "var(--ink-400)" }}>{SearchIcon}</span>
          <input
            ref={inputRef}
            value={q}
            onChange={(e) => setQ(e.target.value)}
            onKeyDown={onKey}
            placeholder="Cari part, VIN, customer, atau halaman…"
            style={{ flex: 1, border: "none", outline: "none", background: "transparent", fontSize: 15, color: "var(--ink-900)", fontFamily: "inherit" }}
          />
          <span className="kbd">Esc</span>
        </div>
        <div style={{ maxHeight: "50vh", overflow: "auto", padding: "6px 0" }}>
          {rows.length === 0 && (
            <div style={{ padding: "18px 16px", fontSize: 13, color: "var(--ink-500)" }}>Tidak ada hasil.</div>
          )}
          {rows.map((row, i) => (
            <div
              key={row.href + i}
              className={"cmdk-row" + (i === active ? " active" : "")}
              onMouseEnter={() => setActive(i)}
              onClick={() => go(i)}
            >
              <span className="cmdk-ic">{row.icon ?? ArrowIcon}</span>
              <span style={{ flex: 1 }}>{row.label}</span>
              {row.hint && <span className="kbd">{row.hint}</span>}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
