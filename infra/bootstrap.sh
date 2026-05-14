#!/usr/bin/env bash
# bootstrap.sh — приведение чистого Ubuntu 24.04 LTS к prod-готовности для Voltari.
#
# Запускать ОТ root, ОДИН раз, на свежем VPS:
#   curl -fsSL https://raw.githubusercontent.com/bbchort/voltari/main/infra/bootstrap.sh -o bootstrap.sh
#   chmod +x bootstrap.sh
#   sudo SSH_PUBKEY="ssh-ed25519 AAAA... ceo@laptop" SSH_PORT=22 ./bootstrap.sh
#
# Переменные окружения (можно задать перед запуском):
#   SSH_PUBKEY  — публичный ключ деплой-юзера (ОБЯЗАТЕЛЬНО)
#   SSH_PORT    — порт SSH (по умолчанию 22; для paranoid — 49152..65535)
#   DEPLOY_USER — имя deploy-пользователя (по умолчанию "deploy")
#   TIMEZONE    — таймзона (по умолчанию "Europe/Moscow")
#
# Что делает:
#   1. apt update + upgrade + базовые пакеты
#   2. Настройка таймзоны и unattended-upgrades
#   3. Установка Docker CE + compose-plugin
#   4. Создание deploy-пользователя с sudo и SSH-ключом
#   5. Hardening SSH (no root, no password, ключи only, опц. кастомный порт)
#   6. ufw firewall (deny incoming, allow 22/80/443)
#   7. fail2ban (sshd jail)
#   8. logrotate для docker logs
#   9. Создание /opt/voltari/ и шаблонов

set -euo pipefail

# ====== Параметры ======
SSH_PUBKEY="${SSH_PUBKEY:-}"
SSH_PORT="${SSH_PORT:-22}"
DEPLOY_USER="${DEPLOY_USER:-deploy}"
TIMEZONE="${TIMEZONE:-Europe/Moscow}"

if [[ $EUID -ne 0 ]]; then
  echo "ERROR: запусти от root (sudo)" >&2
  exit 1
fi

if [[ -z "${SSH_PUBKEY}" ]]; then
  echo "ERROR: SSH_PUBKEY не задан. Пример:" >&2
  echo '  sudo SSH_PUBKEY="ssh-ed25519 AAAA... me@laptop" ./bootstrap.sh' >&2
  exit 1
fi

log() { echo -e "\n\033[1;36m[bootstrap]\033[0m $*"; }

# ====== 1. apt update + base packages ======
log "Шаг 1/9: apt update + базовые пакеты"
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get upgrade -y
apt-get install -y \
  ca-certificates curl wget gnupg lsb-release \
  ufw fail2ban unattended-upgrades \
  htop iotop tmux git jq logrotate cron \
  python3 python3-pip \
  rsync restic awscli

# ====== 2. Таймзона + unattended security upgrades ======
log "Шаг 2/9: таймзона ${TIMEZONE} + unattended-upgrades"
timedatectl set-timezone "${TIMEZONE}"
echo "${TIMEZONE}" > /etc/timezone

cat >/etc/apt/apt.conf.d/20auto-upgrades <<'EOF'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
APT::Periodic::AutocleanInterval "7";
EOF

# Только security-обновления (kernel-патчи требуют reboot — настроим reboot вручную в окно)
sed -i 's|//\(\s*"\${distro_id}:\${distro_codename}-security";\)|\1|' /etc/apt/apt.conf.d/50unattended-upgrades || true

# ====== 3. Docker CE + compose-plugin ======
log "Шаг 3/9: Docker CE + compose-plugin"
if ! command -v docker >/dev/null 2>&1; then
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
  chmod a+r /etc/apt/keyrings/docker.gpg

  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
    https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" \
    > /etc/apt/sources.list.d/docker.list

  apt-get update -y
  apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
fi

systemctl enable --now docker

# Docker daemon: ограничить лог-драйвер по умолчанию
cat >/etc/docker/daemon.json <<'EOF'
{
  "log-driver": "json-file",
  "log-opts": {
    "max-size": "20m",
    "max-file": "5"
  },
  "live-restore": true
}
EOF
systemctl restart docker

# ====== 4. Deploy user ======
log "Шаг 4/9: создание пользователя ${DEPLOY_USER}"
if ! id -u "${DEPLOY_USER}" >/dev/null 2>&1; then
  adduser --disabled-password --gecos "" "${DEPLOY_USER}"
fi
usermod -aG sudo,docker "${DEPLOY_USER}"

# sudo без пароля для docker compose (опционально, удобно для CI)
cat >/etc/sudoers.d/90-${DEPLOY_USER} <<EOF
${DEPLOY_USER} ALL=(ALL) NOPASSWD: /usr/bin/docker, /usr/bin/systemctl, /usr/bin/journalctl
EOF
chmod 440 /etc/sudoers.d/90-${DEPLOY_USER}

# SSH-ключ
mkdir -p /home/${DEPLOY_USER}/.ssh
echo "${SSH_PUBKEY}" > /home/${DEPLOY_USER}/.ssh/authorized_keys
chmod 700 /home/${DEPLOY_USER}/.ssh
chmod 600 /home/${DEPLOY_USER}/.ssh/authorized_keys
chown -R ${DEPLOY_USER}:${DEPLOY_USER} /home/${DEPLOY_USER}/.ssh

# ====== 5. SSH hardening ======
log "Шаг 5/9: SSH hardening (port=${SSH_PORT}, no root, keys only)"

cat >/etc/ssh/sshd_config.d/99-voltari.conf <<EOF
Port ${SSH_PORT}
PermitRootLogin no
PasswordAuthentication no
PubkeyAuthentication yes
ChallengeResponseAuthentication no
UsePAM yes
X11Forwarding no
PrintMotd no
ClientAliveInterval 300
ClientAliveCountMax 2
MaxAuthTries 3
LoginGraceTime 30
AllowUsers ${DEPLOY_USER}
EOF

# Проверка что не сломали конфиг
sshd -t

systemctl restart ssh

# ====== 6. ufw firewall ======
log "Шаг 6/9: ufw (deny incoming, allow ${SSH_PORT}/80/443)"
ufw --force reset
ufw default deny incoming
ufw default allow outgoing
ufw allow ${SSH_PORT}/tcp comment 'SSH'
ufw allow 80/tcp comment 'HTTP'
ufw allow 443/tcp comment 'HTTPS'
ufw allow 443/udp comment 'HTTP/3 QUIC'
ufw --force enable
ufw status verbose

# ====== 7. fail2ban ======
log "Шаг 7/9: fail2ban + sshd jail"
cat >/etc/fail2ban/jail.local <<EOF
[DEFAULT]
bantime  = 1h
findtime = 10m
maxretry = 5
backend  = systemd

[sshd]
enabled  = true
port     = ${SSH_PORT}
logpath  = %(sshd_log)s
maxretry = 3
bantime  = 24h
EOF

systemctl enable --now fail2ban
systemctl restart fail2ban

# ====== 8. logrotate для docker и приложения ======
log "Шаг 8/9: logrotate"
cat >/etc/logrotate.d/voltari <<'EOF'
/var/log/voltari/*.log {
    daily
    rotate 14
    compress
    missingok
    notifempty
    create 0640 deploy deploy
    sharedscripts
}

/var/log/caddy/*.log {
    daily
    rotate 14
    compress
    missingok
    notifempty
    copytruncate
}

/var/log/voltari-backup.log {
    weekly
    rotate 12
    compress
    missingok
    notifempty
    create 0644 deploy deploy
}
EOF

mkdir -p /var/log/voltari /var/log/caddy
chown deploy:deploy /var/log/voltari
chown -R deploy:deploy /var/log/caddy

# ====== 9. /opt/voltari structure ======
log "Шаг 9/9: создаём /opt/voltari/"
mkdir -p /opt/voltari/{scripts,backups}
chown -R ${DEPLOY_USER}:${DEPLOY_USER} /opt/voltari
chmod 750 /opt/voltari

# README на машине
cat >/opt/voltari/README.md <<'EOF'
# /opt/voltari/

Production deployment корень. Файлы:
  docker-compose.yml  — сервисы
  Caddyfile           — reverse proxy
  .env                — секреты (chmod 600, владелец deploy, в git НЕТ)
  scripts/            — backup-pg.sh, restore-pg.sh
  backups/            — локальные временные дампы (перед загрузкой в S3)

Команды:
  cd /opt/voltari
  docker compose pull && docker compose up -d
  docker compose logs -f gateway
  docker compose ps
EOF
chown ${DEPLOY_USER}:${DEPLOY_USER} /opt/voltari/README.md

# ====== Финал ======
log "=== Готово ==="
echo ""
echo "VPS подготовлен. Что дальше:"
echo ""
echo "  1. На своей машине проверь SSH:"
echo "       ssh -p ${SSH_PORT} ${DEPLOY_USER}@<IP>"
echo ""
echo "  2. Залей docker-compose.yml, Caddyfile, scripts/ и .env в /opt/voltari/"
echo "       (через scp или git clone в /opt/voltari)"
echo ""
echo "  3. Запусти:"
echo "       cd /opt/voltari"
echo "       docker compose pull"
echo "       docker compose up -d"
echo ""
echo "  4. Настрой DNS A-записи на этот IP:"
echo "       brikko.ru, www.brikko.ru, api.brikko.ru, cabinet.brikko.ru"
echo ""
echo "  5. Настрой aws cli для бэкапов в Selectel S3:"
echo "       sudo -u ${DEPLOY_USER} aws configure"
echo ""
echo "WARNING: перед закрытием текущей SSH-сессии — открой ВТОРУЮ и проверь что новый коннект работает."
echo "          Если порт ${SSH_PORT} не подключается — у тебя ещё есть текущая сессия чтобы откатить."
