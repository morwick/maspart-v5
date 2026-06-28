"use client";

import { getMyPermissions, type MyPermissions } from "./api";
import { getToken } from "./auth";

// Versi cache — naikkan saat daftar menu/izin berubah agar cache lama otomatis
// diabaikan (mis. penambahan menu "Asisten AI").
const SS_KEY = "maspart_perms_v2";

export function getCachedPerms(): MyPermissions | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = sessionStorage.getItem(SS_KEY);
    return raw ? (JSON.parse(raw) as MyPermissions) : null;
  } catch {
    return null;
  }
}

/** Ambil izin: dari cache kalau ada, kalau tidak fetch & simpan. */
export async function ensurePerms(): Promise<MyPermissions | null> {
  const cached = getCachedPerms();
  if (cached) return cached;
  const token = getToken();
  if (!token) return null;
  try {
    const p = await getMyPermissions(token);
    try {
      sessionStorage.setItem(SS_KEY, JSON.stringify(p));
    } catch {
      /* ignore */
    }
    return p;
  } catch {
    return null;
  }
}

export function clearPerms() {
  if (typeof window === "undefined") return;
  try {
    sessionStorage.removeItem(SS_KEY);
  } catch {
    /* ignore */
  }
}
