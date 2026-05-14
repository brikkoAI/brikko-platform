"""GET /v1/usage tests."""

from __future__ import annotations

import uuid

import pytest

from voltari_gateway.db.models import UsageEvent


@pytest.mark.asyncio
async def test_usage_empty_period(client, api_key_fixture):
    r = await client.get(
        "/v1/usage?from=2026-04-01&to=2026-04-30",
        headers=api_key_fixture.auth_header,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["object"] == "list"
    assert body["totals"] == {
        "request_count": 0,
        "tokens_in": 0,
        "tokens_out": 0,
        "cached_tokens": 0,
        "cost_kop": 0,
    }


@pytest.mark.asyncio
async def test_usage_with_existing_events(client, db, api_key_fixture):
    for _ in range(3):
        db.add(
            UsageEvent(
                account_id=api_key_fixture.account.id,
                api_key_id=api_key_fixture.api_key.id,
                model="gpt-5.4-mini",
                provider="openai",
                input_tokens=100,
                output_tokens=50,
                cached_tokens=0,
                cost_kopecks=15,
                request_id=uuid.uuid4().hex,
            )
        )
    await db.commit()

    # `to` defaults to "now" when omitted, which is what we want here.
    r = await client.get(
        "/v1/usage?from=2026-01-01",
        headers=api_key_fixture.auth_header,
    )
    assert r.status_code == 200
    totals = r.json()["totals"]
    assert totals["request_count"] == 3
    # Field names match the public API contract — same as `x_gateway.cost_kop`
    # in /v1/chat/completions responses. Frontend types in apps/web/src/lib/types.ts
    # (UsageTotals) consume these names directly.
    assert totals["tokens_in"] == 300
    assert totals["tokens_out"] == 150
    assert totals["cost_kop"] == 45
