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

if [ "$WHAT" = "all" ] || [ "$WHAT" = "backend" ]; then
  echo "== kirim backend =="
  scp -r "$REPO/backend/app" "$REPO/backend/shared" \
         "$REPO/backend/requirements.txt" "$REPO/backend/Dockerfile" \
         "$HOST:/opt/maspart/backend/" 2>/dev/null || \
  scp -r "$REPO/backend/app" "$REPO/backend/shared" "$REPO/backend/requirements.txt" \
         "$HOST:/opt/maspart/backend/"
fi
if [ "$WHAT" = "all" ] || [ "$WHAT" = "frontend" ]; then
  echo "== kirim frontend =="
  scp -r "$REPO/frontend/src" "$REPO/frontend/public" \
         "$REPO/frontend/package.json" "$REPO/frontend/package-lock.json" \
         "$HOST:/opt/maspart/frontend/"
fi

echo "== rebuild image di server =="
ssh "$HOST" "bash /opt/maspart/deploy/coolify/build.sh $WHAT"

echo
echo "SELESAI kirim + build."
echo "LANGKAH TERAKHIR: buka Coolify -> Projects -> My first project -> production"
echo "  -> service 'maspart' -> tombol REDEPLOY."
