"use client";

import { useEffect, useState } from "react";
import { applyTheme, getTheme, toggleTheme, type Theme } from "@/lib/theme";

/* Toggle terang/gelap beranimasi (matahari ⇄ bulan + bintang) — port dari
   desain Command Center. */
const SunIcon = (
  <svg width={13} height={13} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round">
    <circle cx="12" cy="12" r="4" />
    <path d="M12 2v2M12 20v2M2 12h2M20 12h2M5 5l1.5 1.5M17.5 17.5 19 19M19 5l-1.5 1.5M6.5 17.5 5 19" />
  </svg>
);
const MoonIcon = (
  <svg width={12} height={12} viewBox="0 0 24 24" fill="currentColor">
    <path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8Z" />
  </svg>
);

export default function ThemeToggle() {
  const [theme, setTheme] = useState<Theme>("light");
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    setTheme(getTheme());
    setMounted(true);
  }, []);

  function onToggle() {
    setTheme(toggleTheme());
  }

  // Sebelum mount, render placeholder netral agar tak mismatch dgn skrip FOUC.
  const dark = mounted && theme === "dark";

  return (
    <button
      onClick={onToggle}
      title="Mode gelap / terang"
      aria-label="Ganti tema"
      style={{
        position: "relative",
        width: 58,
        height: 30,
        borderRadius: 99,
        border: "1px solid var(--ink-200)",
        background: dark ? "#0e1730" : "#dbeafe",
        cursor: "pointer",
        transition: "background .4s ease",
        padding: 0,
        overflow: "hidden",
        flexShrink: 0,
      }}
    >
      {/* Bintang (mode gelap) */}
      <span style={{ position: "absolute", inset: 0, opacity: dark ? 1 : 0, transition: "opacity .4s" }}>
        <span style={{ position: "absolute", left: 10, top: 8, width: 3, height: 3, borderRadius: 99, background: "#e8f0ff", animation: "twinkle 2.2s infinite ease-in-out" }} />
        <span style={{ position: "absolute", left: 19, top: 17, width: 2, height: 2, borderRadius: 99, background: "#e8f0ff", animation: "twinkle 1.8s infinite ease-in-out .4s" }} />
        <span style={{ position: "absolute", left: 26, top: 7, width: 2, height: 2, borderRadius: 99, background: "#e8f0ff", animation: "twinkle 2.6s infinite ease-in-out .9s" }} />
      </span>
      {/* Knob */}
      <span
        style={{
          position: "absolute",
          top: 3,
          left: 3,
          width: 22,
          height: 22,
          borderRadius: 99,
          background: dark ? "#1b2540" : "#fffbe6",
          transform: `translateX(${dark ? 28 : 0}px)`,
          transition: "transform .35s cubic-bezier(.55,1.6,.35,1), background .4s",
          display: "grid",
          placeItems: "center",
          boxShadow: "0 1px 3px rgba(0,0,0,.3)",
        }}
      >
        <span style={{ position: "absolute", display: "inline-flex", color: "#f59e0b", opacity: dark ? 0 : 1, transform: `rotate(${dark ? -90 : 0}deg)`, transition: "opacity .3s, transform .45s" }}>{SunIcon}</span>
        <span style={{ position: "absolute", display: "inline-flex", color: "#c7d2fe", opacity: dark ? 1 : 0, transform: `rotate(${dark ? 0 : 90}deg)`, transition: "opacity .3s, transform .45s" }}>{MoonIcon}</span>
      </span>
    </button>
  );
}

// Dipakai halaman lain bila perlu memaksa tema (mis. reset). Re-export ringan.
export { applyTheme };
