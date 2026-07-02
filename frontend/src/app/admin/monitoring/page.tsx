"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import AppShell from "@/components/AppShell";
import { ApiError, getMonitoring, type MonitoringData } from "@/lib/api";
import { clearSession, getToken, getUser } from "@/lib/auth";

const REFRESH_MS = 15_000; // auto-refresh tiap 15 detik

function fmt(ts?: string | null) {
  if (!ts) return "—";
  try {
    return new Date(ts.includes("Z") ? ts : ts + "Z").toLocaleString("id-ID");
  } catch {
    return ts;
  }
}

// "baru saja" / "3 mnt lalu" dari timestamp aktif terakhir.
function ago(ts?: string | null) {
  if (!ts) return "—";
  try {
    const d = new Date(ts.includes("Z") ? ts : ts + "Z").getTime();
    const s = Math.max(0, Math.floor((Date.now() - d) / 1000));
    if (s < 60) return "baru saja";
    const m = Math.floor(s / 60);
    if (m < 60) return `${m} mnt lalu`;
    const h = Math.floor(m / 60);
    if (h < 24) return `${h} jam lalu`;
    return `${Math.floor(h / 24)} hari lalu`;
  } catch {
    return "—";
  }
}

export default function AdminMonitoringPage() {
  const router = useRouter();
  const [data, setData] = useState<MonitoringData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [onlineOnly, setOnlineOnly] = useState(false);
  const [updatedAt, setUpdatedAt] = useState<string>("");
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);

  const load = useCallback(async () => {
    const token = getToken();
    if (!token) return router.replace("/login");
    try {
      const d = await getMonitoring(token);
      setData(d);
      setError(null);
      setUpdatedAt(new Date().toLocaleTimeString("id-ID"));
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
    timer.current = setInterval(load, REFRESH_MS);
    return () => {
      if (timer.current) clearInterval(timer.current);
    };
  }, [router, load]);

  const windowMin = data?.online_window_minutes ?? 5;
  const users = (data?.users ?? []).filter((u) => (onlineOnly ? u.online : true));
  const offlineCount = (data?.total_users ?? 0) - (data?.online_count ?? 0);

  return (
    <AppShell active="/admin/monitoring" title="Monitoring User" sub="Status online/offline & aktivitas">
      <div className="mx-auto w-full max-w-5xl px-4 py-5 sm:px-6">
        <div className="mb-4 flex flex-wrap items-center justify-between gap-2">
          <h2 className="text-base font-semibold">
            📡 Monitoring <span className="text-brand">User</span>
          </h2>
          <div className="flex items-center gap-3 text-sm">
            {updatedAt && (
              <span className="text-xs text-zinc-400">
                diperbarui {updatedAt} · auto {REFRESH_MS / 1000}s
              </span>
            )}
            <button
              onClick={load}
              className="rounded-lg border border-zinc-300 px-3 py-1.5 font-medium text-zinc-700 hover:bg-zinc-50"
            >
              🔄 Refresh
            </button>
          </div>
        </div>

        {error && (
          <p className="mb-3 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700 ring-1 ring-red-100">
            {error}
          </p>
        )}

        {data && (
          <>
            {/* Kartu ringkasan */}
            <div className="mb-5 grid grid-cols-2 gap-3 sm:grid-cols-3">
              <button
                onClick={() => setOnlineOnly(true)}
                className={`rounded-xl bg-white p-4 text-left ring-1 transition ${
                  onlineOnly ? "ring-2 ring-green-400" : "ring-zinc-200 hover:ring-green-300"
                }`}
              >
                <p className="flex items-center gap-1.5 text-xs text-zinc-500">
                  <span className="relative flex h-2 w-2">
                    <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-green-400 opacity-75" />
                    <span className="relative inline-flex h-2 w-2 rounded-full bg-green-500" />
                  </span>
                  Online ({windowMin} mnt)
                </p>
                <p className="text-2xl font-bold text-green-600">{data.online_count}</p>
              </button>
              <button
                onClick={() => setOnlineOnly(false)}
                className={`rounded-xl bg-white p-4 text-left ring-1 transition ${
                  !onlineOnly ? "ring-2 ring-zinc-300" : "ring-zinc-200 hover:ring-zinc-300"
                }`}
              >
                <p className="text-xs text-zinc-500">Offline</p>
                <p className="text-2xl font-bold text-zinc-400">{offlineCount}</p>
              </button>
              <div className="rounded-xl bg-white p-4 ring-1 ring-zinc-200">
                <p className="text-xs text-zinc-500">Total user</p>
                <p className="text-2xl font-bold">{data.total_users}</p>
              </div>
            </div>

            <div className="mb-2 flex items-center justify-between">
              <h3 className="text-sm font-semibold text-zinc-700">
                {onlineOnly ? "User online" : "Semua user"}
              </h3>
              {onlineOnly && (
                <button
                  onClick={() => setOnlineOnly(false)}
                  className="text-xs text-brand hover:underline"
                >
                  tampilkan semua
                </button>
              )}
            </div>

            <div className="mb-6 overflow-x-auto rounded-xl ring-1 ring-zinc-200">
              <table className="tbl">
                <thead className="bg-zinc-50 text-left text-zinc-600">
                  <tr>
                    <th className="px-3 py-2 font-medium">Status</th>
                    <th className="px-3 py-2 font-medium">Username</th>
                    <th className="px-3 py-2 font-medium">Role</th>
                    <th className="px-3 py-2 font-medium">Aktif terakhir</th>
                    <th className="px-3 py-2 font-medium">Login terakhir</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-zinc-100 bg-white">
                  {users.map((u) => (
                    <tr key={u.username} className="hover:bg-zinc-50">
                      <td className="px-3 py-2">
                        {u.online ? (
                          <span className="inline-flex items-center gap-1.5 font-medium text-green-600">
                            <span className="h-2 w-2 rounded-full bg-green-500" /> online
                          </span>
                        ) : u.is_active ? (
                          <span className="inline-flex items-center gap-1.5 text-zinc-400">
                            <span className="h-2 w-2 rounded-full bg-zinc-300" /> offline
                          </span>
                        ) : (
                          <span className="inline-flex items-center gap-1.5 text-red-400">
                            <span className="h-2 w-2 rounded-full bg-red-300" /> nonaktif
                          </span>
                        )}
                      </td>
                      <td className="px-3 py-2 font-medium">{u.username}</td>
                      <td className="px-3 py-2">{u.role}</td>
                      <td className="px-3 py-2 text-zinc-500" title={fmt(u.last_active_at)}>
                        {u.online ? ago(u.last_active_at) : fmt(u.last_active_at)}
                      </td>
                      <td className="px-3 py-2 text-zinc-500">{fmt(u.last_login_at)}</td>
                    </tr>
                  ))}
                  {users.length === 0 && (
                    <tr>
                      <td colSpan={5} className="px-3 py-3 text-zinc-400">
                        {onlineOnly ? "Tidak ada user online saat ini." : "Belum ada user."}
                      </td>
                    </tr>
                  )}
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
