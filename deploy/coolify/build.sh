#!/usr/bin/env bash
# Rebuild image MASPART setelah update kode — lalu DEPLOY manual lewat Coolify.
#
# Pemakaian (di server, sebagai root):
#   bash /opt/maspart/deploy/coolify/build.sh            # build backend + frontend
#   bash /opt/maspart/deploy/coolify/build.sh backend    # backend saja
#   bash /opt/maspart/deploy/coolify/build.sh frontend   # frontend saja
#
# Setelah selesai: buka Coolify → Projects → My first project → production →
# service "maspart" → klik REDEPLOY. (Tidak perlu API token.)
set -euo pipefail
WHAT="${1:-all}"
cd /opt/maspart

if [ "$WHAT" = "all" ] || [ "$WHAT" = "backend" ]; then
  echo "== build maspart-backend:latest =="
  docker build -t maspart-backend:latest ./backend
fi
if [ "$WHAT" = "all" ] || [ "$WHAT" = "frontend" ]; then
  echo "== build maspart-frontend:latest =="
  docker build --build-arg NEXT_PUBLIC_API_BASE=https://maspart.tech -t maspart-frontend:latest ./frontend
fi

echo
echo "Image siap. LANGKAH TERAKHIR: Coolify dashboard → service 'maspart' → REDEPLOY."
echo "(Redeploy akan recreate container memakai image baru.)"
