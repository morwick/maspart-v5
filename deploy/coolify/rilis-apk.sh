#!/usr/bin/env bash
# Menyiapkan APK rilis untuk halaman /download — dijalankan DI KOMPUTER LOKAL
# (Git Bash) SETELAH `flutter build apk --release --split-per-abi`.
#
#   bash deploy/coolify/rilis-apk.sh [path-apk] [path-pubspec]
#
# Kerjanya: salin APK ke frontend/public/maspart.apk lalu TULIS
# frontend/public/maspart-apk.json berisi versi (dibaca dari pubspec.yaml),
# ukuran byte, sha256, dan tanggal.
#
# ⚠️ Kenapa ada: versi APK dulu diketik tangan di TIGA tempat (pubspec.yaml,
# app_config.latest_name, dan konstanta APP_VERSION di halaman /download) dan
# TERBUKTI melenceng dua kali — halaman masih 2.0.0 saat aplikasi 2.1.4, lalu
# 2.2.2 saat /api/app/meta sudah 2.2.3. Halaman /download sekarang MEMBACA
# berkas yang dibangkitkan skrip ini, jadi satu salinan tulis-tangan hilang.
# Sisa yang MASIH manual: `app_config.latest_name` (dipakai aplikasi untuk
# memunculkan notifikasi update) — skrip mencetak nilai yang harus dipasang.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/../.." && pwd)"
APK="${1:-D:/src/maspart_mobile/build/app/outputs/flutter-apk/app-arm64-v8a-release.apk}"
PUBSPEC="${2:-D:/src/maspart_mobile/pubspec.yaml}"
TUJUAN="$REPO/frontend/public/maspart.apk"
META="$REPO/frontend/public/maspart-apk.json"

[ -f "$APK" ] || { echo "⛔ APK tak ada: $APK" >&2; exit 1; }
[ -f "$PUBSPEC" ] || { echo "⛔ pubspec tak ada: $PUBSPEC" >&2; exit 1; }

# 'version: 2.2.5+18' → nama 2.2.5, build 18
BARIS="$(grep -E '^version:' "$PUBSPEC" | head -1 | sed 's/^version:[[:space:]]*//')"
VERSI="${BARIS%%+*}"
BUILD="${BARIS##*+}"
[ -n "$VERSI" ] && [ -n "$BUILD" ] || { echo "⛔ gagal membaca versi dari pubspec" >&2; exit 1; }

cp -f "$APK" "$TUJUAN"
BYTE="$(stat -c %s "$TUJUAN" 2>/dev/null || stat -f %z "$TUJUAN")"
SHA="$(sha256sum "$TUJUAN" | cut -d' ' -f1)"
TGL="$(date -u +%Y-%m-%d)"

cat > "$META" <<JSON
{
  "_catatan": "DIBANGKITKAN oleh deploy/coolify/rilis-apk.sh — jangan diedit tangan.",
  "versi": "$VERSI",
  "build": $BUILD,
  "byte": $BYTE,
  "sha256": "$SHA",
  "tanggal": "$TGL",
  "abi": "arm64-v8a"
}
JSON

echo "✅ APK siap dilayani:"
echo "   $TUJUAN  ($BYTE byte)"
echo "   sha256 $SHA"
echo "   versi  $VERSI+$BUILD"
echo
echo "LANGKAH BERIKUTNYA (jangan dilewat):"
echo "  1) bash deploy/coolify/push.sh frontend   # mengirim public/ termasuk APK"
echo "  2) Perbarui app_config di server supaya pengguna lama DIBERI TAHU:"
echo "       latest_name = \"$VERSI\"   latest_code = $((2000 + BUILD))"
echo "     (data murni: /opt/maspart/data/app_config.json — aktif tanpa rebuild)"
