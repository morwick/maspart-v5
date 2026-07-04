"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import AppShell from "@/components/AppShell";
import { ApiError, partImageUrl, searchByImage, type ImageMatch } from "@/lib/api";
import { clearSession, getToken } from "@/lib/auth";

// Simpan hasil pencarian foto terakhir agar muncul lagi saat kembali dari detail.
const SS_KEY = "maspart_image_search";
// Flag: di-set saat menuju halaman detail; hasil hanya dipulihkan jika balik dari sana.
const RETURN_KEY = "maspart_image_return";

// Kecilkan gambar jadi data URL kecil (≤400px) untuk disimpan ke sessionStorage.
function downscaleToDataUrl(file: File, max = 400): Promise<string> {
  return new Promise((resolve) => {
    const url = URL.createObjectURL(file);
    const img = new Image();
    img.onload = () => {
      const scale = Math.min(1, max / Math.max(img.width, img.height));
      const w = Math.round(img.width * scale);
      const h = Math.round(img.height * scale);
      const canvas = document.createElement("canvas");
      canvas.width = w;
      canvas.height = h;
      const ctx = canvas.getContext("2d");
      ctx?.drawImage(img, 0, 0, w, h);
      URL.revokeObjectURL(url);
      try {
        resolve(canvas.toDataURL("image/jpeg", 0.7));
      } catch {
        resolve("");
      }
    };
    img.onerror = () => {
      URL.revokeObjectURL(url);
      resolve("");
    };
    img.src = url;
  });
}

export default function SearchImagePage() {
  const router = useRouter();
  const fileRef = useRef<HTMLInputElement>(null);

  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<string | null>(null);
  const [useTta, setUseTta] = useState(false);
  const [results, setResults] = useState<ImageMatch[]>([]);
  const [searched, setSearched] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!getToken()) {
      router.replace("/login");
      return;
    }
    // Hanya pulihkan hasil bila baru kembali dari halaman detail (gating RETURN_KEY).
    // Buka/refresh biasa → tidak memulihkan (mulai bersih).
    // PENTING: jangan menghapus SS_KEY di sini. Effect ini bisa berjalan dua kali
    // (React StrictMode di dev): jalan ke-1 mengonsumsi RETURN_KEY, jalan ke-2 akan
    // melihat flag sudah hilang. Bila kita hapus SS_KEY di kondisi itu, data hasil
    // ikut terhapus → saat kembali berikutnya jadi ter-reset.
    try {
      const returning = sessionStorage.getItem(RETURN_KEY) === "1";
      if (returning) {
        sessionStorage.removeItem(RETURN_KEY); // konsumsi flag
        const raw = sessionStorage.getItem(SS_KEY);
        if (raw) {
          const saved = JSON.parse(raw) as { results: ImageMatch[]; preview: string };
          if (saved.results?.length) {
            setResults(saved.results);
            setSearched(true);
            if (saved.preview) setPreview(saved.preview);
          }
        }
      }
    } catch {
      /* ignore */
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [router]);

  function pickFile(f: File | null) {
    setFile(f);
    setResults([]);
    setSearched(false);
    setError(null);
    if (preview) URL.revokeObjectURL(preview);
    setPreview(f ? URL.createObjectURL(f) : null);
  }

  // Tempel gambar dari clipboard (Ctrl+V) di mana saja pada halaman ini.
  const pickRef = useRef(pickFile);
  pickRef.current = pickFile;
  useEffect(() => {
    function onPaste(e: ClipboardEvent) {
      const items = e.clipboardData?.items;
      if (!items) return;
      for (let i = 0; i < items.length; i++) {
        const it = items[i];
        if (it.type.startsWith("image/")) {
          const f = it.getAsFile();
          if (f) {
            pickRef.current(f);
            e.preventDefault();
            break;
          }
        }
      }
    }
    window.addEventListener("paste", onPaste);
    return () => window.removeEventListener("paste", onPaste);
  }, []);

  async function handleSearch() {
    if (!file) return;
    const token = getToken();
    if (!token) return router.replace("/login");
    setError(null);
    setLoading(true);
    try {
      const res = await searchByImage(file, token, { useTta, topK: 20 });
      setResults(res.results);
      setSearched(true);
      // Simpan hasil + preview kecil agar bisa dipulihkan saat kembali dari detail.
      try {
        const previewData = await downscaleToDataUrl(file);
        sessionStorage.setItem(
          SS_KEY,
          JSON.stringify({ results: res.results, preview: previewData }),
        );
      } catch {
        /* ignore quota */
      }
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        clearSession();
        return router.replace("/login");
      }
      setError(err instanceof Error ? err.message : "Gagal mencari");
      setResults([]);
    } finally {
      setLoading(false);
    }
  }

  return (
    <AppShell active="/search-image" title="Cari by Foto" sub="Cari part mirip via foto (DINOv2 + SIMS)">
      <div className="mx-auto w-full max-w-5xl px-4 py-5 sm:px-6">
        <div className="grid gap-6 md:grid-cols-[260px_1fr]">
          {/* Panel upload */}
          <section>
            <input
              ref={fileRef}
              type="file"
              accept="image/jpeg,image/png,image/webp"
              className="hidden"
              onChange={(e) => pickFile(e.target.files?.[0] ?? null)}
            />
            <button
              type="button"
              onClick={() => fileRef.current?.click()}
              className="grid aspect-square w-full place-items-center overflow-hidden"
              style={{ borderRadius: 12, border: "2px dashed var(--ink-300)", background: "var(--paper)", fontSize: 13.5, color: "var(--ink-500)", cursor: "pointer" }}
            >
              {preview ? (
                // eslint-disable-next-line @next/next/no-img-element
                <img src={preview} alt="preview" className="h-full w-full object-contain" />
              ) : (
                <span style={{ padding: "0 16px", textAlign: "center" }}>
                  📷 Klik untuk pilih foto
                  <br />
                  <span style={{ fontSize: 12, color: "var(--ink-400)" }}>atau tempel (Ctrl+V)</span>
                </span>
              )}
            </button>

            <label className="flex items-center gap-2" style={{ marginTop: 12, fontSize: 13.5, color: "var(--ink-600)" }}>
              <input
                type="checkbox"
                checked={useTta}
                onChange={(e) => setUseTta(e.target.checked)}
              />
              Mode akurat (TTA, lebih lambat)
            </label>

            <button onClick={handleSearch} disabled={!file || loading} className="btn btn-primary" style={{ width: "100%", marginTop: 12 }}>
              {loading ? "Mencari…" : "🔍 Cari Part Mirip"}
            </button>
            {loading && (
              <p style={{ marginTop: 8, fontSize: 12, color: "var(--ink-400)" }}>
                Pencarian pertama bisa lebih lama (memuat model AI).
              </p>
            )}
          </section>

          {/* Hasil */}
          <section>
            {error && <div className="alert alert-error" style={{ marginBottom: 16 }}>{error}</div>}

            {searched && !error && (
              <p style={{ marginBottom: 12, fontSize: 13.5, color: "var(--ink-500)" }}>
                {results.length > 0
                  ? `${results.length} part mirip ditemukan`
                  : "Tidak ada part mirip yang ditemukan."}
              </p>
            )}

            {results.length > 0 && (
              <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
                {results.map((r) => {
                  const pct = Math.round(r.similarity * 100);
                  return (
                    <button
                      key={r.part_number}
                      onClick={() => {
                        try {
                          sessionStorage.setItem(RETURN_KEY, "1");
                        } catch {
                          /* ignore */
                        }
                        router.push(`/part/${encodeURIComponent(r.part_number)}?from=image`);
                      }}
                      className="surface overflow-hidden text-left"
                      style={{ padding: 0, cursor: "pointer" }}
                    >
                      <div className="aspect-square w-full" style={{ background: "var(--ink-50)" }}>
                        {/* eslint-disable-next-line @next/next/no-img-element */}
                        <img
                          src={partImageUrl(r.sims_url)}
                          alt={r.part_number}
                          loading="lazy"
                          className="h-full w-full object-contain"
                        />
                      </div>
                      <div style={{ padding: 8 }}>
                        {r.part_name && (
                          <p className="truncate" style={{ fontSize: 12, fontWeight: 600 }} title={r.part_name}>
                            {r.part_name}
                          </p>
                        )}
                        <p className="truncate mono" style={{ fontSize: 12, color: "var(--ink-500)" }}>
                          {r.part_number}
                        </p>
                        <div className="flex items-center" style={{ gap: 6, marginTop: 4 }}>
                          <div style={{ height: 6, flex: 1, overflow: "hidden", borderRadius: 99, background: "var(--ink-100)" }}>
                            <div style={{ height: "100%", background: "var(--brand-600)", width: `${pct}%` }} />
                          </div>
                          <span className="mono" style={{ fontSize: 11.5, fontWeight: 600, color: "var(--ink-600)" }}>{pct}%</span>
                        </div>
                      </div>
                    </button>
                  );
                })}
              </div>
            )}

            {!searched && !error && (
              <div className="surface grid place-items-center" style={{ height: 224, color: "var(--ink-400)", fontSize: 13.5 }}>
                Hasil pencarian akan muncul di sini.
              </div>
            )}
          </section>
        </div>
      </div>
    </AppShell>
  );
}
