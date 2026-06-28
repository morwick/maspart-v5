"use client";

import { useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import AppShell from "@/components/AppShell";
import {
  ApiError,
  searchParts,
  type PartResult,
  type SearchMode,
} from "@/lib/api";
import { clearSession, getToken, getUser } from "@/lib/auth";
import { ensurePerms } from "@/lib/perms";
import { addToCart, hasPrice, hasWeight } from "@/lib/cart";

const PAGE_SIZES = [20, 50, 100];
const FETCH_SIZE = 200;       // ukuran per request saat memuat semua hasil
const MAX_FETCH = 2000;       // batas aman jumlah hasil yang dimuat untuk saring live
// Simpan & pulihkan state pencarian saat bolak-balik ke halaman detail part.
const SS_KEY = "maspart_part_search";
const RETURN_KEY = "maspart_part_search_return";

export default function SearchPage() {
  const router = useRouter();
  const [mode, setMode] = useState<SearchMode>("pn");
  const [q, setQ] = useState("");

  // Semua hasil dari query aktif (dimuat sekaligus → saring & paginasi di klien).
  const [allResults, setAllResults] = useState<PartResult[]>([]);
  const [totalCount, setTotalCount] = useState(0);
  const [truncated, setTruncated] = useState(false);
  const [searched, setSearched] = useState(false);

  // Saring live di atas hasil.
  const [refine, setRefine] = useState("");

  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);

  const [activeQuery, setActiveQuery] = useState("");
  const [activeMode, setActiveMode] = useState<SearchMode>("pn");

  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [showStok, setShowStok] = useState(true);
  const [showHarga, setShowHarga] = useState(true);
  const [isBuyer, setIsBuyer] = useState(false);

  type SortKey = "part_number" | "part_name" | "file" | "stok" | "harga";
  type SortDir = "asc" | "desc";
  const [sortKey, setSortKey] = useState<SortKey | null>(null);
  const [sortDir, setSortDir] = useState<SortDir>("asc");

  function handleSort(key: SortKey) {
    if (sortKey === key) {
      setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    } else {
      setSortKey(key);
      setSortDir("asc");
    }
    setPage(1);
  }

  useEffect(() => {
    if (!getToken()) {
      router.replace("/login");
      return;
    }
    // Pembeli: kolom stok tidak ditampilkan di hasil pencarian (hanya di detail part).
    const buyer = getUser()?.role === "pembeli";
    setIsBuyer(buyer);
    ensurePerms().then((p) => {
      if (p) {
        setShowStok(buyer ? false : p.columns.includes("col_stok"));
        setShowHarga(p.columns.includes("col_harga"));
      }
    });
    if (buyer) setShowStok(false);
  }, [router]);

  // Pulihkan state pencarian saat kembali dari halaman detail part.
  // Idempoten: konsumsi RETURN_KEY hanya di dalam blok `returning`, jangan menghapus
  // SS_KEY (effect bisa jalan 2× di StrictMode → agar tak ikut menghapus data).
  useEffect(() => {
    try {
      if (sessionStorage.getItem(RETURN_KEY) !== "1") return;
      sessionStorage.removeItem(RETURN_KEY);
      const raw = sessionStorage.getItem(SS_KEY);
      if (!raw) return;
      const s = JSON.parse(raw) as {
        query: string; mode: SearchMode; results: PartResult[];
        totalCount: number; truncated: boolean; page: number; pageSize: number; refine: string;
      };
      if (!s.results?.length) return;
      setMode(s.mode);
      setQ(s.query);
      setActiveQuery(s.query);
      setActiveMode(s.mode);
      setAllResults(s.results);
      setTotalCount(s.totalCount ?? s.results.length);
      setTruncated(!!s.truncated);
      setPageSize(s.pageSize ?? 20);
      setRefine(s.refine ?? "");
      setPage(s.page ?? 1);
      setSearched(true);
    } catch {
      /* ignore */
    }
  }, []);

  async function runSearch(term: string, searchMode: SearchMode) {
    const token = getToken();
    if (!token) return router.replace("/login");
    setError(null);
    setLoading(true);
    try {
      // Muat halaman pertama, lalu sisanya (sampai batas) agar saring live menyentuh semua hasil.
      const first = await searchParts(term, token, searchMode, 1, FETCH_SIZE);
      let acc = first.results;
      const maxPages = Math.min(first.total_pages, Math.ceil(MAX_FETCH / FETCH_SIZE));
      for (let p = 2; p <= maxPages; p++) {
        const r = await searchParts(term, token, searchMode, p, FETCH_SIZE);
        acc = acc.concat(r.results);
      }
      setAllResults(acc);
      setTotalCount(first.count);
      setTruncated(first.count > acc.length);
      setActiveQuery(term);
      setActiveMode(searchMode);
      setRefine("");
      setPage(1);
      setSearched(true);
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        clearSession();
        return router.replace("/login");
      }
      setError(err instanceof Error ? err.message : "Gagal mencari");
      setAllResults([]);
      setTotalCount(0);
      setSearched(true);
    } finally {
      setLoading(false);
    }
  }

  function handleSearch(e: React.FormEvent) {
    e.preventDefault();
    const term = q.trim();
    if (!term) return;
    runSearch(term, mode);
  }

  // Saring live: setiap kata harus muncul di gabungan PN + Nama + Unit/File.
  const refined = useMemo(() => {
    const t = refine.trim().toLowerCase();
    let filtered = allResults;
    if (t) {
      const words = t.split(/\s+/);
      filtered = allResults.filter((r) => {
        const hay = `${r.part_number} ${r.part_name} ${r.file}`.toLowerCase();
        return words.every((w) => hay.includes(w));
      });
    }
    if (!sortKey) return filtered;
    return [...filtered].sort((a, b) => {
      let va: string | number = a[sortKey] ?? "";
      let vb: string | number = b[sortKey] ?? "";
      // Stok & harga: coba parse angka, "—" dianggap -1
      if (sortKey === "stok" || sortKey === "harga") {
        const parse = (v: string | number) => {
          const n = parseFloat(String(v).replace(/[^0-9.-]/g, ""));
          return isNaN(n) ? -1 : n;
        };
        va = parse(va);
        vb = parse(vb);
        return sortDir === "asc" ? (va as number) - (vb as number) : (vb as number) - (va as number);
      }
      va = String(va).toLowerCase();
      vb = String(vb).toLowerCase();
      return sortDir === "asc" ? va.localeCompare(vb) : vb.localeCompare(va);
    });
  }, [allResults, refine, sortKey, sortDir]);

  const totalPages = Math.max(1, Math.ceil(refined.length / pageSize));
  const pageClamped = Math.min(page, totalPages);
  const pageItems = useMemo(
    () => refined.slice((pageClamped - 1) * pageSize, pageClamped * pageSize),
    [refined, pageClamped, pageSize],
  );

  function goToPage(p: number) {
    if (p < 1 || p > totalPages) return;
    setPage(p);
  }
  function changePageSize(size: number) {
    setPageSize(size);
    setPage(1);
  }

  const shown = refined.length;
  const from = shown ? (pageClamped - 1) * pageSize + 1 : 0;
  const to = shown ? Math.min(pageClamped * pageSize, shown) : 0;

  // Sebelum membuka detail: simpan state + tandai agar dipulihkan saat kembali.
  function openDetail(partNumber: string) {
    try {
      sessionStorage.setItem(RETURN_KEY, "1");
      sessionStorage.setItem(
        SS_KEY,
        JSON.stringify({
          query: activeQuery,
          mode: activeMode,
          results: allResults,
          totalCount,
          truncated,
          page: pageClamped,
          pageSize,
          refine,
        }),
      );
    } catch {
      /* abaikan bila kuota penuh — sekadar tidak dipulihkan */
    }
    router.push(`/part/${encodeURIComponent(partNumber)}`);
  }

  return (
    <AppShell
      active="/search"
      title="Search Part"
      sub="Cari berdasarkan kode part (Part Number) atau nama part."
    >
      <div className="mx-auto max-w-6xl px-4 py-5 sm:px-6">
        {/* Search card */}
        <div className="surface" style={{ padding: 16 }}>
          <div className="tabs" style={{ marginBottom: 14 }}>
            {(["pn", "name"] as SearchMode[]).map((m) => (
              <button key={m} className={"tab" + (mode === m ? " active" : "")} onClick={() => setMode(m)}>
                {m === "pn" ? "Part Number" : "Part Name"}
              </button>
            ))}
          </div>
          <form onSubmit={handleSearch} className="flex gap-2.5">
            <input
              className={"input grow" + (mode === "pn" ? " mono" : "")}
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder={mode === "pn" ? "Contoh: 16Y-15-00010" : "Contoh: oil filter"}
              style={{ height: 44, fontSize: 14 }}
            />
            <button className="btn btn-primary btn-lg" disabled={loading}>
              {loading ? "Mencari…" : "Cari"}
            </button>
          </form>
        </div>

        {error && <div className="alert alert-error" style={{ marginTop: 16 }}>{error}</div>}

        {searched && !error && (
          <div className="surface" style={{ marginTop: 16, overflow: "hidden" }}>
            {/* Saring live di atas hasil */}
            {allResults.length > 0 && (
              <div
                className="flex items-center gap-2 px-5 py-3"
                style={{ borderBottom: "1px solid var(--ink-150)" }}
              >
                <span style={{ fontSize: 16, color: "var(--ink-400)" }}>🔎</span>
                <input
                  className="input grow"
                  value={refine}
                  onChange={(e) => {
                    setRefine(e.target.value);
                    setPage(1);
                  }}
                  placeholder={`Saring hasil "${activeQuery}" secara langsung… (mis. sg21)`}
                  style={{ height: 38, fontSize: 13.5 }}
                />
                {refine && (
                  <button className="btn btn-ghost btn-sm" onClick={() => setRefine("")} title="Hapus saringan">
                    ✕
                  </button>
                )}
              </div>
            )}

            {/* stat / meta row */}
            <div
              className="flex flex-wrap items-center gap-x-6 gap-y-2 px-5 py-3.5"
              style={{ borderBottom: "1px solid var(--ink-150)" }}
            >
              <div>
                <div className="stat-label">{refine.trim() ? "Hasil saring" : "Hasil"}</div>
                <div className="stat-value">{shown.toLocaleString("id-ID")}</div>
              </div>
              <div style={{ fontSize: 12.5, color: "var(--ink-500)" }}>
                {shown > 0
                  ? `Menampilkan ${from}–${to}${refine.trim() ? ` dari ${shown.toLocaleString("id-ID")} hasil saring` : ""} · klik baris untuk detail`
                  : refine.trim()
                    ? `Tidak ada hasil cocok dengan saringan "${refine.trim()}"`
                    : "Tidak ada part yang cocok"}
                {truncated && !refine.trim() && (
                  <span style={{ color: "var(--warn-600)" }}>
                    {` · memuat ${allResults.length.toLocaleString("id-ID")} dari ${totalCount.toLocaleString("id-ID")} — persempit kata kunci utama`}
                  </span>
                )}
              </div>
              <div className="grow" />
              {shown > 0 && (
                <label className="flex items-center gap-2" style={{ fontSize: 12.5, color: "var(--ink-500)" }}>
                  Per halaman
                  <select
                    className="select"
                    style={{ width: 78, height: 32 }}
                    value={pageSize}
                    onChange={(e) => changePageSize(Number(e.target.value))}
                  >
                    {PAGE_SIZES.map((s) => (
                      <option key={s} value={s}>{s}</option>
                    ))}
                  </select>
                </label>
              )}
            </div>

            {pageItems.length > 0 && (
              <div style={{ overflow: "auto" }}>
                <table className="tbl">
                  <thead>
                    <tr>
                      {(
                        [
                          { key: "part_number", label: "Part Number" },
                          { key: "part_name",   label: "Nama Part" },
                          { key: "file",        label: "Unit / File" },
                        ] as { key: SortKey; label: string }[]
                      ).map(({ key, label }) => (
                        <th
                          key={key}
                          style={{ cursor: "pointer", userSelect: "none", whiteSpace: "nowrap" }}
                          onClick={() => handleSort(key)}
                        >
                          {label}{" "}
                          <span style={{ opacity: sortKey === key ? 1 : 0.3, fontSize: 11 }}>
                            {sortKey === key ? (sortDir === "asc" ? "▲" : "▼") : "▲▼"}
                          </span>
                        </th>
                      ))}
                      {showStok && (
                        <th
                          className="num"
                          style={{ cursor: "pointer", userSelect: "none", whiteSpace: "nowrap" }}
                          onClick={() => handleSort("stok")}
                        >
                          Stok{" "}
                          <span style={{ opacity: sortKey === "stok" ? 1 : 0.3, fontSize: 11 }}>
                            {sortKey === "stok" ? (sortDir === "asc" ? "▲" : "▼") : "▲▼"}
                          </span>
                        </th>
                      )}
                      {showHarga && (
                        <th
                          className="num"
                          style={{ cursor: "pointer", userSelect: "none", whiteSpace: "nowrap" }}
                          onClick={() => handleSort("harga")}
                        >
                          Harga{" "}
                          <span style={{ opacity: sortKey === "harga" ? 1 : 0.3, fontSize: 11 }}>
                            {sortKey === "harga" ? (sortDir === "asc" ? "▲" : "▼") : "▲▼"}
                          </span>
                        </th>
                      )}
                      {isBuyer && <th style={{ width: 44 }} />}
                    </tr>
                  </thead>
                  <tbody>
                    {pageItems.map((r, i) => (
                      <tr
                        key={`${r.part_number}-${i}`}
                        style={{ cursor: "pointer" }}
                        onClick={() => openDetail(r.part_number)}
                      >
                        <td className="pn">{r.part_number}</td>
                        <td style={{ fontWeight: 500 }}>
                          {r.part_name}
                          {r.source === "sims" && (
                            <span
                              className="pill pill-info"
                              style={{ marginLeft: 8, fontWeight: 600 }}
                              title="Tidak ada di database lokal — nama diambil dari SIMS"
                            >
                              SIMS
                            </span>
                          )}
                        </td>
                        <td style={{ color: "var(--ink-600)" }}>{r.file}</td>
                        {showStok && (
                          <td className="num">
                            {r.stok === "—" ? (
                              <span style={{ color: "var(--ink-400)" }}>—</span>
                            ) : (
                              <span style={{ fontWeight: 550, fontVariantNumeric: "tabular-nums" }}>{r.stok}</span>
                            )}
                          </td>
                        )}
                        {showHarga && <td className="num mono">{r.harga}</td>}
                        {isBuyer && (
                          <td>
                            {Object.values(r.gudang || {}).reduce((n, q) => n + (Number(q) || 0), 0) <= 0 ? (
                              <span className="pill pill-danger" title="Stok habis di lokasimu">Habis</span>
                            ) : !hasPrice(r.harga) ? (
                              <span className="pill pill-warn" title="Harga belum tersedia — belum bisa dibeli">Tanpa harga</span>
                            ) : !hasWeight(r.berat) ? (
                              <span className="pill pill-warn" title="Berat belum ditetapkan admin — belum bisa dibeli">Tanpa berat</span>
                            ) : (
                              <button
                                className="btn btn-secondary btn-sm"
                                title="Tambah ke keranjang"
                                onClick={(e) => {
                                  e.stopPropagation();
                                  addToCart({ part_number: r.part_number, name: r.part_name, harga: r.harga, berat: r.berat });
                                }}
                              >
                                +🛒
                              </button>
                            )}
                          </td>
                        )}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}

            {shown > 0 && totalPages > 1 && (
              <div
                className="flex items-center justify-center gap-2 px-5 py-3"
                style={{ borderTop: "1px solid var(--ink-150)", fontSize: 13 }}
              >
                <button className="btn btn-secondary btn-sm" onClick={() => goToPage(pageClamped - 1)} disabled={pageClamped <= 1}>
                  ← Sebelumnya
                </button>
                <span style={{ color: "var(--ink-500)", padding: "0 8px" }}>
                  Halaman {pageClamped} / {totalPages}
                </span>
                <button className="btn btn-secondary btn-sm" onClick={() => goToPage(pageClamped + 1)} disabled={pageClamped >= totalPages}>
                  Berikutnya →
                </button>
              </div>
            )}
          </div>
        )}

        {!searched && !error && (
          <div
            className="surface grid place-items-center"
            style={{ marginTop: 16, height: 220, color: "var(--ink-500)", fontSize: 13.5 }}
          >
            Ketik kata kunci lalu tekan <span className="kbd" style={{ margin: "0 6px" }}>Enter</span> untuk mencari.
          </div>
        )}
      </div>
    </AppShell>
  );
}