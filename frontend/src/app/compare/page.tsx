"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import AppShell from "@/components/AppShell";
import { ApiError, comparePartsApi, partImageUrl, type CompareResponse } from "@/lib/api";
import { clearSession, getToken } from "@/lib/auth";

const pct = (n: number) => Math.round(n * 100);

function ScoreBar({ label, value }: { label: string; value: number | null }) {
  const p = value == null ? 0 : pct(value);
  return (
    <div>
      <div className="flex justify-between" style={{ marginBottom: 2, fontSize: 12, color: "var(--ink-500)" }}>
        <span>{label}</span>
        <span className="mono" style={{ fontWeight: 600, color: "var(--ink-700)" }}>{value == null ? "—" : `${p}%`}</span>
      </div>
      <div style={{ height: 8, overflow: "hidden", borderRadius: 99, background: "var(--ink-100)" }}>
        <div style={{ height: "100%", background: "var(--brand-600)", width: `${p}%`, transition: "width .3s" }} />
      </div>
    </div>
  );
}

function Gallery({ pn, name, urls }: { pn: string; name: string; urls: string[] }) {
  return (
    <div>
      <p className="mono" style={{ fontSize: 13, fontWeight: 700 }}>{pn}</p>
      {name && <p style={{ marginBottom: 8, fontSize: 12, color: "var(--ink-500)" }}>{name}</p>}
      {urls.length > 0 ? (
        <div className="grid grid-cols-2 gap-2">
          {urls.map((u, i) => (
            // eslint-disable-next-line @next/next/no-img-element
            <img
              key={i}
              src={partImageUrl(u)}
              alt={`${pn} ${i + 1}`}
              loading="lazy"
              className="aspect-square w-full object-contain"
              style={{ borderRadius: 10, background: "var(--paper)", border: "1px solid var(--ink-200)" }}
            />
          ))}
        </div>
      ) : (
        <p style={{ fontSize: 13, color: "var(--ink-400)" }}>Tidak ada gambar.</p>
      )}
    </div>
  );
}

export default function ComparePage() {
  const router = useRouter();
  const [pn1, setPn1] = useState("");
  const [pn2, setPn2] = useState("");
  const [res, setRes] = useState<CompareResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!getToken()) router.replace("/login");
  }, [router]);

  async function run(e: React.FormEvent) {
    e.preventDefault();
    const a = pn1.trim();
    const b = pn2.trim();
    if (!a || !b) return setError("Isi kedua Part Number.");
    if (a.toUpperCase() === b.toUpperCase()) return setError("Part Number tidak boleh sama.");
    const token = getToken();
    if (!token) return router.replace("/login");
    setError(null);
    setLoading(true);
    setRes(null);
    try {
      const r = await comparePartsApi(token, a, b);
      setRes(r);
      if (r.error) setError(r.error);
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        clearSession();
        return router.replace("/login");
      }
      setError(err instanceof Error ? err.message : "Gagal membandingkan");
    } finally {
      setLoading(false);
    }
  }

  const best = res?.best ?? null;
  const bestImg1 = best && res ? res.urls1[best.i] : null;
  const bestImg2 = best && res ? res.urls2[best.j] : null;

  return (
    <AppShell active="/compare" title="Bandingkan 2 Part" sub="Analisis interchange via foto SIMS + nama">
      <div className="mx-auto w-full max-w-5xl px-4 py-6 sm:px-7">
        <p style={{ marginBottom: 16, fontSize: 13.5, color: "var(--ink-500)" }}>
          Analisis interchange berdasarkan foto SIMS (bentuk + warna) dan nama part.
        </p>

        <form onSubmit={run} className="grid gap-2" style={{ gridTemplateColumns: "1fr 1fr auto" }}>
          <input className="input mono" value={pn1} onChange={(e) => setPn1(e.target.value)} placeholder="Part Number #1" />
          <input className="input mono" value={pn2} onChange={(e) => setPn2(e.target.value)} placeholder="Part Number #2" />
          <button className="btn btn-primary" disabled={loading}>{loading ? "Menganalisis…" : "Cek Interchange"}</button>
        </form>
        {loading && (
          <p style={{ marginTop: 10, fontSize: 12, color: "var(--ink-400)" }}>
            Mengambil & menganalisis foto dari SIMS — mohon tunggu sebentar.
          </p>
        )}

        {error && <div className="alert alert-error" style={{ marginTop: 14 }}>{error}</div>}

        {best && res && (
          <>
            {/* Verdict */}
            <div style={{ margin: "20px 0", borderRadius: 12, padding: 16, color: "#fff", backgroundColor: best.color }}>
              <p style={{ fontSize: 18, fontWeight: 700 }}>{best.verdict}</p>
              <p style={{ marginTop: 4, fontSize: 13.5, opacity: 0.9 }}>Skor keseluruhan: {pct(best.overall)}%</p>
            </div>

            {/* Skor + pasangan terbaik */}
            <div className="grid gap-4 md:grid-cols-2" style={{ marginBottom: 24 }}>
              <div className="surface" style={{ padding: 16, display: "flex", flexDirection: "column", gap: 12 }}>
                <ScoreBar label="Bentuk (shape)" value={best.shape_score} />
                <ScoreBar label="Nama part" value={best.name_score} />
                <ScoreBar label="Warna" value={best.color_score} />
              </div>
              <div className="surface" style={{ padding: 16 }}>
                <p style={{ marginBottom: 8, fontSize: 13, fontWeight: 600, color: "var(--ink-700)" }}>Pasangan foto termirip</p>
                <div className="grid grid-cols-2 gap-2">
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  {bestImg1 && <img src={partImageUrl(bestImg1)} alt={res.pn1} className="aspect-square w-full object-contain" style={{ borderRadius: 10, background: "var(--paper)", border: "1px solid var(--ink-200)" }} />}
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  {bestImg2 && <img src={partImageUrl(bestImg2)} alt={res.pn2} className="aspect-square w-full object-contain" style={{ borderRadius: 10, background: "var(--paper)", border: "1px solid var(--ink-200)" }} />}
                </div>
              </div>
            </div>

            {/* Semua gambar kedua part */}
            <div className="grid gap-6 md:grid-cols-2">
              <Gallery pn={res.pn1} name={res.name1} urls={res.urls1} />
              <Gallery pn={res.pn2} name={res.name2} urls={res.urls2} />
            </div>
          </>
        )}
      </div>
    </AppShell>
  );
}
