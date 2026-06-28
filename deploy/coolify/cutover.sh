#!/usr/bin/env bash
# CUTOVER: pindahkan trafik maspart.tech dari systemd+nginx → container (Coolify).
# Dijalankan DI SERVER (root). Idempoten-ish; cetak tiap langkah.
# Rollback: jalankan rollback.sh.
set -uo pipefail

echo "== 1. Hapus route Traefik file-provider lama (maspart.yaml) =="
mv /data/coolify/proxy/dynamic/maspart.yaml /root/maspart.yaml.bak 2>/dev/null \
  && echo "  maspart.yaml -> /root/maspart.yaml.bak" || echo "  (sudah tidak ada)"

echo "== 2. Stop service systemd lama (backend, frontend, nginx) =="
systemctl stop maspart-frontend maspart-backend nginx
systemctl is-active maspart-backend maspart-frontend nginx || true

echo "== 3. Pastikan container Coolify menyala =="
# Coolify yang seharusnya men-deploy; baris ini cuma untuk verifikasi manual.
docker ps --filter "name=maspart" --format "  {{.Names}} {{.Status}}"

echo "== 4. Tunggu backend warmup (torch+DINOv2+galeri) lalu cek =="
for i in $(seq 1 30); do
  if curl -sf -m 5 https://maspart.tech/health >/dev/null; then
    echo "  /health OK setelah ~$((i*5))s"; break
  fi
  sleep 5
done

echo "== 5. Verifikasi akhir =="
curl -s -o /dev/null -w "  https://maspart.tech -> %{http_code}\n" https://maspart.tech
curl -s https://maspart.tech/health; echo
echo "Selesai. Kalau ada masalah: bash rollback.sh"
