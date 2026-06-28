"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import AppShell from "@/components/AppShell";
import {
  ApiError,
  getIndexStatus,
  indexBulk,
  indexPart,
  reloadGallery,
  type IndexResult,
  type IndexStatusInfo,
} from "@/lib/api";
import { clearSession, getToken, getUser } from "@/lib/auth";

export default function AdminIndexPage() {
  const router = useRouter();
  const [status, setStatus] = useState<IndexStatusInfo | null>(null);
  const [pn, setPn] = useState("");
  const [bulkText, setBulkText] = useState("");
  const [reindex, setReindex] = useState(false);
  const [results, setResults] = useState<IndexResult[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [reloading, setReloading] = useState(false);
  const [reloadMsg, setReloadMsg] = useState<string | null>(null);

  const loadStatus = useCallback(async () => {
    const token = getToken();
    if (!token) return;
    try {
      setStatus(await getIndexStatus(token));
    } catch {
      /* ignore */
    }
  }, []);

  useEffect(() => {
    if (!getToken()) return router.replace("/login");
    if (getUser()?.role !== "admin") {
      router.replace("/search");
      return;
    }
    loadStatus();
  }, [router, loadStatus]);

  function fail(e: unknown) {
    if (e instanceof ApiError && e.status === 401) {
      clearSession();
      router.replace("/login");
      return;
    }
    setError(e instanceof Error ? e.message : "Gagal");
  }

  async function runSingle() {
    const token = getToken();
    if (!token || !pn.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const r = await indexPart(token, pn.trim().toUpperCase(), reindex);
      setResults([r]);
      await loadStatus();
    } catch (e) {
      fail(e);
    } finally {
      setBusy(false);
    }
  }

  async function runReload() {
    const token = getToken();
    if (!token) return;
    setReloading(true);
    setError(null);
    setReloadMsg(null);
    try {
      const r = await reloadGallery(token);
      if (r.ok) {
        setReloadMsg(`Galeri dimuat ulang: ${r.total.toLocaleString("id-ID")} foto.`);
        await loadStatus();
      } else {
        setError(r.error ?? "Gagal memuat galeri dari CSV.");
      }
    } catch (e) {
      fail(e);
    } finally {
      setReloading(false);
    }
  }

  async function runBulk() {
    const token = getToken();
    if (!token || !bulkText.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const r = await indexBulk(token, bulkText, reindex);
      setResults(r.results);
      await loadStatus();
    } catch (e) {
      fail(e);
    } finally {
      setBusy(false);
    }
  }

  return (
    <AppShell active="/admin/index" title="Image Index" sub="Bangun embedding DINOv2 untuk Cari by Foto">
      <div className="mx-auto w-full max-w-5xl px-4 py-5 sm:px-6">
        <h2 className="mb-1 text-base font-semibold">
          🧠 Image <span className="text-brand">Index</span>
        </h2>
        <p className="mb-4 text-sm text-zinc-500">
          Bangun embedding (DINOv2) dari foto SIMS supaya part bisa ditemukan via
          “Cari by Foto”.
        </p>

        {status && (
          <div className="mb-4 flex flex-wrap items-center gap-3 text-sm">
            <span className="rounded-lg bg-zinc-100 px-3 py-1.5">
              Total terindeks: <b>{status.total_indexed.toLocaleString("id-ID")}</b>
            </span>
            <span className="rounded-lg bg-zinc-100 px-3 py-1.5">
              Model AI: {status.torch ? "tersedia" : "tidak tersedia"}
              {status.model_ready ? " (siap)" : ""}
            </span>
            <span className="rounded-lg bg-zinc-100 px-3 py-1.5">
              Galeri: {status.gallery_local ? "file CSV (lokal)" : "database"}
            </span>
            <button
              onClick={runReload}
              disabled={reloading}
              title="Muat ulang galeri Cari-by-Foto dari file CSV (setelah CSV diperbarui), tanpa restart server."
              className="rounded-lg border border-brand px-3 py-1.5 text-sm font-semibold text-brand hover:bg-green-50 disabled:opacity-50"
            >
              {reloading ? "Memuat…" : "↻ Reload Galeri"}
            </button>
          </div>
        )}

        {reloadMsg && (
          <p className="mb-3 rounded-lg bg-green-50 px-3 py-2 text-sm text-green-700 ring-1 ring-green-100">
            {reloadMsg}
          </p>
        )}

        {error && (
          <p className="mb-3 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700 ring-1 ring-red-100">
            {error}
          </p>
        )}

        <label className="mb-3 flex items-center gap-2 text-sm text-zinc-600">
          <input type="checkbox" checked={reindex} onChange={(e) => setReindex(e.target.checked)} />
          Re-index (timpa embedding yang sudah ada)
        </label>

        <div className="grid gap-5 md:grid-cols-2">
          <section className="rounded-xl bg-white p-4 ring-1 ring-zinc-200">
            <h3 className="mb-2 text-sm font-semibold">Index 1 Part</h3>
            <div className="flex gap-2">
              <input
                value={pn}
                onChange={(e) => setPn(e.target.value)}
                placeholder="Part Number"
                className="flex-1 rounded-lg border border-zinc-300 px-3 py-2 text-sm outline-none focus:border-brand"
              />
              <button
                onClick={runSingle}
                disabled={busy || !pn.trim()}
                className="rounded-lg bg-brand px-4 py-2 text-sm font-semibold text-white hover:bg-green-700 disabled:opacity-50"
              >
                Index
              </button>
            </div>
          </section>

          <section className="rounded-xl bg-white p-4 ring-1 ring-zinc-200">
            <h3 className="mb-2 text-sm font-semibold">Index Bulk (maks 50 PN)</h3>
            <textarea
              value={bulkText}
              onChange={(e) => setBulkText(e.target.value)}
              rows={4}
              placeholder={"WG9925550180\nWG1642230041"}
              className="w-full rounded-lg border border-zinc-300 px-3 py-2 font-mono text-sm outline-none focus:border-brand"
            />
            <button
              onClick={runBulk}
              disabled={busy || !bulkText.trim()}
              className="mt-2 rounded-lg bg-brand px-4 py-2 text-sm font-semibold text-white hover:bg-green-700 disabled:opacity-50"
            >
              {busy ? "Mengindeks…" : "Index Bulk"}
            </button>
          </section>
        </div>

        {busy && (
          <p className="mt-3 text-xs text-zinc-400">
            Mengunduh foto SIMS + menghitung embedding (CPU) — bisa lambat untuk banyak foto.
          </p>
        )}

        {results.length > 0 && (
          <div className="mt-5 overflow-x-auto rounded-xl ring-1 ring-zinc-200">
            <table className="tbl">
              <thead className="bg-zinc-50 text-left text-zinc-600">
                <tr>
                  <th className="px-3 py-2 font-medium">Part Number</th>
                  <th className="px-3 py-2 font-medium">Ditemukan</th>
                  <th className="px-3 py-2 font-medium">Sudah ada</th>
                  <th className="px-3 py-2 font-medium">Terindeks</th>
                  <th className="px-3 py-2 font-medium">Gagal</th>
                  <th className="px-3 py-2 font-medium">Ket.</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-zinc-100 bg-white">
                {results.map((r, i) => (
                  <tr key={i}>
                    <td className="px-3 py-2 font-mono">{r.pn}</td>
                    <td className="px-3 py-2">{r.found}</td>
                    <td className="px-3 py-2">{r.already}</td>
                    <td className="px-3 py-2 font-medium text-green-700">{r.indexed}</td>
                    <td className="px-3 py-2">{r.failed}</td>
                    <td className="px-3 py-2 text-xs text-zinc-500">{r.error ?? "✓"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </AppShell>
  );
}
