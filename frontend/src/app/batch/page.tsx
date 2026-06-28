"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import AppShell from "@/components/AppShell";
import {
  ApiError,
  buildBatchCatalog,
  downloadBlob,
  fetchBatchTemplate,
} from "@/lib/api";
import { clearSession, getToken } from "@/lib/auth";

export default function BatchPage() {
  const router = useRouter();
  const fileRef = useRef<HTMLInputElement>(null);

  const [text, setText] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!getToken()) router.replace("/login");
  }, [router]);

  const lineCount = text
    .split("\n")
    .map((l) => l.trim())
    .filter(Boolean).length;

  function authOr401(err: unknown) {
    if (err instanceof ApiError && err.status === 401) {
      clearSession();
      router.replace("/login");
      return true;
    }
    return false;
  }

  async function handleTemplate() {
    const token = getToken();
    if (!token) return router.replace("/login");
    try {
      const blob = await fetchBatchTemplate(token);
      downloadBlob(blob, "template_batch_input.xlsx");
    } catch (err) {
      if (!authOr401(err))
        setError(err instanceof Error ? err.message : "Gagal unduh template");
    }
  }

  async function handleProcess() {
    const token = getToken();
    if (!token) return router.replace("/login");
    if (!file && !text.trim()) {
      setError("Masukkan part number atau unggah file dulu.");
      return;
    }
    setError(null);
    setLoading(true);
    try {
      const blob = await buildBatchCatalog(token, { text, file });
      downloadBlob(blob, "catalog.xlsx");
    } catch (err) {
      if (!authOr401(err))
        setError(err instanceof Error ? err.message : "Gagal membuat katalog");
    } finally {
      setLoading(false);
    }
  }

  return (
    <AppShell active="/batch" title="Batch Download" sub="Unduh katalog Excel banyak part sekaligus">
      <div className="mx-auto w-full max-w-3xl px-4 py-5 sm:px-6">
        <h2 className="mb-1 text-base font-semibold">
          📥 Batch <span className="text-brand">Download</span>
        </h2>
        <p className="mb-4 text-sm text-zinc-500">
          Masukkan banyak part number sekaligus → unduh katalog Excel berisi
          nama part & gambar (dari SIMS). Maksimum 300 PN per batch.
        </p>

        <button
          onClick={handleTemplate}
          className="mb-5 rounded-lg border border-zinc-300 px-3 py-1.5 text-sm font-medium text-zinc-700 hover:bg-zinc-50"
        >
          📄 Download Template Input
        </button>

        {error && (
          <p className="mb-4 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700 ring-1 ring-red-100">
            {error}
          </p>
        )}

        <div className="grid gap-5 sm:grid-cols-2">
          {/* Ketik manual */}
          <section>
            <label className="mb-1 block text-sm font-medium text-zinc-700">
              Ketik manual (1 PN per baris)
            </label>
            <textarea
              value={text}
              onChange={(e) => {
                setText(e.target.value);
                setFile(null);
                if (fileRef.current) fileRef.current.value = "";
              }}
              rows={10}
              placeholder={"WG1642821034\nWG9925520270\nAZ9100443082"}
              className="w-full rounded-lg border border-zinc-300 px-3 py-2 font-mono text-sm outline-none focus:border-brand focus:ring-2 focus:ring-brand/20"
            />
            {lineCount > 0 && !file && (
              <p className="mt-1 text-xs text-zinc-500">{lineCount} baris</p>
            )}
          </section>

          {/* Atau upload file */}
          <section>
            <label className="mb-1 block text-sm font-medium text-zinc-700">
              Atau unggah file (Excel/CSV, PN di kolom A)
            </label>
            <input
              ref={fileRef}
              type="file"
              accept=".xlsx,.xls,.xlsm,.csv"
              onChange={(e) => {
                const f = e.target.files?.[0] ?? null;
                setFile(f);
                if (f) setText("");
              }}
              className="block w-full text-sm text-zinc-600 file:mr-3 file:rounded-lg file:border-0 file:bg-zinc-100 file:px-3 file:py-2 file:text-sm file:font-medium hover:file:bg-zinc-200"
            />
            {file && (
              <p className="mt-1 text-xs text-zinc-500">Dipilih: {file.name}</p>
            )}
          </section>
        </div>

        <button
          onClick={handleProcess}
          disabled={loading || (!file && !text.trim())}
          className="mt-6 w-full rounded-lg bg-brand py-2.5 text-sm font-semibold text-white hover:bg-green-700 disabled:opacity-50"
        >
          {loading ? "Memproses & mengunduh…" : "⬇️ Proses & Download Katalog"}
        </button>
        {loading && (
          <p className="mt-2 text-center text-xs text-zinc-400">
            Mengambil gambar dari SIMS untuk tiap part — bisa beberapa menit untuk
            banyak PN.
          </p>
        )}
      </div>
    </AppShell>
  );
}
