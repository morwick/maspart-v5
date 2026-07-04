import type { NextConfig } from "next";

// Origin API backend (untuk connect-src CSP). Di produksi biasanya same-origin
// (di belakang Traefik) sehingga 'self' cukup; di dev = http://localhost:8001.
const API = process.env.NEXT_PUBLIC_API_BASE || "";
const isProd = process.env.NODE_ENV === "production";

// leaflet dimuat dari unpkg (dengan SRI di MapPicker); tile peta = OpenStreetMap.
const csp = [
  "default-src 'self'",
  "script-src 'self' 'unsafe-inline' https://unpkg.com",
  "style-src 'self' 'unsafe-inline' https://unpkg.com",
  "img-src 'self' data: blob: https:",
  "font-src 'self' data:",
  `connect-src ${["'self'", API].filter(Boolean).join(" ")}`,
  "object-src 'none'",
  "base-uri 'self'",
  "frame-ancestors 'none'",
  "form-action 'self'",
].join("; ");

const securityHeaders = [
  { key: "X-Frame-Options", value: "DENY" },
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
  { key: "Permissions-Policy", value: "geolocation=(self), camera=(), microphone=()" },
  // HSTS + CSP hanya di production: di dev, HMR Next butuh eval/ws yang akan
  // dilanggar CSP, dan HSTS di http localhost tak relevan.
  ...(isProd
    ? [
        { key: "Strict-Transport-Security", value: "max-age=63072000; includeSubDomains; preload" },
        { key: "Content-Security-Policy", value: csp },
      ]
    : []),
];

const nextConfig: NextConfig = {
  async headers() {
    return [{ source: "/:path*", headers: securityHeaders }];
  },
};

export default nextConfig;
