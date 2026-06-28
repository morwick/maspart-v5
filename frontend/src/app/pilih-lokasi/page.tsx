"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import {
  ApiError,
  getBuyerLocations,
  setBuyerLocation,
  type BuyerLocation,
} from "@/lib/api";
import { clearSession, getToken, getUser, setUserGudang } from "@/lib/auth";

export default function PilihLokasiPage() {
  const router = useRouter();
  const [locations, setLocations] = useState<BuyerLocation[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    const token = getToken();
    if (!token) {
      router.replace("/login");
      return;
    }
    const u = getUser();
    if (u && u.role !== "pembeli") {
      // Hanya akun pembeli yang memilih lokasi.
      router.replace("/search");
      return;
    }
    setSelected(u?.gudang ?? null);
    getBuyerLocations(token)
      .then((d) => setLocations(d.locations))
      .catch((err) => {
        if (err instanceof ApiError && err.status === 401) {
          clearSession();
          return router.replace("/login");
        }
        setError(err instanceof Error ? err.message : "Gagal memuat lokasi");
      })
      .finally(() => setLoading(false));
  }, [router]);

  async function confirm() {
    const token = getToken();
    if (!token || !selected) return;
    setSaving(true);
    setError(null);
    try {
      const res = await setBuyerLocation(token, selected);
      setUserGudang(res.key);
      router.replace("/search");
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        clearSession();
        return router.replace("/login");
      }
      setError(err instanceof Error ? err.message : "Gagal menyimpan lokasi");
    } finally {
      setSaving(false);
    }
  }

  return (
    <main className="grid min-h-screen place-items-center p-4" style={{ background: "var(--canvas)" }}>
      <div
        className="w-full"
        style={{
          maxWidth: 560,
          borderRadius: "var(--r-xl)",
          boxShadow: "var(--shadow-3)",
          background: "var(--paper)",
          border: "1px solid var(--ink-150)",
          padding: 28,
        }}
      >
        <div className="mb-1 flex items-center gap-2.5">
          <div
            className="mono grid place-items-center"
            style={{ width: 30, height: 30, borderRadius: 8, background: "var(--brand-600)", color: "#fff", fontWeight: 700, fontSize: 14 }}
          >
            M
          </div>
          <span style={{ fontWeight: 650, fontSize: 16 }}>MasPart</span>
        </div>

        <h1 style={{ fontSize: 21, fontWeight: 650, marginTop: 14, letterSpacing: "-0.01em" }}>
          Pilih lokasi terdekatmu
        </h1>
        <p style={{ fontSize: 13, color: "var(--ink-500)", marginTop: 4, marginBottom: 18 }}>
          Pilih kota yang paling dekat denganmu. Ketersediaan stok, harga, dan ongkir
          akan menyesuaikan lokasi ini. Kamu bisa menggantinya kapan saja.
        </p>

        {error && <div className="alert alert-error" style={{ marginBottom: 14 }}>{error}</div>}

        {loading ? (
          <div className="grid place-items-center" style={{ height: 160, color: "var(--ink-500)", fontSize: 13 }}>
            Memuat lokasi…
          </div>
        ) : (
          <>
            <div className="grid gap-2 sm:grid-cols-2">
              {locations.map((loc) => {
                const active = selected === loc.key;
                return (
                  <button
                    key={loc.key}
                    onClick={() => setSelected(loc.key)}
                    className="flex items-center gap-2 rounded-lg px-3 py-2.5 text-left"
                    style={{
                      border: "1px solid " + (active ? "var(--brand-600)" : "var(--ink-200)"),
                      background: active ? "var(--brand-50)" : "var(--paper)",
                    }}
                  >
                    <span
                      className="grid place-items-center rounded-full"
                      style={{
                        width: 18, height: 18, flexShrink: 0,
                        border: "2px solid " + (active ? "var(--brand-600)" : "var(--ink-300)"),
                        background: active ? "var(--brand-600)" : "transparent",
                      }}
                    >
                      {active && <span style={{ width: 6, height: 6, borderRadius: 99, background: "#fff" }} />}
                    </span>
                    <span style={{ fontSize: 13.5, fontWeight: 550 }}>{loc.label}</span>
                  </button>
                );
              })}
            </div>

            <button
              onClick={confirm}
              disabled={!selected || saving}
              className="btn btn-primary btn-lg mt-5"
              style={{ width: "100%" }}
            >
              {saving ? "Menyimpan…" : "Lanjut belanja"}
            </button>
          </>
        )}
      </div>
    </main>
  );
}
