# E2E payment flow — happy-path test plan

**ФИЧА / СИСТЕМА:** end-to-end платёжный сценарий «новый клиент → деньги → запрос к LLM → закрывающие документы».

**ЦЕЛЬ ТЕСТИРОВАНИЯ:** релизный gate перед публичным запуском Brikko (ЮКасса вышла из модерации). Один зелёный прогон = первый платёж первого живого клиента сработает с вероятностью >99%. Тест-план дублируется автотестом `test_e2e_payment_flow.py` для шагов 1-7; шаги 8-13 — ручные/моки (нет sandbox-доступа в CI).

**ОКРУЖЕНИЕ:** in-memory SQLite + fakeredis + Stub-провайдер (gateway-side); ЮКасса webhook signature считается локально из `WEBHOOK_SECRET`; LLM-ответ канонизирован (StubProvider).

---

## ТЕСТ-КЕЙСЫ

### P0 (блокирующие — без них релиз не идёт)

#### TC-1. Регистрация (signup)
- **Шаги:** `POST /v1/auth/signup` с `{email, password}`.
- **Данные:** `email = e2e-<uuid>@test.local`, `password = "correct horse battery staple"`.
- **Expected:**
  - HTTP 200, тело: `{user_id, email, verification_required: true}`.
  - В БД: `users` row, `email_verified=False`, `verification_token` (sha256 hex 64 chars).
  - В БД: `accounts` row для owner_id=user.id, `tariff=PAYG`, `balance_kopecks=0`.
  - Email с verify-link ушёл (через monkeypatch `send_email`, captured).
- **Критерий успеха:** все 4 точки.

#### TC-2. Email verification + welcome credit
- **Шаги:** Сминтить токен через `generate_verification_token(user_id, email)`. `POST /v1/auth/verify-email` с `{token}`.
- **Expected:**
  - HTTP 200, тело: `{verified: true, welcome_credit_kop: 20000}`.
  - В БД: `users.email_verified=True`, `verification_token=None` (consumed).
  - В БД: `accounts.balance_kopecks=20_000` (200 ₽ welcome bonus).
  - В БД: `transactions` row с `type=topup`, `amount_kopecks=20_000`, `ref_id` начинается с `welcome:`.
  - В БД: `welcome_credits_log` row с email_hash.
- **Критерий успеха:** все 5 точек.

#### TC-3. Login → cookies + CSRF
- **Шаги:** `POST /v1/auth/login` с `{email, password}`. `GET /v1/auth/csrf` для CSRF-токена.
- **Expected:**
  - HTTP 200, тело: `{status: "authenticated", user_id, account_id, expires_at, csrf_token}`.
  - Set-Cookie: `vlt_access`, `vlt_refresh`, `vlt_csrf` (httpx сохраняет автоматически).
  - Каждая cookie с `HttpOnly` (кроме `vlt_csrf` — readable by SPA).
- **Критерий успеха:** auth-cookie set + csrf_token доступен.

#### TC-4. API-ключ создан с правильным префиксом
- **Шаги:** `POST /v1/keys` с `{name: "e2e-key", scope: "write"}` + cookies + `X-CSRF-Token`.
- **Expected:**
  - HTTP 201, тело: `{id, name, full_key, prefix, scope, created_at}`.
  - `full_key.startswith("sk-brk-")` — Brikko-литерал.
  - `prefix == full_key[:14]` — 14-char prefix.
  - В БД: `api_keys` row с `key_hash` (argon2), `key_prefix=full_key[:14]`, `status=ACTIVE`.
  - DB **не хранит** plaintext (`full_key` вернулся ровно один раз).
- **Критерий успеха:** все 5 точек + `GET /v1/keys` после этого НЕ возвращает `full_key`.

#### TC-5. Топап через ЮКасса webhook (signed)
- **Шаги:**
  1. Сформировать webhook payload: `{event: "payment.succeeded", object: {id: "pay-e2e-<uuid>", amount: {value: "1500.00"}, metadata: {account_id}}}`.
  2. Подписать `Content-HMAC: sha256=<hmac(WEBHOOK_SECRET, raw_body)>`.
  3. `POST /v1/billing/yookassa/webhook` с raw body + signed header.
- **Данные:** amount = 1500 ₽ (выше порога АКТа 1000 ₽), payment_id уникальный.
- **Expected:**
  - HTTP 200, `{status: "ok", transaction_id}`.
  - В БД: `accounts.balance_kopecks = 20_000 (welcome) + 150_000 (топап) = 170_000`.
  - В БД: `transactions` row с `type=topup`, `amount_kopecks=150_000`, `ref_id=pay-e2e-<uuid>`, `meta.source="yookassa"`.
  - В БД: `processed_webhooks` row, `status=processed`, `error_message=None`.
- **Критерий успеха:** все 4 точки.

#### TC-5b. Идемпотентность webhook
- **Шаги:** Тот же webhook payload + headers из TC-5 повторно.
- **Expected:**
  - HTTP 200, тело содержит `replay: true`.
  - В БД: `accounts.balance_kopecks` НЕ изменился (всё ещё 170_000).
  - В БД: ровно 1 row в `transactions` с этим `ref_id`.
- **Критерий успеха:** баланс не сдвинулся, дубль transactions не создан.

#### TC-6. Первый запрос к /v1/chat/completions с этим ключом
- **Шаги:** `POST /v1/chat/completions` с `Authorization: Bearer <full_key>`, `{model: "gpt-5.4-mini", messages: [{role: "user", content: "hi"}]}`.
- **Expected:**
  - HTTP 200, OpenAI-совместимый envelope: `{id, object: "chat.completion", choices: [{message: {role: "assistant", content: "..."}}], usage: {prompt_tokens, completion_tokens, total_tokens}}`.
  - StubProvider получил запрос (`last_request is not None`).
- **Критерий успеха:** ответ форматом совместим с OpenAI SDK.

#### TC-7. Списание с баланса + появился usage_event
- **Шаги:** После TC-6 — прочитать баланс через `GET /v1/billing/balance` и `usage_events` напрямую из БД.
- **Данные:** Stub возвращает usage `prompt_tokens=1000, completion_tokens=500` (как в test_chat_billing_integration).
- **Expected:**
  - `accounts.balance_kopecks = 170_000 - cost_kop`. Для gpt-5.4-mini @ 1000/500 tokens × markup 1.15 = 28 коп → новый баланс = 169_972.
  - В БД: `usage_events` row с `account_id`, `api_key_id`, `input_tokens=1000`, `output_tokens=500`, `cost_kopecks=28`.
  - В БД: `transactions` row типа `charge` с `amount_kopecks=-28`.
  - Active holds == 0 (settled).
- **Критерий успеха:** математика балансит до копейки.

---

### P1 (важные — должны быть, но обходятся manual-проверкой если упадут)

#### TC-8. Чек самозанятого (mock)
- **Шаги:** В TC-5 вместо отсутствующего `app.state.yookassa_receipts_http` — подсунуть respx-mock на `/v3/receipts?payment_id=...` который возвращает `{items: [{id: "rcpt-1", url: "https://yookassa.ru/receipts/..."}]}`.
- **Expected:**
  - В БД: `transactions.meta.receipt = {id, url, issuer: "yookassa"}`.
  - `GET /v1/billing/receipts/{transaction_id}` возвращает 200 с `receipt_url`.
- **Критерий успеха:** receipt доступен в API.
- **Вне scope автотеста:** реальный выпуск чека через ЮКасса-Самозанятые требует подключённый аккаунт самозанятого (см. ручной чек-лист).

#### TC-9. Скачивание акта (PDF) через Comply Pack
- **Шаги:** `GET /v1/billing/documents/{transaction_id}/akt` (с auth). Где `transaction_id` — id топапа из TC-5 (1500 ₽ — выше порога 1000 ₽).
- **Expected:**
  - HTTP 200.
  - `Content-Type: application/pdf`.
  - `Content-Disposition: attachment; filename="voltari-akt-*.pdf"`.
  - Тело: bytes начинаются с `%PDF-` (PDF magic).
- **Критерий успеха:** PDF cкачался + не пустой (>1 KB).

#### TC-10. Скачивание УПД за период
- **Шаги:** Ещё один топап на 9000 ₽ → суммарная активность периода ≥ 10_000 ₽. `GET /v1/billing/documents/upd?from=...&to=...`.
- **Expected:** HTTP 200, PDF.
- **Критерий успеха:** PDF cкачался.

#### TC-11. Refund-запрос → отмена через webhook
- **Шаги:**
  1. Сформировать `{event: "refund.succeeded", object: {id: "ref-e2e-<uuid>", amount: {value: "1500.00"}, metadata: {account_id}}}`.
  2. Подписать + отправить.
- **Expected:**
  - HTTP 200.
  - В БД: `accounts.balance_kopecks` стал на 150_000 меньше (170_000 - 28 - 150_000 = 19_972, т.е. остался welcome - cost первого запроса).
  - В БД: `transactions` row с `type=refund`, `amount_kopecks=-150_000`, `ref_id=ref-e2e-<uuid>`.
- **Критерий успеха:** баланс корректно списан.

#### TC-12. После refund — баланс < hold → 402 на повторном запросе
- **Шаги:** `POST /v1/chat/completions` с большим промптом (3000+ chars) и max_tokens=4096.
- **Данные:** Баланс ≈ 19_972 коп (199.72 ₽), estimated hold для 4096 max_tokens на gpt-5.4-mini >> 200 коп.
- **Wait — не тот сценарий.** Уточнение: 19_972 коп = 199.72 ₽, что **достаточно** для большинства запросов. Чтобы проверить «после refund баланс кончился», нужно ИЛИ:
  - (a) сразу после welcome (без TC-5) сделать TC-11 — но webhook рефанда не привязан к welcome → баланс уйдёт в минус и refund_account вернёт BillingError → 500.
  - (b) refund на сумму 1690 ₽ (welcome 200₽ + топап 1500₽ - cost 0.28₽ ≈ 1699.72₽). Запрос с большим промптом → 402.
- **Перерасчёт:** делаем refund на полную сумму welcome+топап (после first call cost). Затем — большой промпт.
- **Expected:**
  - HTTP 402, `{error: {type: "insufficient_quota", code: ...}}`.
  - StubProvider НЕ был вызван (`last_request` без новой записи).
  - `accounts.balance_kopecks` НЕ изменился.
- **Критерий успеха:** 402, провайдер не получил трафик.

---

### P2 (nice to have — не блокируют релиз, но снижают MTTR)

#### TC-13. Bad signature → 401, нет записей
- `POST /v1/billing/yookassa/webhook` с `Content-HMAC: sha256=DEADBEEF`.
- Expected: HTTP 401, никаких новых rows в `transactions`/`processed_webhooks`.

#### TC-14. Malformed JSON → 400
- `POST /v1/billing/yookassa/webhook` с `body=b"{not json"` + correct signature.
- Expected: HTTP 400, `{error: {code: "bad_webhook"}}`.

#### TC-15. Webhook canceled event — no-op
- `payment.canceled` event с правильной подписью.
- Expected: HTTP 200, balance не изменён, `processed_webhooks` row со status='processed'.

---

## EDGE CASES

| # | Сценарий | Ожидаемое поведение |
|---|---|---|
| E1 | Webhook доставлен ДО возврата клиента на success-page | Balance уже credited; success-page просто показывает «оплата прошла». OK. |
| E2 | Webhook доставлен 2× из-за retry ЮКассы | Idempotency через `processed_webhooks.payment_id` PK + `transactions.ref_id` UNIQUE. См. TC-5b. |
| E3 | Платёж succeeded, но receipt API ЮКассы вернул 500 | Credit committed, receipt — best-effort. `transactions.meta.receipt` отсутствует, но баланс корректен. Cron-job попробует снова. |
| E4 | Клиент отменил платёж после redirect | `payment.canceled` event → no balance change. См. TC-15. |
| E5 | Refund после полного использования баланса | `refund_account` уведёт balance в отрицательное. ЮКасса всё равно прислала refund — это операционная ошибка. Сейчас: уйдём в минус. **TODO:** добавить guard: если balance < refund_amount, fallback в DLQ + alert ops. |
| E6 | Huge prompt (1M tokens estimate) | Estimated hold >> tariff cap → 402 до похода в провайдер. Provider никогда не нагружается. |
| E7 | Provider timeout на /v1/chat/completions после успешного hold | Hold released, balance unchanged, no usage_event. См. test_chat_billing_integration::test_chat_does_not_debit_on_provider_error. |
| E8 | Network split: webhook принят, но мы не успели ack ЮКассе | ЮКасса retry → idempotent повторный credit (E2). |

---

## БЕЗОПАСНОСТЬ — чек-лист уязвимостей

| # | Атака | Защита |
|---|---|---|
| S1 | Подделка webhook (forged credit) | HMAC SHA256 over raw body c `webhook_secret`. Bad sig → 401, no DB writes. (TC-13) |
| S2 | Replay реального webhook злоумышленником (записал прод-traffic, повторил) | Idempotency через `processed_webhooks.payment_id` PK. Повтор → 200 replay, баланс не двигается. (TC-5b) |
| S3 | Кража API-ключа из логов | Plaintext возвращается **один раз** в POST /v1/keys. После — только argon2 hash в БД. `key_prefix` (14 chars) безопасен для логов (insufficient для bruteforce). |
| S4 | Перехват сессии (CSRF) | Double-submit: `X-CSRF-Token` header == `vlt_csrf` cookie. Mutating verbs (POST/PATCH/DELETE) на cookie-auth обязательно требуют пару. |
| S5 | Email enumeration через /signup | Сейчас возвращаем 409 (CEO-решение: UX > info-leak). Smoke-mitigation — rate limit на /signup (5/мин per IP). |
| S6 | Token replay (verify-email) | Token в БД хранится как HMAC-SHA256(plaintext, EMAIL_TOKEN_SECRET). Утечка БД без secret = бесполезна. |
| S7 | Refund-abuse: клиент переводит в минус | См. E5. **Открытая дыра — добавить guard в refund_account**. |
| S8 | Account_id в metadata подменён на чужой | Шаг проверки: `account_id` парсится → если такого нет в БД → 500 + DLQ. Атакующий не получает доступ к чужому аккаунту, потому что webhook сам по себе только пополняет (не аутентифицирует). |
| S9 | API-ключ revoked но кеш Redis-auth ещё держит 60s | DELETE /v1/keys/{id} → `invalidate_cache_for_key` синхронно чистит redis. Worst case при Redis-down: ≤60s grace. |
| S10 | Прямой POST /v1/chat/completions с украденным cookie (вместо bearer) | `/v1/chat/completions` принимает только Bearer. Cookie-auth работает на /v1/billing/*, /v1/keys/*. |

---

## КРИТЕРИЙ ГОТОВНОСТИ

Фича прошла QA когда:

1. **Автотест `test_e2e_payment_flow.py` зелёный** локально и в CI (шаги TC-1...TC-7, TC-11/E2 dependent).
2. **Ручной чек-лист** (см. ниже) пройден на staging со real ЮКасса test-mode shopId.
3. **Нет регрессий** в `tests/test_chat_billing_integration.py` и `tests/test_billing_webhook.py`.
4. **Уязвимости S1, S2, S6, S9** покрыты автотестами (S1, S2 — TC-13/TC-5b; S6 — `tests/auth/test_email_verify_security.py`; S9 — `tests/test_api_keys.py::test_revoke_invalidates_cache`).
5. **Открытая дыра S7 (refund в минус)** — задокументирована в TECH_DEBT.md, не блокирует MVP-релиз (риск: единичные ручные refund'ы из ЛК ЮКассы).

---

## РЕГРЕССИОННЫЙ ЧЕК-ЛИСТ ПЕРЕД КАЖДЫМ ДЕПЛОЕМ

Эти 7 ручных проверок автотесты НЕ покрывают (внешние сервисы, реальный шифр, реальная почта):

- [ ] **R1. Реальная подпись ЮКасса webhook.** В личном кабинете ЮКассы → Настройки → Уведомления → отправить тестовое уведомление. Должно прийти на `https://api.brikko.ru/v1/billing/yookassa/webhook` с валидным `Content-HMAC` (sha1 или sha256). В логах — `yookassa_webhook_replay_ok` или `transaction_id` в ответе.

- [ ] **R2. Test-mode платёж в sandbox ЮКассы.** Использовать тестовую карту `5555 5555 5555 4444` (любой CVC, future expiry). От `POST /v1/billing/topup` до redirect → возврат → webhook → balance обновлён на дашборде. Проверить что `confirmation_url` действительно открывается в браузере.

- [ ] **R3. Чек НПД ушёл клиенту.** После test-payment проверить что в email клиента (тот, что в `receipt_email`) пришло письмо от ЮКассы «Чек № …» с ссылкой на чек. URL должен открываться публично без auth (это требование ФНС). Если чек не пришёл — проверить статус подключения «ЮKassa Самозанятые» в ЛК.

- [ ] **R4. Real LLM-провайдер отвечает.** На staging с реальным `OPENAI_API_KEY` сделать `POST /v1/chat/completions` с `model=gpt-5.4-mini`, очень коротким промптом. Должен прийти осмысленный ответ. Проверить что `usage_events.cost_kopecks > 0`.

- [ ] **R5. CSRF на проде через настоящий браузер.** Открыть https://app.brikko.ru, залогиниться, в DevTools → Application → Cookies проверить наличие `vlt_access` (HttpOnly), `vlt_refresh` (HttpOnly), `vlt_csrf` (НЕ HttpOnly). Создать ключ через UI — должно сработать.

- [ ] **R6. Email-сервис жив.** Зарегать новый аккаунт на проде → проверить что verify-email пришёл в течение 60s в реальный inbox (не в спам). Проверить кодировку (Cyrillic в subject/body), валидную ссылку (открывается без 404).

- [ ] **R7. Postgres + Redis жив, миграции применены.** На стейджинг-боксе: `alembic current` → последняя ревизия. `psql -c "SELECT count(*) FROM accounts"` отвечает. `redis-cli ping` → PONG. Без этого автотесты-то на SQLite зелёные, а прод упадёт.
