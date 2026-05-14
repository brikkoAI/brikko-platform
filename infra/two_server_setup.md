# Brikko — двухсерверная архитектура

**Статус:** **развёрнуто и работает**, но с отклонениями от первоначального плана.
**Решение CEO:** `02_Product/v1.5/16_ceo_decisions_2026-04-30.md` § «Двухсерверная архитектура».
**Дата плана:** 2026-04-30.
**Последнее обновление:** 2026-05-07 (CF в DNS-only mode, LE-сертификаты).

> ⚠️ **Важные отклонения от плана** (см. `infra/launch_day_runbook.md` § «Фактический прогресс» и `06_Operations/2026-05-07_infra_changes_and_followups.md`):
>
> - VPS не Selectel + Hetzner, а **Reg.ru `80.78.253.225` + Aeza Финляндия `185.125.101.83`** (KYC-ограничения у Selectel и Hetzner для самозанятого).
> - На Aeza крутится **tinyproxy** вместо Caddy (легче для CONNECT-туннелирования).
> - **С 2026-05-07 Cloudflare переведён в DNS-only mode** (TSPU блочит CF из РФ с июня 2025). Trafic идёт прямо на Reg.ru. Caddy получает реальные LE-сертификаты, не `tls internal`.
> - Раздел №2 ниже — **исходный план**, его схема устарела. Реальная топология описана в `infra/launch_day_runbook.md`.

---

## 1. Зачем два сервера

| Проблема | Одиночный сервер | Двухсерверная схема |
|---|---|---|
| 152-ФЗ (ПДн в РФ) | Соблюдается, но zero-margin | Соблюдается с явной изоляцией |
| Доступ к OpenAI/Anthropic из РФ | Нестабилен (Cloudflare/OpenAI блокируют RU IP) | Через зарубежный proxy — стабильно |
| Юридическая чистота для аудита | «Мы ходим в OpenAI с РФ-сервера» — серая зона | Чёткая граница: ПДн в РФ, обезличенный трафик за границу |
| Single point of failure при блокировке провайдером | Полный отказ | Можно переключить proxy на другой регион |
| Дельта стоимости | 1500 ₽/мес | ~2150 ₽/мес (+650 ₽ за compliance + uptime) |

**Главный мотив:** при росте до 50+ платных клиентов любой выезд OpenAI на «ban russian IP» убивает бизнес. Зарубежный proxy за 600 ₽/мес — самая дешёвая страховка из доступных.

---

## 2. Топология

```
                                    Internet
                                       │
                                       ↓
                  ┌────────────────────────────────────┐
                  │ Cloudflare Free                    │
                  │ • DNS управление (4 домена)        │
                  │ • Anycast + DDoS L3/L4 (бесплатно) │
                  │ • Bot Fight Mode                   │
                  │ • Cloudflare-managed WAF rules     │
                  └────────────────┬───────────────────┘
                                   │ TLS handshake
                                   │ X-Forwarded-For
                                   ↓
        ┌─────────────────────────────────────────────────┐
        │ 🇷🇺 РФ-сервер — Selectel Cloud Server           │
        │ Москва ru-1, 2 vCPU / 4 GB / 40 GB SSD          │
        │ Внешний IP: <RU_PUBLIC_IP>                      │
        │ ──────────────────────────────────────────────  │
        │ Caddy 2.8 (TLS-terminate, Let's Encrypt)        │
        │ ├── brikko.ru / www → web:3000 (Next.js)        │
        │ ├── api.brikko.ru → gateway:8000 (FastAPI)      │
        │ ├── app.brikko.ru → web:3000 (alias)            │
        │ ├── docs.brikko.ru → web:3000 (Next route)      │
        │ ├── status.brikko.ru → UptimeRobot Page (CNAME) │
        │ └── брикко.рф → 301 на brikko.ru                │
        │                                                  │
        │ docker-compose stack:                           │
        │ • caddy (reverse-proxy)                         │
        │ • gateway (FastAPI, Python 3.12)                │
        │ • web (Next.js 14)                              │
        │ • postgres:16-alpine (ПДн, биллинг)             │
        │ • redis:7-alpine                                │
        │ • promtail → Grafana Cloud Loki Free            │
        │                                                  │
        │ Backups: pg_dump → Selectel Object Storage      │
        │ Firewall: UFW — только 22, 80, 443, 51820/UDP   │
        └────────────────┬────────────────────────────────┘
                         │
                         │ WireGuard tunnel
                         │ UDP 51820, AES-256
                         │ 10.10.0.0/24
                         │ keepalive 25s
                         ↓
        ┌─────────────────────────────────────────────────┐
        │ 🌐 Hetzner CX22 — Helsinki (Финляндия)          │
        │ 2 vCPU / 4 GB / 40 GB NVMe                      │
        │ Внешний IP: <HEL_PUBLIC_IP>                     │
        │ ──────────────────────────────────────────────  │
        │ • WireGuard server (peer = Selectel)            │
        │ • Caddy 2.8 (HTTP-proxy для исходящего)         │
        │   ├── /openai/*    → api.openai.com             │
        │   ├── /anthropic/* → api.anthropic.com          │
        │   └── /google/*    → generativelanguage...      │
        │ • UFW: 22 (твой IP only) + 51820/UDP            │
        │ • НЕТ Docker (минимальный setup)                │
        │ • НЕТ логов запросов (privacy)                  │
        │ • Нет PostgreSQL, нет Redis, нет хранения       │
        │                                                  │
        │ Listen address для прокси: 10.10.0.1:8080       │
        │ (на 0.0.0.0 НЕ слушает — только в туннеле)      │
        └────────────────┬────────────────────────────────┘
                         │ HTTPS (TLS-к-провайдерам)
                         ↓
              OpenAI / Anthropic / Google
              (для Yandex/Sber/DeepSeek/GigaChat
               прокси не нужен — звоним из РФ напрямую)
```

---

## 3. Что делает РФ-сервер

**Принимает:**
- Все клиентские запросы через `api.brikko.ru` / `app.brikko.ru` / `brikko.ru`.
- Cloudflare → Caddy (Let's Encrypt + HTTP/3).

**Хранит:**
- БД с ПДн пользователей (email, ФИО ИП, ИНН/ОГРН, адрес, телефон).
- API-ключи клиентов (encrypted at rest через `app.SECRET_KEY`).
- Транзакции ЮKassa (закрывающие документы).
- Usage events (нужны для биллинга, без plaintext промптов).
- Логи Caddy/gateway (без plaintext PII — маскируются на уровне gateway).

**Делает при вызове LLM:**
1. Auth — проверка API-ключа клиента, лимиты, баланс.
2. PII-маскинг — заменяет email/ФИО/телефоны на placeholder'ы (mapping остаётся в Redis с TTL = duration запроса).
3. Smart routing — выбор провайдера и модели.
4. **Если провайдер = OpenAI/Anthropic/Google** — отправляет HTTP-запрос на `http://10.10.0.1:8080/<provider>/...` (через WireGuard tunnel).
5. **Если провайдер = Yandex/Sber/DeepSeek/GigaChat** — звонит напрямую из РФ (туннель не нужен).
6. PII-unmask по mapping'у в ответе.
7. Списание баланса, запись usage event, return клиенту.

**Что в env:**
```bash
OUTBOUND_HTTP_PROXY=http://10.10.0.1:8080   # WireGuard endpoint Hetzner-а
HETZNER_TUNNEL_IP=10.10.0.1
PROXIED_PROVIDERS=openai,anthropic,google   # остальные — direct
```

---

## 4. Что делает Hetzner

**Принимает только через WireGuard:**
- HTTP-запросы от Selectel на `10.10.0.1:8080`.
- На `0.0.0.0:8080` Caddy **не слушает** — снаружи 8080 закрыт UFW + Caddy bind на туннельный интерфейс.

**Делает:**
1. Получает запрос от РФ-сервера: `POST http://10.10.0.1:8080/openai/v1/chat/completions`
2. Проксирует к `https://api.openai.com/v1/chat/completions`.
3. Возвращает response в туннель.

**НЕ делает:**
- НЕТ хранения данных (никаких volumes, кроме WireGuard конфига).
- НЕТ логов запросов (Caddy log directive выключен для proxy-блоков; только access.log с минимальной инфой `{"method","path_prefix","status"}` без headers/body, ротация 1 день).
- НЕТ Docker (экономим RAM на маленьком VPS — Caddy поднимается systemd unit'ом).
- НЕТ PostgreSQL / Redis / БД любых.
- НЕТ Sentry / Loki integration.

**Что в env:**
```bash
# /etc/caddy/Caddyfile — конкретные upstream URLs
# /etc/wireguard/wg0.conf — peer config
# Ничего особого больше не нужно
```

---

## 5. WireGuard tunnel — обзор

**Назначение сети:** `10.10.0.0/24` (RFC 1918).

| Хост | Tunnel IP | Public IP | Роль WG |
|---|---|---|---|
| Hetzner Helsinki | `10.10.0.1` | `<HEL_PUBLIC_IP>` | Server (listen 51820/UDP) |
| Selectel Москва | `10.10.0.2` | `<RU_PUBLIC_IP>` | Client (PersistentKeepalive=25) |

Подробности конфига — `infra/wireguard_setup.md`.

**Почему WireGuard, а не OpenVPN/IPsec:**
- Меньше latency (~5ms overhead vs OpenVPN ~15ms).
- Конфиг — 10 строк, не 200.
- В ядре Linux 5.6+ из коробки (kernel module `wireguard`).
- AES-256 + ChaCha20, ротация ключей встроена.
- Persistent keepalive держит туннель через NAT на Selectel-стороне без проблем.

---

## 6. Failover-сценарии

| Инцидент | Что происходит | Что делать (runbook) |
|---|---|---|
| Hetzner упал | OpenAI/Anthropic/Google недоступны через Brikko (Yandex/Sber работают) | (1) Поднять резерв на Hetzner Германия (FSN1) — 30 мин. (2) Переписать `WG_ENDPOINT` на новый IP. (3) `docker compose restart gateway`. |
| Selectel упал | Полный outage сервиса | Восстановление из бэкапа на новый Selectel VPS (RTO ~2ч, RPO 24ч — см. `infra/RUNBOOK.md` §15). |
| WireGuard tunnel ляг | Интерфейс up, пинг до 10.10.0.1 не идёт | (1) `systemctl restart wg-quick@wg0` на обоих концах. (2) Если не помогло — пересоздать ключи (см. `wireguard_setup.md` §7). |
| Provider OpenAI блокирует Hetzner IP | 403 от api.openai.com на конкретный исходящий IP | Поднять второй Hetzner в другом регионе (Nuremberg / Falkenstein), переключить роутинг через `OUTBOUND_HTTP_PROXY`. |
| Cloudflare блокирует РФ-трафик | 5xx на api.brikko.ru для клиентов | (1) Отключить Cloudflare proxy (DNS-only). (2) Прямой A-record на Selectel IP. (3) Принять DDoS-риск временно. |

---

## 7. Что НЕ делается этой схемой

**Не решает:**
- Если OpenAI забанит наш аккаунт (а не IP) — proxy не поможет.
- Если Selectel заблокирует ИП по жалобе — нужен резервный провайдер (план: VK Cloud как backup, поднимаем на M6+).
- Если в РФ ограничат WireGuard на уровне DPI — переключение на Shadowsocks / VLESS (план B на 2027).
- Если Cloudflare откажет в обслуживании РФ-клиентов — switch на BunnyCDN / Yandex Cloud CDN.

**Все эти риски** — в `06_Operations/incidents/` (создаётся отдельно по запросу CEO).

---

## 8. Стоимость инфры

| Item | ₽/мес | Где платим | Карта |
|---|---|---|---|
| Selectel Cloud Server (2 vCPU / 4 GB / 40 GB) | 1 500 | ИП р/с | РФ |
| Hetzner CX22 (Helsinki) | ~450 (€4.49 × ~95) | TBD (CEO action item) | **Зарубежная или USDT** |
| Selectel Object Storage (бэкапы ~5 GB) | ~50 | ИП р/с | РФ |
| Cloudflare Free | 0 | — | — |
| Sentry Developer Free | 0 | — | — |
| Grafana Cloud Free (логи + метрики) | 0 | — | — |
| UptimeRobot Free (50 monitors) | 0 | — | — |
| Reg.ru домены | ~50 (амортизация 4 доменов × 169₽/год) | ИП р/с | РФ |
| **Итого** | **~2 050 ₽/мес** | | |

**Сравнение с одиночным сервером:** 1500 ₽/мес → 2050 ₽/мес = **+550 ₽/мес** за compliance, multi-провайдер access, и failover-страховку. На MRR 200k через 10 мес это 0.27% выручки — копейки.

**Прогноз при росте:**

| Клиенты | RPS | RU-сервер | Hetzner | Итого ₽/мес | Что меняется |
|---|---|---|---|---|---|
| 1-50 | <1 | Selectel S2 (1500) | CX22 (450) | 2 050 | как сейчас |
| 50-200 | 1-5 | Selectel S4 (3000) | CX22 (450) | 3 600 | вертикальный rescale RU |
| 200-500 | 5-20 | Selectel S6 (6000) | CX32 (900) | 7 100 | +DB на отдельную VM |
| 500-1000 | 20-50 | RU: 2 балансера + DB managed (15 000) | 2× CX32 (1800) | 17 000 | переходим на managed PG, балансер перед Caddy |

При MRR 1M ₽/мес инфра 17k = 1.7% — зона нормы для SaaS.

---

## 9. Что не используем (и почему)

- **VK Cloud** — рассматривается как backup РФ-провайдера на M6+. Сейчас Selectel выигрывает по UX и цене.
- **Yandex Cloud** — дороже Selectel в 1.5-2 раза за аналогичные SKU, плюс часть API-методов требуют corporate-аккаунта (юзер ИП-новичок упирается). Оставлен как «плохой backup».
- **Kubernetes** — на 1-2 сервера это over-engineering. docker-compose решает 100% задач MVP.
- **Managed PostgreSQL** (Selectel DBaaS) — 3000 ₽/мес сверх. Не нужен пока БД меньше 10 GB. Включаем при росте.
- **Terraform** — для двух VPS оправданно, но runbook'ом + `bootstrap.sh` обходимся. Terraform рассмотрим при появлении staging/preview-окружений.
- **AWS/GCP/Azure** — карта РФ не работает + 152-ФЗ не позволяет хранить ПДн там. Только зарубежный proxy на Hetzner.

---

## 10. Связь с другими документами

- `infra/RUNBOOK.md` — операционный runbook (один сервер, дополним секциями про Hetzner после развёртывания).
- `infra/dns_setup.md` — DNS для всех 4 доменов.
- `infra/cloudflare_setup.md` — настройка CF Free.
- `infra/hetzner_setup.md` — пошагово как развернуть Hetzner.
- `infra/wireguard_setup.md` — конфиги WireGuard на обеих сторонах.
- `infra/launch_day_runbook.md` — «день D», по часам.
- `02_Product/v1.5/16_ceo_decisions_2026-04-30.md` — CEO-решение по архитектуре.
