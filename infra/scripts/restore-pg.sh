#!/usr/bin/env bash
# restore-pg.sh — восстановление PostgreSQL Voltari из бэкапа в Selectel S3.
#
# !!! ВНИМАНИЕ — этот скрипт УНИЧТОЖАЕТ текущую БД и накатывает дамп !!!
# !!! Запускается только в incident-ситуации.                         !!!
#
# Использование:
#   ./restore-pg.sh                          — интерактивный выбор из последних 10 дампов
#   ./restore-pg.sh daily/voltari-pg-daily-2026-04-29.sql.gz
#   ./restore-pg.sh weekly/voltari-pg-weekly-2026-W17.sql.gz
#
# Что делает (по шагам, спрашивает подтверждение перед каждым опасным действием):
#   1. Останавливает gateway + web (api перестаёт принимать запросы)
#   2. Скачивает дамп из S3 в /opt/voltari/backups/restore/
#   3. Разжимает gzip
#   4. DROP DATABASE + CREATE DATABASE (просит подтверждения!)
#   5. pg_restore → накатывает дамп
#   6. Запускает gateway + web обратно
#   7. Curl на /health
#   8. Отчёт в Telegram

set -euo pipefail

COMPOSE_DIR="/opt/voltari"
ENV_FILE="${COMPOSE_DIR}/.env"
RESTORE_DIR="${COMPOSE_DIR}/backups/restore"

# ====== Цвета для CEO в стрессе ======
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

err() { echo -e "${RED}${BOLD}ERROR${NC} $*" >&2; }
ok()  { echo -e "${GREEN}OK${NC} $*"; }
warn(){ echo -e "${YELLOW}WARN${NC} $*"; }
ask() {
  local prompt="$1"
  echo -e "${YELLOW}${BOLD}? ${prompt}${NC} (введи 'yes' для продолжения):"
  read -r answer
  if [[ "${answer}" != "yes" ]]; then
    err "Отменено пользователем."
    exit 1
  fi
}

trap 'err "Скрипт прерван на строке $LINENO. БД может быть в неконсистентном состоянии."' ERR

# ====== Загрузка env ======
if [[ ! -f "${ENV_FILE}" ]]; then
  err "${ENV_FILE} не найден. Этот скрипт запускается на VPS."
  exit 1
fi
set -a; source "${ENV_FILE}"; set +a
: "${POSTGRES_USER:?}"; : "${POSTGRES_DB:?}"; : "${POSTGRES_PASSWORD:?}"
: "${BACKUP_BUCKET:?}"; : "${S3_ENDPOINT:?}"

mkdir -p "${RESTORE_DIR}"
cd "${COMPOSE_DIR}"

s3() { aws --endpoint-url "${S3_ENDPOINT}" "$@"; }

# ====== Преамбула ======
clear
echo -e "${BOLD}${CYAN}=== Voltari PostgreSQL — RESTORE FROM BACKUP ===${NC}"
echo ""
echo -e "${YELLOW}${BOLD}Это сценарий восстановления из бэкапа.${NC}"
echo -e "${YELLOW}Будут выполнены следующие шаги:${NC}"
echo "   1. Остановка gateway и web"
echo "   2. Скачивание дампа из S3"
echo "   3. ${RED}DROP DATABASE${NC} ${POSTGRES_DB}"
echo "   4. CREATE DATABASE ${POSTGRES_DB}"
echo "   5. pg_restore из дампа"
echo "   6. Запуск gateway и web"
echo "   7. Health check"
echo ""

# ====== Шаг 0. Выбор дампа ======
BACKUP_PATH="${1:-}"

if [[ -z "${BACKUP_PATH}" ]]; then
  echo -e "${CYAN}Последние 10 daily-дампов:${NC}"
  echo ""
  s3 s3 ls "s3://${BACKUP_BUCKET}/daily/" | sort -r | head -10 | nl -w2 -s'. '
  echo ""
  echo -e "${CYAN}Введи путь относительно бакета (например: daily/voltari-pg-daily-2026-04-29.sql.gz):${NC}"
  read -r BACKUP_PATH
fi

if [[ -z "${BACKUP_PATH}" ]]; then
  err "Путь не указан."
  exit 1
fi

S3_URL="s3://${BACKUP_BUCKET}/${BACKUP_PATH}"
LOCAL_FILE="${RESTORE_DIR}/$(basename "${BACKUP_PATH}")"
LOCAL_DUMP="${LOCAL_FILE%.gz}"

echo ""
echo -e "${CYAN}Будет восстановлен дамп:${NC} ${BOLD}${S3_URL}${NC}"
ask "Продолжить?"

# ====== Шаг 1. Стоп gateway/web ======
echo ""
echo -e "${CYAN}>>> Шаг 1/7: останавливаем gateway и web${NC}"
docker compose stop gateway web
ok "gateway и web остановлены"

# ====== Шаг 2. Скачивание ======
echo ""
echo -e "${CYAN}>>> Шаг 2/7: скачиваем дамп${NC}"
s3 s3 cp "${S3_URL}" "${LOCAL_FILE}"
SIZE=$(stat -c%s "${LOCAL_FILE}")
ok "Скачано $(numfmt --to=iec ${SIZE}) → ${LOCAL_FILE}"

# ====== Шаг 3. Разжатие ======
echo ""
echo -e "${CYAN}>>> Шаг 3/7: разжимаем${NC}"
gunzip -kf "${LOCAL_FILE}"
ok "Разжато → ${LOCAL_DUMP}"

# ====== Шаг 4. DROP + CREATE ======
echo ""
echo -e "${RED}${BOLD}>>> Шаг 4/7: DROP DATABASE ${POSTGRES_DB}${NC}"
echo -e "${RED}    Это уничтожит ВСЕ текущие данные в ${POSTGRES_DB}.${NC}"
ask "Точно продолжить?"

# Используем postgres БД для подключения, чтобы можно было дропнуть таргетную
docker compose exec -T postgres psql -U "${POSTGRES_USER}" -d postgres -v ON_ERROR_STOP=1 <<EOF
SELECT pg_terminate_backend(pid)
  FROM pg_stat_activity
 WHERE datname = '${POSTGRES_DB}' AND pid <> pg_backend_pid();
DROP DATABASE IF EXISTS ${POSTGRES_DB};
CREATE DATABASE ${POSTGRES_DB} OWNER ${POSTGRES_USER};
EOF
ok "БД ${POSTGRES_DB} пересоздана"

# ====== Шаг 5. pg_restore ======
echo ""
echo -e "${CYAN}>>> Шаг 5/7: pg_restore (это может занять несколько минут)${NC}"
docker compose exec -T postgres pg_restore \
  -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" \
  --no-owner --no-privileges --jobs 2 \
  < "${LOCAL_DUMP}" || warn "pg_restore завершился с warning'ами (это нормально для no-owner)"

# Sanity-check: смотрим количество таблиц
TABLE_COUNT=$(docker compose exec -T postgres psql -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" -t -c \
  "SELECT count(*) FROM information_schema.tables WHERE table_schema='public';" | tr -d ' ')
ok "Восстановлено таблиц: ${TABLE_COUNT}"

if [[ "${TABLE_COUNT}" -lt 1 ]]; then
  err "В БД нет таблиц после restore! Что-то пошло не так."
  exit 1
fi

# ====== Шаг 6. Старт сервисов ======
echo ""
echo -e "${CYAN}>>> Шаг 6/7: запускаем gateway и web${NC}"
docker compose start gateway web
sleep 10
ok "Сервисы запущены"

# ====== Шаг 7. Health check ======
echo ""
echo -e "${CYAN}>>> Шаг 7/7: health check${NC}"
HEALTH_OK=0
for i in 1 2 3 4 5; do
  if curl -fsS --max-time 10 https://api.brikko.ru/health >/dev/null; then
    HEALTH_OK=1
    break
  fi
  warn "Попытка ${i}/5 не удалась, ждём 5 сек..."
  sleep 5
done

if [[ "${HEALTH_OK}" == "1" ]]; then
  ok "https://api.brikko.ru/health отвечает 200"
else
  err "Health check не прошёл. Проверь docker compose logs gateway"
  exit 1
fi

# ====== Cleanup ======
rm -f "${LOCAL_FILE}" "${LOCAL_DUMP}"

# ====== Telegram notify ======
if [[ -n "${TG_BOT_TOKEN:-}" && -n "${TG_CHAT_ID:-}" ]]; then
  curl -fsS -m 10 -X POST \
    "https://api.telegram.org/bot${TG_BOT_TOKEN}/sendMessage" \
    -d "chat_id=${TG_CHAT_ID}" \
    -d "parse_mode=Markdown" \
    --data-urlencode "text=*Voltari* PostgreSQL восстановлен из \`${BACKUP_PATH}\` на хосте \`$(hostname)\`. Таблиц: ${TABLE_COUNT}." \
    >/dev/null || true
fi

echo ""
echo -e "${GREEN}${BOLD}=== ВОССТАНОВЛЕНИЕ ЗАВЕРШЕНО УСПЕШНО ===${NC}"
echo ""
echo "Что сделать дальше:"
echo "  1. Открыть https://brikko.ru — проверить логин-флоу"
echo "  2. Открыть https://api.brikko.ru/v1/models — проверить API"
echo "  3. Записать инцидент в 06_Operations/incidents/$(date +%Y-%m-%d)-restore.md"
echo "  4. Если потеряны транзакции после момента дампа — восстановить руками из логов"
