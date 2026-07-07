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
import { ensurePerms } from "@/lib/perms";

// Kolom yang bisa dipilih untuk disertakan di katalog Excel.
// Part Number selalu ikut (tidak bisa dimatikan).
const COLUMN_OPTS: { key: string; label: string; desc: string }[] = [
  { key: "nama", label: "Nama Part", desc: "Nama/deskripsi part" },
  { key: "foto", label: "Foto", desc: "2 gambar dari SIMS (paling lambat)" },
  { key: "stok", label: "Stok", desc: "Stok Accurate (live)" },
  { key: "harga_sims", label: "Harga SIMS", desc: "Harga SIMS × kurs → Rupiah" },
  { key: "harga_accurate", label: "Harga Jual Accurate", desc: "Harga jual dari Accurate" },
  { key: "harga_daftar", label: "Harga (Daftar)", desc: "Dari daftar harga internal" },
  { key: "kecocokan", label: "Kecocokan", desc: "File katalog lokal yang cocok" },
];
const DEFAULT_COLUMNS = ["nama", "foto", "stok"];
// Kolom harga: hanya admin & akun 'mas' (di-enforce juga di backend).
const PRICE_KEYS = ["harga_sims", "harga_accurate", "harga_daftar"];

export default function BatchPage() {
  const router = useRouter();
  const fileRef = useRef<HTMLInputElement>(null);

  const [text, setText] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [columns, setColumns] = useState<string[]>(DEFAULT_COLUMNS);
  const [canPrice, setCanPrice] = useState(false);

  function toggleColumn(key: string) {
    setColumns((prev) =>
      prev.includes(key) ? prev.filter((k) => k !== key) : [...prev, key],
    );
  }

  useEffect(() => {
    if (!getToken()) {
      router.replace("/login");
      return;
    }
    ensurePerms().then((p) => {
      const allow = !!p?.can_price;
      setCanPrice(allow);
      // Amankan: buang kolom harga dari pilihan bila akun ini tak berizin.
      if (!allow) setColumns((prev) => prev.filter((k) => !PRICE_KEYS.includes(k)));
    });
  }, [router]);

  // Opsi yang boleh ditampilkan: sembunyikan kolom harga untuk akun non-izin.
  const visibleColumnOpts = COLUMN_OPTS.filter((o) => canPrice || !PRICE_KEYS.includes(o.key));

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
    if (columns.length === 0) {
      setError("Pilih minimal satu kolom untuk disertakan.");
      return;
    }
    setError(null);
    setLoading(true);
    try {
      const blob = await buildBatchCatalog(token, { text, file, columns });
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
          Masukkan banyak part number sekaligus → unduh katalog Excel. Pilih sendiri kolom yang
          disertakan (nama, foto, stok, harga). Maksimum 300 PN per batch.
        </p>

        <button onClick={handleTemplate} className="btn btn-secondary" style={{ marginBottom: 18 }}>
          📄 Download Template Input
        </button>

        {/* Pemilihan kolom */}
        <section style={{ marginBottom: 18 }}>
          <label className="block" style={{ marginBottom: 8, fontSize: 13, fontWeight: 600, color: "var(--ink-700)" }}>
            Kolom yang disertakan
          </label>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
            <span
              className="mono"
              style={{
                display: "inline-flex", alignItems: "center", gap: 6, padding: "8px 12px",
                borderRadius: 10, fontSize: 12.5, fontWeight: 600,
                background: "var(--brand-50)", color: "var(--brand-700)", border: "1px solid var(--brand-100)",
              }}
              title="Selalu disertakan"
            >
              Part Number · wajib
            </span>
            {visibleColumnOpts.map((opt) => {
              const on = columns.includes(opt.key);
              return (
                <button
                  key={opt.key}
                  type="button"
                  onClick={() => toggleColumn(opt.key)}
                  title={opt.desc}
                  style={{
                    display: "inline-flex", alignItems: "center", gap: 7, padding: "8px 12px",
                    borderRadius: 10, fontSize: 12.5, fontWeight: 600, cursor: "pointer",
                    background: on ? "var(--brand-600)" : "var(--paper)",
                    color: on ? "#fff" : "var(--ink-600)",
                    border: on ? "1px solid var(--brand-600)" : "1px solid var(--ink-200)",
                    transition: "all .12s",
                  }}
                >
                  <span style={{ fontSize: 13, lineHeight: 1 }}>{on ? "✓" : "+"}</span>
                  {opt.label}
                </button>
              );
            })}
          </div>
          <p style={{ marginTop: 8, fontSize: 11.5, color: "var(--ink-400)" }}>
            Tip: matikan <b>Foto</b> agar proses jauh lebih cepat bila hanya butuh stok/harga.
          </p>
        </section>

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
          disabled={loading || columns.length === 0 || (!file && !text.trim())}
          className="btn btn-primary btn-lg"
          style={{ marginTop: 24, width: "100%" }}
        >
          {loading ? "Memproses & mengunduh…" : "⬇ Proses & Download Katalog"}
        </button>
        {loading && (
          <p style={{ marginTop: 8, textAlign: "center", fontSize: 12, color: "var(--ink-400)" }}>
            {columns.includes("foto") || columns.includes("harga_sims")
              ? "Mengambil data dari SIMS untuk tiap part — bisa beberapa menit untuk banyak PN."
              : "Menyusun katalog — sebentar lagi selesai."}
          </p>
        )}
      </div>
    </AppShell>
  );
}
