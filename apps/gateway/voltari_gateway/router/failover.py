"""Failover engine for the smart router.

`with_failover` orchestrates retries inside one provider and switching
between providers when a chain of fallback models is exhausted. The
*caller* supplies the actual provider call as an async callable that
takes a `ModelSpec` and returns `(response, latency_ms)`. We intentionally
do NOT depend on httpx/openai SDK in this module — that keeps the failover
unit-testable with plain mocks (no `respx`-required) and avoids leaking
provider-specific details.

Failover triggers (per task brief and `02_Product/04_tech_stack.md` §5.2):

  * HTTP 5xx                       → retry within model, then next
  * Timeout > 30s                  → retry within model, then next
  * 429 Rate Limit                 → retry within model, then next
  * 401 / 403                      → skip model entirely (compromised key)
  * 4xx other (400, 422, 404)      → do NOT retry, do NOT failover —
                                     it's the client's fault, propagate.

Backoff: 200ms / 1s / 5s — deliberately exponential-ish but capped to
keep p95 latency tolerable. We do not retry the LAST attempt's error;
on attempt 3 we either succeed or move on.

The function emits a `FailoverEvent` for each model transition (logged
upstream so it lands in `usage_events` with `failover_used=true` per the
DB schema in tech_stack.md §3).
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TypeVar

import structlog

from voltari_gateway.router.catalog import ModelSpec
from voltari_gateway.router.circuit_breaker import (
    CircuitBreaker,
    CircuitOpenError,
)

log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Errors. We model each retryable category explicitly so the call-site
# can raise them with full context (status code, retry-after header).
# ---------------------------------------------------------------------------


class ProviderError(Exception):
    """Base for upstream provider errors. Subclasses encode retry policy."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class ProviderTimeoutError(ProviderError):
    """Provider did not respond within the configured timeout."""


class ProviderRateLimitError(ProviderError):
    """Provider returned 429. Optionally carries `retry_after_s`."""

    def __init__(
        self,
        message: str,
        *,
        retry_after_s: float | None = None,
        status_code: int | None = 429,
    ) -> None:
        super().__init__(message, status_code=status_code)
        self.retry_after_s = retry_after_s


class ProviderServerError(ProviderError):
    """5xx from the provider — retryable, then failover."""


class ProviderAuthError(ProviderError):
    """401/403 from the provider — DO NOT retry. Switch model immediately.

    The provider key for this model may be revoked or rate-banned at
    account level; retrying with the same key will keep failing.
    """


class ProviderClientError(ProviderError):
    """4xx other than 401/403/429 — caller's fault, propagate to user.

    These errors short-circuit failover: if the user sent a malformed
    request, switching providers won't help and could mask real bugs.
    """


class FailoverError(Exception):
    """All models in the chain were attempted and all failed.

    `last_error` carries the final upstream error so the API layer can
    map it to a sensible HTTP response (typically 502 / 503 / 504).
    """

    def __init__(
        self,
        message: str,
        *,
        attempts: list[FailoverEvent],
        last_error: BaseException | None = None,
    ) -> None:
        super().__init__(message)
        self.attempts = attempts
        self.last_error = last_error


# ---------------------------------------------------------------------------
# Events — one per attempted model. Caller persists these to usage_events.
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class FailoverEvent:
    """Records a single model attempt — successful or not."""

    model_id: str
    provider: str
    attempt: int  # 0-based attempt index within this model
    succeeded: bool
    error_type: str | None = None
    error_message: str | None = None
    latency_ms: int | None = None
    retried_after_ms: int | None = None  # backoff actually applied


T = TypeVar("T")
ProviderCallable = Callable[[ModelSpec], Awaitable[tuple[T, int]]]
"""Async callable that issues the upstream request.

Returns `(response, latency_ms)`. Must raise one of the `Provider*Error`
classes above for failures. Anything else escapes failover unchanged
(programmer error, OOM, etc.).
"""


# ---------------------------------------------------------------------------
# Backoff schedule. Tuple is exhaustive — len() = max attempts per model.
# Tuned to keep total worst-case wait ≤ 6.2s before falling over to next
# provider, leaving headroom under the 30s upstream timeout.
# ---------------------------------------------------------------------------

DEFAULT_BACKOFFS_MS: tuple[int, ...] = (200, 1_000, 5_000)
DEFAULT_TIMEOUT_S: float = 30.0


@dataclass(slots=True)
class FailoverResult[T]:
    """Successful outcome of `with_failover`."""

    response: T
    model: ModelSpec
    failover_used: bool
    events: list[FailoverEvent] = field(default_factory=list)


async def with_failover[T](
    primary: ModelSpec,
    fallback_chain: list[ModelSpec],
    call: ProviderCallable[T],
    *,
    backoffs_ms: tuple[int, ...] = DEFAULT_BACKOFFS_MS,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    failover_enabled: bool = True,
    request_id: str | None = None,
    circuit_breaker: CircuitBreaker | None = None,
) -> FailoverResult[T]:
    """Execute `call(primary)`; on failure, retry, then walk the fallback chain.

    Parameters
    ----------
    primary
        The first-choice model.
    fallback_chain
        Models to try if `primary` exhausts its retries. Order matters
        — the router places cross-provider models first to maximise
        diversity. May be empty (no failover possible).
    call
        Async callable that takes a `ModelSpec` and performs the
        provider request. Must raise one of `Provider*Error` on failure.
    backoffs_ms
        Backoff between retries within the same model. The number of
        attempts per model equals `len(backoffs_ms)`.
    timeout_s
        Hard ceiling on a single `call(model)` invocation. If exceeded
        the call is cancelled and treated as `ProviderTimeoutError`.
    failover_enabled
        If False, only the primary is tried (no fallback walk). Useful
        for clients who explicitly opt out via `router: {failover: false}`.
    request_id
        Correlation id, propagated to logs only.

    Returns
    -------
    FailoverResult containing the successful response, the chosen model,
    and `failover_used=True` iff we did NOT use `primary` for the
    successful response.

    Raises
    ------
    FailoverError
        All models exhausted. Use `.last_error` to map to HTTP status.
    ProviderClientError
        Re-raised immediately on 4xx (other than auth/rate-limit).
        Failover doesn't help when the input is malformed.
    """
    chain: list[ModelSpec] = [primary]
    if failover_enabled:
        chain.extend(fallback_chain)

    events: list[FailoverEvent] = []
    last_error: BaseException | None = None

    for model_idx, model in enumerate(chain):
        # Circuit breaker fast-path: if this provider is OPEN, skip the
        # entire model (no retries) and walk to the next chain entry. We
        # still emit a FailoverEvent so observability records the skip.
        if circuit_breaker is not None:
            try:
                await circuit_breaker.before_call(str(model.provider))
            except CircuitOpenError as cb_err:
                events.append(
                    FailoverEvent(
                        model_id=model.id,
                        provider=str(model.provider),
                        attempt=0,
                        succeeded=False,
                        error_type="CircuitOpenError",
                        error_message=str(cb_err),
                    )
                )
                log.info(
                    "failover.circuit_open_skip",
                    request_id=request_id,
                    model=model.id,
                    provider=str(model.provider),
                    retry_after_s=cb_err.retry_after_s,
                )
                last_error = ProviderServerError(f"circuit_open: {model.provider}")
                continue  # next model in the chain

        for attempt, backoff_ms in enumerate(backoffs_ms):
            try:
                response, latency_ms = await asyncio.wait_for(call(model), timeout=timeout_s)
            except ProviderClientError as err:
                # Not retryable, not a failover candidate. Bail to caller.
                events.append(
                    FailoverEvent(
                        model_id=model.id,
                        provider=str(model.provider),
                        attempt=attempt,
                        succeeded=False,
                        error_type=type(err).__name__,
                        error_message=str(err),
                    )
                )
                log.info(
                    "failover.client_error",
                    request_id=request_id,
                    model=model.id,
                    status=err.status_code,
                )
                raise
            except ProviderAuthError as err:
                # Auth failure on this provider — skip retries, jump to next.
                events.append(
                    FailoverEvent(
                        model_id=model.id,
                        provider=str(model.provider),
                        attempt=attempt,
                        succeeded=False,
                        error_type=type(err).__name__,
                        error_message=str(err),
                    )
                )
                log.warning(
                    "failover.auth_error",
                    request_id=request_id,
                    model=model.id,
                    provider=str(model.provider),
                )
                if circuit_breaker is not None:
                    await circuit_breaker.record_error(str(model.provider))
                last_error = err
                break  # stop retrying this model, fall through to next
            except (
                TimeoutError,
                ProviderTimeoutError,
                ProviderRateLimitError,
                ProviderServerError,
            ) as err:
                # Normalise asyncio timeout into our error type for the event.
                if isinstance(err, asyncio.TimeoutError):
                    err = ProviderTimeoutError(f"timeout after {timeout_s}s on {model.id}")

                last_attempt_for_model = attempt == len(backoffs_ms) - 1
                events.append(
                    FailoverEvent(
                        model_id=model.id,
                        provider=str(model.provider),
                        attempt=attempt,
                        succeeded=False,
                        error_type=type(err).__name__,
                        error_message=str(err),
                        retried_after_ms=None if last_attempt_for_model else backoff_ms,
                    )
                )
                log.warning(
                    "failover.retry",
                    request_id=request_id,
                    model=model.id,
                    attempt=attempt,
                    error=type(err).__name__,
                )
                if circuit_breaker is not None:
                    await circuit_breaker.record_error(str(model.provider))
                last_error = err
                if last_attempt_for_model:
                    break  # exhausted attempts for this model, move on
                # Honour Retry-After if provider sent one and it's reasonable
                wait_s = backoff_ms / 1000.0
                if isinstance(err, ProviderRateLimitError) and err.retry_after_s:
                    # Cap at the configured backoff to keep p99 bounded.
                    wait_s = min(err.retry_after_s, backoff_ms / 1000.0)
                with contextlib.suppress(asyncio.CancelledError):
                    await asyncio.sleep(wait_s)
                continue
            except ProviderError as err:
                # Catch-all for any subclass we haven't enumerated.
                events.append(
                    FailoverEvent(
                        model_id=model.id,
                        provider=str(model.provider),
                        attempt=attempt,
                        succeeded=False,
                        error_type=type(err).__name__,
                        error_message=str(err),
                    )
                )
                log.warning(
                    "failover.unknown_provider_error",
                    request_id=request_id,
                    model=model.id,
                    error=str(err),
                )
                if circuit_breaker is not None:
                    await circuit_breaker.record_error(str(model.provider))
                last_error = err
                break

            # ----------- Success path -----------
            if circuit_breaker is not None:
                await circuit_breaker.record_success(str(model.provider))
            events.append(
                FailoverEvent(
                    model_id=model.id,
                    provider=str(model.provider),
                    attempt=attempt,
                    succeeded=True,
                    latency_ms=latency_ms,
                )
            )
            failover_used = model_idx > 0
            if failover_used:
                log.info(
                    "failover.recovered",
                    request_id=request_id,
                    primary=primary.id,
                    chosen=model.id,
                    after_attempts=len(events),
                )
            return FailoverResult(
                response=response,
                model=model,
                failover_used=failover_used,
                events=events,
            )

        # All retries for this model failed — continue to next in chain.
        # If failover is disabled or chain is exhausted, the loop ends here.

    raise FailoverError(
        f"all {len(chain)} model(s) failed",
        attempts=events,
        last_error=last_error,
    )


__all__ = [
    "DEFAULT_BACKOFFS_MS",
    "DEFAULT_TIMEOUT_S",
    "FailoverError",
    "FailoverEvent",
    "FailoverResult",
    "ProviderAuthError",
    "ProviderCallable",
    "ProviderClientError",
    "ProviderError",
    "ProviderRateLimitError",
    "ProviderServerError",
    "ProviderTimeoutError",
    "with_failover",
]
