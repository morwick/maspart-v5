"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import AppShell from "@/components/AppShell";
import {
  ApiError,
  downloadBlob,
  exportPopulasi,
  getPopulasi,
  type PopulasiResponse,
} from "@/lib/api";
import { clearSession, getToken } from "@/lib/auth";

const PAGE_SIZE = 50;

export default function PopulasiPage() {
  const router = useRouter();
  const [data, setData] = useState<PopulasiResponse | null>(null);
  const [qInput, setQInput] = useState("");
  const [q, setQ] = useState("");
  const [filters, setFilters] = useState<Record<string, string>>({});
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [sortCol, setSortCol] = useState<string | null>(null);
  const [sortDir, setSortDir] = useState<"asc" | "desc">("asc");

  const load = useCallback(
    async (
      p: number,
      keyword: string,
      flt: Record<string, string>,
      sCol: string | null,
      sDir: "asc" | "desc",
    ) => {
      const token = getToken();
      if (!token) return router.replace("/login");
      setLoading(true);
      setError(null);
      try {
        const res = await getPopulasi(token, {
          q: keyword,
          filters: flt,
          page: p,
          pageSize: PAGE_SIZE,
          sort: sCol ?? undefined,
          dir: sDir,
        });
        setData(res);
        setPage(res.page);
      } catch (err) {
        if (err instanceof ApiError && err.status === 401) {
          clearSession();
          return router.replace("/login");
        }
        setError(err instanceof Error ? err.message : "Gagal memuat data");
      } finally {
        setLoading(false);
      }
    },
    [router],
  );

  useEffect(() => {
    if (!getToken()) {
      router.replace("/login");
      return;
    }
    load(1, "", {}, null, "asc");
  }, [router, load]);

  function applySearch(e: React.FormEvent) {
    e.preventDefault();
    setQ(qInput);
    load(1, qInput, filters, sortCol, sortDir);
  }

  function changeFilter(col: string, val: string) {
    const next = { ...filters };
    if (val === "Semua") delete next[col];
    else next[col] = val;
    setFilters(next);
    load(1, q, next, sortCol, sortDir);
  }

  function changeSort(col: string) {
    // Klik kolom yang sama → balik arah; kolom lain → mulai asc.
    const dir: "asc" | "desc" = sortCol === col && sortDir === "asc" ? "desc" : "asc";
    setSortCol(col);
    setSortDir(dir);
    load(1, q, filters, col, dir); // urutkan seluruh data, kembali ke halaman 1
  }

  function goToPage(p: number) {
    if (!data || p < 1 || p > data.total_pages || loading) return;
    load(p, q, filters, sortCol, sortDir);
  }

  async function handleExport() {
    const token = getToken();
    if (!token) return router.replace("/login");
    try {
      const blob = await exportPopulasi(token, { q, filters });
      downloadBlob(blob, "populasi_unit.xlsx");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Gagal export");
    }
  }

  const cols = data?.columns ?? [];
  const fopts = data?.filter_options ?? {};

  return (
    <AppShell active="/populasi" title="Populasi Unit" sub="Daftar unit & spesifikasi armada">
      <div className="mx-auto w-full max-w-6xl" style={{ height: "100%", display: "flex", flexDirection: "column", padding: "22px 28px 18px" }}>
        {/* Toolbar: cari + filter + export — tetap diam (tidak ikut scroll) */}
        <form onSubmit={applySearch} className="flex flex-wrap items-center gap-2" style={{ flexShrink: 0 }}>
          <div style={{ position: "relative", flex: 1, minWidth: 240 }}>
            <span style={{ position: "absolute", left: 12, top: "50%", transform: "translateY(-50%)", color: "var(--ink-400)", display: "inline-flex" }}>
              <svg width={16} height={16} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.6} strokeLinecap="round"><circle cx="11" cy="11" r="7" /><path d="m20 20-3.5-3.5" /></svg>
            </span>
            <input
              className="input"
              value={qInput}
              onChange={(e) => setQInput(e.target.value)}
              placeholder="Cari customer, VIN, model…"
              style={{ paddingLeft: 38 }}
            />
          </div>
          {Object.entries(fopts).map(([col, opts]) => (
            <select
              key={col}
              className="select"
              value={filters[col] ?? "Semua"}
              onChange={(e) => changeFilter(col, e.target.value)}
              style={{ width: "auto", minWidth: 130 }}
              title={col}
            >
              <option value="Semua">{col}: Semua</option>
              {opts.map((o) => (
                <option key={o} value={o}>{o}</option>
              ))}
            </select>
          ))}
          <button type="submit" className="btn btn-primary" disabled={loading}>
            {loading ? "Memuat…" : "Cari"}
          </button>
          <button
            type="button"
            onClick={handleExport}
            disabled={!data || data.total_filtered === 0}
            className="btn btn-secondary"
          >
            ⬇ Export
          </button>
        </form>

        {error && <div className="alert alert-error" style={{ marginTop: 14, flexShrink: 0 }}>{error}</div>}

        {data && (
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1" style={{ marginTop: 14, flexShrink: 0, fontSize: 12.5, color: "var(--ink-500)" }}>
            <span>Total <b style={{ color: "var(--ink-800)" }} className="mono">{data.total.toLocaleString("id-ID")}</b> unit</span>
            <span>·</span>
            <span>Hasil filter <b style={{ color: "var(--ink-800)" }} className="mono">{data.total_filtered.toLocaleString("id-ID")}</b></span>
          </div>
        )}

        {/* Hanya area ini yang menggulir — header tabel (thead) sticky di atasnya */}
        <div className="surface" style={{ marginTop: 12, flex: 1, minHeight: 0, overflow: "auto" }}>
          {data && data.rows.length > 0 ? (
            <table className="tbl">
              <thead>
                <tr>
                  {cols.map((c) => {
                    const active = sortCol === c;
                    return (
                      <th
                        key={c}
                        onClick={() => changeSort(c)}
                        title="Klik untuk mengurutkan"
                        style={{ cursor: "pointer", userSelect: "none", whiteSpace: "nowrap" }}
                      >
                        {c}{" "}
                        <span style={{ opacity: active ? 1 : 0.3, fontSize: 11, color: active ? "var(--brand-700)" : "inherit" }}>
                          {active ? (sortDir === "asc" ? "▲" : "▼") : "▲▼"}
                        </span>
                      </th>
                    );
                  })}
                </tr>
              </thead>
              <tbody>
                {data.rows.map((row, i) => (
                  <tr key={i}>
                    {cols.map((c) => {
                      const isVin = /rangka|vin|mesin/i.test(c);
                      return (
                        <td key={c} className={isVin ? "pn" : undefined} style={{ whiteSpace: "nowrap", color: isVin ? undefined : "var(--ink-700)" }}>
                          {row[c] ?? ""}
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <div className="grid place-items-center" style={{ height: "100%", minHeight: 160, color: "var(--ink-500)", fontSize: 13.5 }}>
              {loading ? "Memuat…" : "Tidak ada data yang cocok."}
            </div>
          )}
        </div>

        {data && data.total_pages > 1 && (
          <div className="flex items-center justify-center gap-2" style={{ marginTop: 14, flexShrink: 0, fontSize: 13 }}>
            <button className="btn btn-secondary btn-sm" onClick={() => goToPage(page - 1)} disabled={page <= 1 || loading}>← Sebelumnya</button>
            <span style={{ color: "var(--ink-500)", padding: "0 8px" }}>Halaman {page} / {data.total_pages}</span>
            <button className="btn btn-secondary btn-sm" onClick={() => goToPage(page + 1)} disabled={page >= data.total_pages || loading}>Berikutnya →</button>
          </div>
        )}
      </div>
    </AppShell>
  );
}
