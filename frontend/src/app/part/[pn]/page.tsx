"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import AppShell from "@/components/AppShell";
import ImageLightbox from "@/components/ImageLightbox";
import { ApiError, cekPartDiUnit, getAccurateStock, getPartExploded, getPartPhotos, getPartSpec, getBuyerLocations, partImageUrl, searchParts, type AccurateStock, type BuyerLocation, type CekUnitResult, type PartResult, type PartSpec } from "@/lib/api";
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
    ensurePerms().then((p) => {
      if (p) {
        setShowStok(admin || p.columns.includes("col_stok"));
        setShowHarga(admin || p.columns.includes("col_harga"));
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
                      <div className="stat-label">Harga {hargaLive && <span className="pill pill-success" style={{ marginLeft: 6 }}>Accurate</span>}</div>
                      <div className="stat-value mono" style={{ color: "var(--brand-700)", fontSize: 18 }}>{hargaStr}</div>
                    </div>
                  </div>
                ) : (
                  <>
                    {(showStok || showHarga) && (
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
                    )}

                    {showStok && (
                      <div className="surface" style={{ overflow: "hidden" }}>
                        <div className="px-4 py-2.5" style={{ fontSize: 13, fontWeight: 600, borderBottom: "1px solid var(--ink-150)", display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8 }}>
                          <span>Stok per Gudang {stokLive && <span className="pill pill-success" style={{ marginLeft: 6 }}>Accurate</span>}</span>
                          {!stokLive && !isBuyer && (accStock?.session_expired || accStock?.error) && (
                            <span style={{ fontSize: 11, fontWeight: 500, color: "var(--ink-400)" }} title="Fetch Accurate gagal — memakai data Excel (export Accurate)">fallback Excel</span>
                          )}
                        </div>
                        {displayGudang.length > 0 ? (
                          <table className="tbl">
                            <tbody>
                              {displayGudang.map(([nama, qty]) => (
                                <tr key={nama}>
                                  <td style={{ color: "var(--ink-600)" }}>{nama}</td>
                                  <td className="num" style={{ fontWeight: 550 }}>{Number(qty).toLocaleString("id-ID")}</td>
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
              </section>
            </div>

            {/* Cek kecocokan part di unit pembeli (per nomor rangka, sumber EPC) */}
            {isBuyer && <CekUnitCard pn={main.part_number} />}
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
    <section className="surface surface-pad" style={{ marginTop: 20 }}>
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
          Mengecek menyeluruh ke EPC (Loading List + seluruh kategori Parts Atlas) —
          pengecekan pertama untuk sebuah unit bisa ±30 detik sampai 2 menit…
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
    </section>
  );
}
