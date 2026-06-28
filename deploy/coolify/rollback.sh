#!/usr/bin/env bash
# ROLLBACK: kembalikan trafik maspart.tech ke systemd+nginx (setup lama yang teruji).
# Dijalankan DI SERVER (root).
set -uo pipefail

echo "== 1. Stop container Coolify (lepas RAM untuk torch systemd) =="
docker ps --filter "name=maspart" -q | xargs -r docker stop

echo "== 2. Kembalikan route Traefik file-provider lama =="
mv /root/maspart.yaml.bak /data/coolify/proxy/dynamic/maspart.yaml 2>/dev/null \
  && echo "  maspart.yaml dipulihkan" || echo "  (backup tidak ada — cek manual)"

echo "== 3. Start service systemd lama =="
systemctl start maspart-backend
systemctl start maspart-frontend
systemctl start nginx

echo "== 4. Tunggu backend warmup lalu verifikasi =="
for i in $(seq 1 30); do
  curl -sf -m 5 https://maspart.tech/health >/dev/null && { echo "  /health OK (~$((i*5))s)"; break; }
  sleep 5
done
curl -s -o /dev/null -w "  https://maspart.tech -> %{http_code}\n" https://maspart.tech
curl -s https://maspart.tech/health; echo
echo "Rollback selesai."
