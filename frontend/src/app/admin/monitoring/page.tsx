"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import AppShell from "@/components/AppShell";
import { ApiError, getMonitoring, type MonitoringData } from "@/lib/api";
import { clearSession, getToken, getUser } from "@/lib/auth";

function fmt(ts?: string | null) {
  if (!ts) return "—";
  try {
    return new Date(ts.includes("Z") ? ts : ts + "Z").toLocaleString("id-ID");
  } catch {
    return ts;
  }
}

export default function AdminMonitoringPage() {
  const router = useRouter();
  const [data, setData] = useState<MonitoringData | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    const token = getToken();
    if (!token) return router.replace("/login");
    try {
      setData(await getMonitoring(token));
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) {
        clearSession();
        return router.replace("/login");
      }
      if (e instanceof ApiError && e.status === 403) return router.replace("/search");
      setError(e instanceof Error ? e.message : "Gagal memuat");
    }
  }, [router]);

  useEffect(() => {
    if (getUser()?.role !== "admin") {
      router.replace("/search");
      return;
    }
    load();
  }, [router, load]);

  return (
    <AppShell active="/admin/monitoring" title="User Monitoring" sub="Aktivitas & status user">
      <div className="mx-auto w-full max-w-5xl px-4 py-5 sm:px-6">
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-base font-semibold">
            📊 User <span className="text-brand">Monitoring</span>
          </h2>
          <button
            onClick={load}
            className="rounded-lg border border-zinc-300 px-3 py-1.5 text-sm font-medium text-zinc-700 hover:bg-zinc-50"
          >
            🔄 Refresh
          </button>
        </div>

        {error && (
          <p className="mb-3 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700 ring-1 ring-red-100">
            {error}
          </p>
        )}

        {data && (
          <>
            <div className="mb-5 flex flex-wrap gap-3">
              <div className="rounded-xl bg-white p-4 ring-1 ring-zinc-200">
                <p className="text-xs text-zinc-500">Online (5 mnt)</p>
                <p className="text-2xl font-bold text-green-600">{data.online_count}</p>
              </div>
              <div className="rounded-xl bg-white p-4 ring-1 ring-zinc-200">
                <p className="text-xs text-zinc-500">Total user</p>
                <p className="text-2xl font-bold">{data.total_users}</p>
              </div>
            </div>

            <h3 className="mb-2 text-sm font-semibold text-zinc-700">User</h3>
            <div className="mb-6 overflow-x-auto rounded-xl ring-1 ring-zinc-200">
              <table className="tbl">
                <thead className="bg-zinc-50 text-left text-zinc-600">
                  <tr>
                    <th className="px-3 py-2 font-medium">Username</th>
                    <th className="px-3 py-2 font-medium">Role</th>
                    <th className="px-3 py-2 font-medium">Status</th>
                    <th className="px-3 py-2 font-medium">Login terakhir</th>
                    <th className="px-3 py-2 font-medium">Aktif terakhir</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-zinc-100 bg-white">
                  {data.users.map((u) => (
                    <tr key={u.username} className="hover:bg-zinc-50">
                      <td className="px-3 py-2 font-medium">{u.username}</td>
                      <td className="px-3 py-2">{u.role}</td>
                      <td className="px-3 py-2">
                        {u.online ? (
                          <span className="text-green-600">● online</span>
                        ) : u.is_active ? (
                          <span className="text-zinc-400">offline</span>
                        ) : (
                          <span className="text-zinc-300">nonaktif</span>
                        )}
                      </td>
                      <td className="px-3 py-2 text-zinc-500">{fmt(u.last_login_at)}</td>
                      <td className="px-3 py-2 text-zinc-500">{fmt(u.last_active_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            <h3 className="mb-2 text-sm font-semibold text-zinc-700">Aktivitas terbaru</h3>
            <div className="overflow-x-auto rounded-xl ring-1 ring-zinc-200">
              <table className="tbl">
                <thead className="bg-zinc-50 text-left text-zinc-600">
                  <tr>
                    <th className="px-3 py-2 font-medium">Waktu</th>
                    <th className="px-3 py-2 font-medium">User</th>
                    <th className="px-3 py-2 font-medium">Aksi</th>
                    <th className="px-3 py-2 font-medium">Target</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-zinc-100 bg-white">
                  {data.recent_activity.map((a, i) => (
                    <tr key={i}>
                      <td className="px-3 py-2 text-zinc-500">{fmt(a.created_at)}</td>
                      <td className="px-3 py-2">{a.username}</td>
                      <td className="px-3 py-2">{a.action}</td>
                      <td className="px-3 py-2 text-zinc-500">{a.target ?? "—"}</td>
                    </tr>
                  ))}
                  {data.recent_activity.length === 0 && (
                    <tr>
                      <td colSpan={4} className="px-3 py-3 text-zinc-400">
                        Belum ada aktivitas tercatat.
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </>
        )}
      </div>
    </AppShell>
  );
}
