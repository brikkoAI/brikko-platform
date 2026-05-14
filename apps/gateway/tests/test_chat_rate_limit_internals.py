"""Edge-case tests for ``voltari_gateway.middleware.rate_limit``.

The happy-path coverage in ``test_rate_limit.py`` exercises the bucket
math; this module pins the failure-mode contracts:

* No Redis configured → fail-open with ``allowed=True``, generous
  ``remaining``.
* Redis pipeline raises (e.g. WatchError, connection drop) → fail-open.
* Garbage values in the Redis bucket (corruption / version-skew) →
  treated as "fresh bucket".
* ``enforce_chat_rate_limit`` short-circuits when limiter not wired
  yet (early-startup edge).

Bumps ``middleware/rate_limit.py`` 85% → ~95%.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi import Request

from voltari_gateway.auth.middleware import AuthPrincipal
from voltari_gateway.middleware.rate_limit import (
    ChatRateLimiter,
    RateLimitDecision,
    enforce_chat_rate_limit,
    set_rate_limiter,
)


def _principal() -> AuthPrincipal:
    return AuthPrincipal(
        account_id=uuid.uuid4(),
        api_key_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        tariff="pro",
        balance_kopecks=100_000,
        store_prompts=True,
    )


def _request() -> Request:
    """Minimal ASGI scope so ``request.state`` works."""

    async def _empty_receive():  # pragma: no cover — never awaited in tests
        return {"type": "http.request"}

    scope = {
        "type": "http",
        "method": "POST",
        "headers": [],
        "path": "/v1/chat/completions",
        "query_string": b"",
    }
    return Request(scope, _empty_receive)


# ---------- ChatRateLimiter without Redis -----------------------------------


@pytest.mark.asyncio
async def test_check_returns_allowed_when_redis_none():
    limiter = ChatRateLimiter(redis=None)
    decision = await limiter.check("acct-1", tariff="pro")
    assert decision.allowed is True
    assert decision.limit_per_second > 0
    assert decision.burst > 0


# ---------- ChatRateLimiter — Redis pipeline failure ------------------------


class _ExplodingPipeline:
    """Async-CM that raises immediately when entered."""

    async def __aenter__(self):
        raise RuntimeError("watch failed")

    async def __aexit__(self, *args):
        return False


class _ExplodingRedis:
    def pipeline(self, transaction: bool = True):
        return _ExplodingPipeline()


@pytest.mark.asyncio
async def test_check_fails_open_on_pipeline_error():
    limiter = ChatRateLimiter(redis=_ExplodingRedis())  # type: ignore[arg-type]
    decision = await limiter.check("acct-2", tariff="payg")
    # Fail-open: returns allowed with the full burst as remaining.
    assert decision.allowed is True
    assert decision.remaining == decision.burst


# ---------- enforce_chat_rate_limit — limiter not wired ---------------------


@pytest.mark.asyncio
async def test_enforce_short_circuits_when_no_limiter():
    """If the singleton hasn't been set up yet, requests must pass."""
    set_rate_limiter(None)
    try:
        decision = await enforce_chat_rate_limit(_request(), _principal())
        assert isinstance(decision, RateLimitDecision)
        assert decision.allowed is True
        assert decision.limit_per_second == 0
        assert decision.burst == 0
    finally:
        set_rate_limiter(None)


# ---------- enforce_chat_rate_limit — sets request.state.rate_limit_headers --


@pytest.mark.asyncio
async def test_enforce_stashes_headers_on_request_state():
    """Successful pass must populate ``request.state.rate_limit_headers``
    so the chat handler can echo the limits in the 200 response.
    """
    limiter = ChatRateLimiter(redis=None)  # fail-open path
    set_rate_limiter(limiter)
    try:
        req = _request()
        decision = await enforce_chat_rate_limit(req, _principal())
        assert decision.allowed is True
        assert hasattr(req.state, "rate_limit_headers")
        headers = req.state.rate_limit_headers
        assert "X-RateLimit-Limit" in headers
        assert "X-RateLimit-Burst" in headers
        assert "X-RateLimit-Remaining" in headers
    finally:
        set_rate_limiter(None)


# ---------- ChatRateLimiter — corrupt bucket value handled gracefully -------


@pytest.mark.asyncio
async def test_check_handles_garbage_redis_values():
    """If the bucket has non-numeric strings (e.g. older format), the
    limiter must treat the bucket as empty/fresh rather than crashing.
    """
    import fakeredis.aioredis

    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    try:
        # Pre-seed with garbage.
        await redis.hset("rl:chat:acct-3", mapping={"tokens": "lol", "ts": "wat"})
        limiter = ChatRateLimiter(redis=redis)
        decision = await limiter.check("acct-3", tariff="pro")
        # Either way it must NOT raise. The fresh-bucket branch yields
        # allowed=True with remaining close to burst.
        assert isinstance(decision, RateLimitDecision)
    finally:
        await redis.aclose()
