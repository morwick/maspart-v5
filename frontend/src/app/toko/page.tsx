"use client";

/* ETALASE BELANJA PEMBELI — beranda toko gaya e-commerce.
   Data dari /api/buyer/home (kategori, terlaris, unggulan) + /api/buyer/catalog
   (grid produk: cari, kategori, sort, ready-only, muat-lebih). Stok sudah
   di-scope backend ke lokasi gudang pembeli. */

import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import AppShell from "@/components/AppShell";
import {
  ApiError,
  getTokoCatalog,
  getTokoHome,
  partImageUrl,
  type TokoCatalog,
  type TokoHome,
  type TokoProduct,
} from "@/lib/api";
import { clearSession, getToken, getUser } from "@/lib/auth";
import { addToCart, getCart, onCartChange, removeFromCart, setQty } from "@/lib/cart";

const PAGE_SIZE = 24;

/* ── Ikon ── */
const Ic = ({ d, s = 16 }: { d: ReactNode; s?: number }) => (
  <svg width={s} height={s} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.7} strokeLinecap="round" strokeLinejoin="round" aria-hidden>{d}</svg>
);
const I = {
  search: <Ic d={<><circle cx="11" cy="11" r="7" /><path d="m20 20-3.5-3.5" /></>} />,
  pin: <Ic d={<><path d="M12 21s-6-5.2-6-10a6 6 0 1 1 12 0c0 4.8-6 10-6 10Z" /><circle cx="12" cy="11" r="2.2" /></>} />,
  cart: <Ic d={<><path d="M3 4h2l2.4 12.5a1 1 0 0 0 1 .8h8.7a1 1 0 0 0 1-.8L21 8H6" /><circle cx="9" cy="20" r="1" /><circle cx="18" cy="20" r="1" /></>} />,
  gear: <Ic s={30} d={<><circle cx="12" cy="12" r="3" /><path d="M12 2v3M12 19v3M2 12h3M19 12h3M4.9 4.9l2.1 2.1M17 17l2.1 2.1M19.1 4.9 17 7M7 17l-2.1 2.1" /></>} />,
  fire: <Ic d={<><path d="M12 21c-4 0-6.5-2.5-6.5-6 0-2.6 1.7-4.6 3-6 .3 1.3 1 2 2 2.5C10.5 8 11 5 13.5 3c-.3 2.5 1 4 2.5 5.5S18.5 12 18.5 15c0 3.5-2.5 6-6.5 6Z" /></>} />,
  spark: <Ic d={<><path d="m12 3 1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z" /><path d="M19 16l.8 2.2L22 19l-2.2.8L19 22l-.8-2.2L16 19l2.2-.8z" /></>} />,
};

const rp = (n: number) => "Rp " + n.toLocaleString("id-ID");

/* ── Kartu produk ── */
function ProductCard({
  p, qty, onOpen, onAdd, onQty, onRemove,
}: {
  p: TokoProduct;
  qty: number;
  onOpen: () => void;
  onAdd: () => void;
  onQty: (q: number) => void;
  onRemove: () => void;
}) {
  const [imgErr, setImgErr] = useState(false);
  return (
    <div
      className="surface"
      style={{ overflow: "hidden", display: "flex", flexDirection: "column", cursor: "pointer", transition: "box-shadow .15s, transform .15s" }}
      onClick={onOpen}
      onMouseEnter={(e) => { e.currentTarget.style.boxShadow = "var(--shadow-2)"; e.currentTarget.style.transform = "translateY(-2px)"; }}
      onMouseLeave={(e) => { e.currentTarget.style.boxShadow = ""; e.currentTarget.style.transform = ""; }}
    >
      {/* Foto — overflow:hidden + minHeight:0 WAJIB: tanpa ini aspect-ratio kalah
          oleh ukuran min-content gambar yang intrinsik tinggi (mis. brake plate
          memanjang) → box melar, tinggi kartu jadi tak seragam. */}
      <div style={{ position: "relative", aspectRatio: "1 / 1", minHeight: 0, overflow: "hidden", background: "var(--ink-075, var(--ink-100))", display: "grid", placeItems: "center", color: "var(--ink-300)" }}>
        {p.foto && !imgErr ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img
            src={partImageUrl(p.foto)}
            alt={p.name}
            loading="lazy"
            onError={() => setImgErr(true)}
            style={{ width: "100%", height: "100%", objectFit: "contain", padding: 8, background: "#fff" }}
          />
        ) : (
          <div style={{ display: "grid", placeItems: "center", gap: 6, fontSize: 11 }}>
            {I.gear}
            <span>Belum ada foto</span>
          </div>
        )}
        <span
          className={"pill " + (p.ready ? "pill-brand" : "pill-danger")}
          style={{ position: "absolute", top: 8, left: 8, fontSize: 10.5, fontWeight: 650 }}
        >
          {p.ready ? `READY${p.gudang ? " · " + p.gudang : ""}` : "Habis"}
        </span>
      </div>

      {/* Isi */}
      <div style={{ padding: "10px 12px 12px", display: "flex", flexDirection: "column", gap: 4, flex: 1 }}>
        <div style={{ fontSize: 13, fontWeight: 600, lineHeight: 1.3, display: "-webkit-box", WebkitLineClamp: 2, WebkitBoxOrient: "vertical", overflow: "hidden", minHeight: 34 }}>
          {p.name}
        </div>
        <div className="mono" style={{ fontSize: 11, color: "var(--ink-500)" }}>{p.part_number}</div>
        <div style={{ marginTop: 2, fontSize: 15.5, fontWeight: 750, color: "var(--brand-700)" }}>{rp(p.harga)}</div>
        {p.ready && <div style={{ fontSize: 11, color: "var(--ink-500)" }}>Stok {p.stok.toLocaleString("id-ID")}</div>}

        <div style={{ marginTop: "auto", paddingTop: 8 }} onClick={(e) => e.stopPropagation()}>
          {qty > 0 ? (
            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <button className="btn btn-secondary btn-sm" style={{ width: 34 }} onClick={() => (qty <= 1 ? onRemove() : onQty(qty - 1))} aria-label="Kurangi">−</button>
              <span className="mono" style={{ minWidth: 26, textAlign: "center", fontWeight: 650 }}>{qty}</span>
              <button className="btn btn-secondary btn-sm" style={{ width: 34 }} onClick={() => onQty(qty + 1)} aria-label="Tambah">+</button>
              <span style={{ marginLeft: "auto", fontSize: 11, color: "var(--ink-500)" }}>di keranjang</span>
            </div>
          ) : (
            <button className="btn btn-primary btn-sm" style={{ width: "100%", display: "inline-flex", alignItems: "center", justifyContent: "center", gap: 6 }} disabled={!p.ready} onClick={onAdd}>
              {I.cart} {p.ready ? "Keranjang" : "Stok habis"}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

/* ── Strip horizontal (terlaris / unggulan) ── */
function ProductStrip({ title, icon, items, cartQty, open, add, qtyFn, rm }: {
  title: string; icon: ReactNode; items: TokoProduct[];
  cartQty: (pn: string) => number;
  open: (pn: string) => void; add: (p: TokoProduct) => void;
  qtyFn: (pn: string, q: number) => void; rm: (pn: string) => void;
}) {
  if (!items.length) return null;
  return (
    <div style={{ marginTop: 20 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 10 }}>
        <span style={{ color: "var(--brand-700)" }}>{icon}</span>
        <span style={{ fontSize: 14.5, fontWeight: 700 }}>{title}</span>
      </div>
      <div style={{ display: "grid", gridAutoFlow: "column", gridAutoColumns: "minmax(170px, 200px)", gap: 12, overflowX: "auto", paddingBottom: 6, scrollSnapType: "x proximity" }}>
        {items.map((p) => (
          <div key={p.part_number} style={{ scrollSnapAlign: "start", display: "grid" }}>
            <ProductCard
              p={p}
              qty={cartQty(p.part_number)}
              onOpen={() => open(p.part_number)}
              onAdd={() => add(p)}
              onQty={(q) => qtyFn(p.part_number, q)}
              onRemove={() => rm(p.part_number)}
            />
          </div>
        ))}
      </div>
    </div>
  );
}

export default function TokoPage() {
  const router = useRouter();

  const [home, setHome] = useState<TokoHome | null>(null);
  const [cat, setCat] = useState<TokoCatalog | null>(null);
  const [items, setItems] = useState<TokoProduct[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [q, setQ] = useState("");
  const [qLive, setQLive] = useState("");   // ter-debounce → dipakai fetch
  const [kategori, setKategori] = useState("");
  const [sort, setSort] = useState("relevan");
  const [readyOnly, setReadyOnly] = useState(false);
  const [page, setPage] = useState(1);

  // Qty keranjang per PN (untuk stepper di kartu).
  const [cartMap, setCartMap] = useState<Record<string, number>>({});
  const refreshCart = useCallback(() => {
    const m: Record<string, number> = {};
    for (const it of getCart()) m[it.part_number] = it.qty;
    setCartMap(m);
  }, []);
  useEffect(() => {
    refreshCart();
    return onCartChange(refreshCart);
  }, [refreshCart]);

  // Guard akses: halaman toko khusus pembeli.
  useEffect(() => {
    const token = getToken();
    if (!token) {
      router.replace("/login");
      return;
    }
    if (getUser()?.role !== "pembeli") {
      router.replace("/search");
      return;
    }
    getTokoHome(token)
      .then(setHome)
      .catch((err) => {
        if (err instanceof ApiError && err.status === 401) {
          clearSession();
          router.replace("/login");
        }
      });
  }, [router]);

  // Debounce ketikan pencarian 400 ms.
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => {
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => setQLive(q.trim()), 400);
    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
    };
  }, [q]);

  // Muat katalog (halaman 1) tiap filter berubah.
  useEffect(() => {
    const token = getToken();
    if (!token) return;
    let alive = true;
    setLoading(true);
    setError(null);
    getTokoCatalog(token, { q: qLive, kategori, sort, ready: readyOnly, page: 1, pageSize: PAGE_SIZE })
      .then((r) => {
        if (!alive) return;
        setCat(r);
        setItems(r.items);
        setPage(1);
      })
      .catch((err) => {
        if (!alive) return;
        if (err instanceof ApiError && err.status === 401) {
          clearSession();
          return router.replace("/login");
        }
        setError(err instanceof Error ? err.message : "Gagal memuat katalog");
      })
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
  }, [qLive, kategori, sort, readyOnly, router]);

  async function loadMore() {
    const token = getToken();
    if (!token || !cat || page >= cat.total_pages) return;
    setLoadingMore(true);
    try {
      const r = await getTokoCatalog(token, { q: qLive, kategori, sort, ready: readyOnly, page: page + 1, pageSize: PAGE_SIZE });
      setItems((prev) => prev.concat(r.items));
      setPage(r.page);
      setCat(r);
    } catch {
      /* biarkan — tombol bisa dicoba lagi */
    } finally {
      setLoadingMore(false);
    }
  }

  const open = useCallback((pn: string) => router.push(`/part/${encodeURIComponent(pn)}`), [router]);
  const add = useCallback((p: TokoProduct) => {
    addToCart({ part_number: p.part_number, name: p.name, harga: p.harga_display, berat: p.berat });
  }, []);
  const cartQty = useCallback((pn: string) => cartMap[pn] ?? 0, [cartMap]);
  const setQtyCb = useCallback((pn: string, qy: number) => setQty(pn, qy), []);
  const rm = useCallback((pn: string) => removeFromCart(pn), []);

  const browsing = !qLive && !kategori;   // mode jelajah → tampilkan strip
  const totalCart = useMemo(() => Object.values(cartMap).reduce((n, v) => n + v, 0), [cartMap]);
  const lokasi = cat?.lokasi ?? home?.lokasi ?? null;

  return (
    <AppShell active="/toko" title="Belanja Part" sub="Etalase part siap kirim dari gudang terdekat Anda.">
      <div style={{ padding: "18px 18px 60px" }}>
        {/* ── Hero: cari + lokasi + keranjang ── */}
        <div
          style={{
            borderRadius: 16, padding: "20px 20px 22px", color: "#fff",
            background: "linear-gradient(135deg, var(--brand-700), var(--brand-500))",
            boxShadow: "var(--shadow-2)", position: "relative", overflow: "hidden",
          }}
        >
          <div style={{ position: "absolute", right: -50, top: -50, width: 190, height: 190, borderRadius: "50%", background: "rgba(255,255,255,.08)" }} />
          <div style={{ position: "relative", display: "flex", flexWrap: "wrap", alignItems: "center", gap: 10 }}>
            <div style={{ minWidth: 220, flex: 1 }}>
              <h1 style={{ margin: 0, fontSize: 21, fontWeight: 750, letterSpacing: "-.02em" }}>Belanja Part</h1>
              <div style={{ fontSize: 12.5, opacity: 0.9, marginTop: 3 }}>
                {home ? `${home.total_produk.toLocaleString("id-ID")} produk siap dibeli` : "Memuat etalase…"}
              </div>
            </div>
            <Link
              href="/pilih-lokasi"
              style={{ display: "inline-flex", alignItems: "center", gap: 6, padding: "7px 13px", borderRadius: 999, background: "rgba(255,255,255,.16)", border: "1px solid rgba(255,255,255,.32)", color: "#fff", fontSize: 12.5, fontWeight: 600, textDecoration: "none" }}
              title="Ganti lokasi gudang"
            >
              {I.pin} {lokasi ? `Gudang ${lokasi}` : "Pilih lokasi"}
            </Link>
            <Link
              href="/keranjang"
              style={{ display: "inline-flex", alignItems: "center", gap: 6, padding: "7px 13px", borderRadius: 999, background: "#fff", color: "var(--brand-700)", fontSize: 12.5, fontWeight: 700, textDecoration: "none", position: "relative" }}
            >
              {I.cart} Keranjang{totalCart > 0 ? ` · ${totalCart}` : ""}
            </Link>
          </div>
          <div style={{ position: "relative", marginTop: 14, display: "flex", gap: 8 }}>
            <div style={{ position: "relative", flex: 1 }}>
              <span style={{ position: "absolute", left: 12, top: "50%", transform: "translateY(-50%)", color: "var(--ink-400)" }}>{I.search}</span>
              <input
                className="input"
                value={q}
                onChange={(e) => setQ(e.target.value)}
                placeholder="Cari part number atau nama part… (mis. filter oli, WG9725190102, kampas rem)"
                style={{ height: 46, fontSize: 14, paddingLeft: 38, border: "none", boxShadow: "var(--shadow-2)" }}
              />
            </div>
            {q && (
              <button className="btn" style={{ background: "rgba(255,255,255,.16)", color: "#fff", border: "1px solid rgba(255,255,255,.32)" }} onClick={() => setQ("")}>
                ✕
              </button>
            )}
          </div>
        </div>

        {/* ── Chip kategori ── */}
        {home && home.kategori.length > 0 && (
          <div style={{ display: "flex", gap: 8, overflowX: "auto", padding: "14px 2px 2px", scrollbarWidth: "thin" }}>
            <button
              className="pill"
              onClick={() => setKategori("")}
              style={{ cursor: "pointer", whiteSpace: "nowrap", fontWeight: 650, ...(kategori === "" ? { background: "var(--brand-700)", color: "#fff", borderColor: "var(--brand-700)" } : {}) }}
            >
              Semua
            </button>
            {home.kategori.map((k) => (
              <button
                key={k.key}
                className="pill"
                onClick={() => setKategori(kategori === k.key ? "" : k.key)}
                style={{ cursor: "pointer", whiteSpace: "nowrap", ...(kategori === k.key ? { background: "var(--brand-700)", color: "#fff", borderColor: "var(--brand-700)", fontWeight: 650 } : {}) }}
                title={`${k.count.toLocaleString("id-ID")} produk`}
              >
                {k.label} <span style={{ opacity: 0.65, fontSize: 10.5 }}>({k.count.toLocaleString("id-ID")})</span>
              </button>
            ))}
          </div>
        )}

        {error && <div className="alert alert-error" style={{ marginTop: 14 }}>{error}</div>}

        {/* ── Strip terlaris & unggulan (hanya mode jelajah) ── */}
        {browsing && home && (
          <>
            <ProductStrip title="Terlaris" icon={I.fire} items={home.terlaris} cartQty={cartQty} open={open} add={add} qtyFn={setQtyCb} rm={rm} />
            <ProductStrip title="Pilihan bergambar" icon={I.spark} items={home.unggulan} cartQty={cartQty} open={open} add={add} qtyFn={setQtyCb} rm={rm} />
          </>
        )}

        {/* ── Toolbar hasil ── */}
        <div style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: 10, marginTop: 22, marginBottom: 12 }}>
          <div style={{ fontSize: 14.5, fontWeight: 700 }}>
            {qLive ? `Hasil "${qLive}"` : kategori ? (home?.kategori.find((k) => k.key === kategori)?.label ?? "Kategori") : "Semua Part"}
          </div>
          {cat && (
            <span style={{ fontSize: 12, color: "var(--ink-500)" }}>
              {cat.count.toLocaleString("id-ID")} produk
            </span>
          )}
          <div style={{ flex: 1 }} />
          <label style={{ display: "inline-flex", alignItems: "center", gap: 6, fontSize: 12.5, color: "var(--ink-600)", cursor: "pointer" }}>
            <input type="checkbox" checked={readyOnly} onChange={(e) => setReadyOnly(e.target.checked)} />
            Hanya READY
          </label>
          <select className="select" style={{ width: 170, height: 34, fontSize: 12.5 }} value={sort} onChange={(e) => setSort(e.target.value)}>
            <option value="relevan">Paling relevan</option>
            <option value="harga_asc">Harga terendah</option>
            <option value="harga_desc">Harga tertinggi</option>
            <option value="nama">Nama A–Z</option>
            <option value="stok">Stok terbanyak</option>
          </select>
        </div>

        {/* ── Grid produk ── */}
        {loading ? (
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(170px, 1fr))", gap: 12 }}>
            {Array.from({ length: 12 }).map((_, i) => (
              <div key={i} className="surface" style={{ height: 300, opacity: 0.55, animation: "pulse 1.4s ease-in-out infinite" }} />
            ))}
          </div>
        ) : items.length === 0 ? (
          <div className="surface grid place-items-center" style={{ height: 200, color: "var(--ink-500)", gap: 8, fontSize: 13.5 }}>
            <div>Tidak ada produk yang cocok{qLive ? ` dengan "${qLive}"` : ""}.</div>
            {(qLive || kategori) && (
              <button className="btn btn-secondary btn-sm" onClick={() => { setQ(""); setKategori(""); }}>
                Tampilkan semua produk
              </button>
            )}
          </div>
        ) : (
          <>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(170px, 1fr))", gap: 12 }}>
              {items.map((p) => (
                <ProductCard
                  key={p.part_number}
                  p={p}
                  qty={cartQty(p.part_number)}
                  onOpen={() => open(p.part_number)}
                  onAdd={() => add(p)}
                  onQty={(qy) => setQtyCb(p.part_number, qy)}
                  onRemove={() => rm(p.part_number)}
                />
              ))}
            </div>
            {cat && page < cat.total_pages && (
              <div style={{ display: "grid", placeItems: "center", marginTop: 18 }}>
                <button className="btn btn-secondary" onClick={loadMore} disabled={loadingMore}>
                  {loadingMore ? "Memuat…" : `Muat lebih banyak (${(cat.count - items.length).toLocaleString("id-ID")} lagi)`}
                </button>
              </div>
            )}
          </>
        )}
      </div>
    </AppShell>
  );
}
