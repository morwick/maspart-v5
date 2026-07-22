"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import AppShell from "@/components/AppShell";
import {
  ApiError,
  getAdminAppConfig,
  getPermOverview,
  resetPerm,
  saveAdminAppConfig,
  setPerm,
  type PermKind,
  type PermOverview,
} from "@/lib/api";
import { clearSession, getToken, getUser } from "@/lib/auth";

const KINDS: [PermKind, string][] = [
  ["menu", "Menu"],
  ["column", "Kolom"],
  ["harga", "Sub-tab Harga"],
  ["sesi", "Sesi"],
  ["asisten", "Asisten AI"],
];

// Tab 'sesi' berbeda sifatnya: centang = MEMBATASI, bukan memberi akses.
// Tab 'asisten': centang = MEMBERI kemampuan yang biasanya khusus admin.
const KETERANGAN: Partial<Record<PermKind, string>> = {
  sesi:
    "Centang “Hanya 1 perangkat” agar akun tidak bisa dipakai bersamaan di dua tempat. " +
    "Saat orang lain login dengan akun itu, perangkat yang lama otomatis keluar. " +
    "Admin tidak pernah dibatasi.",
  asisten:
    "Centang = MEMBERI kemampuan Asisten AI yang biasanya khusus admin. " +
    "Admin & akun “mas” selalu punya Harga SIMS & Populasi — centang tidak pernah " +
    "mencabut dari mereka. Akun pembeli tidak bisa diberi kemampuan ini. " +
    "Stok Tertahan & Alternatif juga butuh centang “Kolom Stok”; hasil berharga " +
    "butuh “Kolom Harga” (blok pertama di bawah).",
};

export default function AdminMenuPage() {
  const router = useRouter();
  const [kind, setKind] = useState<PermKind>("menu");

  useEffect(() => {
    if (getUser()?.role !== "admin") router.replace("/search");
  }, [router]);

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

        {KETERANGAN[kind] && (
          <p className="mb-3 rounded-lg bg-amber-50 px-3 py-2 text-sm text-amber-800 ring-1 ring-amber-200">
            {KETERANGAN[kind]}
          </p>
        )}

        {kind === "asisten" ? (
          // Dua blok, SATU sumber izin masing-masing: blok kolom = kind "column"
          // yang sama dengan tab Kolom (centang di sini = centang di sana),
          // blok kemampuan = kind "asisten".
          <>
            <MaintenanceToggle />
            <KindSection kind="column" title="Kolom Harga & Stok (sumber sama dengan tab Kolom)" />
            <KindSection kind="asisten" title="Kemampuan Asisten AI" />
          </>
        ) : (
          <KindSection kind={kind} />
        )}
      </div>
    </AppShell>
  );
}

/** Saklar GLOBAL "Asisten AI sedang perbaikan" — disimpan di app-config
 *  (config.asisten_perbaikan), bukan izin per-akun: satu centang = semua user
 *  non-admin melihat popup perbaikan & endpoint chat ditolak server (503).
 *  Admin tetap bisa memakai asisten untuk menguji selagi mode ini menyala. */
function MaintenanceToggle() {
  const [aktif, setAktif] = useState<boolean | null>(null); // null = memuat
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    const token = getToken();
    if (!token) return;
    getAdminAppConfig(token)
      .then((c) => setAktif(c.config?.asisten_perbaikan === true))
      .catch(() => setErr("Gagal memuat status mode perbaikan."));
  }, []);

  async function toggle() {
    const token = getToken();
    if (!token || aktif === null || busy) return;
    const baru = !aktif;
    setBusy(true);
    setErr(null);
    try {
      // Kirim config UTUH hasil merge — PUT app-config mengganti seluruh objek
      // config, flag lain (chip saran, dll.) tidak boleh ikut terhapus.
      const cur = await getAdminAppConfig(token);
      await saveAdminAppConfig(token, {
        config: { ...cur.config, asisten_perbaikan: baru },
      });
      setAktif(baru);
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Gagal menyimpan.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div
      className={`mb-4 rounded-xl border p-4 ${
        aktif ? "border-red-300 bg-red-50" : "border-zinc-200 bg-white"
      }`}
    >
      <div className="flex items-center justify-between gap-3">
        <div>
          <div className="text-sm font-semibold">
            🔧 Mode Perbaikan Asisten AI {aktif ? "— SEDANG AKTIF" : ""}
          </div>
          <p className="mt-0.5 text-xs text-zinc-500">
            Centang = semua user (kecuali admin) melihat popup “Asisten AI sedang
            perbaikan” dan tidak bisa mengirim chat. Berlaku juga di aplikasi mobile.
          </p>
          {err && <p className="mt-1 text-xs text-red-600">{err}</p>}
        </div>
        <label className="flex shrink-0 cursor-pointer items-center gap-2 text-sm font-medium">
          <input
            type="checkbox"
            checked={aktif === true}
            disabled={aktif === null || busy}
            onChange={toggle}
            className="h-4 w-4 accent-red-600"
          />
          Sedang perbaikan
        </label>
      </div>
    </div>
  );
}

function KindSection({ kind, title }: { kind: PermKind; title?: string }) {
  const router = useRouter();
  const [data, setData] = useState<PermOverview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [edits, setEdits] = useState<Record<string, Set<string>>>({});
  const [savingRow, setSavingRow] = useState<string | null>(null);

  const load = useCallback(async () => {
    const token = getToken();
    if (!token) return router.replace("/login");
    setData(null);
    setError(null);
    setMsg(null);
    try {
      const d = await getPermOverview(token, kind);
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
  }, [router, kind]);

  useEffect(() => {
    load();
  }, [load]);

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
      await load();
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
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Gagal reset");
    } finally {
      setSavingRow(null);
    }
  }

  return (
    <div className="mb-6">
      {title && <h3 className="mb-2 text-sm font-semibold text-zinc-700">{title}</h3>}

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
