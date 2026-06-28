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
RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential libgomp1 \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --upgrade pip wheel \
 && pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu \
 && pip install -r requirements.txt

# Kode backend (app/, shared/, selftest.py, dll). .env & data di-mount, bukan di-copy.
COPY . ./

EXPOSE 8001
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8001"]
