"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import AppShell from "@/components/AppShell";
import {
  ApiError,
  addSinonim,
  deleteSinonim,
  getSinonim,
  updateSinonim,
  type SinonimEntry,
} from "@/lib/api";
import { clearSession, getToken, getUser } from "@/lib/auth";

/** Pecah input textarea/koma jadi daftar istilah bersih. */
function splitTerms(raw: string): string[] {
  return raw
    .split(/[\n,;]+/)
    .map((s) => s.trim())
    .filter(Boolean);
}

export default function SinonimPage() {
  const router = useRouter();
  const [entries, setEntries] = useState<SinonimEntry[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState("");

  // Form tambah/edit. editIdx = null → mode tambah.
  const [editIdx, setEditIdx] = useState<number | null>(null);
  const [grup, setGrup] = useState("");
  const [trigTxt, setTrigTxt] = useState("");
  const [kwTxt, setKwTxt] = useState("");
  const [saving, setSaving] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  const load = useCallback(async () => {
    const token = getToken();
    if (!token) return router.replace("/login");
    setLoading(true);
    setError(null);
    try {
      const res = await getSinonim(token);
      setEntries(res.entries);
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        clearSession();
        return router.replace("/login");
      }
      setError(err instanceof Error ? err.message : "Gagal memuat kamus");
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
    // Prefill dari halaman Pencarian Nihil: /admin/sinonim?trigger=tapak+shoe
    try {
      const t = new URLSearchParams(window.location.search).get("trigger");
      if (t) setTrigTxt(t);
    } catch {
      /* ignore */
    }
    load();
  }, [router, load]);

  function resetForm() {
    setEditIdx(null);
    setGrup("");
    setTrigTxt("");
    setKwTxt("");
  }

  function startEdit(idx: number) {
    const e = entries[idx];
    if (!e) return;
    setEditIdx(idx);
    setGrup(e.grup);
    setTrigTxt(e.triggers.join(", "));
    setKwTxt(e.keywords.join(", "));
    setNotice(null);
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  async function save() {
    const token = getToken();
    if (!token) return router.replace("/login");
    const entry: SinonimEntry = {
      grup: grup.trim(),
      triggers: splitTerms(trigTxt),
      keywords: splitTerms(kwTxt),
    };
    if (entry.triggers.length === 0 || entry.keywords.length === 0) {
      setError("Isi minimal satu istilah lapangan dan satu kata kunci katalog.");
      return;
    }
    setSaving(true);
    setError(null);
    setNotice(null);
    try {
      if (editIdx == null) await addSinonim(token, entry);
      else await updateSinonim(token, editIdx, entry);
      setNotice(
        `Tersimpan: "${entry.triggers[0]}" → ${entry.keywords.join(", ")}. ` +
        "Asisten AI langsung memakai kamus baru (tanpa restart).",
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
    if (!window.confirm(`Hapus entri "${e.triggers.join(", ")}" → ${e.keywords.join(", ")}?`)) return;
    const token = getToken();
    if (!token) return router.replace("/login");
    setError(null);
    try {
      await deleteSinonim(token, idx);
      if (editIdx === idx) resetForm();
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Gagal menghapus");
      load();
    }
  }

  // Filter live di klien (kamus kecil — puluhan/ratusan entri).
  const view = useMemo(() => {
    const q = filter.trim().toLowerCase();
    const withIdx = entries.map((e, i) => ({ e, i }));
    if (!q) return withIdx;
    return withIdx.filter(({ e }) =>
      e.grup.toLowerCase().includes(q) ||
      e.triggers.some((t) => t.toLowerCase().includes(q)) ||
      e.keywords.some((k) => k.toLowerCase().includes(q)),
    );
  }, [entries, filter]);

  return (
    <AppShell active="/admin/sinonim" title="Kamus Sinonim" sub="Istilah lapangan → kata kunci katalog — langsung dipakai Asisten AI">
      <div className="mx-auto w-full max-w-5xl px-4 py-6 sm:px-7">
        <p style={{ marginBottom: 16, fontSize: 13.5, color: "var(--ink-500)" }}>
          Istilah bengkel/Indonesia di kolom kiri membuat Asisten AI &amp; Cari Part otomatis mencari
          kata kunci katalog (Inggris) di kolom kanan. Contoh: <b>tapak shoe</b> → <b>track plate</b>.
          Perubahan <b>langsung aktif</b> — tidak perlu restart.
        </p>

        {error && <div className="alert alert-error" style={{ marginBottom: 14 }}>{error}</div>}
        {notice && <div className="alert alert-success" style={{ marginBottom: 14 }}>{notice}</div>}

        {/* ── Form tambah / edit ── */}
        <div className="surface" style={{ padding: 18, marginBottom: 22 }}>
          <div style={{ fontSize: 13.5, fontWeight: 650, marginBottom: 12 }}>
            {editIdx == null ? "➕ Tambah sinonim" : `✏️ Edit entri #${editIdx + 1}`}
          </div>
          <div className="grid gap-4 sm:grid-cols-2">
            <section>
              <label className="block" style={{ marginBottom: 6, fontSize: 13, fontWeight: 600, color: "var(--ink-700)" }}>
                Istilah lapangan (Indonesia/slang) — pisah koma atau baris baru
              </label>
              <textarea
                className="textarea"
                rows={3}
                value={trigTxt}
                onChange={(e) => setTrigTxt(e.target.value)}
                placeholder={"tapak shoe\ntapak sepatu"}
              />
            </section>
            <section>
              <label className="block" style={{ marginBottom: 6, fontSize: 13, fontWeight: 600, color: "var(--ink-700)" }}>
                Kata kunci katalog (Inggris) — pisah koma atau baris baru
              </label>
              <textarea
                className="textarea"
                rows={3}
                value={kwTxt}
                onChange={(e) => setKwTxt(e.target.value)}
                placeholder={"track plate\ntrack shoe"}
              />
            </section>
          </div>
          <div className="flex flex-wrap items-end gap-3" style={{ marginTop: 12 }}>
            <section style={{ flex: "0 0 220px" }}>
              <label className="block" style={{ marginBottom: 6, fontSize: 13, fontWeight: 600, color: "var(--ink-700)" }}>
                Grup (opsional, mis. undercarriage)
              </label>
              <input
                className="input"
                value={grup}
                onChange={(e) => setGrup(e.target.value)}
                placeholder="umum"
              />
            </section>
            <div className="flex gap-2" style={{ marginLeft: "auto" }}>
              {editIdx != null && (
                <button className="btn btn-secondary" onClick={resetForm} disabled={saving}>
                  Batal edit
                </button>
              )}
              <button className="btn btn-primary" onClick={save} disabled={saving}>
                {saving ? "Menyimpan…" : editIdx == null ? "Simpan sinonim" : "Simpan perubahan"}
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
            placeholder="Saring: istilah, kata kunci, atau grup…"
          />
          <div style={{ fontSize: 12.5, color: "var(--ink-500)" }}>
            {view.length} dari <b className="mono" style={{ color: "var(--ink-800)" }}>{entries.length}</b> entri
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
                  <th style={{ width: 110 }}>Grup</th>
                  <th>Istilah lapangan</th>
                  <th>Kata kunci katalog</th>
                  <th style={{ width: 130 }} />
                </tr>
              </thead>
              <tbody>
                {view.map(({ e, i }) => (
                  <tr key={i}>
                    <td><span className="pill" style={{ height: 20, fontSize: 10.5, padding: "0 7px" }}>{e.grup || "umum"}</span></td>
                    <td>
                      {e.triggers.map((t) => (
                        <span key={t} className="pill pill-brand" style={{ margin: "1px 4px 1px 0", height: 20, fontSize: 10.5, padding: "0 7px" }}>{t}</span>
                      ))}
                    </td>
                    <td style={{ fontSize: 12.5, color: "var(--ink-700)" }}>{e.keywords.join(", ")}</td>
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
              {entries.length === 0 ? "Kamus masih kosong — tambah sinonim pertama di atas." : "Tidak ada entri yang cocok dengan saringan."}
            </div>
          )
        )}
      </div>
    </AppShell>
  );
}
