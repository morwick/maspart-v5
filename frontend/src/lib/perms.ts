"use client";

import { getMyPermissions, type MyPermissions } from "./api";
import { getToken } from "./auth";

// Versi cache — naikkan saat daftar menu/izin berubah agar cache lama otomatis
// diabaikan (mis. penambahan menu "Asisten AI"). v4: format baru {at, data}
// ber-TTL — perubahan centang oleh admin dulunya baru terlihat setelah
// logout/login karena cache sesi tak pernah kadaluarsa. v5: payload memuat
// `gudang_kelola` — cache v4 yang masih hidup akan menyembunyikan menu Rak &
// tombol Ubah sampai tab ditutup.
const SS_KEY = "maspart_perms_v5";

// Umur maksimum cache. Admin mengubah centang untuk user LAIN sehingga tak ada
// event client-side yang bisa dipakai — TTL pendek + refetch di mount halaman
// sudah cukup; penegakan sesungguhnya ada di server.
const TTL_MS = 60_000;

type CachedPerms = { at: number; data: MyPermissions };

function readCache(): CachedPerms | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = sessionStorage.getItem(SS_KEY);
    return raw ? (JSON.parse(raw) as CachedPerms) : null;
  } catch {
    return null;
  }
}

export function getCachedPerms(): MyPermissions | null {
  return readCache()?.data ?? null;
}

/** Ambil izin: dari cache bila masih segar (< TTL), kalau tidak fetch & simpan.
 *  Fetch gagal → pakai data basi (fail-open UX; server yang menegakkan). */
export async function ensurePerms(): Promise<MyPermissions | null> {
  const cached = readCache();
  if (cached && Date.now() - cached.at < TTL_MS) return cached.data;
  const token = getToken();
  if (!token) return cached?.data ?? null;
  try {
    const p = await getMyPermissions(token);
    try {
      sessionStorage.setItem(SS_KEY, JSON.stringify({ at: Date.now(), data: p }));
    } catch {
      /* ignore */
    }
    return p;
  } catch {
    return cached?.data ?? null;
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
