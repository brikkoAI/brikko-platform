#!/usr/bin/env bash
# infra/scripts/ufw-lockdown-cloudflare.sh
#
# Закрывает порты 80/443 на Reg.ru-сервере (80.78.253.225) только для
# Cloudflare IP-диапазонов. Защита от того, чтобы кто-то ходил на наш
# origin-сервер напрямую, минуя CF (минуя WAF, rate-limit, DDoS-защиту).
#
# Применять ТОЛЬКО ПОСЛЕ:
#   1. CF Origin Certs выпущены и установлены на сервере
#   2. CF SSL/TLS = Full (strict)
#   3. Site проверен через CF (curl -sI https://brikko.ru → 200)
#
# Usage:
#   sudo bash ufw-lockdown-cloudflare.sh           # apply (с подтверждением)
#   sudo bash ufw-lockdown-cloudflare.sh --dry-run # show changes only
#   sudo bash ufw-lockdown-cloudflare.sh --revert  # откатиться к "allow all"

set -euo pipefail

# ----------------------------------------------------------------------------
# Configuration
# ----------------------------------------------------------------------------
CF_IPV4_URL="https://www.cloudflare.com/ips-v4"
CF_IPV6_URL="https://www.cloudflare.com/ips-v6"
SSH_PORT=22  # НИКОГДА не закрываем этот порт — наш доступ
HTTP_PORTS=(80 443)  # ports которые защищаем CF-only режимом
LOG_PREFIX="[ufw-lockdown-cf]"
BACKUP_DIR="/var/backups/ufw"

DRY_RUN=false
REVERT=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) DRY_RUN=true; shift ;;
    --revert)  REVERT=true; shift ;;
    -h|--help)
      sed -n '/^#$/,/^# Usage:/p' "$0" | sed 's/^# \?//'
      exit 0
      ;;
    *) echo "Unknown arg: $1" >&2; exit 1 ;;
  esac
done

log() { echo "$LOG_PREFIX $*"; }

# ----------------------------------------------------------------------------
# Pre-flight checks
# ----------------------------------------------------------------------------
if [[ "${EUID:-$UID}" -ne 0 ]]; then
  echo "Запускайте через sudo." >&2
  exit 1
fi

if ! command -v ufw >/dev/null 2>&1; then
  echo "ufw не установлен. apt-get install ufw" >&2
  exit 1
fi

if ! command -v curl >/dev/null 2>&1; then
  echo "curl не установлен. apt-get install curl" >&2
  exit 1
fi

mkdir -p "$BACKUP_DIR"

# ----------------------------------------------------------------------------
# Backup current rules
# ----------------------------------------------------------------------------
TS=$(date -u +%Y%m%dT%H%M%SZ)
BACKUP_FILE="$BACKUP_DIR/ufw-rules-${TS}.txt"
ufw status numbered > "$BACKUP_FILE"
log "Backup сохранён: $BACKUP_FILE"

# ----------------------------------------------------------------------------
# REVERT mode
# ----------------------------------------------------------------------------
if [[ "$REVERT" == true ]]; then
  log "REVERT mode: открываю 80/443 для всех (allow from any)"
  for port in "${HTTP_PORTS[@]}"; do
    # Удалить все CF-specific allow для этого порта
    ufw status numbered | grep "${port}/tcp" | grep -v "Anywhere" | awk -F'[][]' '{print $2}' \
      | sort -nr | xargs -r -I{} sh -c 'echo "y" | ufw delete {}'
    # Открыть для всех
    ufw allow "${port}/tcp"
  done
  log "REVERT complete. Сейчас 80/443 открыты для всех."
  ufw status
  exit 0
fi

# ----------------------------------------------------------------------------
# Fetch Cloudflare IP ranges
# ----------------------------------------------------------------------------
log "Скачиваю IP-диапазоны Cloudflare..."
CF_IPV4=$(curl -fsSL "$CF_IPV4_URL")
CF_IPV6=$(curl -fsSL "$CF_IPV6_URL")

V4_COUNT=$(echo "$CF_IPV4" | grep -c .)
V6_COUNT=$(echo "$CF_IPV6" | grep -c .)
log "Получено $V4_COUNT IPv4 + $V6_COUNT IPv6 диапазонов"

if (( V4_COUNT < 10 || V6_COUNT < 4 )); then
  echo "ОПАСНО: подозрительно мало диапазонов от CF. Прерываю." >&2
  echo "Проверьте $CF_IPV4_URL и $CF_IPV6_URL вручную." >&2
  exit 1
fi

# ----------------------------------------------------------------------------
# Build action plan
# ----------------------------------------------------------------------------
log "Plan:"
echo "  1. Удалить общие 'allow from anywhere' для портов: ${HTTP_PORTS[*]}"
echo "  2. Добавить 'allow from <CF-range>' для каждого CF IP × каждый порт"
echo "  3. SSH (порт $SSH_PORT) — не трогаем (доступ остаётся всем)"
echo

if [[ "$DRY_RUN" == true ]]; then
  log "DRY-RUN: ничего не меняю. Используйте без --dry-run чтобы применить."
  echo
  log "Команды которые БЫ выполнились:"
  for port in "${HTTP_PORTS[@]}"; do
    echo "  ufw delete allow ${port}/tcp"
    while IFS= read -r ip; do
      [[ -z "$ip" ]] && continue
      echo "  ufw allow from $ip to any port $port proto tcp"
    done <<< "$CF_IPV4"
    while IFS= read -r ip; do
      [[ -z "$ip" ]] && continue
      echo "  ufw allow from $ip to any port $port proto tcp"
    done <<< "$CF_IPV6"
  done
  exit 0
fi

# ----------------------------------------------------------------------------
# Confirm before applying
# ----------------------------------------------------------------------------
echo
log "ВНИМАНИЕ: применяю lockdown. После этого 80/443 будут принимать только CF-трафик."
log "SSH остаётся открытым (порт $SSH_PORT)."
log "Откат: sudo bash $0 --revert"
echo
read -r -p "Продолжить? [yes/NO]: " ANSWER
if [[ "$ANSWER" != "yes" ]]; then
  log "Отменено."
  exit 0
fi

# ----------------------------------------------------------------------------
# Apply
# ----------------------------------------------------------------------------
log "Удаляю общие правила allow для 80/443..."
for port in "${HTTP_PORTS[@]}"; do
  # Удалить все Anywhere-allow для этого порта (ipv4 + ipv6)
  ufw delete allow "${port}/tcp" 2>/dev/null || true
done

log "Добавляю allow from CF IPv4..."
while IFS= read -r ip; do
  [[ -z "$ip" ]] && continue
  for port in "${HTTP_PORTS[@]}"; do
    ufw allow from "$ip" to any port "$port" proto tcp comment "CF-allow $port" >/dev/null
  done
done <<< "$CF_IPV4"

log "Добавляю allow from CF IPv6..."
while IFS= read -r ip; do
  [[ -z "$ip" ]] && continue
  for port in "${HTTP_PORTS[@]}"; do
    ufw allow from "$ip" to any port "$port" proto tcp comment "CF-allow $port" >/dev/null
  done
done <<< "$CF_IPV6"

log "Перезагружаю ufw..."
ufw reload

log "Готово. Текущее состояние:"
ufw status numbered | head -50

echo
log "Тест извне (через CF — должно работать):"
log "  curl -sI https://brikko.ru        → 200 OK"
log "Тест напрямую на IP (должно НЕ работать):"
log "  curl -sI --resolve brikko.ru:443:80.78.253.225 https://brikko.ru  → timeout"
echo
log "Если что-то сломалось — откатитесь: sudo bash $0 --revert"
