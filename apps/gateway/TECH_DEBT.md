# Brikko Gateway — Technical Debt Register

Snapshot: **2026-04-29** (pre-launch polish, just before 1 May go-live).

Format: each item has a **priority** (`P0` blocks launch / data loss /
security; `P1` next sprint; `P2` quarter; `P3` nice-to-have), an
**owner** ("dev" = solo founder unless noted), an **estimate** in
person-days, and a **trigger** — the event that promotes the item up
the queue.

---

## P0 — none

All known launch blockers are closed at the time of writing.

## P1 — within 2 weeks of launch

### TD-1. Sber GigaChat reseller agreement
**Where:** `brikko_gateway/providers/sber_provider.py:3`
**Status:** TODO comment, code production-ready but blocked on contract.
**What:** Sber GigaChat business access requires a signed agreement
with Sber Cloud (CEO 29.04 — pending). Adapter is currently **for
staging/internal evaluation only** — production traffic must not be
routed to GigaChat through this gateway without CEO sign-off.
**Trigger to promote to P0:** any incident routing real client traffic
to Sber, or first paid Business+ customer requesting GigaChat.
**Owner:** CEO (legal) → dev (gating in router config).
**Estimate:** 0.5d dev work after legal closes.

### TD-2. Yandex Cloud reseller / billing entity
**Where:** `brikko_gateway/providers/yandex_provider.py:3`
**Status:** Same shape as TD-1 — Yandex Cloud B2B agreement pending.
**Trigger to promote:** first paid client requesting YandexGPT.
**Owner:** CEO → dev.
**Estimate:** 0.5d dev gating.

### TD-3. Welcome-credit dedup at PG-level uses generic INSERT
**Where:** `brikko_gateway/api/auth.py::_try_grant_welcome_credit`
**Status:** Works on both SQLite (test) and Postgres via the same
``IntegrityError`` path. Functional. Has test coverage on **real
Postgres** in `tests/integration/test_welcome_credit_postgres.py`.
**Improvement:** switch to dialect-aware
``insert(...).on_conflict_do_nothing()`` on Postgres. Saves one
``rollback()`` round-trip on the contended path. Not a correctness
issue.
**Trigger to promote:** signup throughput >5/s (we are nowhere near it
in M1-M3) OR welcome-credit anomaly in audit log.
**Estimate:** 0.5d.

## P2 — within first 90 days

### TD-4. Microkopeck precision in usage_events
**Where:** `brikko_gateway/billing/__init__.py` (math), DB schema for
`usage_events.amount_kopecks`.
**Status:** All monetary fields are `BigInteger` kopecks, rounded with
`math.ceil`. For a single GPT-5-mini call at <100 tokens this can over-
charge by up to **1 kop** (~3-4% on the cheapest calls). Acceptable
for MVP — at 2026-04 Russian-bank precision is also ₽-cent (kopeck).
**Improvement:** introduce a `_microkopecks` column (1e-6 ₽), do all
math in microkopecks, then round-down per **billing period** instead
of per-call. Eliminates the "many small calls" overcharge.
**Trigger to promote:** customer complaint about overcharge OR
finance reconciliation diff >0.5%.
**Estimate:** 1.5d (schema migration + math refactor + tests).
**Note:** holds back V2 prompt-cache pricing too — Anthropic invoices
cached tokens at 0.1× base, math gets noisy without microkopecks.

### TD-5. Provider health checks are configured-only
**Where:** `brikko_gateway/api/health.py::_check_providers`
**Status:** `/health/ready` reports each provider as `"ok"` if its
adapter is built. We deliberately do **not** ping upstream on every
health probe (UptimeRobot would burn ~$50/mo on real Anthropic calls).
**Improvement:** background "synthetic-call" task once / 60s, cache
last result in Redis with TTL=120s, surface in `/health/ready`.
**Trigger to promote:** first incident where an upstream was silently
down for >5min before we noticed.
**Estimate:** 1d.

### TD-6. Redis is single-node
**Where:** `brikko_gateway/auth/middleware.py`, `db/session.py`.
**Status:** One Redis instance, no replication. If it dies, auth
sessions vanish (forced re-login) and rate-limit counters reset.
**Improvement:** Redis Sentinel (managed via Yandex Cloud Managed
Redis) when MRR >100k ₽/mo justifies the +600 ₽/mo expense.
**Trigger to promote:** first auth-cache outage in prod.
**Estimate:** 0.5d for Sentinel client config + chaos test.

## P3 — backlog

### TD-7. ON CONFLICT migration to dialect-aware inserts (broader)
Beyond welcome-credit (TD-3), the same pattern — try INSERT then
catch `IntegrityError` — appears in `email_invites`, `api_keys.last_used`
upsert, and `usage_events` idempotency. Each is independently safe
today; consolidating them under a small `upsert(...)` helper would cut
~50 LOC.
**Estimate:** 1d.

### TD-8. OpenTelemetry tracing
We log structured JSON via `structlog` but do not export OTLP traces.
At scale, debugging a slow router→failover→retry chain over Yandex
Cloud Logging alone is painful.
**Trigger:** P95 latency complaints OR >50 RPS sustained traffic.
**Estimate:** 1.5d (otel-sdk + per-provider span + Yandex Cloud
Tracing exporter).

### TD-9. Provider catalog hot-reload
`router/catalog.py` is loaded at process start. Adding a model = redeploy.
**Improvement:** a periodic refresh from a `models.yaml` in S3 (or
`packages/catalog/`), with checksum-based reload. Allows pricing
updates without `docker compose down`.
**Trigger:** first time we change a price >2× / month.
**Estimate:** 1d.

---

## Closed in this iteration (2026-04-29)

* `brikko_gateway/providers/catalog.py` — backward-compat shim, no
  callers, deleted.
* `tests/test_google_provider.py` — fixed to work with
  `google-genai>=1.0` (`Client.aio` became read-only; `APIError` ctor
  signature changed).
