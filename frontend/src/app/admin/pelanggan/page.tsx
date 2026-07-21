"use client";

// Panel admin: tautkan AKUN → PELANGGAN ACCURATE.
// Penawaran Accurate otomatis (saat order lunas) memakai tautan ini — bukan
// nama penerima yang diketik pembeli di form pengiriman. Satu kali tautkan,
// selamanya benar; kalau belum ditautkan, penawaran di-skip dengan alasan jelas.
import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import AppShell from "@/components/AppShell";
import {
  ApiError, cariPelangganAccurate, getTautPelanggan, tautPelanggan,
  type AccurateCustomer, type TautPelangganRow,
} from "@/lib/api";
import { clearSession, getToken, getUser } from "@/lib/auth";

export default function AdminPelangganPage() {
  const router = useRouter();
  const [rows, setRows] = useState<TautPelangganRow[]>([]);
  const [siap, setSiap] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);

  // Pencarian pelanggan untuk baris yang sedang diedit.
  const [editing, setEditing] = useState<string | null>(null);
  const [q, setQ] = useState("");
  const [hasil, setHasil] = useState<AccurateCustomer[]>([]);
  const [mencari, setMencari] = useState(false);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    const token = getToken();
    if (!token) return router.replace("/login");
    try {
      const d = await getTautPelanggan(token);
      setSiap(d.siap);
      setRows(d.users || []);
      if (!d.siap && d.error) setError(d.error);
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

  async function cari() {
    const token = getToken();
    if (!token || !q.trim()) return;
    setMencari(true);
    setError(null);
    try {
      const d = await cariPelangganAccurate(token, q.trim());
      setHasil(d.customers || []);
      if (!d.configured) setError("Accurate belum aktif — tak bisa mencari pelanggan.");
      else if (!d.customers?.length) setError(`Tidak ada pelanggan cocok "${q.trim()}".`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Pencarian gagal");
    } finally {
      setMencari(false);
    }
  }

  async function simpan(username: string, c: AccurateCustomer | null) {
    const token = getToken();
    if (!token) return router.replace("/login");
    setBusy(true);
    setError(null);
    setMsg(null);
    try {
      await tautPelanggan(token, {
        username,
        customer_id: c ? c.id : null,
        customer_name: c?.name ?? "",
        customer_no: c?.no ?? "",
      });
      setMsg(c ? `${username} → ${c.name}` : `Tautan ${username} dilepas.`);
      setEditing(null);
      setQ("");
      setHasil([]);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Gagal menyimpan");
    } finally {
      setBusy(false);
    }
  }

  const pembeli = rows.filter((r) => r.role === "pembeli");
  const lain = rows.filter((r) => r.role !== "pembeli");

  const tabel = (daftar: TautPelangganRow[]) => (
    <div style={{ overflowX: "auto" }}>
      <table className="table" style={{ width: "100%" }}>
        <thead>
          <tr>
            <th style={{ textAlign: "left" }}>Akun</th>
            <th style={{ textAlign: "left" }}>Pelanggan Accurate</th>
            <th style={{ textAlign: "right" }}>Aksi</th>
          </tr>
        </thead>
        <tbody>
          {daftar.map((r) => (
            <tr key={r.username}>
              <td>
                <strong>{r.username}</strong>
                <div style={{ fontSize: 12, color: "var(--ink-500)" }}>
                  {r.role}
                  {!r.is_active && " · nonaktif"}
                </div>
              </td>
              <td>
                {r.customer_id ? (
                  <>
                    {r.customer_name || `#${r.customer_id}`}
                    <div style={{ fontSize: 12, color: "var(--ink-500)" }}>
                      {r.customer_no ? `${r.customer_no} · ` : ""}id {r.customer_id}
                    </div>
                  </>
                ) : (
                  <span style={{ color: "var(--danger, #b42318)" }}>
                    belum ditautkan — penawaran otomatis di-skip
                  </span>
                )}
              </td>
              <td style={{ textAlign: "right", whiteSpace: "nowrap" }}>
                <button
                  className="btn btn-secondary btn-sm"
                  disabled={busy}
                  onClick={() => {
                    setEditing(editing === r.username ? null : r.username);
                    setQ(r.customer_name || "");
                    setHasil([]);
                    setError(null);
                  }}
                >
                  {r.customer_id ? "Ubah" : "Tautkan"}
                </button>
                {r.customer_id && (
                  <button
                    className="btn btn-ghost btn-sm"
                    style={{ marginLeft: 6 }}
                    disabled={busy}
                    onClick={() => simpan(r.username, null)}
                  >
                    Lepas
                  </button>
                )}
              </td>
            </tr>
          ))}
          {!daftar.length && (
            <tr>
              <td colSpan={3} style={{ color: "var(--ink-500)" }}>Tidak ada akun.</td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );

  return (
    <AppShell
      active="/admin/pelanggan"
      title="Pelanggan Accurate"
      sub="Tautkan akun ke pelanggan Accurate — dipakai penawaran otomatis saat pesanan lunas"
    >
      <div className="mx-auto w-full max-w-4xl px-4 py-6 sm:px-6">
        {error && <div className="alert alert-error" style={{ marginBottom: 14 }}>{error}</div>}
        {msg && <div className="alert alert-success" style={{ marginBottom: 14 }}>{msg}</div>}

        <div className="surface surface-pad" style={{ marginBottom: 14, fontSize: 13.5, color: "var(--ink-600)" }}>
          Saat pesanan lunas, sistem membuat Penawaran Penjualan di Accurate secara
          otomatis. Pelanggannya diambil dari tautan di halaman ini — <strong>bukan</strong> dari
          nama penerima yang diketik pembeli, karena nama itu berubah-ubah tiap pesanan.
          Akun yang belum ditautkan tetap bisa berbelanja; hanya penawaran otomatisnya
          yang dilewati.
        </div>

        {!loaded ? (
          <div className="surface grid place-items-center" style={{ height: 160, color: "var(--ink-500)" }}>Memuat…</div>
        ) : !siap ? (
          <div className="surface surface-pad">
            Kolom tautan belum ada di database. Jalankan{" "}
            <code>migrations/024_users_accurate_customer.sql</code> di Supabase, lalu muat ulang halaman ini.
          </div>
        ) : (
          <div className="flex flex-col gap-4">
            {editing && (
              <section className="surface surface-pad">
                <div style={{ fontWeight: 600, marginBottom: 8 }}>
                  Pilih pelanggan Accurate untuk <code>{editing}</code>
                </div>
                <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                  <input
                    className="input"
                    style={{ flex: "1 1 240px" }}
                    placeholder="Ketik nama pelanggan (mis. PT …)"
                    value={q}
                    onChange={(e) => setQ(e.target.value)}
                    onKeyDown={(e) => e.key === "Enter" && cari()}
                  />
                  <button className="btn btn-primary" disabled={mencari || !q.trim()} onClick={cari}>
                    {mencari ? "Mencari…" : "Cari"}
                  </button>
                  <button className="btn btn-ghost" onClick={() => { setEditing(null); setHasil([]); }}>
                    Batal
                  </button>
                </div>
                {!!hasil.length && (
                  <ul style={{ marginTop: 12, display: "flex", flexDirection: "column", gap: 6 }}>
                    {hasil.map((c) => (
                      <li key={c.id} style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 10 }}>
                        <span>
                          {c.name}
                          <span style={{ fontSize: 12, color: "var(--ink-500)" }}>
                            {c.no ? ` · ${c.no}` : ""} · id {c.id}
                          </span>
                        </span>
                        <button className="btn btn-primary btn-sm" disabled={busy} onClick={() => simpan(editing, c)}>
                          Pilih
                        </button>
                      </li>
                    ))}
                  </ul>
                )}
              </section>
            )}

            <section className="surface surface-pad">
              <div style={{ fontWeight: 600, marginBottom: 8 }}>Akun pembeli</div>
              {tabel(pembeli)}
            </section>

            <section className="surface surface-pad">
              <div style={{ fontWeight: 600, marginBottom: 8 }}>Akun lain</div>
              <div style={{ fontSize: 12.5, color: "var(--ink-500)", marginBottom: 8 }}>
                Hanya perlu ditautkan bila akun ini juga memesan lewat toko.
              </div>
              {tabel(lain)}
            </section>
          </div>
        )}
      </div>
    </AppShell>
  );
}
