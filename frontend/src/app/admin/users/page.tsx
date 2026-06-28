"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import AppShell from "@/components/AppShell";
import {
  ApiError,
  createUser,
  deleteUser,
  listUsers,
  updateUser,
  type AdminUser,
} from "@/lib/api";
import { clearSession, getToken, getUser } from "@/lib/auth";

export default function AdminUsersPage() {
  const router = useRouter();
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // form tambah user
  const [nu, setNu] = useState("");
  const [np, setNp] = useState("");
  const [nr, setNr] = useState("user");

  const me = getUser()?.username;

  const load = useCallback(async () => {
    const token = getToken();
    if (!token) return router.replace("/login");
    try {
      const d = await listUsers(token);
      setUsers(d.users);
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        clearSession();
        return router.replace("/login");
      }
      if (err instanceof ApiError && err.status === 403) return router.replace("/search");
      setError(err instanceof Error ? err.message : "Gagal memuat");
    }
  }, [router]);

  useEffect(() => {
    if (getUser()?.role !== "admin") {
      router.replace("/search");
      return;
    }
    load();
  }, [router, load]);

  function notify(ok: string) {
    setMsg(ok);
    setError(null);
  }
  function fail(e: unknown) {
    if (e instanceof ApiError && e.status === 401) {
      clearSession();
      router.replace("/login");
      return;
    }
    setError(e instanceof Error ? e.message : "Gagal");
    setMsg(null);
  }

  async function add(e: React.FormEvent) {
    e.preventDefault();
    const token = getToken();
    if (!token) return;
    setBusy(true);
    try {
      await createUser(token, { username: nu, password: np, role: nr });
      notify(`User '${nu.toLowerCase()}' ditambahkan.`);
      setNu("");
      setNp("");
      setNr("user");
      await load();
    } catch (err) {
      fail(err);
    } finally {
      setBusy(false);
    }
  }

  async function setRoleFor(u: AdminUser, role: string) {
    const token = getToken();
    if (!token || role === u.role) return;
    try {
      await updateUser(token, u.username, { role });
      notify(`Role '${u.username}' → ${role}.`);
      await load();
    } catch (err) {
      fail(err);
    }
  }

  async function toggleActive(u: AdminUser) {
    const token = getToken();
    if (!token) return;
    try {
      await updateUser(token, u.username, { is_active: !u.is_active });
      notify(`User '${u.username}' ${u.is_active ? "dinonaktifkan" : "diaktifkan"}.`);
      await load();
    } catch (err) {
      fail(err);
    }
  }

  async function resetPass(u: AdminUser) {
    const token = getToken();
    if (!token) return;
    const pw = window.prompt(`Password baru untuk '${u.username}':`);
    if (!pw) return;
    try {
      await updateUser(token, u.username, { password: pw });
      notify(`Password '${u.username}' diganti.`);
    } catch (err) {
      fail(err);
    }
  }

  async function remove(u: AdminUser) {
    const token = getToken();
    if (!token) return;
    if (!window.confirm(`Hapus user '${u.username}'?`)) return;
    try {
      await deleteUser(token, u.username);
      notify(`User '${u.username}' dihapus.`);
      await load();
    } catch (err) {
      fail(err);
    }
  }

  return (
    <AppShell active="/admin/users" title="Manajemen User" sub="Tambah, ubah role, reset password">
      <div className="mx-auto w-full max-w-5xl px-4 py-5 sm:px-6">
        <h2 className="mb-4 text-base font-semibold">
          👥 Manajemen <span className="text-brand">User</span>
        </h2>

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

        {/* Tambah user */}
        <form
          onSubmit={add}
          className="mb-5 flex flex-wrap items-end gap-2 rounded-xl bg-white p-4 ring-1 ring-zinc-200"
        >
          <div className="flex-1">
            <label className="mb-1 block text-xs text-zinc-500">Username</label>
            <input
              value={nu}
              onChange={(e) => setNu(e.target.value)}
              className="w-full rounded-lg border border-zinc-300 px-3 py-2 text-sm outline-none focus:border-brand"
            />
          </div>
          <div className="flex-1">
            <label className="mb-1 block text-xs text-zinc-500">Password</label>
            <input
              value={np}
              onChange={(e) => setNp(e.target.value)}
              className="w-full rounded-lg border border-zinc-300 px-3 py-2 text-sm outline-none focus:border-brand"
            />
          </div>
          <div>
            <label className="mb-1 block text-xs text-zinc-500">Role</label>
            <select
              value={nr}
              onChange={(e) => setNr(e.target.value)}
              className="rounded-lg border border-zinc-300 px-2 py-2 text-sm outline-none focus:border-brand"
            >
              <option value="user">user</option>
              <option value="admin">admin</option>
              <option value="pembeli">pembeli</option>
            </select>
          </div>
          <button
            disabled={busy || !nu.trim() || !np.trim()}
            className="rounded-lg bg-brand px-4 py-2 text-sm font-semibold text-white hover:bg-green-700 disabled:opacity-50"
          >
            + Tambah
          </button>
        </form>

        <div className="overflow-x-auto rounded-xl ring-1 ring-zinc-200">
          <table className="tbl">
            <thead className="bg-zinc-50 text-left text-zinc-600">
              <tr>
                <th className="px-3 py-2 font-medium">Username</th>
                <th className="px-3 py-2 font-medium">Role</th>
                <th className="px-3 py-2 font-medium">Status</th>
                <th className="px-3 py-2 text-right font-medium">Aksi</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-zinc-100 bg-white">
              {users.map((u) => (
                <tr key={u.username} className="hover:bg-zinc-50">
                  <td className="px-3 py-2 font-medium">
                    {u.username}
                    {u.username === me && (
                      <span className="ml-1 text-xs text-zinc-400">(anda)</span>
                    )}
                  </td>
                  <td className="px-3 py-2">
                    <span
                      className={`rounded px-1.5 py-0.5 text-xs ${
                        u.role === "admin"
                          ? "bg-amber-100 text-amber-700"
                          : u.role === "pembeli"
                          ? "bg-green-100 text-green-700"
                          : "bg-zinc-100 text-zinc-600"
                      }`}
                    >
                      {u.role}
                    </span>
                  </td>
                  <td className="px-3 py-2">
                    {u.is_active ? (
                      <span className="text-green-600">aktif</span>
                    ) : (
                      <span className="text-zinc-400">nonaktif</span>
                    )}
                  </td>
                  <td className="space-x-1 whitespace-nowrap px-3 py-2 text-right text-xs">
                    <select
                      value={u.role}
                      onChange={(e) => setRoleFor(u, e.target.value)}
                      className="rounded border border-zinc-300 px-1.5 py-1 font-medium text-zinc-600 outline-none focus:border-brand"
                      title="Ubah role"
                    >
                      <option value="user">user</option>
                      <option value="admin">admin</option>
                      <option value="pembeli">pembeli</option>
                    </select>
                    <button
                      onClick={() => resetPass(u)}
                      className="rounded border border-zinc-300 px-2 py-1 font-medium text-zinc-600 hover:bg-zinc-50"
                    >
                      Reset PW
                    </button>
                    <button
                      onClick={() => toggleActive(u)}
                      className="rounded border border-zinc-300 px-2 py-1 font-medium text-zinc-600 hover:bg-zinc-50"
                    >
                      {u.is_active ? "Nonaktifkan" : "Aktifkan"}
                    </button>
                    {u.username !== me && (
                      <button
                        onClick={() => remove(u)}
                        className="rounded border border-red-300 px-2 py-1 font-medium text-red-600 hover:bg-red-50"
                      >
                        Hapus
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </AppShell>
  );
}
