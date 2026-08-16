"use client";

import { Fragment, useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import AppShell from "@/components/AppShell";
import {
  ApiError,
  deleteChatLog,
  getChatLog,
  type ChatLogRow,
  type ChatLogSummary,
} from "@/lib/api";
import { clearSession, getToken, getUser } from "@/lib/auth";

// Angka token ringkas: 1234 → "1,2rb", 1234567 → "1,2jt".
function fmtTok(n?: number): string {
  const v = n ?? 0;
  if (v >= 1_000_000) return `${(v / 1_000_000).toFixed(1).replace(".", ",")}jt`;
  if (v >= 1_000) return `${(v / 1_000).toFixed(1).replace(".", ",")}rb`;
  return String(v);
}

function Stat({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="surface" style={{ padding: 14, minWidth: 130, flex: 1 }}>
      <div className="stat-label">{label}</div>
      <div className="stat-value" style={{ fontSize: 22 }}>{value}</div>
      {hint && <div style={{ fontSize: 11.5, color: "var(--ink-500)" }}>{hint}</div>}
    </div>
  );
}

// Arti tiap sebab guard (migrasi 026) — dipakai sebagai tooltip di panel.
const GUARD_ARTI: Record<string, string> = {
  pn: "Part Number di jawaban tak ada di hasil tool maupun riwayat — dugaan karangan",
  angka: "Stok/harga/spesifikasi diklaim tanpa dasar hasil tool — dugaan karangan",
  subst: "PN katalog per-model dipakai padahal EPC per-VIN sudah jalan",
  excel: "Jawaban menjanjikan kartu unduh yang tak pernah dibuat",
  dtc: "Pertanyaan kode kesalahan dijawab tanpa memanggil cari_kode_kesalahan",
  epc: "User menyebut nomor rangka tapi PN dijawab tanpa tool EPC",
  ajar: "Jawaban mengaku sudah menyimpan pengetahuan tanpa tool ajarkan_pengetahuan",
  rem: "Rem anti-loop menolak panggilan tool — model diingatkan item itu BELUM dicek",
};

export default function ChatLogPage() {
  const router = useRouter();
  const [summary, setSummary] = useState<ChatLogSummary | null>(null);
  const [rows, setRows] = useState<ChatLogRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  // Filter per akun: klik nama user di tabel → lihat pertanyaan dia saja.
  const [filterUser, setFilterUser] = useState("");
  // Baris yang di-expand → tampilkan teks jawaban AI (monitoring).
  const [openId, setOpenId] = useState<number | null>(null);

  const load = useCallback(async () => {
    const token = getToken();
    if (!token) return router.replace("/login");
    setLoading(true);
    setError(null);
    try {
      const d = await getChatLog(token, 200);
      setSummary(d.ringkasan);
      setRows(d.log);
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        clearSession();
        return router.replace("/login");
      }
      if (err instanceof ApiError && err.status === 403) return router.replace("/search");
      setError(err instanceof Error ? err.message : "Gagal memuat");
    } finally {
      setLoading(false);
    }
  }, [router]);

  const hapus = useCallback(async (beforeDays?: number) => {
    const token = getToken();
    if (!token) return router.replace("/login");
    const pesan = beforeDays
      ? `Hapus log yang lebih tua dari ${beforeDays} hari?`
      : "Hapus SEMUA log observabilitas AI? Tindakan ini tidak bisa dibatalkan.";
    if (!window.confirm(pesan)) return;
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const r = await deleteChatLog(token, beforeDays);
      setNotice(`${r.dihapus >= 0 ? r.dihapus.toLocaleString("id-ID") : "Sejumlah"} baris dihapus.`);
      await load();
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        clearSession();
        return router.replace("/login");
      }
      setError(err instanceof Error ? err.message : "Gagal menghapus");
    } finally {
      setBusy(false);
    }
  }, [router, load]);

  useEffect(() => {
    if (getUser()?.role !== "admin") {
      router.replace("/search");
      return;
    }
    load();
  }, [router, load]);

  const empty = !loading && summary && summary.total === 0;

  // Siapa saja yang bertanya, diurut dari yang paling sering — konteks cepat
  // sebelum menyelami tabel.
  const perUser = (() => {
    const n = new Map<string, number>();
    for (const r of rows) n.set(r.username || "(tanpa nama)", (n.get(r.username || "(tanpa nama)") ?? 0) + 1);
    return [...n.entries()].sort((a, b) => b[1] - a[1]);
  })();
  const tampil = filterUser ? rows.filter((r) => (r.username || "") === filterUser) : rows;

  return (
    <AppShell active="/admin/chat-log" title="Observabilitas AI" sub="Metrik tiap giliran chat asisten">
      <div className="mx-auto w-full max-w-5xl px-4 py-6 sm:px-7">
        <div className="flex items-center gap-3" style={{ marginBottom: 14 }}>
          <p style={{ fontSize: 13.5, color: "var(--ink-500)", flex: 1 }}>
            Ringkasan dari maksimal 1000 giliran chat terakhir — pakai untuk memantau latensi,
            biaya token DeepSeek per pesan, seberapa sering guard anti-halusinasi menyala, dan
            tool mana yang paling dipakai. Kolom Token = input / output (arahkan kursor untuk
            rincian cache &amp; jumlah panggilan API).
          </p>
          <button className="btn btn-secondary btn-sm" onClick={load} disabled={loading || busy}>
            {loading ? "Memuat…" : "↻ Muat ulang"}
          </button>
          <button className="btn btn-secondary btn-sm" onClick={() => hapus(30)} disabled={loading || busy}
                  title="Hapus baris yang lebih tua dari 30 hari">
            🧹 Hapus &gt;30 hari
          </button>
          <button className="btn btn-danger btn-sm" onClick={() => hapus()} disabled={loading || busy}
                  title="Hapus SEMUA log">
            {busy ? "Menghapus…" : "🗑️ Hapus semua"}
          </button>
        </div>

        <p style={{ fontSize: 12, color: "var(--ink-500)", marginTop: -6, marginBottom: 12 }}>
          Log &gt;30 hari juga terhapus <b>otomatis</b> (retensi harian).
        </p>

        {notice && <div className="alert alert-success" style={{ marginBottom: 14 }}>{notice}</div>}
        {error && <div className="alert alert-error" style={{ marginBottom: 14 }}>{error}</div>}

        {empty && (
          <div className="surface grid place-items-center" style={{ height: 160, color: "var(--ink-500)", fontSize: 13.5, textAlign: "center", padding: 20 }}>
            Belum ada data. Pastikan tabel <code className="mono">ai_chat_log</code> sudah dibuat di
            Supabase (migrations/016_ai_chat_log.sql), lalu coba beberapa percakapan di Asisten.
          </div>
        )}

        {summary && summary.total > 0 && (
          <>
            <div className="flex flex-wrap gap-3" style={{ marginBottom: 14 }}>
              <Stat label="Giliran tercatat" value={summary.total.toLocaleString("id-ID")} />
              <Stat label="Latensi p50" value={`${((summary.latensi_ms?.p50 ?? 0) / 1000).toFixed(1)}s`}
                    hint={`p90 ${((summary.latensi_ms?.p90 ?? 0) / 1000).toFixed(1)}s · maks ${((summary.latensi_ms?.maks ?? 0) / 1000).toFixed(1)}s`} />
              <Stat label="Guard menyala" value={`${summary.guard_rasio_persen ?? 0}%`}
                    hint={`${summary.guard_menyala ?? 0} giliran`} />
              <Stat label="Tool gagal" value={`${summary.tool_gagal_rasio_persen ?? 0}%`}
                    hint={`${summary.tool_gagal ?? 0} giliran`} />
              {/* Biaya DeepSeek per pesan: rata-rata token masuk+keluar per giliran.
                  Cache hit = bagian input bertarif ±1/10 (makin tinggi makin hemat). */}
              <Stat
                label="Token / pesan"
                value={
                  (summary.token?.giliran_terukur ?? 0) > 0
                    ? `${fmtTok((summary.token?.rata2_in ?? 0) + (summary.token?.rata2_out ?? 0))}`
                    : "—"
                }
                hint={
                  (summary.token?.giliran_terukur ?? 0) > 0
                    ? `in ${fmtTok(summary.token?.rata2_in)} · out ${fmtTok(summary.token?.rata2_out)} · cache ${summary.token?.cache_hit_persen ?? 0}%`
                    : "belum ada data (migrasi 021)"
                }
              />
            </div>

            <div className="flex flex-wrap gap-3" style={{ marginBottom: 22 }}>
              <div className="surface" style={{ padding: 14, flex: 1, minWidth: 240 }}>
                <div className="stat-label" style={{ marginBottom: 8 }}>Tool tersering</div>
                {(summary.tool_tersering ?? []).map(([t, n]) => (
                  <div key={t} className="flex items-center justify-between" style={{ fontSize: 12.5, padding: "2px 0" }}>
                    <span className="mono">{t}</span>
                    <span style={{ color: "var(--ink-500)" }}>{n}</span>
                  </div>
                ))}
              </div>
              {(summary.tool_gagal_tersering ?? []).length > 0 && (
                <div className="surface" style={{ padding: 14, flex: 1, minWidth: 240 }}>
                  <div className="stat-label" style={{ marginBottom: 8 }}>Tool paling sering gagal</div>
                  {(summary.tool_gagal_tersering ?? []).map(([t, n, pct, nf, err, rem]) => (
                    <div key={t} className="flex items-center justify-between" style={{ fontSize: 12.5, padding: "2px 0" }}>
                      <span className="mono">{t}</span>
                      <span
                        style={{ color: "var(--ink-500)" }}
                        title={`${n} gagal · ${pct}% dari pemakaian · ${nf ?? 0} tak ketemu · ${err ?? 0} error · ${rem ?? 0} ditolak rem (belum dicek)`}
                      >
                        {n}{" "}
                        <span style={{ fontSize: 11 }}>
                          ({pct}%{nf || err || rem ? ` · ${nf ?? 0}✕/${err ?? 0}⚠${rem ? `/${rem}⛔` : ""}` : ""})
                        </span>
                      </span>
                    </div>
                  ))}
                  {summary.tool_gagal_rincian && (
                    <div style={{ fontSize: 11, color: "var(--ink-500)", marginTop: 6 }}>
                      ✕ tak ketemu: {summary.tool_gagal_rincian.nf ?? 0} · ⚠ error: {summary.tool_gagal_rincian.err ?? 0}
                      {/* ⛔ rem = plafon panggilan KITA yang menolak, bukan data
                          hilang. Dulu ikut terhitung "lama" sehingga kelas
                          kegagalan yang paling bisa diperbaiki tak terlihat. */}
                      {(summary.tool_gagal_rincian.brake ?? 0) > 0 && ` · ⛔ ditolak rem: ${summary.tool_gagal_rincian.brake}`}
                      {(summary.tool_gagal_rincian.legacy ?? 0) > 0 && ` · lama: ${summary.tool_gagal_rincian.legacy}`}
                    </div>
                  )}
                </div>
              )}
              {/* Sebab guard — memisahkan dugaan KARANGAN dari SUBSTITUSI dan
                  dari guard wajib-tool. Dulu semuanya cuma satu angka, sehingga
                  guard paling berharga (dtc) tak terlihat sama sekali. */}
              {summary.guard_sebab && Object.keys(summary.guard_sebab).length > 0 && (
                <div className="surface" style={{ padding: 14, flex: 1, minWidth: 240 }}>
                  <div className="stat-label" style={{ marginBottom: 8 }}>Sebab guard menyala</div>
                  {Object.entries(summary.guard_sebab)
                    .sort((a, b) => b[1] - a[1])
                    .map(([g, n]) => (
                      <div key={g} className="flex items-center justify-between" style={{ fontSize: 12.5, padding: "2px 0" }}>
                        <span title={GUARD_ARTI[g] || g}>{g}</span>
                        <span style={{ color: "var(--ink-500)" }}>{n}</span>
                      </div>
                    ))}
                  <div style={{ fontSize: 11, color: "var(--ink-500)", marginTop: 6, lineHeight: 1.5 }}>
                    pn/angka = dugaan karangan · subst = PN per-model menyalip EPC ·
                    dtc/epc/excel/ajar = jawaban tanpa tool wajib · rem = plafon
                    panggilan tool tercapai (ada item yang belum dicek)
                  </div>
                </div>
              )}
              <div className="surface" style={{ padding: 14, flex: 1, minWidth: 240 }}>
                <div className="stat-label" style={{ marginBottom: 8 }}>Outcome jawaban</div>
                {Object.entries(summary.outcome ?? {}).map(([o, n]) => (
                  <div key={o} className="flex items-center justify-between" style={{ fontSize: 12.5, padding: "2px 0" }}>
                    <span>{o}</span>
                    <span style={{ color: "var(--ink-500)" }}>{n}</span>
                  </div>
                ))}
              </div>
              {/* Siapa yang paling banyak bertanya (dari baris yang dimuat). */}
              <div className="surface" style={{ padding: 14, flex: 1, minWidth: 240 }}>
                <div className="stat-label" style={{ marginBottom: 8 }}>Penanya tersering</div>
                {perUser.slice(0, 8).map(([u, n]) => (
                  <button
                    key={u}
                    onClick={() => setFilterUser(filterUser === u ? "" : u)}
                    className="flex w-full items-center justify-between"
                    style={{
                      fontSize: 12.5,
                      padding: "2px 0",
                      background: "transparent",
                      border: "none",
                      cursor: "pointer",
                      color: filterUser === u ? "var(--brand-700)" : "var(--ink-800)",
                      fontWeight: filterUser === u ? 600 : 400,
                    }}
                    title={filterUser === u ? "Klik untuk tampilkan semua" : `Tampilkan pertanyaan ${u} saja`}
                  >
                    <span>{u}</span>
                    <span style={{ color: "var(--ink-500)" }}>{n}</span>
                  </button>
                ))}
                {perUser.length === 0 && (
                  <div style={{ fontSize: 12, color: "var(--ink-500)" }}>—</div>
                )}
              </div>
            </div>
          </>
        )}

        {rows.length > 0 && (
          <div className="surface" style={{ overflow: "auto" }}>
            {filterUser && (
              <div
                className="flex items-center justify-between"
                style={{ padding: "8px 12px", borderBottom: "1px solid var(--ink-150)", fontSize: 12.5 }}
              >
                <span>
                  Menampilkan <b>{tampil.length}</b> giliran dari akun <b>{filterUser}</b>
                </span>
                <button className="btn btn-secondary btn-sm" onClick={() => setFilterUser("")}>
                  Tampilkan semua
                </button>
              </div>
            )}
            <table className="tbl">
              <thead>
                <tr>
                  <th>Waktu</th>
                  <th style={{ width: 120 }}>Akun</th>
                  <th>Pertanyaan</th>
                  <th style={{ width: 120 }}>Tool</th>
                  <th className="num" style={{ width: 70 }}>Ronde</th>
                  <th className="num" style={{ width: 80 }}>Latensi</th>
                  <th className="num" style={{ width: 110 }}>Token</th>
                  <th style={{ width: 90 }}>Outcome</th>
                </tr>
              </thead>
              <tbody>
                {tampil.map((r) => {
                  const open = openId === r.id;
                  return (
                  <Fragment key={r.id}>
                  <tr
                    onClick={() => setOpenId(open ? null : r.id)}
                    style={{ cursor: "pointer" }}
                    title="Klik untuk lihat jawaban AI"
                  >
                    <td style={{ fontSize: 11.5, color: "var(--ink-500)", whiteSpace: "nowrap" }}>
                      {new Date(r.created_at).toLocaleString("id-ID", { dateStyle: "short", timeStyle: "short" })}
                    </td>
                    <td style={{ whiteSpace: "nowrap" }}>
                      <button
                        onClick={(e) => { e.stopPropagation(); setFilterUser(filterUser === r.username ? "" : r.username || ""); }}
                        title={r.role ? `Peran: ${r.role}` : undefined}
                        style={{
                          background: "transparent",
                          border: "none",
                          padding: 0,
                          cursor: "pointer",
                          fontSize: 12.5,
                          fontWeight: 600,
                          color: "var(--ink-800)",
                        }}
                      >
                        {r.username || "—"}
                      </button>
                      {r.role && (
                        <span
                          className="pill"
                          style={{ marginLeft: 6, height: 17, fontSize: 9.5, padding: "0 5px" }}
                        >
                          {r.role}
                        </span>
                      )}
                    </td>
                    <td style={{ maxWidth: 320, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={r.question || ""}>
                      {r.question || "—"}
                      {r.guard_hit && <span className="pill" style={{ marginLeft: 6, height: 18, fontSize: 10, padding: "0 6px" }}>guard</span>}
                      {r.tool_failed && <span className="pill" style={{ marginLeft: 4, height: 18, fontSize: 10, padding: "0 6px" }}>tool gagal</span>}
                    </td>
                    <td style={{ fontSize: 11, color: "var(--ink-600)" }} title={r.tools || ""}>
                      {r.tools_count > 0 ? `${r.tools_count} tool` : "—"}
                    </td>
                    <td className="num mono">{r.rounds}</td>
                    <td className="num mono">{(r.latency_ms / 1000).toFixed(1)}s</td>
                    <td
                      className="num mono"
                      style={{ fontSize: 11, whiteSpace: "nowrap" }}
                      title={
                        (r.tokens_in ?? 0) || (r.tokens_out ?? 0)
                          ? `Input ${(r.tokens_in ?? 0).toLocaleString("id-ID")} token (cache hit ${(r.tokens_cache_hit ?? 0).toLocaleString("id-ID")}) · output ${(r.tokens_out ?? 0).toLocaleString("id-ID")} token · ${r.api_calls ?? 0}x panggilan API`
                          : "Baris lama — token belum dicatat (migrasi 021)"
                      }
                    >
                      {(r.tokens_in ?? 0) || (r.tokens_out ?? 0)
                        ? `${fmtTok(r.tokens_in)} / ${fmtTok(r.tokens_out)}`
                        : "—"}
                    </td>
                    <td style={{ fontSize: 11.5, whiteSpace: "nowrap" }}>
                      {r.outcome || "—"}
                      <span style={{ marginLeft: 6, color: "var(--ink-400)" }}>{open ? "▾" : "▸"}</span>
                    </td>
                  </tr>
                  {open && (
                    <tr>
                      <td colSpan={8} style={{ background: "var(--surface-2, rgba(0,0,0,0.03))", padding: "10px 14px" }}>
                        <div style={{ fontSize: 11, fontWeight: 600, color: "var(--ink-500)", marginBottom: 3 }}>
                          Pertanyaan
                        </div>
                        <div style={{ fontSize: 12.5, color: "var(--ink-700)", marginBottom: 10, whiteSpace: "pre-wrap" }}>
                          {r.question || "—"}
                        </div>
                        {r.tools_failed && (
                          <div style={{ fontSize: 12, color: "var(--danger-600, #c0392b)", marginBottom: 10 }}>
                            <b>Tool gagal:</b>{" "}
                            <span className="mono">
                              {r.tools_failed.replaceAll(":nf", " (tak ketemu)").replaceAll(":err", " (error)")}
                            </span>
                          </div>
                        )}
                        <div style={{ fontSize: 11, fontWeight: 600, color: "var(--ink-500)", marginBottom: 3 }}>
                          Jawaban AI
                        </div>
                        <div
                          style={{
                            fontSize: 12.5,
                            color: "var(--ink-800)",
                            whiteSpace: "pre-wrap",
                            maxHeight: 340,
                            overflowY: "auto",
                            lineHeight: 1.5,
                          }}
                        >
                          {r.reply
                            ? r.reply
                            : "— (jawaban tak tersimpan untuk giliran ini — hanya giliran setelah fitur ini aktif yang menyimpan teks jawaban)"}
                        </div>
                      </td>
                    </tr>
                  )}
                  </Fragment>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </AppShell>
  );
}
