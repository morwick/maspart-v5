"use client";

import { useCallback, useEffect, useMemo, useState, type CSSProperties } from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import AppShell from "@/components/AppShell";
import ImageLightbox from "@/components/ImageLightbox";
import { ApiError, cekPartDiUnit, deleteRakFoto, getAccurateStock, getPartExploded, getPartExplodedFigure, getPartPhotos, getPartSpec, getPartVarian, getBuyerLocations, getRakForPart, getWeichaiStock, partImageUrl, saveRak, searchParts, uploadRakFoto, type AccurateStock, type BuyerLocation, type CekUnitResult, type PartExplodedFigure, type PartResult, type PartSpec, type PartVarian, type PartVarianItem, type RakInfo, type WeichaiStock } from "@/lib/api";
import { clearSession, getToken, getUser } from "@/lib/auth";
import { ensurePerms } from "@/lib/perms";
import { addToCart, hasPrice, hasWeight } from "@/lib/cart";

// Buang prefix nomor gudang untuk tampilan pembeli ("01.Jakarta" → "Jakarta").
const locName = (s: string) => s.replace(/^\s*\d+\s*\.\s*/, "").trim() || s;

const angka = (n: number) => Number(n).toLocaleString("id-ID");
const rupiah = (n: number) => "Rp " + angka(n);

// Sel baris TOTAL tabel gudang. Ditulis inline karena `.tbl tbody td` tidak
// menjangkau <tfoot> — tanpa ini barisnya kehilangan tinggi & padding.
const SEL_TOTAL: CSSProperties = {
  background: "var(--ink-50)",
  fontWeight: 600,
  height: "var(--row-h)",
  padding: "0 var(--row-px)",
  borderTop: "1px solid var(--ink-200)",
};

export default function PartDetailPage() {
  const router = useRouter();
  const params = useParams<{ pn: string }>();
  const pn = decodeURIComponent(Array.isArray(params.pn) ? params.pn[0] : params.pn ?? "");

  const [units, setUnits] = useState<PartResult[]>([]);
  const [photos, setPhotos] = useState<string[]>([]);
  const [photoSource, setPhotoSource] = useState("");
  const [spec, setSpec] = useState<PartSpec | null>(null);
  const [specBeratGram, setSpecBeratGram] = useState(0);
  const [loading, setLoading] = useState(true);
  const [loadingPhotos, setLoadingPhotos] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [lightbox, setLightbox] = useState<string | null>(null);
  const [backHref, setBackHref] = useState("/search");
  const [showStok, setShowStok] = useState(true);
  const [showHarga, setShowHarga] = useState(true);
  const [isBuyer, setIsBuyer] = useState(false);
  const [buyerLocs, setBuyerLocs] = useState<BuyerLocation[]>([]);
  const [accStock, setAccStock] = useState<AccurateStock | null>(null);
  // KELUARGA VARIAN PEMASOK: satu part fisik bisa punya beberapa kartu barang
  // Accurate (base + '/SN' + '/SH'), masing-masing beda stok & HARGA.
  // `tabVarian` = kode varian yang sedang dilihat; "" = tab "Semua varian".
  const [varianData, setVarianData] = useState<PartVarian | null>(null);
  const [tabVarian, setTabVarian] = useState("");
  // Exploded view TANPA nomor rangka. SENGAJA tidak dimuat saat halaman dibuka:
  // panggilan pertama bisa 10-60 dtk (PN umum dipakai belasan ribu model), dan
  // gambar hanya ditampilkan bila user memang meminta.
  const [exploded, setExploded] = useState<PartExplodedFigure | null>(null);
  const [explodedBusy, setExplodedBusy] = useState(false);
  const [explodedErr, setExplodedErr] = useState<string | null>(null);
  // Rak & kartu stok per gudang (staf internal saja). `kelola` = gudang yang
  // boleh DITULIS akun ini; admin selalu boleh.
  const [rakMap, setRakMap] = useState<Record<string, RakInfo>>({});
  const [rakOpen, setRakOpen] = useState<string | null>(null);
  const [kelola, setKelola] = useState<string[]>([]);
  const [isAdmin, setIsAdmin] = useState(false);

  useEffect(() => {
    const b = getUser()?.role === "pembeli";
    setIsBuyer(b);
    if (b) {
      const t = getToken();
      if (t) getBuyerLocations(t).then((d) => setBuyerLocs(d.locations)).catch(() => {});
    }
    // Admin selalu melihat stok & harga — izin kolom hanya membatasi staf bawahan,
    // bukan admin (samakan dgn backend yang memberi admin semua kolom).
    const admin = getUser()?.role === "admin";
    setIsAdmin(admin);
    ensurePerms().then((p) => {
      if (p) {
        setShowStok(admin || p.columns.includes("col_stok"));
        setShowHarga(admin || p.columns.includes("col_harga"));
        setKelola(p.gudang_kelola ?? []);
      }
    });
  }, []);

  useEffect(() => {
    const from = new URLSearchParams(window.location.search).get("from");
    setBackHref(from === "image" ? "/search-image" : "/search");
  }, []);

  // Stok live Accurate (kolom tambahan) — non-fatal, independen dari data utama.
  useEffect(() => {
    const t = getToken();
    if (!pn || !t) return;
    setAccStock(null);
    getAccurateStock(pn, t).then(setAccStock).catch(() => setAccStock(null));
  }, [pn]);

  // Keluarga varian pemasok — non-fatal (pola getAccurateStock): kalau gagal,
  // `varianData` tetap null dan SELURUH tampilan lama dipakai apa adanya.
  useEffect(() => {
    const t = getToken();
    if (!pn || !t) return;
    setVarianData(null);
    setTabVarian("");
    getPartVarian(pn, t).then(setVarianData).catch(() => setVarianData(null));
  }, [pn]);

  // Rak & kartu stok — hanya staf internal (backend 403 untuk pembeli) dan
  // hanya bila kolom stok memang boleh dilihat. Non-fatal: gagal baca rak tak
  // boleh menjatuhkan info stok/harga.
  const muatRak = useCallback(async () => {
    const t = getToken();
    if (!pn || !t || isBuyer || !showStok) return;
    try {
      const r = await getRakForPart(t, pn);
      setRakMap(r.rak || {});
    } catch {
      setRakMap({});
    }
  }, [pn, isBuyer, showStok]);

  useEffect(() => {
    setRakMap({});
    setRakOpen(null);
    void muatRak();
  }, [muatRak]);

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

    // Spesifikasi fisik resmi (berat/dimensi dari SIMS) — non-blocking.
    setSpec(null);
    setSpecBeratGram(0);
    getPartSpec(pn, token)
      .then((r) => {
        if (!active) return;
        setSpec(r.spec && Object.keys(r.spec).length ? r.spec : null);
        setSpecBeratGram(r.berat_gram || 0);
      })
      .catch(() => {});

    return () => {
      active = false;
    };
  }, [pn, router]);

  const main = units[0];
  const gudang = useMemo(() => Object.entries(main?.gudang || {}), [main]);
  // Stok: Accurate = sumber UTAMA (data resmi ERP). Excel stok.xlsx = FALLBACK,
  // dipakai bila fetch Accurate gagal/tak tersedia/PN tak ada (Excel di-export dari
  // Accurate jadi datanya sama). Pembeli TETAP pakai stok lokal terscope (alur beli).
  const acc = !isBuyer && accStock?.found && accStock.stock ? accStock.stock : null;
  const stokLive = !!acc;
  const displayGudang: [string, number][] = acc
    ? (acc.per_gudang ?? []).map((g) => [g.gudang, g.qty] as [string, number])
    : gudang;
  const displayTotal = acc
    ? `${acc.available_to_sell.toLocaleString("id-ID")} ${acc.unit}`
    : (main?.stok ?? "—");
  // Baris tabel gudang = UNION gudang berstok ∪ gudang yang punya data rak.
  // Gudang ber-rak tapi stok 0 SENGAJA tetap tampil: justru saat barang habis
  // orang paling butuh tahu rak lamanya (mau diisi ulang / cek sisa fisik).
  const barisGudang: [string, number][] = useMemo(() => {
    const m = new Map<string, number>();
    for (const [nama, qty] of displayGudang) m.set(nama, Number(qty) || 0);
    for (const label of Object.keys(rakMap)) if (!m.has(label)) m.set(label, 0);
    return [...m.entries()];
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [JSON.stringify(displayGudang), rakMap]);
  // HARGA JUAL: Accurate = utama (juga isi part yg lokalnya kosong → jadi bisa dibeli),
  // `harga.xlsx` fallback. Berlaku semua peran (termasuk pembeli).
  const accHarga = accStock?.found && accStock.stock?.harga ? accStock.stock.harga : 0;
  const hargaLive = accHarga > 0;
  const hargaStr = hargaLive ? "Rp " + accHarga.toLocaleString("id-ID") : (main?.harga ?? "—");
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

  // ── Varian pemasok ─────────────────────────────────────────────────────────
  // UI varian hanya hidup bila keluarga memang > 1 kartu. Satu anggota (atau
  // endpoint gagal/tak dikonfigurasi) → `varian` null → tampilan lama utuh.
  const varian: PartVarianItem[] | null =
    varianData?.found && (varianData.varian?.length ?? 0) > 1 ? varianData.varian! : null;
  const varianAktif = varian?.find((v) => v.kode === tabVarian) ?? null;
  // Label pendek untuk chip/kolom tabel: "…/SN" → "/SN", kartu dasar → "base".
  const labelVarian = useCallback(
    (kode: string) => {
      const b = (varianData?.base || "").toUpperCase();
      const k = kode.toUpperCase();
      if (!b) return kode;
      if (k === b) return "base";
      return k.startsWith(b) ? kode.slice(b.length) : kode;
    },
    [varianData?.base],
  );
  // Tabel GABUNGAN: satu baris per gudang, satu kolom per varian + Total.
  // Gudang ber-rak tapi tanpa stok tetap ikut (alasan sama dgn `barisGudang`).
  const barisVarianSemua = useMemo(() => {
    if (!varian) return [];
    const m = new Map<string, number[]>();
    varian.forEach((v, i) => {
      for (const g of v.per_gudang ?? []) {
        const row = m.get(g.gudang) ?? Array<number>(varian.length).fill(0);
        row[i] += Number(g.qty) || 0;
        m.set(g.gudang, row);
      }
    });
    for (const label of Object.keys(rakMap)) {
      if (!m.has(label)) m.set(label, Array<number>(varian.length).fill(0));
    }
    return [...m.entries()]
      .map(([nama, kolom]) => ({ nama, kolom, total: kolom.reduce((a, b) => a + b, 0) }))
      .sort((a, b) => b.total - a.total || a.nama.localeCompare(b.nama));
  }, [varian, rakMap]);
  // Tabel SATU varian terpilih.
  const barisVarianAktif = useMemo<[string, number][]>(() => {
    if (!varianAktif) return [];
    const m = new Map<string, number>();
    for (const g of varianAktif.per_gudang ?? []) {
      m.set(g.gudang, (m.get(g.gudang) ?? 0) + (Number(g.qty) || 0));
    }
    for (const label of Object.keys(rakMap)) if (!m.has(label)) m.set(label, 0);
    return [...m.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
  }, [varianAktif, rakMap]);
  // Total kolom = jumlah baris yang BENAR-BENAR ditampilkan (bukan
  // `total_available`), supaya footer tabel selalu konsisten dengan isinya.
  const totalKolomVarian = useMemo(() => {
    if (!varian) return [];
    return varian.map((_, i) => barisVarianSemua.reduce((n, r) => n + r.kolom[i], 0));
  }, [varian, barisVarianSemua]);
  // Baris tabel gudang yang BENAR-BENAR dirender — satu jalur untuk ketiga mode
  // (tanpa varian / tab "Semua" / tab satu varian) supaya integrasi RAK per baris
  // tetap satu tempat. `kolom` hanya terisi di mode gabungan.
  const barisTampil = useMemo<{ nama: string; qty: number; kolom?: number[] }[]>(() => {
    if (!varian) return barisGudang.map(([nama, qty]) => ({ nama, qty }));
    if (varianAktif) return barisVarianAktif.map(([nama, qty]) => ({ nama, qty }));
    return barisVarianSemua.map((r) => ({ nama: r.nama, qty: r.total, kolom: r.kolom }));
  }, [varian, varianAktif, barisGudang, barisVarianAktif, barisVarianSemua]);
  const totalTampil = useMemo(() => barisTampil.reduce((n, r) => n + r.qty, 0), [barisTampil]);
  // Label rentang harga — LABEL saja, tak pernah dirata-rata (aturan pemilik).
  const rentangHarga = useMemo(() => {
    const lo = varianData?.harga_min;
    const hi = varianData?.harga_max;
    if (!lo || !hi) return null;
    return lo === hi ? rupiah(lo) : `${rupiah(lo)} – ${angka(hi)}`;
  }, [varianData?.harga_min, varianData?.harga_max]);

  async function loadExploded() {
    if (explodedBusy || exploded) return;
    const token = getToken();
    if (!token) return router.replace("/login");
    setExplodedBusy(true);
    setExplodedErr(null);
    try {
      setExploded(await getPartExplodedFigure(token, pn));
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        clearSession();
        return router.replace("/login");
      }
      setExplodedErr(err instanceof Error ? err.message : "Gagal memuat exploded view.");
    } finally {
      setExplodedBusy(false);
    }
  }

  return (
    <AppShell
      active={backHref}
      title={main ? main.part_number : pn}
      sub={main?.part_name || "Detail part"}
      actions={
        <>
          {main && isBuyer && (
            // Keluarga varian: harga BEDA per pemasok → tak ada tombol beli
            // "gabungan" di header (keranjang wajib menunjuk kode spesifik).
            varian ? (
              <span className="pill pill-warn" title="Part ini punya beberapa varian pemasok — harga berbeda, pilih salah satu">
                {varian.length} varian — pilih di bawah
              </span>
            ) : (!buyerStock || buyerStock.qty <= 0) ? (
              <span className="pill pill-danger" title="Stok habis di lokasimu">Stok habis</span>
            ) : !hasPrice(hargaStr) ? (
              <span className="pill pill-warn" title="Harga belum tersedia — belum bisa dibeli">Tanpa harga</span>
            ) : !hasWeight(main.berat || specBeratGram) ? (
              <span className="pill pill-warn" title="Berat belum ditetapkan admin — belum bisa dibeli">Tanpa berat</span>
            ) : (
              <button
                className="btn btn-primary btn-sm"
                onClick={() => addToCart({ part_number: main.part_number, name: main.part_name, harga: hargaStr, berat: main.berat || specBeratGram })}
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
              <div className="flex flex-wrap items-center gap-2">
                <div className="mono" style={{ fontSize: 24, fontWeight: 600 }}>{main.part_number}</div>
                {varian && (
                  <span className="pill pill-warn" title="Part yang sama dipecah jadi beberapa kartu barang Accurate — beda pemasok, beda harga">
                    {varian.length} varian pemasok
                  </span>
                )}
              </div>
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
                  varian ? (
                    // Keluarga varian pemasok: SATU BARIS per kartu — harga & stok
                    // wilayah PASTI milik varian itu, dan tombol keranjang menunjuk
                    // `kode` spesifik (tak ada penjualan atas nama "gabungan").
                    // Tetap TANPA sebaran antar-gudang (aturan pemilik).
                    <div className="surface" style={{ overflow: "hidden" }}>
                      <div className="px-4 py-2.5 flex flex-wrap items-center gap-2" style={{ fontSize: 13, fontWeight: 600, borderBottom: "1px solid var(--ink-150)" }}>
                        Pilih varian pemasok
                        <span className="pill pill-warn">{varian.length} pilihan</span>
                      </div>
                      {varian.map((v) => {
                        const hargaV = v.harga ? rupiah(v.harga) : "—";
                        const stokV = v.stok_wilayah ?? 0;
                        const beratV = main.berat || specBeratGram;
                        return (
                          <div
                            key={v.kode}
                            className="flex items-center gap-3 px-4 py-2.5"
                            style={{ borderBottom: "1px solid var(--ink-100)" }}
                          >
                            <div style={{ minWidth: 0 }}>
                              <div className="mono" style={{ fontSize: 13, fontWeight: 550 }}>{v.kode}</div>
                              <div style={{ fontSize: 11.5, color: "var(--ink-500)" }}>{v.nama || main.part_name}</div>
                            </div>
                            <div className="ml-auto" style={{ textAlign: "right" }}>
                              <div className="mono" style={{ fontSize: 13.5, fontWeight: 600, color: "var(--brand-700)" }}>{hargaV}</div>
                              <div style={{ fontSize: 11.5, color: "var(--ink-500)" }}>stok wilayahmu: {angka(stokV)}</div>
                            </div>
                            {stokV <= 0 ? (
                              <span className="pill pill-danger" title="Stok habis di wilayahmu">Stok habis</span>
                            ) : !hasPrice(hargaV) ? (
                              <span className="pill pill-warn" title="Harga belum tersedia — belum bisa dibeli">Tanpa harga</span>
                            ) : !hasWeight(beratV) ? (
                              <span className="pill pill-warn" title="Berat belum ditetapkan admin — belum bisa dibeli">Tanpa berat</span>
                            ) : (
                              <button
                                className="btn btn-primary btn-sm"
                                title={`Masukkan ${v.kode} ke keranjang`}
                                onClick={() => addToCart({ part_number: v.kode, name: v.nama || main.part_name, harga: hargaV, berat: beratV })}
                              >
                                + 🛒
                              </button>
                            )}
                          </div>
                        );
                      })}
                      <div className="px-4 py-2" style={{ fontSize: 11.5, color: "var(--ink-400)" }}>
                        Part fisik sama, pemasok berbeda — harga mengikuti varian yang dipilih.
                      </div>
                    </div>
                  ) : (
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
                      <div className="stat-label">Harga {hargaLive && <span className="pill pill-success" style={{ marginLeft: 6 }}>Accurate</span>}</div>
                      <div className="stat-value mono" style={{ color: "var(--brand-700)", fontSize: 18 }}>{hargaStr}</div>
                    </div>
                  </div>
                  )
                ) : (
                  <>
                    {/* Tab varian pemasok — default "Semua varian" (gabungan).
                        Hanya muncul bila keluarga memang > 1 kartu Accurate. */}
                    {varian && (showStok || showHarga) && (
                      <div className="tabs" role="tablist" aria-label="Varian pemasok" style={{ overflowX: "auto" }}>
                        <button
                          type="button"
                          role="tab"
                          aria-selected={!varianAktif}
                          className={"tab" + (!varianAktif ? " active" : "")}
                          style={{ flex: "0 0 auto", whiteSpace: "nowrap" }}
                          onClick={() => setTabVarian("")}
                        >
                          Semua varian
                          {showStok && varianData?.total_available != null && (
                            <span style={{ color: "var(--ink-400)", fontWeight: 450 }}>· {angka(varianData.total_available)}</span>
                          )}
                        </button>
                        {varian.map((v) => {
                          const aktif = varianAktif?.kode === v.kode;
                          const sub = [
                            showStok && v.stok != null ? angka(v.stok) : "",
                            showHarga && v.harga ? rupiah(v.harga) : "",
                          ].filter(Boolean).join(" · ");
                          return (
                            <button
                              key={v.kode}
                              type="button"
                              role="tab"
                              aria-selected={aktif}
                              className={"tab" + (aktif ? " active" : "")}
                              style={{ flex: "0 0 auto", whiteSpace: "nowrap" }}
                              onClick={() => setTabVarian(v.kode)}
                              title={v.nama}
                            >
                              <span className="mono">{v.kode}</span>
                              {sub && <span style={{ color: "var(--ink-400)", fontWeight: 450 }}>· {sub}</span>}
                            </button>
                          );
                        })}
                      </div>
                    )}

                    {(showStok || showHarga) && (
                      varian ? (
                        // Kartu versi VARIAN — menggantikan kartu stok/harga lama
                        // (jangan dirender dobel): tab "Semua" = total keluarga +
                        // rentang harga; tab varian = angka pasti kartu itu.
                        <div className="grid grid-cols-2 gap-3">
                          {showStok && (
                            <div className="surface surface-pad">
                              <div className="stat-label">
                                {varianAktif ? "Stok varian ini" : "Stok total"} <span className="pill pill-success" style={{ marginLeft: 6 }}>Accurate</span>
                              </div>
                              <div className="stat-value">
                                {varianAktif
                                  ? (varianAktif.stok != null ? `${angka(varianAktif.stok)}${varianAktif.unit ? ` ${varianAktif.unit}` : ""}` : "—")
                                  : (varianData?.total_available != null
                                      ? `${angka(varianData.total_available)}${varian[0]?.unit ? ` ${varian[0].unit}` : ""}`
                                      : "—")}
                              </div>
                              {varianAktif ? (
                                <div style={{ fontSize: 12, color: "var(--ink-500)", marginTop: 4 }}>{varianAktif.nama}</div>
                              ) : (
                                <div className="mt-2 flex flex-wrap gap-1.5">
                                  {varian.map((v) => (
                                    <span key={v.kode} className="pill mono" style={{ fontSize: 11 }} title={v.nama}>
                                      {labelVarian(v.kode)} <b>{v.stok != null ? angka(v.stok) : "—"}</b>
                                    </span>
                                  ))}
                                </div>
                              )}
                            </div>
                          )}
                          {showHarga && (
                            <div className="surface surface-pad">
                              <div className="stat-label">Harga <span className="pill pill-success" style={{ marginLeft: 6 }}>Accurate</span></div>
                              <div className="stat-value mono" style={{ color: "var(--brand-700)", fontSize: 18 }}>
                                {varianAktif
                                  ? (varianAktif.harga ? rupiah(varianAktif.harga) : "—")
                                  : (rentangHarga ?? "—")}
                              </div>
                              <div style={{ fontSize: 12, color: "var(--ink-500)", marginTop: 4 }}>
                                {varianAktif
                                  ? "harga pasti varian ini — dipakai keranjang & penawaran"
                                  : "beda per pemasok — pilih varian untuk harga pasti"}
                              </div>
                            </div>
                          )}
                          <div className="surface surface-pad" style={{ gridColumn: "1 / -1" }}>
                            <div className="stat-label">Kode Accurate</div>
                            <div className="stat-value mono" style={{ fontSize: 14 }}>
                              {varianAktif ? varianAktif.kode : `${varian.length} kartu barang`}
                            </div>
                            <div className="mono" style={{ fontSize: 12, color: "var(--ink-500)", marginTop: 4 }}>
                              {varianAktif
                                ? `Kode Accurate: ${varianAktif.no || "—"}`
                                : varian.map((v) => (v.no || "").split(".")[0]).filter(Boolean).join(" · ")}
                            </div>
                          </div>
                        </div>
                      ) : (
                      <div className="grid grid-cols-2 gap-3">
                        {showStok && (
                          <div className="surface surface-pad">
                            <div className="stat-label">
                              Stok total {stokLive && <span className="pill pill-success" style={{ marginLeft: 6 }}>Accurate</span>}
                            </div>
                            <div className="stat-value">{displayTotal}</div>
                          </div>
                        )}
                        {showHarga && (
                          <div className="surface surface-pad">
                            <div className="stat-label">Harga {hargaLive && <span className="pill pill-success" style={{ marginLeft: 6 }}>Accurate</span>}</div>
                            <div className="stat-value mono" style={{ color: "var(--brand-700)", fontSize: 18 }}>{hargaStr}</div>
                          </div>
                        )}
                      </div>
                      )
                    )}

                    {showStok && (
                      <div className="surface" style={{ overflow: "hidden" }}>
                        <div className="px-4 py-2.5" style={{ fontSize: 13, fontWeight: 600, borderBottom: "1px solid var(--ink-150)", display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8 }}>
                          <span>
                            Stok per Gudang {(stokLive || varian) && <span className="pill pill-success" style={{ marginLeft: 6 }}>Accurate</span>}
                            {varian && (
                              <span className="pill mono" style={{ marginLeft: 6, fontSize: 11 }}>
                                {varianAktif ? varianAktif.kode : "gabungan varian"}
                              </span>
                            )}
                          </span>
                          {!stokLive && !varian && !isBuyer && (accStock?.session_expired || accStock?.error) && (
                            <span style={{ fontSize: 11, fontWeight: 500, color: "var(--ink-400)" }} title="Fetch Accurate gagal — memakai data Excel (export Accurate)">fallback Excel</span>
                          )}
                        </div>
                        {barisTampil.length > 0 ? (
                          <>
                            {/* Mode gabungan bisa punya banyak kolom varian → biar
                                menggeser sendiri, jangan memaksa halaman melebar. */}
                            <div style={{ overflowX: "auto" }}>
                            <table className="tbl">
                              {varian && !varianAktif && (
                                <thead>
                                  <tr>
                                    <th>Gudang</th>
                                    {varian.map((v) => (
                                      <th key={v.kode} className="num mono" title={v.kode}>{labelVarian(v.kode)}</th>
                                    ))}
                                    <th className="num">Total</th>
                                  </tr>
                                </thead>
                              )}
                              <tbody>
                                {barisTampil.map(({ nama, qty, kolom }) => (
                                  <GudangRakRow
                                    key={nama}
                                    pn={main.part_number}
                                    gudang={nama}
                                    qty={qty}
                                    kolom={kolom}
                                    info={rakMap[nama]}
                                    boleh={isAdmin || kelola.includes(nama)}
                                    open={rakOpen === nama}
                                    onToggle={() => setRakOpen((g) => (g === nama ? null : nama))}
                                    onChanged={muatRak}
                                    onZoom={setLightbox}
                                  />
                                ))}
                              </tbody>
                              {varian && (
                                <tfoot>
                                  <tr>
                                    <td style={SEL_TOTAL}>Total</td>
                                    {!varianAktif && totalKolomVarian.map((t, i) => (
                                      <td key={i} className="num" style={SEL_TOTAL}>{angka(t)}</td>
                                    ))}
                                    <td className="num" style={SEL_TOTAL}>{angka(totalTampil)}</td>
                                  </tr>
                                </tfoot>
                              )}
                            </table>
                            </div>
                            <div className="px-4 py-2" style={{ fontSize: 11.5, color: "var(--ink-400)", borderTop: "1px solid var(--ink-150)" }}>
                              Klik baris gudang untuk melihat lokasi rak & kartu stok.
                              {varian && " Rak dicatat per gudang untuk part ini (berlaku semua varian)."}
                            </div>
                          </>
                        ) : (
                          <div className="px-4 py-3" style={{ fontSize: 13, color: "var(--ink-400)" }}>
                            Tidak ada rincian stok per gudang.
                          </div>
                        )}
                      </div>
                    )}

                    {/* Stok PEMASOK Weichai — diambil LIVE saat diminta (tombol).
                        Terpisah dari stok Accurate (beda makna: stok KITA vs
                        ketersediaan di PEMASOK untuk restok). Internal-only. */}
                    {showStok && <WeichaiStockCard pn={main.part_number} />}
                  </>
                )}

                {/* Spesifikasi fisik resmi (berat/dimensi) dari SIMS */}
                {spec && (
                  <div className="surface" style={{ overflow: "hidden" }}>
                    <div className="px-4 py-2.5 flex items-center gap-2" style={{ fontSize: 13, fontWeight: 600, borderBottom: "1px solid var(--ink-150)" }}>
                      Spesifikasi <span className="pill">sumber: SIMS</span>
                    </div>
                    <table className="tbl">
                      <tbody>
                        {spec.berat_kirim_kg != null && (
                          <tr>
                            <td style={{ color: "var(--ink-600)" }}>Berat (kirim)</td>
                            <td className="num" style={{ fontWeight: 550 }}>{spec.berat_kirim_kg} kg</td>
                          </tr>
                        )}
                        {spec.berat_bersih_kg != null && spec.berat_bersih_kg !== spec.berat_kirim_kg && (
                          <tr>
                            <td style={{ color: "var(--ink-600)" }}>Berat (bersih)</td>
                            <td className="num" style={{ fontWeight: 550 }}>{spec.berat_bersih_kg} kg</td>
                          </tr>
                        )}
                        {spec.dimensi_cm && (
                          <tr>
                            <td style={{ color: "var(--ink-600)" }}>Dimensi (P×L×T)</td>
                            <td className="num" style={{ fontWeight: 550 }}>{spec.dimensi_cm} cm</td>
                          </tr>
                        )}
                        {spec.satuan && (
                          <tr>
                            <td style={{ color: "var(--ink-600)" }}>Satuan</td>
                            <td className="num" style={{ fontWeight: 550 }}>{spec.satuan}</td>
                          </tr>
                        )}
                        {spec.kemasan_minimum != null && (
                          <tr>
                            <td style={{ color: "var(--ink-600)" }}>Kemasan minimum</td>
                            <td className="num" style={{ fontWeight: 550 }}>{spec.kemasan_minimum}</td>
                          </tr>
                        )}
                        {spec.merek && (
                          <tr>
                            <td style={{ color: "var(--ink-600)" }}>Merek</td>
                            <td className="num" style={{ fontWeight: 550 }}>{spec.merek}</td>
                          </tr>
                        )}
                      </tbody>
                    </table>
                  </div>
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

                {/* Cek kecocokan part di unit pembeli — tepat di bawah daftar unit,
                    supaya alurnya alami: "dipakai di unit-unit ini → cek unit SAYA". */}
                {isBuyer && <CekUnitCard pn={main.part_number} />}
              </section>
            </div>

            {/* Exploded view EPC tanpa nomor rangka — dimuat HANYA saat diminta */}
            <section className="surface surface-pad mt-5">
              <div className="mb-3 flex flex-wrap items-center gap-2">
                <h2 style={{ fontSize: 13, fontWeight: 600 }}>Exploded View</h2>
                <span className="pill">sumber: EPC</span>
                {exploded?.found && exploded.balon != null && (
                  <span className="pill">balon {exploded.balon}</span>
                )}
                {!exploded && (
                  <button
                    type="button"
                    className="btn btn-ghost ml-auto"
                    onClick={loadExploded}
                    disabled={explodedBusy}
                    style={{ fontSize: 12 }}
                  >
                    {explodedBusy ? "memuat… (1–2 menit)" : "Tampilkan exploded view"}
                  </button>
                )}
              </div>

              {!exploded && !explodedBusy && !explodedErr && (
                <div style={{ fontSize: 12, color: "var(--ink-500)", lineHeight: 1.6 }}>
                  Gambar rakitan resmi EPC yang memuat part ini, tanpa perlu nomor rangka.
                  Tidak dimuat otomatis karena pencarian pertamanya bisa memakan 1–2 menit
                  (terukur 94 detik untuk part yang dipakai belasan ribu model). Sesudah itu
                  tersimpan di server 24 jam, jadi pembukaan berikutnya seketika.
                </div>
              )}
              {explodedBusy && (
                <div className="img-ph" style={{ height: 200 }}>
                  mencari figure di EPC… (1–2 menit, jangan tutup halaman)
                </div>
              )}
              {explodedErr && (
                <div style={{ fontSize: 12, color: "var(--danger, #dc2626)" }}>{explodedErr}</div>
              )}
              {exploded && !exploded.found && (
                <div style={{ fontSize: 12, color: "var(--ink-500)", lineHeight: 1.6 }}>
                  {exploded.alasan || "Figure exploded view tidak ditemukan untuk part ini."}
                </div>
              )}
              {exploded?.found && exploded.png_base64 && (
                <>
                  <button
                    type="button"
                    onClick={() => setLightbox(`data:image/png;base64,${exploded.png_base64}`)}
                    className="w-full overflow-hidden"
                    style={{ cursor: "zoom-in", borderRadius: 8, border: "1px solid var(--ink-200)", background: "#fff" }}
                    title="Klik untuk perbesar"
                  >
                    {/* eslint-disable-next-line @next/next/no-img-element */}
                    <img
                      src={`data:image/png;base64,${exploded.png_base64}`}
                      alt={`Exploded view ${main.part_number}`}
                      className="w-full"
                      style={{ objectFit: "contain", maxHeight: 520 }}
                    />
                  </button>
                  <div className="mt-2" style={{ fontSize: 12, color: "var(--ink-600)", lineHeight: 1.6 }}>
                    {exploded.figure_nama && (
                      <div>
                        Figure: <b>{exploded.figure_nama}</b>
                        {exploded.figure_pn ? ` (${exploded.figure_pn})` : ""}
                        {exploded.jumlah_item ? ` · ${exploded.jumlah_item} part di gambar` : ""}
                      </div>
                    )}
                    {exploded.catatan && (
                      <div className="mt-1" style={{ color: "var(--ink-500)" }}>⚠️ {exploded.catatan}</div>
                    )}
                  </div>
                </>
              )}
            </section>
          </>
        )}
      </div>

      {lightbox && (
        <ImageLightbox
          src={partImageUrl(lightbox)}
          onClose={() => setLightbox(null)}
          alt="Foto part"
        />
      )}
    </AppShell>
  );
}

// ── Baris gudang + panel Rak & Kartu Stok ────────────────────────────────────
// Pola expandable meniru StokRow (app/stok/page.tsx): baris diklik → panel di
// bawahnya. MELIHAT terbuka untuk semua staf internal; tombol Ubah hanya muncul
// bila akun ini memang mengelola gudang tersebut (label PENUH, mis. "01.Jakarta"
// — bukan nama pendek pembeli).
function tglSingkat(iso: string): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString("id-ID", { day: "2-digit", month: "short", year: "numeric" });
}

function GudangRakRow({
  pn,
  gudang,
  qty,
  kolom,
  info,
  boleh,
  open,
  onToggle,
  onChanged,
  onZoom,
}: {
  pn: string;
  gudang: string;
  qty: number;
  // Mode KELUARGA VARIAN (tab "Semua"): qty per varian, urutannya sejajar
  // dengan header kolom. `qty` di atas = totalnya. Undefined = tabel 2 kolom
  // seperti sedia kala.
  kolom?: number[];
  info?: RakInfo;
  boleh: boolean;
  open: boolean;
  onToggle: () => void;
  onChanged: () => Promise<void> | void;
  onZoom: (url: string) => void;
}) {
  const [edit, setEdit] = useState(false);
  const [rak, setRak] = useState(info?.rak ?? "");
  const [catatan, setCatatan] = useState(info?.catatan ?? "");
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  // Pratinjau foto TERPILIH sebelum disimpan — di HP mudah salah pilih dari
  // galeri, dan yang salah unggah MENIMPA foto lama (tanpa riwayat).
  const [preview, setPreview] = useState<string | null>(null);
  useEffect(() => {
    if (!file) {
      setPreview(null);
      return;
    }
    const u = URL.createObjectURL(file);
    setPreview(u);
    return () => URL.revokeObjectURL(u);
  }, [file]);

  // Selama form tertutup, isian mengikuti data server (mis. sesudah disimpan
  // oleh baris lain / dimuat ulang). Saat form terbuka jangan diganggu.
  useEffect(() => {
    if (!edit) {
      setRak(info?.rak ?? "");
      setCatatan(info?.catatan ?? "");
    }
  }, [info?.rak, info?.catatan, edit]);

  async function simpan() {
    const token = getToken();
    if (!token) return;
    if (!rak.trim()) {
      setErr("Kode rak wajib diisi (mis. A-12, atau 'A-12 & C-03' bila terpecah).");
      return;
    }
    setBusy(true);
    setErr(null);
    try {
      await saveRak(token, gudang, pn, { rak: rak.trim(), catatan: catatan.trim() });
      // Foto diunggah SESUDAH baris ada — backend menolak foto untuk pasangan
      // (pn × gudang) yang belum punya baris rak.
      if (file) await uploadRakFoto(token, gudang, pn, file);
      setFile(null);
      setEdit(false);
      await onChanged();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Gagal menyimpan rak.");
    } finally {
      setBusy(false);
    }
  }

  async function hapusFoto() {
    const token = getToken();
    if (!token || !window.confirm(`Hapus foto kartu stok ${pn} di ${gudang}?`)) return;
    setBusy(true);
    setErr(null);
    try {
      await deleteRakFoto(token, gudang, pn);
      await onChanged();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Gagal menghapus foto.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <tr onClick={onToggle} style={{ cursor: "pointer" }} title="Klik untuk rak & kartu stok">
        <td style={{ color: "var(--ink-600)" }}>
          <span style={{ color: "var(--ink-400)", fontSize: 11, marginRight: 6 }}>{open ? "▾" : "▸"}</span>
          {gudang}
          {info?.rak && (
            <span className="pill" style={{ marginLeft: 6, fontSize: 11 }} title="Lokasi rak">
              rak {info.rak}
            </span>
          )}
          {qty <= 0 && info?.rak && (
            <span className="pill pill-warn" style={{ marginLeft: 6, fontSize: 11 }} title="Tidak ada stok, tapi rak-nya tercatat">
              stok 0
            </span>
          )}
        </td>
        {kolom?.map((q, i) => (
          // Sel 0 ditulis "—" redup, bukan angka 0 — gudang yang memang tak
          // pernah memegang varian itu tak perlu ikut ramai.
          <td key={i} className="num" style={q ? undefined : { color: "var(--ink-300)" }}>
            {q ? Number(q).toLocaleString("id-ID") : "—"}
          </td>
        ))}
        <td className="num" style={{ fontWeight: 550 }}>{Number(qty).toLocaleString("id-ID")}</td>
      </tr>
      {open && (
        <tr>
          <td colSpan={2 + (kolom?.length ?? 0)} style={{ background: "var(--ink-50)", padding: "10px 14px" }}>
            {err && <div className="alert alert-error" style={{ marginBottom: 8 }}>{err}</div>}

            {!edit ? (
              <div style={{ fontSize: 12.5, lineHeight: 1.7 }}>
                {info ? (
                  <>
                    <div>
                      Rak: <b className="mono">{info.rak || "—"}</b>
                    </div>
                    {info.catatan && (
                      <div style={{ color: "var(--ink-600)" }}>Catatan: {info.catatan}</div>
                    )}
                    {info.foto_url && (
                      <button
                        type="button"
                        onClick={() => onZoom(info.foto_url)}
                        style={{ display: "block", marginTop: 6, padding: 0, border: "1px solid var(--ink-200)", borderRadius: 8, overflow: "hidden", background: "var(--paper)", cursor: "zoom-in" }}
                        title="Klik untuk perbesar kartu stok"
                      >
                        {/* eslint-disable-next-line @next/next/no-img-element */}
                        <img
                          src={partImageUrl(info.foto_url)}
                          alt={`Kartu stok ${pn} di ${gudang}`}
                          loading="lazy"
                          style={{ width: 120, height: 120, objectFit: "cover", display: "block" }}
                        />
                      </button>
                    )}
                    {(info.updated_by || info.updated_at) && (
                      <div style={{ color: "var(--ink-400)", fontSize: 11.5, marginTop: 4 }}>
                        diperbarui {info.updated_by || "—"}
                        {info.updated_at ? ` · ${tglSingkat(info.updated_at)}` : ""}
                      </div>
                    )}
                  </>
                ) : (
                  <span style={{ color: "var(--ink-500)" }}>
                    Belum ada data rak untuk gudang ini.
                  </span>
                )}
                {boleh && (
                  <div className="flex flex-wrap gap-2" style={{ marginTop: 8 }}>
                    <button className="btn btn-secondary btn-sm" onClick={() => setEdit(true)}>
                      ✏️ {info ? "Ubah" : "Isi rak"}
                    </button>
                    {info?.foto_url && (
                      <button className="btn btn-secondary btn-sm" onClick={hapusFoto} disabled={busy}>
                        Hapus foto
                      </button>
                    )}
                  </div>
                )}
              </div>
            ) : (
              <div className="flex flex-col gap-2" style={{ fontSize: 12.5 }}>
                <label className="flex flex-col gap-1">
                  <span style={{ color: "var(--ink-500)" }}>Kode rak</span>
                  <input
                    className="input mono"
                    style={{ height: 34, maxWidth: 320 }}
                    value={rak}
                    onChange={(e) => setRak(e.target.value)}
                    onKeyDown={(e) => {
                      // Enter = simpan (<form> tidak sah di dalam <td> tabel induk).
                      if (e.key === "Enter" && !busy) {
                        e.preventDefault();
                        void simpan();
                      }
                    }}
                    placeholder="mis. A-12 (boleh 'A-12 & C-03')"
                    autoFocus
                  />
                </label>
                <label className="flex flex-col gap-1">
                  <span style={{ color: "var(--ink-500)" }}>Catatan (opsional)</span>
                  <input
                    className="input"
                    style={{ height: 34, maxWidth: 420 }}
                    value={catatan}
                    onChange={(e) => setCatatan(e.target.value)}
                    placeholder="mis. sisa 2 pcs di rak bawah"
                  />
                </label>
                <label className="flex flex-col gap-1">
                  <span style={{ color: "var(--ink-500)" }}>
                    Foto kartu stok (jpg/png/webp, maks 10 MB) — mengganti foto lama
                  </span>
                  <input
                    type="file"
                    accept="image/jpeg,image/png,image/webp"
                    onChange={(e) => setFile(e.target.files?.[0] ?? null)}
                    style={{ fontSize: 12 }}
                  />
                </label>
                {preview && (
                  <div className="flex items-center gap-2">
                    {/* eslint-disable-next-line @next/next/no-img-element */}
                    <img
                      src={preview}
                      alt="Pratinjau foto kartu stok"
                      style={{ width: 84, height: 84, objectFit: "cover", borderRadius: 8, border: "1px solid var(--ink-200)" }}
                    />
                    <button
                      type="button"
                      className="btn btn-ghost btn-sm"
                      onClick={() => setFile(null)}
                      title="Batal pakai foto ini"
                    >
                      ✕ Lepas foto
                    </button>
                  </div>
                )}
                <div className="flex gap-2">
                  <button className="btn btn-primary btn-sm" onClick={simpan} disabled={busy}>
                    {busy ? "Menyimpan…" : "Simpan"}
                  </button>
                  <button
                    className="btn btn-secondary btn-sm"
                    onClick={() => {
                      setEdit(false);
                      setFile(null);
                      setErr(null);
                    }}
                    disabled={busy}
                  >
                    Batal
                  </button>
                </div>
              </div>
            )}
          </td>
        </tr>
      )}
    </>
  );
}

// ── Cek kecocokan part di unit pembeli ───────────────────────────────────────
// Pembeli memasukkan nomor rangka unitnya → backend memverifikasi ke BOM per-VIN
// EPC (aturan: part per-unit SELALU dicek EPC, jangan menebak). Bila cocok:
// penjelasan ("ini Brake friction plate / kampas rem, di bagian …") + gambar
// exploded view dengan nomor balon part-nya.
const RANGKA_KEY = "maspart_rangka_v1";

function CekUnitCard({ pn }: { pn: string }) {
  const [rangka, setRangka] = useState("");
  const [busy, setBusy] = useState(false);
  const [res, setRes] = useState<CekUnitResult | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [imgUrl, setImgUrl] = useState<string | null>(null);
  const [zoom, setZoom] = useState(false);

  // Nomor rangka diingat antar-kunjungan — pembeli biasanya punya 1-2 unit.
  useEffect(() => {
    try {
      setRangka(localStorage.getItem(RANGKA_KEY) || "");
    } catch { /* ignore */ }
  }, []);

  // Ambil PNG exploded view (butuh auth → fetch blob → objectURL).
  useEffect(() => {
    const id = res?.image_id;
    const token = getToken();
    if (!id || !token) {
      setImgUrl(null);
      return;
    }
    let alive = true;
    let made: string | null = null;
    getPartExploded(token, id)
      .then((b) => {
        made = URL.createObjectURL(b);
        if (alive) setImgUrl(made);
      })
      .catch(() => { if (alive) setImgUrl(null); });
    return () => {
      alive = false;
      if (made) URL.revokeObjectURL(made);
    };
  }, [res?.image_id]);

  async function cek() {
    const token = getToken();
    if (!token) return;
    const r = rangka.trim().toUpperCase();
    if (r.length < 6) {
      setErr("Isi nomor rangka/VIN unit Anda (min. 6 karakter).");
      return;
    }
    setBusy(true);
    setErr(null);
    setRes(null);
    try {
      const out = await cekPartDiUnit(token, pn, r);
      setRes(out);
      if (out.checked) {
        try { localStorage.setItem(RANGKA_KEY, r); } catch { /* ignore */ }
      }
      if (!out.checked && out.error) setErr(out.error);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Gagal mengecek ke EPC. Coba lagi.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div style={{ marginTop: 16, paddingTop: 14, borderTop: "1px solid var(--ink-150)" }}>
      <div className="mb-2 flex items-center gap-2">
        <h2 style={{ fontSize: 13, fontWeight: 600 }}>🔍 Cocok di unit saya?</h2>
        <span className="pill">sumber: EPC resmi per-VIN</span>
      </div>
      <div style={{ fontSize: 12.5, color: "var(--ink-500)", marginBottom: 10 }}>
        Masukkan <b>nomor rangka (VIN)</b> unit Anda — sistem mengecek langsung ke katalog
        EPC apakah part ini memang terpasang di unit tersebut.
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <input
          className="input mono"
          style={{ height: 36, minWidth: 260, flex: "1 1 260px", maxWidth: 420 }}
          placeholder="mis. LZZ5DMSD5RT108966"
          value={rangka}
          onChange={(e) => setRangka(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter" && !busy) void cek(); }}
        />
        <button className="btn btn-primary btn-sm" style={{ height: 36 }} disabled={busy} onClick={() => void cek()}>
          {busy ? "Mengecek ke EPC…" : "Cek di Unit Saya"}
        </button>
      </div>
      {busy && (
        <div style={{ fontSize: 12, color: "var(--ink-400)", marginTop: 8 }}>
          Mengecek menyeluruh ke katalog EPC unit ini — biasanya beberapa detik;
          menyiapkan gambar exploded view bisa sedikit lebih lama…
        </div>
      )}
      {err && <div className="alert alert-error" style={{ marginTop: 10 }}>{err}</div>}

      {res?.checked && res.cocok && (
        <div style={{ marginTop: 12 }}>
          <div
            className="rounded-lg px-3 py-2.5"
            style={{ background: "var(--brand-50, #eef8f0)", border: "1px solid var(--brand-600)", fontSize: 13, lineHeight: 1.55 }}
          >
            {res.penjelasan}
          </div>
          {imgUrl && (
            <button
              type="button"
              onClick={() => setZoom(true)}
              style={{ display: "block", width: "100%", marginTop: 10, border: "1px solid var(--ink-150)", borderRadius: 10, overflow: "hidden", cursor: "zoom-in", background: "#fff" }}
              title="Klik untuk perbesar"
            >
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img src={imgUrl} alt={`Exploded view ${pn}`} style={{ width: "100%", display: "block" }} />
              <div style={{ fontSize: 11.5, color: "var(--ink-500)", padding: "6px 10px", textAlign: "left" }}>
                Exploded view: {res.lokasi}{res.balon ? ` — part ini nomor balon ${res.balon} (disorot)` : ""} · klik untuk perbesar
              </div>
            </button>
          )}
          {zoom && imgUrl && (
            <ImageLightbox src={imgUrl} onClose={() => setZoom(false)} alt={`Exploded view ${pn}`} />
          )}
        </div>
      )}
      {res?.checked && res.cocok === false && (
        <div className="alert alert-error" style={{ marginTop: 12 }}>
          ❌ {res.pesan}
        </div>
      )}
    </div>
  );
}

// ── Stok PEMASOK Weichai (portal tci-pnp) ────────────────────────────────────
// Diambil LIVE saat staf menekan tombol — BUKAN tiap buka halaman (portal lambat
// ~5-8 dtk & di balik Cloudflare). Beda makna dari stok Accurate: ini
// ketersediaan di PEMASOK untuk restok, bukan stok yang kita pegang. Portal tak
// memberi harga → STOK saja. Non-fatal; internal-only (server memblokir pembeli).
function WeichaiStockCard({ pn }: { pn: string }) {
  const [data, setData] = useState<WeichaiStock | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  // PN berganti (navigasi antar-part) → reset, jangan tampilkan hasil part lama.
  useEffect(() => {
    setData(null);
    setErr(null);
    setBusy(false);
  }, [pn]);

  async function cek() {
    const token = getToken();
    if (!token || busy) return;
    setBusy(true);
    setErr(null);
    try {
      setData(await getWeichaiStock(pn, token));
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Gagal menghubungi portal Weichai.");
    } finally {
      setBusy(false);
    }
  }

  const stock = data?.found ? data.stock : null;

  return (
    <div className="surface" style={{ overflow: "hidden" }}>
      <div
        className="px-4 py-2.5 flex items-center gap-2"
        style={{ fontSize: 13, fontWeight: 600, borderBottom: "1px solid var(--ink-150)" }}
      >
        <span>Stok Pemasok</span>
        <span className="pill" style={{ background: "var(--brand-50, #eef4ff)", color: "#1d4ed8", borderColor: "#bfdbfe" }}>
          Weichai
        </span>
        {!data && !busy && (
          <button className="btn btn-ghost ml-auto" style={{ fontSize: 12 }} onClick={cek}>
            Cek stok Weichai
          </button>
        )}
        {data && !busy && (
          <button className="btn btn-ghost ml-auto" style={{ fontSize: 12 }} onClick={cek} title="Ambil ulang (live)">
            ↻ Perbarui
          </button>
        )}
      </div>

      <div className="px-4 py-3" style={{ fontSize: 12.5, color: "var(--ink-500)" }}>
        {!data && !busy && !err && (
          <>Ketersediaan di pemasok Weichai untuk restok — diambil langsung dari portal saat diminta (±5–8 dtk). Tanpa harga.</>
        )}
        {busy && <span>Mengecek ke portal Weichai…</span>}
        {err && <span style={{ color: "var(--danger, #dc2626)" }}>{err}</span>}
        {data && !busy && data.configured === false && (
          <span>Portal Weichai belum dikonfigurasi di server.</span>
        )}
        {data && !busy && data.error && (
          <span style={{ color: "var(--danger, #dc2626)" }}>Gagal login/koneksi ke portal Weichai. Coba lagi.</span>
        )}
        {data && !busy && data.configured !== false && !data.error && !stock && (
          <span>Part ini <b>tidak tersedia</b> di Weichai.</span>
        )}
      </div>

      {stock && !busy && (
        <>
          <div className="px-4 pb-1 flex items-baseline gap-2">
            <div className="stat-value" style={{ fontSize: 20 }}>
              {stock.total.toLocaleString("id-ID")} {stock.satuan}
            </div>
            <span style={{ fontSize: 12, color: "var(--ink-500)" }}>tersedia di pemasok</span>
          </div>
          {stock.per_cabang.length > 0 ? (
            <table className="tbl">
              <tbody>
                {stock.per_cabang.map((b) => (
                  <tr key={b.cabang}>
                    <td style={{ color: "var(--ink-600)" }}>{b.cabang}</td>
                    <td className="num" style={{ fontWeight: 550 }}>
                      {b.qty.toLocaleString("id-ID")} {b.satuan}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <div className="px-4 pb-3" style={{ fontSize: 12, color: "var(--ink-400)" }}>
              Rincian per-cabang tak tersedia.
            </div>
          )}
          <div className="px-4 py-2" style={{ fontSize: 11.5, color: "var(--ink-400)", borderTop: "1px solid var(--ink-150)" }}>
            Sumber: portal Weichai (tci-pnp) · barcode <span className="mono">{stock.barcode}</span> · stok pemasok, bukan stok kita.
          </div>
        </>
      )}
    </div>
  );
}
