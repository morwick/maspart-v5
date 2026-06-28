# MASPART frontend (Next.js 16) — image untuk deploy via Coolify.
# Build context = folder frontend/ .
#
#   docker build -f deploy/coolify/frontend.Dockerfile \
#     --build-arg NEXT_PUBLIC_API_BASE=https://maspart.tech \
#     -t maspart-frontend:latest ./frontend
#
# PENTING: NEXT_PUBLIC_* di-inline saat BUILD, jadi base URL API ditentukan di sini.
FROM node:20-slim

WORKDIR /app

# Install SEMUA dependensi (termasuk devDependencies: tailwind, postcss, typescript)
# — jangan set NODE_ENV=production di sini, kalau tidak npm ci melewati devDeps
# dan build gagal "Cannot find module '@tailwindcss/postcss'".
COPY package.json package-lock.json ./
RUN npm ci

COPY . ./

# Sama origin dengan situs → Traefik yang mengarahkan /api & /health ke backend.
ARG NEXT_PUBLIC_API_BASE=https://maspart.tech
ENV NEXT_PUBLIC_API_BASE=$NEXT_PUBLIC_API_BASE
RUN npm run build

# Baru set production untuk runtime (next start).
ENV NODE_ENV=production
EXPOSE 3000
CMD ["npm", "run", "start", "--", "-H", "0.0.0.0", "-p", "3000"]
