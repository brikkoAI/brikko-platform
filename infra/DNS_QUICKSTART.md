# DNS Quickstart — что делать руками (15 минут)

> Это короткая инструкция для пользователя, что нажимать в браузере чтобы доменам начать резолвиться на наш Reg.ru-сервер.
>
> Полная инструкция: `infra/dns_setup.md` + `infra/cloudflare_setup.md`. Здесь — только TL;DR.

**Сервер РФ (куда смотрят домены):** `80.78.253.225` (Reg.ru)

---

## Шаг 1. Завести бесплатный аккаунт Cloudflare (3 минуты)

1. Открыть https://dash.cloudflare.com/sign-up
2. Ввести email + пароль. **Включить 2FA** (Google Authenticator) сразу.
3. Готово. План — Free.

---

## Шаг 2. Добавить 4 зоны (10 минут)

В Cloudflare Dashboard → "Add a Site" — поочерёдно добавить:

| # | Домен | Что вводить |
|---|---|---|
| 1 | `brikko.ru` | brikko.ru |
| 2 | `брикко.рф` | xn--80ajjbl9c.xn--p1ai *(или вводи кириллицей — CF сконвертирует)* |
| 3 | `brikko.online` | brikko.online |
| 4 | `brikko.tech` | brikko.tech |

**Для каждой зоны:**
- План: **Free**
- Cloudflare предложит скан существующих DNS-записей — пропусти (Continue without scanning).
- На следующем шаге Cloudflare покажет 2 NS-сервера (например `ada.ns.cloudflare.com` и `rick.ns.cloudflare.com`). **Запиши их** для каждого домена.

---

## Шаг 3. Поменять NS в Reg.ru (5 минут)

В личном кабинете Reg.ru → Мои домены → выбрать домен → "Управление DNS" → "Передача NS другому регистратору" (или похожее). Заменить дефолтные NS Reg.ru на 2 NS Cloudflare.

**Делать для всех 4 доменов.** Распространение NS — обычно 1-4 часа, иногда быстрее.

---

## Шаг 4. Настроить DNS-записи в Cloudflare (5 минут)

После того как NS распространились (можно проверить: `nslookup -type=NS brikko.ru` должен показать cloudflare.com):

### Для brikko.ru:

| Type | Name | Content | Proxy | TTL |
|---|---|---|---|---|
| A | `@` | `80.78.253.225` | 🟧 Proxied | Auto |
| A | `www` | `80.78.253.225` | 🟧 Proxied | Auto |
| A | `api` | `80.78.253.225` | 🟧 Proxied | Auto |
| A | `app` | `80.78.253.225` | 🟧 Proxied | Auto |
| A | `docs` | `80.78.253.225` | 🟧 Proxied | Auto |

### Для брикко.рф (xn--80ajjbl9c.xn--p1ai):

| Type | Name | Content | Proxy |
|---|---|---|---|
| A | `@` | `80.78.253.225` | 🟧 Proxied |
| A | `www` | `80.78.253.225` | 🟧 Proxied |

### Для brikko.online и brikko.tech:

| Type | Name | Content | Proxy |
|---|---|---|---|
| A | `@` | `80.78.253.225` | 🟧 Proxied |
| A | `www` | `80.78.253.225` | 🟧 Proxied |

*(Page Rules для редиректа `.online`/`.tech` → `.ru` настроим позже, сначала просто чтобы домены резолвились.)*

---

## Шаг 5. Cloudflare настройки (одни и те же для всех 4 зон)

В Cloudflare Dashboard → выбрать зону → SSL/TLS:
- **SSL/TLS encryption mode: Full** *(временно. После выпуска Caddy LE-сертификата переключим на Full strict)*
- **Always Use HTTPS: ON**
- **Automatic HTTPS Rewrites: ON**

Speed → Optimization:
- **Auto Minify: OFF** *(Next.js уже минифицирует, не дублировать)*
- **Brotli: ON**
- **HTTP/3 (with QUIC): ON**

Security → WAF:
- **Managed Rules: ON** (Medium sensitivity)
- **Bot Fight Mode: ON**

---

## Шаг 6. Проверить что работает

Через 5-30 минут после настройки:

```bash
# Должен быть IP Cloudflare (не Reg.ru)
nslookup brikko.ru
# → должен вернуть IP типа 104.21.x.x или 172.67.x.x (это CF proxy)

# Через CF proxy — должен открываться сайт
curl -sI https://brikko.ru
# → HTTP/2 200, server: cloudflare
```

Если `curl https://brikko.ru` возвращает 525/526 — это ОК пока, Caddy ещё не получил Let's Encrypt cert. Через 1-3 минуты после первого запроса cert выпустится, и сайт заработает.

---

## Что я (Claude) делаю параллельно

Пока вы настраиваете DNS в браузере — я разворачиваю стек на Reg.ru:
- Postgres + Redis + Brikko gateway (FastAPI) + Brikko web (Next.js) + Caddy reverse-proxy
- Сертификаты Let's Encrypt через Caddy (автоматически)
- Smoke-test что всё работает на уровне IP (без DNS)

Когда DNS пропишется — Caddy автоматически выпустит SSL и сайт заработает по https://brikko.ru.
