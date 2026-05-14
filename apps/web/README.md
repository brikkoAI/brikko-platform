# @brikko/web

Next.js 14 (App Router) — лендинг, auth-страницы, дашборд клиента.

## Стек
- Next.js 14.2 + React 18.3 + TypeScript 5.6 (strict, `noUncheckedIndexedAccess`)
- Tailwind 3.4 + design tokens из `02_Product/09_design_system.md`
- TanStack Query 5 (серверное состояние) + Zustand 4 (UI-state)
- react-hook-form 7 + Zod 3 (валидация форм)
- Radix UI primitives + sonner (toasts)
- ky (HTTP-клиент) + MSW (моки)
- Vitest + Playwright

## Запуск

```bash
pnpm install                  # postinstall сгенерирует public/mockServiceWorker.js
cp .env.example .env.local
pnpm dev                      # http://localhost:3000 — MSW активен по умолчанию
pnpm typecheck
pnpm test                     # vitest unit + integration через MSW server
pnpm test:e2e                 # Playwright поверх MSW worker
```

Если первый `pnpm install` не сгенерировал worker — выполни вручную:

```bash
pnpm msw:init                 # → public/mockServiceWorker.js
```

## Переключение MSW ↔ real backend

Логика в `src/lib/api.ts` → `resolveBaseUrl()`:

| `NEXT_PUBLIC_API_BASE_URL`           | Поведение                                         |
| ------------------------------------ | ------------------------------------------------- |
| не задан / пусто                     | MSW в браузере (`/api/mock/v1`)                   |
| `http://localhost:3000/v1`           | MSW (защита от ошибки — это сам Next, не backend) |
| `http://localhost:8000/v1`           | реальный локальный backend                        |
| `https://api.brikko.ru/v1`          | production                                       |

В production-сборке (`pnpm build && pnpm start`) MSW выключен жёстко (см. `mocks/index.ts`)
— `import('@/mocks/browser')` стоит за `if (process.env.NODE_ENV === 'development')`,
поэтому Next tree-shake'ает зависимость `msw` из production-bundle.

## Full-stack local development

Когда нужно проверить frontend против реального gateway (e2e, smoke, debug интеграции):

### 1. Запустить gateway на :8000

```bash
cd apps/gateway
docker compose up -d postgres redis             # infra
uv sync                                         # python deps
alembic upgrade head                            # migrations
uvicorn brikko_gateway.main:app --reload --port 8000
```

Email-клиент в dev-режиме пишет письма в stdout (см. `apps/gateway/.env`:
`SMTP_BACKEND=console`). Verify-email link появится прямо в логах uvicorn.

### 2. Запустить web против real backend

```bash
cd apps/web
cp .env.example .env.local
# раскомментировать в .env.local:
#   NEXT_PUBLIC_API_BASE_URL=http://localhost:8000/v1
#   NEXT_PUBLIC_AUTH_MIDDLEWARE_DISABLED=  # оставить пустым → middleware включается
pnpm install
pnpm dev
```

### 3. Проверить flow

1. `http://localhost:3000/signup` → создать аккаунт.
2. В логах gateway найти строку `Verification link: http://localhost:3000/verify-email?token=...`
3. Перейти по ссылке → автологин, welcome 200 ₽ начислен.
4. `/app` показывает баланс и пустой список ключей.
5. Создать ключ → попробовать `curl http://localhost:8000/v1/chat/completions -H "Authorization: Bearer sk-vt-..."` → 200.
6. Revoke ключа → тот же curl → 401.

CORS: gateway по умолчанию пускает `http://localhost:3000` (см. `config.py:cors_origins_list`).
Если стоит на другом порту — обнови `CORS_ORIGINS` в `.env` gateway'а.

## Переключение middleware

`src/middleware.ts` проверяет cookie `vlt_access` и редиректит:
- `/app/*` без cookie → `/login?reason=session_expired&next=...`
- `/login` или `/signup` с cookie → `/app`

В MSW-режиме реального cookie нет, поэтому в dev `NEXT_PUBLIC_AUTH_MIDDLEWARE_DISABLED=true`
выключает middleware. В full-stack-режиме — снять флаг.

## Что готово

- **Лендинг** `/`, `/pricing`, `/faq` (Hero, Features, Comparison, Pricing, FAQ, Footer).
- **Auth flow**: `/signup`, `/signup/verify-email`, `/login`, `/forgot-password`, `/reset-password`.
  - signup → verify по token из URL → редирект на `/app` + welcome 200 ₽.
  - 401 redirect автоматический в `lib/api.ts` (на `/login?reason=session_expired`).
- **Дашборд** `/app/*`:
  - `/app` — overview: баланс, расход за 7 дней, активные ключи, curl-сниппет, последние транзакции.
  - `/app/keys` — CRUD: список, создание (modal с full_key показом), отзыв (typing-confirmation).
  - `/app/billing` — баланс, форма пополнения через ЮKassa, история транзакций с чеками.
  - `/app/usage` — графики по 7/30/90 дням + drill-down по моделям.
  - `/app/team` — seats list, pending invites, отправка приглашений (PAYG → upgrade-CTA).
  - `/app/settings` — профиль, prompt-logging toggle, logout.
- **Дизайн-система** (`components/ui/`): button, input, card, table, dialog, banner, badge,
  empty-state, form fields, skeleton, toast.
- **MSW** (`src/mocks/`):
  - 30+ handlers под все группы контракта 29.04 (auth/account/keys/billing/usage + debug).
  - Реалистичные фикстуры: 1 пользователь, баланс 1000 ₽, 2 ключа, 30 дней usage по 6 моделям,
    3 транзакции, 1 pending invite.
  - В тестах — `setupServer`; в браузере — service worker.
- **Тесты**:
  - `tests/api-client.test.ts` — интеграция MSW ↔ ApiClient (signup/keys/billing happy + error path).
  - `tests/utils.test.ts` — formatKopecks/formatRub/formatTokens/maskApiKey/formatRelative.
  - `tests/signup-schema.test.ts` — Zod схема signup-формы.
  - `e2e/signup.spec.ts` — happy path до /app + welcome 200 ₽ (через MSW).
  - `e2e/keys.spec.ts` — создание ключа с copy-and-confirm + revoke с typing-confirmation.
  - `e2e/billing.spec.ts` — topup flow через mock ЮKassa.
  - `e2e/integration.spec.ts` — **only when `E2E_REAL_BACKEND=true`**, против live gateway на :8000.

Запуск integration-тестов:

```bash
# 1. Подними gateway (см. «Full-stack local development»)
# 2. .env.local: NEXT_PUBLIC_API_BASE_URL=http://localhost:8000/v1
# 3.
pnpm test:e2e:integration
```

## Архитектурные решения

- **Branded type `Kopecks`** для денег вместо float'ов — исключает ошибки округления подписочного биллинга.
- **snake_case** в DTO сохранён 1:1 с API — никакого mapping-слоя.
  Это упрощает копи-пейст из Postman и переезд на openapi-typescript.
- **Modal vs Drawer** для CRUD ключей — modal: контекст создания короткий, юзеру не нужен back-context.
- **Typing-confirmation** при revoke — паттерн GitHub/Stripe; одного клика недостаточно для деструктива.
- **Кастомный SVG bar-chart** вместо Recharts — экономия 40-100KB на странице с одним графиком.
  Когда добавим drill-down с zoom — переедем на Echarts (V2).
- **MSW по умолчанию в dev** — фронт развивается параллельно с backend, без блокеров.
