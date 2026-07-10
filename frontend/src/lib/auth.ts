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

/**
 * Simpan sesi. `remember=true` → localStorage (bertahan lintas restart browser);
 * `remember=false` → sessionStorage (hilang saat tab ditutup — lebih aman untuk
 * komputer gudang/bersama). Token JWT tetap terbaca JS, jadi pertahanan utama
 * terhadap XSS adalah CSP + tidak menyisakan token lebih lama dari perlu.
 */
export function saveSession(token: string, user: UserOut, remember = true) {
  if (typeof window === "undefined") return;
  // Mulai sesi bersih: buang cache & token milik sesi/akun sebelumnya di KEDUA store.
  clearSessionCaches();
  clearSession();
  const store = remember ? localStorage : sessionStorage;
  store.setItem(TOKEN_KEY, token);
  store.setItem(USER_KEY, JSON.stringify(user));
}

function _get(key: string): string | null {
  if (typeof window === "undefined") return null;
  return localStorage.getItem(key) ?? sessionStorage.getItem(key);
}

export function getToken(): string | null {
  return _get(TOKEN_KEY);
}

export function getUser(): UserOut | null {
  const raw = _get(USER_KEY);
  if (!raw) return null;
  try {
    return JSON.parse(raw) as UserOut;
  } catch {
    return null;
  }
}

/** Halaman tujuan setelah login sesuai role. Pembeli wajib pilih lokasi dulu;
 *  admin mendarat di Dashboard Command Center; internal lain (cabang/user) ke Beranda. */
export function landingPath(user: UserOut | null): string {
  if (!user) return "/login";
  if (user.role === "pembeli") return user.gudang ? "/search" : "/pilih-lokasi";
  if (user.role === "admin") return "/dashboard";
  return "/beranda";
}

/** Perbarui lokasi gudang user tersimpan (akun pembeli setelah pilih lokasi). */
export function setUserGudang(key: string | null) {
  if (typeof window === "undefined") return;
  const u = getUser();
  if (!u) return;
  // Tulis ke store tempat user tersimpan (local ATAU session), jangan bikin ganda.
  const store = localStorage.getItem(USER_KEY) !== null ? localStorage : sessionStorage;
  store.setItem(USER_KEY, JSON.stringify({ ...u, gudang: key }));
}

export function clearSession() {
  if (typeof window === "undefined") return;
  [localStorage, sessionStorage].forEach((s) => {
    s.removeItem(TOKEN_KEY);
    s.removeItem(USER_KEY);
  });
  clearSessionCaches();
}

// Alasan user terlempar keluar (mis. akunnya dipakai login di perangkat lain).
// Ditulis saat 401 diterima, dibaca SEKALI oleh halaman login lalu dibuang —
// tanpa ini user cuma mendarat di form login tanpa tahu apa yang terjadi.
const LOGOUT_REASON_KEY = "maspart_logout_reason";

export function setLogoutReason(msg: string) {
  if (typeof window === "undefined") return;
  try {
    sessionStorage.setItem(LOGOUT_REASON_KEY, msg);
  } catch {
    /* ignore */
  }
}

export function takeLogoutReason(): string {
  if (typeof window === "undefined") return "";
  try {
    const v = sessionStorage.getItem(LOGOUT_REASON_KEY) ?? "";
    sessionStorage.removeItem(LOGOUT_REASON_KEY);
    return v;
  } catch {
    return "";
  }
}
