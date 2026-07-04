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
      <div className="mx-auto w-full max-w-3xl px-4 py-6 sm:px-7">
        <p style={{ marginBottom: 16, fontSize: 13.5, color: "var(--ink-500)" }}>
          Masukkan banyak part number sekaligus → unduh katalog Excel berisi nama part & gambar
          (dari SIMS). Maksimum 300 PN per batch.
        </p>

        <button onClick={handleTemplate} className="btn btn-secondary" style={{ marginBottom: 18 }}>
          📄 Download Template Input
        </button>

        {error && <div className="alert alert-error" style={{ marginBottom: 16 }}>{error}</div>}

        <div className="grid gap-5 sm:grid-cols-2">
          {/* Ketik manual */}
          <section>
            <label className="block" style={{ marginBottom: 6, fontSize: 13, fontWeight: 600, color: "var(--ink-700)" }}>
              Ketik manual (1 PN per baris)
            </label>
            <textarea
              className="textarea mono"
              value={text}
              onChange={(e) => {
                setText(e.target.value);
                setFile(null);
                if (fileRef.current) fileRef.current.value = "";
              }}
              rows={10}
              placeholder={"WG1642821034\nWG9925520270\nAZ9100443082"}
            />
            {lineCount > 0 && !file && (
              <p style={{ marginTop: 4, fontSize: 12, color: "var(--ink-500)" }}>{lineCount} baris</p>
            )}
          </section>

          {/* Atau upload file */}
          <section>
            <label className="block" style={{ marginBottom: 6, fontSize: 13, fontWeight: 600, color: "var(--ink-700)" }}>
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
              className="block w-full"
              style={{ fontSize: 13, color: "var(--ink-600)" }}
            />
            {file && (
              <p style={{ marginTop: 4, fontSize: 12, color: "var(--ink-500)" }}>Dipilih: {file.name}</p>
            )}
          </section>
        </div>

        <button
          onClick={handleProcess}
          disabled={loading || (!file && !text.trim())}
          className="btn btn-primary btn-lg"
          style={{ marginTop: 24, width: "100%" }}
        >
          {loading ? "Memproses & mengunduh…" : "⬇ Proses & Download Katalog"}
        </button>
        {loading && (
          <p style={{ marginTop: 8, textAlign: "center", fontSize: 12, color: "var(--ink-400)" }}>
            Mengambil gambar dari SIMS untuk tiap part — bisa beberapa menit untuk banyak PN.
          </p>
        )}
      </div>
    </AppShell>
  );
}
