"""Tests for ``voltari_gateway.router.circuit_breaker`` and its
integration with ``with_failover``.

We use ``fakeredis.aioredis.FakeRedis`` so the breaker exercises the
real Redis Lua/script paths without a network round-trip.

The tuning here uses small thresholds (``threshold=3``, ``window=2``,
``reset=1``) so the tests run fast — the production defaults are 10/60/30.
"""

from __future__ import annotations

import asyncio

import fakeredis.aioredis
import pytest

from voltari_gateway.router.catalog import ModelSpec, Provider
from voltari_gateway.router.circuit_breaker import (
    CircuitBreaker,
    CircuitConfig,
    CircuitOpenError,
    CircuitState,
)
from voltari_gateway.router.failover import (
    ProviderServerError,
    with_failover,
)

# ---- helpers ----------------------------------------------------------------


def _model(provider: Provider, model_id: str = "gpt-test") -> ModelSpec:
    """Build a minimal ModelSpec — the breaker only reads ``provider``."""
    from voltari_gateway.router.catalog import ModelTier

    return ModelSpec(
        id=model_id,
        provider=provider,
        tier=ModelTier.MID,
        input_price_kop_per_1k=10,
        cached_price_kop_per_1k=1,
        output_price_kop_per_1k=20,
        context_window=4096,
        latency_p50_ms=500,
        quality_score=70,
        supports_streaming=True,
        supports_tools=False,
        ru_legal=False,
    )


@pytest.fixture
async def redis_for_cb():
    client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    yield client
    await client.aclose()


@pytest.fixture
def cb_config() -> CircuitConfig:
    return CircuitConfig(threshold=3, window_seconds=2, reset_seconds=1)


@pytest.fixture
async def breaker(redis_for_cb, cb_config):
    return CircuitBreaker(redis_for_cb, config=cb_config)


# ---- direct breaker tests ---------------------------------------------------


@pytest.mark.asyncio
async def test_circuit_starts_closed(breaker: CircuitBreaker) -> None:
    assert await breaker.state("openai") == CircuitState.CLOSED
    # before_call must NOT raise for a closed breaker.
    await breaker.before_call("openai")


@pytest.mark.asyncio
async def test_circuit_opens_after_threshold(
    breaker: CircuitBreaker, cb_config: CircuitConfig
) -> None:
    """N=threshold errors flip the breaker to OPEN."""
    for _ in range(cb_config.threshold):
        await breaker.record_error("openai")

    assert await breaker.state("openai") == CircuitState.OPEN

    # The (threshold+1)-th call should fast-fail with CircuitOpenError.
    with pytest.raises(CircuitOpenError) as exc:
        await breaker.before_call("openai")
    assert exc.value.provider == "openai"
    assert exc.value.retry_after_s >= 1


@pytest.mark.asyncio
async def test_circuit_per_provider(breaker: CircuitBreaker, cb_config: CircuitConfig) -> None:
    """Opening OpenAI must NOT affect Anthropic."""
    for _ in range(cb_config.threshold):
        await breaker.record_error("openai")

    assert await breaker.state("openai") == CircuitState.OPEN
    assert await breaker.state("anthropic") == CircuitState.CLOSED
    # No raise for the unaffected provider.
    await breaker.before_call("anthropic")


@pytest.mark.asyncio
async def test_circuit_half_open_recovers_on_success(
    breaker: CircuitBreaker, cb_config: CircuitConfig
) -> None:
    """After RESET expires, a successful probe closes the breaker."""
    for _ in range(cb_config.threshold):
        await breaker.record_error("openai")
    assert await breaker.state("openai") == CircuitState.OPEN

    # Wait for OPEN to expire.
    await asyncio.sleep(cb_config.reset_seconds + 0.1)

    # First call after the OPEN expires: breaker is CLOSED-by-default
    # (the Redis state key has TTL'd out), so we expect no raise.
    await breaker.before_call("openai")

    # And a record_success keeps it closed + clears any leftover counter.
    await breaker.record_success("openai")
    assert await breaker.state("openai") == CircuitState.CLOSED


@pytest.mark.asyncio
async def test_circuit_half_open_keeps_open_on_failed_probe(
    breaker: CircuitBreaker, cb_config: CircuitConfig
) -> None:
    """A probe failure during HALF_OPEN reopens the breaker.

    Implementation detail: after the OPEN TTL expires, the next
    ``before_call`` lets through. If that probe records an error we
    increment again; once we re-cross the threshold the breaker opens
    again. (Threshold=3, so 3 more errors needed to reopen.)
    """
    cfg = cb_config
    for _ in range(cfg.threshold):
        await breaker.record_error("openai")
    await asyncio.sleep(cfg.reset_seconds + 0.1)

    # Probe call starts fine (CLOSED-by-default after TTL).
    await breaker.before_call("openai")

    # Probe failed → triple-record to push back over threshold.
    for _ in range(cfg.threshold):
        await breaker.record_error("openai")

    assert await breaker.state("openai") == CircuitState.OPEN


@pytest.mark.asyncio
async def test_circuit_record_success_resets_counter(
    breaker: CircuitBreaker, cb_config: CircuitConfig
) -> None:
    """A success between errors should reset the counter, not let two
    bursts of (threshold-1) errors compound into an open."""
    # Two errors below threshold...
    await breaker.record_error("openai")
    await breaker.record_error("openai")
    # ...success clears the counter...
    await breaker.record_success("openai")
    # ...so two more errors should NOT yet open the breaker (threshold=3).
    await breaker.record_error("openai")
    await breaker.record_error("openai")

    assert await breaker.state("openai") == CircuitState.CLOSED


@pytest.mark.asyncio
async def test_circuit_redis_failure_fails_open(cb_config: CircuitConfig) -> None:
    """Redis=None must NOT make the breaker raise on any call."""
    breaker = CircuitBreaker(redis=None, config=cb_config)

    await breaker.before_call("openai")  # CLOSED, no raise
    await breaker.record_error("openai")
    await breaker.record_error("openai")
    await breaker.record_error("openai")
    # Without Redis the breaker can't record state — stays CLOSED.
    await breaker.before_call("openai")
    assert await breaker.state("openai") == CircuitState.CLOSED


# ---- integration with with_failover -----------------------------------------


@pytest.mark.asyncio
async def test_failover_skips_open_provider(
    breaker: CircuitBreaker, cb_config: CircuitConfig
) -> None:
    """When OpenAI is OPEN, the failover walk should skip it without
    burning any retries — and immediately try the next provider.
    """
    # Open the OpenAI breaker.
    for _ in range(cb_config.threshold):
        await breaker.record_error("openai")
    assert await breaker.state("openai") == CircuitState.OPEN

    primary = _model(Provider.OPENAI, "gpt-test")
    fallback = _model(Provider.ANTHROPIC, "claude-test")
    calls: list[str] = []

    async def callable_(model: ModelSpec) -> tuple[str, int]:
        calls.append(str(model.provider))
        return ("ok", 1)

    result = await with_failover(
        primary,
        [fallback],
        callable_,
        backoffs_ms=(1, 1, 1),
        circuit_breaker=breaker,
    )
    assert result.failover_used is True
    assert calls == ["anthropic"]  # OpenAI never reached
    # Anthropic call was successful — record_success closed any stale state.
    assert await breaker.state("anthropic") == CircuitState.CLOSED


@pytest.mark.asyncio
async def test_failover_records_errors_into_breaker(
    breaker: CircuitBreaker, cb_config: CircuitConfig
) -> None:
    """A run of provider errors should accumulate in the breaker counter
    so a subsequent request opens it for the offending provider.
    """
    primary = _model(Provider.OPENAI)
    fallback = _model(Provider.ANTHROPIC)

    async def always_fail_openai_succeed_anthropic(model: ModelSpec):
        if str(model.provider) == "openai":
            raise ProviderServerError("openai 5xx")
        return ("ok", 1)

    # 3 attempts × backoffs to push over threshold=3.
    for _ in range(cb_config.threshold):
        await with_failover(
            primary,
            [fallback],
            always_fail_openai_succeed_anthropic,
            backoffs_ms=(1,),  # one attempt per model = one error per call
            circuit_breaker=breaker,
        )

    assert await breaker.state("openai") == CircuitState.OPEN
