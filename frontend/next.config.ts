import type { NextConfig } from "next";

// Origin API backend (untuk connect-src CSP). Di produksi biasanya same-origin
// (di belakang Traefik) sehingga 'self' cukup; di dev = http://localhost:8001.
const API = process.env.NEXT_PUBLIC_API_BASE || "";
const isProd = process.env.NODE_ENV === "production";

// leaflet dimuat dari unpkg (dengan SRI di MapPicker); tile peta = OpenStreetMap;
// font Geist + JetBrains Mono dari Google Fonts (CSS di googleapis, file di gstatic).
// 'wasm-unsafe-eval' = engine 3D ThingView (/viewer3d, WebAssembly.instantiate) —
// tanpa ini Chrome menolak kompilasi WASM di bawah CSP (Viewer3D).
const csp = [
  "default-src 'self'",
  "script-src 'self' 'unsafe-inline' 'wasm-unsafe-eval' https://unpkg.com",
  "style-src 'self' 'unsafe-inline' https://unpkg.com https://fonts.googleapis.com",
  "img-src 'self' data: blob: https:",
  "font-src 'self' data: https://fonts.gstatic.com",
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

// Halaman part memuat engine 3D ThingView (embind Emscripten lama) yang membangun
// invoker lewat konstruktor `Function` (craftInvokerFunction) → butuh 'unsafe-eval'.
// Menulis ulang embind di blob minified proprietary terlalu berisiko, jadi
// 'unsafe-eval' diberikan HANYA untuk /part/* (entri belakangan menimpa kunci
// header yang sama — perilaku resmi Next). Sisa situs tetap tanpa 'unsafe-eval'.
const cspPart = csp.replace("'wasm-unsafe-eval'", "'wasm-unsafe-eval' 'unsafe-eval'");

const nextConfig: NextConfig = {
  async headers() {
    return [
      { source: "/:path*", headers: securityHeaders },
      // Engine 3D ThingView (±12,8 MB) hanya berubah saat deploy: biarkan browser
      // menyimpannya, jangan revalidasi tiap kali halaman part dibuka. Viewer3D
      // juga menyimpannya sendiri di Cache Storage (berlapis, karena bawaan Next
      // untuk berkas public/ adalah `public, max-age=0`).
      {
        source: "/viewer3d/:path*",
        headers: [
          { key: "Cache-Control", value: "public, max-age=86400, stale-while-revalidate=604800" },
        ],
      },
      ...(isProd
        ? [{ source: "/part/:path*", headers: [{ key: "Content-Security-Policy", value: cspPart }] }]
        : []),
    ];
  },
};

export default nextConfig;
