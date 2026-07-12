"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import AppShell from "@/components/AppShell";
import { ApiError, geoReverse, getAdminGudang, saveAdminGudang, type AdminGudang } from "@/lib/api";
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

  // Auto-isi kode pos dari koordinat (reverse-geocoding OSM via backend).
  const postalTimers = useRef<Record<string, ReturnType<typeof setTimeout>>>({});

  const lookupPostal = useCallback(async (label: string, lat: number, lon: number) => {
    const token = getToken();
    if (!token) return;
    try {
      const r = await geoReverse(token, lat, lon);
      if (r.postal) {
        setItems((arr) => arr.map((it) => (it.label === label ? { ...it, origin_postal: r.postal } : it)));
      }
    } catch {
      /* biarkan — kode pos bisa diisi manual */
    }
  }, []);

  const load = useCallback(async () => {
    const token = getToken();
    if (!token) return router.replace("/login");
    try {
      const d = await getAdminGudang(token);
      setItems(d.gudang.map((it) => ({ ...it, coordText: coordTextOf(it) })));
      // Isi otomatis kode pos yang masih KOSONG untuk SEMUA gudang berkoordinat —
      // bukan hanya lokasi pilihan pembeli: gudang pemenuh (fallback terdekat) juga
      // mengirim barang, dan tanpa kode pos ongkir dari gudang itu ditolak server.
      // Berurutan dengan jeda — Nominatim membatasi laju permintaan.
      const kosong = d.gudang.filter((it) => !it.origin_postal && it.lat != null && it.lon != null);
      for (let i = 0; i < kosong.length; i++) {
        const it = kosong[i];
        setTimeout(() => lookupPostal(it.label, it.lat as number, it.lon as number), i * 1200);
      }
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
  // Koordinat valid → kode pos diisi OTOMATIS dari koordinat itu (debounce 800 ms).
  function setCoord(label: string, text: string) {
    const parts = text.split(/[,\s]+/).filter(Boolean);
    const lat = numOrNull(parts[0] ?? "");
    const lon = numOrNull(parts[1] ?? "");
    patch(label, { coordText: text, lat, lon });
    if (postalTimers.current[label]) clearTimeout(postalTimers.current[label]);
    if (lat != null && lon != null) {
      postalTimers.current[label] = setTimeout(() => lookupPostal(label, lat, lon), 800);
    }
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
          origin_postal: it.origin_postal ?? "",
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
          gudang terpilih kosong. <b>Kode Pos</b> = titik ASAL hitung ongkir, <b>wajib untuk SEMUA
          gudang</b> (bukan cuma yang bisa dipilih pembeli) — gudang terdekat juga ikut mengirim
          barang, dan tanpa kode pos ongkir dari gudang itu ditolak. Terisi otomatis dari koordinat,
          boleh dikoreksi manual. Centang <b>Pembeli</b> agar gudang muncul di pilihan lokasi pembeli,
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
                  <th style={{ width: 110 }}>Kode Pos</th>
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
                    <td>
                      <input
                        className="input mono"
                        style={{ width: 96, height: 32 }}
                        value={it.origin_postal ?? ""}
                        placeholder="otomatis"
                        title="Kode pos ASAL ongkir — terisi otomatis dari koordinat; boleh dikoreksi manual"
                        onChange={(e) => patch(it.label, { origin_postal: e.target.value.replace(/\D/g, "").slice(0, 10) })}
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
