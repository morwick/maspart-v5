"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import AppShell from "@/components/AppShell";
import { ApiError, getBranchSales, type SalesRecap } from "@/lib/api";
import { clearSession, getToken } from "@/lib/auth";
import { ORDER_STATUS, rp } from "@/lib/order-ui";

const monthLabel = (m: string) => {
  const [y, mo] = m.split("-");
  const names = ["Jan", "Feb", "Mar", "Apr", "Mei", "Jun", "Jul", "Agu", "Sep", "Okt", "Nov", "Des"];
  return `${names[Number(mo) - 1] ?? mo} ${y}`;
};

export default function BranchSalesPage() {
  const router = useRouter();
  const [data, setData] = useState<SalesRecap | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    const token = getToken();
    if (!token) return router.replace("/login");
    getBranchSales(token)
      .then(setData)
      .catch((err) => {
        if (err instanceof ApiError && err.status === 401) {
          clearSession();
          return router.replace("/login");
        }
        // Bukan akun cabang → kembalikan ke pencarian.
        if (err instanceof ApiError && err.status === 403) return router.replace("/search");
        setError(err instanceof Error ? err.message : "Gagal memuat");
      })
      .finally(() => setLoaded(true));
  }, [router]);

  const s = data?.summary;
  const branchName = data?.by_gudang?.[0]?.gudang ?? "";
  const maxMonth = Math.max(1, ...(data?.by_month.map((m) => m.omzet) ?? [1]));

  return (
    <AppShell
      active="/cabang/penjualan"
      title="Penjualan Cabang"
      sub={branchName ? `Rekap omzet & pesanan · ${branchName}` : "Rekap omzet & pesanan cabang Anda"}
    >
      <div className="mx-auto w-full max-w-6xl px-4 py-5 sm:px-6">
        {error && <div className="alert alert-error" style={{ marginBottom: 16 }}>{error}</div>}

        {!data ? (
          <div className="surface grid place-items-center" style={{ height: 200, color: "var(--ink-500)" }}>
            {loaded ? "Tidak ada data." : "Memuat…"}
          </div>
        ) : (
          <>
            {/* KPI */}
            <div className="grid gap-3 mb-5" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(160px, 1fr))" }}>
              <div className="surface surface-pad">
                <div className="stat-label">Omzet (terjual)</div>
                <div className="stat-value mono" style={{ color: "var(--brand-700)" }}>{rp(s?.omzet ?? 0)}</div>
              </div>
              <div className="surface surface-pad">
                <div className="stat-label">Pesanan Terjual</div>
                <div className="stat-value">{(s?.paid_orders ?? 0).toLocaleString("id-ID")}</div>
              </div>
              <div className="surface surface-pad">
                <div className="stat-label">Item Terjual</div>
                <div className="stat-value">{(s?.items_sold ?? 0).toLocaleString("id-ID")}</div>
              </div>
              <div className="surface surface-pad">
                <div className="stat-label">Total Pesanan</div>
                <div className="stat-value">{(s?.total_orders ?? 0).toLocaleString("id-ID")}</div>
              </div>
            </div>

            <div className="grid gap-4 md:grid-cols-2">
              {/* Per bulan */}
              <div className="surface surface-pad">
                <div className="mb-3" style={{ fontSize: 14, fontWeight: 600 }}>Omzet per Bulan</div>
                {data.by_month.length === 0 ? (
                  <div style={{ fontSize: 13, color: "var(--ink-400)" }}>Belum ada penjualan.</div>
                ) : (
                  <div className="flex flex-col gap-2">
                    {data.by_month.map((m) => (
                      <div key={m.month}>
                        <div className="flex justify-between" style={{ fontSize: 12.5 }}>
                          <span style={{ color: "var(--ink-600)" }}>{monthLabel(m.month)} · {m.count} pesanan</span>
                          <span className="mono">{rp(m.omzet)}</span>
                        </div>
                        <div style={{ height: 6, borderRadius: 99, background: "var(--ink-100)", marginTop: 3 }}>
                          <div style={{ height: "100%", borderRadius: 99, background: "var(--brand-600)", width: `${(m.omzet / maxMonth) * 100}%` }} />
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </div>

              {/* Per status */}
              <div className="surface surface-pad">
                <div className="mb-3" style={{ fontSize: 14, fontWeight: 600 }}>Pesanan per Status</div>
                {Object.keys(data.by_status).length === 0 ? (
                  <div style={{ fontSize: 13, color: "var(--ink-400)" }}>Belum ada pesanan.</div>
                ) : (
                  <table className="tbl">
                    <thead>
                      <tr><th>Status</th><th className="num">Jumlah</th><th className="num">Nilai</th></tr>
                    </thead>
                    <tbody>
                      {Object.entries(data.by_status).map(([st, v]) => (
                        <tr key={st}>
                          <td><span className={"pill " + (ORDER_STATUS[st]?.pill || "")}>{ORDER_STATUS[st]?.label || st}</span></td>
                          <td className="num">{v.count}</td>
                          <td className="num mono">{rp(v.omzet)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </div>
            </div>

            {/* Part terlaris */}
            <div className="surface surface-pad mt-4">
              <div className="mb-3" style={{ fontSize: 14, fontWeight: 600 }}>Part Terlaris</div>
              {data.top_parts.length === 0 ? (
                <div style={{ fontSize: 13, color: "var(--ink-400)" }}>Belum ada penjualan.</div>
              ) : (
                <table className="tbl">
                  <thead>
                    <tr><th>Part</th><th className="num">Qty</th><th className="num">Omzet</th></tr>
                  </thead>
                  <tbody>
                    {data.top_parts.map((p) => (
                      <tr key={p.part_number}>
                        <td>
                          <div className="pn">{p.part_number}</div>
                          <div className="truncate" style={{ fontSize: 11.5, color: "var(--ink-500)" }}>{p.name}</div>
                        </td>
                        <td className="num">{p.qty}</td>
                        <td className="num mono">{rp(p.omzet)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>

            <p style={{ fontSize: 11.5, color: "var(--ink-400)", marginTop: 12 }}>
              Omzet dihitung dari harga jual barang (subtotal, tanpa ongkir) untuk pesanan yang sudah dibayar (Diproses/Dikirim/Selesai).
            </p>
          </>
        )}
      </div>
    </AppShell>
  );
}
