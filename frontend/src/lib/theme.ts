// Manajemen tema terang/gelap (Command Center). Disimpan di localStorage &
// diterapkan sebagai atribut data-theme pada <html> — dibaca oleh token CSS di
// globals.css. Skrip anti-FOUC di layout menerapkannya sebelum paint pertama.
"use client";

export type Theme = "light" | "dark";
const KEY = "maspart_theme";

export function getTheme(): Theme {
  if (typeof document === "undefined") return "light";
  return document.documentElement.getAttribute("data-theme") === "dark" ? "dark" : "light";
}

export function applyTheme(t: Theme) {
  if (typeof document === "undefined") return;
  document.documentElement.setAttribute("data-theme", t);
  // body juga di-set agar selektor body[data-theme] ikut aktif.
  document.body?.setAttribute("data-theme", t);
  try {
    localStorage.setItem(KEY, t);
  } catch {
    /* ignore */
  }
}

export function toggleTheme(): Theme {
  const next: Theme = getTheme() === "dark" ? "light" : "dark";
  applyTheme(next);
  return next;
}

// Snippet yang dijalankan inline di <head> untuk mencegah kedip (FOUC).
export const THEME_INIT_SCRIPT = `(function(){try{var t=localStorage.getItem('${KEY}');if(!t){t=window.matchMedia&&window.matchMedia('(prefers-color-scheme: dark)').matches?'dark':'light';}document.documentElement.setAttribute('data-theme',t);}catch(e){}})();`;
