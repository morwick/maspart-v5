"use client";

import { useEffect, useMemo, useState, type ReactNode } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import AppShell from "@/components/AppShell";
import { getToken, getUser } from "@/lib/auth";
import {
  getAiStatus,
  getIndexStatus,
  getMonitoring,
  getSalesRecap,
  getBranchOrdersCount,
  type MonitoringActivity,
} from "@/lib/api";

/* ── Ikon ── */
const Ic = ({ d }: { d: ReactNode }) => (
  <svg width={16} height={16} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.6} strokeLinecap="round" strokeLinejoin="round" aria-hidden>{d}</svg>
);
const I = {
  search: <Ic d={<><circle cx="11" cy="11" r="7" /><path d="m20 20-3.5-3.5" /></>} />,
  ai: <Ic d={<><path d="M12 3v3M12 18v3M3 12h3M18 12h3" /><rect x="6" y="6" width="12" height="12" rx="3" /><path d="M9.5 11h.01M14.5 11h.01M9.5 14h5" /></>} />,
  camera: <Ic d={<><path d="M4 8h3l2-2h6l2 2h3v11H4z" /><circle cx="12" cy="13" r="3.5" /></>} />,
  truck: <Ic d={<><path d="M3 7h11v9H3zM14 11h4l3 3v2h-7zM7 19a2 2 0 1 0 0-4 2 2 0 0 0 0 4ZM17 19a2 2 0 1 0 0-4 2 2 0 0 0 0 4Z" /></>} />,
  money: <Ic d={<><rect x="3" y="6" width="18" height="12" rx="2" /><circle cx="12" cy="12" r="2.5" /></>} />,
  compare: <Ic d={<><path d="M8 4v16M16 4v16M3 8h5M16 16h5M3 16h5M16 8h5" /></>} />,
  download: <Ic d={<><path d="M12 3v12m0 0 4-4m-4 4-4-4M5 21h14" /></>} />,
  users: <Ic d={<><circle cx="9" cy="8" r="3.5" /><path d="M2 21a7 7 0 0 1 14 0M16 11a3.5 3.5 0 0 0 0-7M22 21a6 6 0 0 0-4-5.7" /></>} />,
  cart: <Ic d={<><path d="M3 4h2l2.4 12.5a1 1 0 0 0 1 .8h8.7a1 1 0 0 0 1-.8L21 8H6" /><circle cx="9" cy="20" r="1" /><circle cx="18" cy="20" r="1" /></>} />,
  pulse: <Ic d={<><path d="M3 12h4l2 6 4-14 2 8h6" /></>} />,
  grid: <Ic d={<><rect x="3" y="3" width="7" height="7" rx="1" /><rect x="14" y="3" width="7" height="7" rx="1" /><rect x="3" y="14" width="7" height="7" rx="1" /><rect x="14" y="14" width="7" height="7" rx="1" /></>} />,
};

type Stat = { label: string; value: string; sub: string; icon: ReactNode; href: string; tone?: "brand" | "warn" | "info" };

function rupiah(n: number): string {
  try {
    return "Rp " + new Intl.NumberFormat("id-ID").format(Math.round(n));
  } catch {
    return "Rp " + n;
  }
}

function StatCard({ st }: { st: Stat }) {
  const tone = st.tone === "warn"
    ? { bg: "var(--warn-50)", fg: "var(--warn-600)" }
    : st.tone === "info"
    ? { bg: "var(--info-50)", fg: "var(--info-600)" }
    : { bg: "var(--brand-50)", fg: "var(--brand-700)" };
  return (
    <Link href={st.href} className="surface" style={{ padding: 18, display: "block", textDecoration: "none", color: "inherit" }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <div className="stat-label">{st.label}</div>
        <span style={{ display: "inline-flex", width: 28, height: 28, borderRadius: 7, alignItems: "center", justifyContent: "center", background: tone.bg, color: tone.fg }}>{st.icon}</span>
      </div>
      <div className="mono" style={{ fontSize: 28, fontWeight: 700, marginTop: 10, letterSpacing: "-.02em", color: "var(--ink-900)" }}>{st.value}</div>
      <div style={{ fontSize: 12, color: "var(--ink-500)", marginTop: 4 }}>{st.sub}</div>
    </Link>
  );
}

export default function DashboardPage() {
  const router = useRouter();
  const [uname, setUname] = useState("");
  const [role, setRole] = useState("");
  const [aiOn, setAiOn] = useState<boolean | null>(null);
  const [online, setOnline] = useState<number | null>(null);
  const [omzet, setOmzet] = useState<number | null>(null);
  const [indexed, setIndexed] = useState<number | null>(null);
  const [branchN, setBranchN] = useState<number | null>(null);
  const [activity, setActivity] = useState<MonitoringActivity[]>([]);

  useEffect(() => {
    const u = getUser();
    const token = getToken();
    if (!token) {
      router.replace("/login");
      return;
    }
    if (u?.role === "pembeli") {
      router.replace("/search");
      return;
    }
    setUname(u?.username ?? "");
    setRole(u?.role ?? "");
    const admin = u?.role === "admin";

    getAiStatus(token).then((d) => setAiOn(d.available)).catch(() => setAiOn(null));
    if (admin) {
      getMonitoring(token).then((d) => { setOnline(d.online_count); setActivity(d.recent_activity?.slice(0, 6) ?? []); }).catch(() => {});
      getSalesRecap(token).then((d) => setOmzet(d.summary?.omzet ?? 0)).catch(() => {});
      getIndexStatus(token).then((d) => setIndexed(d.total_indexed)).catch(() => {});
    } else {
      getBranchOrdersCount(token).then((d) => setBranchN(d.count)).catch(() => {});
    }
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
      return new Date().toLocaleDateString("id-ID", { weekday: "long", day: "numeric", month: "long", year: "numeric" }).toUpperCase();
    } catch {
      return "";
    }
  }, []);

  const isAdmin = role === "admin";

  const stats: Stat[] = useMemo(() => {
    const val = (n: number | null) => (n == null ? "—" : new Intl.NumberFormat("id-ID").format(n));
    if (isAdmin) {
      return [
        { label: "User online", value: online == null ? "—" : String(online), sub: "aktif ≤ 5 menit", icon: I.users, href: "/admin/monitoring" },
        { label: "Omzet", value: omzet == null ? "—" : rupiah(omzet), sub: "total pesanan lunas", icon: I.money, href: "/admin/penjualan", tone: "brand" },
        { label: "Part terindeks (foto)", value: val(indexed), sub: "galeri cari-by-foto", icon: I.grid, href: "/admin/index", tone: "info" },
        { label: "Asisten AI", value: aiOn == null ? "—" : aiOn ? "Aktif" : "Nonaktif", sub: aiOn ? "DeepSeek + tool live" : "belum dikonfigurasi", icon: I.ai, href: "/asisten", tone: aiOn === false ? "warn" : "brand" },
      ];
    }
    return [
      { label: "Pesanan masuk", value: branchN == null ? "—" : String(branchN), sub: "menunggu diproses", icon: I.cart, href: "/cabang/pesanan" },
      { label: "Asisten AI", value: aiOn == null ? "—" : aiOn ? "Aktif" : "Nonaktif", sub: aiOn ? "DeepSeek + tool live" : "belum dikonfigurasi", icon: I.ai, href: "/asisten", tone: aiOn === false ? "warn" : "brand" },
    ];
  }, [isAdmin, online, omzet, indexed, aiOn, branchN]);

  const quick = [
    { label: "Cari Part", desc: "Part number / nama", icon: I.search, href: "/search" },
    { label: "Tanya Asisten", desc: "Stok, harga, BOM per-VIN", icon: I.ai, href: "/asisten" },
    { label: "Cari by Foto", desc: "Kenali part dari gambar", icon: I.camera, href: "/search-image" },
    { label: "Populasi Unit", desc: "Armada terdaftar", icon: I.truck, href: "/populasi" },
    { label: "Harga", desc: "Daftar & batch harga", icon: I.money, href: "/harga" },
    { label: "Bandingkan Part", desc: "Dua part berdampingan", icon: I.compare, href: "/compare" },
  ];

  function actionText(a: MonitoringActivity): string {
    const t = a.target ? ` ${a.target}` : "";
    return `${a.username} — ${a.action}${t}`;
  }

  return (
    <AppShell active="/dashboard" title="Dashboard">
      <div className="mx-auto" style={{ maxWidth: 1200, padding: "28px 28px 40px" }}>
        {/* Header greeting */}
        <div style={{ display: "flex", alignItems: "flex-end", justifyContent: "space-between", gap: 16, flexWrap: "wrap" }}>
          <div>
            <div style={{ fontSize: 12, color: "var(--ink-500)", fontWeight: 550, textTransform: "uppercase", letterSpacing: ".08em" }}>{todayLabel}</div>
            <h1 style={{ margin: "4px 0 0", fontSize: 26, fontWeight: 700, letterSpacing: "-.02em" }}>
              {greeting}{uname ? `, ${uname}` : ""} 👷
            </h1>
          </div>
          <div style={{ display: "flex", gap: 8 }}>
            <Link href="/search" className="btn btn-primary btn-lg">{I.search} Cari Part</Link>
            <Link href="/asisten" className="btn btn-secondary btn-lg">{I.ai} Tanya Asisten</Link>
          </div>
        </div>

        {/* Stat cards */}
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))", gap: 14, marginTop: 22 }}>
          {stats.map((st) => <StatCard key={st.label} st={st} />)}
        </div>

        {/* Quick access + activity */}
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(340px, 1fr))", gap: 14, marginTop: 14 }}>
          <div className="surface" style={{ overflow: "hidden" }}>
            <div style={{ padding: "14px 18px", borderBottom: "1px solid var(--ink-150)", fontSize: 13.5, fontWeight: 650 }}>Akses cepat</div>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 1, background: "var(--ink-150)" }}>
              {quick.map((q) => (
                <Link key={q.href} href={q.href} style={{ display: "flex", alignItems: "center", gap: 12, padding: "14px 16px", background: "var(--paper)", textDecoration: "none", color: "inherit" }}>
                  <span style={{ display: "inline-flex", width: 34, height: 34, borderRadius: 8, alignItems: "center", justifyContent: "center", background: "var(--brand-50)", color: "var(--brand-700)", border: "1px solid var(--brand-100)", flexShrink: 0 }}>{q.icon}</span>
                  <span style={{ minWidth: 0 }}>
                    <span style={{ display: "block", fontSize: 13.5, fontWeight: 600 }}>{q.label}</span>
                    <span style={{ display: "block", fontSize: 11.5, color: "var(--ink-500)" }}>{q.desc}</span>
                  </span>
                </Link>
              ))}
            </div>
          </div>

          <div className="surface" style={{ overflow: "hidden" }}>
            <div style={{ padding: "14px 18px", borderBottom: "1px solid var(--ink-150)", fontSize: 13.5, fontWeight: 650 }}>
              {isAdmin ? "Aktivitas terbaru" : "Status sistem"}
            </div>
            {isAdmin ? (
              <div style={{ display: "flex", flexDirection: "column" }}>
                {activity.length === 0 && <div style={{ padding: "16px 18px", fontSize: 12.5, color: "var(--ink-500)" }}>Belum ada aktivitas terbaru.</div>}
                {activity.map((a, i) => (
                  <div key={i} style={{ display: "flex", gap: 10, padding: "11px 18px", borderBottom: "1px solid var(--ink-100)", alignItems: "flex-start" }}>
                    <span style={{ display: "inline-flex", width: 26, height: 26, borderRadius: 7, alignItems: "center", justifyContent: "center", flexShrink: 0, background: "var(--info-50)", color: "var(--info-600)", marginTop: 1 }}>{I.pulse}</span>
                    <div style={{ minWidth: 0 }}>
                      <div style={{ fontSize: 12.5, color: "var(--ink-800)", lineHeight: 1.45 }}>{actionText(a)}</div>
                      {a.created_at && <div style={{ fontSize: 11, color: "var(--ink-400)", marginTop: 2 }}>{new Date(a.created_at).toLocaleString("id-ID")}</div>}
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <div style={{ display: "flex", flexDirection: "column" }}>
                <Row k="Akun" v={`${uname || "—"} · ${role || "user"}`} />
                <Row k="Asisten AI" v={aiOn == null ? "…" : aiOn ? "Aktif" : "Nonaktif"} tone={aiOn === false ? "warn" : "brand"} />
                <Row k="EPC Sinotruk" v="Live" tone="brand" />
              </div>
            )}
          </div>
        </div>
      </div>
    </AppShell>
  );
}

function Row({ k, v, tone }: { k: string; v: string; tone?: "brand" | "warn" }) {
  const color = tone === "warn" ? "var(--warn-600)" : tone === "brand" ? "var(--brand-700)" : "var(--ink-800)";
  return (
    <div style={{ display: "flex", justifyContent: "space-between", gap: 12, padding: "12px 18px", borderBottom: "1px solid var(--ink-100)", fontSize: 13 }}>
      <span style={{ color: "var(--ink-500)" }}>{k}</span>
      <span style={{ fontWeight: 600, color }}>{v}</span>
    </div>
  );
}
