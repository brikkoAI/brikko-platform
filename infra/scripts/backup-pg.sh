#!/usr/bin/env bash
# backup-pg.sh — ежедневный бэкап PostgreSQL Voltari в Selectel Object Storage.
#
# Что делает:
#   1. pg_dump через docker exec в кастомном формате
#   2. gzip
#   3. Загрузка в S3 (Selectel) под daily/weekly/monthly префиксы
#   4. Локальная очистка
#   5. Логирование в /var/log/voltari-backup.log
#   6. Telegram-нотификация при ошибке (или при подозрительно маленьком дампе)
#
# Cron (от пользователя deploy):
#   0 3 * * * /opt/voltari/scripts/backup-pg.sh >> /var/log/voltari-backup.log 2>&1
#
# Требования:
#   - awscli настроен (~/.aws/credentials с Selectel-ключами)
#   - .env содержит TG_BOT_TOKEN, TG_CHAT_ID, BACKUP_BUCKET, S3_ENDPOINT
#   - docker compose работает в /opt/voltari
#
# Retention (через S3 lifecycle на бакете, конфигурируется в ЛК Selectel):
#   - daily/    — 7 дней
#   - weekly/   — 28 дней (4 недели)
#   - monthly/  — 365 дней (12 месяцев)
# Дополнительно к lifecycle мы сами решаем что куда копировать.

set -euo pipefail

# ====== Конфиг ======
COMPOSE_DIR="/opt/voltari"
ENV_FILE="${COMPOSE_DIR}/.env"
LOG_FILE="/var/log/voltari-backup.log"
TMP_DIR="${COMPOSE_DIR}/backups"

mkdir -p "${TMP_DIR}"

# Подгружаем переменные из .env (POSTGRES_*, BACKUP_BUCKET, S3_ENDPOINT, TG_*)
if [[ -f "${ENV_FILE}" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "${ENV_FILE}"
  set +a
else
  echo "[$(date -Iseconds)] ERROR: ${ENV_FILE} not found" | tee -a "${LOG_FILE}"
  exit 1
fi

: "${POSTGRES_USER:?POSTGRES_USER not set}"
: "${POSTGRES_DB:?POSTGRES_DB not set}"
: "${BACKUP_BUCKET:?BACKUP_BUCKET not set}"
: "${S3_ENDPOINT:?S3_ENDPOINT not set}"

# Дата
TODAY=$(date +%Y-%m-%d)
DOW=$(date +%u)         # 1=Mon..7=Sun
DOM=$(date +%d)
WEEK=$(date +%V)        # ISO неделя
MONTH=$(date +%Y-%m)

# Имена файлов
DAILY_NAME="voltari-pg-daily-${TODAY}.sql.gz"
WEEKLY_NAME="voltari-pg-weekly-$(date +%Y)-W${WEEK}.sql.gz"
MONTHLY_NAME="voltari-pg-monthly-${MONTH}.sql.gz"

LOCAL_DUMP="${TMP_DIR}/${DAILY_NAME}"

# ====== Утилиты ======
log() {
  echo "[$(date -Iseconds)] $*" | tee -a "${LOG_FILE}"
}

notify_tg() {
  local msg="$1"
  if [[ -n "${TG_BOT_TOKEN:-}" && -n "${TG_CHAT_ID:-}" ]]; then
    curl -fsS -m 10 -X POST \
      "https://api.telegram.org/bot${TG_BOT_TOKEN}/sendMessage" \
      -d "chat_id=${TG_CHAT_ID}" \
      -d "parse_mode=Markdown" \
      --data-urlencode "text=${msg}" \
      >/dev/null || log "WARN: telegram notify failed"
  fi
}

on_error() {
  local exit_code=$?
  local line=$1
  local msg="*Voltari backup FAILED* line ${line}, exit ${exit_code}, host \`$(hostname)\`"
  log "ERROR: ${msg}"
  notify_tg "${msg}"
  # cleanup
  rm -f "${LOCAL_DUMP}" || true
  exit $exit_code
}
trap 'on_error ${LINENO}' ERR

s3() {
  aws --endpoint-url "${S3_ENDPOINT}" "$@"
}

# ====== Шаги ======
log "=== Backup start: ${TODAY} ==="

# 1. pg_dump → gzip
log "pg_dump ${POSTGRES_DB}..."
cd "${COMPOSE_DIR}"
docker compose exec -T postgres \
  pg_dump -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" \
          --format=custom --compress=0 --no-owner --no-privileges \
  | gzip -9 > "${LOCAL_DUMP}"

DUMP_SIZE=$(stat -c%s "${LOCAL_DUMP}")
log "Dump size: ${DUMP_SIZE} bytes"

# Sanity-check: дамп должен быть > 1 KB (пустой gzip = ~20 байт)
if [[ "${DUMP_SIZE}" -lt 1024 ]]; then
  log "ERROR: dump подозрительно маленький (${DUMP_SIZE} байт)"
  notify_tg "*Voltari backup WARN* dump only ${DUMP_SIZE} bytes — проверь БД на хосте \`$(hostname)\`"
  exit 1
fi

# 2. Daily — всегда
log "Upload daily/${DAILY_NAME}"
s3 s3 cp "${LOCAL_DUMP}" "s3://${BACKUP_BUCKET}/daily/${DAILY_NAME}" --only-show-errors

# 3. Weekly — по воскресеньям (DOW=7)
if [[ "${DOW}" == "7" ]]; then
  log "Upload weekly/${WEEKLY_NAME}"
  s3 s3 cp "${LOCAL_DUMP}" "s3://${BACKUP_BUCKET}/weekly/${WEEKLY_NAME}" --only-show-errors
fi

# 4. Monthly — 1-го числа
if [[ "${DOM}" == "01" ]]; then
  log "Upload monthly/${MONTHLY_NAME}"
  s3 s3 cp "${LOCAL_DUMP}" "s3://${BACKUP_BUCKET}/monthly/${MONTHLY_NAME}" --only-show-errors
fi

# 5. Локальная очистка
rm -f "${LOCAL_DUMP}"

# 6. Локальная ротация (на всякий случай — если файлы остались)
find "${TMP_DIR}" -name 'voltari-pg-*.sql.gz' -mtime +2 -delete || true

# 7. Server-side ротация:
#    - lifecycle policy на бакете (настраивается в Selectel UI):
#         daily/   → expire 7 days
#         weekly/  → expire 28 days
#         monthly/ → expire 365 days
#    - дополнительно скрипт здесь чистит старые daily, если lifecycle ещё не сработал

log "Cleanup old daily (>7 days) on S3..."
SEVEN_DAYS_AGO=$(date -d '7 days ago' +%Y-%m-%d)
s3 s3 ls "s3://${BACKUP_BUCKET}/daily/" 2>/dev/null \
  | awk '{print $4}' \
  | grep -E '^voltari-pg-daily-[0-9]{4}-[0-9]{2}-[0-9]{2}\.sql\.gz$' \
  | while read -r f; do
      d=$(echo "$f" | sed -E 's/voltari-pg-daily-([0-9]{4}-[0-9]{2}-[0-9]{2})\.sql\.gz/\1/')
      if [[ "${d}" < "${SEVEN_DAYS_AGO}" ]]; then
        log "  delete daily/${f}"
        s3 s3 rm "s3://${BACKUP_BUCKET}/daily/${f}" --only-show-errors || true
      fi
    done

# 8. Done
log "=== Backup OK: ${DAILY_NAME} (${DUMP_SIZE} bytes) ==="

# Раз в неделю — короткий success-пинг в Telegram (по воскресеньям после weekly)
if [[ "${DOW}" == "7" ]]; then
  notify_tg "Voltari backup OK: ${DAILY_NAME}, $(numfmt --to=iec ${DUMP_SIZE})"
fi
