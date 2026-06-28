#!/usr/bin/env bash
# MASPART — prasyarat sistem untuk VPS Ubuntu 22.04/24.04 (jalankan sebagai root).
#   bash deploy/setup-vps.sh
# Memasang: python venv+pip, nginx, git, Node.js 20, build tools, dan swap 2GB
# (penting di VPS 4GB supaya build Next.js / load torch tidak kehabisan RAM).
set -euo pipefail

echo "==> Update apt"
apt-get update -y

echo "==> Paket dasar (python, nginx, git, build tools)"
apt-get install -y python3-venv python3-pip python3-dev nginx git curl ca-certificates build-essential

echo "==> Node.js 20 (NodeSource)"
if ! command -v node >/dev/null 2>&1 || [ "$(node -v | cut -d. -f1 | tr -d v)" -lt 20 ]; then
  curl -fsSL https://deb.nodesource.com/setup_20.x | bash -
  apt-get install -y nodejs
fi
echo "    node $(node -v) / npm $(npm -v)"

echo "==> Swap 2GB (kalau belum ada swap apa pun)"
if ! swapon --show | grep -q .; then
  fallocate -l 2G /swapfile || dd if=/dev/zero of=/swapfile bs=1M count=2048
  chmod 600 /swapfile
  mkswap /swapfile
  swapon /swapfile
  grep -q '/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
  echo "    swap aktif."
else
  echo "    swap sudah ada, lewati."
fi

echo "==> Firewall (UFW): izinkan OpenSSH + HTTP"
if command -v ufw >/dev/null 2>&1; then
  ufw allow OpenSSH || true
  ufw allow 'Nginx HTTP' || true
  echo "    (jalankan 'ufw enable' manual bila ingin mengaktifkan firewall)"
fi

echo "==> Selesai. Lanjut ke langkah aplikasi di deploy/DEPLOY.md"
