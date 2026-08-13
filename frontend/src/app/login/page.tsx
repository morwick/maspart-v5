"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { checkHealth, login } from "@/lib/api";
import { landingPath, saveSession, takeLogoutReason } from "@/lib/auth";

// Tiga langkah alur kerja — ditampilkan di panel merek (desktop).
const STEPS: [string, string, string][] = [
  ["01", "Cari part", "Nama, part number, foto, atau nomor rangka unit."],
  ["02", "Cek stok & harga", "Ketersediaan per gudang dan perbandingan harga."],
  ["03", "Pesan & lacak", "Dari keranjang sampai barang sampai di cabang."],
];

const HEALTH_TEXT = {
  checking: { dot: "#c5cac6", label: "Memeriksa status layanan…" },
  ok: { dot: "#7ee29a", label: "Semua sistem normal" },
  down: { dot: "#ffcf7a", label: "Server tidak terjangkau" },
} as const;

export default function LoginPage() {
  const router = useRouter();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [showPw, setShowPw] = useState(false);
  const [remember, setRemember] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  // Alasan user terlempar ke sini (mis. akunnya dipakai login di perangkat lain).
  const [notice, setNotice] = useState("");
  const [health, setHealth] = useState<keyof typeof HEALTH_TEXT>("checking");

  useEffect(() => {
    setNotice(takeLogoutReason());
  }, []);

  // Titik status hanya boleh hijau kalau backend memang menjawab — indikator
  // "semua normal" yang dipaku di markup itu berbohong saat server mati.
  useEffect(() => {
    let alive = true;
    checkHealth().then((ok) => {
      if (alive) setHealth(ok ? "ok" : "down");
    });
    return () => {
      alive = false;
    };
  }, []);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      const res = await login(username.trim(), password);
      saveSession(res.access_token, res.user, remember);
      router.replace(landingPath(res.user));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Gagal login");
    } finally {
      setLoading(false);
    }
  }

  const labelStyle = { fontSize: 13, fontWeight: 550, color: "var(--ink-700)" } as const;

  return (
    <main
      className="flex min-h-dvh flex-col md:grid md:grid-cols-2"
      style={{ background: "var(--canvas)" }}
    >
      {/* ── Panel merek — kolom kiri di desktop, hero atas di HP ── */}
      <section className="login-pane flex flex-col justify-between px-6 pt-7 pb-[30px] md:px-16 md:py-14">
        <div className="login-blob login-blob-1" />
        <div className="login-blob login-blob-2" />

        <div className="relative flex items-center gap-2.5">
          <div
            className="mono grid place-items-center"
            style={{
              width: 34,
              height: 34,
              borderRadius: 9,
              background: "#fff",
              color: "#026a0e",
              fontWeight: 700,
              fontSize: 16,
            }}
          >
            M
          </div>
          <span style={{ fontWeight: 650, fontSize: 17 }}>MasPart</span>
        </div>

        <div className="relative mt-6 md:mt-0 md:max-w-[440px]">
          <h2
            className="text-[26px] md:text-[40px]"
            style={{ margin: 0, fontWeight: 650, lineHeight: 1.12, letterSpacing: "-0.025em" }}
          >
            Satu tempat untuk
            <br />
            semua part.
          </h2>

          {/* HP: satu kalimat ringkas — layarnya tak cukup untuk 3 langkah. */}
          <p
            className="mt-2 md:hidden"
            style={{ fontSize: 13.5, lineHeight: 1.5, color: "rgba(255,255,255,.8)" }}
          >
            Cari part, cek stok &amp; harga, pesan dan lacak sampai cabang.
          </p>

          {/* Desktop: 3 langkah bernomor. */}
          <div
            className="mt-9 hidden md:block"
            style={{ borderTop: "1px solid rgba(255,255,255,.18)" }}
          >
            {STEPS.map(([no, judul, isi]) => (
              <div key={no} className="login-step">
                <span className="login-step-no">{no}</span>
                <div>
                  <div style={{ fontSize: 15, fontWeight: 600 }}>{judul}</div>
                  <div
                    style={{
                      fontSize: 13,
                      lineHeight: 1.5,
                      color: "rgba(255,255,255,.78)",
                      marginTop: 2,
                    }}
                  >
                    {isi}
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>

        <div
          className="relative hidden items-center gap-2 md:flex"
          style={{ fontSize: 12, color: "rgba(255,255,255,.7)" }}
        >
          <span
            style={{
              width: 7,
              height: 7,
              borderRadius: 99,
              background: HEALTH_TEXT[health].dot,
              display: "inline-block",
            }}
          />
          {HEALTH_TEXT[health].label}
        </div>
      </section>

      {/* ── Kolom form ── */}
      <section className="flex flex-1 flex-col justify-center px-6 pt-7 pb-[22px] md:px-24 md:py-14">
        <h1
          className="text-[22px] md:text-[30px]"
          style={{ margin: 0, fontWeight: 650, letterSpacing: "-0.02em", color: "var(--ink-900)" }}
        >
          Masuk
        </h1>
        <p
          className="mt-1.5 mb-6 text-[13.5px] md:mb-9 md:text-[14px]"
          style={{ color: "var(--ink-500)" }}
        >
          Gunakan akun MasPart yang diberikan admin.
        </p>

        <form onSubmit={handleSubmit} className="flex flex-col gap-4 md:gap-[18px]">
          <div>
            <label htmlFor="login-username" className="mb-[7px] block" style={labelStyle}>
              Username
            </label>
            <input
              id="login-username"
              className="input login-input"
              type="text"
              autoComplete="username"
              placeholder="andi.gudang"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              required
            />
          </div>

          <div>
            <label htmlFor="login-password" className="mb-[7px] block" style={labelStyle}>
              Password
            </label>
            <div className="relative">
              <input
                id="login-password"
                className="input login-input has-toggle"
                type={showPw ? "text" : "password"}
                autoComplete="current-password"
                placeholder="••••••••"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                required
              />
              <button
                type="button"
                className="login-eye"
                aria-controls="login-password"
                aria-pressed={showPw}
                onClick={() => setShowPw((v) => !v)}
              >
                {showPw ? "Sembunyikan" : "Lihat"}
              </button>
            </div>
          </div>

          <label
            className="flex min-h-11 cursor-pointer items-center gap-2.5 md:min-h-0"
            style={{ fontSize: 14, color: "var(--ink-700)" }}
          >
            <input
              type="checkbox"
              checked={remember}
              onChange={(e) => setRemember(e.target.checked)}
              style={{ width: 18, height: 18, accentColor: "var(--brand-600)", cursor: "pointer" }}
            />
            Ingat saya di device ini
          </label>

          {notice && !error && <div className="alert alert-error">{notice}</div>}
          {error && <div className="alert alert-error">{error}</div>}

          <button type="submit" className="btn btn-primary login-btn md:mt-1.5" disabled={loading}>
            {loading ? "Memproses…" : "Masuk"}
          </button>
        </form>

        <div
          className="mt-auto flex justify-between pt-5 md:mt-9 md:border-t"
          style={{ fontSize: 12, color: "var(--ink-400)", borderColor: "var(--ink-150)" }}
        >
          <span>Lupa password? Hubungi admin.</span>
          <span className="mono">v4.0</span>
        </div>
      </section>
    </main>
  );
}
