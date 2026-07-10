"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import AppShell from "@/components/AppShell";
import {
  ApiError,
  getLoginHistory,
  getMonitoring,
  getMonitoringSql,
  type LoginHistoryRow,
  type MonitoringData,
} from "@/lib/api";
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
  // Riwayat login satu user (dibuka dari tombol "riwayat" di baris tabel).
  const [detail, setDetail] = useState<{ user: string; rows: LoginHistoryRow[] } | null>(null);
  const [sql, setSql] = useState("");
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

  const openHistory = useCallback(async (username: string) => {
    const token = getToken();
    if (!token) return;
    setDetail({ user: username, rows: [] });
    try {
      const d = await getLoginHistory(token, username, 100);
      setDetail({ user: username, rows: d.riwayat });
    } catch {
      setDetail({ user: username, rows: [] });
    }
  }, []);

  // Tabel belum dibuat → tampilkan DDL agar admin tinggal salin ke Supabase.
  useEffect(() => {
    if (!data || data.riwayat_tersedia !== false || sql) return;
    const token = getToken();
    if (!token) return;
    getMonitoringSql(token)
      .then((d) => setSql(d.sql))
      .catch(() => {});
  }, [data, sql]);

  const windowMin = data?.online_window_minutes ?? 5;
  const users = (data?.users ?? []).filter((u) => (onlineOnly ? u.online : true));
  const offlineCount = (data?.total_users ?? 0) - (data?.online_count ?? 0);
  const shareDays = data?.share_days ?? 30;
  const flagged = (data?.users ?? []).filter((u) => u.kemungkinan_dipakai_ramai).length;

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

            {/* Tabel riwayat belum dibuat → panduan, bukan error diam-diam. */}
            {data.riwayat_tersedia === false && (
              <div className="mb-5 rounded-xl bg-amber-50 p-4 text-sm ring-1 ring-amber-200">
                <p className="font-semibold text-amber-900">
                  Riwayat login belum aktif — kolom IP &amp; perangkat masih kosong setelah restart.
                </p>
                <p className="mt-1 text-amber-800">
                  Buat tabelnya sekali di <b>Supabase → SQL Editor</b>, lalu refresh halaman ini.
                  Login yang terjadi setelah itu akan tercatat.
                </p>
                {sql && (
                  <pre className="mt-2 overflow-x-auto rounded-lg bg-white p-3 text-[11px] leading-relaxed text-zinc-700 ring-1 ring-amber-200">
                    {sql}
                  </pre>
                )}
              </div>
            )}

            {flagged > 0 && (
              <div className="mb-5 rounded-xl bg-amber-50 p-4 text-sm ring-1 ring-amber-200">
                <p className="font-semibold text-amber-900">
                  ⚠️ {flagged} akun mungkin dipakai beramai-ramai
                </p>
                <p className="mt-1 text-amber-800">
                  Ditandai bila dalam {shareDays} hari terakhir login dari ≥{data.share_ip_min ?? 4} IP
                  atau ≥{data.share_device_min ?? 3} perangkat berbeda. Ini <b>sinyal, bukan bukti</b> —
                  IP bisa berubah sendiri saat ganti WiFi/kuota. Klik <b>riwayat</b> untuk menelusuri.
                </p>
              </div>
            )}

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
                    <th className="px-3 py-2 font-medium">IP terakhir</th>
                    <th className="px-3 py-2 font-medium">Perangkat</th>
                    <th className="px-3 py-2 font-medium" title={`IP berbeda / perangkat berbeda dalam ${shareDays} hari`}>
                      Sebaran ({shareDays}h)
                    </th>
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
                      <td className="px-3 py-2 font-medium">
                        <span className="inline-flex items-center gap-1.5">
                          {u.username}
                          {u.kemungkinan_dipakai_ramai && (
                            <span
                              className="rounded px-1.5 py-0.5 text-[10px] font-semibold text-amber-800 ring-1 ring-amber-300"
                              title={`Login dari ${u.ip_count} IP & ${u.device_count} perangkat berbeda dalam ${shareDays} hari`}
                            >
                              ⚠️ ramai?
                            </span>
                          )}
                        </span>
                      </td>
                      <td className="px-3 py-2">{u.role}</td>
                      <td className="px-3 py-2 font-mono text-xs text-zinc-600">{u.last_ip ?? "—"}</td>
                      <td className="px-3 py-2 text-zinc-600">{u.last_device ?? "—"}</td>
                      <td className="px-3 py-2 text-zinc-500">
                        {u.login_count ? (
                          <button
                            onClick={() => openHistory(u.username)}
                            className="text-brand hover:underline"
                            title={`${u.login_count} login tercatat — klik lihat riwayat`}
                          >
                            {u.ip_count} IP · {u.device_count} perangkat
                          </button>
                        ) : (
                          "—"
                        )}
                      </td>
                      <td className="px-3 py-2 text-zinc-500" title={fmt(u.last_active_at)}>
                        {u.online ? ago(u.last_active_at) : fmt(u.last_active_at)}
                      </td>
                      <td className="px-3 py-2 text-zinc-500">{fmt(u.last_login_at)}</td>
                    </tr>
                  ))}
                  {users.length === 0 && (
                    <tr>
                      <td colSpan={8} className="px-3 py-3 text-zinc-400">
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
                    <th className="px-3 py-2 font-medium">Dari</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-zinc-100 bg-white">
                  {data.recent_activity.map((a, i) => (
                    <tr key={i}>
                      <td className="px-3 py-2 text-zinc-500">{fmt(a.created_at)}</td>
                      <td className="px-3 py-2">{a.username}</td>
                      <td className="px-3 py-2">{a.action}</td>
                      <td className="px-3 py-2 text-zinc-500">
                        {a.ip ? (
                          <span>
                            <span className="font-mono text-xs">{a.ip}</span>
                            {a.device ? ` · ${a.device}` : ""}
                          </span>
                        ) : (
                          (a.target ?? "—")
                        )}
                      </td>
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

        {/* Riwayat login satu user — untuk menelusuri akun yang ditandai. */}
        {detail && (
          <div
            className="fixed inset-0 z-50 grid place-items-center p-4"
            style={{ background: "rgba(15,20,17,.5)" }}
            onClick={() => setDetail(null)}
          >
            <div
              className="max-h-[80vh] w-full max-w-2xl overflow-auto rounded-xl bg-white p-4 shadow-xl"
              onClick={(e) => e.stopPropagation()}
            >
              <div className="mb-3 flex items-center justify-between">
                <h3 className="text-sm font-semibold">
                  Riwayat login · <span className="text-brand">{detail.user}</span>
                </h3>
                <button onClick={() => setDetail(null)} className="text-sm text-zinc-500 hover:text-zinc-800">
                  tutup
                </button>
              </div>
              <table className="tbl w-full">
                <thead className="bg-zinc-50 text-left text-zinc-600">
                  <tr>
                    <th className="px-3 py-2 font-medium">Waktu</th>
                    <th className="px-3 py-2 font-medium">IP</th>
                    <th className="px-3 py-2 font-medium">Perangkat</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-zinc-100">
                  {detail.rows.map((r) => (
                    <tr key={r.id}>
                      <td className="px-3 py-2 text-zinc-500">{fmt(r.created_at)}</td>
                      <td className="px-3 py-2 font-mono text-xs">{r.ip ?? "—"}</td>
                      <td className="px-3 py-2">{r.device ?? "—"}</td>
                    </tr>
                  ))}
                  {detail.rows.length === 0 && (
                    <tr>
                      <td colSpan={3} className="px-3 py-3 text-zinc-400">
                        Belum ada riwayat login tercatat untuk user ini.
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </div>
    </AppShell>
  );
}
