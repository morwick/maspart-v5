"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import AppShell from "@/components/AppShell";
import {
  ApiError,
  approveSinonimUsulan,
  generateSinonimUsulan,
  getSearchMisses,
  getSinonimUsulan,
  rejectSinonimUsulan,
  resolveSearchMiss,
  type SearchMiss,
  type SinonimUsulan,
} from "@/lib/api";
import { clearSession, getToken, getUser } from "@/lib/auth";

export default function SearchMissesPage() {
  const router = useRouter();
  const [rows, setRows] = useState<SearchMiss[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [usulan, setUsulan] = useState<SinonimUsulan[]>([]);
  const [generating, setGenerating] = useState(false);
  const [genNote, setGenNote] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);

  const load = useCallback(async () => {
    const token = getToken();
    if (!token) return router.replace("/login");
    setLoading(true);
    setError(null);
    try {
      const [res, us] = await Promise.all([
        getSearchMisses(token),
        getSinonimUsulan(token).catch(() => ({ jumlah: 0, usulan: [] })),
      ]);
      setRows(res.misses);
      setTotal(res.total);
      setUsulan(us.usulan);
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        clearSession();
        return router.replace("/login");
      }
      setError(err instanceof Error ? err.message : "Gagal memuat");
    } finally {
      setLoading(false);
    }
  }, [router]);

  useEffect(() => {
    if (!getToken()) {
      router.replace("/login");
      return;
    }
    if (getUser()?.role !== "admin") {
      router.replace("/search");
      return;
    }
    load();
  }, [router, load]);

  async function resolve(q: string) {
    const token = getToken();
    if (!token) return;
    setRows((r) => r.filter((x) => x.query !== q)); // optimistis
    try {
      await resolveSearchMiss(token, q);
    } catch {
      load(); // gagal → muat ulang
    }
  }

  async function generate() {
    const token = getToken();
    if (!token) return;
    setGenerating(true);
    setGenNote(null);
    try {
      const res = await generateSinonimUsulan(token, 10);
      if (res.error) setGenNote(`Gagal: ${res.error}`);
      else if (res.dibuat === 0) setGenNote(res.catatan || "Tidak ada usulan baru — AI tidak menemukan istilah yang bisa dipetakan ke katalog.");
      else setGenNote(`AI membuat ${res.dibuat} usulan baru — tinjau lalu Setujui/Tolak di bawah.`);
      await load();
    } catch (err) {
      setGenNote(err instanceof Error ? err.message : "Gagal membuat usulan");
    } finally {
      setGenerating(false);
    }
  }

  async function decide(id: string, ok: boolean) {
    const token = getToken();
    if (!token) return;
    setBusyId(id);
    try {
      if (ok) await approveSinonimUsulan(token, id);
      else await rejectSinonimUsulan(token, id);
      setUsulan((u) => u.filter((x) => x.id !== id));
      // approve/reject juga me-resolve miss-nya di backend → segarkan daftar miss
      setRows((r) => r.filter((x) => x.query.toLowerCase() !== id));
      setTotal((t) => Math.max(0, t - 1));
    } catch (err) {
      setGenNote(err instanceof Error ? err.message : "Gagal memproses usulan");
    } finally {
      setBusyId(null);
    }
  }

  return (
    <AppShell active="/admin/search-misses" title="Pencarian Nihil" sub="Query yang 0 hasil — kandidat sinonim">
      <div className="mx-auto w-full max-w-4xl px-4 py-6 sm:px-7">
        <div className="flex flex-wrap items-center gap-3" style={{ marginBottom: 14 }}>
          <p style={{ fontSize: 13.5, color: "var(--ink-500)", flex: 1, minWidth: 220 }}>
            Istilah yang dicari user (di Cari Part & Asisten) tapi <b>tidak menemukan apa pun</b>.
            Klik <b>🤖 Usulkan sinonim (AI)</b> agar AI memetakan istilah-istilah ini ke kata kunci
            katalog (tervalidasi ke data nyata), lalu tinggal <b>Setujui</b> — langsung dipakai asisten.
          </p>
          <button className="btn btn-primary btn-sm" onClick={generate} disabled={generating || loading}>
            {generating ? "AI sedang menyusun…" : "🤖 Usulkan sinonim (AI)"}
          </button>
          <button className="btn btn-secondary btn-sm" onClick={load} disabled={loading}>
            {loading ? "Memuat…" : "↻ Muat ulang"}
          </button>
        </div>

        {error && <div className="alert alert-error" style={{ marginBottom: 14 }}>{error}</div>}
        {genNote && <div className="alert" style={{ marginBottom: 14 }}>{genNote}</div>}

        {usulan.length > 0 && (
          <div style={{ marginBottom: 22 }}>
            <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 8 }}>
              Usulan AI menunggu keputusan ({usulan.length})
            </div>
            <div className="surface" style={{ overflow: "auto" }}>
              <table className="tbl">
                <thead>
                  <tr>
                    <th>Istilah</th>
                    <th>Kata kunci katalog (tervalidasi)</th>
                    <th style={{ width: 90 }}>Keyakinan</th>
                    <th style={{ width: 170 }} />
                  </tr>
                </thead>
                <tbody>
                  {usulan.map((u) => (
                    <tr key={u.id}>
                      <td>
                        <div style={{ fontWeight: 500 }}>{u.triggers.join(", ")}</div>
                        {u.alasan && (
                          <div style={{ fontSize: 11.5, color: "var(--ink-500)" }}>{u.alasan}</div>
                        )}
                      </td>
                      <td>
                        {u.keywords.map((k) => (
                          <span key={k} className="pill" style={{ marginRight: 4, height: 20, fontSize: 10.5, padding: "0 7px" }}>{k}</span>
                        ))}
                        {(u.keywords_dibuang || []).map((k) => (
                          <span key={k} className="pill" title="Dibuang: tidak ditemukan di katalog" style={{ marginRight: 4, height: 20, fontSize: 10.5, padding: "0 7px", opacity: 0.45, textDecoration: "line-through" }}>{k}</span>
                        ))}
                      </td>
                      <td className="num mono">{Math.round((u.confidence || 0) * 100)}%</td>
                      <td style={{ textAlign: "right", whiteSpace: "nowrap" }}>
                        <button
                          className="btn btn-primary btn-sm"
                          style={{ marginRight: 6 }}
                          disabled={busyId === u.id}
                          onClick={() => decide(u.id, true)}
                          title="Masukkan ke Kamus Sinonim (langsung aktif di asisten)"
                        >
                          ✓ Setujui
                        </button>
                        <button
                          className="btn btn-secondary btn-sm"
                          disabled={busyId === u.id}
                          onClick={() => decide(u.id, false)}
                          title="Tolak (tidak akan diusulkan lagi)"
                        >
                          ✕ Tolak
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}

        <div style={{ fontSize: 12.5, color: "var(--ink-500)", marginBottom: 10 }}>
          Total <b className="mono" style={{ color: "var(--ink-800)" }}>{total}</b> istilah unik belum ketemu.
        </div>

        {rows.length > 0 ? (
          <div className="surface" style={{ overflow: "auto" }}>
            <table className="tbl">
              <thead>
                <tr>
                  <th>Query</th>
                  <th className="num" style={{ width: 90 }}>Frekuensi</th>
                  <th style={{ width: 130 }}>Sumber</th>
                  <th style={{ width: 90 }} />
                </tr>
              </thead>
              <tbody>
                {rows.map((m) => (
                  <tr key={m.query}>
                    <td style={{ fontWeight: 500 }}>{m.query}</td>
                    <td className="num mono">{m.count}</td>
                    <td>
                      {(m.sources || []).map((s) => (
                        <span key={s} className="pill" style={{ marginRight: 4, height: 20, fontSize: 10.5, padding: "0 7px" }}>{s}</span>
                      ))}
                    </td>
                    <td style={{ textAlign: "right", whiteSpace: "nowrap" }}>
                      <button
                        className="btn btn-secondary btn-sm"
                        style={{ marginRight: 6 }}
                        onClick={() => router.push(`/admin/sinonim?trigger=${encodeURIComponent(m.query)}`)}
                        title="Buka Kamus Sinonim dengan istilah ini terisi"
                      >
                        + Sinonim
                      </button>
                      <button className="btn btn-secondary btn-sm" onClick={() => resolve(m.query)} title="Hapus dari daftar (sudah ditangani)">
                        Selesai
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          !loading && (
            <div className="surface grid place-items-center" style={{ height: 160, color: "var(--ink-500)", fontSize: 13.5 }}>
              Belum ada pencarian nihil yang tercatat. 🎉
            </div>
          )
        )}
      </div>
    </AppShell>
  );
}
