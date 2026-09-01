"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import AppShell from "@/components/AppShell";
import {
  ApiError,
  downloadBlob,
  exportStokList,
  getAccurateStock,
  getStokList,
  type AccurateStock,
  type StokListResponse,
} from "@/lib/api";
import { clearSession, getToken } from "@/lib/auth";
import { EmptyIcon, EmptyState, TableSkeleton } from "@/components/States";

const PAGE_SIZE = 50;

export default function StokPage() {
  const router = useRouter();
  const [data, setData] = useState<StokListResponse | null>(null);
  const [qInput, setQInput] = useState("");
  const [q, setQ] = useState("");
  const [sort, setSort] = useState("pn");
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Rincian per gudang/cabang: dimuat ON-DEMAND saat baris diklik. Backend TIDAK
  // menembak Accurate live per-PN — /api/parts/accurate-stock membaca INDEKS
  // bersama (agregat + rincian per-gudang hasil enrichment) yang ditarik 3× sehari
  // pada jam WIB tetap 07/12/19 lalu dipersist ke disk. On-demand di sini murni
  // agar daftar tak mengirim ratusan rincian sekaligus, bukan karena mahal.
  const [openPn, setOpenPn] = useState<string | null>(null);
  const [details, setDetails] = useState<Record<string, AccurateStock | "loading">>({});

  const on401 = useCallback(
    (err: unknown): boolean => {
      if (err instanceof ApiError && err.status === 401) {
        clearSession();
        router.replace("/login");
        return true;
      }
      return false;
    },
    [router],
  );

  const load = useCallback(
    async (p: number, keyword: string, srt: string) => {
      const token = getToken();
      if (!token) return router.replace("/login");
      setLoading(true);
      setError(null);
      try {
        const res = await getStokList(token, { q: keyword, sort: srt, page: p, pageSize: PAGE_SIZE });
        setData(res);
        setPage(res.page);
        setOpenPn(null);
      } catch (err) {
        if (!on401(err)) setError(err instanceof Error ? err.message : "Gagal memuat");
      } finally {
        setLoading(false);
      }
    },
    [router, on401],
  );

  useEffect(() => {
    if (!getToken()) {
      router.replace("/login");
      return;
    }
    load(1, "", "pn");
  }, [router, load]);

  async function handleExport() {
    const token = getToken();
    if (!token) return;
    try {
      const blob = await exportStokList(token, { q, sort });
      downloadBlob(blob, "stok_accurate.xlsx");
    } catch (err) {
      if (!on401(err)) setError(err instanceof Error ? err.message : "Gagal export");
    }
  }

  function toggleDetail(pn: string) {
    if (!pn) return;
    if (openPn === pn) {
      setOpenPn(null);
      return;
    }
    setOpenPn(pn);
    if (details[pn] && details[pn] !== "loading") return; // sudah pernah dimuat
    const token = getToken();
    if (!token) return;
    setDetails((d) => ({ ...d, [pn]: "loading" }));
    getAccurateStock(pn, token)
      .then((res) => setDetails((d) => ({ ...d, [pn]: res })))
      .catch((err) => {
        if (!on401(err)) setDetails((d) => ({ ...d, [pn]: { configured: true, error: true } }));
      });
  }

  const notConfigured = data && data.configured === false;
  const sessionExpired = data && data.session_expired;
  const fetchError = data && data.error;

  return (
    <AppShell active="/stok" title="Stok" sub="Stok seluruh barang dari Accurate (sinkron 3× sehari: 07.00, 12.00, 19.00 WIB)">
      <div className="mx-auto w-full max-w-5xl px-4 py-6 sm:px-7">
        <div className="flex flex-wrap gap-2">
          <form
            onSubmit={(e) => {
              e.preventDefault();
              setQ(qInput);
              load(1, qInput, sort);
            }}
            className="flex flex-1 gap-2"
            style={{ minWidth: 240 }}
          >
            <input
              className="input mono"
              value={qInput}
              onChange={(e) => setQInput(e.target.value)}
              placeholder="Cari Part Number / Part Name…"
            />
            <button className="btn btn-primary">Cari</button>
          </form>
          <select
            className="select"
            style={{ width: "auto", minWidth: 150 }}
            value={sort}
            onChange={(e) => {
              setSort(e.target.value);
              load(1, q, e.target.value);
            }}
          >
            <option value="pn">Urut: Part Number</option>
            <option value="name">Urut: Part Name</option>
            <option value="stok_desc">Stok ↓</option>
            <option value="stok_asc">Stok ↑</option>
          </select>
        </div>

        {error && <div className="alert alert-error" style={{ marginTop: 12 }}>{error}</div>}

        {notConfigured && (
          <div className="alert" style={{ marginTop: 16, background: "var(--warn-50)", color: "var(--warn-600)", borderColor: "#f6d9a8" }}>
            Sesi Accurate belum tersedia. Hubungi admin untuk mengaktifkan koneksi Accurate.
          </div>
        )}
        {sessionExpired && (
          <div className="alert" style={{ marginTop: 16, background: "var(--warn-50)", color: "var(--warn-600)", borderColor: "#f6d9a8" }}>
            Sesi Accurate kadaluarsa. Coba lagi beberapa saat, atau minta admin memperbarui sesi.
          </div>
        )}
        {fetchError && (
          <div className="alert alert-error" style={{ marginTop: 16 }}>
            Gagal mengambil stok dari Accurate. Coba lagi beberapa saat.
          </div>
        )}

        {data && data.configured !== false && !sessionExpired && !fetchError && (
          <div className="flex flex-wrap items-center justify-between gap-2" style={{ marginTop: 12, fontSize: 12.5, color: "var(--ink-500)" }}>
            <span>
              Total <b className="mono" style={{ color: "var(--ink-800)" }}>{data.total.toLocaleString("id-ID")}</b> · Hasil filter{" "}
              <b className="mono" style={{ color: "var(--ink-800)" }}>{data.total_filtered.toLocaleString("id-ID")}</b>
            </span>
            <button className="btn btn-secondary btn-sm" onClick={handleExport} disabled={data.total_filtered === 0}>⬇ Export Excel</button>
          </div>
        )}

        {/* Memuat: rangka tabel setinggi baris asli, bukan layar kosong. */}
        {loading && !data && !error && !notConfigured && (
          <div className="surface" style={{ marginTop: 12 }}>
            <TableSkeleton rows={10} cols={4} />
          </div>
        )}

        {data && data.rows.length > 0 && (
          <div className="surface" style={{ marginTop: 12, overflow: "auto" }}>
            <table className="tbl">
              <thead>
                <tr>
                  <th style={{ width: 28 }} aria-label="Rincian" />
                  <th>Part Number</th>
                  <th>Part Name</th>
                  <th className="num">Stok</th>
                  <th>Satuan</th>
                </tr>
              </thead>
              <tbody>
                {data.rows.map((r, i) => {
                  const pn = r["Part Number"];
                  const open = openPn === pn;
                  const det = details[pn];
                  return (
                    <StokRow
                      key={`${pn}-${i}`}
                      row={r}
                      open={open}
                      detail={open ? det : undefined}
                      onToggle={() => toggleDetail(pn)}
                    />
                  );
                })}
              </tbody>
            </table>
          </div>
        )}

        {data && data.configured !== false && !sessionExpired && !fetchError && data.rows.length === 0 && (
          <div className="surface" style={{ marginTop: 12 }}>
            <EmptyState
              icon={EmptyIcon.box}
              title="Tidak ada barang yang cocok"
              sub={
                q
                  ? <>Tidak ada part yang mengandung <b>{q}</b> di indeks Accurate.</>
                  : "Indeks Accurate tidak mengembalikan baris apa pun untuk saringan ini."
              }
              action={
                q ? (
                  <button
                    className="btn btn-secondary btn-sm"
                    onClick={() => {
                      setQInput("");
                      setQ("");
                      load(1, "", sort);
                    }}
                  >
                    Tampilkan semua
                  </button>
                ) : null
              }
            />
          </div>
        )}

        {data && data.rows.length > 0 && (
          <p style={{ marginTop: 8, fontSize: 12, color: "var(--ink-400)" }}>
            Klik baris untuk melihat rincian stok per gudang/cabang (diambil langsung dari Accurate).
          </p>
        )}

        {data && data.total_pages > 1 && (
          <div className="flex items-center justify-center gap-2" style={{ marginTop: 16, fontSize: 13 }}>
            <button className="btn btn-secondary btn-sm" onClick={() => load(page - 1, q, sort)} disabled={page <= 1 || loading}>← Sebelumnya</button>
            <span style={{ color: "var(--ink-500)", padding: "0 8px" }}>Halaman {page} / {data.total_pages}</span>
            <button className="btn btn-secondary btn-sm" onClick={() => load(page + 1, q, sort)} disabled={page >= data.total_pages || loading}>Berikutnya →</button>
          </div>
        )}
      </div>
    </AppShell>
  );
}

/** Satu baris stok + panel rincian per gudang (expand saat diklik). */
function StokRow({
  row,
  open,
  detail,
  onToggle,
}: {
  row: Record<string, string>;
  open: boolean;
  detail?: AccurateStock | "loading";
  onToggle: () => void;
}) {
  const per = detail && detail !== "loading" && detail.found ? detail.stock?.per_gudang || [] : [];
  const failed =
    detail && detail !== "loading" && (detail.error || detail.session_expired || detail.found === false);
  return (
    <>
      <tr onClick={onToggle} style={{ cursor: "pointer" }} title="Klik untuk rincian per gudang">
        <td style={{ color: "var(--ink-400)", fontSize: 11, textAlign: "center" }}>{open ? "▾" : "▸"}</td>
        <td className="pn">{row["Part Number"]}</td>
        <td style={{ fontWeight: 500 }}>{row["Part Name"]}</td>
        <td className="num mono">{row["Stok"]}</td>
        <td style={{ color: "var(--ink-500)" }}>{row["Satuan"]}</td>
      </tr>
      {open && (
        <tr>
          <td colSpan={5} style={{ background: "var(--ink-50)", padding: "10px 14px" }}>
            {(!detail || detail === "loading") && (
              <span style={{ fontSize: 12.5, color: "var(--ink-500)" }}>Memuat rincian gudang…</span>
            )}
            {failed && (
              <span style={{ fontSize: 12.5, color: "var(--warn-600)" }}>
                Rincian per gudang tidak tersedia saat ini (koneksi Accurate gagal / PN tak ditemukan).
              </span>
            )}
            {detail && detail !== "loading" && detail.found && (
              per.length > 0 ? (
                <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
                  {per.map((g, j) => (
                    <span
                      key={j}
                      className="pill"
                      title={g.deskripsi || g.gudang}
                      style={{ fontSize: 12 }}
                    >
                      {g.gudang} · <b className="mono">{g.qty.toLocaleString("id-ID")}</b>
                    </span>
                  ))}
                </div>
              ) : (
                <span style={{ fontSize: 12.5, color: "var(--ink-500)" }}>
                  Tidak ada gudang berstok untuk barang ini.
                </span>
              )
            )}
          </td>
        </tr>
      )}
    </>
  );
}
