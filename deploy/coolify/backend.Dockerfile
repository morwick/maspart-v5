# MASPART backend (FastAPI + torch CPU) — image untuk deploy via Coolify.
# Build context = folder backend/ .  data/ TIDAK ikut image (di-bind-mount saat runtime).
#
#   docker build -f deploy/coolify/backend.Dockerfile -t maspart-backend:latest ./backend
#
# ⛔⛔ BUKAN FILE INI yang dipakai deploy nyata. `build.sh` menjalankan
# `docker build ./backend` TANPA -f, jadi yang terbangun adalah `backend/Dockerfile`
# — dan `push.sh` pun hanya mengirim file itu ke server. Salinan di sini sudah
# pernah tertinggal (libgl1 untuk ddddocr hanya ada di backend/Dockerfile).
# Ubah `backend/Dockerfile` LEBIH DULU; isi di sini disamakan agar build manual
# dengan -f tidak diam-diam berperilaku beda. Tes menjaga keduanya tetap seragam.
#
# Catatan: torch versi CPU dipasang dari index khusus agar TIDAK menarik CUDA (~2GB sia-sia).
FROM python:3.12-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DATA_DIR=/app/data

# libgomp1 dibutuhkan torch; build-essential untuk wheel yang perlu kompilasi.
# fonts-liberation WAJIB utk render gambar exploded view EPC (resvg): angka balon
# = elemen <text font-family='Arial'> di SVG — tanpa font sistem, teks DIBUANG
# diam-diam dan gambar kehilangan semua nomornya (Liberation Sans ≈ metrik Arial).
RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential libgomp1 fonts-liberation \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --upgrade pip wheel \
 && pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu \
 && pip install -r requirements.txt

# Kode backend (app/, shared/, selftest.py, dll). .env & data di-mount, bukan di-copy.
COPY . ./

# Setelan alokator — penjelasan lengkap ada di `backend/Dockerfile` (file ITU yang
# dipakai deploy). Ditaruh di bawah `pip install` supaya cache layer torch tak batal.
ENV MALLOC_ARENA_MAX=2 \
    MALLOC_TRIM_THRESHOLD_=131072 \
    OMP_NUM_THREADS=1 \
    OPENBLAS_NUM_THREADS=1 \
    MKL_NUM_THREADS=1

EXPOSE 8001
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8001"]
