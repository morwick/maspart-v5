"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import AppShell from "@/components/AppShell";
import {
  ApiError,
  getPermOverview,
  resetPerm,
  setPerm,
  type PermKind,
  type PermOverview,
} from "@/lib/api";
import { clearSession, getToken, getUser } from "@/lib/auth";

const KINDS: [PermKind, string][] = [
  ["menu", "Menu"],
  ["column", "Kolom"],
  ["harga", "Sub-tab Harga"],
];

export default function AdminMenuPage() {
  const router = useRouter();
  const [kind, setKind] = useState<PermKind>("menu");
  const [data, setData] = useState<PermOverview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [edits, setEdits] = useState<Record<string, Set<string>>>({});
  const [savingRow, setSavingRow] = useState<string | null>(null);

  const load = useCallback(
    async (k: PermKind) => {
      const token = getToken();
      if (!token) return router.replace("/login");
      setData(null);
      setError(null);
      setMsg(null);
      try {
        const d = await getPermOverview(token, k);
        setData(d);
        const init: Record<string, Set<string>> = { __default__: new Set(d.default) };
        for (const u of d.users) {
          if (u.role === "admin") continue;
          init[u.username] = new Set(d.permissions[u.username] ?? d.default);
        }
        setEdits(init);
      } catch (err) {
        if (err instanceof ApiError && err.status === 401) {
          clearSession();
          return router.replace("/login");
        }
        if (err instanceof ApiError && err.status === 403) return router.replace("/search");
        setError(err instanceof Error ? err.message : "Gagal memuat");
      }
    },
    [router],
  );

  useEffect(() => {
    if (getUser()?.role !== "admin") {
      router.replace("/search");
      return;
    }
    load(kind);
  }, [router, load, kind]);

  function toggle(username: string, key: string) {
    setEdits((prev) => {
      const next = { ...prev };
      const s = new Set(next[username] ?? []);
      if (s.has(key)) s.delete(key);
      else s.add(key);
      next[username] = s;
      return next;
    });
  }

  async function save(username: string) {
    const token = getToken();
    if (!token || !data) return;
    setSavingRow(username);
    setMsg(null);
    setError(null);
    try {
      const keys = Object.keys(data.all_keys).filter((k) => edits[username]?.has(k));
      await setPerm(token, kind, username, keys);
      setMsg(`Tersimpan: ${username}`);
      await load(kind);
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        clearSession();
        return router.replace("/login");
      }
      setError(err instanceof Error ? err.message : "Gagal menyimpan");
    } finally {
      setSavingRow(null);
    }
  }

  async function reset(username: string) {
    const token = getToken();
    if (!token) return;
    setSavingRow(username);
    try {
      await resetPerm(token, kind, username);
      setMsg(`Direset ke default: ${username}`);
      await load(kind);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Gagal reset");
    } finally {
      setSavingRow(null);
    }
  }

  return (
    <AppShell active="/admin/menu" title="Menu Control" sub="Atur akses menu, kolom, sub-tab per user">
      <div className="mx-auto w-full max-w-6xl px-4 py-5 sm:px-6">
        <h2 className="mb-1 text-base font-semibold">
          ⚙️ Menu <span className="text-brand">Control</span>
        </h2>
        <p className="mb-4 text-sm text-zinc-500">
          Atur akses per user. User <b>admin</b> selalu punya akses penuh.
        </p>

        <div className="mb-5 inline-flex rounded-lg border border-zinc-300 p-0.5 text-sm">
          {KINDS.map(([k, label]) => (
            <button
              key={k}
              onClick={() => setKind(k)}
              className={`rounded-md px-3 py-1.5 font-medium transition-colors ${
                kind === k ? "bg-brand text-white" : "text-zinc-600 hover:bg-zinc-100"
              }`}
            >
              {label}
            </button>
          ))}
        </div>

        {error && (
          <p className="mb-3 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700 ring-1 ring-red-100">
            {error}
          </p>
        )}
        {msg && (
          <p className="mb-3 rounded-lg bg-green-50 px-3 py-2 text-sm text-green-700 ring-1 ring-green-100">
            {msg}
          </p>
        )}

        {!data ? (
          <p className="text-sm text-zinc-500">Memuat…</p>
        ) : (
          <Matrix
            data={data}
            edits={edits}
            savingRow={savingRow}
            onToggle={toggle}
            onSave={save}
            onReset={reset}
          />
        )}
      </div>
    </AppShell>
  );
}

function Matrix({
  data,
  edits,
  savingRow,
  onToggle,
  onSave,
  onReset,
}: {
  data: PermOverview;
  edits: Record<string, Set<string>>;
  savingRow: string | null;
  onToggle: (u: string, k: string) => void;
  onSave: (u: string) => void;
  onReset: (u: string) => void;
}) {
  const keys = Object.keys(data.all_keys);
  const always = new Set(data.always);
  const usersEditable = data.users.filter((u) => u.role !== "admin");

  const Row = ({ username, label }: { username: string; label: React.ReactNode }) => {
    const set = edits[username] ?? new Set<string>();
    const usingDefault = username !== "__default__" && !(username in data.permissions);
    return (
      <tr className="hover:bg-zinc-50">
        <td className="whitespace-nowrap px-3 py-2 font-medium">
          {label}
          {usingDefault && (
            <span className="ml-2 rounded bg-zinc-100 px-1.5 py-0.5 text-xs font-normal text-zinc-500">
              default
            </span>
          )}
        </td>
        {keys.map((k) => {
          const locked = always.has(k);
          return (
            <td key={k} className="px-3 py-2 text-center">
              <input
                type="checkbox"
                checked={locked || set.has(k)}
                disabled={locked}
                onChange={() => onToggle(username, k)}
              />
            </td>
          );
        })}
        <td className="whitespace-nowrap px-3 py-2 text-right">
          <button
            onClick={() => onSave(username)}
            disabled={savingRow === username}
            className="rounded-lg bg-brand px-3 py-1 text-xs font-semibold text-white hover:bg-green-700 disabled:opacity-50"
          >
            Simpan
          </button>
          {username !== "__default__" && (
            <button
              onClick={() => onReset(username)}
              disabled={savingRow === username}
              className="ml-1 rounded-lg border border-zinc-300 px-2 py-1 text-xs font-medium text-zinc-600 hover:bg-zinc-50 disabled:opacity-50"
            >
              Reset
            </button>
          )}
        </td>
      </tr>
    );
  };

  return (
    <>
      <div className="overflow-x-auto rounded-xl ring-1 ring-zinc-200">
        <table className="tbl">
          <thead className="bg-zinc-50 text-zinc-600">
            <tr>
              <th className="px-3 py-2 text-left font-medium">User</th>
              {keys.map((k) => (
                <th key={k} className="px-3 py-2 text-center font-medium">
                  {data.all_keys[k]}
                </th>
              ))}
              <th className="px-3 py-2" />
            </tr>
          </thead>
          <tbody className="divide-y divide-zinc-100 bg-white">
            <Row username="__default__" label={<span className="text-zinc-800">Default (user baru)</span>} />
            {usersEditable.map((u) => (
              <Row key={u.username} username={u.username} label={u.username} />
            ))}
          </tbody>
        </table>
      </div>
      {usersEditable.length === 0 && (
        <p className="mt-3 text-sm text-zinc-500">Tidak ada user non-admin.</p>
      )}
    </>
  );
}
