"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { login } from "@/lib/api";
import { landingPath, saveSession } from "@/lib/auth";

function Logo({ onGreen = false }: { onGreen?: boolean }) {
  return (
    <div className="flex items-center gap-2.5">
      <div
        className="mono grid place-items-center"
        style={{
          width: 34,
          height: 34,
          borderRadius: 9,
          background: onGreen ? "#fff" : "var(--brand-600)",
          color: onGreen ? "var(--brand-700)" : "#fff",
          fontWeight: 700,
          fontSize: 16,
        }}
      >
        M
      </div>
      <span style={{ fontWeight: 650, fontSize: 17, color: onGreen ? "#fff" : "var(--ink-900)" }}>MasPart</span>
    </div>
  );
}

export default function LoginPage() {
  const router = useRouter();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [remember, setRemember] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      const res = await login(username.trim(), password);
      saveSession(res.access_token, res.user);
      router.replace(landingPath(res.user));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Gagal login");
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="grid min-h-screen place-items-center p-4" style={{ background: "var(--canvas)" }}>
      <div
        className="grid w-full overflow-hidden md:grid-cols-2"
        style={{
          maxWidth: 880,
          borderRadius: "var(--r-xl)",
          boxShadow: "var(--shadow-3)",
          background: "var(--paper)",
          border: "1px solid var(--ink-150)",
        }}
      >
        {/* LEFT — green brand panel */}
        <div
          className="hidden flex-col justify-between p-8 md:flex"
          style={{
            background: "linear-gradient(150deg, var(--brand-500) 0%, var(--brand-600) 45%, var(--brand-700) 100%)",
            color: "#fff",
            minHeight: 460,
          }}
        >
          <Logo onGreen />
          <div>
            <h2 style={{ fontSize: 28, fontWeight: 650, lineHeight: 1.15, letterSpacing: "-0.02em" }}>
              Cari part dengan
              <br />
              cepat &amp; akurat.
            </h2>
            <p style={{ fontSize: 13, lineHeight: 1.6, marginTop: 12, color: "rgba(255,255,255,.85)" }}>
              Database part Shantui &amp; Sinotruk — pencarian fuzzy, by foto, perbandingan
              harga, dan populasi unit dalam satu tempat.
            </p>
            <div className="mt-7 flex gap-8">
              {[
                ["2.418", "part terindeks"],
                ["42", "unit aktif"],
                ["17", "cabang"],
              ].map(([v, l]) => (
                <div key={l}>
                  <div className="mono" style={{ fontSize: 22, fontWeight: 700 }}>{v}</div>
                  <div style={{ fontSize: 11.5, color: "rgba(255,255,255,.8)" }}>{l}</div>
                </div>
              ))}
            </div>
          </div>
        </div>

        {/* RIGHT — form */}
        <div className="flex flex-col justify-center p-8">
          {/* brand for mobile */}
          <div className="mb-6 md:hidden">
            <Logo />
          </div>

          <div style={{ fontSize: 11, fontWeight: 600, letterSpacing: "0.08em", color: "var(--ink-400)" }}>
            LOGIN
          </div>
          <h1 style={{ fontSize: 22, fontWeight: 650, marginTop: 4, letterSpacing: "-0.01em" }}>
            Selamat datang kembali
          </h1>
          <p style={{ fontSize: 13, color: "var(--ink-500)", marginTop: 4, marginBottom: 20 }}>
            Masuk dengan akun MasPart-mu.
          </p>

          <form onSubmit={handleSubmit} className="flex flex-col gap-3.5">
            <div>
              <label className="mb-1.5 block" style={{ fontSize: 12.5, fontWeight: 550, color: "var(--ink-700)" }}>
                Username
              </label>
              <input className="input" type="text" autoComplete="username" placeholder="andi.gudang"
                value={username} onChange={(e) => setUsername(e.target.value)} required />
            </div>
            <div>
              <div className="mb-1.5 flex items-center justify-between">
                <label style={{ fontSize: 12.5, fontWeight: 550, color: "var(--ink-700)" }}>Password</label>
                <span className="link" style={{ fontSize: 12 }} onClick={() => setError("Hubungi admin untuk reset password.")}>
                  Lupa password?
                </span>
              </div>
              <input className="input" type="password" autoComplete="current-password" placeholder="••••••••"
                value={password} onChange={(e) => setPassword(e.target.value)} required />
            </div>

            <label className="flex items-center gap-2" style={{ fontSize: 13, color: "var(--ink-700)" }}>
              <input type="checkbox" checked={remember} onChange={(e) => setRemember(e.target.checked)} />
              Ingat saya di device ini
            </label>

            {error && <div className="alert alert-error">{error}</div>}

            <button type="submit" className="btn btn-primary btn-lg" style={{ width: "100%" }} disabled={loading}>
              {loading ? "Memproses…" : "Masuk"}
            </button>
          </form>

          <div className="my-4 flex items-center gap-3">
            <div className="divider" style={{ flex: 1 }} />
            <span style={{ fontSize: 10.5, color: "var(--ink-400)", letterSpacing: "0.08em" }}>ATAU</span>
            <div className="divider" style={{ flex: 1 }} />
          </div>

          <button
            type="button"
            className="btn btn-secondary btn-lg"
            style={{ width: "100%" }}
            onClick={() => setError("SSO via Supabase belum diaktifkan.")}
          >
            SSO via Supabase
          </button>

          <p className="mt-5 text-center" style={{ fontSize: 11, color: "var(--ink-400)" }}>
            v4.0 · MasPart · session timeout 12 jam
          </p>
        </div>
      </div>
    </main>
  );
}
