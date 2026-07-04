"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import AppShell from "@/components/AppShell";
import { ApiError, getSearchMisses, resolveSearchMiss, type SearchMiss } from "@/lib/api";
import { clearSession, getToken, getUser } from "@/lib/auth";

export default function SearchMissesPage() {
  const router = useRouter();
  const [rows, setRows] = useState<SearchMiss[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    const token = getToken();
    if (!token) return router.replace("/login");
    setLoading(true);
    setError(null);
    try {
      const res = await getSearchMisses(token);
      setRows(res.misses);
      setTotal(res.total);
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        clearSession();
        return router.replace("/login");
      }
      setError(err instanceof Error ? err.message : "Gagal memuat");
    } finally {
      setLoading(false);
    }
  }, [router]);

  useEffect(() => {
    if (!getToken()) {
      router.replace("/login");
      return;
    }
    if (getUser()?.role !== "admin") {
      router.replace("/search");
      return;
    }
    load();
  }, [router, load]);

  async function resolve(q: string) {
    const token = getToken();
    if (!token) return;
    setRows((r) => r.filter((x) => x.query !== q)); // optimistis
    try {
      await resolveSearchMiss(token, q);
    } catch {
      load(); // gagal → muat ulang
    }
  }

  return (
    <AppShell active="/admin/search-misses" title="Pencarian Nihil" sub="Query yang 0 hasil — kandidat sinonim">
      <div className="mx-auto w-full max-w-4xl px-4 py-6 sm:px-7">
        <div className="flex flex-wrap items-center gap-3" style={{ marginBottom: 14 }}>
          <p style={{ fontSize: 13.5, color: "var(--ink-500)", flex: 1, minWidth: 220 }}>
            Istilah yang dicari user (di Cari Part & Asisten) tapi <b>tidak menemukan apa pun</b>.
            Tambahkan istilah yang relevan ke <span className="mono">data/sinonim/sinonim.json</span>,
            lalu tandai <b>Selesai</b> untuk menghapusnya dari daftar.
          </p>
          <button className="btn btn-secondary btn-sm" onClick={load} disabled={loading}>
            {loading ? "Memuat…" : "↻ Muat ulang"}
          </button>
        </div>

        {error && <div className="alert alert-error" style={{ marginBottom: 14 }}>{error}</div>}

        <div style={{ fontSize: 12.5, color: "var(--ink-500)", marginBottom: 10 }}>
          Total <b className="mono" style={{ color: "var(--ink-800)" }}>{total}</b> istilah unik belum ketemu.
        </div>

        {rows.length > 0 ? (
          <div className="surface" style={{ overflow: "auto" }}>
            <table className="tbl">
              <thead>
                <tr>
                  <th>Query</th>
                  <th className="num" style={{ width: 90 }}>Frekuensi</th>
                  <th style={{ width: 130 }}>Sumber</th>
                  <th style={{ width: 90 }} />
                </tr>
              </thead>
              <tbody>
                {rows.map((m) => (
                  <tr key={m.query}>
                    <td style={{ fontWeight: 500 }}>{m.query}</td>
                    <td className="num mono">{m.count}</td>
                    <td>
                      {(m.sources || []).map((s) => (
                        <span key={s} className="pill" style={{ marginRight: 4, height: 20, fontSize: 10.5, padding: "0 7px" }}>{s}</span>
                      ))}
                    </td>
                    <td style={{ textAlign: "right" }}>
                      <button className="btn btn-secondary btn-sm" onClick={() => resolve(m.query)} title="Hapus dari daftar (sudah ditangani)">
                        Selesai
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          !loading && (
            <div className="surface grid place-items-center" style={{ height: 160, color: "var(--ink-500)", fontSize: 13.5 }}>
              Belum ada pencarian nihil yang tercatat. 🎉
            </div>
          )
        )}
      </div>
    </AppShell>
  );
}
