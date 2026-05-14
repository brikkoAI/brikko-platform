# Brikko Gateway

OpenAI-compatible LLM gateway for the Russian B2B market — single API key, ruble billing, full closing-document set.

This package is the API service. Smart router, full billing, YooKassa integration, admin endpoints and SDK clients ship in follow-up tasks. See `02_Product/04_tech_stack.md` for the design and `02_Product/01_mvp_scope.md` for the product scope.

## Deploy: alembic upgrade is not optional

Every `docker compose up -d gateway` (or any image rebuild) MUST be followed
by:

```bash
docker exec brikko-gateway alembic upgrade head
```

The 2026-05-06 inci­dent: a deploy added a new column to a model without
running migrations, the gateway boot raised `UndefinedColumn` mid-request,
the login endpoint returned 503, the CEO was locked out for ~2 hours.

`scripts/deploy-gateway.sh` already runs alembic as step 4/5; do not bypass
that script in production. If you must run a manual `docker compose` step,
chain alembic immediately:

```bash
docker compose -f docker-compose.prod.yml up -d gateway && \
docker exec brikko-gateway alembic upgrade head
```

## Status

- [x] OpenAI-compatible API surface (`/v1/chat/completions`, `/v1/models`, `/v1/usage`)
- [x] Bearer-token auth with argon2 hashes + Redis cache
- [x] Async SQLAlchemy 2.0 / asyncpg / Alembic migration 0001
- [x] 6 provider adapters: OpenAI (>=1.54), Anthropic (>=0.39, with prompt caching), Google Gemini (google-genai >=0.3, tiered pricing), DeepSeek (OpenAI-compat, auto cache hit), YandexGPT (REST + IAM/Api-Key)\*, Sber GigaChat (REST + OAuth 2.0)\*
- [x] ProviderRegistry — single instance per upstream, unconfigured providers auto-excluded from routing
- [x] Smart router engaged on every chat request (`auto:cheap` / `auto:smart` / `auto:fast` / `auto:ru-legal` + pinned)
- [x] Failover with retry-then-cross-provider chain, `X-Router-Decision` header, persisted events in `usage_events`
- [x] Static catalog of 14 MVP models with kopecks-per-1k pricing (Gemini 3.1 Pro tiered above 200k input)
- [x] OpenAI-compatible error envelope on every error path
- [x] pytest suite (~170 tests) on aiosqlite + fakeredis + respx
- [x] Billing: debit/credit/refund engine + holds + ЮKassa + autorefill
- [x] PAYG receipts: ЮKassa-самозанятые primary, lknpd.nalog.ru fallback
- [x] Management auth API (signup / verify / login / refresh / forgot+reset / change password) on cookie sessions
- [x] Account API (`GET /v1/account`, `PATCH /v1/account/profile`, `PATCH /v1/account/settings`)
- [x] Keys API (`/v1/keys` CRUD with tariff caps + revoke-cache-purge)
- [x] Seats & Invites API (`/v1/account/seats`, `/v1/account/invites`) with anonymous accept + welcome credit on new-user signup-via-invite
- [x] Speech-to-Text proxy (`/v1/audio/transcriptions`) — OpenAI Whisper, 25 MB cap, billed per minute
- [x] Embeddings proxy (`/v1/embeddings`) — OpenAI text-embedding-3-{small,large}, billed per input token
- [ ] Admin API (cross-account moderation / billing / documents) — separate task

## Modality endpoints (Sprint M1)

Both endpoints accept the same Bearer token as chat (`sk-vlt-...`) and write
to `usage_events` with `modality='stt'` / `modality='embeddings'` so the
analytics layer can split revenue by surface.

### Speech-to-Text — `POST /v1/audio/transcriptions`

OpenAI-compatible. Multipart upload, ≤25 MB. Billing is per minute of audio
(rounded UP to next 0.1 min) at ₽55.2/min including the +15% Brikko markup.

```bash
curl https://api.brikko.ru/v1/audio/transcriptions \
  -H "Authorization: Bearer sk-vlt-..." \
  -F file=@/path/to/audio.mp3 \
  -F model=whisper-1 \
  -F language=ru
```

Response is OpenAI-shape (`{"text": "..."}` for the default JSON format).
Headers: `X-Gateway-Cost-Kop`, `X-Gateway-Minutes`, `X-Gateway-Modality: stt`.

### Embeddings — `POST /v1/embeddings`

OpenAI-compatible. JSON body, single string or array of strings (≤2048 items).
Billing is per input token at ₽184/1M (small) or ₽1196/1M (large).

```bash
curl https://api.brikko.ru/v1/embeddings \
  -H "Authorization: Bearer sk-vlt-..." \
  -H "Content-Type: application/json" \
  -d '{
    "model": "text-embedding-3-small",
    "input": "Текст для векторизации"
  }'
```

Response is OpenAI-shape (`{"object": "list", "data": [{"embedding": [...]}], "usage": {...}}`).
Headers: `X-Gateway-Cost-Kop`, `X-Gateway-Tokens`, `X-Gateway-Modality: embeddings`.

\* Yandex and Sber require a signed reseller agreement with the respective cloud provider before they can carry production traffic. Until then, deploy with their env vars set on staging only. The catalog still exposes them for evaluation.

## Billing

The billing module is split across four files. Money is stored in **kopecks**
as `BigInteger`, never as float. The hot debit path is idempotent on
`(account_id, ref_id)` so duplicate webhooks and client retries cannot
double-spend or double-credit.

The user-facing `/v1/billing/*` endpoints accept **either** a Bearer token
**or** a cookie session — see "Auth methods" below for the full matrix.

```
                       ┌──────────────────────────────────────────┐
                       │  /v1/chat/completions                    │
                       │  → compute_cost_kopecks                  │
                       │  → debit_account(ref_id=request_id)      │
                       └──────────┬───────────────────────────────┘
                                  │
                ┌─────────────────┴────────────────┐
                │  billing/engine.py               │
                │  • debit_account  (FOR UPDATE)   │
                │  • credit_account (FOR UPDATE)   │
                │  • refund_account                │
                │  • hold_amount / release / commit│
                └─────────────────┬────────────────┘
                                  │
   ┌──────────────────┬───────────┴──────────────┬──────────────────┐
   │                  │                          │                  │
┌──┴──────────┐  ┌────┴────────┐   ┌─────────────┴──────┐   ┌───────┴────────┐
│ /v1/billing │  │ ЮKassa      │   │ Autorefill loop    │   │ PAYG receipt   │
│ /balance    │  │ webhooks    │   │ (asyncio, 5 min)   │   │ ЮKassa→lknpd   │
│ /topup      │  │ HMAC verify │   │ Redis lock         │   │ fallback       │
│ /transactions│  │ + idempotent│   │ Circuit breaker   │   │                │
│ /receipts/{id}│  │ credit      │   │  on 3 fails/day   │   │                │
│ /autorefill   │  └─────────────┘  └────────────────────┘   └────────────────┘
└───────────────┘
```

### Required env vars

```
YOOKASSA_SHOP_ID=             # из ЛК ЮKassa → Магазин → ID
YOOKASSA_SECRET_KEY=          # секретный ключ магазина (не публичный)
YOOKASSA_WEBHOOK_SECRET=      # секрет HMAC из «Уведомления»
YOOKASSA_RETURN_URL_TEMPLATE=https://brikko.ru/billing/return?account={account_id}

# Самозанятый-режим (CEO самозанятый до M9)
NPD_ENABLED=true
NPD_INN=                      # ИНН CEO в кабинете НПД
NPD_PASSWORD=                 # пароль lknpd.nalog.ru

AUTOREFILL_ENABLED=true
AUTOREFILL_INTERVAL_SECONDS=300
```

### ЮKassa — production checklist

1. В личном кабинете ЮKassa: оформить договор «Самозанятые» (отдельный
   продукт). Без него поле `receipt` в `create_payment` не выпускает
   автоматически чек НПД — нужен fallback через lknpd.
2. В разделе «Уведомления» добавить URL: `https://brikko.ru/v1/billing/yookassa/webhook`,
   подписать события `payment.succeeded`, `refund.succeeded`, `payment.canceled`.
   Формат подписи: HMAC SHA1 / SHA256 (мы поддерживаем оба).
3. Тестовые карты — https://yookassa.ru/developers/using-api/testing
   (карта `5555 5555 5555 4477` — успешная оплата с CVC `123` и любой датой).

### Тестирование биллинга

41 теста (engine + API + autorefill + математика 35 сценариев из
`03_Finance/10_billing_test_scenarios.md`):

```bash
uv run pytest tests/test_billing_engine.py     # 10 тестов: debit/credit/refund/holds/race
uv run pytest tests/test_billing_api.py        # 13 тестов: HTTP-эндпоинты + webhook
uv run pytest tests/test_billing_autorefill.py # 4 теста: cron, circuit breaker
uv run pytest tests/test_billing_scenarios.py  # 14 параметризированных тестов цены
```

## Local quickstart

```bash
cd apps/gateway

# 1. configure env
cp .env.example .env
# put your real OPENAI_API_KEY in .env, or leave the dummy for tests

# 2. start dependencies (or comment out and use sqlite + skip redis)
docker compose up -d postgres redis     # see infra/docker-compose.yml

# 3. install (uv preferred, pip works)
uv sync                                  # or: pip install -e ".[dev]"

# 4. apply migration
uv run alembic upgrade head

# 5. run dev server
uv run uvicorn brikko_gateway.main:app --reload --port 8000
```

Smoke test:

```bash
curl -s http://localhost:8000/healthz
# {"status":"ok","version":"0.1.0"}

# you'll need a seeded API key — create one via the admin API in the next task,
# or insert directly via psql for now (see "Seeding a key" below).
curl -s http://localhost:8000/v1/models -H "Authorization: Bearer sk-vlt-..." | jq
```

## Test

```bash
uv run pytest -v            # 20+ tests, full suite ~3 sec on a laptop
uv run pytest --cov=brikko_gateway
```

Tests run on aiosqlite in-memory + fakeredis — no Docker, no real OpenAI calls.

## Docker

The included `Dockerfile` is a multi-stage `python:3.12-slim` build. Reference it from `infra/docker-compose.yml`:

```yaml
services:
  gateway:
    build:
      context: ../apps/gateway
    image: brikko-gateway:dev
    env_file: ../apps/gateway/.env
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy
    ports:
      - "8000:8000"
    healthcheck:
      test: ["CMD", "curl", "-fsS", "http://localhost:8000/healthz"]
      interval: 30s
      timeout: 5s
      retries: 3
```

Production runs gunicorn with the uvicorn worker (default CMD in the Dockerfile).

## Layout

```
apps/gateway/
├── pyproject.toml              # python 3.12, fastapi 0.115, sqlalchemy 2.0, ...
├── Dockerfile                  # multi-stage build, gunicorn + uvicorn worker
├── alembic.ini, alembic/       # async-aware Alembic env, migration 0001_initial
├── brikko_gateway/
│   ├── main.py                 # FastAPI app factory + lifespan
│   ├── config.py               # pydantic-settings
│   ├── api/
│   │   ├── chat.py             # /v1/chat/completions (stream + json)
│   │   ├── models.py           # /v1/models, /v1/models/{id}
│   │   ├── usage.py            # /v1/usage?from=&to=
│   │   └── deps.py             # FastAPI deps (provider singleton)
│   ├── auth/
│   │   ├── keys.py             # generate / verify (argon2id)
│   │   └── middleware.py       # require_api_key dependency + Redis cache
│   ├── db/
│   │   ├── session.py          # async engine + session factory
│   │   └── models.py           # 7 tables, portable UUID, JSONB-or-JSON
│   ├── providers/
│   │   ├── base.py             # Provider abstract class + dataclasses
│   │   ├── catalog.py          # static catalog of 12 MVP models
│   │   └── openai_provider.py  # adapter on top of openai>=1.54
│   ├── billing/__init__.py     # compute_cost_kopecks + MARKUP (15 %)
│   └── utils/
│       ├── logging.py          # structlog (json in prod, console in dev)
│       └── errors.py           # OpenAI-compatible error envelope + handlers
└── tests/
    ├── conftest.py             # aiosqlite + fakeredis + StubProvider
    ├── test_db.py              # 2 tests
    ├── test_auth.py            # 6 tests
    ├── test_models.py          # 4 tests
    ├── test_chat_completions.py# 7 tests
    └── test_usage.py           # 2 tests
```

## Providers × models × env vars

| Provider | Models | Env vars | Streaming | Notes |
|---|---|---|---|---|
| OpenAI | `gpt-5.4-mini`, `gpt-5.4`, `gpt-5`, `o3`, `o4-mini` | `OPENAI_API_KEY` (`OPENAI_BASE_URL`) | yes | SDK >=1.54 |
| Anthropic | `claude-sonnet-4.6`, `claude-haiku-4.5`, `claude-opus-4.7` | `ANTHROPIC_API_KEY` | yes (translated to OpenAI SSE) | Prompt caching enabled when system prompt > ~1k tokens |
| Google | `gemini-3-flash`, `gemini-3.1-pro` | `GOOGLE_API_KEY` | yes | `gemini-3.1-pro` charges 2× rates above 200k input tokens (tiered pricing) |
| DeepSeek | `deepseek-v3.2-chat` | `DEEPSEEK_API_KEY` (`DEEPSEEK_BASE_URL`) | yes | OpenAI-compatible API; auto prompt cache (`prompt_cache_hit_tokens`) |
| Yandex\* | `yandexgpt-5.1-pro`, `yandexgpt-5-lite` | `YANDEX_API_KEY` + `YANDEX_FOLDER_ID` | no in MVP (V2.1) | `*reseller-OK` pending CEO sign-off — staging only |
| Sber\* | `gigachat-2-pro`, `gigachat-2-lite` | `SBER_AUTH_KEY` (Basic), `SBER_SCOPE` | yes (SSE → OpenAI) | `*reseller-OK` pending CEO sign-off — staging only |

\* Russian providers require a signed reseller / partner agreement with Yandex Cloud or Sber Cloud before production traffic can flow through this gateway. The adapters work in staging without that agreement; lifting to production requires CEO sign-off.

If a provider's env vars are not set, the registry skips it at startup and the router auto-excludes it from `auto:*` candidate lists. Pinned requests for an unconfigured model return HTTP 503 (`provider_unavailable`).

## Seeding a key (until admin API lands)

```python
import asyncio
from brikko_gateway.auth.keys import generate_api_key
from brikko_gateway.db.session import get_session_factory, init_engine
from brikko_gateway.db.models import User, Account, ApiKey

async def main():
    init_engine()
    factory = get_session_factory()
    async with factory() as s:
        u = User(email="founder@brikko.local", password_hash="x", email_verified=True)
        s.add(u); await s.flush()
        a = Account(owner_id=u.id, name="Founder", balance_kopecks=100_000_00)
        s.add(a); await s.flush()
        gen = generate_api_key()
        s.add(ApiKey(account_id=a.id, name="dev", key_hash=gen.key_hash, key_prefix=gen.prefix))
        await s.commit()
        print("API KEY (save this!):", gen.plaintext)

asyncio.run(main())
```

## Curl reference

Non-streaming:

```bash
curl -s -X POST http://localhost:8000/v1/chat/completions \
  -H "Authorization: Bearer $BRIKKO_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-5.4-mini",
    "messages": [{"role":"user","content":"Привет!"}]
  }' | jq
```

Streaming:

```bash
curl -N -X POST http://localhost:8000/v1/chat/completions \
  -H "Authorization: Bearer $BRIKKO_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-5.4-mini",
    "messages": [{"role":"user","content":"расскажи про SSE"}],
    "stream": true
  }'
```

OpenAI Python SDK works as-is by overriding `base_url`:

```python
from openai import OpenAI
c = OpenAI(api_key="sk-vlt-...", base_url="https://api.brikko.local/v1")
print(c.chat.completions.create(
    model="gpt-5.4-mini",
    messages=[{"role":"user","content":"hi"}],
).choices[0].message.content)
```

## Env reference

| Var | Default | Purpose |
|---|---|---|
| `BRAND_NAME` / `BRAND_DOMAIN` | `Brikko` / `brikko.local` | Surfaced in OpenAPI title etc. |
| `APP_ENV` | `local` | `local` switches log renderer to pretty console |
| `APP_DEBUG` | `false` | When true, exposes `/docs` and `/openapi.json` |
| `DATABASE_URL` | `sqlite+aiosqlite:///./brikko.db` | Use postgres+asyncpg in prod |
| `REDIS_URL` | `redis://localhost:6379/0` | Auth cache; service degrades to no-cache if down |
| `OPENAI_API_KEY` | — | Provider-side OpenAI key (server-only) |
| `OPENAI_BASE_URL` | `https://api.openai.com/v1` | Override for compat-providers / tests |
| `OPENAI_TIMEOUT_SECONDS` | `60` | HTTP read timeout (we never read-timeout streams) |
| `ANTHROPIC_API_KEY` | — | Server-only |
| `ANTHROPIC_BASE_URL` | `https://api.anthropic.com` | — |
| `GOOGLE_API_KEY` | — | AI Studio key for google-genai |
| `DEEPSEEK_API_KEY` | — | Server-only |
| `DEEPSEEK_BASE_URL` | `https://api.deepseek.com/v1` | — |
| `YANDEX_API_KEY` + `YANDEX_FOLDER_ID` | — | Both required to enable YandexGPT (\*reseller-OK pending) |
| `SBER_AUTH_KEY` | — | Basic-auth string from Sber dev cabinet (\*reseller-OK pending) |
| `SBER_SCOPE` | `GIGACHAT_API_CORP` | Scope passed to Sber OAuth |
| `ENCRYPTION_KEY` | — | Fernet key for `request_payloads` (used by future PII pipeline) |
| `AUTH_CACHE_TTL_SECONDS` | `60` | Redis cache TTL for verified keys |
| `REQUEST_PAYLOAD_RETENTION_DAYS` | `30` | TTL of stored prompts/responses |

## Auth methods — which endpoints accept what

Three auth scopes are in play. Mixing them up is the most common cause of
401s in dev, so call it out explicitly:

| Scope                              | Endpoints                                                                     | Auth                       |
|------------------------------------|-------------------------------------------------------------------------------|----------------------------|
| **End-user API (M2M / SDK)**       | `/v1/chat/completions`, `/v1/models`, `/v1/models/{id}`, `/v1/usage`, `/v1/embeddings` | **Bearer only** (`Authorization: Bearer sk-vlt-...`) |
| **Account / Auth / Keys / Seats**  | `/v1/auth/*`, `/v1/account/*`, `/v1/keys`, `/v1/account/seats`, `/v1/account/invites` | **Cookie session only** (HttpOnly `vlt_access` + `X-Requested-With: brikko-web` on mutating verbs) |
| **Billing**                        | `/v1/billing/balance`, `/v1/billing/transactions`, `/v1/billing/receipts/{id}`, `/v1/billing/topup`, `/v1/billing/autorefill` | **Bearer OR cookie session** (dual auth) |

The `/v1/billing/yookassa/webhook` endpoint is its own thing — authenticated
by HMAC signature on the request body, never by Bearer or cookie.

### Why billing is dual-auth

The same routes serve two callers:

1. **Browser dashboard (`/app/billing`)** — the SPA sees cookies, doesn't
   know any Bearer token, and shouldn't have to fetch the admin API just
   to render a balance.
2. **CLI / SDK / monitoring** — a script that already has `sk-vlt-...`
   should be able to read the balance without scraping a login flow.

`require_api_key_or_session` resolves Bearer first, then falls back to the
cookie. CSRF (`X-Requested-With: brikko-web`) is enforced **only** for the
cookie path on mutating verbs. Bearer flow is by definition not
CSRF-able — there's no ambient credential a third-party site can replay.

```
                ┌──────────────────────────────────────────────────┐
                │     /v1/billing/* (dashboard hotfix 29.04.26)    │
                └────────────────────────┬─────────────────────────┘
                                         │
                          require_api_key_or_session
                                         │
              ┌──────────────────────────┼──────────────────────────┐
              │                          │                          │
   1. Bearer present + valid?    2. Cookie session?           3. Neither?
              │                          │                          │
              ▼                          ▼                          ▼
   Principal(auth_method=          enforce X-Requested-With        401
     "api_key", api_key_id)        on POST/PATCH/DELETE          authentication_error
                                         │
                                         ▼
                                Principal(auth_method=
                                  "session", user, account)
```

## Auth API (management)

The dashboard talks to the gateway via cookie sessions, not Bearer tokens.
All endpoints live under `/v1/auth` and return the OpenAI-style error
envelope on failure. Bearer-token end-user API endpoints (`/v1/chat/...`,
`/v1/models`, ...) are unaffected.

### Flow

```
[ /signup ]  → 200 + verification email
                     │
                     ▼
[ /verify-email ] → 200 (+welcome 200 ₽ on first verify, atomic)
                     │
                     ▼
[ /login ] → 200 + Set-Cookie vlt_access (Strict, 15 m) + vlt_refresh (Lax, 30 d)

… while logged in …

POST /v1/auth/refresh           → rotate JTI, new cookies
POST /v1/auth/logout            → revoke current refresh JTI + clear cookies
POST /v1/auth/change-password   → revoke ALL refresh JTIs (re-login on every device)

POST /v1/auth/forgot-password   → 200 always (anti-enumeration); emails reset link if user exists
POST /v1/auth/reset-password    → revokes ALL refresh JTIs after the rotation
```

CSRF protection on session-protected mutating endpoints requires the SPA to
send `X-Requested-With: brikko-web` (cheap, no double-submit token in MVP).

### Welcome credit

200 ₽ is granted exactly once per email — guarded by a UNIQUE primary key
on `welcome_credits_log.email_hash`. `email_hash = sha256(email_lower)`,
`ip_hash = sha256(client_ip)` (forensic, not for blocking). Concurrent
verify calls are race-safe: the second one hits the conflict and returns
`welcome_credit_kop: 0`.

### Anti-abuse

| Endpoint           | Limit              | Key             |
|--------------------|--------------------|-----------------|
| `/signup`          | 5 / minute         | client IP       |
| `/login`           | 5 / minute         | (email, IP)     |
| `/forgot-password` | 3 / hour           | email           |

In-memory limiter for now (one-box MVP). Swappable to Redis token-bucket
without changing the call sites.

### Email backends

- `EMAIL_BACKEND=console` (default in `local`) — prints messages to stdout.
  Used by tests and dev.
- `EMAIL_BACKEND=smtp` — async via `aiosmtplib`. Requires `SMTP_HOST`,
  `SMTP_PORT`, optional `SMTP_USER`/`SMTP_PASSWORD`/`SMTP_STARTTLS`.

Templates live as plaintext files in `brikko_gateway/email/templates/` —
swap to HTML + Jinja in V2 if needed.

### Required env vars (auth)

```
JWT_SECRET=                     # rotate on prod; dev default fails loudly in production-like env
JWT_ACCESS_TTL_MINUTES=15
JWT_REFRESH_TTL_DAYS=30

COOKIE_DOMAIN=.brikko.ru       # leave empty in dev/test
COOKIE_SECURE=true              # MUST be false on http://localhost dev

CORS_ORIGINS=https://brikko.ru,http://localhost:3000

EMAIL_VERIFICATION_TTL_HOURS=24
PASSWORD_RESET_TTL_MINUTES=60

EMAIL_BACKEND=console           # console | smtp
EMAIL_FROM=no-reply@brikko.ru
SMTP_HOST=
SMTP_PORT=587
SMTP_USER=
SMTP_PASSWORD=
SMTP_STARTTLS=true
SMTP_TIMEOUT_SECONDS=10

BASE_URL_FRONTEND=http://localhost:3000   # used to build {link} in emails

SIGNUP_RATE_PER_MINUTE=5
LOGIN_RATE_PER_MINUTE=5
FORGOT_RATE_PER_HOUR=3
```

## Account API

All routes live under `/v1/account` and are session-cookie protected
(`require_session` + `X-Requested-With: brikko-web` on mutating verbs).

| Method | Path                  | Body                                          | Description                                        |
|--------|-----------------------|-----------------------------------------------|----------------------------------------------------|
| GET    | `/v1/account`         | —                                             | Profile + balance + flags snapshot                 |
| PATCH  | `/v1/account/profile` | `{name?, email?}`                             | Rename workspace and / or change login email       |
| PATCH  | `/v1/account/settings`| `{prompt_logging_enabled?, notifications?}`   | Toggle prompt logging + free-form preferences      |

```bash
# GET /v1/account
curl -b cookies.txt http://localhost:8000/v1/account
# {
#   "user_id": "...",
#   "email": "alice@example.com",
#   "account_id": "...",
#   "name": "Alice",
#   "tariff": "pro",
#   "balance_kopecks": 99900,
#   "prompt_logging_enabled": true,
#   "notifications": {},
#   "email_verified": true,
#   "created_at": "2026-04-29T10:42:11+00:00"
# }

# PATCH /v1/account/profile — change email; resets email_verified=false and emails new link
curl -b cookies.txt -c cookies.txt \
  -H "Content-Type: application/json" \
  -H "X-Requested-With: brikko-web" \
  -X PATCH http://localhost:8000/v1/account/profile \
  -d '{"email": "alice+new@example.com"}'

# PATCH /v1/account/settings — opt out of prompt logging (CEO 29.04 store_prompts toggle)
curl -b cookies.txt -c cookies.txt \
  -H "Content-Type: application/json" \
  -H "X-Requested-With: brikko-web" \
  -X PATCH http://localhost:8000/v1/account/settings \
  -d '{"prompt_logging_enabled": false}'
```

Field mapping (no DB rename per CEO 29.04):

- `prompt_logging_enabled` (API) ⇄ `accounts.store_prompts` (DB, BOOLEAN).
- `notifications` (API)          ⇄ `accounts.settings["notifications"]` (JSONB).

Email-change semantics: collision-checked against `users.email`, demotes
`email_verified=false`, and dispatches a fresh verification email (best
effort — failure is logged, never raises).

## Keys API

CRUD for end-user API keys (the `sk-vlt-...` Bearer tokens used against
`/v1/chat/...`). Cookie-session protected.

| Method | Path              | Body                       | Code  | Description                                              |
|--------|-------------------|----------------------------|-------|----------------------------------------------------------|
| GET    | `/v1/keys`        | —                          | 200   | List keys (active + revoked, frontend filters)           |
| POST   | `/v1/keys`        | `{name, scope}`            | 201   | Create — `full_key` shown ONCE in response               |
| PATCH  | `/v1/keys/{id}`   | `{name}`                   | 200   | Rename only (scope is immutable post-create)             |
| DELETE | `/v1/keys/{id}`   | —                          | 204   | Soft-delete: status=REVOKED + revoked_at=now()           |

```bash
# Create — capture the plaintext NOW; you can never get it back
curl -b cookies.txt \
  -H "Content-Type: application/json" \
  -H "X-Requested-With: brikko-web" \
  -X POST http://localhost:8000/v1/keys \
  -d '{"name": "production", "scope": "write"}'
# 201 → {"id":"...","name":"production","full_key":"sk-vlt-aB12cd34...","prefix":"sk-vlt-aB12cd","scope":"write","created_at":"..."}

# List — never echoes plaintext
curl -b cookies.txt http://localhost:8000/v1/keys
# 200 → [{"id":"...","name":"production","prefix":"sk-vlt-aB12cd","scope":"write","status":"active","last_used_at":null,"created_at":"...","revoked_at":null}]

# Rename
curl -b cookies.txt \
  -H "Content-Type: application/json" \
  -H "X-Requested-With: brikko-web" \
  -X PATCH http://localhost:8000/v1/keys/$KEY_ID \
  -d '{"name": "renamed"}'

# Revoke (soft)
curl -b cookies.txt \
  -H "X-Requested-With: brikko-web" \
  -X DELETE http://localhost:8000/v1/keys/$KEY_ID
# 204 (no body)
```

### Tariff limits

Active (non-revoked) keys per tariff:

| Tariff          | Max active keys |
|-----------------|-----------------|
| `payg`          | 3               |
| `pro`           | 10              |
| `team`          | 30              |
| `business`      | 100             |
| `business_plus` | unlimited       |

Hitting the cap returns `403 key_limit_reached`. Revoking a key frees a
slot — the cap counts against `status != REVOKED`.

### Safety model

- Plaintext is generated server-side, hashed with **argon2** (OWASP params),
  then discarded. The DB stores only `key_hash` + `key_prefix` (first 14
  chars: `sk-vlt-XXXXXXX`).
- POST is the **only** endpoint that ever returns the plaintext. Listing or
  fetching a key later returns just the prefix.
- Ownership is enforced on every PATCH/DELETE: keys belonging to other
  accounts return **404** (not 403) to avoid leaking key existence.
- DELETE is soft (`status=REVOKED`, `revoked_at=now()`). The bearer-auth
  Redis cache is purged via the `auth:key:by_id:<key_id>` index, so a
  freshly-revoked key 401s on the next chat call without waiting for the
  60 s `AUTH_CACHE_TTL_SECONDS`.

## Seats & Invites API

Team management for a workspace. All cookie-protected endpoints share the
`X-Requested-With: brikko-web` CSRF gate from `require_session`. The single
**anonymous** endpoint is `POST /v1/account/seats/accept` — invites arrive as
a link in an email, the recipient is not yet logged in.

| Method | Path                                  | Auth        | Description                                     |
|--------|---------------------------------------|-------------|-------------------------------------------------|
| GET    | `/v1/account/seats`                   | session     | List owner + members                            |
| POST   | `/v1/account/seats/invite`            | owner/admin | Send an invite email (idempotent on email)      |
| DELETE | `/v1/account/seats/{user_id}`         | owner/admin | Remove a member (rules below)                   |
| POST   | `/v1/account/seats/accept`            | **none**    | Accept an invite token (anonymous, rate-limited)|
| GET    | `/v1/account/invites`                 | owner/admin | List pending non-expired invites                |
| DELETE | `/v1/account/invites/{invite_id}`     | owner/admin | Revoke a pending invite                         |

### Roles & permissions matrix

|                                | owner | admin | member |
|--------------------------------|:-----:|:-----:|:------:|
| List seats                     |  yes  |  yes  |  yes   |
| List pending invites           |  yes  |  yes  |  no    |
| Invite new admin / member      |  yes  |  yes  |  no    |
| Revoke pending invite          |  yes  |  yes  |  no    |
| Remove a **member**            |  yes  |  yes  |  no    |
| Remove **another admin**       |  yes  |  no   |  no    |
| Remove the **owner**           |  no   |  no   |  no    |

`role="owner"` is set exactly once at signup. Inviting a user as `owner`
returns `400 invalid_role`. Transferring ownership is a V2 endpoint.

### Flow — invite + accept (existing user)

```
[Owner SPA]                                     [Invitee inbox]
     │                                                  │
     │  POST /v1/account/seats/invite                   │
     │       {email: "alice@x.ru", role: "member"}      │
     │ ─────────────────────────────────────────────►   │
     │                                                  │
     │  EmailInvite row created (token_hash only)       │
     │  email sent with /accept-invite?token=…          │
     │                                                  │
     │                                                  │  click link
     │                                                  ▼
     │                                            [Frontend /accept-invite]
     │                                                  │
     │                                                  │  POST /v1/account/seats/accept
     │                                                  │       {token: "..."}
     │                                                  ▼
     │                                            User exists → Seat created,
     │                                            invite.accepted_at set, no cookies.
     │                                            User logs in normally.
```

### Flow — invite + accept (new user, autologin)

```
POST /v1/account/seats/accept {token}
       │
       ├─ Token resolved → EmailInvite found
       │
       ├─ User does NOT exist:
       │      ├─ Create User (email_verified=true — link proves ownership)
       │      ├─ Create primary Account (their own workspace)
       │      ├─ Atomic 200 ₽ welcome credit (PK on welcome_credits_log.email_hash
       │      │   prevents farming via disposable mailboxes)
       │      └─ Create Seat in inviter's account (role from invite)
       │
       ├─ Mark invite.accepted_at = now()
       │
       └─ Mint access + refresh JWT, Set-Cookie (autologin=true).
          The cookies anchor to the user's OWN primary account,
          not the inviter's — the SPA's account-switcher (V1.5) handles that.
```

### Idempotency, expiry, error codes

* Re-inviting the same email while a non-expired invite exists **rotates the
  token** (the old plaintext is unrecoverable — only the hash was stored) and
  returns the **same** `invite_id`. The role on the existing row is updated
  to whatever the latest call requested. Two emails go out (one per call).
* Inviting an email that already has a Seat in this account → `409 already_member`.
* Accepting an expired token → `400 invite_expired`.
* Accepting a token whose row has `accepted_at` set → `410 invite_accepted`.
* Accept endpoint is **rate-limited by client IP** (`ACCEPT_INVITE_RATE_PER_HOUR`,
  default 5/h) to block token-guessing brute force.

### Required env vars (seats)

```
INVITE_TTL_DAYS=7                       # email_invites.expires_at = now + N days
ACCEPT_INVITE_RATE_PER_HOUR=5           # per-IP, anonymous endpoint
```

### Curl reference

```bash
# Send an invite
curl -b cookies.txt \
  -H "Content-Type: application/json" \
  -H "X-Requested-With: brikko-web" \
  -X POST http://localhost:8000/v1/account/seats/invite \
  -d '{"email":"alice@x.ru","role":"member"}'
# 200 → {"invite_id":"...","expires_at":"2026-05-06T..."}

# List members
curl -b cookies.txt http://localhost:8000/v1/account/seats
# 200 → [{"user_id":"...","email":"...","role":"owner","joined_at":"..."}, ...]

# Pending invites
curl -b cookies.txt http://localhost:8000/v1/account/invites

# Revoke pending invite
curl -b cookies.txt \
  -H "X-Requested-With: brikko-web" \
  -X DELETE http://localhost:8000/v1/account/invites/$INVITE_ID
# 204

# Accept (anonymous — no cookies, no CSRF header)
curl -X POST http://localhost:8000/v1/account/seats/accept \
  -H "Content-Type: application/json" \
  -d '{"token":"<from-email>"}'
# 200 → {"user_id":"...","account_id":"...","autologin":true|false} + Set-Cookie if new user

# Remove a member (owner or admin)
curl -b cookies.txt \
  -H "X-Requested-With: brikko-web" \
  -X DELETE http://localhost:8000/v1/account/seats/$USER_ID
# 204
```

## Production Deployment

Single-host docker-compose deploy (the M0 shape). HA upgrade lives in M6.

### One-time bootstrap

```bash
# 1) Clone repo on the prod host (Yandex Cloud VM, Ubuntu 24.04).
git clone git@github.com:bbchort/brikko.git && cd brikko/apps/gateway

# 2) Copy + edit env file. Never commit the resulting .env — it has secrets.
cp .env.example .env
# Open .env and fill: OPENAI_API_KEY, ANTHROPIC_API_KEY, GOOGLE_API_KEY,
# DEEPSEEK_API_KEY, YOOKASSA_*, JWT_SECRET (min 32 chars), COOKIE_DOMAIN,
# CORS_ORIGINS, EMAIL_BACKEND=smtp + SMTP_* if real mail, NPD_INN/NPD_PASSWORD.

# 3) Build the gateway image locally (or pull from GHCR if the deploy
#    workflow has run). Tag matches the git SHA for traceability.
docker build -t brikko-gateway:$(git rev-parse --short HEAD) .
docker tag brikko-gateway:$(git rev-parse --short HEAD) brikko-gateway:latest

# 4) Bring up DB + Redis + gateway. Compose file lives at infra/.
cd ../../infra && docker compose up -d postgres redis gateway

# 5) Apply migrations against the live DB. Idempotent — safe to re-run.
docker compose run --rm gateway alembic upgrade head

# 6) Smoke: liveness + readiness (Caddy/UptimeRobot will hit /health/ready).
curl -fsS http://localhost:8000/healthz
curl -fsS http://localhost:8000/health/ready | jq

# 7) Caddy in front (TLS + HTTP/2). Sample Caddyfile in infra/Caddyfile.
sudo systemctl reload caddy
```

### Day-2: rolling release

```bash
# Pull latest, rebuild, replace the gateway container only.
git pull
docker build -t brikko-gateway:$(git rev-parse --short HEAD) apps/gateway
docker tag brikko-gateway:$(git rev-parse --short HEAD) brikko-gateway:latest
cd infra && docker compose up -d --no-deps --force-recreate gateway

# Migrations BEFORE traffic if the release ships a new alembic revision.
docker compose run --rm gateway alembic upgrade head
```

The compose file uses `restart: unless-stopped`. The Dockerfile has a `HEALTHCHECK` curling `/healthz` so Caddy's `health_uri` directive can route around a crashed container while another instance comes up.

### Full env-var reference

The single source of truth is `apps/gateway/.env.example`. Keep it in sync. Vars grouped by concern, with defaults / required-flag:

**App identity**

| Var | Default | Required | Notes |
|---|---|---|---|
| `BRAND_NAME` | `Brikko` | no | OpenAPI title, email subjects. |
| `BRAND_DOMAIN` | `brikko.local` | no | Used in templated email links. |
| `APP_ENV` | `local` | yes (`production` in prod) | Switches log renderer + cookie defaults. |
| `APP_DEBUG` | `false` | no | `true` exposes `/docs` and `/openapi.json`. **Never true in prod.** |

**Database / cache**

| Var | Default | Required | Notes |
|---|---|---|---|
| `DATABASE_URL` | `sqlite+aiosqlite:///./brikko.db` | yes | `postgresql+asyncpg://user:pass@host:5432/db` in prod. |
| `REDIS_URL` | `redis://localhost:6379/0` | yes | Single node MVP; Sentinel in M6 (TD-6). |

**LLM providers** (set only those you actually have keys for; unconfigured = excluded from router)

| Var | Default | Required | Notes |
|---|---|---|---|
| `OPENAI_API_KEY` | — | recommended | Server-only, never sent to the browser. |
| `OPENAI_BASE_URL` | `https://api.openai.com/v1` | no | Override for proxies / tests. |
| `OPENAI_TIMEOUT_SECONDS` | `60` | no | Read timeout (streams never read-timeout). |
| `ANTHROPIC_API_KEY` | — | recommended | — |
| `ANTHROPIC_BASE_URL` | `https://api.anthropic.com` | no | — |
| `GOOGLE_API_KEY` | — | recommended | AI Studio key for `google-genai`. |
| `DEEPSEEK_API_KEY` | — | recommended | OpenAI-compat client. |
| `DEEPSEEK_BASE_URL` | `https://api.deepseek.com/v1` | no | — |
| `YANDEX_API_KEY` + `YANDEX_FOLDER_ID` | — | optional | **Reseller agreement pending** — staging only. |
| `SBER_AUTH_KEY` | — | optional | **Reseller agreement pending** — staging only. |
| `SBER_SCOPE` | `GIGACHAT_API_CORP` | no | OAuth scope. |

**Auth / sessions**

| Var | Default | Required | Notes |
|---|---|---|---|
| `JWT_SECRET` | `dev-only-not-secure` | **yes in prod** | Min 32 chars, rotate quarterly. |
| `JWT_ACCESS_TTL_MINUTES` | `15` | no | — |
| `JWT_REFRESH_TTL_DAYS` | `30` | no | — |
| `COOKIE_DOMAIN` | empty | yes in prod | Set to `.brikko.ru` so the SPA on `app.brikko.ru` shares cookies with the API on `api.brikko.ru`. |
| `COOKIE_SECURE` | `true` | yes (`false` only on http://localhost dev) | — |
| `CORS_ORIGINS` | `http://localhost:3000` | yes | Comma-separated list, no trailing slash. |
| `AUTH_CACHE_TTL_SECONDS` | `60` | no | Redis TTL for verified API keys. |
| `ENCRYPTION_KEY` | — | yes in prod | Fernet key (`python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`) for `request_payloads`. |
| `REQUEST_PAYLOAD_RETENTION_DAYS` | `30` | no | TTL of stored prompts/responses. |

**Email**

| Var | Default | Required | Notes |
|---|---|---|---|
| `EMAIL_BACKEND` | `console` | yes (`smtp` in prod) | `console` writes to stderr — fine for local. |
| `EMAIL_FROM` | `no-reply@brikko.ru` | yes | Visible to users. |
| `SMTP_HOST` / `SMTP_PORT` | — / `587` | yes if `smtp` | Yandex Mail: `smtp.yandex.ru:465` + STARTTLS=false. |
| `SMTP_USER` / `SMTP_PASSWORD` | — | yes if `smtp` | App-password, never the personal password. |
| `SMTP_STARTTLS` | `true` | no | — |
| `SMTP_TIMEOUT_SECONDS` | `10` | no | — |
| `BASE_URL_FRONTEND` | `http://localhost:3000` | yes in prod | Used in `{link}` of emails. |
| `EMAIL_VERIFICATION_TTL_HOURS` | `24` | no | — |
| `PASSWORD_RESET_TTL_MINUTES` | `60` | no | — |

**Rate limits**

| Var | Default | Required | Notes |
|---|---|---|---|
| `SIGNUP_RATE_PER_MINUTE` | `5` | no | Per IP. |
| `LOGIN_RATE_PER_MINUTE` | `5` | no | Per email-or-IP. |
| `FORGOT_RATE_PER_HOUR` | `3` | no | Per email. |

**Billing — ЮKassa + НПД**

| Var | Default | Required | Notes |
|---|---|---|---|
| `YOOKASSA_SHOP_ID` | — | yes for paid tariffs | From ЮKassa cabinet. |
| `YOOKASSA_SECRET_KEY` | — | yes for paid tariffs | Secret, not public. |
| `YOOKASSA_WEBHOOK_SECRET` | — | yes for paid tariffs | HMAC verification on `/v1/billing/yookassa/webhook`. |
| `YOOKASSA_RETURN_URL_TEMPLATE` | — | yes | `https://brikko.ru/billing/return?account={account_id}`. |
| `NPD_ENABLED` | `false` | yes if CEO is самозанятый | Activates lknpd fallback for receipts. |
| `NPD_INN` | — | yes if `NPD_ENABLED` | — |
| `NPD_PASSWORD` | — | yes if `NPD_ENABLED` | lknpd.nalog.ru password. |
| `AUTOREFILL_ENABLED` | `true` | no | Disable to pause auto-topup loop. |
| `AUTOREFILL_INTERVAL_SECONDS` | `300` | no | Poll cadence; aggressive past 60s. |

### Troubleshooting

**`401 Unauthorized` on every `/v1/auth/login` POST despite valid creds.**
The browser is dropping the `brikko_session` cookie because it is on a different eTLD+1 from the API. Check `COOKIE_DOMAIN`:

* SPA at `app.brikko.ru`, API at `api.brikko.ru` → set `COOKIE_DOMAIN=.brikko.ru` (leading dot is required).
* SPA and API on the same host (e.g. `brikko.ru/` + `brikko.ru/v1/`) → leave `COOKIE_DOMAIN` empty.
* `localhost:3000` ↔ `localhost:8000` → leave empty AND set `COOKIE_SECURE=false`.

**`/health/ready` returns 503 but `/healthz` returns 200.**
Process is alive, dependency is not. Read the `checks` JSON:

* `database: down` → check `DATABASE_URL`, network ACLs, run `docker compose logs postgres` and `docker compose exec gateway alembic current`.
* `redis: down` → `docker compose ps redis`, `docker compose exec redis redis-cli ping`.
* `redis: not_configured` → app booted before Redis was reachable; the gateway will log `redis_unavailable` on startup. Restart the gateway container after Redis is up.

**ЮKassa webhook returns 400 / never reaches the gateway.**
Two common causes:

* **Reverse-proxy buffering** — Nginx default buffers POST bodies up to 16 KB. If your Nginx config has `proxy_request_buffering on;`, switch it off for `/v1/billing/yookassa/webhook` so the body streams through. Caddy buffers by default but the limit is 1 MB and ЮKassa payloads are <2 KB, so no action there.
* **HMAC mismatch** — verify `YOOKASSA_WEBHOOK_SECRET` matches the secret in ЮKassa cabinet → Уведомления. Rotate if unsure (it does not invalidate already-paid orders).

**Gemini calls fail with `read-only attribute 'aio'`.**
You're on a stale image. `google-genai >=1.0` made `Client.aio` read-only; the fix is in `tests/test_google_provider.py` and the adapter itself uses the property correctly. Rebuild the gateway image with the latest source.

**SSE chat completions hang / appear to buffer for 30s before any byte arrives.**
Disable response buffering on the reverse proxy for `/v1/chat/completions`:

* Nginx — `proxy_buffering off; proxy_cache off; chunked_transfer_encoding on;` in the location block.
* Caddy — `flush_interval -1` in `reverse_proxy`.

**`docker compose up gateway` fails with `address already in use :8000`.**
Another process owns port 8000. `sudo lsof -nP -i:8000` (or `Get-NetTCPConnection -LocalPort 8000` on Windows) → kill it or change `gateway.ports` in `infra/docker-compose.yml`.

**`alembic upgrade head` hangs forever.**
A long-running session is holding `AccessExclusiveLock`. Find it with:

```sql
SELECT pid, now()-pg_stat_activity.query_start AS age, query
FROM pg_stat_activity
WHERE state <> 'idle' AND query NOT LIKE '%pg_stat_activity%'
ORDER BY age DESC;
```

Then `SELECT pg_terminate_backend(<pid>);` if the session is unrelated.

## What's intentionally NOT in this skeleton

- **Yandex streaming** — V2.1 (NDJSON parser). MVP serves Yandex requests synthetically (single chunk + DONE).
- **Pre-flight cost cap** (`max_cost_kop`) — V2 feature. Token counting for Yandex / Sber happens post-hoc from `usage` in the response.
- **Hedge requests** for latency — V3.
- **SDK clients** — Python + JS wrappers (separate package).
- **Admin API** — `/admin/keys`, `/admin/team`, `/admin/billing/*` — see tech_stack.md §4.5.
- **Audit log, request_payloads encryption, retention cron** — schema is in place; consumer code TBD.
