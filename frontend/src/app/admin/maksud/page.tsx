"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import AppShell from "@/components/AppShell";
import {
  ApiError,
  addMaksud,
  deleteMaksud,
  getMaksud,
  updateMaksud,
  type MaksudEntry,
} from "@/lib/api";
import { clearSession, getToken, getUser } from "@/lib/auth";

/** Pecah input textarea/koma jadi daftar frasa bersih. */
function splitTerms(raw: string): string[] {
  return raw
    .split(/[\n,;]+/)
    .map((s) => s.trim())
    .filter(Boolean);
}

export default function MaksudPage() {
  const router = useRouter();
  const [entries, setEntries] = useState<MaksudEntry[]>([]);
  const [tools, setTools] = useState<string[]>([]);
  const [maks, setMaks] = useState(60);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState("");

  // Form tambah/edit. editIdx = null → mode tambah.
  const [editIdx, setEditIdx] = useState<number | null>(null);
  const [frasaTxt, setFrasaTxt] = useState("");
  const [tool, setTool] = useState("");
  const [catatan, setCatatan] = useState("");
  const [saving, setSaving] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  const load = useCallback(async () => {
    const token = getToken();
    if (!token) return router.replace("/login");
    setLoading(true);
    setError(null);
    try {
      const res = await getMaksud(token);
      setEntries(res.entries);
      setTools(res.tools);
      setMaks(res.maks);
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        clearSession();
        return router.replace("/login");
      }
      setError(err instanceof Error ? err.message : "Gagal memuat rute");
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

  function resetForm() {
    setEditIdx(null);
    setFrasaTxt("");
    setTool("");
    setCatatan("");
  }

  function startEdit(idx: number) {
    const e = entries[idx];
    if (!e) return;
    setEditIdx(idx);
    setFrasaTxt(e.frasa.join(", "));
    setTool(e.tool);
    setCatatan(e.catatan || "");
    setNotice(null);
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  async function save() {
    const token = getToken();
    if (!token) return router.replace("/login");
    const entry: MaksudEntry = {
      frasa: splitTerms(frasaTxt),
      tool: tool.trim(),
      catatan: catatan.trim(),
    };
    if (entry.frasa.length === 0 || !entry.tool) {
      setError("Isi minimal satu frasa dan pilih alat tujuannya.");
      return;
    }
    setSaving(true);
    setError(null);
    setNotice(null);
    try {
      if (editIdx == null) await addMaksud(token, entry);
      else await updateMaksud(token, editIdx, entry);
      setNotice(
        `Tersimpan: "${entry.frasa[0]}" → ${entry.tool}. ` +
        "Asisten AI langsung mematuhinya (tanpa restart).",
      );
      resetForm();
      await load();
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        clearSession();
        return router.replace("/login");
      }
      setError(err instanceof Error ? err.message : "Gagal menyimpan");
    } finally {
      setSaving(false);
    }
  }

  async function remove(idx: number) {
    const e = entries[idx];
    if (!e) return;
    if (!window.confirm(`Hapus rute "${e.frasa.join(", ")}" → ${e.tool}?`)) return;
    const token = getToken();
    if (!token) return router.replace("/login");
    setError(null);
    try {
      await deleteMaksud(token, idx);
      if (editIdx === idx) resetForm();
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Gagal menghapus");
      load();
    }
  }

  // Filter live di klien (store kecil — plafon puluhan entri).
  const view = useMemo(() => {
    const q = filter.trim().toLowerCase();
    const withIdx = entries.map((e, i) => ({ e, i }));
    if (!q) return withIdx;
    return withIdx.filter(({ e }) =>
      e.tool.toLowerCase().includes(q) ||
      (e.catatan || "").toLowerCase().includes(q) ||
      e.frasa.some((f) => f.toLowerCase().includes(q)),
    );
  }, [entries, filter]);

  return (
    <AppShell active="/admin/maksud" title="Rute Maksud" sub="Istilah khas bengkel → alat yang dipakai Asisten AI">
      <div className="mx-auto w-full max-w-5xl px-4 py-6 sm:px-7">
        <p style={{ marginBottom: 16, fontSize: 13.5, color: "var(--ink-500)" }}>
          Kalau di tempat Anda sebuah istilah punya arti khusus, daftarkan di sini supaya Asisten AI
          langsung memakai <b>alat yang benar</b>. Contoh: <b>gambar teknis</b> → <b>gambar_exploded</b>.
          Bedanya dengan <b>Kamus Sinonim</b>: kamus mengubah <i>kata yang dicari</i>, rute mengubah{" "}
          <i>alat yang dipakai</i>. Perubahan <b>langsung aktif</b> — tidak perlu restart.
        </p>
        <p style={{ marginBottom: 16, fontSize: 12.5, color: "var(--ink-500)" }}>
          ⚠️ Hindari kata yang terlalu umum (&quot;gambar&quot;, &quot;part&quot;, &quot;cek&quot;) — rute
          seperti itu ikut campur di percakapan lain dan akan ditolak. Rute adalah <b>arahan kuat</b>,
          bukan paksaan: asisten tetap boleh memilih lain bila kalimat user jelas berkata lain.
          Anda juga bisa membuat rute langsung dari chat: <i>&quot;ingat ya, kalau saya minta gambar
          teknis itu maksudnya exploded view&quot;</i>.
        </p>

        {error && <div className="alert alert-error" style={{ marginBottom: 14 }}>{error}</div>}
        {notice && <div className="alert alert-success" style={{ marginBottom: 14 }}>{notice}</div>}

        {/* ── Form tambah / edit ── */}
        <div className="surface" style={{ padding: 18, marginBottom: 22 }}>
          <div style={{ fontSize: 13.5, fontWeight: 650, marginBottom: 12 }}>
            {editIdx == null ? "➕ Tambah rute" : `✏️ Edit rute #${editIdx + 1}`}
          </div>
          <div className="grid gap-4 sm:grid-cols-2">
            <section>
              <label className="block" style={{ marginBottom: 6, fontSize: 13, fontWeight: 600, color: "var(--ink-700)" }}>
                Frasa yang dipakai user — pisah koma atau baris baru
              </label>
              <textarea
                className="textarea"
                rows={3}
                value={frasaTxt}
                onChange={(e) => setFrasaTxt(e.target.value)}
                placeholder={"gambar teknis\ngambar urai"}
              />
            </section>
            <section>
              <label className="block" style={{ marginBottom: 6, fontSize: 13, fontWeight: 600, color: "var(--ink-700)" }}>
                Alat tujuan
              </label>
              <select className="input" value={tool} onChange={(e) => setTool(e.target.value)}>
                <option value="">— pilih alat —</option>
                {tools.map((t) => (
                  <option key={t} value={t}>{t}</option>
                ))}
              </select>
              <label className="block" style={{ margin: "12px 0 6px", fontSize: 13, fontWeight: 600, color: "var(--ink-700)" }}>
                Catatan pembeda (opsional, maks 160 huruf)
              </label>
              <input
                className="input"
                value={catatan}
                onChange={(e) => setCatatan(e.target.value)}
                placeholder="maksudnya exploded view, bukan foto part"
              />
            </section>
          </div>
          <div className="flex flex-wrap items-center gap-3" style={{ marginTop: 12 }}>
            <div style={{ fontSize: 12.5, color: "var(--ink-500)" }}>
              {entries.length} / {maks} rute terpakai
            </div>
            <div className="flex gap-2" style={{ marginLeft: "auto" }}>
              {editIdx != null && (
                <button className="btn btn-secondary" onClick={resetForm} disabled={saving}>
                  Batal edit
                </button>
              )}
              <button className="btn btn-primary" onClick={save} disabled={saving}>
                {saving ? "Menyimpan…" : editIdx == null ? "Simpan rute" : "Simpan perubahan"}
              </button>
            </div>
          </div>
        </div>

        {/* ── Daftar ── */}
        <div className="flex flex-wrap items-center gap-3" style={{ marginBottom: 12 }}>
          <input
            className="input"
            style={{ maxWidth: 320 }}
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            placeholder="Saring: frasa, alat, atau catatan…"
          />
          <div style={{ fontSize: 12.5, color: "var(--ink-500)" }}>
            {view.length} dari <b className="mono" style={{ color: "var(--ink-800)" }}>{entries.length}</b> rute
          </div>
          <button className="btn btn-secondary btn-sm" onClick={load} disabled={loading} style={{ marginLeft: "auto" }}>
            {loading ? "Memuat…" : "↻ Muat ulang"}
          </button>
        </div>

        {view.length > 0 ? (
          <div className="surface" style={{ overflow: "auto" }}>
            <table className="tbl">
              <thead>
                <tr>
                  <th>Frasa user</th>
                  <th style={{ width: 190 }}>Alat</th>
                  <th>Catatan</th>
                  <th style={{ width: 130 }} />
                </tr>
              </thead>
              <tbody>
                {view.map(({ e, i }) => (
                  <tr key={i}>
                    <td>
                      {e.frasa.map((f) => (
                        <span key={f} className="pill pill-brand" style={{ margin: "1px 4px 1px 0", height: 20, fontSize: 10.5, padding: "0 7px" }}>{f}</span>
                      ))}
                    </td>
                    <td className="mono" style={{ fontSize: 12 }}>{e.tool}</td>
                    <td style={{ fontSize: 12.5, color: "var(--ink-700)" }}>{e.catatan}</td>
                    <td style={{ textAlign: "right", whiteSpace: "nowrap" }}>
                      <button className="btn btn-secondary btn-sm" onClick={() => startEdit(i)} style={{ marginRight: 6 }}>
                        Edit
                      </button>
                      <button className="btn btn-danger btn-sm" onClick={() => remove(i)}>
                        Hapus
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          !loading && (
            <div className="surface grid place-items-center" style={{ height: 140, color: "var(--ink-500)", fontSize: 13.5 }}>
              {entries.length === 0 ? "Belum ada rute — tambah yang pertama di atas." : "Tidak ada rute yang cocok dengan saringan."}
            </div>
          )
        )}
      </div>
    </AppShell>
  );
}
