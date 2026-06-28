// Penyimpanan sesi sederhana (localStorage) untuk JWT + info user.
"use client";

import type { UserOut } from "./api";

const TOKEN_KEY = "maspart_token";
const USER_KEY = "maspart_user";
// Cache per-sesi yang harus dibersihkan saat ganti user (login/logout).
const SESSION_CACHE_KEYS = [
  "maspart_perms",
  "maspart_image_search",
  "maspart_image_return",
  "maspart_allowed_menus",
];

function clearSessionCaches() {
  if (typeof window === "undefined") return;
  try {
    SESSION_CACHE_KEYS.forEach((k) => sessionStorage.removeItem(k));
  } catch {
    /* ignore */
  }
}

export function saveSession(token: string, user: UserOut) {
  if (typeof window === "undefined") return;
  // Mulai sesi bersih: buang cache milik sesi/akun sebelumnya.
  clearSessionCaches();
  localStorage.setItem(TOKEN_KEY, token);
  localStorage.setItem(USER_KEY, JSON.stringify(user));
}

export function getToken(): string | null {
  if (typeof window === "undefined") return null;
  return localStorage.getItem(TOKEN_KEY);
}

export function getUser(): UserOut | null {
  if (typeof window === "undefined") return null;
  const raw = localStorage.getItem(USER_KEY);
  if (!raw) return null;
  try {
    return JSON.parse(raw) as UserOut;
  } catch {
    return null;
  }
}

/** Halaman tujuan setelah login sesuai role. Pembeli wajib pilih lokasi dulu. */
export function landingPath(user: UserOut | null): string {
  if (!user) return "/login";
  if (user.role === "pembeli") return user.gudang ? "/search" : "/pilih-lokasi";
  return "/search";
}

/** Perbarui lokasi gudang user tersimpan (akun pembeli setelah pilih lokasi). */
export function setUserGudang(key: string | null) {
  if (typeof window === "undefined") return;
  const u = getUser();
  if (!u) return;
  localStorage.setItem(USER_KEY, JSON.stringify({ ...u, gudang: key }));
}

export function clearSession() {
  if (typeof window === "undefined") return;
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(USER_KEY);
  clearSessionCaches();
}
