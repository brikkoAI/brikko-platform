# syntax=docker/dockerfile:1.7

# ---------- deps ----------
FROM node:22-alpine AS deps
WORKDIR /app

# Используем npm (lock-файл будет создан при первой сборке локально).
# Если есть pnpm-lock.yaml — переключай на `pnpm install --frozen-lockfile`.
COPY package.json package-lock.json* ./
RUN npm install --no-audit --no-fund

# ---------- builder ----------
FROM node:22-alpine AS builder
WORKDIR /app

# Корневые конфиги
COPY package.json package-lock.json* ./
COPY tsconfig.json next.config.mjs tailwind.config.ts postcss.config.js ./
COPY public ./public
COPY src ./src

# Зависимости из deps-stage
COPY --from=deps /app/node_modules ./node_modules

# MSW worker нужен в public/ для dev-режима, но в production он отключён через
# двойную защиту в src/mocks/index.ts (NODE_ENV + hostname check).
# Если MSW worker не сгенерирован — не страшно, в production он не подгружается.

# NEXT_PUBLIC_* переменные запекаются в JS-бандл на build time.
# Без них клиент видит ENV_BASE=undefined и переключается на MSW-моки → 404 в продакшне.
# Значения должны прийти из docker-compose build.args.
ARG NEXT_PUBLIC_API_BASE_URL=http://localhost:8000/v1
ARG NEXT_PUBLIC_FRONTEND_URL=http://localhost:3000
ARG NEXT_PUBLIC_BRAND_NAME=Brikko
ARG NEXT_PUBLIC_AUTH_MIDDLEWARE_DISABLED=false
ENV NEXT_PUBLIC_API_BASE_URL=$NEXT_PUBLIC_API_BASE_URL \
    NEXT_PUBLIC_FRONTEND_URL=$NEXT_PUBLIC_FRONTEND_URL \
    NEXT_PUBLIC_BRAND_NAME=$NEXT_PUBLIC_BRAND_NAME \
    NEXT_PUBLIC_AUTH_MIDDLEWARE_DISABLED=$NEXT_PUBLIC_AUTH_MIDDLEWARE_DISABLED

ENV NEXT_TELEMETRY_DISABLED=1
ENV NODE_ENV=production

# Standalone-сборка для минимального runtime-образа
RUN npm run build

# ---------- runtime ----------
FROM node:22-alpine AS runtime
WORKDIR /app

ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    HOSTNAME=0.0.0.0

RUN addgroup -S voltari && adduser -S -G voltari voltari

# Копируем standalone-сборку Next.js
COPY --from=builder --chown=voltari:voltari /app/.next/standalone ./
COPY --from=builder --chown=voltari:voltari /app/.next/static ./.next/static
COPY --from=builder --chown=voltari:voltari /app/public ./public

USER voltari

EXPOSE 3000

# node:22-alpine standalone image не содержит wget — используем встроенный
# fetch (Node 18+). Должно совпадать с healthcheck'ом в infra/docker-compose.yml
# чтобы `docker run voltari-web` без compose тоже корректно репортил здоровье.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD node -e "fetch('http://localhost:3000/api/health').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"

CMD ["node", "server.js"]
