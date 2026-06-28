"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import AppShell from "@/components/AppShell";
import {
  ApiError,
  getCatalogFolders,
  uploadCatalog,
  uploadDataset,
  type UploadKind,
} from "@/lib/api";
import { clearSession, getToken, getUser } from "@/lib/auth";

const DATASETS: { kind: UploadKind; label: string; icon: string; file: string }[] = [
  { kind: "stok", label: "Stok", icon: "📦", file: "stok.xlsx" },
  { kind: "harga", label: "Harga", icon: "💰", file: "harga.xlsx" },
  { kind: "populasi", label: "Populasi Unit", icon: "🚛", file: "populasi.xlsx" },
];

function UploadCard({
  kind,
  label,
  icon,
  file,
  onDone,
  onErr,
}: {
  kind: UploadKind;
  label: string;
  icon: string;
  file: string;
  onDone: (m: string) => void;
  onErr: (m: string) => void;
}) {
  const router = useRouter();
  const ref = useRef<HTMLInputElement>(null);
  const [picked, setPicked] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);

  async function upload() {
    const token = getToken();
    if (!token) return router.replace("/login");
    if (!picked) return;
    setBusy(true);
    try {
      const r = await uploadDataset(token, kind, picked);
      onDone(`${label}: berhasil diunggah (${(r.size / 1024).toFixed(0)} KB) & index diperbarui.`);
      setPicked(null);
      if (ref.current) ref.current.value = "";
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        clearSession();
        return router.replace("/login");
      }
      onErr(`${label}: ${err instanceof Error ? err.message : "gagal"}`);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="rounded-xl bg-white p-4 ring-1 ring-zinc-200">
      <p className="mb-1 font-semibold">
        {icon} {label}
      </p>
      <p className="mb-3 text-xs text-zinc-500">
        Akan menimpa <span className="font-mono">{file}</span> di Storage.
      </p>
      <input
        ref={ref}
        type="file"
        accept=".xlsx,.xls,.xlsm"
        onChange={(e) => setPicked(e.target.files?.[0] ?? null)}
        className="block w-full text-sm text-zinc-600 file:mr-3 file:rounded-lg file:border-0 file:bg-zinc-100 file:px-3 file:py-2 file:text-sm file:font-medium hover:file:bg-zinc-200"
      />
      <button
        onClick={upload}
        disabled={!picked || busy}
        className="mt-3 w-full rounded-lg bg-brand py-2 text-sm font-semibold text-white hover:bg-green-700 disabled:opacity-50"
      >
        {busy ? "Mengunggah…" : "⬆️ Upload & Refresh"}
      </button>
    </div>
  );
}

function CatalogUploadCard({
  onDone,
  onErr,
}: {
  onDone: (m: string) => void;
  onErr: (m: string) => void;
}) {
  const router = useRouter();
  const ref = useRef<HTMLInputElement>(null);
  const [picked, setPicked] = useState<File[]>([]);
  const [subdir, setSubdir] = useState("");
  const [folders, setFolders] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    const token = getToken();
    if (!token) return;
    getCatalogFolders(token)
      .then((r) => setFolders(r.folders))
      .catch(() => {});
  }, []);

  async function upload() {
    const token = getToken();
    if (!token) return router.replace("/login");
    if (!picked.length) return;
    const dir = subdir.trim();
    if (!dir) return onErr("Katalog: tentukan folder tujuan dulu (mis. Sinotruk/NX380HP).");
    setBusy(true);
    try {
      const r = await uploadCatalog(token, dir, picked);
      let m = `Katalog: ${r.count} file tersimpan ke /data/${dir} & index diperbarui.`;
      if (r.errors?.length) {
        m += ` ${r.errors.length} gagal: ${r.errors.map((e) => `${e.file} (${e.error})`).join("; ")}`;
      }
      onDone(m);
      setPicked([]);
      if (ref.current) ref.current.value = "";
      getCatalogFolders(token).then((x) => setFolders(x.folders)).catch(() => {});
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        clearSession();
        return router.replace("/login");
      }
      onErr(`Katalog: ${err instanceof Error ? err.message : "gagal"}`);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="rounded-xl bg-white p-4 ring-1 ring-zinc-200">
      <p className="mb-1 font-semibold">🗂️ Katalog Part (per unit/model)</p>
      <p className="mb-3 text-xs text-zinc-500">
        Unggah satu atau beberapa Excel katalog ke folder tujuan di server (mis.{" "}
        <span className="font-mono">Sinotruk/NX380HP</span>). Disimpan ke{" "}
        <span className="font-mono">/data</span> & langsung terindeks.
      </p>
      <label className="mb-1 block text-xs font-medium text-zinc-600">Folder tujuan</label>
      <input
        list="catalog-folders"
        value={subdir}
        onChange={(e) => setSubdir(e.target.value)}
        placeholder="mis. Sinotruk/NX380HP (boleh folder baru)"
        className="mb-3 block w-full rounded-lg border border-zinc-200 px-3 py-2 text-sm"
      />
      <datalist id="catalog-folders">
        {folders.map((f) => (
          <option key={f} value={f} />
        ))}
      </datalist>
      <label className="mb-1 block text-xs font-medium text-zinc-600">
        File Excel (boleh pilih beberapa sekaligus)
      </label>
      <input
        ref={ref}
        type="file"
        multiple
        accept=".xlsx,.xls,.xlsm"
        onChange={(e) => setPicked(e.target.files ? Array.from(e.target.files) : [])}
        className="block w-full text-sm text-zinc-600 file:mr-3 file:rounded-lg file:border-0 file:bg-zinc-100 file:px-3 file:py-2 file:text-sm file:font-medium hover:file:bg-zinc-200"
      />
      {picked.length > 0 && (
        <p className="mt-2 text-xs text-zinc-500">
          {picked.length} file dipilih: {picked.map((f) => f.name).join(", ")}
        </p>
      )}
      <button
        onClick={upload}
        disabled={!picked.length || busy}
        className="mt-3 w-full rounded-lg bg-brand py-2 text-sm font-semibold text-white hover:bg-green-700 disabled:opacity-50"
      >
        {busy
          ? "Mengunggah…"
          : `⬆️ Upload ${picked.length > 1 ? `${picked.length} Katalog` : "Katalog"} & Refresh`}
      </button>
    </div>
  );
}

export default function AdminUploadPage() {
  const router = useRouter();
  const [msg, setMsg] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!getToken()) return router.replace("/login");
    if (getUser()?.role !== "admin") router.replace("/search");
  }, [router]);

  return (
    <AppShell active="/admin/upload" title="Upload Data" sub="Unggah stok, harga, populasi">
      <div className="mx-auto w-full max-w-6xl px-4 py-5 sm:px-6">
        <h2 className="mb-1 text-base font-semibold">
          ⬆️ Upload <span className="text-brand">Data</span>
        </h2>
        <p className="mb-4 text-sm text-zinc-500">
          Unggah file Excel terbaru untuk Stok, Harga, atau Populasi. File akan
          disimpan ke Supabase Storage dan index langsung diperbarui.
        </p>

        {msg && (
          <p className="mb-3 rounded-lg bg-green-50 px-3 py-2 text-sm text-green-700 ring-1 ring-green-100">
            {msg}
          </p>
        )}
        {error && (
          <p className="mb-3 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700 ring-1 ring-red-100">
            {error}
          </p>
        )}

        <div className="grid gap-4 sm:grid-cols-3">
          {DATASETS.map((d) => (
            <UploadCard
              key={d.kind}
              {...d}
              onDone={(m) => {
                setMsg(m);
                setError(null);
              }}
              onErr={(m) => {
                setError(m);
                setMsg(null);
              }}
            />
          ))}
        </div>

        <p className="mt-4 text-xs text-zinc-400">
          Catatan: upload Stok/Harga memicu rebuild index (beberapa detik berkat
          cache). Populasi langsung dimuat ulang.
        </p>

        <div className="mt-8">
          <h2 className="mb-1 text-base font-semibold">
            🗂️ Upload <span className="text-brand">Katalog</span>
          </h2>
          <p className="mb-4 text-sm text-zinc-500">
            Unggah file Excel katalog part (per unit/model) langsung ke folder{" "}
            <span className="font-mono">/data</span> di server — pilih folder
            tujuan, lalu file langsung tersimpan & terindeks (tanpa perlu Redeploy).
          </p>
          <div className="grid gap-4 sm:grid-cols-2">
            <CatalogUploadCard
              onDone={(m) => {
                setMsg(m);
                setError(null);
              }}
              onErr={(m) => {
                setError(m);
                setMsg(null);
              }}
            />
          </div>
        </div>
      </div>
    </AppShell>
  );
}
