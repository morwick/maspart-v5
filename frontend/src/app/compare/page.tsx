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
      <div className="mb-0.5 flex justify-between text-xs text-zinc-500">
        <span>{label}</span>
        <span className="font-medium text-zinc-700">{value == null ? "—" : `${p}%`}</span>
      </div>
      <div className="h-2 overflow-hidden rounded-full bg-zinc-100">
        <div className="h-full bg-brand" style={{ width: `${p}%` }} />
      </div>
    </div>
  );
}

function Gallery({ pn, name, urls }: { pn: string; name: string; urls: string[] }) {
  return (
    <div>
      <p className="font-mono text-sm font-bold">{pn}</p>
      {name && <p className="mb-2 text-xs text-zinc-500">{name}</p>}
      {urls.length > 0 ? (
        <div className="grid grid-cols-2 gap-2">
          {urls.map((u, i) => (
            // eslint-disable-next-line @next/next/no-img-element
            <img
              key={i}
              src={partImageUrl(u)}
              alt={`${pn} ${i + 1}`}
              loading="lazy"
              className="aspect-square w-full rounded-lg bg-white object-contain ring-1 ring-zinc-200"
            />
          ))}
        </div>
      ) : (
        <p className="text-sm text-zinc-400">Tidak ada gambar.</p>
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
      <div className="mx-auto w-full max-w-5xl px-4 py-5 sm:px-6">
        <h2 className="mb-1 text-base font-semibold">
          🔍 Bandingkan <span className="text-brand">2 Part</span>
        </h2>
        <p className="mb-4 text-sm text-zinc-500">
          Analisis interchange berdasarkan foto SIMS (bentuk + warna) dan nama part.
        </p>

        <form onSubmit={run} className="mb-5 grid gap-2 sm:grid-cols-[1fr_1fr_auto]">
          <input
            value={pn1}
            onChange={(e) => setPn1(e.target.value)}
            placeholder="Part Number #1"
            className="rounded-lg border border-zinc-300 px-3 py-2 text-sm outline-none focus:border-brand focus:ring-2 focus:ring-brand/20"
          />
          <input
            value={pn2}
            onChange={(e) => setPn2(e.target.value)}
            placeholder="Part Number #2"
            className="rounded-lg border border-zinc-300 px-3 py-2 text-sm outline-none focus:border-brand focus:ring-2 focus:ring-brand/20"
          />
          <button
            disabled={loading}
            className="rounded-lg bg-brand px-5 py-2 text-sm font-semibold text-white hover:bg-green-700 disabled:opacity-60"
          >
            {loading ? "Menganalisis…" : "🔬 Cek Interchange"}
          </button>
        </form>
        {loading && (
          <p className="mb-3 text-xs text-zinc-400">
            Mengambil & menganalisis foto dari SIMS — mohon tunggu sebentar.
          </p>
        )}

        {error && (
          <p className="mb-4 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700 ring-1 ring-red-100">
            {error}
          </p>
        )}

        {best && res && (
          <>
            {/* Verdict */}
            <div
              className="mb-5 rounded-xl p-4 text-white"
              style={{ backgroundColor: best.color }}
            >
              <p className="text-lg font-bold">{best.verdict}</p>
              <p className="mt-1 text-sm opacity-90">
                Skor keseluruhan: {pct(best.overall)}%
              </p>
            </div>

            {/* Skor + pasangan terbaik */}
            <div className="mb-6 grid gap-6 md:grid-cols-2">
              <div className="space-y-3 rounded-xl bg-white p-4 ring-1 ring-zinc-200">
                <ScoreBar label="Bentuk (shape)" value={best.shape_score} />
                <ScoreBar label="Nama part" value={best.name_score} />
                <ScoreBar label="Warna" value={best.color_score} />
              </div>
              <div className="rounded-xl bg-white p-4 ring-1 ring-zinc-200">
                <p className="mb-2 text-sm font-semibold text-zinc-700">
                  Pasangan foto termirip
                </p>
                <div className="grid grid-cols-2 gap-2">
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  {bestImg1 && (
                    <img
                      src={partImageUrl(bestImg1)}
                      alt={res.pn1}
                      className="aspect-square w-full rounded-lg bg-white object-contain ring-1 ring-zinc-200"
                    />
                  )}
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  {bestImg2 && (
                    <img
                      src={partImageUrl(bestImg2)}
                      alt={res.pn2}
                      className="aspect-square w-full rounded-lg bg-white object-contain ring-1 ring-zinc-200"
                    />
                  )}
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
