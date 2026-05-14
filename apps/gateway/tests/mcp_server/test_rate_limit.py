"""Tests for the per-token MCP rate limiter.

Covers:

* First request → allowed, decrementing counter shape.
* 61st request inside one minute → denied.
* Different tokens have independent buckets.
* Redis absence → fail-open (always allow).
* Redis error → fail-open (always allow).
* reset_in_seconds is < 60 and > 0.
"""

from __future__ import annotations

import uuid

import pytest

from voltari_gateway.mcp_server.rate_limit import McpRateLimiter


@pytest.fixture
def token_id() -> uuid.UUID:
    return uuid.uuid4()


async def test_first_call_allowed(redis_client, token_id):
    limiter = McpRateLimiter(redis_client, limit_per_min=60)
    decision = await limiter.check(token_id)
    assert decision.allowed is True
    assert decision.limit_per_min == 60
    assert decision.remaining == 59


async def test_returns_correct_remaining_decrement(redis_client, token_id):
    limiter = McpRateLimiter(redis_client, limit_per_min=10)
    for i in range(5):
        decision = await limiter.check(token_id)
        assert decision.allowed is True
        assert decision.remaining == 10 - (i + 1)


async def test_denies_when_limit_exceeded(redis_client, token_id):
    limiter = McpRateLimiter(redis_client, limit_per_min=3)
    await limiter.check(token_id)
    await limiter.check(token_id)
    await limiter.check(token_id)
    decision = await limiter.check(token_id)
    assert decision.allowed is False
    assert decision.remaining == 0
    assert decision.reset_in_seconds > 0
    assert decision.reset_in_seconds <= 60


async def test_different_tokens_independent(redis_client):
    limiter = McpRateLimiter(redis_client, limit_per_min=2)
    a, b = uuid.uuid4(), uuid.uuid4()
    await limiter.check(a)
    await limiter.check(a)
    # a is at limit
    decision_a = await limiter.check(a)
    assert decision_a.allowed is False
    # b is unaffected
    decision_b = await limiter.check(b)
    assert decision_b.allowed is True


async def test_no_redis_means_always_allow(token_id):
    """``set_mcp_rate_limiter(None)`` use case + tests with no Redis."""
    limiter = McpRateLimiter(redis=None, limit_per_min=1)
    for _ in range(5):
        decision = await limiter.check(token_id)
        assert decision.allowed is True


async def test_redis_error_fails_open(redis_client, token_id, monkeypatch):
    """A broken pipeline call → fail-open, NOT a server error."""
    limiter = McpRateLimiter(redis_client, limit_per_min=60)

    class _BrokenPipeline:
        async def __aenter__(self):
            raise RuntimeError("simulated redis outage")

        async def __aexit__(self, *_args):
            return False

    monkeypatch.setattr(redis_client, "pipeline", lambda *_a, **_k: _BrokenPipeline())
    decision = await limiter.check(token_id)
    assert decision.allowed is True


async def test_minimum_limit_floored_to_1():
    """``limit_per_min=0`` would block everything — floor at 1."""
    limiter = McpRateLimiter(redis=None, limit_per_min=0)
    assert limiter._limit == 1
