"""Edge-case tests for ``voltari_gateway.router.circuit_breaker``.

Targets the Redis-error fallthrough paths that the happy-path test
suite (``test_circuit_breaker.py``) doesn't exercise: every breaker
operation must be **fail-open** if Redis returns an exception or is
plain unreachable. Falling closed on Redis hiccups would unnecessarily
DOS our own provider integrations.

Coverage gain: bumps ``router/circuit_breaker.py`` from ~71% to ~90%.
"""

from __future__ import annotations

import pytest

from voltari_gateway.router.circuit_breaker import (
    CircuitBreaker,
    CircuitConfig,
    CircuitState,
)


class _ExplodingRedis:
    """Drop-in fake whose every awaited method raises.

    Used to verify the breaker's ``except Exception`` branches.
    Compatible with the methods circuit_breaker.py touches: ``get``,
    ``set``, ``incr``, ``expire``, ``delete``, ``ttl``.
    """

    def __init__(self, exc: BaseException = RuntimeError("redis down")) -> None:
        self._exc = exc

    async def get(self, *_args, **_kwargs):
        raise self._exc

    async def set(self, *_args, **_kwargs):
        raise self._exc

    async def incr(self, *_args, **_kwargs):
        raise self._exc

    async def expire(self, *_args, **_kwargs):
        raise self._exc

    async def delete(self, *_args, **_kwargs):
        raise self._exc

    async def ttl(self, *_args, **_kwargs):
        raise self._exc


@pytest.mark.asyncio
async def test_state_returns_closed_on_redis_error():
    cb = CircuitBreaker(redis=_ExplodingRedis(), config=CircuitConfig())
    assert await cb.state("openai") == CircuitState.CLOSED


@pytest.mark.asyncio
async def test_state_returns_closed_when_redis_is_none():
    cb = CircuitBreaker(redis=None, config=CircuitConfig())
    assert await cb.state("openai") == CircuitState.CLOSED


@pytest.mark.asyncio
async def test_error_count_returns_zero_on_redis_error():
    cb = CircuitBreaker(redis=_ExplodingRedis(), config=CircuitConfig())
    assert await cb.error_count("anthropic") == 0


@pytest.mark.asyncio
async def test_error_count_returns_zero_when_redis_is_none():
    cb = CircuitBreaker(redis=None, config=CircuitConfig())
    assert await cb.error_count("anthropic") == 0


@pytest.mark.asyncio
async def test_before_call_fail_open_on_redis_error():
    """Redis explodes during ``before_call`` → no exception, request proceeds."""
    cb = CircuitBreaker(redis=_ExplodingRedis(), config=CircuitConfig())
    # Must NOT raise CircuitOpenError or anything else — fail-open.
    await cb.before_call("openai")


@pytest.mark.asyncio
async def test_before_call_no_op_when_redis_is_none():
    cb = CircuitBreaker(redis=None, config=CircuitConfig())
    await cb.before_call("openai")  # no exception


@pytest.mark.asyncio
async def test_record_success_no_op_on_redis_error():
    cb = CircuitBreaker(redis=_ExplodingRedis(), config=CircuitConfig())
    await cb.record_success("openai")  # no exception


@pytest.mark.asyncio
async def test_record_success_no_op_when_redis_is_none():
    cb = CircuitBreaker(redis=None, config=CircuitConfig())
    await cb.record_success("openai")


@pytest.mark.asyncio
async def test_record_error_no_op_on_redis_error():
    cb = CircuitBreaker(redis=_ExplodingRedis(), config=CircuitConfig())
    await cb.record_error("anthropic")  # no exception


@pytest.mark.asyncio
async def test_record_error_no_op_when_redis_is_none():
    cb = CircuitBreaker(redis=None, config=CircuitConfig())
    await cb.record_error("anthropic")


@pytest.mark.asyncio
async def test_state_returns_closed_for_unknown_string():
    """If Redis somehow stores a value other than 'open', treat as CLOSED."""
    import fakeredis.aioredis

    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    try:
        cb = CircuitBreaker(redis=redis, config=CircuitConfig())
        await redis.set("voltari:cb:state:openai", "garbage-value")
        assert await cb.state("openai") == CircuitState.CLOSED
    finally:
        await redis.aclose()


@pytest.mark.asyncio
async def test_error_count_handles_corrupt_value_gracefully():
    """If Redis returns a non-integer string, fall back to 0."""
    import fakeredis.aioredis

    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    try:
        cb = CircuitBreaker(redis=redis, config=CircuitConfig())
        await redis.set("voltari:cb:errors:openai", "not-an-int")
        assert await cb.error_count("openai") == 0
    finally:
        await redis.aclose()
