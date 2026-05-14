# syntax=docker/dockerfile:1.7
#
# Voltari Gateway — Alpine multi-stage build (TD-033).
#
# Цель: ~220 MB (vs 386 MB на python:3.12-slim в Sprint 2).
# Главные размерные потери: glibc-libpq vs musl-libpq, отсутствие
# `apt`-метаданных, дроп многих vendored shared libs.
#
# Build-time deps (только в builder-stage, в runtime их нет):
#   build-base, postgresql-dev, libffi-dev, openssl-dev — для psycopg/asyncpg/cryptography
#   git — некоторые wheels проверяют git при build из sdist
#
# Runtime-stage:
#   libpq      — runtime для asyncpg/psycopg
#   libstdc++  — нужен для multiple wheels (cryptography, hiredis, asyncpg)
#   libffi     — runtime для cffi-based libs (cryptography, argon2-cffi)
#   ca-certificates — корневые сертификаты для httpx/openai SDK
#   tzdata     — нужен для UTC-aware datetime (asyncpg/SQLAlchemy)
#
# TD-006 (Sprint 3 Поток H) — curl убран из runtime, HEALTHCHECK теперь
# использует встроенный ``python -c "import urllib.request; ..."`` (минус
# ~5 MB на runtime image, минус 1 пакет в supply chain).

# ---------- builder ----------
FROM python:3.12-alpine AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apk add --no-cache \
        build-base \
        postgresql-dev \
        libffi-dev \
        openssl-dev \
        git

WORKDIR /build
COPY pyproject.toml README.md ./
COPY voltari_gateway ./voltari_gateway
COPY alembic ./alembic
COPY alembic.ini ./

# Production install — runtime deps only. Dev tooling (pytest, ruff, mypy,
# testcontainers, fakeredis, respx) лежит за `[dev]` extra и в runtime-образ
# не попадает.
#
# Multi-arch wheel coverage:
#   - asyncpg: musllinux wheels с 0.30+
#   - cryptography: musllinux wheels с 41+
#   - argon2-cffi, httptools, uvloop: тоже musllinux-ready
#   - psycopg НЕ ставим (мы на asyncpg+SQLAlchemy)
#
# Если какой-либо wheel оказался без musllinux варианта — pip соберёт
# его из sdist через build-base + соответствующие dev-headers (выше).
RUN pip install --upgrade pip && \
    pip install --prefix=/install "."

# Удаляем артефакты build-времени, которые могут случайно попасть в /install
RUN find /install -depth \
        \( -name '__pycache__' -o -name '*.pyc' -o -name '*.pyo' \) \
        -exec rm -rf {} + && \
    find /install -name 'tests' -type d -prune -exec rm -rf {} + && \
    find /install -name '*.dist-info' -type d -exec sh -c \
        'rm -rf "$1"/RECORD "$1"/INSTALLER "$1"/REQUESTED' _ {} \; || true

# ---------- runtime ----------
FROM python:3.12-alpine AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    PORT=8000

# Runtime-only пакеты. НЕ ставим build-base, postgresql-dev, libffi-dev — они
# нужны были только в builder-stage.
#
# TD-045 (Sprint 5) — ttf-dejavu установлен в runtime для Comply Pack PDF
# (`voltari_gateway/billing/documents.py`). Без него fpdf2 не имеет Cyrillic
# fallback, и документы с русским текстом транслитерируются в латиницу.
# Размер: ~3.5 MB — приемлемо. Пакет ставит файлы в /usr/share/fonts/dejavu/
# (этот путь уже зашит в `_find_unicode_font()` как первый кандидат для
# не-env override'нутых случаев).
RUN apk add --no-cache \
        libpq \
        libstdc++ \
        libffi \
        ca-certificates \
        tzdata \
        ttf-dejavu \
    && addgroup -S voltari \
    && adduser -S -G voltari -h /app -s /sbin/nologin voltari

# Russian Trusted Root CA (Минцифры) — нужен для GigaChat / Sber:
# их API хостится на ngw.devices.sberbank.ru и подписан российским CA,
# которого нет в стандартном truststore. Без этого Sber-провайдер падает
# с CERTIFICATE_VERIFY_FAILED. PEM-файлы скачаны с https://www.gosuslugi.ru/crt
# (root + sub) — публичные сертификаты, безопасны для commit.
COPY certs/*.crt /usr/local/share/ca-certificates/
RUN update-ca-certificates

# Для stdlib ssl и любых non-certifi путей.
ENV SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt \
    REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt

WORKDIR /app

COPY --from=builder /install /usr/local
COPY --chown=voltari:voltari voltari_gateway ./voltari_gateway
COPY --chown=voltari:voltari alembic ./alembic
COPY --chown=voltari:voltari alembic.ini ./

# httpx читает CA bundle через certifi, не из системы. Перезаписываем
# certifi-bundle нашим системным (содержит стандартные CAs + российский
# Минцифры) — иначе Sber падает с CERTIFICATE_VERIFY_FAILED, а standalone-libs
# (httpx, requests, urllib3) продолжают игнорить /etc/ssl. Шаг идёт после
# COPY --from=builder, потому что certifi устанавливается в этом install.
RUN cp /etc/ssl/certs/ca-certificates.crt \
    "$(python -c 'import certifi; print(certifi.where())')"

USER voltari

EXPOSE 8000

# HEALTHCHECK — Python urllib (TD-006). Avoids the curl runtime dependency.
# urlopen() raises a non-zero exit on connection errors / 5xx (read() reads
# the body, which 200 returns; 4xx/5xx propagate as HTTPError → non-zero).
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request, sys; urllib.request.urlopen('http://localhost:${PORT}/healthz', timeout=4).read(); sys.exit(0)" || exit 1

# TD-007 (Sprint 3 Поток H) — gunicorn config:
#   * --workers ${WEB_CONCURRENCY:-2}: scale via env without rebuilds.
#   * --worker-tmp-dir /dev/shm: avoid disk I/O for the heartbeat file.
#   * --max-requests 1000 + jitter 100: graceful worker recycle to clamp
#     long-tail memory growth (asyncpg, httpx connection pools).
CMD ["sh", "-c", "exec gunicorn voltari_gateway.main:app \
     --worker-class uvicorn.workers.UvicornWorker \
     --workers ${WEB_CONCURRENCY:-2} \
     --worker-tmp-dir /dev/shm \
     --max-requests 1000 \
     --max-requests-jitter 100 \
     --bind 0.0.0.0:8000 \
     --access-logfile - \
     --error-logfile - \
     --timeout 120 \
     --graceful-timeout 30"]
