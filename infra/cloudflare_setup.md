# Cloudflare Free — настройка для Brikko

**План:** Free (0 ₽/мес).
**Что даёт:** DNS, Anycast, DDoS L3/L4 protection (unmetered), Universal SSL, Bot Fight Mode, 3 page rules, 1 zone-level rate limit rule (раньше требовало paid, с 2024 — есть в Free, см. ниже).
**Чего НЕТ на Free:** Custom WAF rules (только Cloudflare-managed), advanced rate limiting (>1 правила), image optimization, cache analytics.

---

## 1. Регистрация и подключение зон

**Сделай для всех 4 доменов** (см. `dns_setup.md` §2). Один Cloudflare-аккаунт держит все зоны.

```
1. cloudflare.com → Sign up (email + пароль + 2FA — обязательно)
2. Add a Site → brikko.ru → Free
3. Перенести NS в Reg.ru (см. dns_setup.md)
4. Повторить для брикко.рф / brikko.online / brikko.tech
```

---

## 2. SSL/TLS

Меню: **SSL/TLS → Overview**.

| Раздел | Установка | Зачем |
|---|---|---|
| Encryption mode | **Full (strict)** | CF проверяет валидность LE-сертификата на origin. Защита от MITM. |
| Edge Certificates | Universal SSL — **on** | CF выдаёт wildcard *.brikko.ru за свой счёт |
| Always Use HTTPS | **on** | Любой http:// → 301 на https:// до того как достанет origin |
| Minimum TLS Version | **TLS 1.2** | TLS 1.0/1.1 устарели, банки и крупные ИП-клиенты обычно требуют 1.2+ |
| Opportunistic Encryption | on | Доп. безопасность для http2 |
| TLS 1.3 | **on** | Современный, быстрый |
| Automatic HTTPS Rewrites | **on** | Старые ссылки в нашем HTML с http:// автоматом переписываются |
| HSTS | **включить через 7 дней после полного запуска** (preload after stable) | Если включить сразу и потом откатиться на http — браузеры будут заблокированы. См. §6. |

**ВАЖНО:** не включай Full (strict) **до** того как Caddy на Selectel выдаст Let's Encrypt cert. Иначе Cloudflare → 525. Порядок:
1. Сначала `Full` (без strict) — CF принимает любой cert от origin.
2. Дождаться `docker logs voltari-caddy | grep "obtained certificate"`.
3. Переключить на `Full (strict)`.

---

## 3. DNS

Меню: **DNS → Records**. Конкретные записи — `infra/dns_setup.md` §3-5.

Здесь только настройка zone-defaults:

| Раздел | Установка |
|---|---|
| CNAME Flattening | **At root only** (для apex CNAME work-around если понадобится) |
| Use this server's NS records as authoritative | on (default) |
| DNSSEC | **enable** в Cloudflare → DNS → Settings → DNSSEC → Enable. Затем скопируй `DS record` и **внеси в Reg.ru** в раздел DNSSEC. Защита от DNS-спуфинга. |

---

## 4. Firewall / WAF

Меню: **Security → WAF**.

### 4.1 Managed Rules (Free tier)

| Ruleset | Установка | Что блокирует |
|---|---|---|
| Cloudflare Managed Ruleset | **on, "OWASP Top 10"** | SQL injection, XSS, path traversal, common CMS exploits |
| Cloudflare OWASP Core Ruleset | sensitivity = **Medium** | На High будет много false-positive на legitimate API-запросы. Medium безопаснее на старте. |

### 4.2 Custom Rules (Free даёт 5 правил)

Меню: **Security → WAF → Custom Rules → Create rule**.

**Rule 1 — Block scanners:**
```
When: (http.user_agent contains "nessus") or (http.user_agent contains "sqlmap")
       or (http.user_agent contains "nikto") or (http.user_agent contains "masscan")
Action: Block
```

**Rule 2 — Block WordPress probes (мы не WP):**
```
When: (http.request.uri.path contains "/wp-admin")
       or (http.request.uri.path contains "/wp-login.php")
       or (http.request.uri.path contains "/xmlrpc.php")
       or (http.request.uri.path contains "/wp-content/")
Action: Block
```

**Rule 3 — Block .env / .git probes:**
```
When: (http.request.uri.path contains "/.env")
       or (http.request.uri.path contains "/.git/")
       or (http.request.uri.path contains "/.aws/")
       or (http.request.uri.path contains "/config.php")
Action: Block
```

**Rule 4 — Block PHP / cgi-bin probes:**
```
When: (http.request.uri.path matches "(?i)\\.(php|asp|cgi|jsp)$")
       and not (http.request.uri.path contains "/api/")
Action: Block
```

**Rule 5 — Russia-only on `/app/*` (защита админки от ботов из СНГ-зоны и ЕС):**
```
When: (http.request.uri.path contains "/app/")
       and (ip.geoip.country ne "RU")
       and (ip.geoip.country ne "BY")
Action: JS Challenge (не block — у нас могут быть юзеры в командировке)
```

> Rule 5 — опционально. Если есть зарубежные ИП-клиенты — снимаем.

### 4.3 Rate Limiting

Меню: **Security → WAF → Rate limiting rules**.

CF Free даёт **1 правило** zone-wide. Используем для самой опасной точки — `/auth/login`:

```
Rule name: brute-force-login-protection
When: (http.request.uri.path eq "/v1/auth/login")
       and (http.request.method eq "POST")
Rate: more than 10 requests per 1 minute (per IP)
Action: Block for 1 hour
```

**Application-level rate limit** (per API-key, не per IP) — внутри gateway через Redis. Это `slowapi` middleware, в Sprint 5 уже было.

### 4.4 Bot Fight Mode

Меню: **Security → Bots**.

| Раздел | Установка |
|---|---|
| Bot Fight Mode | **on** (бесплатно, basic) |
| Verified Bots | **allow** (Google/Bing/Yandex bot — для индексации лендинга) |
| Definitely Automated | **block** |

---

## 5. Page Rules (редиректы доменов-сквоттеров)

Меню для каждого домена: **Rules → Page Rules**.

Для `brikko.online`:
```
URL pattern: *brikko.online/*
Settings: Forwarding URL → 301 Permanent Redirect
Destination: https://brikko.ru/$2
```

Дублируй с `*www.brikko.online/*`.

То же для `brikko.tech`.

> **Лимит:** CF Free даёт 3 page rules **на каждый сайт (zone)**. На `brikko.online` нам нужно 2 (apex + www). Ок.

---

## 6. HSTS Preload (отложить на M2-M3)

**Зачем:** заносим домен в `chromium/firefox/safari` HSTS preload list. Браузеры начинают обращаться к нам ТОЛЬКО по HTTPS, без шанса на http-fallback. Защита от downgrade-атак.

**Когда включать:** через 30 дней после стабильной работы https://brikko.ru. Раньше — рискуем что захотим временно откатиться на http (debug, миграция) и сломаемся.

**Как:**
1. Cloudflare → SSL/TLS → Edge Certificates → HTTP Strict Transport Security (HSTS) → Enable.
2. Параметры:
   - Max Age = 12 months (`31536000`)
   - Include subdomains = **on**
   - Preload = **on**
3. Подать заявку на https://hstspreload.org/?domain=brikko.ru — Google проверит и через ~1-3 мес добавит в preload-list.
4. Caddy у нас уже отдаёт HSTS-header (`Strict-Transport-Security: max-age=63072000; includeSubDomains; preload`) — двойная защита.

**ВАЖНО:** preload — необратим в течение 6-12 мес (нельзя «убрать» домен из browser preload list быстро). Включай только когда уверен что https навсегда.

---

## 7. Speed / Caching

Меню: **Caching → Configuration**.

| Раздел | Установка |
|---|---|
| Caching Level | **Standard** |
| Browser Cache TTL | **Respect Existing Headers** (Caddy уже шлёт Cache-Control) |
| Always Online | **on** (если origin ляжет — CF отдаст последний кеш для статики) |

Меню: **Speed → Optimization**.

| Раздел | Установка |
|---|---|
| Auto Minify (HTML, CSS, JS) | **off** (Next.js уже всё минифицирует, double-minify ломает source maps) |
| Brotli | **on** |
| Early Hints | **on** (быстрее first paint) |
| Rocket Loader | **off** (ломает React-гидрацию) |

---

## 8. Network

Меню: **Network**.

| Раздел | Установка |
|---|---|
| HTTP/2 | **on** |
| HTTP/3 (QUIC) | **on** |
| 0-RTT Connection Resumption | **on** |
| WebSockets | **on** (на будущее, если станем стримить через WS) |
| IP Geolocation | **on** (для WAF rule 5) |
| Onion Routing | off |
| gRPC | **on** (если когда-нибудь добавим) |

---

## 9. Email

Меню: **Email → Email Routing** — пропустить (мы используем Yandex 360 SMTP, см. dns_setup §3 MX-записи). CF не нужен для почты.

---

## 10. Analytics & Logs

Меню: **Analytics & Logs**.

| Раздел | Что есть на Free |
|---|---|
| Web Analytics | **on** (бесплатно). Privacy-friendly, без cookies. Дополняет/заменяет GA. |
| Security Events | **on**, читать раз в неделю на предмет блокированных атак |
| Traffic Analytics | **on** (последние 24ч на Free, дольше — paid) |

---

## 11. Notifications

Меню: **Notifications**.

Создать notification:
- Type: **Health Check Notification** + **DDoS Attack Alert** + **HTTP DDoS Attack Alert**
- Channel: email на `ops@brikko.ru` (или личный gmail если y360 ещё не настроен)
- Trigger: high severity

---

## 12. API Token (для GH Actions / Terraform на будущее)

Меню: **My Profile → API Tokens → Create Token**.

Template: **Edit zone DNS** → выбрать только зону `brikko.ru` → **Create Token**.

Положить в GH Actions secret `CLOUDFLARE_API_TOKEN`. Сейчас не используем — на будущее (auto-DNS-deploy).

---

## 13. Чек-лист после настройки

```bash
# 1. SSL должен быть Full strict
curl -sI https://brikko.ru | grep -i "cf-"
# Ожидаем headers cf-cache-status, cf-ray

# 2. WAF блокирует sqlmap
curl -A "sqlmap/1.0" -sI https://brikko.ru
# Ожидаем HTTP/2 403 + cf-mitigated: challenge или blocked

# 3. .env пробы блокируются
curl -sI https://api.brikko.ru/.env
# Ожидаем 403 от CF (не 404 от Caddy)

# 4. WP-пробы блокируются
curl -sI https://brikko.ru/wp-admin/
# Ожидаем 403

# 5. Rate limit на /v1/auth/login — спам curl'ом 12 раз и убедиться что 11-й вернёт 429
for i in {1..15}; do
  curl -sI -X POST https://api.brikko.ru/v1/auth/login -H "Content-Type: application/json" -d '{}'
done
# Ожидаем: первые 10 → 400 (bad request, бэкенд), затем 429 (CF rate limit)

# 6. HTTP/3 работает
curl --http3 -sI https://brikko.ru | head -1
# Ожидаем "HTTP/3 200"
```

---

## 14. Откат Cloudflare (если что-то ломается)

**Если CF ломает прод (false-positive WAF, 525 SSL, etc):**

1. **Quick:** В CF → DNS → переключить proxy с Proxied (оранжевое) на DNS only (серое) для проблемного record. Трафик пойдёт напрямую на Selectel в обход CF (теряем DDoS protection, но сервис работает).
2. **Full:** В Reg.ru вернуть NS на `ns1.reg.ru`/`ns2.reg.ru`, пересоздать A-записи там. Распространение 1-24ч.

---

## 15. Связь с другими документами

- `infra/dns_setup.md` — конкретные DNS-записи.
- `infra/two_server_setup.md` — общая схема, где CF в стеке.
- `infra/launch_day_runbook.md` — порядок включения CF в день запуска.
