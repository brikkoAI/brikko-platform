"""Sprint 12.6 — GET /v1/public/status tests.

Public live-metrics for the marketing widget. Anonymous, Redis-cached
30 s, rate-limited per-IP, never 5xx. Tests cover the four contract
guarantees:

1. Happy path — 50+ rows in usage_events → real successful_requests_24h.
2. Empty / sparse data → static fallbacks but a real count.
3. Postgres timeout → degraded:true response (no 500).
4. Cache hit — second request never touches Postgres.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest

from voltari_gateway.api import public_status as public_status_module
from voltari_gateway.db.models import (
    Account,
    AccountStatus,
    ApiKey,
    ApiKeyStatus,
    Tariff,
    UsageEvent,
    User,
)

# --- helpers -----------------------------------------------------------------


async def _seed_account(db) -> tuple[uuid.UUID, uuid.UUID]:
    """Insert a minimal user/account/key triple and return their IDs.

    UsageEvent rows need real foreign keys to ``accounts`` and (nullable)
    ``api_keys``, so we seed once per test that needs to insert events.
    """
    user = User(
        email=f"u-{uuid.uuid4().hex[:8]}@test.local",
        password_hash="$argon2id$v=19$m=65536,t=2,p=2$dummy",
        email_verified=True,
    )
    db.add(user)
    await db.flush()

    account = Account(
        owner_id=user.id,
        name="Status test account",
        balance_kopecks=100_000,
        tariff=Tariff.PRO,
        status=AccountStatus.ACTIVE,
        store_prompts=False,
    )
    db.add(account)
    await db.flush()

    api_key = ApiKey(
        account_id=account.id,
        name="default",
        key_hash="dummy-hash",
        key_prefix="sk-test-",
        status=ApiKeyStatus.ACTIVE,
    )
    db.add(api_key)
    await db.commit()
    return account.id, api_key.id


async def _seed_usage_events(
    db, *, account_id: uuid.UUID, api_key_id: uuid.UUID, count: int
) -> None:
    now = datetime.now(UTC)
    for i in range(count):
        ev = UsageEvent(
            account_id=account_id,
            api_key_id=api_key_id,
            model="gpt-5.4-mini",
            provider="openai",
            modality="chat",
            unit="token",
            input_tokens=10,
            output_tokens=20,
            cached_tokens=0,
            cost_kopecks=5,
            request_id=f"req-{i}",
        )
        # Spread across the 24 h window so the cutoff math matters.
        ev.created_at = now - timedelta(minutes=i)
        db.add(ev)
    await db.commit()


# --- tests -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_happy_path_returns_real_count(client, db, redis_client):
    """50+ rows in window → endpoint returns the real COUNT and 200 shape."""
    account_id, api_key_id = await _seed_account(db)
    await _seed_usage_events(db, account_id=account_id, api_key_id=api_key_id, count=60)

    resp = await client.get("/v1/public/status")
    assert resp.status_code == 200, resp.text

    body = resp.json()
    # Contract shape — every key the widget reads must be present.
    assert set(body.keys()) >= {
        "p95_latency_ms",
        "uptime_percent_24h",
        "successful_requests_24h",
        "window",
        "updated_at",
    }
    assert body["window"] == "24h"
    assert isinstance(body["p95_latency_ms"], int)
    assert isinstance(body["uptime_percent_24h"], (int, float))
    assert body["successful_requests_24h"] == 60
    # Fresh response — no degraded flag in the success envelope.
    assert body.get("degraded") is not True
    assert resp.headers.get("X-Cache") == "miss"


@pytest.mark.asyncio
async def test_empty_data_uses_fallback_for_p95_and_uptime(client, db, redis_client):
    """<50 rows → real count, but p95/uptime fall back to SLA baseline.

    The count itself is honest (zero traffic is zero traffic); the
    derived percentile/uptime numbers need a sample floor to be
    meaningful.
    """
    resp = await client.get("/v1/public/status")
    assert resp.status_code == 200

    body = resp.json()
    assert body["successful_requests_24h"] == 0
    assert body["p95_latency_ms"] == public_status_module.FALLBACK_P95_LATENCY_MS
    assert body["uptime_percent_24h"] == public_status_module.FALLBACK_UPTIME_PCT


@pytest.mark.asyncio
async def test_db_timeout_returns_degraded_payload(client, redis_client):
    """Postgres unavailable → 200 + degraded:true. The widget must not break."""

    async def _raise_timeout() -> int:
        raise TimeoutError("simulated postgres timeout")

    with patch.object(
        public_status_module,
        "_count_successful_24h",
        side_effect=_raise_timeout,
    ):
        resp = await client.get("/v1/public/status")

    assert resp.status_code == 200
    body = resp.json()
    assert body["degraded"] is True
    assert body["successful_requests_24h"] is None
    assert body["p95_latency_ms"] == public_status_module.FALLBACK_P95_LATENCY_MS
    assert body["uptime_percent_24h"] == public_status_module.FALLBACK_UPTIME_PCT
    # Degraded responses are NOT cached — header must indicate that.
    assert resp.headers.get("X-Cache") == "miss-degraded"


@pytest.mark.asyncio
async def test_cache_hit_skips_db(client, db, redis_client):
    """First request hits DB; second request returns the cached payload.

    We assert by mutating the cache between calls — if the second
    response equals the mutated cache value we know it didn't recompute.
    """
    account_id, api_key_id = await _seed_account(db)
    await _seed_usage_events(db, account_id=account_id, api_key_id=api_key_id, count=10)

    # Warm the cache.
    first = await client.get("/v1/public/status")
    assert first.status_code == 200
    assert first.headers.get("X-Cache") == "miss"

    # Mutate the cached payload — if the endpoint reads cache it returns this.
    sentinel_payload = {
        "p95_latency_ms": 999,
        "uptime_percent_24h": 12.34,
        "successful_requests_24h": 99999,
        "window": "24h",
        "updated_at": "2099-01-01T00:00:00Z",
    }
    await redis_client.setex(
        public_status_module.CACHE_KEY,
        public_status_module.CACHE_TTL_SECONDS,
        json.dumps(sentinel_payload),
    )

    second = await client.get("/v1/public/status")
    assert second.status_code == 200
    assert second.headers.get("X-Cache") == "hit"
    assert second.json() == sentinel_payload
