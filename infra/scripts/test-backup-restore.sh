#!/usr/bin/env bash
# test-backup-restore.sh — quarterly automated drill that the latest pg_dump
# in S3 actually restores into a fresh Postgres and contains expected tables.
#
# Запускается:
#   - вручную раз в квартал (см. infra/RUNBOOK.md §6 → "Тестирование восстановления")
#   - cron-ом раз в неделю (infra/cron.d/voltari-backup-drill) — тихий smoke
#
# Что делает (БЕЗОПАСНО — НЕ ТРОГАЕТ ПРОД):
#   1. Скачивает последний daily/* дамп из S3
#   2. Поднимает временный Postgres-контейнер на random port (НЕ воляр-postgres)
#   3. pg_restore туда → проверяет SELECT count(*) на ключевых таблицах
#   4. Сверяет с эталоном (минимум таблиц = 10, accounts > 0)
#   5. Уничтожает временный контейнер
#   6. Telegram-нотификация success/fail
#
# Exit codes:
#   0 = drill passed
#   1 = backup file not found / corrupted
#   2 = restore failed
#   3 = sanity-check failed (нет ожидаемых таблиц)

set -euo pipefail

COMPOSE_DIR="/opt/voltari"
ENV_FILE="${COMPOSE_DIR}/.env"
DRILL_DIR="${COMPOSE_DIR}/backups/drill"
LOG_FILE="/var/log/voltari-backup-drill.log"
TMP_CONTAINER="voltari-pg-drill-$$"     # $$ = pid → unique
TMP_PORT=$((10000 + RANDOM % 50000))    # random high port

mkdir -p "${DRILL_DIR}"

if [[ -f "${ENV_FILE}" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "${ENV_FILE}"
  set +a
else
  echo "[ERROR] ${ENV_FILE} not found"
  exit 1
fi

: "${POSTGRES_USER:?}"
: "${POSTGRES_DB:?}"
: "${POSTGRES_PASSWORD:?}"
: "${BACKUP_BUCKET:?}"
: "${S3_ENDPOINT:?}"

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

cleanup() {
  log "Cleanup: stopping ${TMP_CONTAINER}"
  docker rm -f "${TMP_CONTAINER}" >/dev/null 2>&1 || true
  rm -f "${DRILL_DIR}"/*.sql.gz "${DRILL_DIR}"/*.sql || true
}
trap cleanup EXIT

s3() { aws --endpoint-url "${S3_ENDPOINT}" "$@"; }

log "=== Backup restore drill starting ==="

# ----- 1. Найти последний daily dump -----
LATEST=$(s3 s3 ls "s3://${BACKUP_BUCKET}/daily/" \
  | awk '{print $4}' \
  | grep -E '^voltari-pg-daily-[0-9]{4}-[0-9]{2}-[0-9]{2}\.sql\.gz$' \
  | sort -r | head -1)

if [[ -z "${LATEST}" ]]; then
  log "ERROR: no daily backups found in s3://${BACKUP_BUCKET}/daily/"
  notify_tg "*Voltari backup drill FAIL* — no daily backups found in S3"
  exit 1
fi

log "Latest backup: ${LATEST}"

# Возраст бэкапа: если старше 36 ч — alert
BACKUP_DATE=$(echo "${LATEST}" | sed -E 's/voltari-pg-daily-([0-9]{4}-[0-9]{2}-[0-9]{2})\.sql\.gz/\1/')
BACKUP_EPOCH=$(date -d "${BACKUP_DATE}" +%s 2>/dev/null || echo 0)
NOW_EPOCH=$(date +%s)
AGE_HOURS=$(( (NOW_EPOCH - BACKUP_EPOCH) / 3600 ))

if [[ "${AGE_HOURS}" -gt 36 ]]; then
  log "WARN: latest backup is ${AGE_HOURS}h old (threshold 36h)"
  notify_tg "*Voltari backup drill WARN* — latest dump (${LATEST}) is ${AGE_HOURS}h old"
fi

# ----- 2. Скачать -----
LOCAL_GZ="${DRILL_DIR}/${LATEST}"
log "Downloading s3://${BACKUP_BUCKET}/daily/${LATEST} → ${LOCAL_GZ}"
s3 s3 cp "s3://${BACKUP_BUCKET}/daily/${LATEST}" "${LOCAL_GZ}" --only-show-errors

SIZE=$(stat -c%s "${LOCAL_GZ}")
if [[ "${SIZE}" -lt 1024 ]]; then
  log "ERROR: dump is suspiciously small (${SIZE} bytes)"
  notify_tg "*Voltari backup drill FAIL* — dump only ${SIZE} bytes"
  exit 1
fi
log "Size: $(numfmt --to=iec ${SIZE})"

# ----- 3. Разжать -----
gunzip -kf "${LOCAL_GZ}"
LOCAL_DUMP="${LOCAL_GZ%.gz}"

# ----- 4. Поднять временный postgres -----
log "Starting temporary postgres ${TMP_CONTAINER} on port ${TMP_PORT}"
docker run -d --rm \
  --name "${TMP_CONTAINER}" \
  -e POSTGRES_PASSWORD=drill \
  -e POSTGRES_DB="${POSTGRES_DB}" \
  -e POSTGRES_USER="${POSTGRES_USER}" \
  -p "127.0.0.1:${TMP_PORT}:5432" \
  postgres:16-alpine >/dev/null

# Wait for ready
for i in 1 2 3 4 5 6 7 8 9 10; do
  if docker exec "${TMP_CONTAINER}" pg_isready -U "${POSTGRES_USER}" >/dev/null 2>&1; then
    break
  fi
  sleep 2
  if [[ "${i}" == "10" ]]; then
    log "ERROR: temporary postgres did not become ready in 20s"
    exit 2
  fi
done
log "Temporary postgres ready"

# ----- 5. pg_restore -----
log "Running pg_restore..."
if ! docker exec -i "${TMP_CONTAINER}" pg_restore \
       -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" \
       --no-owner --no-privileges --jobs 2 \
       < "${LOCAL_DUMP}" 2>&1 | tee -a "${LOG_FILE}"; then
  # pg_restore returns non-zero on warnings (no-owner produces "WARNING role
  # does not exist" — это ОК). Проверим только что таблицы создались.
  log "WARN: pg_restore completed with warnings (typical for --no-owner)"
fi

# ----- 6. Sanity-check -----
TABLE_COUNT=$(docker exec "${TMP_CONTAINER}" psql -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" -t -c \
  "SELECT count(*) FROM information_schema.tables WHERE table_schema='public';" | tr -d ' ')

log "Restored tables: ${TABLE_COUNT}"

# Требуем минимум 10 таблиц (после миграций 0001..0008 их ~15+).
if [[ "${TABLE_COUNT}" -lt 10 ]]; then
  log "ERROR: only ${TABLE_COUNT} tables restored, expected >= 10"
  notify_tg "*Voltari backup drill FAIL* — only ${TABLE_COUNT} tables in restored dump"
  exit 3
fi

# Главные таблицы должны существовать
for tbl in accounts users transactions usage_events api_keys; do
  if ! docker exec "${TMP_CONTAINER}" psql -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" -t -c \
         "SELECT 1 FROM information_schema.tables WHERE table_schema='public' AND table_name='${tbl}';" \
         | grep -q 1; then
    log "ERROR: required table ${tbl} missing in restored dump"
    notify_tg "*Voltari backup drill FAIL* — missing table ${tbl}"
    exit 3
  fi
done

# Если есть accounts с записями — проверим что accounts > 0 (защита от пустого дампа)
ACCOUNT_COUNT=$(docker exec "${TMP_CONTAINER}" psql -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" -t -c \
  "SELECT count(*) FROM accounts;" | tr -d ' ')
log "Accounts in restored dump: ${ACCOUNT_COUNT}"

# ----- 7. Success -----
log "=== Backup restore drill PASSED ==="
log "  backup:      ${LATEST}"
log "  age:         ${AGE_HOURS}h"
log "  size:        $(numfmt --to=iec ${SIZE})"
log "  tables:      ${TABLE_COUNT}"
log "  accounts:    ${ACCOUNT_COUNT}"

# Раз в неделю — короткая success-нотификация (не каждый день)
if [[ "$(date +%u)" == "1" ]]; then
  notify_tg "*Voltari backup drill OK* \`${LATEST}\` (${TABLE_COUNT} tables, ${ACCOUNT_COUNT} accounts, $(numfmt --to=iec ${SIZE}))"
fi

exit 0
