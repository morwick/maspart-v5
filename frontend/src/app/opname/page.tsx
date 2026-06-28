"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import AppShell from "@/components/AppShell";
import {
  ApiError,
  deleteOpnameDraft,
  finalizeOpname,
  getOpnameDraft,
  getOpnameHistory,
  opnameFromUpload,
  saveOpnameDraft,
  type OpnameSession,
} from "@/lib/api";
import { clearSession, getToken } from "@/lib/auth";

type Row = {
  pn: string;
  qty_sistem: number | null;
  qty_fisik: number | null;
  note: string;
  part_name: string;
};

function toRows(s: OpnameSession): Row[] {
  return Object.entries(s.items).map(([pn, it]) => ({ pn, ...it }));
}
function toItems(rows: Row[]): OpnameSession["items"] {
  const out: OpnameSession["items"] = {};
  for (const r of rows) {
    out[r.pn] = {
      qty_sistem: r.qty_sistem,
      qty_fisik: r.qty_fisik,
      note: r.note,
      part_name: r.part_name,
    };
  }
  return out;
}

export default function OpnamePage() {
  const router = useRouter();
  const fileRef = useRef<HTMLInputElement>(null);
  const [draft, setDraft] = useState<OpnameSession | null>(null);
  const [rows, setRows] = useState<Row[]>([]);
  const [history, setHistory] = useState<OpnameSession[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);

  const fail = useCallback(
    (e: unknown) => {
      if (e instanceof ApiError && e.status === 401) {
        clearSession();
        router.replace("/login");
        return;
      }
      setError(e instanceof Error ? e.message : "Gagal");
      setMsg(null);
    },
    [router],
  );

  const loadAll = useCallback(async () => {
    const token = getToken();
    if (!token) return router.replace("/login");
    try {
      const d = await getOpnameDraft(token);
      setDraft(d.draft);
      setRows(d.draft ? toRows(d.draft) : []);
      const h = await getOpnameHistory(token);
      setHistory(h.history);
    } catch (e) {
      fail(e);
    }
  }, [router, fail]);

  useEffect(() => {
    if (!getToken()) return router.replace("/login");
    loadAll();
  }, [router, loadAll]);

  async function startUpload() {
    const token = getToken();
    const f = fileRef.current?.files?.[0];
    if (!token || !f) return;
    setBusy(true);
    setError(null);
    try {
      const r = await opnameFromUpload(token, f);
      setDraft(r.session);
      setRows(toRows(r.session));
      setMsg(`Sesi dibuat: ${Object.keys(r.session.items).length} part.`);
      if (fileRef.current) fileRef.current.value = "";
    } catch (e) {
      fail(e);
    } finally {
      setBusy(false);
    }
  }

  function setRow(pn: string, patch: Partial<Row>) {
    setRows((prev) => prev.map((r) => (r.pn === pn ? { ...r, ...patch } : r)));
  }

  async function save() {
    const token = getToken();
    if (!token || !draft) return;
    setBusy(true);
    setError(null);
    try {
      const session = { ...draft, items: toItems(rows) };
      await saveOpnameDraft(token, session);
      setMsg("Draft tersimpan.");
    } catch (e) {
      fail(e);
    } finally {
      setBusy(false);
    }
  }

  async function finalize() {
    const token = getToken();
    if (!token || !draft) return;
    if (!window.confirm("Finalisasi opname? Draft akan masuk riwayat.")) return;
    setBusy(true);
    setError(null);
    try {
      await finalizeOpname(token, { ...draft, items: toItems(rows) });
      setMsg("Opname difinalisasi.");
      setDraft(null);
      setRows([]);
      await loadAll();
    } catch (e) {
      fail(e);
    } finally {
      setBusy(false);
    }
  }

  async function discard() {
    const token = getToken();
    if (!token) return;
    if (!window.confirm("Buang draft opname ini?")) return;
    try {
      await deleteOpnameDraft(token);
      setDraft(null);
      setRows([]);
      setMsg("Draft dibuang.");
    } catch (e) {
      fail(e);
    }
  }

  const counted = rows.filter((r) => r.qty_fisik !== null).length;
  const selisihCount = rows.filter(
    (r) => r.qty_fisik !== null && r.qty_sistem !== null && r.qty_fisik !== r.qty_sistem,
  ).length;

  return (
    <AppShell active="/opname" title="Stok Opname" sub="Hitung fisik vs sistem, simpan draft & finalisasi">
      <div className="mx-auto w-full max-w-6xl px-4 py-5 sm:px-6">
        <h2 className="mb-1 text-base font-semibold">
          📋 Stok <span className="text-brand">Opname</span>
        </h2>
        <p className="mb-4 text-sm text-zinc-500">
          Unggah daftar (Part Number + qty sistem), isi qty fisik, simpan draft,
          lalu finalisasi.
        </p>

        {msg && (
          <p className="mb-3 rounded-lg bg-green-50 px-3 py-2 text-sm text-green-700 ring-1 ring-green-100">
            {msg}
          </p>
        )}
        {error && (
          <p className="mb-3 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700 ring-1 ring-red-100">
            {error}
          </p>
        )}

        {!draft ? (
          <div className="mb-6 rounded-xl bg-white p-4 ring-1 ring-zinc-200">
            <p className="mb-2 text-sm font-medium text-zinc-700">Mulai sesi baru</p>
            <div className="flex flex-wrap items-center gap-2">
              <input
                ref={fileRef}
                type="file"
                accept=".xlsx,.xls,.xlsm,.csv"
                className="text-sm text-zinc-600 file:mr-2 file:rounded-lg file:border-0 file:bg-zinc-100 file:px-3 file:py-1.5 file:text-sm"
              />
              <button
                onClick={startUpload}
                disabled={busy}
                className="rounded-lg bg-brand px-4 py-2 text-sm font-semibold text-white hover:bg-green-700 disabled:opacity-50"
              >
                {busy ? "Memproses…" : "Mulai Opname"}
              </button>
            </div>
            <p className="mt-2 text-xs text-zinc-400">
              Kolom dikenali otomatis: Part Number, qty sistem/stok, part name.
            </p>
          </div>
        ) : (
          <>
            <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
              <div className="text-sm text-zinc-500">
                <b className="text-zinc-700">{rows.length}</b> part · terhitung{" "}
                <b className="text-zinc-700">{counted}</b> · selisih{" "}
                <b className="text-amber-600">{selisihCount}</b>
                {draft.source_filename ? ` · ${draft.source_filename}` : ""}
              </div>
              <div className="flex gap-2">
                <button
                  onClick={save}
                  disabled={busy}
                  className="rounded-lg border border-zinc-300 px-3 py-1.5 text-sm font-medium text-zinc-700 hover:bg-zinc-50 disabled:opacity-50"
                >
                  💾 Simpan Draft
                </button>
                <button
                  onClick={finalize}
                  disabled={busy}
                  className="rounded-lg bg-brand px-3 py-1.5 text-sm font-semibold text-white hover:bg-green-700 disabled:opacity-50"
                >
                  ✅ Finalisasi
                </button>
                <button
                  onClick={discard}
                  className="rounded-lg border border-red-300 px-3 py-1.5 text-sm font-medium text-red-600 hover:bg-red-50"
                >
                  Buang
                </button>
              </div>
            </div>

            <div className="max-h-[60vh] overflow-auto rounded-xl ring-1 ring-zinc-200">
              <table className="tbl">
                <thead className="sticky top-0 bg-zinc-50 text-left text-zinc-600">
                  <tr>
                    <th className="px-3 py-2 font-medium">Part Number</th>
                    <th className="px-3 py-2 font-medium">Part Name</th>
                    <th className="px-3 py-2 font-medium">Sistem</th>
                    <th className="px-3 py-2 font-medium">Fisik</th>
                    <th className="px-3 py-2 font-medium">Selisih</th>
                    <th className="px-3 py-2 font-medium">Catatan</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-zinc-100 bg-white">
                  {rows.map((r) => {
                    const sel =
                      r.qty_fisik !== null && r.qty_sistem !== null
                        ? r.qty_fisik - r.qty_sistem
                        : null;
                    return (
                      <tr key={r.pn} className="hover:bg-zinc-50">
                        <td className="px-3 py-1.5 font-mono">{r.pn}</td>
                        <td className="px-3 py-1.5 text-zinc-600">{r.part_name || "—"}</td>
                        <td className="px-3 py-1.5">{r.qty_sistem ?? "—"}</td>
                        <td className="px-3 py-1.5">
                          <input
                            type="number"
                            value={r.qty_fisik ?? ""}
                            onChange={(e) =>
                              setRow(r.pn, {
                                qty_fisik: e.target.value === "" ? null : Number(e.target.value),
                              })
                            }
                            className="w-20 rounded border border-zinc-300 px-2 py-1 text-sm outline-none focus:border-brand"
                          />
                        </td>
                        <td
                          className={`px-3 py-1.5 font-medium ${
                            sel === null ? "text-zinc-400" : sel === 0 ? "text-green-600" : "text-amber-600"
                          }`}
                        >
                          {sel === null ? "—" : sel > 0 ? `+${sel}` : sel}
                        </td>
                        <td className="px-3 py-1.5">
                          <input
                            value={r.note}
                            onChange={(e) => setRow(r.pn, { note: e.target.value })}
                            className="w-full rounded border border-zinc-300 px-2 py-1 text-sm outline-none focus:border-brand"
                          />
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </>
        )}

        {/* Riwayat */}
        {history.length > 0 && (
          <div className="mt-8">
            <h3 className="mb-2 text-sm font-semibold text-zinc-700">Riwayat Opname</h3>
            <div className="overflow-x-auto rounded-xl ring-1 ring-zinc-200">
              <table className="tbl">
                <thead className="bg-zinc-50 text-left text-zinc-600">
                  <tr>
                    <th className="px-3 py-2 font-medium">Selesai</th>
                    <th className="px-3 py-2 font-medium">Sumber</th>
                    <th className="px-3 py-2 font-medium">Jumlah Part</th>
                    <th className="px-3 py-2 font-medium">Selisih</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-zinc-100 bg-white">
                  {history.map((h) => {
                    const items = Object.values(h.items);
                    const sel = items.filter(
                      (it) =>
                        it.qty_fisik !== null &&
                        it.qty_sistem !== null &&
                        it.qty_fisik !== it.qty_sistem,
                    ).length;
                    return (
                      <tr key={h.session_id}>
                        <td className="px-3 py-2 text-zinc-500">
                          {h.finalized_at
                            ? new Date(h.finalized_at.replace("Z", "") + "Z").toLocaleString("id-ID")
                            : "—"}
                        </td>
                        <td className="px-3 py-2 text-zinc-500">{h.source_filename || "—"}</td>
                        <td className="px-3 py-2">{items.length}</td>
                        <td className="px-3 py-2 text-amber-600">{sel}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </div>
    </AppShell>
  );
}
