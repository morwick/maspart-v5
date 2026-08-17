# MASPART backend (FastAPI + torch CPU) — image untuk deploy via Coolify.
# Build context = folder backend/ .  data/ TIDAK ikut image (di-bind-mount saat runtime).
#
#   docker build -f deploy/coolify/backend.Dockerfile -t maspart-backend:latest ./backend
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
# libgl1 + libglib2.0-0 WAJIB utk opencv (cv2) yang ditarik ddddocr (auto-login EPC
# Shantui): image slim tak punya libGL.so.1 → tanpa ini `import cv2` gagal →
# ddddocr gagal → auto-refresh token Shantui mati diam-diam.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      build-essential libgomp1 fonts-liberation libgl1 libglib2.0-0 \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --upgrade pip wheel \
 && pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu \
 && pip install -r requirements.txt

# Kode backend (app/, shared/, selftest.py, dll). .env & data di-mount, bukan di-copy.
COPY . ./

# ── Setelan alokator: menahan RSS menggelembung tanpa menyentuh kode ──────────
# Diukur 2026-08-17 di container: 15 thread, 5 arena glibc ~64 MB, dan TIDAK ada
# satu pun setelan alokator/thread terpasang. glibc membuat arena per-thread yang
# masing-masing tak pernah dikembalikan penuh ke kernel — memori "bebas" menurut
# Python tapi tetap terhitung oleh cgroup, dan cgroup-lah yang membunuh container
# ini (12 Agu & 17 Agu 02:19 UTC).
#   MALLOC_ARENA_MAX=2      → batasi jumlah arena; fragmentasi antar-thread turun
#   MALLOC_TRIM_THRESHOLD_  → 128 KB, kembalikan blok bebas ke kernel lebih agresif
#                             (menyetelnya juga MEMATIKAN ambang mmap dinamis, jadi
#                             alokasi besar tetap lewat mmap dan benar-benar hilang
#                             saat di-free — itu yang kita mau untuk tensor torch)
#   *_NUM_THREADS=1         → server 1 vCPU: kolam thread OpenMP/BLAS hanya menambah
#                             arena scratch tanpa mempercepat apa pun
# ⚠️ WAJIB env image/container, BUKAN `os.environ` di Python: glibc & OpenMP
# membacanya saat proses START, jadi menyetelnya dari dalam kode tak berpengaruh.
# ⚠️ Sengaja ditaruh DI BAWAH `pip install`: ENV di atas sana membatalkan cache
# layer torch → rebuild penuh ±15 menit di server 1 vCPU yang sedang sesak.
ENV MALLOC_ARENA_MAX=2 \
    MALLOC_TRIM_THRESHOLD_=131072 \
    OMP_NUM_THREADS=1 \
    OPENBLAS_NUM_THREADS=1 \
    MKL_NUM_THREADS=1

EXPOSE 8001
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8001"]
