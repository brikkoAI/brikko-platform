"""Failover engine tests.

Coverage:
* Retry within a single model on 5xx.
* Switch to fallback model on exhausted retries.
* `failover_used=True` is set when fallback is consumed.
* Auth errors (401/403) skip retries and jump to next model.
* Client errors (400/422) propagate immediately.
* Timeouts are normalised and retried.
* Events are recorded for every attempt — for usage_event audit.
* `failover_enabled=False` honours the opt-out.
"""

from __future__ import annotations

import pytest

from voltari_gateway.router.failover import (
    DEFAULT_BACKOFFS_MS,
    FailoverError,
    ProviderAuthError,
    ProviderClientError,
    ProviderRateLimitError,
    ProviderServerError,
    with_failover,
)
from voltari_gateway.router.tests.conftest import (
    CallRecorder,
    make_always_failing_call,
    make_call_that_fails_for_models,
    make_call_that_fails_then_succeeds,
    make_slow_call,
)

# Use sub-millisecond backoffs in tests so they finish in <50ms total.
TEST_BACKOFFS_MS = (1, 1, 1)


@pytest.mark.asyncio
class TestFailoverWithinModel:
    async def test_succeeds_on_first_attempt(self, synthetic_catalog) -> None:
        primary = synthetic_catalog[0]  # cheap-1
        recorder = CallRecorder()
        call = make_call_that_fails_then_succeeds(0, recorder=recorder)

        result = await with_failover(
            primary,
            [],
            call,
            backoffs_ms=TEST_BACKOFFS_MS,
        )

        assert result.response == "ok"
        assert result.model.id == "cheap-1"
        assert result.failover_used is False
        assert recorder.calls == ["cheap-1"]
        assert len(result.events) == 1
        assert result.events[0].succeeded is True

    async def test_retries_on_5xx(self, synthetic_catalog) -> None:
        primary = synthetic_catalog[0]
        recorder = CallRecorder()
        call = make_call_that_fails_then_succeeds(2, recorder=recorder)

        result = await with_failover(
            primary,
            [],
            call,
            backoffs_ms=TEST_BACKOFFS_MS,
        )

        # 2 fails on cheap-1 then a success on cheap-1 — 3 calls total.
        assert recorder.calls == ["cheap-1", "cheap-1", "cheap-1"]
        assert result.failover_used is False
        assert sum(1 for e in result.events if not e.succeeded) == 2
        assert sum(1 for e in result.events if e.succeeded) == 1

    async def test_exhausts_retries_then_raises_when_no_fallback(self, synthetic_catalog) -> None:
        primary = synthetic_catalog[0]
        recorder = CallRecorder()
        call = make_always_failing_call(recorder=recorder)

        with pytest.raises(FailoverError) as exc_info:
            await with_failover(
                primary,
                [],
                call,
                backoffs_ms=TEST_BACKOFFS_MS,
            )

        # 3 attempts on the only model.
        assert len(recorder.calls) == len(TEST_BACKOFFS_MS)
        assert isinstance(exc_info.value.last_error, ProviderServerError)
        assert all(not e.succeeded for e in exc_info.value.attempts)


@pytest.mark.asyncio
class TestFailoverAcrossModels:
    async def test_failover_uses_fallback_on_5xx(self, synthetic_catalog) -> None:
        primary = synthetic_catalog[0]  # cheap-1 (DeepSeek)
        fallback = [synthetic_catalog[1]]  # cheap-2-openai (OpenAI)
        recorder = CallRecorder()
        call = make_call_that_fails_for_models(
            failing_model_ids={"cheap-1"},
            recorder=recorder,
        )

        result = await with_failover(
            primary,
            fallback,
            call,
            backoffs_ms=TEST_BACKOFFS_MS,
        )

        # 3 attempts on cheap-1 (all fail), then 1 attempt on cheap-2 (success).
        assert recorder.calls == [
            "cheap-1",
            "cheap-1",
            "cheap-1",
            "cheap-2-openai",
        ]
        assert result.model.id == "cheap-2-openai"
        assert result.failover_used is True

    async def test_failover_logs_decision(self, synthetic_catalog) -> None:
        """Each attempt produces exactly one FailoverEvent — used for audit log."""
        primary = synthetic_catalog[0]
        fallback = [synthetic_catalog[1]]
        call = make_call_that_fails_for_models(failing_model_ids={"cheap-1"})

        result = await with_failover(primary, fallback, call, backoffs_ms=TEST_BACKOFFS_MS)

        # 3 failed events on primary + 1 success event on fallback = 4 total.
        assert len(result.events) == 4
        primary_events = [e for e in result.events if e.model_id == "cheap-1"]
        assert len(primary_events) == 3
        assert all(not e.succeeded for e in primary_events)
        assert all(e.error_type == "ProviderServerError" for e in primary_events)

        fallback_events = [e for e in result.events if e.model_id == "cheap-2-openai"]
        assert len(fallback_events) == 1
        assert fallback_events[0].succeeded is True
        assert fallback_events[0].latency_ms is not None

    async def test_all_providers_failed_raises_with_attempts(self, synthetic_catalog) -> None:
        primary = synthetic_catalog[0]
        fallback = [synthetic_catalog[1], synthetic_catalog[2]]
        call = make_always_failing_call()

        with pytest.raises(FailoverError) as exc_info:
            await with_failover(primary, fallback, call, backoffs_ms=TEST_BACKOFFS_MS)

        # 3 attempts × 3 models = 9 events.
        assert len(exc_info.value.attempts) == 9


@pytest.mark.asyncio
class TestFailoverErrorTypes:
    async def test_auth_error_skips_retries_jumps_to_next(self, synthetic_catalog) -> None:
        primary = synthetic_catalog[0]
        fallback = [synthetic_catalog[1]]
        recorder = CallRecorder()
        call = make_call_that_fails_for_models(
            failing_model_ids={"cheap-1"},
            error_factory=lambda: ProviderAuthError("compromised key", status_code=401),
            recorder=recorder,
        )

        result = await with_failover(primary, fallback, call, backoffs_ms=TEST_BACKOFFS_MS)

        # Auth error → ONE call to cheap-1 (no retries) + 1 success on fallback.
        assert recorder.calls == ["cheap-1", "cheap-2-openai"]
        assert result.failover_used is True

    async def test_client_error_propagates_immediately(self, synthetic_catalog) -> None:
        primary = synthetic_catalog[0]
        fallback = [synthetic_catalog[1]]
        recorder = CallRecorder()
        call = make_call_that_fails_for_models(
            failing_model_ids={"cheap-1"},
            error_factory=lambda: ProviderClientError(
                "bad request: messages[0] missing", status_code=400
            ),
            recorder=recorder,
        )

        with pytest.raises(ProviderClientError):
            await with_failover(primary, fallback, call, backoffs_ms=TEST_BACKOFFS_MS)

        # Client error → no failover, no retry, no fallback hit.
        assert recorder.calls == ["cheap-1"]

    async def test_rate_limit_is_retried(self, synthetic_catalog) -> None:
        primary = synthetic_catalog[0]
        recorder = CallRecorder()
        # Fail once with 429, then succeed.
        state = {"calls": 0}

        async def call(model):
            recorder(model)
            state["calls"] += 1
            if state["calls"] == 1:
                raise ProviderRateLimitError("slow down", retry_after_s=0.001)
            return "ok", 1

        result = await with_failover(primary, [], call, backoffs_ms=TEST_BACKOFFS_MS)
        assert result.response == "ok"
        assert recorder.calls == ["cheap-1", "cheap-1"]

    async def test_timeout_is_normalised_and_retried(self, synthetic_catalog) -> None:
        primary = synthetic_catalog[0]
        recorder = CallRecorder()
        call = make_slow_call(0.1, recorder=recorder)

        with pytest.raises(FailoverError) as exc_info:
            await with_failover(
                primary,
                [],
                call,
                backoffs_ms=TEST_BACKOFFS_MS,
                timeout_s=0.001,
            )

        # All 3 attempts timed out.
        assert len(recorder.calls) == 3
        assert all(e.error_type == "ProviderTimeoutError" for e in exc_info.value.attempts)


@pytest.mark.asyncio
class TestFailoverDisabled:
    async def test_failover_disabled_skips_fallback_chain(self, synthetic_catalog) -> None:
        primary = synthetic_catalog[0]
        fallback = [synthetic_catalog[1]]
        recorder = CallRecorder()
        call = make_always_failing_call(recorder=recorder)

        with pytest.raises(FailoverError):
            await with_failover(
                primary,
                fallback,
                call,
                backoffs_ms=TEST_BACKOFFS_MS,
                failover_enabled=False,
            )

        # Only primary attempted (3 retries), fallback never touched.
        assert all(c == "cheap-1" for c in recorder.calls)
        assert "cheap-2-openai" not in recorder.calls


@pytest.mark.asyncio
class TestEventShape:
    async def test_event_carries_attempt_index_and_backoff(self, synthetic_catalog) -> None:
        """Verifies the event payload that gets written into usage_events."""
        primary = synthetic_catalog[0]
        call = make_call_that_fails_then_succeeds(2)

        result = await with_failover(primary, [], call, backoffs_ms=TEST_BACKOFFS_MS)

        # First two events: failed, with retry-after recorded.
        assert result.events[0].attempt == 0
        assert result.events[0].retried_after_ms == TEST_BACKOFFS_MS[0]
        assert result.events[1].attempt == 1
        assert result.events[1].retried_after_ms == TEST_BACKOFFS_MS[1]
        # Final event: success.
        assert result.events[2].attempt == 2
        assert result.events[2].succeeded is True


def test_default_backoffs_are_sane() -> None:
    """Sanity check on the production backoff schedule.

    We don't want a regression to ship 200ms / 200ms / 200ms (= no real
    backoff) or 5min / 5min (= total deathloop on outages).
    """
    assert DEFAULT_BACKOFFS_MS == (200, 1_000, 5_000)
    # Total worst-case blocking time per model: 200 + 1000 + 5000 = 6.2s.
    assert sum(DEFAULT_BACKOFFS_MS) < 10_000
