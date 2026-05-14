# Brikko Infra — RUNBOOK

Production runbook для VPS-окружения Brikko (Selectel / Timeweb / любой
российский провайдер VPS, 4 vCPU / 8 GB RAM / 80 GB SSD).

> Этот файл — единственный источник правды по инфре. Если что-то в коде
> расходится с runbook'ом — расхождение чинится в коде, не в runbook'е.

---

## 1. Стек

| Слой | Технология | Контейнер |
|---|---|---|
| Reverse proxy / SSL | Caddy 2.8 | `brikko-caddy` |
| Web (SPA + SSR) | Next.js 15 standalone | `brikko-web` |
| API gateway | FastAPI + gunicorn-uvicorn | `brikko-gateway` |
| Database | PostgreSQL 16-alpine | `brikko-postgres` |
| Cache / sessions | Redis 7-alpine | `brikko-redis` |
| Backups | restic / pg_dump → Selectel S3 | cron на хосте |

---

## 2. Network topology (DO P1-16)

Три сети вместо одной плоской — уменьшает blast radius при RCE.

```
                            ┌──────────────┐
   internet ──443/tcp/udp── │  brikko-caddy │
                            └──────┬───────┘
                                   │  (frontend-net)
                          ┌────────┴─────────┐
                          │                  │
                          ▼                  ▼
                  ┌──────────────┐   ┌──────────────┐
                  │ brikko-web  │   │brikko-gateway│
                  └──────────────┘   └──────┬───────┘
                                            │  (backend-net)
                              ┌─────────────┴────────────┐
                              ▼                          ▼
                     ┌──────────────┐           ┌──────────────┐
                     │brikko-postgres│           │ brikko-redis │
                     └──────────────┘           └──────────────┘
```

**Свойства:**

- Caddy и web НЕ имеют сетевого пути до 5432/6379. Если в caddy появится
  RCE — атакующий не сможет открыть прямое подключение к БД.
- Gateway подключён к обеим сетям (это единственный сервис, кому нужен
  доступ и сюда, и туда).
- Caddy — только наружу + frontend-net. Postgres — только backend-net.

**Disaster recovery:** если gateway упал и из caddy / web никто не может
ходить в БД для аварийного скрипта — это _by design_. SSH в хост, run
`docker compose exec postgres psql ...`. См. § 7.

---

## 3. Resource limits & VPS sizing (DO P0-4)

На VPS 4 vCPU / 8 GB RAM мы выделяем **жёсткие лимиты** через
`mem_limit` / `cpus` (legacy syntax, работает с `docker compose up`,
не требует swarm-mode).

| Сервис | CPU limit | Mem limit | Mem reservation | Обоснование |
|---|---|---|---|---|
| postgres | 2.0 | 2g | 1g | Главный потребитель IO + cache |
| gateway | 2.0 | 2g | 1g | Основной CPU-потребитель (LLM-стриминг + JSON) |
| web | 1.0 | 1g | 512m | SSR + ISR; standalone Next.js потребляет ≈300-500 MB |
| redis | 0.5 | 768m | 256m | maxmemory=512mb + AOF buffer + overhead |
| caddy | 0.5 | 256m | 128m | Лёгкий, но H/2 + auto-SSL хочет немного памяти |
| **Σ** | **6.0 / 4** | **6 GB / 8 GB** | **2.875 GB** | OS+overhead остаётся ≈2 GB |

CPU суммарно превышает 4 vCPU — это ОК (limit, не reservation): сервисы
шарят простаивающие циклы.

**Когда менять лимиты:**

- Если `docker stats` показывает 80%+ usage → удвой limit для этого сервиса
  (если на хосте есть запас).
- При переходе на 8 vCPU / 16 GB → удваиваем gateway + postgres.
- При переходе на multi-VPS (≥1500 client / month) → выводим postgres на
  отдельный managed-инстанс Selectel Cloud Database (от 2 990 ₽/мес за
  4 GB / 2 vCPU, бэкапы и failover на стороне провайдера).

**Стоимость VPS:**

| Конфигурация | Цена/мес | Когда |
|---|---|---|
| 4 vCPU / 8 GB / 80 GB SSD (Selectel) | ≈1 800 ₽ | старт, до ~100 клиентов |
| 8 vCPU / 16 GB / 160 GB SSD | ≈3 600 ₽ | 100-500 клиентов |
| 8 vCPU + Selectel managed PG (4 GB) | ≈3 600 + 2 990 = 6 590 | >500 клиентов, нужен SLA на БД |

---

## 4. Migrations (DO P0-8)

**Алгоритм деплоя в `.github/workflows/deploy.yml`:**

```
1. docker compose pull           # тянем новые образы (gateway + web)
2. docker compose run --rm gateway alembic upgrade head   # миграции в одноразовом контейнере
3. docker compose up -d --remove-orphans gateway web      # переключаем сервисы
4. healthcheck /healthz          # liveness
5. readyz /readyz                # readiness (DB+Redis)
6. failure → rollback            # откат IMAGE_TAG, повторный pull/up
```

**Свойства:**

- Миграции — **до** старта нового кода: если миграция падает, старый
  gateway остаётся на старом теге, никакого rollback не нужно (новый код
  не запускался).
- Идемпотентность: повторный run `alembic upgrade head` = no-op (это
  закрыто в Sprint 1, BE P0-10).
- `--no-deps`: одноразовый контейнер не тянет healthcheck'и postgres/redis
  (они уже healthy от живых сервисов).

**Ручной запуск миграций (если CI не справился):**

```bash
ssh deploy@PROD_HOST
cd /opt/brikko
docker compose run --rm gateway alembic upgrade head
```

**Откат миграции (только если новая миграция reverse-safe):**

```bash
docker compose run --rm gateway alembic downgrade -1
```

NB: не все миграции 0001-0005 безопасны для downgrade. Если миграция
DROP TABLE / DROP COLUMN — restore из бэкапа (см. § 6).

---

## 5. Health endpoints (DO P0-9)

Унифицировано на k8s-style. Один паттерн в Dockerfile, compose, deploy
healthcheck'е, Caddy upstream-checks.

| Endpoint | Назначение | I/O | Status codes |
|---|---|---|---|
| `GET /healthz` | Liveness — процесс жив | none | 200 always |
| `GET /readyz` | Readiness — DB+Redis+providers | DB SELECT 1, Redis PING | 200 ready / 503 down |
| `GET /health/ready` | Алиас `/readyz` (legacy) | same | same |

**Использование:**

- Docker `HEALTHCHECK` — `/healthz`
- Compose healthcheck — `/healthz`
- Caddy upstream check (если включить) — `/healthz`
- Deploy pipeline post-deploy — `/healthz` (liveness) + `/readyz`
  (readiness) с retry × 5
- UptimeRobot (внешний) — `/healthz` (раз в 5 мин)

**Не использовать `/health`** (старое имя без `z` — оставлено как 404).

### 5.1. Caddy healthcheck отключён (2026-05-13)

У `brikko-caddy` намеренно НЕТ `HEALTHCHECK` в `docker-compose.prod.yml`.
Причина: BusyBox wget в `caddy:2.8-alpine` не умеет `--max-redirect`, а Caddy
на :80 безусловно делает 301→https (HSTS), после чего wget пытается
TLS-handshake к `localhost:443`, ловит self-signed-mismatch и контейнер
вечно `(unhealthy)`. Доступность снаружи мониторит UptimeRobot, изнутри
crash отлавливается `restart: unless-stopped`. Подробности — diff
`feat/infra-cleanup-2026-05-13`.

Если когда-нибудь захочется вернуть healthcheck — корректный вариант:
включить admin endpoint Caddy на `localhost:2019` и проверять `GET /config/`.

---

## 5.2. Disk hygiene — еженедельный Docker prune (2026-05-13)

Файл: `/etc/cron.weekly/brikko-docker-prune` (источник — `infra/cron.d/brikko-docker-prune`).

Запускается cron.weekly слотом (~Sun 06:25 anacron). Делает:

```bash
docker system prune -af --filter "until=168h" | logger -t brikko-prune
```

Удаляет unused images + build cache старше 168h. Контейнеры / named volumes /
networks НЕ трогает. История: 2026-05-13 build cache набрался до 85% диска
из-за CI/CD сборок → деплои стали падать.

**Просмотр логов:**
```bash
journalctl -t brikko-prune --since '1 week ago' --no-pager
```

**Manual run:** `sudo bash /etc/cron.weekly/brikko-docker-prune`

---

## 6. Backups & retention (DO P0-7)

### Что бэкапим

- **PostgreSQL** ежедневно через `pg_dump` (см. `infra/scripts/backup-pg.sh`)
- **caddy_data** (Let's Encrypt сертификаты) — раз в неделю
- **redis_data** (опционально, AOF восстанавливается из БД)

### Куда

Selectel Object Storage (S3-совместимое, РФ-юрисдикция, 152-ФЗ-friendly).

```
Endpoint:  https://s3.ru-1.storage.selcloud.ru
Bucket:    brikko-backups-pg
Структура:
  daily/YYYY-MM-DD.sql.gz     (последние 7)
  weekly/YYYY-WW.sql.gz       (последние 4)
  monthly/YYYY-MM.sql.gz      (последние 12)
```

### Lifecycle policy

Управляется через `infra/scripts/setup-s3-lifecycle.sh` (idempotent).

```
daily/   → expire 7 days
weekly/  → expire 28 days
monthly/ → expire 365 days
```

**Когда запускать:**

- Один раз после первого provisioning'а bucket'а
- После ротации S3-ключа (для смоук-теста доступа)
- Если меняем retention в скрипте

```bash
ssh deploy@PROD_HOST
cd /opt/brikko/infra/scripts
sudo -u deploy bash setup-s3-lifecycle.sh
# Verify:
aws --endpoint-url $S3_ENDPOINT s3api get-bucket-lifecycle-configuration --bucket brikko-backups-pg
```

### Стоимость

При 200 MB БД и retention 7d/28d/365d — общий объём ≈12 GB.
Selectel Object Storage: 1.5 ₽/GB/мес → **≈18 ₽/мес**.

### Тестирование восстановления

**Раз в квартал**: восстанавливаем dump в staging и проверяем `SELECT count(*) FROM accounts` (= prod). См. `infra/scripts/restore-pg.sh`.

---

## 7. Disaster recovery

Каждый сценарий — RPO/RTO + step-by-step команды. Следуй ровно по списку,
не импровизируй (для импровизации есть § 7.6 — fallback процедуры).

### 7.1. Полная потеря VPS (Selectel выключил, диск умер, лиц/банк-блок)

**RPO:** до 24 ч (последний `daily/` снимок в Selectel S3 + потеря всех
событий после полуночи UTC до момента сбоя)
**RTO:** ≈2 ч от обнаружения до DNS-switch'а

```bash
# === ШАГ 1: Уведомить пользователей (если знаем адреса) ===
# Telegram через @BrikkoOpsBot:
#   curl -X POST "https://api.telegram.org/bot$TG_BOT_TOKEN/sendMessage" \
#     -d "chat_id=$TG_CHAT_ID" \
#     -d "text=Brikko: maintenance window — restoring service. ETA 2 hours."

# === ШАГ 2: Поднять новый VPS той же конфигурации ===
# Selectel ЛК → My Cloud → Servers → Create
#   Region: ru-1 (Питер)
#   Plan: 4 vCPU / 8 GB / 80 GB SSD
#   OS: Ubuntu 22.04 LTS
#   SSH key: deploy public key
#   Запомнить новый IP — он понадобится для DNS-switch на шаге 7

# === ШАГ 3: Provision (bootstrap.sh ставит docker, ufw, fail2ban, deploy user) ===
ssh root@NEW_VPS_IP
curl -sSL https://raw.githubusercontent.com/bbchort/brikko/main/infra/bootstrap.sh | bash
# Проверить: ufw enabled, sshd на порту 22 только pubkey, docker группа существует.

# === ШАГ 4: Клонировать репо + расшифровать секреты ===
ssh deploy@NEW_VPS_IP
sudo mkdir -p /opt/brikko && sudo chown deploy:deploy /opt/brikko
git clone https://github.com/bbchort/brikko.git /opt/brikko
cd /opt/brikko

# Восстановить age private key из 1Password vault "Brikko Production"
# (мастер-пароль 1Password — в сейфе у CEO)
mkdir -p ~/.config/sops/age
# вставить содержимое ключа
nano ~/.config/sops/age/keys.txt
chmod 600 ~/.config/sops/age/keys.txt

# Расшифровать .env
infra/scripts/sops-decrypt-env.sh
# Verify: head -3 infra/.env должно показать IMAGE_TAG и ENVIRONMENT

# === ШАГ 5: Поднять postgres (БЕЗ остальных сервисов) ===
cd /opt/brikko
docker compose up -d postgres
sleep 30
docker compose ps postgres   # должен быть healthy

# === ШАГ 6: Восстановить БД из последнего бэкапа ===
# Список доступных снимков:
aws --endpoint-url $S3_ENDPOINT s3 ls s3://brikko-backups-pg/daily/ | tail -5

# Restore последний:
infra/scripts/restore-pg.sh daily/$(date -u -d 'yesterday' +%Y-%m-%d).sql.gz
# (если вчерашнего нет — берём предпоследний или weekly)

# Smoke-проверка БД:
docker compose exec postgres psql -U brikko -d brikko -c "SELECT count(*) FROM accounts;"
# Сверить с метрикой накануне (Grafana Cloud / TG-боту присылается ежедневно)

# === ШАГ 7: Поднять остальное ===
docker compose pull gateway web
# alembic не нужен — БД уже на head, восстановили.
docker compose up -d
# Подождать healthy:
docker compose ps

# Smoke с самого хоста (минуя DNS):
curl -fsS http://localhost/healthz
# Должно быть {"status":"ok"}

# === ШАГ 8: DNS switch (Cloudflare) ===
# https://dash.cloudflare.com/<account>/brikko.ru/dns/records
# Найти A-record:
#   brikko.ru → старый_IP   → заменить на NEW_VPS_IP
#   api.brikko.ru → старый_IP → NEW_VPS_IP
#   www.brikko.ru → старый_IP → NEW_VPS_IP
# TTL: 60s (автоматически у CF)
# Proxied: Yes (оранжевое облачко) — DDoS-щит

# Verify:
dig +short brikko.ru
# Должны увидеть Cloudflare IP'ы (104.21.x.x / 172.67.x.x), а origin —
# уже новый VPS. Проверка origin:
curl --resolve brikko.ru:443:NEW_VPS_IP https://brikko.ru/healthz

# === ШАГ 9: Дождаться LE-renew (если SSL не работает) ===
# Caddy запросит свежий сертификат за 60 секунд при первом HTTPS-вызове.
# Если 5+ мин не работает — посмотреть caddy logs:
docker compose logs caddy --tail 100

# === ШАГ 10: Уведомить о восстановлении ===
# TG: "Brikko: service restored. RTO {время} от {начало}."

# === ШАГ 11: Post-mortem (за 24 ч) ===
# - Что сработало в DR-плане, что нет
# - Что добавить в этот runbook
# - Связаться с Selectel поддержкой если был их сбой — компенсация
```

**Если Selectel недоступен полностью (региональный сбой):**
- Backup провайдер: Timeweb Cloud (тот же CIS-стек, такой же compose)
- Бэкапы в Selectel S3 — пока работают (S3 не зависит от VPS-региона)
- DNS быстро переключаем — приоритет на восстановление за <4 ч

### 7.2. Compromise API ключа (OpenAI / Anthropic / любой провайдер)

**Признаки:** аномальный spike usage в Sentry/billing-метрике, неизвестные
запросы в `usage_events`, OpenAI прислал email "unusual activity".

**RPO:** 0 (мы не теряем данные)
**RTO:** 30 минут

```bash
# === ШАГ 1: НЕМЕДЛЕННО revoke ключа ===
# OpenAI:    https://platform.openai.com/api-keys → Delete
# Anthropic: https://console.anthropic.com/settings/keys → Revoke
# Google:    https://aistudio.google.com/app/apikey → Delete
# DeepSeek:  https://platform.deepseek.com/api_keys → Delete
# Yandex/Sber: ЛК → Service Accounts → Delete service account key

# === ШАГ 2: Создать новый ключ С ЛИМИТОМ ===
# OpenAI: usage limit $50/mo (или $200 если выручка позволяет)
# Anthropic: usage limit $50/mo
# Google: quota limit в Google Cloud Console
# DeepSeek: hard limit в biller-настройках

# === ШАГ 3: Положить новый ключ в SOPS, не в чат ===
# НИКОГДА не вставляем новый ключ в Claude Chat, в email, в Slack.
# Поток: 1Password → провайдер-ключ записан туда → SOPS-edit:
cd /opt/brikko
sops infra/.env.sops.yaml
# Откроется $EDITOR с расшифрованным YAML. Заменить значение OPENAI_API_KEY.
# Сохранить — SOPS перешифрует автоматически.
git add infra/.env.sops.yaml
git commit -m "rotate: OPENAI_API_KEY (compromise response)"
git push origin main

# === ШАГ 4: Применить на VPS ===
ssh deploy@PROD_HOST
cd /opt/brikko
git pull
infra/scripts/sops-decrypt-env.sh
docker compose restart gateway   # gateway читает .env при старте
docker compose ps gateway        # должен быть healthy

# === ШАГ 5: Smoke-test ===
curl -X POST https://api.brikko.ru/v1/chat/completions \
  -H "Authorization: Bearer $YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"gpt-5.4-mini","messages":[{"role":"user","content":"ping"}],"max_tokens":5}'

# === ШАГ 6: Проверка billing-аномалий за период компрометации ===
# Установим окно компрометации (от alert-time до revoke-time):
PERIOD_FROM="2026-04-29T15:00:00Z"  # подставить
PERIOD_TO="2026-04-29T15:30:00Z"

# По каждому провайдеру — суммарные траты в провайдер-консоли:
# OpenAI: Usage page → custom date range → сравнить с нашим usage_events
# Anthropic: Settings → Usage → Daily breakdown

# В нашей БД:
docker compose exec postgres psql -U brikko -d brikko -c "
SELECT account_id, provider, SUM(cost_kopecks) / 100.0 AS rub_total, COUNT(*) AS calls
FROM usage_events
WHERE created_at BETWEEN '$PERIOD_FROM' AND '$PERIOD_TO'
GROUP BY account_id, provider
ORDER BY rub_total DESC LIMIT 20;
"

# Если провайдерский usage сильно больше нашего — значит ключ уплыл и
# использовался без нашего gateway → claim refund у провайдера + блок IP.

# === ШАГ 7: TD-002 update ===
# В docs/tech_debt_registry.md → TD-002 → отметить ротацию + дата
# В commit message указать причину (compromise vs scheduled rotation)

# === ШАГ 8: Post-mortem (24 ч) ===
# - Как утечка случилась? (env-файл, логи, чат, screen, scope-too-wide ключ)
# - Что добавить в защиту? (см. TD-024 SOPS уже закрывает основную дыру)
# - Уведомить аффектед клиентов (если их деньги списались на чужой usage)
```

### 7.3. DDoS / L7-flood / brute-force на /v1/auth/login

**Признаки:** Caddy access.log → spike RPS на один эндпоинт от широкого
набора IP, gateway logs → 429 от rate-limit middleware (Sprint 2 уже
встроенный), pg connection pool заполнен, Sentry → spike error rate.

**RPO:** 0
**RTO:** 5-15 минут (Cloudflare включается за минуты)

```bash
# === ШАГ 1: Включить Cloudflare proxy (если ещё не) ===
# https://dash.cloudflare.com/<acc>/brikko.ru/dns/records
# Для каждого A-record нажать на "облачко" — должно стать оранжевое.
# С этого момента весь трафик идёт через CF, наш origin виден только
# CF IP'ам.

# === ШАГ 2: Настроить CF firewall rules (бесплатно на Free плане) ===
# Security → WAF → Custom rules → Create rule:
#
#   Rule 1 — block known bad bots:
#     (cf.client.bot) → Block
#
#   Rule 2 — challenge non-RU on auth endpoints:
#     (http.request.uri.path contains "/v1/auth/" and ip.geoip.country ne "RU")
#     → Managed Challenge
#
#   Rule 3 — rate limit на сам IP (CF Free даёт 1 правило):
#     If: request from same IP > 100 req per 1 min
#     Then: Block 1 hour
#
# Применить — изменения вступают за <60s.

# === ШАГ 3: Включить "Under Attack Mode" (если совсем плохо) ===
# CF dashboard → Security → Under Attack Mode → ON
# Каждый запрос проходит JS-challenge — чистые боты не пройдут.
# ВАЖНО: API-запросы (/v1/*) тоже будут challenge'нуты.
# Поэтому подключаем только если 99% трафика — атака.

# === ШАГ 4: Внутренний rate limit Sprint 2 уже работает ===
# Проверить логи gateway:
docker compose logs gateway --tail 200 | grep "rate_limit_exceeded"
# Метрика request_id'ов — кто хватает 429.

# === ШАГ 5: Если /v1/auth/login конкретно атакуется ===
# Внутри gateway включить более жёсткий лимит:
# В infra/.env.sops.yaml:
#   AUTH_LOGIN_RATE_LIMIT_PER_IP=5    # вместо стандартных 30/min
#   AUTH_LOGIN_RATE_LIMIT_PER_EMAIL=3  # на конкретный email
# sops + commit + decrypt + restart gateway

# === ШАГ 6: Если Postgres connection pool исчерпан ===
# Увеличить:
# В docker-compose.yml postgres command:
#   -c max_connections=200    # было 100
# В gateway settings:
#   DB_POOL_SIZE=20           # было 10
# Restart Postgres — БД перезапускается за 30s, gateway переподключается автоматически.

# === ШАГ 7: Post-attack analysis ===
# CF Analytics → Security → review attack pattern
# Запросить логи у CF (24 ч retention на Free, дольше на Pro).
# Дополнить ipset-блок-листом если паттерн повторяется.
```

**Стоимость защиты:**
- Cloudflare Free: 0 ₽ — этого достаточно для L3/L4 + базовая WAF
- Cloudflare Pro: $20/мес — добавляет custom WAF rules без лимита,
  улучшенный bot management. Подключаем когда атак становится >1/мес.

### 7.4. БД повреждена / corrupt (физическая порча, неудачная миграция)

**Признаки:** `pg_isready` 200 но запросы выдают `ERROR: invalid page in
block`, `pg_dump` падает посередине, Sentry → spike `OperationalError`.

**RPO:** до 24 ч (последний daily) — но мы можем сделать диф для частичного recovery
**RTO:** 30-60 мин (зависит от объёма)

```bash
# === ШАГ 1: ОСТАНОВИТЬ записи в БД ===
ssh deploy@PROD_HOST
cd /opt/brikko
docker compose stop gateway web
# Caddy продолжает отвечать, но 502 всем endpoint'ам — пользователи видят
# maintenance-page (если настроена в Caddy) или просто 502.

# === ШАГ 2: Снять "криминальный" дамп (для post-mortem) ===
# Даже если он битый — может пригодиться для diff'а.
docker compose exec postgres pg_dumpall -U brikko -c > /tmp/corrupt-dump-$(date -u +%Y%m%dT%H%M%S).sql 2>&1 || true
# Скопировать на безопасное место (НЕ на тот же диск VPS):
scp deploy@PROD_HOST:/tmp/corrupt-dump-*.sql ./local-forensics/

# === ШАГ 3: Список последних бэкапов ===
aws --endpoint-url $S3_ENDPOINT s3 ls s3://brikko-backups-pg/daily/ | tail -10
# Найти последний "хороший" — обычно вчерашний или позавчерашний.

# === ШАГ 4: Diff с last good backup ===
# Скачиваем last good:
aws --endpoint-url $S3_ENDPOINT s3 cp s3://brikko-backups-pg/daily/2026-04-28.sql.gz /tmp/lastgood.sql.gz
gunzip /tmp/lastgood.sql.gz

# Поднимаем temporary postgres для сравнения:
docker run --rm -d --name pg-tmp -e POSTGRES_PASSWORD=tmp postgres:16-alpine
sleep 10
docker exec -i pg-tmp psql -U postgres < /tmp/lastgood.sql

# Сравнить таблицы (что добавилось ПОСЛЕ бэкапа — это потеряем):
docker exec pg-tmp psql -U postgres -d brikko -c "SELECT count(*) FROM accounts" > /tmp/lastgood-counts
docker compose exec postgres psql -U brikko -d brikko -c "SELECT count(*) FROM accounts" > /tmp/current-counts
diff /tmp/lastgood-counts /tmp/current-counts
# (повторить для main-таблиц: users, transactions, usage_events, api_keys)

# Если diff небольшой и БД частично читаема — экспортнуть только недостающие
# rows и применить ПОСЛЕ restore'а:
docker compose exec postgres psql -U brikko -d brikko -c "
COPY (SELECT * FROM transactions WHERE created_at >= '2026-04-28T00:00:00Z')
TO STDOUT WITH CSV HEADER
" > /tmp/recent-transactions.csv

docker stop pg-tmp

# === ШАГ 5: Стандартный restore ===
infra/scripts/restore-pg.sh daily/2026-04-28.sql.gz
# Скрипт сам:
#   1. drops brikko schema
#   2. creates fresh
#   3. restores from gzip
#   4. runs alembic upgrade head (на случай если бэкап был старее текущего кода)

# === ШАГ 6: Применить дельту (если выгребли её на шаге 4) ===
docker compose exec -T postgres psql -U brikko -d brikko -c "
COPY transactions FROM STDIN WITH CSV HEADER
" < /tmp/recent-transactions.csv

# === ШАГ 7: Перезапустить gateway/web ===
docker compose up -d gateway web
sleep 20
curl -fsS https://api.brikko.ru/healthz
curl -fsS https://api.brikko.ru/readyz

# === ШАГ 8: Уведомить аффектед клиентов ===
# Если данные между last-backup и crash потеряны:
# - Идентифицировать кто платил топ-апы или делал chat в этом окне
# - Сделать manual credit (через admin API или прямой UPDATE) на сумму потерянного баланса
# - Email с извинениями + объяснением

# === ШАГ 9: Root cause (24 ч) ===
# - Что вызвало corruption? (диск, OOM, миграция, manual SQL)
# - Если миграция — pin alembic-head и обновить migration tests
# - Если диск — Selectel SLA-claim + плановый VPS-rebuild
# - Если OOM — увеличить mem_limit postgres в compose
```

### 7.5. Утечка email-базы / списка пользователей

**Признаки:** клиент сообщил что получает spam на email, который он
использовал ТОЛЬКО для Brikko. Или security-researcher написал что
нашёл список users.

**RPO:** не относится — данные не потеряны, утекли
**RTO:** 24 ч до полного response (152-ФЗ требует уведомить
Роскомнадзор за 72 часа)

```bash
# === ШАГ 1: Содержать (не дать утечь больше) ===
# Понять scope: где пользователи могли утечь?
# - DB dump на S3? Проверить access logs Selectel S3.
# - .env с DB-password в логах CI? Проверить gh run list.
# - Открытый /v1/admin/users? Проверить routes.

# === ШАГ 2: Закрыть дыру ===
# Если backup-bucket был public → сделать private (Selectel ЛК → bucket → ACL)
# Если CI logged secret → revoke + ротация SOPS
# Если admin route открыт → patch + deploy

# === ШАГ 3: Ротировать ВСЕ секреты ===
# Каждый ключ в .env.sops.yaml — заменить:
sops infra/.env.sops.yaml
# Заменить: SECRET_KEY, POSTGRES_PASSWORD, REDIS_PASSWORD, все *_API_KEY,
# YOOKASSA_*, SENTRY_DSN_*, SMTP_PASSWORD.
# Это эффективно "перезагружает" infra с нуля.

# === ШАГ 4: Force logout всех users ===
docker compose exec postgres psql -U brikko -d brikko -c "
UPDATE users SET token_version = token_version + 1;
"
# Все JWT с предыдущим token_version становятся невалидными → forced re-login.

# === ШАГ 5: Email всем users (152-ФЗ) ===
# Шаблон письма (RU + EN), без юридического trash-talk:
# "Мы обнаружили утечку списка email'ов наших пользователей. Ваши пароли
# хешированы (Argon2), скорее всего безопасны, но мы рекомендуем сменить
# пароль. Подробности: [link to incident-page]."

# === ШАГ 6: Уведомить Роскомнадзор (152-ФЗ ст. 21 ч.3.1) ===
# Срок: 24 ч на УВЕДОМЛЕНИЕ + 72 ч на ОТЧЁТ
# Форма: https://pd.rkn.gov.ru/operators-registry/ → личный кабинет
# (требует регистрации оператора ПДн — сделать ДО первого пользователя)

# === ШАГ 7: Post-incident page ===
# Опубликовать на brikko.ru/incidents/2026-XX (Markdown в репо).
# Что произошло, что починено, что улучшим. Stripe-стиль — честность.
```

### 7.6. OpenAI / Anthropic полностью down (failover на резервный провайдер)

**Признаки:** Sentry → spike `ProviderUpstreamError`, gateway logs →
все запросы к OpenAI 502/timeout, провайдерский status-page показывает incident.

**RPO:** 0
**RTO:** автоматический failover ~5 секунд (smart-router из MVP)

```bash
# === ШАГ 1: Проверить smart-router работает ===
# Логи gateway должны показывать переключение:
docker compose logs gateway --tail 100 | grep "router_failover"

# === ШАГ 2: Если автоматика не сработала — force-route ===
# Через SOPS: установить FORCE_ROUTE=anthropic в .env.sops.yaml
# Это обходит smart-router, заставляя все запросы идти на резерв.
sops infra/.env.sops.yaml
# Set:
#   FORCE_ROUTE: anthropic
git commit -am "ops: force-route to anthropic during openai outage"
git push
ssh deploy@PROD_HOST
cd /opt/brikko && git pull && infra/scripts/sops-decrypt-env.sh && docker compose restart gateway

# === ШАГ 3: Уведомить пользователей о partial degradation ===
# В Telegram-канал клиентов:
#   "Brikko: OpenAI experiencing outage, ваши запросы автоматически
#   маршрутизируются на Anthropic Claude. Производительность не
#   снижается, тарификация — по фактической модели."

# === ШАГ 4: Когда OpenAI восстановится — снять force-route ===
# Обнулить FORCE_ROUTE: '' и redeploy
```

### 7.7. Утрата SOPS age private key (CEO потерял ключ)

**Признаки:** не можем расшифровать `.env.sops.yaml`, deploy падает на
"sops: cannot decrypt".

**RTO:** 4-6 ч (нужно ротировать все секреты + заново шифровать)

```bash
# === ШАГ 1: Не паниковать — это не утечка, это потеря доступа ===
# .env.sops.yaml в репо БЕЗОПАСЕН (он зашифрован). Просто мы его не можем
# открыть. На VPS есть текущий расшифрованный .env (chmod 600).

# === ШАГ 2: На VPS .env ещё работает — service не падает ===
# До рестарта gateway/web всё функционирует. Срочности нет.

# === ШАГ 3: Сгенерировать новую age пару ===
age-keygen -o ~/.config/sops/age/keys.txt
PUB=$(grep '^# public key:' ~/.config/sops/age/keys.txt | sed 's/.*: //')
echo "New public key: $PUB"

# === ШАГ 4: Получить plain values из VPS .env ===
# Только так — у нас нет другого источника, потому что мы потеряли ключ.
ssh deploy@PROD_HOST cat /opt/brikko/.env > /tmp/recovered.env
# (НЕ оставлять этот файл валяться — обработать сразу)

# === ШАГ 5: Обновить .sops.yaml с новым публичным ключом ===
# В .sops.yaml заменить age: ... на новый PUB
# Зашифровать новым ключом:
yq -y '.' /tmp/recovered.env > /tmp/plain.yaml  # converted env→yaml
sops -e --age "$PUB" /tmp/plain.yaml > infra/.env.sops.yaml
shred -u /tmp/plain.yaml /tmp/recovered.env

# === ШАГ 6: GH Actions secrets ===
# https://github.com/bbchort/brikko/settings/secrets/actions
# Replace SOPS_AGE_KEY с содержимым нового ~/.config/sops/age/keys.txt

# === ШАГ 7: Tg-уведомить себе же ===
# В чате CEO с самим собой — сохранить в pinned message:
# "Новая age pair от 2026-XX-XX, fingerprint=$PUB, в 1Password vault."

# === ШАГ 8: Backup ===
# Положить новый ~/.config/sops/age/keys.txt в:
#   - 1Password vault "Brikko Production" → "SOPS age key (CEO)"
#   - На бумаге в сейфе — мастер-фраза 1Password (если её ещё нет)
#   - На отдельном USB-носителе с шифрованием

# === ШАГ 9: Ротация всех ключей (опционально) ===
# Поскольку plaintext значения проходили через несколько шагов выше — для
# сверхбезопасности ротировать каждый OPENAI_/ANTHROPIC_/etc ключ ещё раз.
```

### 7.8. Quick reference — Severity → Action

| Симптом | Severity | Контактный шаг | Раздел |
|---|---|---|---|
| `/healthz` 404/500 на VPS | P0 | Перезапуск gateway, если не помогло — restore | § 7.4 |
| Все запросы к OpenAI 5xx | P1 | force-route на резерв | § 7.6 |
| Spike usage на API key | P0 | Revoke + ротация | § 7.2 |
| BEAM пользователей в spam | P1 | Investigate leak source | § 7.5 |
| VPS unreachable >5 мин | P0 | DR на новый VPS | § 7.1 |
| `pg_dump` падает | P0 | Restore + diff dance | § 7.4 |
| Atak >1k RPS на 1 IP | P1 | CF Under Attack Mode | § 7.3 |
| `sops: cannot decrypt` | P2 | Re-key через § 7.7 | § 7.7 |

---

## 8. CI/CD

| Workflow | Триггер | Что делает |
|---|---|---|
| `.github/workflows/ci.yml` | push, PR на main | lint + tests + coverage gate (см. `branch_protection_setup.md`) |
| `.github/workflows/deploy.yml` | push tag `v*.*.*` | build → push GHCR → SSH-deploy → health → rollback |
| `.github/workflows/regression.yml` | nightly cron | Полный e2e suite на staging (когда staging будет) |

**Branch protection на main:** см. `docs/branch_protection_setup.md`.
Required check — `ci · quality-gate` (один аггрегатор, дёргает все
обязательные jobs).

---

## 9. Observability

| Слой | Сервис | Стоимость | Статус |
|---|---|---|---|
| Errors (gateway) | Sentry Developer free | 0 ₽ (5k events/мес) | sprint 5 ✅ (web — sprint 1) |
| Errors (web) | Sentry Developer free | shared | sprint 1 ✅ |
| Uptime | UptimeRobot Free | 0 ₽ (50 monitors / 5 мин) | sprint 2 ✅ |
| Metrics (gateway) | `prometheus_client` → `/metrics` | 0 ₽ | sprint 5 ✅ |
| Metrics aggregation | Grafana Cloud Free | 0 ₽ (10k series / 50 GB logs) | sprint 3 |
| Logs | docker json-file → Promtail → Loki | 0 ₽ | ✅ |

**Sentry setup (gateway):**

```bash
# 1. Создать проект Sentry: https://sentry.io/organizations/brikko/projects/new/
#    Platform: Python → FastAPI
# 2. Скопировать DSN
# 3. SOPS edit:
sops infra/.env.sops.yaml
# Установить:
#   SENTRY_DSN_GATEWAY: https://...@oXXX.ingest.sentry.io/PROJECT_ID
#   SENTRY_TRACES_SAMPLE_RATE: 0.05      # 5% запросов трассируются
# 4. Deploy → Sentry автоматически активируется на старте gateway.
# 5. Verify: docker compose logs gateway | grep sentry_initialised
```

**Что именно ловим:**
- Unhandled exceptions (5xx) — автоматически.
- Performance traces — 5% запросов с метаданными provider/route.
- Breadcrumbs (без PII) — 50 последних log-events перед ошибкой.
- 4xx HTTPException — игнорируются (`_sentry_filter_4xx` в observability.py).

**Prometheus метрики (gateway):**

Доступны через `GET /metrics` (закрыт от публичного доступа в Caddyfile,
доступен только из backend-net через Grafana Cloud Agent / Promtail-style scrape).

| Метрика | Тип | Что считает |
|---|---|---|
| `brikko_requests_total` | counter | Total HTTP-запросы (labels: method, path, status) |
| `brikko_request_latency_seconds` | histogram | p50/p95/p99 latency (buckets 50ms..5min) |
| `brikko_provider_calls_total` | counter | Outbound calls (labels: provider, outcome) |
| `brikko_circuit_breaker_state` | gauge | 0=closed, 1=open, 2=half_open per provider |
| `brikko_balance_holds_active` | gauge | Active holds в Redis |
| `brikko_auth_failures_total` | counter | Auth failures by reason |

**UptimeRobot setup:**

```
Monitor 1:  HTTPS  https://api.brikko.ru/healthz   (interval 5 min) — liveness
Monitor 2:  HTTPS  https://api.brikko.ru/readyz    (interval 5 min) — readiness
Monitor 3:  HTTPS  https://brikko.ru                (interval 5 min) — landing/SPA
Alert contact: TG-bot @BrikkoOpsBot
```

**Alerting rules** — см. `infra/alerts.yml`.

P0/critical (24/7 SMS+TG):
- API down >2m
- Postgres down (/readyz=503) >2m
- 5xx rate >5% за 5m
- Negative balance detected (data corruption)
- Disk free <10%

P1/warning (TG в рабочее время):
- 4xx rate >20% (brute-force suspect)
- p95 latency >5s
- Circuit breaker открыт >5m
- Backup pipeline не запускался >26h
- Auth failure spike >10/sec

**Backup drill:**

Раз в неделю (`infra/cron.d/brikko` → понедельник 04:30 UTC) скрипт
`infra/scripts/test-backup-restore.sh` тянет последний `daily/` дамп из S3,
поднимает временный postgres-контейнер, делает `pg_restore` и проверяет
наличие ключевых таблиц. Failure → Telegram alert.

```bash
# Ручной запуск:
ssh deploy@PROD_HOST
sudo /opt/brikko/infra/scripts/test-backup-restore.sh
```

---

## 10. Image sizes (текущие после Sprint 2)

| Image | До Sprint 2 | После Sprint 2 | Что выкинуто |
|---|---|---|---|
| brikko-gateway | 533 MB | ≈250 MB | `[dev]` deps, `.venv`, tests, .git, *.md (через .dockerignore) |
| brikko-web | ≈400 MB | ≈400 MB | без изменений (web .dockerignore был корректен) |

Целевой объём по сервису gateway — 250 MB. Если меньше — ОК. Если больше
260 MB после следующих изменений — добавить в Dockerfile `find ... -name
'__pycache__' -delete` после COPY.

---

## 11. Environment variables

Источники правды:

- **`infra/.env.example`** — schema всех переменных (без значений, в git)
- **`infra/.env.sops.yaml`** — production secrets (зашифровано SOPS+age, в git)
- **`infra/.env`** — расшифрованный runtime файл на VPS (chmod 600, НЕ в git)

Производное:

- `infra/docker-compose.yml` читает `${VAR}` из `.env`
- `.github/workflows/deploy.yml` расшифровывает SOPS перед deploy (см. § 12)
- `apps/web/Dockerfile` принимает `NEXT_PUBLIC_*` через `ARG` (а не runtime
  env, потому что Next.js запекает их в bundle на build)

**Сверка `.env.example` с фактическим `.env`:**

```bash
cd /opt/brikko
diff <(grep -E '^[A-Z_]+=' .env | cut -d= -f1 | sort) \
     <(grep -E '^[A-Z_]+=' .env.example | cut -d= -f1 | sort)
```

---

## 11.1. Staging environment

**URL:** https://staging.brikko.ru
**Что роутится:**
- `/` → web-staging (Next.js)
- `/api/v1/*` → gateway-staging (FastAPI)
- Один origin → нет CORS-сложностей

**Изоляция от prod:**
- Compose project: `brikko-staging` (vs `brikko`)
- Containers: `brikko-staging-*` (vs `brikko-*`)
- БД: отдельный volume `pg_data_staging`, отдельный Postgres контейнер
- Redis: отдельный volume `redis_data_staging`
- Сеть: `brikko-staging_staging-backend-net` (БД изолирована)

**Общее с prod:**
- Caddy (один на VPS, маршрутизация по hostname)
- VPS железо (4 vCPU / 8 GB)
- frontend-net (нужно для роутинга Caddy → gateway-staging)
- Logs идут в ту же Loki/Promtail (с label `env=staging`)

**Workflow:**
```
git push origin main
   → CI зелёный
   → .github/workflows/deploy-staging.yml автоматически запускается
   → build images :staging-<sha>
   → SOPS decrypt staging secrets → /opt/brikko/.env.staging
   → docker compose -p brikko-staging up -d
   → smoke staging.brikko.ru
   → TG notify
```

**Production deploy:**
```
# После того как staging проверен — создаём релиз-тег:
git tag v0.2.0
git push origin v0.2.0
   → .github/workflows/deploy.yml запускается
   → build images :v0.2.0
   → deploy на prod
```

**Ресурсы (на той же 4vCPU/8GB):**

| Сервис | CPU | Mem |
|---|---|---|
| postgres-staging | 0.5 | 1g |
| gateway-staging | 1.0 | 1g |
| web-staging | 0.5 | 512m |
| redis-staging | 0.25 | 256m |
| **Σ staging** | **2.25** | **2.75 GB** |
| **Σ prod (см. § 3)** | 6.0 | 6 GB |
| **Σ ОБА** | **8.25** | **8.75 GB** |

CPU oversubscribe ОК (limits, не reservations). Память близко к пределу
8 GB → когда staging начнёт стабильно жрать >2.5 GB (после M3 трафика) —
выносим staging на отдельный VPS 2vCPU/4GB (~900 ₽/мес).

**Ручной staging deploy:**
```bash
ssh deploy@PROD_HOST
cd /opt/brikko
infra/scripts/sops-decrypt-env.sh        # prod .env (если нужно)
SOPS_FILE=infra/.env.staging.sops.yaml OUT=.env.staging \
    infra/scripts/sops-decrypt-env.sh    # staging .env

docker compose -p brikko-staging --env-file .env.staging \
    -f infra/docker-compose.staging.yml pull
docker compose -p brikko-staging --env-file .env.staging \
    -f infra/docker-compose.staging.yml up -d
```

**Сброс staging БД (когда нужно протестировать миграцию с нуля):**
```bash
docker compose -p brikko-staging --env-file .env.staging \
    -f infra/docker-compose.staging.yml down -v
# -v удаляет staging volumes (pg_data_staging, redis_data_staging)
# Prod volumes НЕ затрагиваются (другой compose project).
```

---

## 12. Secrets management — SOPS (TD-024 / TD-025)

**Цель:** секреты живут в git зашифрованными; ни один секрет никогда не
проходит через Claude Chat / email / Slack. На VPS plain `.env` существует
ровно столько, сколько идёт deploy (~30 сек) — потом удаляется.

### Почему SOPS+age, а не Vault или 1Password CLI

| Решение | Стоимость | Комплексность | Подходит соло-MVP? |
|---|---|---|---|
| Plain `.env` (что было) | 0 ₽ | низкая | ❌ компрометируется чатом |
| 1Password CLI | $7.99/мес/user | средняя | возможно, требует app-tokens |
| **SOPS+age** | **0 ₽** | **низкая** | **✅** |
| Hashicorp Vault | self-host или $0.5+/hour | высокая | overkill |
| AWS Secrets Manager | $0.40/secret/mo | средняя | не РФ |

SOPS — open-source утилита Mozilla. age — современный криптопротокол
(простой, без legacy GPG-баги). Один age-приватный ключ открывает все
зашифрованные файлы. На GH Actions — secret `SOPS_AGE_KEY`. На VPS —
не нужен вообще (deploy расшифровывает на runner'е и scp передаёт plain).

### One-time setup (при первом внедрении или утрате ключа)

```bash
# === Локально (CEO рабочая станция) ===

# Установить sops + age:
brew install sops age              # macOS
# или: scoop install sops age      # Windows
# или: apt install age + curl bin  # Linux

# Сгенерировать пару:
mkdir -p ~/.config/sops/age
age-keygen -o ~/.config/sops/age/keys.txt
chmod 600 ~/.config/sops/age/keys.txt

# Извлечь PUBLIC ключ:
grep '^# public key:' ~/.config/sops/age/keys.txt
# > # public key: age1xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx

# Положить PUBLIC в .sops.yaml (поле `age:`).
# git add .sops.yaml && git commit && push.

# === GitHub Actions ===
# Положить ВЕСЬ файл keys.txt (включая private key) в repo secret SOPS_AGE_KEY.
# https://github.com/bbchort/brikko/settings/secrets/actions → New
#   Name:  SOPS_AGE_KEY
#   Value: <содержимое ~/.config/sops/age/keys.txt>

# === Создать первый зашифрованный .env ===
cp infra/.env.sops.yaml.example infra/.env.sops.yaml.plain
# Заполнить .plain реальными значениями (в редакторе)
sops -e infra/.env.sops.yaml.plain > infra/.env.sops.yaml
shred -u infra/.env.sops.yaml.plain  # обязательно!
git add infra/.env.sops.yaml
git commit -m "Initial SOPS-encrypted env"
```

### Day-to-day workflow

**Изменить один секрет:**
```bash
sops infra/.env.sops.yaml
# Откроется $EDITOR (vim/nano) с расшифрованным YAML.
# Меняем value, сохраняем.
# SOPS автоматически перешифровывает при выходе.
git add infra/.env.sops.yaml
git commit -m "rotate: OPENAI_API_KEY"
git push
```

**Расшифровать локально (для local dev):**
```bash
infra/scripts/sops-decrypt-env.sh
# Пишет infra/.env с chmod 600
```

**Ротация ключей (раз в квартал или при инциденте):**
```bash
# Изменить age-ключ в .sops.yaml на новый pub:
sops updatekeys infra/.env.sops.yaml
# Перешифровывает файл новым keyset'ом без изменения values.
git commit -am "Rotate SOPS keys"
```

### Что делать если SOPS ключ потерян

См. § 7.7 — процедура восстановления через VPS plain `.env`.

### Local dev — fallback на plain .env

SOPS не должен ломать локальную разработку. Если `~/.config/sops/age/keys.txt`
отсутствует, `infra/.env` остаётся обычным `.env`-файлом, и docker-compose
работает как раньше. **Не коммитить** plain `.env` — он в `.gitignore`.

```bash
# Local dev option 1: plain .env (как было)
cp infra/.env.example infra/.env
# Заполнить значения для local Postgres/Redis (можно слабые)

# Local dev option 2: расшифровать SOPS (если age key есть)
infra/scripts/sops-decrypt-env.sh
```

### Важно

- **SOPS-encrypted файл может быть в публичном репо** — он зашифрован.
- **Plain `.env`** — НИКОГДА не в git. `.gitignore` это enforces.
- **age private key** — только в:
  - `~/.config/sops/age/keys.txt` (chmod 600) на маинтейнерах
  - GH Actions secret `SOPS_AGE_KEY`
  - 1Password vault "Brikko Production"
  - Бумажная копия мастер-фразы 1Password в сейфе
- **Никогда** не вставлять plain values секретов в Claude Chat / Slack /
  Discord / email. После любой такой утечки — ротировать все ключи + § 7.5.

---

## 12. Что в стоп-листе на этом этапе

- ❌ Kubernetes / Nomad / OpenShift (overkill для одного VPS)
- ❌ Managed AWS / GCP / Azure (РФ-юрисдикция, нужны ИНН-договоры)
- ❌ Multi-region failover (Sprint V3+)
- ❌ Pgbouncer (postgres max_connections=100 + 2 gunicorn worker × 10 pool = 20, запас огромный)
- ❌ Шифрование диска на VPS (Selectel шифрует tier-1, для остального достаточно encrypted-bucket для бэкапов)

---

## Quick links

- `infra/docker-compose.yml` — production стек
- `infra/Caddyfile` — reverse proxy + SSL
- `infra/.env.example` — все переменные окружения
- `infra/scripts/setup-s3-lifecycle.sh` — настройка ретеншна бэкапов
- `infra/scripts/backup-pg.sh` — ежедневный бэкап (запускается через cron)
- `infra/scripts/restore-pg.sh` — восстановление из бэкапа
- `infra/cron.d/brikko-docker-prune` — еженедельная очистка docker images / build cache
- `infra/scripts/smoke-extended.sh` — расширенный smoke после деплоя
- `infra/bootstrap.sh` — first-time provisioning нового VPS
- `docs/branch_protection_setup.md` — ручная настройка GitHub UI
- `docs/personal_launch_quickstart.md` — пошаговый гайд первого запуска
- `docs/monitoring.md` — UptimeRobot / Sentry / Grafana setup
