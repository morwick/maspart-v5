"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import AppShell from "@/components/AppShell";
import {
  ApiError,
  downloadBlob,
  exportStokList,
  getStokList,
  type StokListResponse,
} from "@/lib/api";
import { clearSession, getToken } from "@/lib/auth";

const PAGE_SIZE = 50;

export default function StokPage() {
  const router = useRouter();
  const [data, setData] = useState<StokListResponse | null>(null);
  const [qInput, setQInput] = useState("");
  const [q, setQ] = useState("");
  const [sort, setSort] = useState("pn");
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const on401 = useCallback(
    (err: unknown): boolean => {
      if (err instanceof ApiError && err.status === 401) {
        clearSession();
        router.replace("/login");
        return true;
      }
      return false;
    },
    [router],
  );

  const load = useCallback(
    async (p: number, keyword: string, srt: string) => {
      const token = getToken();
      if (!token) return router.replace("/login");
      setLoading(true);
      setError(null);
      try {
        const res = await getStokList(token, { q: keyword, sort: srt, page: p, pageSize: PAGE_SIZE });
        setData(res);
        setPage(res.page);
      } catch (err) {
        if (!on401(err)) setError(err instanceof Error ? err.message : "Gagal memuat");
      } finally {
        setLoading(false);
      }
    },
    [router, on401],
  );

  useEffect(() => {
    if (!getToken()) {
      router.replace("/login");
      return;
    }
    load(1, "", "pn");
  }, [router, load]);

  async function handleExport() {
    const token = getToken();
    if (!token) return;
    try {
      const blob = await exportStokList(token, { q, sort });
      downloadBlob(blob, "stok_accurate.xlsx");
    } catch (err) {
      if (!on401(err)) setError(err instanceof Error ? err.message : "Gagal export");
    }
  }

  const notConfigured = data && data.configured === false;
  const sessionExpired = data && data.session_expired;
  const fetchError = data && data.error;

  return (
    <AppShell active="/stok" title="Stok" sub="Stok seluruh barang dari Accurate (sinkron tiap 5 jam)">
      <div className="mx-auto w-full max-w-5xl px-4 py-6 sm:px-7">
        <div className="flex flex-wrap gap-2">
          <form
            onSubmit={(e) => {
              e.preventDefault();
              setQ(qInput);
              load(1, qInput, sort);
            }}
            className="flex flex-1 gap-2"
            style={{ minWidth: 240 }}
          >
            <input
              className="input mono"
              value={qInput}
              onChange={(e) => setQInput(e.target.value)}
              placeholder="Cari Part Number / Part Name…"
            />
            <button className="btn btn-primary">Cari</button>
          </form>
          <select
            className="select"
            style={{ width: "auto", minWidth: 150 }}
            value={sort}
            onChange={(e) => {
              setSort(e.target.value);
              load(1, q, e.target.value);
            }}
          >
            <option value="pn">Urut: Part Number</option>
            <option value="name">Urut: Part Name</option>
            <option value="stok_desc">Stok ↓</option>
            <option value="stok_asc">Stok ↑</option>
          </select>
        </div>

        {error && <div className="alert alert-error" style={{ marginTop: 12 }}>{error}</div>}

        {notConfigured && (
          <div className="alert" style={{ marginTop: 16, background: "var(--warn-50)", color: "var(--warn-600)", borderColor: "#f6d9a8" }}>
            Sesi Accurate belum tersedia. Hubungi admin untuk mengaktifkan koneksi Accurate.
          </div>
        )}
        {sessionExpired && (
          <div className="alert" style={{ marginTop: 16, background: "var(--warn-50)", color: "var(--warn-600)", borderColor: "#f6d9a8" }}>
            Sesi Accurate kadaluarsa. Coba lagi beberapa saat, atau minta admin memperbarui sesi.
          </div>
        )}
        {fetchError && (
          <div className="alert alert-error" style={{ marginTop: 16 }}>
            Gagal mengambil stok dari Accurate. Coba lagi beberapa saat.
          </div>
        )}

        {data && data.configured !== false && !sessionExpired && !fetchError && (
          <div className="flex flex-wrap items-center justify-between gap-2" style={{ marginTop: 12, fontSize: 12.5, color: "var(--ink-500)" }}>
            <span>
              Total <b className="mono" style={{ color: "var(--ink-800)" }}>{data.total.toLocaleString("id-ID")}</b> · Hasil filter{" "}
              <b className="mono" style={{ color: "var(--ink-800)" }}>{data.total_filtered.toLocaleString("id-ID")}</b>
            </span>
            <button className="btn btn-secondary btn-sm" onClick={handleExport} disabled={data.total_filtered === 0}>⬇ Export Excel</button>
          </div>
        )}

        {data && data.rows.length > 0 && (
          <div className="surface" style={{ marginTop: 12, overflow: "auto" }}>
            <table className="tbl">
              <thead>
                <tr>
                  <th>Part Number</th>
                  <th>Part Name</th>
                  <th className="num">Stok</th>
                  <th>Satuan</th>
                </tr>
              </thead>
              <tbody>
                {data.rows.map((r, i) => (
                  <tr key={i}>
                    <td className="pn">{r["Part Number"]}</td>
                    <td style={{ fontWeight: 500 }}>{r["Part Name"]}</td>
                    <td className="num mono">{r["Stok"]}</td>
                    <td style={{ color: "var(--ink-500)" }}>{r["Satuan"]}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {data && data.configured !== false && !sessionExpired && !fetchError && data.rows.length === 0 && (
          <p style={{ marginTop: 16, fontSize: 13, color: "var(--ink-500)" }}>
            Tidak ada barang yang cocok.
          </p>
        )}

        {data && data.total_pages > 1 && (
          <div className="flex items-center justify-center gap-2" style={{ marginTop: 16, fontSize: 13 }}>
            <button className="btn btn-secondary btn-sm" onClick={() => load(page - 1, q, sort)} disabled={page <= 1 || loading}>← Sebelumnya</button>
            <span style={{ color: "var(--ink-500)", padding: "0 8px" }}>Halaman {page} / {data.total_pages}</span>
            <button className="btn btn-secondary btn-sm" onClick={() => load(page + 1, q, sort)} disabled={page >= data.total_pages || loading}>Berikutnya →</button>
          </div>
        )}
      </div>
    </AppShell>
  );
}
