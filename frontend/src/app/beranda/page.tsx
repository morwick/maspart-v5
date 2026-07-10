"use client";

import { useEffect, useMemo, useState, type ReactNode } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import AppShell from "@/components/AppShell";
import { getToken, getUser } from "@/lib/auth";
import { ensurePerms } from "@/lib/perms";
import {
  getAiStatus,
  getBranchOrdersCount,
  getBranchSales,
} from "@/lib/api";

/* ── Ikon ── */
const Ic = ({ d }: { d: ReactNode }) => (
  <svg width={18} height={18} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.6} strokeLinecap="round" strokeLinejoin="round" aria-hidden>{d}</svg>
);
const I = {
  search: <Ic d={<><circle cx="11" cy="11" r="7" /><path d="m20 20-3.5-3.5" /></>} />,
  ai: <Ic d={<><path d="M12 3v3M12 18v3M3 12h3M18 12h3" /><rect x="6" y="6" width="12" height="12" rx="3" /><path d="M9.5 11h.01M14.5 11h.01M9.5 14h5" /></>} />,
  camera: <Ic d={<><path d="M4 8h3l2-2h6l2 2h3v11H4z" /><circle cx="12" cy="13" r="3.5" /></>} />,
  compare: <Ic d={<><path d="M8 4v16M16 4v16M3 8h5M16 16h5M3 16h5M16 8h5" /></>} />,
  download: <Ic d={<><path d="M12 3v12m0 0 4-4m-4 4-4-4M5 21h14" /></>} />,
  truck: <Ic d={<><path d="M3 7h11v9H3zM14 11h4l3 3v2h-7zM7 19a2 2 0 1 0 0-4 2 2 0 0 0 0 4ZM17 19a2 2 0 1 0 0-4 2 2 0 0 0 0 4Z" /></>} />,
  money: <Ic d={<><rect x="3" y="6" width="18" height="12" rx="2" /><circle cx="12" cy="12" r="2.5" /></>} />,
  grid: <Ic d={<><rect x="3" y="3" width="7" height="7" rx="1" /><rect x="14" y="3" width="7" height="7" rx="1" /><rect x="3" y="14" width="7" height="7" rx="1" /><rect x="14" y="14" width="7" height="7" rx="1" /></>} />,
  cart: <Ic d={<><path d="M3 4h2l2.4 12.5a1 1 0 0 0 1 .8h8.7a1 1 0 0 0 1-.8L21 8H6" /><circle cx="9" cy="20" r="1" /><circle cx="18" cy="20" r="1" /></>} />,
  chart: <Ic d={<><path d="M4 20V4M4 20h16M8 16v-4M12 16V8M16 16v-6" /></>} />,
  arrow: <Ic d={<><path d="M5 12h14M13 6l6 6-6 6" /></>} />,
};

/* Kartu menu (task tile) — dikunci ke izin `menus` user. `search` selalu tampil. */
type Tile = { key: string; label: string; desc: string; icon: ReactNode; href: string };
const TILES: Tile[] = [
  { key: "search", label: "Cari Part", desc: "Part number atau nama", icon: I.search, href: "/search" },
  { key: "ai", label: "Asisten AI", desc: "Stok, harga, BOM per-VIN", icon: I.ai, href: "/asisten" },
  { key: "search_image", label: "Cari by Foto", desc: "Kenali part dari gambar", icon: I.camera, href: "/search-image" },
  { key: "stok", label: "Stok", desc: "Stok gudang & cabang", icon: I.grid, href: "/stok" },
  { key: "harga", label: "Harga", desc: "Daftar & cari harga", icon: I.money, href: "/harga" },
  { key: "populasi", label: "Populasi Unit", desc: "Armada terdaftar", icon: I.truck, href: "/populasi" },
  { key: "compare", label: "Bandingkan 2 Part", desc: "Dua part berdampingan", icon: I.compare, href: "/compare" },
  { key: "batch", label: "Batch Download", desc: "Unduh massal katalog", icon: I.download, href: "/batch" },
];

const rupiah = (n: number) =>
  "Rp " + new Intl.NumberFormat("id-ID", { maximumFractionDigits: 0 }).format(n);

export default function BerandaPage() {
  const router = useRouter();
  const [uname, setUname] = useState("");
  const [allowed, setAllowed] = useState<string[] | null>(null);
  const [branch, setBranch] = useState<string | null>(null);
  const [aiOn, setAiOn] = useState<boolean | null>(null);
  const [branchN, setBranchN] = useState<number | null>(null);
  const [omzet, setOmzet] = useState<number | null>(null);

  useEffect(() => {
    const u = getUser();
    const token = getToken();
    if (!token) {
      router.replace("/login");
      return;
    }
    // Beranda ini khusus staf internal. Admin punya Command Center; pembeli ke belanja.
    if (u?.role === "admin") {
      router.replace("/dashboard");
      return;
    }
    if (u?.role === "pembeli") {
      router.replace("/toko");
      return;
    }
    setUname(u?.username ?? "");

    getAiStatus(token).then((d) => setAiOn(d.available)).catch(() => setAiOn(null));

    ensurePerms()
      .then((p) => {
        setAllowed(p ? p.menus : null);
        const b = p?.branch ?? null;
        setBranch(b);
        if (b) {
          getBranchOrdersCount(token).then((d) => setBranchN(d.count)).catch(() => {});
          getBranchSales(token).then((d) => setOmzet(d.summary?.omzet ?? 0)).catch(() => {});
        }
      })
      .catch(() => setAllowed(null));
  }, [router]);

  const greeting = useMemo(() => {
    const h = new Date().getHours();
    if (h < 11) return "Selamat pagi";
    if (h < 15) return "Selamat siang";
    if (h < 19) return "Selamat sore";
    return "Selamat malam";
  }, []);
  const todayLabel = useMemo(() => {
    try {
      return new Date().toLocaleDateString("id-ID", { weekday: "long", day: "numeric", month: "long", year: "numeric" });
    } catch {
      return "";
    }
  }, []);

  // allowed==null → tampilkan semua (fail-open ringan; halaman tetap dilindungi guard menu di tiap page).
  const tiles = TILES.filter((t) => t.key === "search" || allowed == null || allowed.includes(t.key));

  return (
    <AppShell active="/beranda" title="Beranda">
      <div className="mx-auto" style={{ maxWidth: 1100, padding: "24px 24px 44px" }}>
        {/* ── Hero band (identitas berbeda dari Command Center admin) ── */}
        <div
          style={{
            borderRadius: 16,
            padding: "26px 26px 28px",
            background: "linear-gradient(135deg, var(--brand-700), var(--brand-500))",
            color: "#fff",
            boxShadow: "var(--shadow-2)",
            position: "relative",
            overflow: "hidden",
          }}
        >
          <div style={{ position: "absolute", right: -40, top: -40, width: 180, height: 180, borderRadius: "50%", background: "rgba(255,255,255,.08)" }} />
          <div style={{ position: "absolute", right: 40, bottom: -60, width: 150, height: 150, borderRadius: "50%", background: "rgba(255,255,255,.06)" }} />
          <div style={{ position: "relative" }}>
            <div style={{ fontSize: 11.5, opacity: 0.85, fontWeight: 550, textTransform: "uppercase", letterSpacing: ".1em" }}>{todayLabel}</div>
            <h1 style={{ margin: "6px 0 0", fontSize: 27, fontWeight: 700, letterSpacing: "-.02em" }}>
              {greeting}{uname ? `, ${uname}` : ""} 👋
            </h1>
            <p style={{ margin: "6px 0 0", fontSize: 13.5, opacity: 0.9, maxWidth: 520 }}>
              {branch ? `Cabang ${branch} · ` : ""}Cari part, cek stok & harga, atau tanya Asisten AI untuk data per-VIN.
            </p>
            <div style={{ display: "flex", gap: 10, marginTop: 18, flexWrap: "wrap" }}>
              <Link href="/search" className="btn btn-lg" style={{ background: "#fff", color: "var(--brand-700)", fontWeight: 650 }}>{I.search} Cari Part</Link>
              <Link href="/asisten" className="btn btn-lg" style={{ background: "rgba(255,255,255,.16)", color: "#fff", border: "1px solid rgba(255,255,255,.3)" }}>{I.ai} Tanya Asisten</Link>
            </div>
          </div>
        </div>

        {/* ── Ringkas cabang (hanya akun cabang) ── */}
        {branch && (
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))", gap: 14, marginTop: 16 }}>
            <Link href="/cabang/pesanan" className="surface" style={{ padding: 18, display: "block", textDecoration: "none", color: "inherit" }}>
              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
                <div className="stat-label">Pesanan masuk</div>
                <span style={{ display: "inline-flex", width: 30, height: 30, borderRadius: 8, alignItems: "center", justifyContent: "center", background: "var(--brand-50)", color: "var(--brand-700)" }}>{I.cart}</span>
              </div>
              <div className="mono" style={{ fontSize: 28, fontWeight: 700, marginTop: 10, color: "var(--ink-900)" }}>{branchN == null ? "—" : branchN}</div>
              <div style={{ fontSize: 12, color: "var(--ink-500)", marginTop: 4 }}>menunggu diproses</div>
            </Link>
            <Link href="/cabang/penjualan" className="surface" style={{ padding: 18, display: "block", textDecoration: "none", color: "inherit" }}>
              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
                <div className="stat-label">Omzet cabang</div>
                <span style={{ display: "inline-flex", width: 30, height: 30, borderRadius: 8, alignItems: "center", justifyContent: "center", background: "var(--info-50)", color: "var(--info-600)" }}>{I.chart}</span>
              </div>
              <div className="mono" style={{ fontSize: 24, fontWeight: 700, marginTop: 10, color: "var(--ink-900)" }}>{omzet == null ? "—" : rupiah(omzet)}</div>
              <div style={{ fontSize: 12, color: "var(--ink-500)", marginTop: 4 }}>lihat laporan penjualan</div>
            </Link>
          </div>
        )}

        {/* ── Menu Anda (task tiles sesuai izin) ── */}
        <div style={{ marginTop: 22, display: "flex", alignItems: "baseline", justifyContent: "space-between" }}>
          <div style={{ fontSize: 14, fontWeight: 650, color: "var(--ink-800)" }}>Menu Anda</div>
          <div style={{ fontSize: 12, color: "var(--ink-500)" }}>{tiles.length} menu tersedia</div>
        </div>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(240px, 1fr))", gap: 12, marginTop: 12 }}>
          {tiles.map((t) => (
            <Link key={t.key} href={t.href} className="surface" style={{ padding: 16, display: "flex", alignItems: "center", gap: 13, textDecoration: "none", color: "inherit" }}>
              <span style={{ display: "inline-flex", width: 40, height: 40, borderRadius: 10, alignItems: "center", justifyContent: "center", background: "var(--brand-50)", color: "var(--brand-700)", border: "1px solid var(--brand-100)", flexShrink: 0 }}>{t.icon}</span>
              <span style={{ minWidth: 0, flex: 1 }}>
                <span style={{ display: "block", fontSize: 13.5, fontWeight: 650 }}>{t.label}</span>
                <span style={{ display: "block", fontSize: 11.5, color: "var(--ink-500)" }}>{t.desc}</span>
              </span>
              <span style={{ color: "var(--ink-300)", flexShrink: 0 }}>{I.arrow}</span>
            </Link>
          ))}
        </div>

        {/* ── Kartu bantuan Asisten AI ── */}
        <div className="surface" style={{ marginTop: 14, padding: 18, display: "flex", gap: 14, alignItems: "center", flexWrap: "wrap" }}>
          <span style={{ display: "inline-flex", width: 44, height: 44, borderRadius: 11, alignItems: "center", justifyContent: "center", background: aiOn === false ? "var(--warn-50)" : "var(--brand-50)", color: aiOn === false ? "var(--warn-600)" : "var(--brand-700)", flexShrink: 0 }}>{I.ai}</span>
          <div style={{ minWidth: 0, flex: 1 }}>
            <div style={{ fontSize: 13.5, fontWeight: 650 }}>
              Asisten AI {aiOn == null ? "" : aiOn ? "· aktif" : "· belum aktif"}
            </div>
            <div style={{ fontSize: 12.5, color: "var(--ink-500)", marginTop: 2 }}>
              Tanya dengan bahasa biasa — stok live Accurate, harga, populasi unit, hingga BOM per nomor rangka.
            </div>
          </div>
          <Link href="/asisten" className="btn btn-secondary" style={{ flexShrink: 0 }}>Buka Asisten {I.arrow}</Link>
        </div>
      </div>
    </AppShell>
  );
}
