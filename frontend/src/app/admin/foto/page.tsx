"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import AppShell from "@/components/AppShell";
import {
  ApiError,
  deletePhoto,
  listAdminPhotos,
  uploadPhoto,
  type AdminPhoto,
} from "@/lib/api";
import { clearSession, getToken, getUser } from "@/lib/auth";

export default function AdminFotoPage() {
  const router = useRouter();
  const fileRef = useRef<HTMLInputElement>(null);
  const [pn, setPn] = useState("");
  const [active, setActive] = useState("");
  const [photos, setPhotos] = useState<AdminPhoto[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);

  useEffect(() => {
    if (!getToken()) return router.replace("/login");
    if (getUser()?.role !== "admin") router.replace("/search");
  }, [router]);

  function fail(e: unknown) {
    if (e instanceof ApiError && e.status === 401) {
      clearSession();
      router.replace("/login");
      return;
    }
    setError(e instanceof Error ? e.message : "Gagal");
    setMsg(null);
  }

  async function loadPhotos(target: string) {
    const token = getToken();
    if (!token || !target.trim()) return;
    setError(null);
    try {
      const d = await listAdminPhotos(token, target.trim().toUpperCase());
      setPhotos(d.photos);
      setActive(target.trim().toUpperCase());
    } catch (e) {
      fail(e);
    }
  }

  async function doUpload() {
    const token = getToken();
    const f = fileRef.current?.files?.[0];
    if (!token || !active || !f) return;
    setBusy(true);
    setError(null);
    try {
      await uploadPhoto(token, active, f);
      setMsg(`Foto diunggah untuk ${active}.`);
      if (fileRef.current) fileRef.current.value = "";
      await loadPhotos(active);
    } catch (e) {
      fail(e);
    } finally {
      setBusy(false);
    }
  }

  async function doDelete(id: string) {
    const token = getToken();
    if (!token) return;
    if (!window.confirm("Hapus foto ini?")) return;
    try {
      await deletePhoto(token, id);
      setMsg("Foto dihapus.");
      await loadPhotos(active);
    } catch (e) {
      fail(e);
    }
  }

  return (
    <AppShell active="/admin/foto" title="Foto Part" sub="Kelola foto part (Supabase)">
      <div className="mx-auto w-full max-w-5xl px-4 py-5 sm:px-6">
        <h2 className="mb-4 text-base font-semibold">
          🖼️ Foto <span className="text-brand">Part</span>
        </h2>

        <form
          onSubmit={(e) => {
            e.preventDefault();
            loadPhotos(pn);
          }}
          className="mb-4 flex gap-2"
        >
          <input
            value={pn}
            onChange={(e) => setPn(e.target.value)}
            placeholder="Part Number (mis. WG9925550180)"
            className="flex-1 rounded-lg border border-zinc-300 px-3 py-2 text-sm outline-none focus:border-brand"
          />
          <button className="rounded-lg bg-brand px-5 py-2 text-sm font-semibold text-white hover:bg-green-700">
            Muat Foto
          </button>
        </form>

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

        {active && (
          <>
            <div className="mb-4 flex flex-wrap items-center gap-2 rounded-xl bg-white p-3 ring-1 ring-zinc-200">
              <span className="text-sm text-zinc-600">
                Upload foto untuk <b className="font-mono">{active}</b>:
              </span>
              <input
                ref={fileRef}
                type="file"
                accept=".jpg,.jpeg,.png,.webp"
                className="text-sm text-zinc-600 file:mr-2 file:rounded-lg file:border-0 file:bg-zinc-100 file:px-3 file:py-1.5 file:text-sm"
              />
              <button
                onClick={doUpload}
                disabled={busy}
                className="rounded-lg bg-brand px-4 py-1.5 text-sm font-semibold text-white hover:bg-green-700 disabled:opacity-50"
              >
                {busy ? "Mengunggah…" : "⬆️ Upload"}
              </button>
            </div>

            {photos.length > 0 ? (
              <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
                {photos.map((p) => (
                  <div key={p.id} className="overflow-hidden rounded-xl ring-1 ring-zinc-200">
                    <div className="aspect-square bg-zinc-50">
                      {/* eslint-disable-next-line @next/next/no-img-element */}
                      <img src={p.storage_url} alt={p.file_name} className="h-full w-full object-contain" />
                    </div>
                    <div className="flex items-center justify-between gap-1 p-2">
                      <span className="truncate text-xs text-zinc-500" title={p.file_name}>
                        {p.file_name}
                      </span>
                      <button
                        onClick={() => doDelete(p.id)}
                        className="rounded border border-red-300 px-2 py-0.5 text-xs font-medium text-red-600 hover:bg-red-50"
                      >
                        Hapus
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <p className="text-sm text-zinc-500">Belum ada foto untuk {active}.</p>
            )}
          </>
        )}
      </div>
    </AppShell>
  );
}
