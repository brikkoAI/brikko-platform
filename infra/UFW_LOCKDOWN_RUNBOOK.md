# UFW Lockdown to Cloudflare-only — Runbook

**Что:** закрыть порты 80/443 на Reg.ru-сервере (`80.78.253.225`) **только для IP-диапазонов Cloudflare**, чтобы никто не мог обойти CF и стучаться напрямую.

**Зачем:**
- Защита от прямых атак на origin (минуя WAF/rate-limit/Bot Fight Mode)
- Защита от ботов / сканеров — они идут на IP, не на домены
- Это **рекомендация Cloudflare** в их официальной документации

**Когда применять:** **только после** установки CF Origin Cert + переключения SSL/TLS = Full (strict).

---

## TL;DR — что делает скрипт `infra/scripts/ufw-lockdown-cloudflare.sh`

1. Делает backup текущих правил UFW в `/var/backups/ufw/ufw-rules-<ts>.txt`.
2. Скачивает свежие IP-листы CF с `https://www.cloudflare.com/ips-v4` и `/ips-v6`.
3. Sanity-check: если IPv4 < 10 диапазонов или IPv6 < 4 — прерывается (защита от подмены / пустого ответа).
4. Удаляет общее правило `allow 80/tcp` и `allow 443/tcp`.
5. Добавляет `ufw allow from <CF-range> to any port 80/443 proto tcp` для каждого диапазона (28 правил суммарно: 14 IPv4 + 6 IPv6, × 2 порта).
6. Перезагружает UFW.

**Поддерживаемые режимы:**
- `sudo bash ufw-lockdown-cloudflare.sh` — apply (с интерактивным `yes/NO`).
- `sudo bash ufw-lockdown-cloudflare.sh --dry-run` — показывает план без изменений.
- `sudo bash ufw-lockdown-cloudflare.sh --revert` — откат: открывает 80/443 для всех.

---

## Какие порты открыты ПОСЛЕ lockdown'а (Reg.ru `80.78.253.225`)

| Порт | Протокол | Кому открыт | Зачем |
|---|---|---|---|
| 22 | TCP | **всем** (см. примечание ниже) | SSH-доступ CEO. Защищён ssh-key only + fail2ban. |
| 80 | TCP | **только Cloudflare** (14 IPv4 + 6 IPv6 диапазонов) | HTTP → редирект на HTTPS внутри Caddy. |
| 443 | TCP | **только Cloudflare** | HTTPS, terminate в Caddy. |
| 51820 | UDP | **не открывается** (на этом сервере не нужен) | WireGuard на Reg.ru — **клиент**, исходящий коннект к Aeza (`185.125.101.83:51820`). Слушает на random ephemeral-порту (`wg show` → `listening port: 53231`). Открывать 51820 нужно **на Aeza**, а не здесь. |

> **Примечание про SSH:** скрипт **не ограничивает порт 22 по IP**. Это сознательный компромисс:
> - Pro: CEO может подключиться с любой сети (мобильный интернет, кафе, поездка) — нет риска запереть себя.
> - Con: SSH открыт для bruteforce-сканеров. Митигация: **ssh-key only** (PasswordAuthentication=no) + `fail2ban` (ban после 5 неудач на 1 час).
> - Ужесточение по запросу CEO см. § «Опционально: жёсткий SSH-allowlist» ниже.

**После lockdown итоговый набор правил `ufw status`:**
```
22/tcp                     ALLOW IN    Anywhere                   # SSH
22/tcp (v6)                ALLOW IN    Anywhere (v6)              # SSH IPv6
80/tcp                     ALLOW IN    173.245.48.0/20            # CF
80/tcp                     ALLOW IN    103.21.244.0/22            # CF
... (× 14 IPv4 + 6 IPv6, всего 28 строк × 2 порта = ~40 строк CF-allow) ...
443/tcp                    ALLOW IN    2400:cb00::/32             # CF IPv6
```

Default policy: **deny incoming**, allow outgoing.

---

## Что нельзя забыть проверить ДО запуска

1. **Все 4 зоны в Cloudflare в статусе Active** (NS пропагировались). Проверить:
   ```
   dig +short NS brikko.ru
   dig +short NS brikko.online
   dig +short NS brikko.tech
   dig +short NS xn--80ajjbl9c.xn--p1ai
   ```
   — должны вернуть `*.ns.cloudflare.com`.

2. **CF Origin Certs выпущены** для каждой зоны и установлены:
   ```
   ssh ... 'ls -la /opt/brikko/data/caddy/origin/'
   # ожидаем: brikko.ru.crt, brikko.ru.key, brikko.online.crt, ...
   ```

3. **Caddyfile** использует `tls /path/cert /path/key` (НЕ `tls internal`). Проверить:
   ```
   ssh ... 'grep -A1 "tls " /opt/brikko/Caddyfile | head -20'
   ```

4. **CF SSL/TLS = Full (strict)** для всех 4 зон. CF dashboard → SSL/TLS → Overview.

5. **Site реально проксируется через CF** — оранжевое облачко на DNS-записях `api`, `www`, `@`, `docs`, `app` (не серое). Проверить:
   ```
   curl -sI https://api.brikko.ru/healthz | grep -iE "server|cf-ray"
   # ожидаем: server: cloudflare + cf-ray: <hash>-<airport>
   ```

6. **CF IP-ranges актуальны.** В скрипт встроен sanity-check (`>=10` IPv4, `>=4` IPv6), но желательно сравнить с эталоном:
   ```
   curl -fsSL https://www.cloudflare.com/ips-v4
   ```
   Сейчас (2026-05-01): 14 IPv4 + 6 IPv6 диапазонов.

7. **Резервный доступ через Reg.ru панель работает.** Один раз залогиниться в `https://www.reg.ru/user/account/` → VPS → Консоль (KVM) → проверить что пускает по root-паролю (если потеряли — сбросить через панель **до** lockdown'а, не после).

8. **Скрипт скачан на сервер и исполняемый:**
   ```
   ssh ... 'ls -la /usr/local/bin/ufw-lockdown-cloudflare.sh'
   ```

---

## Пошаговая инструкция для CEO «как запустить»

### Шаг 0 — закачать скрипт (один раз)

С локальной машины из корня репо:
```bash
scp -i ~/.ssh/brikko_regru \
  infra/scripts/ufw-lockdown-cloudflare.sh \
  root@80.78.253.225:/usr/local/bin/

ssh -i ~/.ssh/brikko_regru root@80.78.253.225 \
  'chmod +x /usr/local/bin/ufw-lockdown-cloudflare.sh'
```

### Шаг 1 — dry-run (увидеть план без изменений)

```bash
ssh -i ~/.ssh/brikko_regru root@80.78.253.225 \
  'sudo bash /usr/local/bin/ufw-lockdown-cloudflare.sh --dry-run'
```

Глазами проверить:
- В выводе строка `Получено 14 IPv4 + 6 IPv6 диапазонов` (числа могут чуть меняться).
- Команды `ufw allow from <ip>...` для каждого CF-диапазона.
- **Нет** команд, трогающих порт 22.

### Шаг 2 — apply

**Открыть второе SSH-окно** в параллель (страховка — если первое порвётся, второе ещё держится). Затем:
```bash
ssh -i ~/.ssh/brikko_regru root@80.78.253.225 \
  'sudo bash /usr/local/bin/ufw-lockdown-cloudflare.sh'
```
Скрипт спросит подтверждение `[yes/NO]`. Ввести `yes`.

После применения — скрипт покажет первые 50 строк `ufw status numbered`.

### Шаг 3 — verify (3 проверки)

**A) Через CF — должно работать (200 OK):**
```bash
curl -sI https://api.brikko.ru/healthz | head -3
# HTTP/2 200, server: cloudflare
```

**B) Прямо на IP — должно НЕ работать (timeout):**
```bash
curl -sI --resolve api.brikko.ru:443:80.78.253.225 https://api.brikko.ru/healthz \
  --connect-timeout 10
# curl: (28) Connection timed out
```

**C) SSH должен работать:**
```bash
ssh -i ~/.ssh/brikko_regru root@80.78.253.225 'echo OK'
# OK
```

Если все 3 проверки прошли — lockdown работает корректно.

### Шаг 4 — записать в журнал применений (внизу этого файла).

---

## Откат если что-то сломалось

### Если SSH работает

```bash
ssh -i ~/.ssh/brikko_regru root@80.78.253.225 \
  'sudo bash /usr/local/bin/ufw-lockdown-cloudflare.sh --revert'
```

Это откроет 80/443 для всех (как было до lockdown'а).

### Если SSH **не работает** (заблокировали сами себя)

1. **Reg.ru панель** → ваш VPS → **Консоль** (KVM)
2. Войти под `root` (пароль был при создании, или сбросить через панель)
3. Выполнить:
   ```bash
   ufw allow 22/tcp
   ufw allow 80/tcp
   ufw allow 443/tcp
   ufw reload
   ufw status
   ```
4. SSH должен заработать снова

### Восстановить из backup

Скрипт сохраняет старые правила в `/var/backups/ufw/ufw-rules-<timestamp>.txt`.

```bash
ls -lt /var/backups/ufw/
cat /var/backups/ufw/ufw-rules-<latest>.txt
```

Если хотите вернуть **точное** прежнее состояние — придётся применять правила вручную из этого файла.

---

## Cron-задание для еженедельного обновления списка CF IPs

Cloudflare **очень редко** меняет свои IP-диапазоны (1-2 раза в год), но когда меняет — нужно обновить наши правила, иначе часть CF-трафика будет блокироваться.

### Установка cron-задания

```bash
ssh -i ~/.ssh/brikko_regru root@80.78.253.225 'cat > /etc/cron.weekly/ufw-cloudflare-refresh << "EOF"
#!/bin/bash
# Раз в неделю обновляем список CF IP-диапазонов в UFW
LOG=/var/log/ufw-cloudflare-refresh.log
{
  echo "=== $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="
  bash /usr/local/bin/ufw-lockdown-cloudflare.sh --dry-run > /tmp/cf-refresh-plan.txt
  if grep -q "ufw allow" /tmp/cf-refresh-plan.txt; then
    echo "Plan содержит изменения. Запускаю apply (с auto-yes)..."
    yes yes | bash /usr/local/bin/ufw-lockdown-cloudflare.sh
  else
    echo "Изменений нет."
  fi
} >> "$LOG" 2>&1
EOF
chmod +x /etc/cron.weekly/ufw-cloudflare-refresh'
```

**Проверить логи:**

```bash
ssh -i ~/.ssh/brikko_regru root@80.78.253.225 \
  'tail -20 /var/log/ufw-cloudflare-refresh.log'
```

⚠️ **Ограничение:** автоматический `yes yes |` опасен — если CF когда-то начнёт отдавать неожиданный список (например пустой) — скрипт может сломать прод. Поэтому в скрипт встроена проверка `(( V4_COUNT < 10 || V6_COUNT < 4 ))` — он откажется применять если получил подозрительно мало диапазонов.

---

## Опционально: жёсткий SSH-allowlist (по запросу CEO)

Если CEO хочет ужесточить SSH до конкретного списка белых IP — выполнить **отдельно от lockdown-скрипта**, вручную:

```bash
# 1. Узнать свой текущий публичный IP (с ноута CEO):
curl -s https://api.ipify.org

# 2. Добавить allow только для него + удалить общий allow:
ssh -i ~/.ssh/brikko_regru root@80.78.253.225 \
  "ufw allow from <CEO_IP> to any port 22 proto tcp comment 'CEO SSH' \
   && ufw delete allow OpenSSH \
   && ufw delete allow 'OpenSSH (v6)' \
   && ufw status numbered"
```

**Риски:**
- Если у CEO динамический IP (мобильный модем / провайдер с DHCP) — попадёт в lockout при смене.
- Митигация: добавить ещё 1-2 backup-IP (рабочее место, домашний) или использовать Tailscale/VPN с фиксированным exit-node.

**Откат:**
```bash
ufw allow OpenSSH && ufw allow 'OpenSSH (v6)' && ufw reload
```

**Рекомендация:** на стадии MVP (соло-CEO, путешествия) — **не делать**. ssh-key-only + fail2ban достаточно.

---

## Дополнительная защита — Authenticated Origin Pulls (опционально)

CF может **подписывать каждый запрос** к origin, и мы можем проверять подпись на Caddy. Это превращает обход CF в **математически невозможный**.

**Когда:** если когда-то начнут DDoS'ить именно через имитацию CF-IP (маловероятно, но возможно).

**Как:** в CF → SSL/TLS → Origin Server → **Authenticated Origin Pulls** → Enable. Затем в Caddyfile добавить `tls { client_auth { mode require_and_verify trust_pool ... } }`.

**Не делаем сейчас** — overkill для MVP, делаем когда будет первый incident или при контракте Business+.

---

## Что НЕ делает этот скрипт

- Не закрывает SSH (порт 22 остаётся открыт — см. примечание выше; ужесточение — § «Опционально: жёсткий SSH-allowlist»).
- Не настраивает CF Origin Cert (это отдельный шаг до lockdown'а).
- Не переключает CF SSL/TLS режим (надо вручную в CF dashboard).
- Не настраивает rate-limiting на Caddy (это в Caddyfile делается отдельно).
- Не защищает от Layer-7 атак внутри CF-трафика (для этого WAF в CF).
- Не трогает 51820/UDP — на Reg.ru WG-клиент (исходящий), порт здесь не нужен. Открывать 51820/UDP надо **на Aeza** `185.125.101.83`.

---

## История применений

| Дата | Применил | Причина | Откат? |
|---|---|---|---|
| - | - | - | - |

(заполняется после каждого применения)
