import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Unduh Aplikasi — MasPart",
  description: "Unduh aplikasi Android MasPart — katalog & pencarian suku cadang truk.",
};

const APK_URL = "/maspart.apk";
const APK_SIZE = "51 MB";
const APP_VERSION = "1.0.0";

export default function DownloadPage() {
  return (
    <main
      style={{
        minHeight: "100vh",
        display: "flex",
        alignItems: "flex-start",
        justifyContent: "center",
        padding: "40px 20px",
        background: "radial-gradient(120% 80% at 50% 0%, #eef3f1 0%, #dde5e2 100%)",
        fontFamily: "system-ui, -apple-system, Segoe UI, Roboto, sans-serif",
      }}
    >
      <div style={{ width: "100%", maxWidth: 440 }}>
        {/* Kartu utama */}
        <div
          style={{
            background: "#fff",
            borderRadius: 24,
            padding: "32px 26px",
            boxShadow: "0 30px 60px -24px rgba(12,30,20,.35)",
            textAlign: "center",
          }}
        >
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src="/icon.png"
            alt="MasPart"
            width={84}
            height={84}
            style={{ borderRadius: 20, margin: "0 auto 16px", display: "block", boxShadow: "0 10px 22px -8px rgba(3,134,18,.4)" }}
          />
          <h1 style={{ fontSize: 26, fontWeight: 700, color: "#13211a", margin: "0 0 4px", letterSpacing: "-0.5px" }}>
            MasPart
          </h1>
          <p style={{ fontSize: 14, color: "#6b7a72", margin: "0 0 4px" }}>
            Spare Part Truck &amp; Alat Berat
          </p>
          <p style={{ fontSize: 12.5, color: "#9aa8a1", margin: "0 0 24px" }}>
            Aplikasi Android · versi {APP_VERSION} · {APK_SIZE}
          </p>

          <a
            href={APK_URL}
            download="maspart.apk"
            style={{
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              gap: 10,
              height: 54,
              borderRadius: 14,
              background: "#038612",
              color: "#fff",
              fontSize: 16,
              fontWeight: 600,
              textDecoration: "none",
              boxShadow: "0 12px 24px -10px rgba(3,134,18,.7)",
            }}
          >
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#fff" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M12 3v12M7 11l5 5 5-5M5 21h14" />
            </svg>
            Unduh APK ({APK_SIZE})
          </a>

          <p style={{ fontSize: 11.5, color: "#9aa8a1", margin: "14px 0 0" }}>
            Khusus perangkat Android
          </p>
        </div>

        {/* Cara pasang */}
        <div
          style={{
            background: "#fff",
            borderRadius: 18,
            padding: "20px 22px",
            marginTop: 16,
            border: "1px solid #e8ede9",
          }}
        >
          <p style={{ fontSize: 14, fontWeight: 700, color: "#13211a", margin: "0 0 14px" }}>
            Cara memasang
          </p>
          {[
            "Ketuk tombol “Unduh APK” di atas.",
            "Buka file maspart.apk yang terunduh.",
            "Bila muncul peringatan “sumber tidak dikenal”, izinkan pemasangan.",
            "Ketuk Pasang, lalu buka aplikasi dan masuk dengan akun Anda.",
          ].map((t, i) => (
            <div key={i} style={{ display: "flex", gap: 12, marginBottom: i < 3 ? 12 : 0, alignItems: "flex-start" }}>
              <div
                style={{
                  flexShrink: 0,
                  width: 24,
                  height: 24,
                  borderRadius: 8,
                  background: "#e8f6ec",
                  color: "#038612",
                  fontSize: 12.5,
                  fontWeight: 700,
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                }}
              >
                {i + 1}
              </div>
              <p style={{ fontSize: 13.5, color: "#43534b", lineHeight: 1.5, margin: 0 }}>{t}</p>
            </div>
          ))}
        </div>

        <p style={{ textAlign: "center", fontSize: 12, color: "#8c9a92", marginTop: 18 }}>
          © MasPart · maspart.tech
        </p>
      </div>
    </main>
  );
}
