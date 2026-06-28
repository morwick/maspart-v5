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

type Sub = "list" | "cari" | "batch";
const PAGE_SIZE = 50;
const SUB_KEY: Record<Sub, string> = {
  list: "subtab_list_harga",
  cari: "subtab_cari_harga",
  batch: "subtab_batch_harga",
};
const SUB_TABS: [Sub, string][] = [
  ["list", "📋 List Harga"],
  ["cari", "🔍 Cari Harga (SIMS)"],
  ["batch", "📥 Batch Cari Harga"],
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
        // Kalau sub aktif tidak diizinkan, pindah ke yang pertama diizinkan.
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
      <div className="mx-auto w-full max-w-5xl px-4 py-5 sm:px-6">
        <h2 className="mb-4 text-base font-semibold">
          💰 <span className="text-brand">Harga</span> Sparepart
        </h2>

        <div className="mb-5 inline-flex flex-wrap rounded-lg border border-zinc-300 p-0.5 text-sm">
          {visibleTabs.map(([k, label]) => (
            <button
              key={k}
              onClick={() => setSub(k)}
              className={`rounded-md px-3 py-1.5 font-medium transition-colors ${
                sub === k ? "bg-brand text-white" : "text-zinc-600 hover:bg-zinc-100"
              }`}
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
          <p className="text-sm text-zinc-500">
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

  const cols = ["Part Number", "Part Name", "Harga (Rp)"];

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
      <div className="mb-3 flex flex-wrap gap-2">
        <form
          onSubmit={(e) => {
            e.preventDefault();
            setQ(qInput);
            load(1, qInput, sort);
          }}
          className="flex flex-1 gap-2"
        >
          <input
            value={qInput}
            onChange={(e) => setQInput(e.target.value)}
            placeholder="Cari Part Number / Part Name…"
            className="flex-1 rounded-lg border border-zinc-300 px-3 py-2 text-sm outline-none focus:border-brand focus:ring-2 focus:ring-brand/20"
          />
          <button className="rounded-lg bg-brand px-5 py-2 text-sm font-semibold text-white hover:bg-green-700">
            Cari
          </button>
        </form>
        <select
          value={sort}
          onChange={(e) => {
            setSort(e.target.value);
            load(1, q, e.target.value);
          }}
          className="rounded-lg border border-zinc-300 px-2 py-2 text-sm outline-none focus:border-brand"
        >
          <option value="pn">Part Number</option>
          <option value="name">Part Name</option>
          <option value="harga_asc">Harga ↑</option>
          <option value="harga_desc">Harga ↓</option>
        </select>
      </div>

      {error && (
        <p className="mb-3 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700 ring-1 ring-red-100">
          {error}
        </p>
      )}

      {data && (
        <div className="mb-3 flex flex-wrap items-center justify-between gap-2 text-sm text-zinc-500">
          <span>
            Total <b className="text-zinc-700">{data.total}</b> · Hasil filter{" "}
            <b className="text-zinc-700">{data.total_filtered}</b>
          </span>
          <button
            onClick={handleExport}
            disabled={data.total_filtered === 0}
            className="rounded-lg border border-zinc-300 px-3 py-1.5 font-medium text-zinc-700 hover:bg-zinc-50 disabled:opacity-50"
          >
            ⬇️ Download Excel
          </button>
        </div>
      )}

      {data && data.rows.length > 0 && (
        <div className="overflow-x-auto rounded-xl ring-1 ring-zinc-200">
          <table className="tbl">
            <thead className="bg-zinc-50 text-left text-zinc-600">
              <tr>
                {cols.map((c) => (
                  <th key={c} className="px-3 py-2 font-medium">
                    {c}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-zinc-100 bg-white">
              {data.rows.map((r, i) => (
                <tr key={i} className="hover:bg-zinc-50">
                  <td className="px-3 py-2 font-mono">{r["Part Number"]}</td>
                  <td className="px-3 py-2">{r["Part Name"]}</td>
                  <td className="px-3 py-2 font-medium">{r["Harga (Rp)"]}</td>
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
      <p className="mb-3 text-sm text-zinc-500">
        Ambil harga part langsung dari SIMS (CNY) dan konversi otomatis ke IDR.
      </p>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          search(false);
        }}
        className="mb-4 flex gap-2"
      >
        <input
          value={pn}
          onChange={(e) => setPn(e.target.value)}
          placeholder="Contoh: WG1641230025"
          className="flex-1 rounded-lg border border-zinc-300 px-3 py-2 text-sm outline-none focus:border-brand focus:ring-2 focus:ring-brand/20"
        />
        <button
          disabled={loading}
          className="rounded-lg bg-brand px-5 py-2 text-sm font-semibold text-white hover:bg-green-700 disabled:opacity-60"
        >
          {loading ? "Mengambil…" : "🔍 Cari"}
        </button>
        <button
          type="button"
          onClick={() => search(true)}
          disabled={loading || !pn.trim()}
          className="rounded-lg border border-zinc-300 px-3 py-2 text-sm font-medium text-zinc-700 hover:bg-zinc-50 disabled:opacity-50"
        >
          🔄
        </button>
      </form>

      {error && (
        <p className="mb-3 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700 ring-1 ring-red-100">
          {error}
        </p>
      )}

      {res &&
        (res.cny != null ? (
          <div className="rounded-xl bg-green-50 p-5 ring-1 ring-green-100">
            <p className="font-mono text-lg font-bold text-green-900">{res.pn}</p>
            <div className="mt-3 flex flex-wrap gap-10">
              <div>
                <p className="text-xs text-zinc-500">Harga SIMS (CNY)</p>
                <p className="text-2xl font-extrabold text-blue-700">{fmtCny(res.cny)}</p>
              </div>
              <div>
                <p className="text-xs text-zinc-500">Harga IDR</p>
                <p className="text-2xl font-extrabold text-green-700">{fmtRp(res.idr)}</p>
              </div>
            </div>
            <p className="mt-2 text-xs text-zinc-400">
              Kurs: 1 CNY = Rp {res.rate.toLocaleString("id-ID")}
              {res.note ? ` · ${res.note}` : ""}
            </p>
          </div>
        ) : (
          <p className="rounded-lg bg-amber-50 px-3 py-2 text-sm text-amber-800 ring-1 ring-amber-100">
            Harga tidak ditemukan untuk <b>{res.pn}</b>
            {res.note ? ` (${res.note})` : ""}.
          </p>
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
      <p className="mb-3 text-sm text-zinc-500">
        Masukkan banyak part number (1 per baris) → ambil harga dari SIMS sekaligus.
        Maksimum 300 PN.
      </p>
      <textarea
        value={text}
        onChange={(e) => setText(e.target.value)}
        rows={6}
        placeholder={"WG1641230025\nWG9725520274"}
        className="w-full rounded-lg border border-zinc-300 px-3 py-2 font-mono text-sm outline-none focus:border-brand focus:ring-2 focus:ring-brand/20"
      />
      <button
        onClick={run}
        disabled={loading || !text.trim()}
        className="mt-3 rounded-lg bg-brand px-5 py-2.5 text-sm font-semibold text-white hover:bg-green-700 disabled:opacity-50"
      >
        {loading ? "Mengambil harga…" : "🔍 Proses Batch"}
      </button>
      {loading && (
        <p className="mt-2 text-xs text-zinc-400">
          Mengambil dari SIMS per part — bisa beberapa menit untuk banyak PN.
        </p>
      )}

      {error && (
        <p className="mt-3 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700 ring-1 ring-red-100">
          {error}
        </p>
      )}

      {data && (
        <>
          <div className="mb-2 mt-5 flex flex-wrap items-center justify-between gap-2 text-sm text-zinc-500">
            <span>
              {data.found}/{data.count} ditemukan · kurs 1 CNY = Rp{" "}
              {data.rate.toLocaleString("id-ID")}
            </span>
            <button
              onClick={handleExport}
              className="rounded-lg border border-zinc-300 px-3 py-1.5 font-medium text-zinc-700 hover:bg-zinc-50"
            >
              ⬇️ Download Excel
            </button>
          </div>
          <div className="overflow-x-auto rounded-xl ring-1 ring-zinc-200">
            <table className="tbl">
              <thead className="bg-zinc-50 text-left text-zinc-600">
                <tr>
                  <th className="px-3 py-2 font-medium">Part Number</th>
                  <th className="px-3 py-2 font-medium">Harga (CNY)</th>
                  <th className="px-3 py-2 font-medium">Harga (IDR)</th>
                  <th className="px-3 py-2 font-medium">Ket.</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-zinc-100 bg-white">
                {data.results.map((r, i) => (
                  <tr key={i} className="hover:bg-zinc-50">
                    <td className="px-3 py-2 font-mono">{r.pn}</td>
                    <td className="px-3 py-2">{fmtCny(r.cny)}</td>
                    <td className="px-3 py-2 font-medium">{fmtRp(r.idr)}</td>
                    <td className="px-3 py-2 text-xs text-zinc-500">
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
    <div className="mt-4 flex items-center justify-center gap-2 text-sm">
      <button
        onClick={() => onGo(page - 1)}
        disabled={page <= 1 || loading}
        className="rounded-lg border border-zinc-300 px-3 py-1.5 font-medium text-zinc-700 hover:bg-zinc-50 disabled:opacity-40"
      >
        ← Sebelumnya
      </button>
      <span className="px-2 text-zinc-500">
        Halaman {page} / {totalPages}
      </span>
      <button
        onClick={() => onGo(page + 1)}
        disabled={page >= totalPages || loading}
        className="rounded-lg border border-zinc-300 px-3 py-1.5 font-medium text-zinc-700 hover:bg-zinc-50 disabled:opacity-40"
      >
        Berikutnya →
      </button>
    </div>
  );
}
