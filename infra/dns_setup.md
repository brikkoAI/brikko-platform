# DNS-конфигурация Brikko (4 домена)

**Регистратор:** Reg.ru (домены уже куплены).
**DNS-провайдер:** Cloudflare Free (после смены NS-серверов в Reg.ru).
**Цель:** все домены резолвятся, SSL автоматически выдаётся, кириллический брикко.рф работает.

---

## 1. Список доменов

| Домен | Назначение | Стоимость продления |
|---|---|---|
| `brikko.ru` | Primary | ~169 ₽/год |
| `брикко.рф` (Punycode `xn--80ajjbl9c.xn--p1ai`) | Защита от squatting + кирилл-аудитория | ~169 ₽/год |
| `brikko.online` | Защита от squatting (редирект) | ~226 ₽/год |
| `brikko.tech` | Защита от squatting (редирект) | ~1900 ₽/год (после первого года) |

---

## 2. Шаг 1: Передача NS-серверов в Cloudflare

**Делается ОДИН РАЗ для каждого домена** (4 раза):

1. Залогинься в Cloudflare → Add Site → ввести `brikko.ru` → Free plan.
2. Cloudflare сканит существующие DNS — пустые, скипай.
3. Cloudflare выдаст 2 NS-сервера, например:
   - `lola.ns.cloudflare.com`
   - `walt.ns.cloudflare.com`
   *(имена меняются для каждого аккаунта — копируй ровно те, что выдал CF)*
4. Залогинься в Reg.ru → Мои домены → `brikko.ru` → DNS-серверы → **Изменить**.
5. Удалить дефолтные `ns1.reg.ru` / `ns2.reg.ru`.
6. Вписать те 2 NS от Cloudflare.
7. Save. Распространение NS — 1-24 часа (обычно 2-4ч).

**Повторить для:** `брикко.рф`, `brikko.online`, `brikko.tech`.

**Проверить успех:** `dig NS brikko.ru +short` → должны вернуться cloudflare-серверы. Cloudflare пришлёт email «Domain is now active on Cloudflare».

---

## 3. Шаг 2: DNS-записи для brikko.ru (primary)

В Cloudflare dashboard → DNS → Records.

> **Важно:** колонка «Proxy status» в Cloudflare — это «оранжевое облако» (Proxied) или «серая стрелка» (DNS only). Proxied = трафик идёт через CF, скрыт origin IP, работает WAF/DDoS. DNS only = CF только резолвит, дальше клиент идёт напрямую.

| Type | Name | Content | TTL | Proxy | Зачем |
|---|---|---|---|---|---|
| A | `@` (= brikko.ru) | `<RU_PUBLIC_IP>` | Auto | **Proxied** ☑️ | Лендинг |
| A | `www` | `<RU_PUBLIC_IP>` | Auto | **Proxied** ☑️ | Алиас. Caddy редиректит на apex |
| A | `api` | `<RU_PUBLIC_IP>` | Auto | **Proxied** ☑️ | API gateway |
| A | `app` | `<RU_PUBLIC_IP>` | Auto | **Proxied** ☑️ | Личный кабинет (alias web:3000) |
| A | `docs` | `<RU_PUBLIC_IP>` | Auto | **Proxied** ☑️ | Документация (Next.js route /docs) |
| CNAME | `status` | `stats.uptimerobot.com` | Auto | **DNS only** ⚪️ | UptimeRobot status page (CF не proxy'ит CNAME за внешний хост) |
| TXT | `@` | `v=spf1 include:_spf.yandex.net -all` | Auto | — | SPF (Yandex 360 mail) |
| TXT | `_dmarc` | `v=DMARC1; p=quarantine; rua=mailto:postmaster@brikko.ru; pct=100` | Auto | — | DMARC |
| TXT | `mail._domainkey` | (значение из Yandex 360 → DKIM) | Auto | — | DKIM. Достаётся в админке Y360. |
| MX | `@` | `10 mx.yandex.net` | Auto | — | Входящая почта через Yandex 360 |
| TXT | `@` | `yandex-verification: <код из Y360>` | Auto | — | Подтверждение домена в Y360 |

**SSL для brikko.ru:**
- Cloudflare выдаёт Universal SSL автоматически (15 минут после добавления домена) — это CF edge cert.
- Caddy на Selectel выдаёт Let's Encrypt cert для origin (CF → Caddy: TLS).
- В Cloudflare → SSL/TLS → Overview → выставить режим **Full (strict)**. Это значит: CF проверяет валидность origin-сертификата.

---

## 4. Шаг 3: DNS-записи для брикко.рф (Punycode)

В Cloudflare добавляешь сайт как `xn--80ajjbl9c.xn--p1ai` (CF сам сконвертирует, можно вписать `брикко.рф`).

| Type | Name | Content | TTL | Proxy |
|---|---|---|---|---|
| A | `@` | `<RU_PUBLIC_IP>` | Auto | **Proxied** ☑️ |
| A | `www` | `<RU_PUBLIC_IP>` | Auto | **Proxied** ☑️ |

**SSL для IDN:** Caddy 2.8+ поддерживает Let's Encrypt с IDN. В Caddyfile прописываем оба варианта:
```
xn--80ajjbl9c.xn--p1ai, www.xn--80ajjbl9c.xn--p1ai {
    redir https://brikko.ru{uri} permanent
}
```

Не используй кириллицу прямо в Caddyfile — некоторые версии конфигурируются криво. Используй Punycode.

**Тест:** в браузере открой `брикко.рф` → должно редиректиться на `https://brikko.ru/`.

---

## 5. Шаг 4: brikko.online и brikko.tech (только редирект)

**Цель:** не запускать на них сервис, только редиректить на brikko.ru. Защита от squatting + если кто-то опечатается — попадёт куда надо.

**В Cloudflare для каждого:**

| Type | Name | Content | TTL | Proxy |
|---|---|---|---|---|
| A | `@` | `192.0.2.1` (IPv4 заглушка из RFC 5737) | Auto | **Proxied** ☑️ |
| A | `www` | `192.0.2.1` | Auto | **Proxied** ☑️ |

> **Зачем заглушка:** Cloudflare Page Rules требуют DNS-record для срабатывания proxy. IP `192.0.2.1` — официально reserved-for-documentation, никому не принадлежит. Трафик до origin не доходит, потому что Page Rule перехватывает первым.

**Page Rules** (Cloudflare → Rules → Page Rules):

Для `brikko.online` (создать 2 правила, по одному на apex и www):
- URL: `*brikko.online/*` → Forwarding URL → 301 Permanent → `https://brikko.ru/$2`
- URL: `*www.brikko.online/*` → Forwarding URL → 301 Permanent → `https://brikko.ru/$2`

То же для `brikko.tech`:
- URL: `*brikko.tech/*` → 301 → `https://brikko.ru/$2`
- URL: `*www.brikko.tech/*` → 301 → `https://brikko.ru/$2`

**Лимит CF Free:** 3 page rules бесплатно на каждый сайт. У нас 2 — укладываемся.

**SSL:** Universal SSL CF покроет всё автоматически.

---

## 6. Чек-лист после завершения DNS

```bash
# Запускать с любого хоста с dig (Linux/Mac/WSL).

# 1. NS должны быть Cloudflare на всех доменах
dig NS brikko.ru +short
dig NS xn--80ajjbl9c.xn--p1ai +short
dig NS brikko.online +short
dig NS brikko.tech +short

# 2. A-записи резолвятся через Cloudflare (увидишь IP'ы CF, не наш origin)
dig A brikko.ru +short
dig A api.brikko.ru +short
dig A app.brikko.ru +short

# 3. SSL должен быть валидным
curl -vI https://brikko.ru 2>&1 | grep -E "subject|issuer"
curl -vI https://api.brikko.ru 2>&1 | grep -E "subject|issuer"

# 4. Редиректы работают
curl -sI https://brikko.online | grep -i location  # → https://brikko.ru/
curl -sI https://брикко.рф | grep -i location      # → https://brikko.ru/

# 5. WWW-редирект на apex
curl -sI https://www.brikko.ru | grep -i location  # → https://brikko.ru/
```

---

## 7. Cost summary доменов

| Домен | Год 1 | Продление | Год 5 (бюджет) |
|---|---|---|---|
| brikko.ru | 169 ₽ | 169 ₽/год | 845 ₽ |
| брикко.рф | 169 ₽ | 169 ₽/год | 845 ₽ |
| brikko.online | 226 ₽ | ~1500 ₽/год (после 1-го года) | 6 226 ₽ |
| brikko.tech | 226 ₽ | ~1900 ₽/год | 7 826 ₽ |
| **Итого** | **790 ₽** | **~3 740 ₽/год** | **~15 700 ₽** |

**Решение:** держим все 4 года 1-2 (защита бренда). Если `brikko.tech`/`brikko.online` слишком дорого продлевать — отпускаем, оставляем только `brikko.ru` + `брикко.рф` (это критичные).

---

## 8. Если что-то пошло не так

| Симптом | Причина | Что делать |
|---|---|---|
| `brikko.ru` не резолвится | NS ещё не распространились | Подождать до 24ч; `dig NS brikko.ru @8.8.8.8` |
| SSL ERR_CERT_COMMON_NAME_INVALID | Caddy не успел получить cert | `docker logs voltari-caddy 2>&1 \| grep -i acme`; убедиться что 80/443 не закрыт UFW |
| `брикко.рф` не открывается | IDN в Caddyfile в кириллице → парсер ломается | Используй Punycode в конфиге |
| Cloudflare Error 521 | Origin недоступен (Caddy упал, UFW блокирует CF IPs) | `ufw allow from <CF-range> to any port 443`; см. https://www.cloudflare.com/ips/ |
| Cloudflare Error 525 | Mismatched SSL — origin cert невалиден или режим SSL Full strict, а cert self-signed | Поставь Cloudflare → SSL/TLS → **Full (strict)** только когда LE-cert уже выдан |
| MX не работает | Y360 не верифицировал домен | Дождись TXT yandex-verification; обычно 1-24ч |

---

## 9. Что в следующих документах

- `infra/cloudflare_setup.md` — детали WAF, rate limiting, page rules «куда нажимать».
- `infra/launch_day_runbook.md` — последовательность дня запуска, в т.ч. этот DNS как этап.
