#!/usr/bin/env bash
# Jalankan DI KOMPUTER LOKAL (Git Bash). Kirim kode terbaru ke server + rebuild image.
# Setelah ini tinggal: Coolify -> service "maspart" -> REDEPLOY.
#
# Pemakaian (dari root repo):
#   bash deploy/coolify/push.sh            # backend + frontend
#   bash deploy/coolify/push.sh backend    # backend saja
#   bash deploy/coolify/push.sh frontend   # frontend saja
set -euo pipefail
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
HOST=root@maspart.tech
WHAT="${1:-all}"

# ⚠️ scp MENIMPA tapi TAK MENGHAPUS file yang sudah tak ada di repo → file basi
# (mis. modul yang di-rename) tertinggal & bisa mematikan build. git bash lokal
# tak punya rsync, jadi kita BERSIHKAN dir kode di server SEBELUM scp (setara
# rsync --delete). Aman: hanya dir KODE — data runtime ada di bind-mount
# /opt/maspart/data yang TERPISAH & tak disentuh.
if [ "$WHAT" = "all" ] || [ "$WHAT" = "backend" ]; then
  echo "== bersihkan + kirim backend =="
  ssh "$HOST" "rm -rf /opt/maspart/backend/app /opt/maspart/backend/shared"
  scp -r "$REPO/backend/app" "$REPO/backend/shared" \
         "$REPO/backend/requirements.txt" "$REPO/backend/Dockerfile" \
         "$HOST:/opt/maspart/backend/" 2>/dev/null || \
  scp -r "$REPO/backend/app" "$REPO/backend/shared" "$REPO/backend/requirements.txt" \
         "$HOST:/opt/maspart/backend/"
fi
if [ "$WHAT" = "all" ] || [ "$WHAT" = "frontend" ]; then
  echo "== bersihkan + kirim frontend =="
  ssh "$HOST" "rm -rf /opt/maspart/frontend/src"
  # ⚠️ next.config.ts WAJIB ikut: ia memuat CSP/security headers. 2026-08-29
  # 'wasm-unsafe-eval' (viewer 3D) tidak tayang karena file ini tak terkirim →
  # server membangun dengan config lama.
  scp -r "$REPO/frontend/src" "$REPO/frontend/public" \
         "$REPO/frontend/package.json" "$REPO/frontend/package-lock.json" \
         "$REPO/frontend/next.config.ts" "$REPO/frontend/tsconfig.json" \
         "$REPO/frontend/postcss.config.mjs" \
         "$HOST:/opt/maspart/frontend/"
fi

echo "== rebuild image di server =="
ssh "$HOST" "bash /opt/maspart/deploy/coolify/build.sh $WHAT"

echo
echo "SELESAI kirim + build."
echo "LANGKAH TERAKHIR: buka Coolify -> Projects -> My first project -> production"
echo "  -> service 'maspart' -> tombol REDEPLOY."
