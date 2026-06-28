"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import AppShell from "@/components/AppShell";
import { ApiError, getAdminGudang, saveAdminGudang, type AdminGudang } from "@/lib/api";
import { clearSession, getToken, getUser } from "@/lib/auth";

type Row = AdminGudang & { coordText: string };

const coordTextOf = (it: AdminGudang): string =>
  it.lat != null && it.lon != null ? `${it.lat}, ${it.lon}` : "";

export default function AdminGudangPage() {
  const router = useRouter();
  const [items, setItems] = useState<Row[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [loaded, setLoaded] = useState(false);

  const load = useCallback(async () => {
    const token = getToken();
    if (!token) return router.replace("/login");
    try {
      const d = await getAdminGudang(token);
      setItems(d.gudang.map((it) => ({ ...it, coordText: coordTextOf(it) })));
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

  function patch(label: string, p: Partial<Row>) {
    setItems((arr) => arr.map((it) => (it.label === label ? { ...it, ...p } : it)));
  }

  // Parse "lat, lon" (atau "lat lon") → angka; biarkan teks apa adanya saat mengetik.
  function setCoord(label: string, text: string) {
    const parts = text.split(/[,\s]+/).filter(Boolean);
    const lat = numOrNull(parts[0] ?? "");
    const lon = numOrNull(parts[1] ?? "");
    patch(label, { coordText: text, lat, lon });
  }

  async function save() {
    const token = getToken();
    if (!token) return;
    setBusy(true);
    setError(null);
    setMsg(null);
    try {
      await saveAdminGudang(
        token,
        items.map((it) => ({
          label: it.label,
          lat: it.lat,
          lon: it.lon,
          selectable: it.selectable,
          key: it.key,
          pic: it.pic ?? "",
        })),
      );
      setMsg("Konfigurasi lokasi gudang tersimpan.");
      await load();
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

  const numOrNull = (s: string): number | null => {
    const t = s.trim();
    if (t === "" || t === "-") return null;
    const n = Number(t);
    return Number.isFinite(n) ? n : null;
  };

  return (
    <AppShell
      active="/admin/gudang"
      title="Lokasi Gudang"
      sub="Atur koordinat tiap gudang (penentu stok terdekat) & lokasi yang bisa dipilih pembeli"
      actions={
        <button onClick={save} disabled={busy} className="btn btn-primary btn-sm">
          {busy ? "Menyimpan…" : "Simpan"}
        </button>
      }
    >
      <div className="mx-auto w-full max-w-6xl px-4 py-5 sm:px-6">
        {msg && (
          <p className="mb-3 rounded-lg bg-green-50 px-3 py-2 text-sm text-green-700 ring-1 ring-green-100">{msg}</p>
        )}
        {error && (
          <p className="mb-3 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700 ring-1 ring-red-100">{error}</p>
        )}

        <div className="mb-3" style={{ fontSize: 12.5, color: "var(--ink-500)" }}>
          Koordinat (lat/lon) dipakai untuk menghitung <b>gudang terdekat</b> otomatis saat stok di
          gudang terpilih kosong. Centang <b>Pembeli</b> agar gudang muncul di pilihan lokasi pembeli,
          lalu isi <b>Key/Akun</b> (username akun cabang untuk routing pesanan).
        </div>

        {loaded && items.length === 0 && !error ? (
          <div className="surface grid place-items-center" style={{ height: 180, color: "var(--ink-500)" }}>
            Belum ada data gudang.
          </div>
        ) : (
          <div className="surface" style={{ overflow: "auto" }}>
            <table className="tbl">
              <thead>
                <tr>
                  <th>Gudang</th>
                  <th style={{ width: 230 }}>Koordinat (lat, lon)</th>
                  <th style={{ width: 70 }}>Pembeli</th>
                  <th style={{ width: 140 }}>Key / Akun</th>
                  <th style={{ width: 150 }}>No. PIC</th>
                  <th>Terdekat (otomatis)</th>
                </tr>
              </thead>
              <tbody>
                {items.map((it) => (
                  <tr key={it.label}>
                    <td>
                      <div style={{ fontWeight: 550 }}>{it.display}</div>
                      <div className="mono" style={{ fontSize: 11, color: "var(--ink-400)" }}>{it.label}</div>
                    </td>
                    <td>
                      <input
                        className="input mono"
                        style={{ width: 210, height: 32 }}
                        value={it.coordText}
                        placeholder="-6.21, 106.85"
                        onChange={(e) => setCoord(it.label, e.target.value)}
                      />
                    </td>
                    <td style={{ textAlign: "center" }}>
                      <input
                        type="checkbox"
                        checked={it.selectable}
                        onChange={(e) => patch(it.label, { selectable: e.target.checked })}
                      />
                    </td>
                    <td>
                      <input
                        className="input"
                        style={{ width: 130, height: 32 }}
                        value={it.key ?? ""}
                        placeholder="mis. jakarta"
                        disabled={!it.selectable}
                        onChange={(e) => patch(it.label, { key: e.target.value.trim().toLowerCase() })}
                      />
                    </td>
                    <td>
                      <input
                        className="input mono"
                        style={{ width: 140, height: 32 }}
                        value={it.pic ?? ""}
                        placeholder="08xxxxxxxxxx"
                        onChange={(e) => patch(it.label, { pic: e.target.value })}
                      />
                    </td>
                    <td style={{ color: "var(--ink-500)", fontSize: 12 }}>
                      {it.nearest.length ? it.nearest.join(" · ") : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        <p style={{ fontSize: 11.5, color: "var(--ink-400)", marginTop: 10 }}>
          Kolom “Terdekat” diperbarui setelah disimpan (mengikuti koordinat baru).
        </p>
      </div>
    </AppShell>
  );
}
