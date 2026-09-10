import type { Metadata, Viewport } from "next";
import "./globals.css";
import { THEME_INIT_SCRIPT } from "@/lib/theme";

export const metadata: Metadata = {
  title: "MasPart",
  description: "Part Number Finder — pencarian & katalog spare part",
};

/* Viewport DIEKSPLISITKAN (dulu hanya bawaan Next):
   - `viewportFit: "cover"` = halaman boleh memakai area di balik notch;
     pasangannya `env(safe-area-inset-*)` di globals.css/AppShell.
   - `maximumScale` sengaja TIDAK dikunci — mengunci zoom menghalangi user
     yang butuh memperbesar. Zoom-paksa saat mengetik sudah diatasi dengan
     menaikkan font kolom isian ke 16px, bukan dengan melarang zoom.
   - `themeColor` membuat bilah status HP ikut warna tema aktif. */
export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  viewportFit: "cover",
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#faf9f5" },
    { media: "(prefers-color-scheme: dark)", color: "#0e130f" },
  ],
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="id" className="h-full antialiased" suppressHydrationWarning>
      <head>
        {/* Terapkan tema tersimpan SEBELUM paint agar tak ada kedip (FOUC). */}
        <script dangerouslySetInnerHTML={{ __html: THEME_INIT_SCRIPT }} />
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="" />
        <link
          href="https://fonts.googleapis.com/css2?family=Geist:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500;600;700&display=swap"
          rel="stylesheet"
        />
      </head>
      <body className="min-h-full" suppressHydrationWarning>{children}</body>
    </html>
  );
}
