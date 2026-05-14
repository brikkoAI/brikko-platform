# Launch Day Runbook — день запуска Brikko prod

**Когда:** в день получения ИНН/ОГРНИП ИП (или на следующий рабочий день).
**Длительность:** 8-10 часов чистой работы. Лучше делать с утра в будний день, чтобы Selectel/Reg.ru/банк были на связи.
**Кто:** founder сам (Claude помогает по чек-листу).

---

## 🟢 Фактический прогресс (snapshot 2026-04-30)

> Реальные операции уже выполнены, фиксирую отклонения от изначального плана.

**Что отличается от плана:**
- VPS-провайдеры **не Selectel + Hetzner**, а **Reg.ru + Aeza** (Hetzner Cloud не принимает РФ-карты, Selectel пока не подключён до получения ИНН).
- Зарубежная нода — **Aeza Финляндия 185.125.101.83** (1200 ₽/мес, 2 vCPU / 4 GB RAM, Ubuntu 24.04).
- РФ-нода — **Reg.ru 80.78.253.225** (5700 ₽/мес — дороже плана, но без KYC задержек, Ubuntu 22.04, 4 vCPU / 8 GB RAM).
- Outbound-proxy на Aeza — **tinyproxy** (не Caddy). Caddy на Aeza не нужен пока: tinyproxy легче и достаточен для CONNECT-туннелирования.

**WireGuard subnet:** 10.10.0.0/24 (Aeza = 10.10.0.1, Reg.ru = 10.10.0.2). Peer-endpoint Aeza :51820/UDP.

**Smoke-test через proxy (PASSED 2026-04-30):**
```bash
# С Reg.ru:
curl -x http://10.10.0.1:8080 https://api.openai.com/v1/models  → 401 (соединение есть, ключа нет — ОК)
curl -x http://10.10.0.1:8080 https://api.anthropic.com/v1/models → 401
curl -x http://10.10.0.1:8080 https://api.ipify.org              → 185.125.101.83 (Aeza Финляндия!)
```

**Что было сломано и как починили:**
- `BindSame yes` в `/etc/tinyproxy/tinyproxy.conf` заставлял исходящие соединения биндиться на 10.10.0.1 (приватный WireGuard IP), из-за чего tinyproxy не мог достучаться до интернета. Убрали — заработало.

**SSH-ключи:**
- `~/.ssh/brikko_regru` (ed25519) → root@80.78.253.225
- `~/.ssh/brikko_aeza` (ed25519) → root@185.125.101.83
- Оба пароля отключены в `/etc/ssh/sshd_config`. Login только по ключу.

**Hardening (применён к обоим серверам):**
- UFW firewall: 22, 80, 443 + 51820/UDP (только Aeza) + `wg0 in` (только Aeza)
- fail2ban с sshd jail (3 попытки, бан 1 час)
- unattended-upgrades включен
- non-root юзер `brikko` создан на Reg.ru с sudo

**Что осталось из этого runbook'а:**
- Cloudflare DNS для 4 доменов
- Production-deploy stack'а Brikko (gateway + web + Postgres + Redis + Caddy) на Reg.ru
- SSL Let's Encrypt через Caddy
- E2E smoke-test через api.brikko.ru
- Монитринг (UptimeRobot, Sentry, Loki)

**Что НЕ нужно делать сейчас (отложено):**
- Selectel: не нужен пока — Reg.ru покрывает РФ-юрисдикцию. Selectel — резерв на M3 если Reg.ru не вытянет.
- Hetzner: не нужен — Aeza покрывает зарубежный proxy. Hetzner — резерв на M3 если будет блокировка.
- Y360 SMTP: D+1 после фактического запуска.

---

---

## ⚠️ Pre-flight (за 1-2 дня до)

Действия которые НЕ требуют ИНН — делай заранее, чтобы день D был чисто исполнительский:

- [ ] **CEO action #1: Решить как платить Hetzner** (зарубежная карта / USDT). См. `infra/hetzner_setup.md` §1.
- [ ] **CEO action #2: Зарегистрироваться в Cloudflare** (бесплатно, email + 2FA). Просто аккаунт, не добавлять зоны пока.
- [ ] **CEO action #3: Создать SSH-ключи** на локальной машине: `~/.ssh/brikko_selectel` (ed25519) и `~/.ssh/brikko_hetzner` (ed25519). Passphrase обязателен. Положить в 1Password.
- [ ] **CEO action #4: Купить если ещё нет — Yandex 360 для домена brikko.ru** (288 ₽/мес базовый, для тех. почты). Подождать настройки SPF/DKIM позже.
- [ ] **CEO action #5: Заполнить `infra/.env.example` → `.env.local-template`** со всеми ключами OpenAI/Anthropic/Google/Yandex/Sber/ЮKassa/Sentry. Эти секреты понадобятся в SOPS на день запуска. Не коммитить!
- [ ] **CEO action #6: Подготовить SOPS age-key** если ещё не сделано (см. `infra/.env.sops.yaml.example` и комментарии в `.github/workflows/deploy.yml`). Получить age-публичный ключ, положить в `.sops.yaml`. Приватный — в 1Password и в GH Actions secret `SOPS_AGE_KEY`.
- [ ] **CEO action #7: Проверить что `bbchort/brikko` на GitHub имеет environment `production`** с required secrets: `PROD_HOST`, `PROD_SSH_PORT`, `PROD_SSH_KEY`, `SOPS_AGE_KEY`, `TG_CHAT_ID`, `TG_BOT_TOKEN`.

**Если не сделано — день D будет 12-14 часов вместо 8.**

---

## День D — по часам

### Час 0 (09:00) — Получили ИНН/ОГРНИП

**Что есть на руках:**
- ИНН ИП (12 цифр)
- ОГРНИП (15 цифр)
- Свидетельство о регистрации
- Уведомление о применении УСН (если уже подал заявление)

**Что делаем сразу:**
1. Открой ЛК Selectel → Settings → Реквизиты ЮЛ/ИП → ввести ИНН + ОГРНИП. Отправить на верификацию (1-3 рабочих часа обычно).

---

### Час 1-2 (09:00 — 11:00) — Hetzner setup

**Параллельно с верификацией Selectel** (она не требует от нас активности).

1. **Регистрация Hetzner** (если ещё не сделана):
   - https://accounts.hetzner.com/signUp
   - Email + пароль + 2FA (Google Authenticator)
   - Загрузить копию паспорта (KYC от Hetzner)
   - Добавить payment method (зарубежная карта / USDT депозит)

2. **Создать Cloud Project** «Brikko Proxy»

3. **Заказать сервер CX22 в Helsinki** — детали в `infra/hetzner_setup.md` §3.
   - Локация: HEL1 (Helsinki)
   - Image: Ubuntu 24.04
   - SSH key: загрузить ~/.ssh/brikko_hetzner.pub
   - Cloud-init: скопировать из `hetzner_setup.md` §5
   - Hostname: `brikko-proxy-hel1`
   - **Записать публичный IP**: `<HEL_PUBLIC_IP> = ___.___.___.___`

4. **Подключиться по SSH** через 2-3 минуты после старта:
   ```bash
   ssh -i ~/.ssh/brikko_hetzner deploy@<HEL_PUBLIC_IP>
   ```
   (root login отключён cloud-init'ом, заходим через `deploy`)

5. **Verify cloud-init:**
   ```bash
   sudo cloud-init status   # done
   sudo ufw status verbose
   sudo systemctl status fail2ban
   ```

6. **Записать IP** в `infra/.env.local-template` как `HETZNER_TUNNEL_IP=<IP>`.

---

### Час 2-3 (11:00 — 12:00) — WireGuard на Hetzner

1. **Сгенерировать ключи на Hetzner:**
   ```bash
   ssh deploy@<HEL_PUBLIC_IP>
   sudo -i
   cd /etc/wireguard && umask 077
   wg genkey | tee privatekey | wg pubkey > publickey
   chmod 600 privatekey
   echo "HETZNER PUBLIC KEY:"; cat publickey
   ```
   **Записать `HETZNER_PUBLIC_KEY`.**

2. **Подготовить wg0.conf** с placeholder'ами для SELECTEL_PUBLIC_KEY (заполним когда Selectel будет):
   ```bash
   nano /etc/wireguard/wg0.conf
   # Скопировать конфиг из infra/wireguard_setup.md §3
   # PrivateKey = $(cat /etc/wireguard/privatekey)
   # SELECTEL_PUBLIC_KEY = пока заглушка, подставим позже
   ```
   ⚠️ **НЕ запускаем `wg-quick@wg0`** пока нет настоящего peer. Иначе пустые PSK ругнутся.

---

### Час 3-4 (12:00 — 13:00) — Selectel сервер

К этому моменту Selectel должен верифицировать ИНН (если не — позвонить им: 8 800 555-06-75).

1. **Заказать сервер** в ЛК Selectel:
   - Cloud Servers → Создать сервер
   - Регион: **Москва (ru-1)**
   - Конфигурация: 2 vCPU / 4 GB / 40 GB SSD
   - OS: **Ubuntu 24.04 LTS**
   - SSH-ключ: загрузить `~/.ssh/brikko_selectel.pub`
   - Имя: `brikko-prod-msk1`
   - Сеть: публичный IPv4
   - Backup: ВКЛ (Selectel managed snapshot, ~7% к цене — ~100 ₽/мес. Можно ОТКЛ если делаем свои бэкапы через `infra/scripts/backup-pg.sh`)

2. **Дождаться запуска** (обычно <5 мин). **Записать IP**: `<RU_PUBLIC_IP>`.

3. **Подключиться:**
   ```bash
   ssh -i ~/.ssh/brikko_selectel root@<RU_PUBLIC_IP>
   ```

4. **Запустить bootstrap** (используем существующий `infra/bootstrap.sh`):
   ```bash
   curl -fsSL https://raw.githubusercontent.com/bbchort/brikko/main/infra/bootstrap.sh | sudo bash
   # Создаёт deploy-юзера, ставит docker, настраивает UFW, fail2ban, Telegram alerts
   ```

5. **Скопировать SSH-ключ для deploy-юзера:**
   ```bash
   sudo mkdir -p /home/deploy/.ssh
   sudo cp ~/.ssh/authorized_keys /home/deploy/.ssh/
   sudo chown -R deploy:deploy /home/deploy/.ssh
   sudo chmod 700 /home/deploy/.ssh
   sudo chmod 600 /home/deploy/.ssh/authorized_keys
   ```

6. **Verify базовая security:**
   ```bash
   sudo ufw status verbose
   # 22, 80, 443/tcp, 443/udp ALLOW; rest DENY
   sudo systemctl status docker
   sudo systemctl status fail2ban
   ```

---

### Час 4-5 (13:00 — 14:00) — WireGuard на Selectel + handshake

1. **Установить WG:**
   ```bash
   ssh deploy@<RU_PUBLIC_IP>
   sudo apt update && sudo apt install -y wireguard
   ```

2. **Сгенерировать ключи:**
   ```bash
   sudo -i
   cd /etc/wireguard && umask 077
   wg genkey | tee privatekey | wg pubkey > publickey
   chmod 600 privatekey
   echo "SELECTEL PUBLIC KEY:"; cat publickey
   ```
   **Записать `SELECTEL_PUBLIC_KEY`.**

3. **Подставить SELECTEL_PUBLIC_KEY в wg0.conf на Hetzner:**
   ```bash
   ssh deploy@<HEL_PUBLIC_IP>
   sudo nano /etc/wireguard/wg0.conf
   # Заменить <SELECTEL_PUBLIC_KEY> на реальный ключ
   sudo systemctl enable --now wg-quick@wg0
   sudo wg show
   # Должно показать peer без handshake (Selectel ещё не запустил свой)
   ```

4. **Создать wg0.conf на Selectel:**
   ```bash
   ssh deploy@<RU_PUBLIC_IP>
   sudo nano /etc/wireguard/wg0.conf
   # Скопировать из infra/wireguard_setup.md §4
   # PrivateKey = $(cat /etc/wireguard/privatekey)
   # PublicKey peer = HETZNER_PUBLIC_KEY
   # Endpoint = <HEL_PUBLIC_IP>:51820
   sudo systemctl enable --now wg-quick@wg0
   ```

5. **Проверить handshake:**
   ```bash
   # На Selectel
   sudo wg show
   # latest handshake: X seconds ago
   ping -c 3 10.10.0.1
   # 64 bytes from 10.10.0.1: ttl=64 time=20-40 ms
   ```

   Если не пингуется — см. `infra/wireguard_setup.md` §7 troubleshoot.

6. **Запустить Caddy на Hetzner** (теперь, когда туннель up):
   ```bash
   ssh deploy@<HEL_PUBLIC_IP>
   sudo nano /etc/caddy/Caddyfile
   # Скопировать из infra/hetzner_setup.md §7
   sudo caddy validate --config /etc/caddy/Caddyfile
   sudo systemctl enable --now caddy
   ```

7. **С Selectel проверить что прокси работает:**
   ```bash
   curl http://10.10.0.1:8080/healthz
   # → ok
   curl http://10.10.0.1:8080/openai/v1/models -H "Authorization: Bearer fake"
   # → 401 от OpenAI = достучались
   ```

---

### Час 5-6 (14:00 — 15:00) — Cloudflare DNS

1. **В Cloudflare добавить 4 зоны** (см. `infra/cloudflare_setup.md` §1):
   - brikko.ru
   - xn--80ajjbl9c.xn--p1ai (брикко.рф — введёшь кириллицей, CF сконвертирует)
   - brikko.online
   - brikko.tech

2. **Для каждой зоны** Cloudflare выдаст 2 NS-сервера. **Записать.**

3. **В Reg.ru заменить NS** на cloudflare-выданные для всех 4 доменов (см. `dns_setup.md` §2). Это 4 раза по 2 минуты.

4. **Дождаться распространения NS.** Проверка:
   ```bash
   dig NS brikko.ru +short
   # Должны быть cloudflare.com NS, не reg.ru
   ```
   Обычно 1-4 часа. Иногда быстрее (Reg.ru использует short-TTL).

5. **Пока ждём** — настроить Cloudflare zone settings для brikko.ru:
   - SSL/TLS = **Full** (НЕ strict пока — поставим strict после Let's Encrypt cert)
   - Always Use HTTPS = on
   - WAF managed rules = on (Medium sensitivity)
   - Custom rules — добавить 5 штук из `cloudflare_setup.md` §4.2
   - Rate limit на /v1/auth/login (§4.3)
   - Bot Fight Mode = on (§4.4)
   - HTTP/3 = on
   - Brotli = on
   - Auto Minify = **off**

6. **Когда NS распространились** — добавить DNS records (`dns_setup.md` §3):
   - A `@` → `<RU_PUBLIC_IP>` Proxied
   - A `www` → `<RU_PUBLIC_IP>` Proxied
   - A `api` → `<RU_PUBLIC_IP>` Proxied
   - A `app` → `<RU_PUBLIC_IP>` Proxied
   - A `docs` → `<RU_PUBLIC_IP>` Proxied
   (TXT/MX для почты — потом, когда Y360 настроим)

7. **брикко.рф:** A `@` → `<RU_PUBLIC_IP>` Proxied + A `www` Proxied.

8. **brikko.online / brikko.tech:** A `@` → `192.0.2.1` Proxied + A `www` Proxied + Page Rules (см. `cloudflare_setup.md` §5).

---

### Час 6-7 (15:00 — 16:00) — Backend deploy

К этому моменту: 2 сервера up, WG-туннель up, DNS зарезолвен на Cloudflare → Selectel.

1. **Создать deploy-каталог на Selectel:**
   ```bash
   ssh deploy@<RU_PUBLIC_IP>
   sudo mkdir -p /opt/voltari
   sudo chown deploy:deploy /opt/voltari
   cd /opt/voltari
   ```

2. **Склонировать репо или собрать docker-compose+Caddyfile вручную:**
   ```bash
   # Вариант 1 — git clone (если репо публичный или с deploy-key)
   git clone https://github.com/bbchort/brikko.git /tmp/brikko
   cp /tmp/brikko/infra/docker-compose.yml /opt/voltari/
   cp /tmp/brikko/infra/Caddyfile /opt/voltari/
   cp -r /tmp/brikko/infra/loki /opt/voltari/
   rm -rf /tmp/brikko

   # Вариант 2 — scp с локальной машины (если репо приватный без deploy-key)
   # scp infra/docker-compose.yml deploy@<RU_PUBLIC_IP>:/opt/voltari/
   ```

3. **Собрать `.env`:**
   ```bash
   cp ~/brikko-env-template.local /opt/voltari/.env
   # ИЛИ — если используем SOPS, расшифровать .env.sops.yaml локально и скопировать
   chmod 600 /opt/voltari/.env
   ```

   Заполнить:
   - `OUTBOUND_HTTP_PROXY=http://10.10.0.1:8080`
   - `HETZNER_TUNNEL_IP=10.10.0.1`
   - Все ключи OpenAI/Anthropic/Google/Yandex/Sber/ЮKassa
   - SECRET_KEY, POSTGRES_PASSWORD, REDIS_PASSWORD (новые, через `openssl rand -base64 32`)
   - SENTRY_DSN_*, GRAFANA_CLOUD_*, LOKI_*

4. **Триггернуть deploy через GitHub Actions:**
   - На локальной машине: `git tag v0.1.0 && git push origin v0.1.0`
   - GH Actions автоматически: build images → push в ghcr.io → SOPS decrypt → scp .env → docker compose up
   - Watch: https://github.com/bbchort/brikko/actions

5. **Альтернативно — manual первый deploy** (если auto-deploy ещё не настроен):
   ```bash
   ssh deploy@<RU_PUBLIC_IP>
   cd /opt/voltari
   docker login ghcr.io -u bbchort   # PAT с read:packages
   docker compose pull
   docker compose up -d
   docker compose ps
   docker compose logs gateway --tail=50
   ```

6. **Дождаться когда Caddy получит Let's Encrypt cert** (1-3 минуты):
   ```bash
   docker compose logs caddy | grep -i "obtained certificate"
   # certificate obtained successfully
   ```

7. **Переключить Cloudflare SSL на Full (strict)** теперь, когда LE-cert валиден.

---

### Час 7-8 (16:00 — 17:00) — Smoke test

1. **DNS + SSL работают:**
   ```bash
   curl -sI https://brikko.ru
   # HTTP/2 200, cf-ray header, server: cloudflare
   curl -sI https://api.brikko.ru/healthz
   # HTTP/2 200
   curl -s https://api.brikko.ru/healthz
   # {"status":"ok"} или подобное
   ```

2. **Frontend загружается:**
   ```bash
   curl -s https://brikko.ru/ | grep -i "<title>"
   # <title>Brikko - ...</title>
   ```

3. **Проверить /readyz** (DB+Redis):
   ```bash
   curl -s https://api.brikko.ru/readyz
   # 200 если postgres+redis up; 503 если нет
   ```

4. **🎯 КРИТИЧНЫЙ ТЕСТ — запрос идёт через Hetzner:**

   На Hetzner запустить tcpdump на интерфейсе wg0:
   ```bash
   ssh deploy@<HEL_PUBLIC_IP>
   sudo tcpdump -i wg0 -n -A 'port 8080' &
   ```

   С локальной машины — реальный API call:
   ```bash
   # Создать тестового юзера через UI brikko.ru → получить API key
   curl -X POST https://api.brikko.ru/v1/chat/completions \
     -H "Authorization: Bearer brk_<test_user_api_key>" \
     -H "Content-Type: application/json" \
     -d '{"model":"gpt-5-mini","messages":[{"role":"user","content":"Say hi"}]}'
   ```

   **Что должно произойти:**
   - На Hetzner tcpdump покажет HTTP-запрос на `10.10.0.1:8080/openai/v1/chat/completions`
   - В response — реальный ответ от OpenAI
   - В Selectel логах gateway — usage event записан
   - В Selectel `psql` — баланс уменьшился на размер запроса × naценка 15%

   ```bash
   # Проверка usage в БД
   docker compose exec postgres psql -U voltari -d voltari -c \
     "SELECT created_at, model, total_cost_rub FROM usage_events ORDER BY created_at DESC LIMIT 5;"
   ```

5. **Проверить редиректы:**
   ```bash
   curl -sI https://brikko.online | grep -i location
   # Location: https://brikko.ru/
   curl -sI https://www.brikko.ru | grep -i location
   # Location: https://brikko.ru/
   curl -sI https://брикко.рф
   # должно отдать редирект на https://brikko.ru
   ```

6. **WAF блокирует пробы:**
   ```bash
   curl -sI https://brikko.ru/.env       # 403 от CF
   curl -sI https://brikko.ru/wp-admin/  # 403
   curl -sI -A "sqlmap/1.0" https://brikko.ru   # 403
   ```

---

### Час 8-9 (17:00 — 18:00) — Мониторинг

1. **UptimeRobot — добавить 5 monitor'ов:**
   - HTTP Check `https://brikko.ru` — 5 min interval
   - HTTP Check `https://api.brikko.ru/healthz` — 1 min interval
   - HTTP Check `https://app.brikko.ru` — 5 min interval
   - HTTP Check `https://api.brikko.ru/readyz` — 5 min interval
   - HTTP Check `http://<HEL_PUBLIC_IP>:9090/healthz` — 5 min interval (Hetzner туннель health)

   Alert contacts: email + Telegram bot (`Brikko Alerts` group).

2. **Создать Status Page** в UptimeRobot (Public status page для status.brikko.ru):
   - Public URL: `https://stats.uptimerobot.com/<unique_id>`
   - Custom domain: `status.brikko.ru` (CNAME уже добавлен в DNS)
   - Включить все 5 monitor'ов

3. **Sentry** — проверить что событие приходит:
   ```bash
   ssh deploy@<RU_PUBLIC_IP>
   docker compose exec gateway python -c "import sentry_sdk; sentry_sdk.capture_message('launch day test', level='info')"
   ```
   В Sentry → Issues должен появиться event с тегом `environment=production`.

4. **Grafana Cloud Loki** — проверить что логи льются:
   ```bash
   docker compose --profile logs up -d promtail
   docker logs voltari-promtail | grep -i "level=info"
   ```
   В Grafana Cloud Explore → datasource Loki → query `{app="voltari"}` — должны быть строки.

5. **Telegram-бот** — тест alert:
   - Отправить в группу "Brikko Alerts" сообщение от GH Actions
   - Verify: при `git tag v0.1.1 && git push --tags` → бот пишет «Deploy v0.1.1 → success»

---

### Час 9 (18:00 — 19:00) — Backup setup

1. **Selectel Object Storage:**
   ```bash
   ssh deploy@<RU_PUBLIC_IP>
   # Создаём bucket в ЛК Selectel → Object Storage → Создать
   # Имя: voltari-backups-pg
   # Регион: ru-1
   # Создаём access key → записываем
   mkdir -p ~/.aws
   cat > ~/.aws/credentials << 'EOF'
[default]
aws_access_key_id = <ACCESS_KEY>
aws_secret_access_key = <SECRET_KEY>
EOF
   chmod 600 ~/.aws/credentials
   ```

2. **Запустить первый бэкап вручную:**
   ```bash
   sudo /opt/voltari/scripts/backup-pg.sh
   # Должен создать .sql.gz и загрузить в S3
   aws s3 ls s3://voltari-backups-pg/ --endpoint-url=https://s3.ru-1.storage.selcloud.ru
   ```

3. **Cron для автоматических бэкапов** — уже в `infra/cron.d/voltari`. Установить:
   ```bash
   sudo cp /opt/voltari/cron.d/voltari /etc/cron.d/voltari
   sudo systemctl restart cron
   ```

4. **Проверить test-restore** через `infra/scripts/test-backup-restore.sh` (Sprint 5 уже создан) — на следующей неделе. Сейчас не критично, но в TD занести.

---

### Час 10 (19:00) — Открываем приём первых клиентов

1. **Создать тестового clientского пользователя через UI** brikko.ru:
   - Регистрация → email verify → создать API key → пополнить welcome 200 ₽
   - Сделать тестовый запрос — убедиться что списание работает

2. **Проверить ЮKassa в test mode:**
   - Создать платёж на 100 ₽ (минимум)
   - Дойти до return_url, увидеть «зачислено»

3. **🎉 Запостить в Telegram-канал/Twitter/etc** — «Brikko открыт».

4. **Watch metrics первые 24 часа:**
   - UptimeRobot — нет ли false-down алертов
   - Sentry — какие исключения
   - Grafana — RPS, latency, error rate
   - `docker stats` — CPU/RAM на Selectel

---

## После запуска (день D+1, +7, +30)

**День D+1:**
- Установить HSTS preload в Cloudflare (если хочется на 24-часовом запасе) — нет, лучше отложить.
- Перенастроить Y360 SMTP, протестировать transactional email.

**Неделя 1:**
- Запустить `test-backup-restore.sh` — убедиться что recovery работает.
- Подать заявку на HSTS preload (`hstspreload.org`) — если sevenday uptime OK.
- Создать первого реального paying customer (даже друга-разработчика).

**Месяц 1:**
- Ревью costs: реальные ли 2050 ₽/мес или больше?
- Ревью UptimeRobot: SLA достигнут (99.5%+)?
- Ревью logs: какие самые частые ошибки в Sentry, что чинить.

---

## Если что-то идёт не так

| Симптом | Часовая метка | Что делать |
|---|---|---|
| Selectel не верифицирует ИП | Час 0-3 | Звонить в саппорт 8 800 555-06-75. **Если задержка >24ч** — переехать на VK Cloud (резерв). |
| Hetzner отказывает в KYC | Час 1-2 | Доказательство личности через Wise/банк. Резерв: **Vultr** (Frankfurt, $6/мес, принимает крипту, нет KYC). |
| WG не поднимается | Час 4-5 | См. `infra/wireguard_setup.md` §7. Самые частые причины: UFW не пропускает 51820/UDP, или endpoint в client-config неправильный. |
| Cloudflare error 525 | Час 6-7 | SSL/TLS режим временно поставить `Full` без strict. После того как Caddy выдаст cert — `Full (strict)`. |
| OpenAI отвечает 403 при тесте через прокси | Час 7-8 | Hetzner IP заблокирован OpenAI. Поднять второй CX22 в FSN1/NBG1. |
| Deploy через GH Actions падает на migration | Час 6-7 | `docker compose run --rm gateway alembic current` — посмотреть текущий state. Manual `alembic upgrade head`. |
| 4xx/5xx у frontend на /app | Час 7-8 | `NEXT_PUBLIC_API_BASE_URL` в build-args не совпадает с runtime. Проверить `.github/workflows/deploy.yml` build-args. |

---

## После запуска — что было НЕ сделано (TD)

- HSTS preload submission — D+30
- Test-backup-restore drill — D+7
- Yandex 360 SMTP final config + DKIM verify — D+1
- VK Cloud резерв — TD-070 на M3
- Vultr backup-proxy — TD-071 на M3 (если Hetzner не блокируют)
- Terraform для voice-as-a-code — TD-072 на M6 (когда добавится staging)
- Sentry sample rate tuning (если 5k events/мес исчерпываются раньше срока) — D+30

---

## CEO Action Items (короткий summary)

**ДО ДНЯ ЗАПУСКА (минимум за 2 дня):**

1. **Решить как платить Hetzner** (зарубежная карта или USDT). Без этого зарубежного proxy не будет.
2. **Зарегистрироваться в Cloudflare** + 2FA. Бесплатно, 5 минут.
3. **Создать SSH-ключи** на локальной машине (ed25519, passphrase). Положить в 1Password.
4. **Открыть аккаунт в Yandex 360** для домена brikko.ru — 288 ₽/мес базовый.
5. **Заполнить `.env.local-template`** со всеми API-ключами провайдеров. Ключи в 1Password.
6. **Подготовить SOPS age-key** + положить приватный в GH Actions secret `SOPS_AGE_KEY`.

**В ДЕНЬ ЗАПУСКА:**

1. **Verify ИП в Selectel** (это первое утреннее действие, верификация 1-3 часа).
2. **Заказать Hetzner CX22** в Helsinki — параллельно с Selectel verification.
3. **Контролировать прохождение этапов** по этому runbook'у. Если что-то не на 100% — НЕ переходить дальше, лучше задержаться на час.
