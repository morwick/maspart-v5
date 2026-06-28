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
    <AppShell active="/populasi" title="Populasi Unit" sub="Daftar unit & spesifikasi">
      <div className="mx-auto w-full max-w-6xl px-4 py-5 sm:px-6">
        <h2 className="mb-4 text-base font-semibold">
          🚛 Populasi <span className="text-brand">Unit</span>
        </h2>

        {/* Filter & cari */}
        <div className="mb-4 rounded-xl bg-white p-4 ring-1 ring-zinc-200">
          <form onSubmit={applySearch} className="mb-3 flex gap-2">
            <input
              value={qInput}
              onChange={(e) => setQInput(e.target.value)}
              placeholder="Cari (semua kolom)…"
              className="flex-1 rounded-lg border border-zinc-300 px-3 py-2 text-sm outline-none focus:border-brand focus:ring-2 focus:ring-brand/20"
            />
            <button
              type="submit"
              disabled={loading}
              className="rounded-lg bg-brand px-5 py-2 text-sm font-semibold text-white hover:bg-green-700 disabled:opacity-60"
            >
              Cari
            </button>
          </form>

          {Object.keys(fopts).length > 0 && (
            <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
              {Object.entries(fopts).map(([col, opts]) => (
                <label key={col} className="text-xs text-zinc-500">
                  {col}
                  <select
                    value={filters[col] ?? "Semua"}
                    onChange={(e) => changeFilter(col, e.target.value)}
                    className="mt-1 block w-full rounded-lg border border-zinc-300 px-2 py-1.5 text-sm text-zinc-800 outline-none focus:border-brand"
                  >
                    <option value="Semua">Semua</option>
                    {opts.map((o) => (
                      <option key={o} value={o}>
                        {o}
                      </option>
                    ))}
                  </select>
                </label>
              ))}
            </div>
          )}
        </div>

        {error && (
          <p className="mb-4 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700 ring-1 ring-red-100">
            {error}
          </p>
        )}

        {data && (
          <div className="mb-3 flex flex-wrap items-center justify-between gap-2 text-sm text-zinc-500">
            <span>
              Total <b className="text-zinc-700">{data.total}</b> unit · Hasil
              filter <b className="text-zinc-700">{data.total_filtered}</b>
            </span>
            <button
              onClick={handleExport}
              disabled={data.total_filtered === 0}
              className="rounded-lg border border-zinc-300 px-3 py-1.5 font-medium text-zinc-700 hover:bg-zinc-50 disabled:opacity-50"
            >
              ⬇️ Download Excel
            </button>
          </div>
        )}

        {data && data.rows.length > 0 ? (
          <div className="overflow-x-auto rounded-xl ring-1 ring-zinc-200">
            <table className="tbl">
              <thead className="bg-zinc-50 text-left text-zinc-600">
                <tr>
                  {cols.map((c) => {
                    const active = sortCol === c;
                    return (
                      <th
                        key={c}
                        onClick={() => changeSort(c)}
                        title="Klik untuk mengurutkan"
                        className="cursor-pointer select-none whitespace-nowrap px-3 py-2 font-medium transition hover:bg-zinc-100"
                      >
                        <span className="inline-flex items-center gap-1">
                          {c}
                          <span className={active ? "text-brand" : "text-zinc-300"}>
                            {active ? (sortDir === "asc" ? "▲" : "▼") : "↕"}
                          </span>
                        </span>
                      </th>
                    );
                  })}
                </tr>
              </thead>
              <tbody className="divide-y divide-zinc-100 bg-white">
                {data.rows.map((row, i) => (
                  <tr key={i} className="hover:bg-zinc-50">
                    {cols.map((c) => (
                      <td key={c} className="whitespace-nowrap px-3 py-2">
                        {row[c] ?? ""}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          data &&
          !loading && (
            <p className="text-sm text-zinc-500">Tidak ada data yang cocok.</p>
          )
        )}

        {data && data.total_pages > 1 && (
          <div className="mt-4 flex items-center justify-center gap-2 text-sm">
            <button
              onClick={() => goToPage(page - 1)}
              disabled={page <= 1 || loading}
              className="rounded-lg border border-zinc-300 px-3 py-1.5 font-medium text-zinc-700 hover:bg-zinc-50 disabled:opacity-40"
            >
              ← Sebelumnya
            </button>
            <span className="px-2 text-zinc-500">
              Halaman {page} / {data.total_pages}
            </span>
            <button
              onClick={() => goToPage(page + 1)}
              disabled={page >= data.total_pages || loading}
              className="rounded-lg border border-zinc-300 px-3 py-1.5 font-medium text-zinc-700 hover:bg-zinc-50 disabled:opacity-40"
            >
              Berikutnya →
            </button>
          </div>
        )}
      </div>
    </AppShell>
  );
}
