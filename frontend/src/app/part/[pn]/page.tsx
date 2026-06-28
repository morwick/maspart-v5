"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import AppShell from "@/components/AppShell";
import { ApiError, getPartPhotos, getBuyerLocations, partImageUrl, searchParts, type BuyerLocation, type PartResult } from "@/lib/api";
import { clearSession, getToken, getUser } from "@/lib/auth";
import { ensurePerms } from "@/lib/perms";
import { addToCart, hasPrice, hasWeight } from "@/lib/cart";

// Buang prefix nomor gudang untuk tampilan pembeli ("01.Jakarta" → "Jakarta").
const locName = (s: string) => s.replace(/^\s*\d+\s*\.\s*/, "").trim() || s;

export default function PartDetailPage() {
  const router = useRouter();
  const params = useParams<{ pn: string }>();
  const pn = decodeURIComponent(Array.isArray(params.pn) ? params.pn[0] : params.pn ?? "");

  const [units, setUnits] = useState<PartResult[]>([]);
  const [photos, setPhotos] = useState<string[]>([]);
  const [photoSource, setPhotoSource] = useState("");
  const [loading, setLoading] = useState(true);
  const [loadingPhotos, setLoadingPhotos] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [lightbox, setLightbox] = useState<string | null>(null);
  const [zoom, setZoom] = useState(1);
  const [pan, setPan] = useState({ x: 0, y: 0 });
  const [dragging, setDragging] = useState(false);
  const lbRef = useRef<HTMLDivElement>(null);
  const dragRef = useRef<{ x: number; y: number } | null>(null);
  const movedRef = useRef(false);
  const zoomRef = useRef(1);
  const [backHref, setBackHref] = useState("/search");
  const [showStok, setShowStok] = useState(true);
  const [showHarga, setShowHarga] = useState(true);
  const [isBuyer, setIsBuyer] = useState(false);
  const [buyerLocs, setBuyerLocs] = useState<BuyerLocation[]>([]);

  useEffect(() => {
    const b = getUser()?.role === "pembeli";
    setIsBuyer(b);
    if (b) {
      const t = getToken();
      if (t) getBuyerLocations(t).then((d) => setBuyerLocs(d.locations)).catch(() => {});
    }
    ensurePerms().then((p) => {
      if (p) {
        setShowStok(p.columns.includes("col_stok"));
        setShowHarga(p.columns.includes("col_harga"));
      }
    });
  }, []);

  useEffect(() => {
    const from = new URLSearchParams(window.location.search).get("from");
    setBackHref(from === "image" ? "/search-image" : "/search");
  }, []);

  useEffect(() => {
    const token = getToken();
    if (!token) {
      router.replace("/login");
      return;
    }
    let active = true;
    setLoading(true);
    searchParts(pn, token, "pn")
      .then((res) => {
        if (!active) return;
        const exact = res.results.filter((r) => r.part_number.toUpperCase() === pn.toUpperCase());
        setUnits(exact.length ? exact : res.results);
      })
      .catch((err) => {
        if (!active) return;
        if (err instanceof ApiError && err.status === 401) {
          clearSession();
          return router.replace("/login");
        }
        setError(err instanceof Error ? err.message : "Gagal memuat data");
      })
      .finally(() => active && setLoading(false));

    setLoadingPhotos(true);
    getPartPhotos(pn, token)
      .then((r) => {
        if (!active) return;
        setPhotos(r.photos);
        setPhotoSource(r.source);
      })
      .catch(() => active && setPhotos([]))
      .finally(() => active && setLoadingPhotos(false));

    return () => {
      active = false;
    };
  }, [pn, router]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setLightbox(null);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  // Reset zoom/geser tiap kali buka gambar baru.
  useEffect(() => {
    setZoom(1);
    setPan({ x: 0, y: 0 });
  }, [lightbox]);

  useEffect(() => {
    zoomRef.current = zoom;
    if (zoom <= 1) setPan({ x: 0, y: 0 });
  }, [zoom]);

  // Lightbox: scroll untuk zoom (non-passive) + seret untuk geser.
  useEffect(() => {
    if (!lightbox) return;
    const el = lbRef.current;
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      setZoom((z) => Math.min(8, Math.max(1, z * (e.deltaY < 0 ? 1.12 : 0.89))));
    };
    el?.addEventListener("wheel", onWheel, { passive: false });
    const onMove = (e: MouseEvent) => {
      if (!dragRef.current || zoomRef.current <= 1) return;
      movedRef.current = true;
      setPan({ x: e.clientX - dragRef.current.x, y: e.clientY - dragRef.current.y });
    };
    const onUp = () => {
      dragRef.current = null;
      setDragging(false);
    };
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
    return () => {
      el?.removeEventListener("wheel", onWheel);
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
    };
  }, [lightbox]);

  const main = units[0];
  const gudang = useMemo(() => Object.entries(main?.gudang || {}), [main]);
  // Stok untuk pembeli: hanya daerah terpilih (sudah discope backend, fallback
  // ke lokasi terdekat bila daerah terpilih kosong).
  const buyerStock = useMemo(() => {
    if (!gudang.length) return null;
    const qty = gudang.reduce((n, [, q]) => n + (Number(q) || 0), 0);
    return { qty, loc: locName(gudang[0][0]) };
  }, [gudang]);
  // Key gudang untuk chat: cocokkan lokasi stok → key; fallback ke lokasi pembeli.
  const chatKey = useMemo(() => {
    if (!isBuyer) return null;
    const byLabel = buyerStock ? buyerLocs.find((l) => l.label === buyerStock.loc)?.key : undefined;
    return byLabel ?? getUser()?.gudang ?? null;
  }, [isBuyer, buyerLocs, buyerStock]);

  return (
    <AppShell
      active={backHref}
      title={main ? main.part_number : pn}
      sub={main?.part_name || "Detail part"}
      actions={
        <>
          {main && isBuyer && (
            (!buyerStock || buyerStock.qty <= 0) ? (
              <span className="pill pill-danger" title="Stok habis di lokasimu">Stok habis</span>
            ) : !hasPrice(main.harga) ? (
              <span className="pill pill-warn" title="Harga belum tersedia — belum bisa dibeli">Tanpa harga</span>
            ) : !hasWeight(main.berat) ? (
              <span className="pill pill-warn" title="Berat belum ditetapkan admin — belum bisa dibeli">Tanpa berat</span>
            ) : (
              <button
                className="btn btn-primary btn-sm"
                onClick={() => addToCart({ part_number: main.part_number, name: main.part_name, harga: main.harga, berat: main.berat })}
              >
                + 🛒 Keranjang
              </button>
            )
          )}
          {isBuyer && (
            <button
              className="btn btn-secondary btn-sm"
              onClick={() => router.push(`/chat${chatKey ? `?gudang=${encodeURIComponent(chatKey)}` : ""}`)}
              title="Tanya ketersediaan ke gudang"
            >
              💬 Chat Gudang
            </button>
          )}
          <Link href={backHref} className="btn btn-secondary btn-sm">
            ← Kembali
          </Link>
        </>
      }
    >
      <div className="mx-auto w-full max-w-5xl px-4 py-5 sm:px-6">
        {error ? (
          <div className="alert alert-error">{error}</div>
        ) : loading ? (
          <div className="surface grid place-items-center" style={{ height: 200, color: "var(--ink-500)" }}>
            Memuat detail part…
          </div>
        ) : !main ? (
          <div className="surface grid place-items-center" style={{ height: 200, color: "var(--ink-500)" }}>
            Part <span className="mono" style={{ margin: "0 4px" }}>{pn}</span> tidak ditemukan.
          </div>
        ) : (
          <>
            {/* Heading */}
            <div className="mb-4">
              <div className="mono" style={{ fontSize: 24, fontWeight: 600 }}>{main.part_number}</div>
              <div style={{ color: "var(--ink-600)", marginTop: 2 }}>{main.part_name}</div>
            </div>

            <div className="grid gap-5 md:grid-cols-2">
              {/* Gambar */}
              <section className="surface surface-pad">
                <div className="mb-3 flex items-center gap-2">
                  <h2 style={{ fontSize: 13, fontWeight: 600 }}>Gambar Part</h2>
                  {photoSource && photos.length > 0 && (
                    <span className="pill">sumber: {photoSource === "sims" ? "SIMS" : photoSource === "image_index" ? "galeri foto" : "part_photos"}</span>
                  )}
                </div>
                {loadingPhotos ? (
                  <div className="img-ph" style={{ height: 220 }}>memuat…</div>
                ) : photos.length > 0 ? (
                  <div className="grid grid-cols-2 gap-2">
                    {photos.map((url, i) => (
                      <button
                        key={i}
                        type="button"
                        onClick={() => setLightbox(url)}
                        className="overflow-hidden"
                        style={{ cursor: "zoom-in", borderRadius: 8, border: "1px solid var(--ink-200)", background: "var(--paper)" }}
                      >
                        {/* eslint-disable-next-line @next/next/no-img-element */}
                        <img src={partImageUrl(url)} alt={`${main.part_number} ${i + 1}`} loading="lazy" className="w-full" style={{ aspectRatio: "1", objectFit: "contain" }} />
                      </button>
                    ))}
                  </div>
                ) : (
                  <div className="img-ph" style={{ height: 220 }}>Tidak ada gambar</div>
                )}
              </section>

              {/* Info */}
              <section className="flex flex-col gap-4">
                {/* Pembeli: stok hanya untuk daerah terpilih (tanpa total) */}
                {isBuyer ? (
                  <div className="grid grid-cols-2 gap-3">
                    <div className="surface surface-pad">
                      <div className="stat-label">Stok tersedia</div>
                      <div className="stat-value">{buyerStock ? buyerStock.qty.toLocaleString("id-ID") : 0}</div>
                      <div style={{ fontSize: 12, color: "var(--ink-500)", marginTop: 2 }}>
                        {buyerStock ? `di ${buyerStock.loc}` : "stok tidak tersedia"}
                      </div>
                    </div>
                    {/* Pembeli selalu melihat harga (perlu untuk belanja), lepas dari izin kolom internal. */}
                    <div className="surface surface-pad">
                      <div className="stat-label">Harga</div>
                      <div className="stat-value mono" style={{ color: "var(--brand-700)", fontSize: 18 }}>{main.harga}</div>
                    </div>
                  </div>
                ) : (
                  <>
                    {(showStok || showHarga) && (
                      <div className="grid grid-cols-2 gap-3">
                        {showStok && (
                          <div className="surface surface-pad">
                            <div className="stat-label">Stok total</div>
                            <div className="stat-value">{main.stok}</div>
                          </div>
                        )}
                        {showHarga && (
                          <div className="surface surface-pad">
                            <div className="stat-label">Harga</div>
                            <div className="stat-value mono" style={{ color: "var(--brand-700)", fontSize: 18 }}>{main.harga}</div>
                          </div>
                        )}
                      </div>
                    )}

                    {showStok && (
                      <div className="surface" style={{ overflow: "hidden" }}>
                        <div className="px-4 py-2.5" style={{ fontSize: 13, fontWeight: 600, borderBottom: "1px solid var(--ink-150)" }}>
                          Stok per Gudang
                        </div>
                        {gudang.length > 0 ? (
                          <table className="tbl">
                            <tbody>
                              {gudang.map(([nama, qty]) => (
                                <tr key={nama}>
                                  <td style={{ color: "var(--ink-600)" }}>{nama}</td>
                                  <td className="num" style={{ fontWeight: 550 }}>{qty}</td>
                                </tr>
                              ))}
                            </tbody>
                          </table>
                        ) : (
                          <div className="px-4 py-3" style={{ fontSize: 13, color: "var(--ink-400)" }}>
                            Tidak ada rincian stok per gudang.
                          </div>
                        )}
                      </div>
                    )}
                  </>
                )}

                {units.length > 0 && (
                  <div>
                    <h2 className="mb-2" style={{ fontSize: 13, fontWeight: 600, color: "var(--ink-700)" }}>
                      Ditemukan di {units.length} unit
                    </h2>
                    <div className="flex flex-wrap gap-1.5">
                      {units.map((u, i) => (
                        <span key={`${u.file}-${i}`} className="pill">{u.file}</span>
                      ))}
                    </div>
                  </div>
                )}
              </section>
            </div>
          </>
        )}
      </div>

      {lightbox && (
        <div
          ref={lbRef}
          className="fixed inset-0 z-50 grid place-items-center p-4"
          style={{ background: "rgba(0,0,0,.85)", overflow: "hidden", cursor: zoom > 1 ? (dragging ? "grabbing" : "grab") : "default" }}
          onMouseDown={(e) => {
            if (e.button !== 0 || zoom <= 1) return;
            dragRef.current = { x: e.clientX - pan.x, y: e.clientY - pan.y };
            movedRef.current = false;
            setDragging(true);
          }}
          onClick={() => {
            if (movedRef.current) {
              movedRef.current = false;
              return;
            }
            setLightbox(null);
          }}
        >
          <button
            type="button"
            onClick={() => setLightbox(null)}
            className="btn btn-sm"
            style={{ position: "absolute", right: 16, top: 16, background: "rgba(255,255,255,.12)", color: "#fff", zIndex: 2 }}
          >
            ✕ Tutup
          </button>
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src={partImageUrl(lightbox)}
            alt="Foto part"
            draggable={false}
            onClick={(e) => e.stopPropagation()}
            onDoubleClick={(e) => {
              e.stopPropagation();
              setZoom(1);
              setPan({ x: 0, y: 0 });
            }}
            style={{
              maxHeight: "90vh",
              maxWidth: "100%",
              borderRadius: 8,
              objectFit: "contain",
              transform: `translate(${pan.x}px, ${pan.y}px) scale(${zoom})`,
              transition: dragging ? "none" : "transform .08s ease-out",
              userSelect: "none",
            }}
          />
          <div
            style={{
              position: "absolute", bottom: 14, left: 0, right: 0, textAlign: "center",
              fontSize: 12, color: "rgba(255,255,255,.6)", pointerEvents: "none",
            }}
          >
            Scroll untuk zoom · seret untuk geser · klik dua kali untuk reset
          </div>
        </div>
      )}
    </AppShell>
  );
}
