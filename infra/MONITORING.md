# Brikko Monitoring Stack

**Что это:** локальный Prometheus + Grafana stack на главном VPS (Reg.ru),
поверх существующего prod-compose. Покрывает host metrics, container
metrics, app metrics, и предоставляет один dashboard для CEO.

**Что покрывает:**
- Host CPU / RAM / Disk / Network через `node-exporter`
- Per-container CPU / RAM / I/O через `cAdvisor`
- App-level метрики Brikko Gateway: RPS, latency p50/p95, провайдер-latency,
  error rate, cache hit rate, billing-копейки — через Prometheus client уже
  встроенный в `voltari_gateway` (`/metrics`)
- Логи всех контейнеров (опционально) → Grafana Cloud Loki через `promtail`

**Что НЕ покрывает (отдельные задачи):**
- External uptime monitoring (UptimeRobot / Better Stack делают извне)
- Aeza-side метрики (CI runner + WireGuard) — отдельный stack пока не поднят
- Alerting через Telegram — отдельный шаг (Grafana → Contact points → Telegram)

## Запуск (на VPS)

Требования: prod stack уже запущен (`docker compose -f docker-compose.prod.yml up -d`),
WireGuard поднят и main-VPS виден на `10.10.0.2`.

1. **Создать `.env` поверх существующего:**

```bash
cd /opt/brikko
echo "GRAFANA_ADMIN_PASSWORD=<сгенерировать-32-символа>" >> .env
# Опционально, если grafana должна быть на ВСЕХ интерфейсах (НЕ для prod):
# echo "GRAFANA_BIND_IP=0.0.0.0" >> .env
```

Сгенерировать пароль:

```bash
openssl rand -base64 24
```

2. **Подгрузить новые конфиги** (после `git pull`):

```bash
mkdir -p data/prometheus data/grafana
chown -R 65534:65534 data/prometheus  # nobody:nogroup, как в node-exporter
chown -R 472:472 data/grafana          # grafana user
```

3. **Поднять monitoring-stack:**

```bash
docker compose \
  -f docker-compose.prod.yml \
  -f docker-compose.monitoring.yml \
  --env-file .env \
  up -d node-exporter cadvisor prometheus grafana
```

Проверить:

```bash
docker compose ps node-exporter cadvisor prometheus grafana
# Все четыре должны быть Up (healthy)
curl -sS http://localhost:9090/-/ready    # Prometheus → ready
```

4. **Открыть Grafana** с CEO-машины через WireGuard:

```
http://10.10.0.2:3001
```

Login: `admin` / `<GRAFANA_ADMIN_PASSWORD из .env>`.

Сразу после login будет dashboard «Brikko · Overview» в папке Brikko.

## Опционально: Loki log forwarder

Если у вас есть Grafana Cloud Loki Free tier (см. [infra/loki/promtail-config.yaml](loki/promtail-config.yaml)):

1. Положить в `.env`:

```
LOKI_URL=https://logs-prod-XX.grafana.net/loki/api/v1/push
LOKI_TENANT_ID=12345
LOKI_API_KEY=glc_xxxxxxxxxxxxxxxxxxxx
```

2. Поднять с профилем `loki`:

```bash
docker compose \
  -f docker-compose.prod.yml \
  -f docker-compose.monitoring.yml \
  --profile loki \
  --env-file .env \
  up -d promtail
```

## Безопасность

- Grafana bind'ится на VPN-IP (`10.10.0.2:3001`) — доступна только через
  WireGuard. Если зашёл человек без WG-ключа — он не видит UI вообще.
- Prometheus / cAdvisor / node-exporter в `backend-net`, БЕЗ ports.
  Доступны только grafana (через docker DNS).
- Default Grafana password обязателен в `.env` (compose упадёт без него).
- Анонимный доступ + регистрация выключены в env Grafana.

## Что делать если…

**Grafana показывает «No data» на panel'ах:**
- Проверить что Prometheus видит targets:
  `curl http://localhost:9090/api/v1/targets | jq '.data.activeTargets[].health'`
- Все должны быть `up`. Если `down` — проверить `docker logs brikko-prometheus`.

**Diск переполняется:**
- TSDB retention = 15 дней, cap = 5 GB (в `docker-compose.monitoring.yml`).
- Если всё равно растёт — `docker compose exec prometheus du -sh /prometheus`.
- Уменьшить retention.size до 2GB и рестарт.

**Метрики gateway не появляются:**
- Проверить `curl http://localhost:8000/metrics` (внутри сети) или через
  `docker compose exec gateway curl localhost:8000/metrics`
- Если 503 — `METRICS_ENABLED=true` в `.env`?

## Resource cost

| Сервис | RAM | Disk | CPU |
|---|---|---|---|
| node-exporter | ~64 MB | 0 | <1% |
| cadvisor | ~256 MB | 0 | ~5% |
| prometheus | ~1 GB | до 5 GB | ~5% |
| grafana | ~256 MB | <100 MB | ~2% |
| promtail (опц.) | ~64 MB | 0 | ~2% |

**Итого:** ~1.7 GB RAM, до 5 GB disk, ~13% CPU на 4-vCPU VPS.

На текущем Reg.ru VPS (4 vCPU / 8 GB RAM / 80 GB SSD) — спокойно влезает.
Когда дойдём до 100+ RPS и Prometheus перерастёт 5GB — переключимся на
remote_write в Grafana Cloud Hosted Metrics (free tier 10k series).
