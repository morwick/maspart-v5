"use client";

import { useEffect, useState, type ReactNode } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { clearSession, getUser } from "@/lib/auth";
import { clearPerms, ensurePerms } from "@/lib/perms";
import { cartCount, onCartChange } from "@/lib/cart";
import { getBranchOrdersCount } from "@/lib/api";
import { getToken } from "@/lib/auth";

/* ── Inline icons (minimal stroke, 1.6) ── */
const Ic = ({ d }: { d: ReactNode }) => (
  <svg width={16} height={16} viewBox="0 0 24 24" fill="none" stroke="currentColor"
    strokeWidth={1.6} strokeLinecap="round" strokeLinejoin="round" aria-hidden>
    {d}
  </svg>
);
const I = {
  search: <Ic d={<><circle cx="11" cy="11" r="7" /><path d="m20 20-3.5-3.5" /></>} />,
  camera: <Ic d={<><path d="M4 8h3l2-2h6l2 2h3v11H4z" /><circle cx="12" cy="13" r="3.5" /></>} />,
  compare: <Ic d={<><path d="M8 4v16M16 4v16M3 8h5M16 16h5M3 16h5M16 8h5" /></>} />,
  download: <Ic d={<><path d="M12 3v12m0 0 4-4m-4 4-4-4M5 21h14" /></>} />,
  truck: <Ic d={<><path d="M3 7h11v9H3zM14 11h4l3 3v2h-7zM7 19a2 2 0 1 0 0-4 2 2 0 0 0 0 4ZM17 19a2 2 0 1 0 0-4 2 2 0 0 0 0 4Z" /></>} />,
  money: <Ic d={<><rect x="3" y="6" width="18" height="12" rx="2" /><circle cx="12" cy="12" r="2.5" /></>} />,
  clipboard: <Ic d={<><rect x="6" y="4" width="12" height="17" rx="2" /><path d="M9 4h6v3H9zM9 11h6M9 15h4" /></>} />,
  shield: <Ic d={<><path d="M12 3 5 6v6c0 4 3 7 7 9 4-2 7-5 7-9V6z" /></>} />,
  upload: <Ic d={<><path d="M12 21V9m0 0 4 4m-4-4-4 4M5 3h14" /></>} />,
  user: <Ic d={<><circle cx="12" cy="8" r="4" /><path d="M4 21a8 8 0 0 1 16 0" /></>} />,
  photo: <Ic d={<><rect x="3" y="5" width="18" height="14" rx="2" /><circle cx="9" cy="11" r="2" /><path d="m3 17 5-5 4 4 3-3 6 6" /></>} />,
  grid: <Ic d={<><rect x="3" y="3" width="7" height="7" rx="1" /><rect x="14" y="3" width="7" height="7" rx="1" /><rect x="3" y="14" width="7" height="7" rx="1" /><rect x="14" y="14" width="7" height="7" rx="1" /></>} />,
  chart: <Ic d={<><path d="M4 20V4M4 20h16M8 16v-4M12 16V8M16 16v-6" /></>} />,
  logout: <Ic d={<><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4M16 17l5-5-5-5M21 12H9" /></>} />,
  menu: <Ic d={<><path d="M4 6h16M4 12h16M4 18h16" /></>} />,
  cart: <Ic d={<><path d="M3 4h2l2.4 12.5a1 1 0 0 0 1 .8h8.7a1 1 0 0 0 1-.8L21 8H6M9 21a1 1 0 1 0 0-2 1 1 0 0 0 0 2ZM18 21a1 1 0 1 0 0-2 1 1 0 0 0 0 2Z" /></>} />,
  receipt: <Ic d={<><path d="M5 3h14v18l-3-2-2 2-2-2-2 2-2-2-3 2zM8 7h8M8 11h8M8 15h5" /></>} />,
  chat: <Ic d={<><path d="M4 5h16v11H9l-5 4z" /><path d="M8 9h8M8 12h5" /></>} />,
  ai: <Ic d={<><path d="M12 3v3M12 18v3M3 12h3M18 12h3" /><rect x="6" y="6" width="12" height="12" rx="3" /><path d="M9.5 11h.01M14.5 11h.01M9.5 14h5" /></>} />,
  pulse: <Ic d={<><path d="M3 12h4l2 6 4-14 2 8h6" /></>} />,
};

type NavItem = { key?: string; href: string; label: string; icon: ReactNode };

const NAV_PRIMARY: NavItem[] = [
  { key: "ai", href: "/asisten", label: "Asisten AI", icon: I.ai },
  { key: "search", href: "/search", label: "Cari Part", icon: I.search },
  { key: "search_image", href: "/search-image", label: "Cari by Foto", icon: I.camera },
  { key: "compare", href: "/compare", label: "Bandingkan 2 Part", icon: I.compare },
  { key: "batch", href: "/batch", label: "Batch Download", icon: I.download },
];
const NAV_DATA: NavItem[] = [
  { key: "populasi", href: "/populasi", label: "Populasi Unit", icon: I.truck },
  { key: "harga", href: "/harga", label: "Harga", icon: I.money },

];
const NAV_ADMIN: NavItem[] = [
  { href: "/admin/orders", label: "Pesanan", icon: I.cart },
  { href: "/admin/penjualan", label: "Laporan Penjualan", icon: I.chart },
  { href: "/admin/feedback", label: "Umpan Balik AI", icon: I.ai },
  { href: "/admin/menu", label: "Menu Control", icon: I.shield },
  { href: "/admin/monitoring", label: "Monitoring User", icon: I.pulse },
  { href: "/admin/upload", label: "Upload Data", icon: I.upload },
  { href: "/admin/users", label: "Manajemen User", icon: I.user },
  { href: "/admin/gudang", label: "Lokasi Gudang", icon: I.truck },
  { href: "/admin/foto", label: "Foto Part", icon: I.photo },
  { href: "/admin/index", label: "Image Index", icon: I.grid },


];

// Akun pembeli hanya melihat alur belanja.
const NAV_BUYER: NavItem[] = [
  { href: "/search", label: "Cari Part", icon: I.search },
  { href: "/asisten", label: "Asisten AI", icon: I.ai },
  { href: "/chat", label: "Chat", icon: I.chat },
  { href: "/pesanan", label: "Pesanan Saya", icon: I.receipt },
  { href: "/pilih-lokasi", label: "Ganti Lokasi", icon: I.truck },
];
// Halaman internal yang TIDAK boleh diakses pembeli.
const BUYER_DENY = ["/search-image", "/compare", "/batch", "/populasi", "/harga", "/cabang/pesanan", "/cabang/chat"];

export default function AppShell({
  active,
  title,
  sub,
  actions,
  children,
}: {
  active: string;
  title: string;
  sub?: string;
  actions?: ReactNode;
  children: ReactNode;
}) {
  const router = useRouter();
  const [allowed, setAllowed] = useState<string[] | null>(null);
  const [isAdmin, setIsAdmin] = useState(false);
  const [isBuyer, setIsBuyer] = useState(false);
  const [uname, setUname] = useState("");
  const [role, setRole] = useState("");
  const [open, setOpen] = useState(false);
  const [cartN, setCartN] = useState(0);
  const [branchLabel, setBranchLabel] = useState<string | null>(null);
  const [branchN, setBranchN] = useState(0);

  useEffect(() => {
    const u = getUser();
    setUname(u?.username ?? "");
    setRole(u?.role ?? "");
    setIsAdmin(u?.role === "admin");
    setIsBuyer(u?.role === "pembeli");
    // Pembeli hanya boleh di alur belanja — tendang dari halaman internal/admin.
    if (u?.role === "pembeli" && (active.startsWith("/admin") || BUYER_DENY.includes(active))) {
      router.replace("/search");
      return;
    }
    if (u?.role !== "pembeli") {
      ensurePerms()
        .then((p) => {
          setAllowed(p ? p.menus : NAV_PRIMARY.filter((i) => i.key).map((i) => i.key!));
          setBranchLabel(p?.branch ?? null);
        })
        .catch(() => setAllowed(NAV_PRIMARY.filter((i) => i.key).map((i) => i.key!)));
    }
    const sync = () => setCartN(cartCount());
    sync();
    return onCartChange(sync);
  }, [active, router]);

  // Akun cabang: ambil & poll jumlah pesanan masuk (badge notifikasi).
  useEffect(() => {
    if (!branchLabel) return;
    let alive = true;
    const fetchCount = () => {
      const token = getToken();
      if (!token) return;
      getBranchOrdersCount(token)
        .then((d) => alive && setBranchN(d.count))
        .catch(() => {});
    };
    fetchCount();
    const id = setInterval(fetchCount, 30000);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, [branchLabel, active]);

  function logout() {
    clearSession();
    clearPerms();
    router.replace("/login");
  }

  const show = (it: NavItem) => !it.key || allowed == null || allowed.includes(it.key);

  const Item = (it: NavItem) => (
    <Link
      key={it.href}
      href={it.href}
      onClick={() => setOpen(false)}
      className={"nav-item" + (it.href === active ? " active" : "")}
    >
      <span style={{ display: "inline-flex", opacity: it.href === active ? 1 : 0.75 }}>{it.icon}</span>
      <span className="truncate" style={{ flex: 1 }}>{it.label}</span>
    </Link>
  );

  const sidebar = (
    <aside
      className="flex w-60 shrink-0 flex-col"
      style={{ background: "var(--paper)", borderRight: "1px solid var(--ink-150)" }}
    >
      <div className="flex items-center gap-2.5 px-4 pb-3.5 pt-4">
        <div
          className="mono grid place-items-center"
          style={{ width: 30, height: 30, borderRadius: 8, background: "var(--brand-600)", color: "#fff", fontWeight: 700, fontSize: 14 }}
        >
          M
        </div>
        <div style={{ lineHeight: 1.1 }}>
          <div style={{ fontWeight: 650, fontSize: 14 }}>MasPart</div>
          <div style={{ fontSize: 11, color: "var(--ink-500)" }}>Part Number Finder</div>
        </div>
      </div>

      <nav className="flex-1 overflow-auto px-3 pb-4">
        {isBuyer ? (
          <>
            <div className="nav-section-label">Belanja</div>
            <div className="flex flex-col gap-0.5">{NAV_BUYER.map(Item)}</div>
          </>
        ) : (
          <>
            <div className="nav-section-label">Pencarian</div>
            <div className="flex flex-col gap-0.5">{NAV_PRIMARY.filter(show).map(Item)}</div>
            <div className="nav-section-label">Data</div>
            <div className="flex flex-col gap-0.5">{NAV_DATA.filter(show).map(Item)}</div>
            {branchLabel && (
              <>
                <div className="nav-section-label">Cabang {branchLabel}</div>
                <div className="flex flex-col gap-0.5">
                  <Link
                    href="/cabang/pesanan"
                    onClick={() => setOpen(false)}
                    className={"nav-item" + ("/cabang/pesanan" === active ? " active" : "")}
                  >
                    <span style={{ display: "inline-flex", opacity: "/cabang/pesanan" === active ? 1 : 0.75 }}>{I.cart}</span>
                    <span className="truncate" style={{ flex: 1 }}>Pesanan Masuk</span>
                    {branchN > 0 && (
                      <span
                        className="mono"
                        style={{
                          minWidth: 18, height: 18, padding: "0 5px", borderRadius: 99,
                          background: "var(--brand-600)", color: "#fff", fontSize: 10.5,
                          fontWeight: 700, display: "grid", placeItems: "center",
                        }}
                      >
                        {branchN}
                      </span>
                    )}
                  </Link>
                  <Link
                    href="/cabang/chat"
                    onClick={() => setOpen(false)}
                    className={"nav-item" + ("/cabang/chat" === active ? " active" : "")}
                  >
                    <span style={{ display: "inline-flex", opacity: "/cabang/chat" === active ? 1 : 0.75 }}>{I.chat}</span>
                    <span className="truncate" style={{ flex: 1 }}>Chat</span>
                  </Link>
                  <Link
                    href="/cabang/penjualan"
                    onClick={() => setOpen(false)}
                    className={"nav-item" + ("/cabang/penjualan" === active ? " active" : "")}
                  >
                    <span style={{ display: "inline-flex", opacity: "/cabang/penjualan" === active ? 1 : 0.75 }}>{I.chart}</span>
                    <span className="truncate" style={{ flex: 1 }}>Laporan Penjualan</span>
                  </Link>
                </div>
              </>
            )}
            {isAdmin && (
              <>
                <div className="nav-section-label">Admin</div>
                <div className="flex flex-col gap-0.5">{NAV_ADMIN.map(Item)}</div>
              </>
            )}
          </>
        )}
      </nav>

      <div className="p-3" style={{ borderTop: "1px solid var(--ink-150)" }}>
        <div className="flex items-center gap-2.5 rounded-lg p-2" style={{ background: "var(--ink-50)" }}>
          <div
            className="grid place-items-center rounded-full"
            style={{ width: 30, height: 30, background: "var(--brand-100)", color: "var(--brand-700)", fontWeight: 650, fontSize: 12.5 }}
          >
            {(uname || "?").slice(0, 2).toUpperCase()}
          </div>
          <div style={{ lineHeight: 1.15, flex: 1, minWidth: 0 }}>
            <div className="truncate" style={{ fontSize: 12.5, fontWeight: 600 }}>{uname || "—"}</div>
            <div style={{ fontSize: 11, color: "var(--ink-500)" }}>{role || ""}</div>
          </div>
          <button className="btn btn-ghost btn-sm" onClick={logout} title="Keluar" aria-label="Keluar">
            {I.logout}
          </button>
        </div>
      </div>
    </aside>
  );

  return (
    <div className="flex h-screen overflow-hidden">
      {/* Sidebar desktop */}
      <div className="hidden md:flex">{sidebar}</div>

      {/* Sidebar mobile (drawer overlay) */}
      {open && (
        <div className="fixed inset-0 z-40 md:hidden">
          <div className="absolute inset-0" style={{ background: "rgba(15,20,17,.4)" }} onClick={() => setOpen(false)} />
          <div className="absolute left-0 top-0 h-full" style={{ boxShadow: "var(--shadow-3)" }}>{sidebar}</div>
          <button
            onClick={() => setOpen(false)}
            aria-label="Tutup menu"
            className="btn"
            style={{ position: "absolute", top: 12, right: 12, background: "var(--paper)", border: "1px solid var(--ink-200)" }}
          >
            ✕
          </button>
        </div>
      )}

      {/* Main column */}
      <div className="flex min-w-0 flex-1 flex-col">
        <header
          className="flex h-[60px] shrink-0 items-center gap-3 px-4 sm:px-6"
          style={{ background: "var(--paper)", borderBottom: "1px solid var(--ink-150)" }}
        >
          <div className="md:hidden">
            <button
              className="btn btn-secondary btn-sm"
              onClick={() => setOpen(true)}
              aria-label="Buka menu"
            >
              {I.menu}
            </button>
          </div>
          <div className="min-w-0 flex-1" style={{ lineHeight: 1.2 }}>
            <div className="truncate" style={{ fontSize: 17, fontWeight: 600 }}>{title}</div>
            {sub && <div className="truncate" style={{ fontSize: 12, color: "var(--ink-500)" }}>{sub}</div>}
          </div>
          {actions && <div className="flex items-center gap-2">{actions}</div>}
          {isBuyer && (
            <Link
              href="/keranjang"
              className="btn btn-secondary"
              style={{ position: "relative" }}
              aria-label="Keranjang"
              title="Keranjang"
            >
              {I.cart}
              {cartN > 0 && (
                <span
                  className="mono"
                  style={{
                    position: "absolute", top: -7, right: -7, minWidth: 18, height: 18,
                    padding: "0 4px", borderRadius: 99, background: "var(--brand-600)", color: "#fff",
                    fontSize: 10.5, fontWeight: 700, display: "grid", placeItems: "center",
                  }}
                >
                  {cartN}
                </span>
              )}
            </Link>
          )}
        </header>

        <main className="flex-1 overflow-auto">{children}</main>
      </div>
    </div>
  );
}
