"use client";

// Panel admin: atur metadata aplikasi mobile TANPA rebuild/deploy.
//  - Versi: memicu notifikasi update in-app (aplikasi cek /api/app/meta saat dibuka).
//  - Config: feature-flag yang dibaca aplikasi (saran Asisten, default Cari-by-Foto,
//    limit Cari Part). Perubahan berlaku saat aplikasi dibuka berikutnya.
import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import AppShell from "@/components/AppShell";
import { ApiError, getAdminAppConfig, saveAdminAppConfig, type AppMetaVersion } from "@/lib/api";
import { clearSession, getToken, getUser } from "@/lib/auth";

type Foto = { tta_default: boolean; top_k: number; threshold: number };
type Search = { page_size: number; fetch_size: number; max_fetch: number };

const num = (v: unknown, d: number): number => {
  const n = Number(v);
  return Number.isFinite(n) ? n : d;
};

export default function AdminAppConfigPage() {
  const router = useRouter();
  const [version, setVersion] = useState<AppMetaVersion>({
    latest_code: 0, latest_name: "", min_code: 0, min_name: "",
    download_url: "https://maspart.tech/download", force: false,
  });
  const [suggestions, setSuggestions] = useState<string[]>([]);
  const [foto, setFoto] = useState<Foto>({ tta_default: true, top_k: 20, threshold: 0.3 });
  const [search, setSearch] = useState<Search>({ page_size: 20, fetch_size: 200, max_fetch: 2000 });
  const [rawConfig, setRawConfig] = useState<Record<string, unknown>>({});
  const [error, setError] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [loaded, setLoaded] = useState(false);

  const load = useCallback(async () => {
    const token = getToken();
    if (!token) return router.replace("/login");
    try {
      const d = await getAdminAppConfig(token);
      setVersion((v) => ({ ...v, ...d.version }));
      const c = d.config || {};
      setRawConfig(c);
      if (Array.isArray(c.asisten_suggestions)) {
        setSuggestions((c.asisten_suggestions as unknown[]).map(String));
      }
      const f = (c.foto ?? {}) as Partial<Foto>;
      setFoto({ tta_default: f.tta_default ?? true, top_k: num(f.top_k, 20), threshold: num(f.threshold, 0.3) });
      const s = (c.search ?? {}) as Partial<Search>;
      setSearch({ page_size: num(s.page_size, 20), fetch_size: num(s.fetch_size, 200), max_fetch: num(s.max_fetch, 2000) });
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        clearSession();
        return router.replace("/login");
      }
      if (err instanceof ApiError && err.status === 403) return router.replace("/search");
      setError(err instanceof Error ? err.message : "Gagal memuat");
    } finally {
      setLoaded(true);
    }
  }, [router]);

  useEffect(() => {
    if (getUser()?.role !== "admin") {
      router.replace("/search");
      return;
    }
    load();
  }, [router, load]);

  async function save() {
    const token = getToken();
    if (!token) return router.replace("/login");
    setBusy(true);
    setError(null);
    setMsg(null);
    try {
      // Pertahankan kunci config lain yang tak diedit di panel ini.
      const config = {
        ...rawConfig,
        asisten_suggestions: suggestions.map((s) => s.trim()).filter(Boolean),
        foto,
        search,
      };
      const d = await saveAdminAppConfig(token, { version, config });
      setRawConfig(d.config || {});
      setMsg("Tersimpan. Perubahan berlaku saat aplikasi dibuka berikutnya (tanpa rebuild).");
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        clearSession();
        return router.replace("/login");
      }
      setError(err instanceof Error ? err.message : "Gagal menyimpan");
    } finally {
      setBusy(false);
    }
  }

  return (
    <AppShell active="/admin/app-config" title="Config Aplikasi" sub="Versi APK & feature-flag aplikasi mobile — tanpa rebuild">
      <div className="mx-auto w-full max-w-3xl px-4 py-6 sm:px-6">
        {error && <div className="alert alert-error" style={{ marginBottom: 14 }}>{error}</div>}
        {msg && <div className="alert alert-success" style={{ marginBottom: 14 }}>{msg}</div>}

        {!loaded ? (
          <div className="surface grid place-items-center" style={{ height: 160, color: "var(--ink-500)" }}>Memuat…</div>
        ) : (
          <div className="flex flex-col gap-4">
            {/* ── Versi (notifikasi update in-app) ── */}
            <section className="surface surface-pad">
              <div style={{ fontSize: 14, fontWeight: 650, marginBottom: 4 }}>Versi aplikasi</div>
              <p style={{ fontSize: 12.5, color: "var(--ink-500)", marginBottom: 12 }}>
                Cukup isi <b>nomor versi</b> (mis. <span className="mono">2.1.4</span>). Aplikasi membandingkannya dengan versinya
                sendiri; bila ada yang lebih baru, muncul halaman ajakan unduh. Kosongkan <b>Versi terbaru</b> untuk menonaktifkan notifikasi.
              </p>
              <div className="grid gap-3" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))" }}>
                <label className="flex flex-col gap-1">
                  <span className="stat-label">Versi terbaru</span>
                  <input className="input" value={version.latest_name} placeholder="2.1.4"
                    onChange={(e) => setVersion({ ...version, latest_name: e.target.value })} />
                </label>
                <label className="flex flex-col gap-1">
                  <span className="stat-label">Versi minimum (untuk force)</span>
                  <input className="input" value={version.min_name} placeholder="2.1.0"
                    onChange={(e) => setVersion({ ...version, min_name: e.target.value })} />
                </label>
              </div>
              <label className="flex flex-col gap-1" style={{ marginTop: 12 }}>
                <span className="stat-label">Link unduh</span>
                <input className="input" value={version.download_url}
                  onChange={(e) => setVersion({ ...version, download_url: e.target.value })} />
              </label>
              <label className="flex items-center gap-2" style={{ marginTop: 12, fontSize: 13.5 }}>
                <input type="checkbox" checked={version.force}
                  onChange={(e) => setVersion({ ...version, force: e.target.checked })} />
                <span><b>Force update</b> — versi di bawah <b>Versi minimum</b> diblokir (wajib update, tak bisa ditutup).</span>
              </label>
            </section>

            {/* ── Saran Asisten ── */}
            <section className="surface surface-pad">
              <div style={{ fontSize: 14, fontWeight: 650, marginBottom: 4 }}>Saran Asisten AI</div>
              <p style={{ fontSize: 12.5, color: "var(--ink-500)", marginBottom: 12 }}>
                Chip contoh pertanyaan di layar Asisten (saat chat kosong). Kosongkan semua → aplikasi pakai bawaan.
              </p>
              <div className="flex flex-col gap-2">
                {suggestions.map((s, i) => (
                  <div key={i} className="flex items-center gap-2">
                    <input className="input grow" value={s}
                      onChange={(e) => setSuggestions((arr) => arr.map((x, j) => (j === i ? e.target.value : x)))} />
                    <button className="btn btn-secondary btn-sm" onClick={() => setSuggestions((arr) => arr.filter((_, j) => j !== i))} title="Hapus">✕</button>
                  </div>
                ))}
                <button className="btn btn-secondary btn-sm" style={{ alignSelf: "flex-start" }}
                  onClick={() => setSuggestions((arr) => [...arr, ""])}>+ Tambah saran</button>
              </div>
            </section>

            {/* ── Default Cari by Foto ── */}
            <section className="surface surface-pad">
              <div style={{ fontSize: 14, fontWeight: 650, marginBottom: 12 }}>Default Cari by Foto</div>
              <div className="grid gap-3" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(160px, 1fr))" }}>
                <label className="flex flex-col gap-1">
                  <span className="stat-label">top_k (jumlah kandidat)</span>
                  <input className="input" type="number" value={foto.top_k}
                    onChange={(e) => setFoto({ ...foto, top_k: num(e.target.value, 20) })} />
                </label>
                <label className="flex flex-col gap-1">
                  <span className="stat-label">threshold (0–1)</span>
                  <input className="input" type="number" step="0.05" value={foto.threshold}
                    onChange={(e) => setFoto({ ...foto, threshold: num(e.target.value, 0.3) })} />
                </label>
              </div>
              <label className="flex items-center gap-2" style={{ marginTop: 12, fontSize: 13.5 }}>
                <input type="checkbox" checked={foto.tta_default}
                  onChange={(e) => setFoto({ ...foto, tta_default: e.target.checked })} />
                <span>Mode akurat (TTA) nyala secara default</span>
              </label>
            </section>

            {/* ── Limit Cari Part ── */}
            <section className="surface surface-pad">
              <div style={{ fontSize: 14, fontWeight: 650, marginBottom: 12 }}>Limit Cari Part</div>
              <div className="grid gap-3" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))" }}>
                <label className="flex flex-col gap-1">
                  <span className="stat-label">page_size</span>
                  <input className="input" type="number" value={search.page_size}
                    onChange={(e) => setSearch({ ...search, page_size: num(e.target.value, 20) })} />
                </label>
                <label className="flex flex-col gap-1">
                  <span className="stat-label">fetch_size</span>
                  <input className="input" type="number" value={search.fetch_size}
                    onChange={(e) => setSearch({ ...search, fetch_size: num(e.target.value, 200) })} />
                </label>
                <label className="flex flex-col gap-1">
                  <span className="stat-label">max_fetch</span>
                  <input className="input" type="number" value={search.max_fetch}
                    onChange={(e) => setSearch({ ...search, max_fetch: num(e.target.value, 2000) })} />
                </label>
              </div>
            </section>

            <div className="flex items-center gap-2">
              <button className="btn btn-primary" disabled={busy} onClick={save}>
                {busy ? "Menyimpan…" : "Simpan"}
              </button>
              <button className="btn btn-secondary" disabled={busy} onClick={load}>Muat ulang</button>
            </div>
          </div>
        )}
      </div>
    </AppShell>
  );
}
