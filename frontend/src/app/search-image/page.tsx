"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import AppShell from "@/components/AppShell";
import {
  ApiError,
  learnImageMatch,
  partImageUrl,
  searchByImage,
  type ImageMatch,
  type ImageSearchResponse,
} from "@/lib/api";
import { clearSession, getToken, getUser } from "@/lib/auth";

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

type CropSel = { x: number; y: number; w: number; h: number } | null;

export default function SearchImagePage() {
  const router = useRouter();
  const fileRef = useRef<HTMLInputElement>(null);
  const cameraRef = useRef<HTMLInputElement>(null);
  const cropImgRef = useRef<HTMLImageElement>(null);

  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<string | null>(null);
  const [useTta, setUseTta] = useState(true); // mode akurat DEFAULT nyala
  const [results, setResults] = useState<ImageMatch[]>([]);
  const [meta, setMeta] = useState<Pick<ImageSearchResponse, "galeri_total" | "galeri_parts" | "pesan"> | null>(null);
  const [searched, setSearched] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Mode CROP: buang latar → embedding fokus ke part → akurasi naik drastis
  // untuk foto lapangan. Seleksi digambar dengan drag di atas preview.
  const [cropMode, setCropMode] = useState(false);
  const [cropSel, setCropSel] = useState<CropSel>(null);
  const dragStart = useRef<{ x: number; y: number } | null>(null);

  // Galeri belajar (admin): PN yang barusan dikonfirmasi dari foto ini.
  const [learnedPn, setLearnedPn] = useState<string | null>(null);
  const [learnBusy, setLearnBusy] = useState<string | null>(null);
  const user = typeof window !== "undefined" ? getUser() : null;
  const canLearn = !!user && (user.role === "admin" || (user.username || "").toLowerCase() === "mas");

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
    setMeta(null);
    setSearched(false);
    setError(null);
    setCropMode(false);
    setCropSel(null);
    setLearnedPn(null);
    if (preview?.startsWith("blob:")) URL.revokeObjectURL(preview);
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

  // ── Crop: drag rect di atas gambar → potong via canvas → jadi file query baru ──
  function cropPoint(e: React.PointerEvent): { x: number; y: number } | null {
    const img = cropImgRef.current;
    if (!img) return null;
    const r = img.getBoundingClientRect();
    return {
      x: Math.min(Math.max(e.clientX - r.left, 0), r.width),
      y: Math.min(Math.max(e.clientY - r.top, 0), r.height),
    };
  }

  function onCropDown(e: React.PointerEvent) {
    const p = cropPoint(e);
    if (!p) return;
    dragStart.current = p;
    setCropSel({ x: p.x, y: p.y, w: 0, h: 0 });
    (e.target as HTMLElement).setPointerCapture?.(e.pointerId);
    e.preventDefault();
  }

  function onCropMove(e: React.PointerEvent) {
    const s = dragStart.current;
    if (!s) return;
    const p = cropPoint(e);
    if (!p) return;
    setCropSel({
      x: Math.min(s.x, p.x),
      y: Math.min(s.y, p.y),
      w: Math.abs(p.x - s.x),
      h: Math.abs(p.y - s.y),
    });
    e.preventDefault();
  }

  function onCropUp() {
    dragStart.current = null;
  }

  async function applyCrop() {
    const img = cropImgRef.current;
    if (!img || !cropSel || cropSel.w < 12 || cropSel.h < 12) return;
    const scaleX = img.naturalWidth / img.clientWidth;
    const scaleY = img.naturalHeight / img.clientHeight;
    const sx = Math.round(cropSel.x * scaleX);
    const sy = Math.round(cropSel.y * scaleY);
    const sw = Math.max(1, Math.round(cropSel.w * scaleX));
    const sh = Math.max(1, Math.round(cropSel.h * scaleY));
    const canvas = document.createElement("canvas");
    canvas.width = sw;
    canvas.height = sh;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    ctx.drawImage(img, sx, sy, sw, sh, 0, 0, sw, sh);
    const blob: Blob | null = await new Promise((res) => canvas.toBlob(res, "image/jpeg", 0.92));
    if (!blob) return;
    pickFile(new File([blob], "crop.jpg", { type: "image/jpeg" }));
  }

  async function handleSearch() {
    if (!file) return;
    const token = getToken();
    if (!token) return router.replace("/login");
    setError(null);
    setLoading(true);
    setLearnedPn(null);
    try {
      const res = await searchByImage(file, token, { useTta, topK: 20 });
      setResults(res.results);
      setMeta({ galeri_total: res.galeri_total, galeri_parts: res.galeri_parts, pesan: res.pesan });
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

  // Galeri belajar: konfirmasi "foto query ini = PN itu" → indeks ke galeri.
  async function handleLearn(pn: string) {
    if (!file || learnBusy) return;
    const token = getToken();
    if (!token) return router.replace("/login");
    if (!window.confirm(`Yakin foto ini memang part ${pn}? Foto akan diindeks ke galeri (memperbaiki akurasi pencarian berikutnya).`)) return;
    setLearnBusy(pn);
    try {
      const res = await learnImageMatch(file, pn, token);
      setLearnedPn(res.pn);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Gagal mengindeks foto");
    } finally {
      setLearnBusy(null);
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
            {/* Kamera belakang HP — di desktop jatuh ke dialog file biasa. */}
            <input
              ref={cameraRef}
              type="file"
              accept="image/*"
              capture="environment"
              className="hidden"
              onChange={(e) => pickFile(e.target.files?.[0] ?? null)}
            />

            {cropMode && preview ? (
              <div>
                <div
                  style={{ position: "relative", touchAction: "none", borderRadius: 12, overflow: "hidden", border: "2px solid var(--brand-600)", userSelect: "none" }}
                  onPointerDown={onCropDown}
                  onPointerMove={onCropMove}
                  onPointerUp={onCropUp}
                >
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img ref={cropImgRef} src={preview} alt="crop" draggable={false} style={{ width: "100%", display: "block", pointerEvents: "none" }} />
                  {cropSel && cropSel.w > 2 && (
                    <div
                      style={{
                        position: "absolute",
                        left: cropSel.x,
                        top: cropSel.y,
                        width: cropSel.w,
                        height: cropSel.h,
                        border: "2px dashed #fff",
                        outline: "1px solid var(--brand-600)",
                        background: "rgba(37,99,235,.15)",
                        boxShadow: "0 0 0 9999px rgba(0,0,0,.35)",
                        pointerEvents: "none",
                      }}
                    />
                  )}
                </div>
                <p style={{ marginTop: 6, fontSize: 12, color: "var(--ink-500)" }}>
                  Seret kotak mengelilingi PART-nya saja (buang latar) → akurasi naik.
                </p>
                <div className="flex" style={{ gap: 8, marginTop: 8 }}>
                  <button className="btn btn-primary" style={{ flex: 1 }} disabled={!cropSel || cropSel.w < 12} onClick={applyCrop}>
                    ✂ Terapkan
                  </button>
                  <button className="btn" style={{ flex: 1 }} onClick={() => { setCropMode(false); setCropSel(null); }}>
                    Batal
                  </button>
                </div>
              </div>
            ) : (
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
            )}

            {!cropMode && (
              <div className="flex" style={{ gap: 8, marginTop: 8 }}>
                <button className="btn" style={{ flex: 1 }} onClick={() => cameraRef.current?.click()}>
                  📸 Kamera
                </button>
                <button className="btn" style={{ flex: 1 }} disabled={!preview} onClick={() => { setCropMode(true); setCropSel(null); }}>
                  ✂ Crop
                </button>
              </div>
            )}

            <label className="flex items-center gap-2" style={{ marginTop: 12, fontSize: 13.5, color: "var(--ink-600)" }}>
              <input
                type="checkbox"
                checked={useTta}
                onChange={(e) => setUseTta(e.target.checked)}
              />
              Mode akurat (sedikit lebih lambat)
            </label>

            <button onClick={handleSearch} disabled={!file || loading || cropMode} className="btn btn-primary" style={{ width: "100%", marginTop: 12 }}>
              {loading ? "Mencari…" : "🔍 Cari Part Mirip"}
            </button>
            {loading && (
              <p style={{ marginTop: 8, fontSize: 12, color: "var(--ink-400)" }}>
                Pencarian pertama bisa lebih lama (memuat model AI).
              </p>
            )}
            {meta && meta.galeri_total > 0 && (
              <p style={{ marginTop: 8, fontSize: 12, color: "var(--ink-400)" }}>
                Galeri: {meta.galeri_total.toLocaleString("id-ID")} foto dari {meta.galeri_parts.toLocaleString("id-ID")} part.
              </p>
            )}
          </section>

          {/* Hasil */}
          <section>
            {error && <div className="alert alert-error" style={{ marginBottom: 16 }}>{error}</div>}
            {learnedPn && (
              <div className="alert" style={{ marginBottom: 16 }}>
                ✅ Foto diindeks ke galeri sebagai <b className="mono">{learnedPn}</b> — pencarian foto serupa berikutnya makin akurat.
              </div>
            )}

            {searched && !error && (
              <p style={{ marginBottom: 12, fontSize: 13.5, color: "var(--ink-500)" }}>
                {results.length > 0
                  ? `${results.length} part mirip ditemukan`
                  : meta?.pesan || "Tidak ada part mirip yang ditemukan."}
              </p>
            )}

            {results.length > 0 && (
              <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
                {results.map((r) => {
                  const pct = Math.round(r.similarity * 100);
                  return (
                    <div key={r.part_number} className="surface overflow-hidden" style={{ padding: 0 }}>
                      <button
                        onClick={() => {
                          try {
                            sessionStorage.setItem(RETURN_KEY, "1");
                          } catch {
                            /* ignore */
                          }
                          router.push(`/part/${encodeURIComponent(r.part_number)}?from=image`);
                        }}
                        className="block w-full text-left"
                        style={{ padding: 0, cursor: "pointer", background: "none", border: "none" }}
                      >
                        <div className="aspect-square w-full" style={{ background: "var(--ink-50)" }}>
                          {r.sims_url ? (
                            // eslint-disable-next-line @next/next/no-img-element
                            <img
                              src={partImageUrl(r.sims_url)}
                              alt={r.part_number}
                              loading="lazy"
                              className="h-full w-full object-contain"
                              onError={(e) => { (e.target as HTMLImageElement).style.visibility = "hidden"; }}
                            />
                          ) : (
                            <div className="grid h-full w-full place-items-center" style={{ color: "var(--ink-300)", fontSize: 28 }}>🔩</div>
                          )}
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
                          <p className="truncate" style={{ fontSize: 12, marginTop: 2 }}>
                            {r.tersedia ? (
                              <span style={{ color: "var(--ok-600, #16a34a)", fontWeight: 600 }}>✅ Stok {r.stok}</span>
                            ) : (
                              <span style={{ color: "var(--ink-400)" }}>Stok {r.stok || "—"}</span>
                            )}
                            {r.harga && r.harga !== "—" && (
                              <span style={{ color: "var(--ink-500)" }}> · {r.harga}</span>
                            )}
                          </p>
                          <div className="flex items-center" style={{ gap: 6, marginTop: 4 }}>
                            <div style={{ height: 6, flex: 1, overflow: "hidden", borderRadius: 99, background: "var(--ink-100)" }}>
                              <div style={{ height: "100%", background: "var(--brand-600)", width: `${pct}%` }} />
                            </div>
                            <span className="mono" style={{ fontSize: 11.5, fontWeight: 600, color: "var(--ink-600)" }}>{pct}%</span>
                          </div>
                        </div>
                      </button>
                      {canLearn && file && (
                        <button
                          onClick={() => handleLearn(r.part_number)}
                          disabled={!!learnBusy || learnedPn === r.part_number}
                          className="block w-full"
                          title="Konfirmasi foto query = part ini → indeks ke galeri (belajar)"
                          style={{ padding: "5px 8px", fontSize: 11.5, cursor: "pointer", background: "var(--ink-50)", border: "none", borderTop: "1px solid var(--ink-100)", color: learnedPn === r.part_number ? "var(--ok-600, #16a34a)" : "var(--ink-500)" }}
                        >
                          {learnedPn === r.part_number ? "✅ terindeks" : learnBusy === r.part_number ? "mengindeks…" : "✓ benar part ini → ajari galeri"}
                        </button>
                      )}
                    </div>
                  );
                })}
              </div>
            )}

            {!searched && !error && (
              <div className="surface grid place-items-center" style={{ height: 224, color: "var(--ink-400)", fontSize: 13.5, padding: 16, textAlign: "center" }}>
                Hasil pencarian akan muncul di sini. Tips akurasi: foto dekat, part di tengah, lalu ✂ Crop ke part-nya saja.
              </div>
            )}
          </section>
        </div>
      </div>
    </AppShell>
  );
}
