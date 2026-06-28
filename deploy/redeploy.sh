#!/usr/bin/env bash
# Redeploy MASPART ke VPS — sync KODE saja (tanpa data/venv/node_modules/.env),
# lalu rebuild frontend + restart service. Jalankan dari Git Bash di komputer lokal:
#     bash deploy/redeploy.sh
#
# Aman: file produksi di VPS TIDAK ditimpa (backend/.env, frontend/.env.production,
# folder data/ berisi galeri CSV 316MB — semua dikecualikan dari pengiriman).
set -e

HOST="root@72.62.244.233"
PROJ_DIR="/d/Project Python/maspart-main (PROJECT V5)/maspart-main"
PARENT_DIR="/d/Project Python/maspart-main (PROJECT V5)"
PKG="/tmp/maspart-update.tar.gz"

echo "==> Bungkus kode (tanpa data besar & config produksi)"
tar czf "$PKG" --force-local \
  --exclude='maspart-main/backend/venv' \
  --exclude='maspart-main/backend/.env' \
  --exclude='maspart-main/backend/.cache' \
  --exclude='maspart-main/frontend/node_modules' \
  --exclude='maspart-main/frontend/.next' \
  --exclude='maspart-main/frontend/.env.local' \
  --exclude='maspart-main/frontend/.env.production' \
  --exclude='maspart-main/frontend/tsconfig.tsbuildinfo' \
  --exclude='maspart-main/.git' \
  --exclude='maspart-main/data' \
  --exclude='maspart-main/images' \
  --exclude='maspart-main/.cache' \
  --exclude='*__pycache__*' \
  --exclude='*.pyc' \
  -C "$PARENT_DIR" "maspart-main"
echo "    paket: $(du -h "$PKG" | cut -f1)"

echo "==> Kirim ke VPS"
scp -o ConnectTimeout=20 "$PKG" "$HOST:/root/maspart-update.tar.gz"

echo "==> Ekstrak + rebuild + restart di VPS"
ssh -o ConnectTimeout=20 -o ServerAliveInterval=15 "$HOST" '
  set -e
  tar xzf /root/maspart-update.tar.gz -C /opt/maspart --strip-components=1
  echo "   - install dependensi backend bila requirements berubah"
  cd /opt/maspart/backend && ./venv/bin/pip install -q -r requirements.txt
  echo "   - rebuild frontend"
  cd /opt/maspart/frontend && npm run build > /tmp/redeploy-build.log 2>&1 \
    && test -f .next/BUILD_ID && echo "     build OK" \
    || { echo "     BUILD GAGAL:"; tail -15 /tmp/redeploy-build.log; exit 1; }
  echo "   - restart service"
  systemctl restart maspart-backend maspart-frontend
  sleep 6
  curl -s -o /dev/null -w "     backend health: %{http_code}\n" http://127.0.0.1:8001/health
  curl -s -o /dev/null -w "     situs (8090):   %{http_code}\n" http://127.0.0.1:8090/
'
echo "==> Selesai → https://maspart.tech"
