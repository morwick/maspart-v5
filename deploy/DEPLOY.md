# Deploy MASPART ke VPS Hostinger (Ubuntu 22.04/24.04) — via SSH

Arsitektur: **Nginx** (port 80) di depan; **FastAPI** (uvicorn 127.0.0.1:8001) +
**Next.js** (127.0.0.1:3000) di belakang, dijalankan **systemd**. Supabase tetap
remote. Cari-by-Foto mencocokkan dari **CSV lokal** (tanpa database).

Metode transfer: **kirim arsip lewat SSH** (tanpa GitHub). Arsip
`maspart-deploy.tar.gz` sudah dibuatkan di komputer Anda (`D:\maspart-deploy.tar.gz`,
~131 MB) dan SUDAH berisi kode + `backend/.env` + galeri CSV 316 MB. Tidak ada
`venv`/`node_modules`/cache di dalamnya (dibangun ulang di VPS).

> Ganti `<IP>` dengan IP VPS Anda di semua perintah. Asumsi login sebagai `root`.

---

## 1. (DI KOMPUTER LOKAL) Kirim arsip ke VPS

Buka **PowerShell** di komputer Anda, lalu:

```powershell
scp D:\maspart-deploy.tar.gz root@<IP>:/root/
```
(Masukkan password VPS bila diminta. Upload ~131 MB — tergantung kecepatan internet.)

## 2. (DI VPS) Masuk SSH & ekstrak

```bash
ssh root@<IP>

mkdir -p /opt/maspart
tar xzf /root/maspart-deploy.tar.gz -C /opt/maspart --strip-components=1
cd /opt/maspart
ls   # harus terlihat: backend  frontend  data  deploy  ...
```

## 3. Pasang prasyarat sistem (python, node, nginx, swap 2GB)

```bash
bash /opt/maspart/deploy/setup-vps.sh
```

## 4. Backend — venv + torch (CPU) + dependensi

```bash
cd /opt/maspart/backend
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip wheel

# torch versi CPU (versi default menarik CUDA ~2GB sia-sia)
pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu

# sisa dependensi
pip install -r requirements.txt
deactivate
```

## 5. Backend — sesuaikan `.env` untuk produksi

`.env` sudah ikut terkirim (berisi nilai Supabase). Tinggal edit untuk produksi:

```bash
nano /opt/maspart/backend/.env
```
Pastikan/isi:
```
APP_ENV=prod
JWT_SECRET=<hasil-generate-di-bawah>      # WAJIB 32+ karakter acak; kalau prod, server tdk start bila lemah
PUBLIC_BASE_URL=http://<IP>
```
Generate JWT_SECRET acak:
```bash
python3 -c "import secrets; print(secrets.token_urlsafe(48))"
```
(Galeri CSV sudah ada di `/opt/maspart/data/` — otomatis terdeteksi, tidak perlu setting.)

## 6. Frontend — env + build

```bash
cd /opt/maspart/frontend
echo "NEXT_PUBLIC_API_BASE=http://<IP>" > .env.production
npm ci
npm run build      # butuh RAM — swap dari langkah 3 membantu
```

## 7. Jalankan sebagai service systemd

```bash
cp /opt/maspart/deploy/maspart-backend.service  /etc/systemd/system/
cp /opt/maspart/deploy/maspart-frontend.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now maspart-backend
systemctl enable --now maspart-frontend
```
Pantau backend start pertama (load torch + galeri CSV, ~10–30 dtk):
```bash
journalctl -u maspart-backend -f
```
Tunggu `Application startup complete` dan `galeri lokal dimuat: ... embedding`, lalu Ctrl-C.

## 8. Nginx

```bash
cp /opt/maspart/deploy/nginx-maspart.conf /etc/nginx/sites-available/maspart
ln -sf /etc/nginx/sites-available/maspart /etc/nginx/sites-enabled/maspart
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl reload nginx
```

## 9. Verifikasi

```bash
curl -s http://127.0.0.1:8001/health     # {"status":"ok",...}
curl -s -I http://127.0.0.1/             # 200 (Next.js via Nginx)
```
Buka browser: **http://<IP>** → halaman login MASPART. 🎉

---

## Update / redeploy nanti (tanpa GitHub)

Di **komputer lokal**, bungkus ulang & kirim (saya bisa bantu buatkan arsipnya lagi):
```powershell
scp D:\maspart-deploy.tar.gz root@<IP>:/root/
```
Di **VPS**:
```bash
tar xzf /root/maspart-deploy.tar.gz -C /opt/maspart --strip-components=1
cd /opt/maspart/frontend && npm ci && npm run build      # bila frontend berubah
systemctl restart maspart-backend maspart-frontend
```

## Reload galeri CSV (setelah CSV diperbarui) — tanpa restart
Login admin → menu **Image Index** → tombol **↻ Reload Galeri**.

---

## (NANTI) Tambah domain + HTTPS
1. Arahkan A record domain → `<IP>`.
2. Edit `server_name _;` di `/etc/nginx/sites-available/maspart` jadi domain Anda; `systemctl reload nginx`.
3. `apt-get install -y certbot python3-certbot-nginx && certbot --nginx -d app.domainanda.com`
4. `echo "NEXT_PUBLIC_API_BASE=https://app.domainanda.com" > /opt/maspart/frontend/.env.production`
   lalu `cd /opt/maspart/frontend && npm run build && systemctl restart maspart-frontend`.

---

## Troubleshooting cepat
| Gejala | Cek |
|---|---|
| Backend gagal start | `journalctl -u maspart-backend -e` — sering JWT_SECRET lemah (APP_ENV=prod) |
| 502 Bad Gateway | `systemctl status maspart-backend maspart-frontend` |
| Cari-by-Foto kosong | Galeri CSV ada di `/opt/maspart/data/`? |
| RAM penuh saat `npm run build` | `swapon --show` — pastikan swap aktif |
| Login gagal semua | `.env` Supabase benar? `APP_ENV=prod` + JWT_SECRET valid? |
