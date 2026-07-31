"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import AppShell from "@/components/AppShell";
import {
  ApiError,
  batchHarga,
  cariHarga,
  downloadBlob,
  exportBatchHarga,
  exportHargaList,
  getHargaList,
  type BatchHargaResponse,
  type CariHargaResult,
  type HargaListResponse,
} from "@/lib/api";
import { clearSession, getToken } from "@/lib/auth";
import { ensurePerms } from "@/lib/perms";
import { EmptyIcon, EmptyState, TableSkeleton } from "@/components/States";

type Sub = "list" | "cari" | "batch";
const PAGE_SIZE = 50;
const SUB_KEY: Record<Sub, string> = {
  list: "subtab_list_harga",
  cari: "subtab_cari_harga",
  batch: "subtab_batch_harga",
};
const SUB_TABS: [Sub, string][] = [
  ["list", "List Harga"],
  ["cari", "Cari Harga (SIMS)"],
  ["batch", "Batch Cari Harga"],
];
const fmtRp = (n: number | null) =>
  n == null ? "—" : "Rp " + n.toLocaleString("id-ID");
const fmtCny = (n: number | null) =>
  n == null ? "—" : "¥ " + n.toLocaleString("id-ID", { minimumFractionDigits: 2 });

export default function HargaPage() {
  const router = useRouter();
  const [sub, setSub] = useState<Sub>("list");
  const [allowedSubs, setAllowedSubs] = useState<string[] | null>(null);

  function on401(err: unknown): boolean {
    if (err instanceof ApiError && err.status === 401) {
      clearSession();
      router.replace("/login");
      return true;
    }
    return false;
  }

  useEffect(() => {
    if (!getToken()) {
      router.replace("/login");
      return;
    }
    ensurePerms().then((p) => {
      if (p) {
        setAllowedSubs(p.harga_subtabs);
        const firstAllowed = SUB_TABS.find(([k]) => p.harga_subtabs.includes(SUB_KEY[k]));
        if (firstAllowed && !p.harga_subtabs.includes(SUB_KEY[sub])) {
          setSub(firstAllowed[0]);
        }
      }
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [router]);

  const visibleTabs = SUB_TABS.filter(
    ([k]) => allowedSubs == null || allowedSubs.includes(SUB_KEY[k]),
  );

  return (
    <AppShell active="/harga" title="Harga" sub="List, cari & batch harga sparepart">
      <div className="mx-auto w-full max-w-5xl px-4 py-6 sm:px-7">
        {/* Segmented tabs */}
        <div style={{ display: "inline-flex", background: "var(--ink-100)", borderRadius: 9, padding: 3, gap: 2, marginBottom: 18, flexWrap: "wrap" }}>
          {visibleTabs.map(([k, label]) => (
            <button
              key={k}
              onClick={() => setSub(k)}
              style={{
                borderRadius: 7, padding: "7px 14px", fontSize: 13, fontWeight: 600,
                border: "none", cursor: "pointer", fontFamily: "inherit",
                background: sub === k ? "var(--paper)" : "transparent",
                color: sub === k ? "var(--brand-700)" : "var(--ink-600)",
                boxShadow: sub === k ? "var(--shadow-1)" : "none",
              }}
            >
              {label}
            </button>
          ))}
        </div>

        {visibleTabs.some(([k]) => k === sub) ? (
          <>
            {sub === "list" && <ListHarga on401={on401} router={router} />}
            {sub === "cari" && <CariHarga on401={on401} router={router} />}
            {sub === "batch" && <BatchHarga on401={on401} router={router} />}
          </>
        ) : (
          <p style={{ fontSize: 13, color: "var(--ink-500)" }}>
            Anda tidak memiliki akses ke sub-tab harga manapun.
          </p>
        )}
      </div>
    </AppShell>
  );
}

/* ── Sub-tab: List Harga ─────────────────────────────────────────── */
function ListHarga({
  on401,
  router,
}: {
  on401: (e: unknown) => boolean;
  router: ReturnType<typeof useRouter>;
}) {
  const [data, setData] = useState<HargaListResponse | null>(null);
  const [qInput, setQInput] = useState("");
  const [q, setQ] = useState("");
  const [sort, setSort] = useState("pn");
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(
    async (p: number, keyword: string, srt: string) => {
      const token = getToken();
      if (!token) return router.replace("/login");
      setLoading(true);
      setError(null);
      try {
        const res = await getHargaList(token, { q: keyword, sort: srt, page: p, pageSize: PAGE_SIZE });
        setData(res);
        setPage(res.page);
      } catch (err) {
        if (!on401(err)) setError(err instanceof Error ? err.message : "Gagal memuat");
      } finally {
        setLoading(false);
      }
    },
    [router, on401],
  );

  useEffect(() => {
    load(1, "", "pn");
  }, [load]);

  async function handleExport() {
    const token = getToken();
    if (!token) return;
    try {
      const blob = await exportHargaList(token, { q, sort });
      downloadBlob(blob, "harga_sparepart.xlsx");
    } catch (err) {
      if (!on401(err)) setError(err instanceof Error ? err.message : "Gagal export");
    }
  }

  return (
    <div>
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
          <option value="harga_asc">Harga ↑</option>
          <option value="harga_desc">Harga ↓</option>
        </select>
      </div>

      {error && <div className="alert alert-error" style={{ marginTop: 12 }}>{error}</div>}

      {data && (
        <div className="flex flex-wrap items-center justify-between gap-2" style={{ marginTop: 12, fontSize: 12.5, color: "var(--ink-500)" }}>
          <span>
            Total <b className="mono" style={{ color: "var(--ink-800)" }}>{data.total.toLocaleString("id-ID")}</b> · Hasil filter{" "}
            <b className="mono" style={{ color: "var(--ink-800)" }}>{data.total_filtered.toLocaleString("id-ID")}</b>
          </span>
          <button className="btn btn-secondary btn-sm" onClick={handleExport} disabled={data.total_filtered === 0}>⬇ Export Excel</button>
        </div>
      )}

      {/* Memuat: rangka tabel, bukan halaman kosong yang lalu tiba-tiba terisi. */}
      {loading && !data && !error && (
        <div className="surface" style={{ marginTop: 12 }}>
          <TableSkeleton rows={10} cols={3} />
        </div>
      )}

      {data && data.rows.length === 0 && !error && (
        <div className="surface" style={{ marginTop: 12 }}>
          <EmptyState
            icon={EmptyIcon.doc}
            title="Tidak ada harga yang cocok"
            sub={
              q
                ? <>Tidak ada part yang mengandung <b>{q}</b> di daftar harga.</>
                : "Daftar harga tidak mengembalikan baris apa pun."
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
        <div className="surface" style={{ marginTop: 12, overflow: "auto" }}>
          <table className="tbl">
            <thead>
              <tr>
                <th>Part Number</th>
                <th>Part Name</th>
                <th className="num">Harga (Rp)</th>
              </tr>
            </thead>
            <tbody>
              {data.rows.map((r, i) => (
                <tr key={i}>
                  <td className="pn">{r["Part Number"]}</td>
                  <td style={{ fontWeight: 500 }}>{r["Part Name"]}</td>
                  <td className="num mono">{r["Harga (Rp)"]}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {data && data.total_pages > 1 && (
        <Pager page={page} totalPages={data.total_pages} loading={loading} onGo={(p) => load(p, q, sort)} />
      )}
    </div>
  );
}

/* ── Sub-tab: Cari Harga (SIMS) ──────────────────────────────────── */
function CariHarga({
  on401,
  router,
}: {
  on401: (e: unknown) => boolean;
  router: ReturnType<typeof useRouter>;
}) {
  const [pn, setPn] = useState("");
  const [res, setRes] = useState<CariHargaResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function search(refresh = false) {
    const token = getToken();
    if (!token) return router.replace("/login");
    const term = pn.trim().toUpperCase();
    if (!term) return;
    setLoading(true);
    setError(null);
    try {
      setRes(await cariHarga(token, term, refresh));
    } catch (err) {
      if (!on401(err)) setError(err instanceof Error ? err.message : "Gagal");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div>
      <p style={{ fontSize: 13, color: "var(--ink-500)", marginBottom: 12 }}>
        Ambil harga part langsung dari SIMS (CNY) dan konversi otomatis ke IDR.
      </p>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          search(false);
        }}
        className="flex gap-2"
      >
        <input
          className="input mono"
          value={pn}
          onChange={(e) => setPn(e.target.value)}
          placeholder="Contoh: WG1641230025"
        />
        <button className="btn btn-primary" disabled={loading}>{loading ? "Mengambil…" : "Cari"}</button>
        <button type="button" className="btn btn-secondary" onClick={() => search(true)} disabled={loading || !pn.trim()} title="Ambil ulang (abaikan cache)">🔄</button>
      </form>

      {error && <div className="alert alert-error" style={{ marginTop: 12 }}>{error}</div>}

      {res &&
        (res.cny != null ? (
          <div className="surface" style={{ marginTop: 16, padding: 20, borderColor: "var(--brand-100)", background: "var(--brand-50)" }}>
            <p className="mono" style={{ fontSize: 18, fontWeight: 700, color: "var(--brand-700)" }}>{res.pn}</p>
            <div className="flex flex-wrap" style={{ gap: 40, marginTop: 12 }}>
              <div>
                <p style={{ fontSize: 11.5, color: "var(--ink-500)" }}>Harga SIMS (CNY)</p>
                <p className="mono" style={{ fontSize: 24, fontWeight: 800, color: "var(--info-600)" }}>{fmtCny(res.cny)}</p>
              </div>
              <div>
                <p style={{ fontSize: 11.5, color: "var(--ink-500)" }}>Harga IDR</p>
                <p className="mono" style={{ fontSize: 24, fontWeight: 800, color: "var(--brand-700)" }}>{fmtRp(res.idr)}</p>
              </div>
            </div>
            <p style={{ marginTop: 8, fontSize: 11.5, color: "var(--ink-400)" }}>
              Kurs: 1 CNY = Rp {res.rate.toLocaleString("id-ID")}{res.note ? ` · ${res.note}` : ""}
            </p>
          </div>
        ) : (
          <div className="alert" style={{ marginTop: 16, background: "var(--warn-50)", color: "var(--warn-600)", borderColor: "#f6d9a8" }}>
            Harga tidak ditemukan untuk <b>{res.pn}</b>{res.note ? ` (${res.note})` : ""}.
          </div>
        ))}
    </div>
  );
}

/* ── Sub-tab: Batch Cari Harga ───────────────────────────────────── */
function BatchHarga({
  on401,
  router,
}: {
  on401: (e: unknown) => boolean;
  router: ReturnType<typeof useRouter>;
}) {
  const [text, setText] = useState("");
  const [data, setData] = useState<BatchHargaResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function run() {
    const token = getToken();
    if (!token) return router.replace("/login");
    if (!text.trim()) return;
    setLoading(true);
    setError(null);
    try {
      setData(await batchHarga(token, text));
    } catch (err) {
      if (!on401(err)) setError(err instanceof Error ? err.message : "Gagal");
    } finally {
      setLoading(false);
    }
  }

  async function handleExport() {
    const token = getToken();
    if (!token || !data) return;
    try {
      const blob = await exportBatchHarga(token, data.rate, data.results);
      downloadBlob(blob, "batch_harga.xlsx");
    } catch (err) {
      if (!on401(err)) setError(err instanceof Error ? err.message : "Gagal export");
    }
  }

  return (
    <div>
      <p style={{ fontSize: 13, color: "var(--ink-500)", marginBottom: 12 }}>
        Masukkan banyak part number (1 per baris) → ambil harga dari SIMS sekaligus. Maksimum 300 PN.
      </p>
      <textarea
        className="textarea mono"
        value={text}
        onChange={(e) => setText(e.target.value)}
        rows={6}
        placeholder={"WG1641230025\nWG9725520274"}
      />
      <button className="btn btn-primary btn-lg" style={{ marginTop: 12 }} onClick={run} disabled={loading || !text.trim()}>
        {loading ? "Mengambil harga…" : "Proses Batch"}
      </button>
      {loading && (
        <p style={{ marginTop: 8, fontSize: 11.5, color: "var(--ink-400)" }}>
          Mengambil dari SIMS per part — bisa beberapa menit untuk banyak PN.
        </p>
      )}

      {error && <div className="alert alert-error" style={{ marginTop: 12 }}>{error}</div>}

      {data && (
        <>
          <div className="flex flex-wrap items-center justify-between gap-2" style={{ marginTop: 20, marginBottom: 8, fontSize: 12.5, color: "var(--ink-500)" }}>
            <span>
              <b className="mono" style={{ color: "var(--ink-800)" }}>{data.found}/{data.count}</b> ditemukan · kurs 1 CNY = Rp {data.rate.toLocaleString("id-ID")}
            </span>
            <button className="btn btn-secondary btn-sm" onClick={handleExport}>⬇ Export Excel</button>
          </div>
          <div className="surface" style={{ overflow: "auto" }}>
            <table className="tbl">
              <thead>
                <tr>
                  <th>Part Number</th>
                  <th className="num">Harga (CNY)</th>
                  <th className="num">Harga (IDR)</th>
                  <th>Ket.</th>
                </tr>
              </thead>
              <tbody>
                {data.results.map((r, i) => (
                  <tr key={i}>
                    <td className="pn">{r.pn}</td>
                    <td className="num mono">{fmtCny(r.cny)}</td>
                    <td className="num mono" style={{ fontWeight: 500 }}>{fmtRp(r.idr)}</td>
                    <td style={{ fontSize: 12, color: "var(--ink-500)" }}>
                      {r.status === "ok" ? r.note ?? "✓" : "Tidak ditemukan"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  );
}

/* ── Pager kecil ─────────────────────────────────────────────────── */
function Pager({
  page,
  totalPages,
  loading,
  onGo,
}: {
  page: number;
  totalPages: number;
  loading: boolean;
  onGo: (p: number) => void;
}) {
  return (
    <div className="flex items-center justify-center gap-2" style={{ marginTop: 16, fontSize: 13 }}>
      <button className="btn btn-secondary btn-sm" onClick={() => onGo(page - 1)} disabled={page <= 1 || loading}>← Sebelumnya</button>
      <span style={{ color: "var(--ink-500)", padding: "0 8px" }}>Halaman {page} / {totalPages}</span>
      <button className="btn btn-secondary btn-sm" onClick={() => onGo(page + 1)} disabled={page >= totalPages || loading}>Berikutnya →</button>
    </div>
  );
}
