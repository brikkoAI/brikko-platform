"""Tests for the ``read_usage`` MCP tool.

Cover surface:

* Empty usage → zeroed shape.
* 24h window includes rows from last 23h, excludes >24h-old.
* 7d window includes rows from last 6d.
* 30d window includes rows from last 29d.
* Cross-account isolation (no leak).
* top_models ordered by request count, capped at 5.
* total_kopecks sums correctly across models.
* Invalid period → 400.
* Default period = '24h'.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from voltari_gateway.db.models import UsageEvent
from voltari_gateway.mcp_server.tools.read_usage import handler
from voltari_gateway.utils.errors import GatewayError


async def _add_event(
    db,
    *,
    account_id: uuid.UUID,
    model: str,
    cost_kopecks: int,
    created_at: datetime,
    provider: str = "openai",
) -> None:
    db.add(
        UsageEvent(
            account_id=account_id,
            api_key_id=None,
            model=model,
            provider=provider,
            input_tokens=100,
            output_tokens=50,
            cached_tokens=0,
            cost_kopecks=cost_kopecks,
            request_id=f"req-{uuid.uuid4().hex[:12]}",
            created_at=created_at,
        )
    )
    await db.commit()


# ---------------------------------------------------------------------------
# Empty / happy paths
# ---------------------------------------------------------------------------


async def test_empty_usage_returns_zeroed_shape(db, mcp_token_fixture, bind_principal):
    bind_principal(mcp_token_fixture)
    result = await handler({"period": "24h"}, db)
    assert result == {
        "period": "24h",
        "total_requests": 0,
        "total_kopecks": 0,
        "total_rub": 0.0,
        "top_models": [],
    }


async def test_defaults_to_24h_when_period_omitted(db, mcp_token_fixture, bind_principal):
    bind_principal(mcp_token_fixture)
    result = await handler({}, db)
    assert result["period"] == "24h"


async def test_sums_kopecks_across_models(db, mcp_token_fixture, bind_principal):
    now = datetime.now(UTC)
    for model, cost in [("gpt-5-mini", 100), ("claude-haiku-4.5", 250), ("gpt-5-mini", 50)]:
        await _add_event(
            db,
            account_id=mcp_token_fixture.account.id,
            model=model,
            cost_kopecks=cost,
            created_at=now - timedelta(hours=1),
        )
    bind_principal(mcp_token_fixture)
    result = await handler({"period": "24h"}, db)
    assert result["total_requests"] == 3
    assert result["total_kopecks"] == 400
    assert result["total_rub"] == 4.0


async def test_top_models_ordered_by_request_count(db, mcp_token_fixture, bind_principal):
    now = datetime.now(UTC)
    # gpt-5-mini: 3 requests, claude-haiku-4.5: 1 request
    for _ in range(3):
        await _add_event(
            db,
            account_id=mcp_token_fixture.account.id,
            model="gpt-5-mini",
            cost_kopecks=10,
            created_at=now - timedelta(hours=1),
        )
    await _add_event(
        db,
        account_id=mcp_token_fixture.account.id,
        model="claude-haiku-4.5",
        cost_kopecks=50,
        created_at=now - timedelta(hours=1),
    )
    bind_principal(mcp_token_fixture)
    result = await handler({"period": "24h"}, db)
    assert result["top_models"][0]["model_id"] == "gpt-5-mini"
    assert result["top_models"][0]["requests"] == 3
    assert result["top_models"][1]["model_id"] == "claude-haiku-4.5"
    assert result["top_models"][1]["requests"] == 1


async def test_top_models_capped_at_5(db, mcp_token_fixture, bind_principal):
    now = datetime.now(UTC)
    for i in range(7):
        await _add_event(
            db,
            account_id=mcp_token_fixture.account.id,
            model=f"model-{i}",
            cost_kopecks=10,
            created_at=now - timedelta(hours=1),
        )
    bind_principal(mcp_token_fixture)
    result = await handler({"period": "24h"}, db)
    assert len(result["top_models"]) == 5


# ---------------------------------------------------------------------------
# Window boundary tests
# ---------------------------------------------------------------------------


async def test_24h_excludes_events_older_than_24h(db, mcp_token_fixture, bind_principal):
    now = datetime.now(UTC)
    await _add_event(
        db,
        account_id=mcp_token_fixture.account.id,
        model="gpt-5",
        cost_kopecks=100,
        created_at=now - timedelta(hours=23),  # inside
    )
    await _add_event(
        db,
        account_id=mcp_token_fixture.account.id,
        model="gpt-5",
        cost_kopecks=999,
        created_at=now - timedelta(hours=48),  # outside
    )
    bind_principal(mcp_token_fixture)
    result = await handler({"period": "24h"}, db)
    assert result["total_requests"] == 1
    assert result["total_kopecks"] == 100


async def test_7d_includes_recent_week(db, mcp_token_fixture, bind_principal):
    now = datetime.now(UTC)
    await _add_event(
        db,
        account_id=mcp_token_fixture.account.id,
        model="gpt-5",
        cost_kopecks=100,
        created_at=now - timedelta(days=6),  # inside
    )
    await _add_event(
        db,
        account_id=mcp_token_fixture.account.id,
        model="gpt-5",
        cost_kopecks=999,
        created_at=now - timedelta(days=8),  # outside
    )
    bind_principal(mcp_token_fixture)
    result = await handler({"period": "7d"}, db)
    assert result["total_requests"] == 1
    assert result["total_kopecks"] == 100


async def test_30d_includes_recent_month(db, mcp_token_fixture, bind_principal):
    now = datetime.now(UTC)
    await _add_event(
        db,
        account_id=mcp_token_fixture.account.id,
        model="gpt-5",
        cost_kopecks=100,
        created_at=now - timedelta(days=29),  # inside
    )
    await _add_event(
        db,
        account_id=mcp_token_fixture.account.id,
        model="gpt-5",
        cost_kopecks=999,
        created_at=now - timedelta(days=32),  # outside
    )
    bind_principal(mcp_token_fixture)
    result = await handler({"period": "30d"}, db)
    assert result["total_requests"] == 1
    assert result["total_kopecks"] == 100


# ---------------------------------------------------------------------------
# Cross-account isolation
# ---------------------------------------------------------------------------


async def test_other_account_events_excluded(db, mcp_token_fixture, seed_mcp_token, bind_principal):
    other = await seed_mcp_token()
    now = datetime.now(UTC)
    await _add_event(
        db,
        account_id=other.account.id,
        model="gpt-5",
        cost_kopecks=99_999,
        created_at=now - timedelta(hours=1),
    )
    await _add_event(
        db,
        account_id=mcp_token_fixture.account.id,
        model="gpt-5",
        cost_kopecks=100,
        created_at=now - timedelta(hours=1),
    )
    bind_principal(mcp_token_fixture)
    result = await handler({"period": "24h"}, db)
    assert result["total_requests"] == 1
    assert result["total_kopecks"] == 100


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------


async def test_invalid_period_raises_400(db, mcp_token_fixture, bind_principal):
    bind_principal(mcp_token_fixture)
    with pytest.raises(GatewayError) as exc:
        await handler({"period": "12h"}, db)
    assert exc.value.status_code == 400
    assert exc.value.error_code == "invalid_period"


async def test_unknown_keys_ignored_by_handler(db, mcp_token_fixture, bind_principal):
    """SDK validator catches these in production; handler stays tolerant."""
    bind_principal(mcp_token_fixture)
    result = await handler({"period": "24h", "unexpected": True}, db)
    assert result["period"] == "24h"
