"""Per-provider circuit breaker (BE P1-20).

Without this, ``with_failover`` keeps retrying a dead provider for every
incoming request: 3 attempts × 30s timeout = 90s p99 worst-case while
OpenAI is in a regional outage. The breaker fast-fails after a small
number of errors so the failover walk reaches a healthy provider almost
immediately.

State machine
-------------

::

    CLOSED ── error_count ≥ THRESHOLD in WINDOW ──► OPEN
       ▲                                              │
       │                                  RESET timer expires
       │                                              │
       │                                              ▼
       └──── HALF_OPEN ── one probe call succeeds ──► CLOSED
                  │
              probe fails
                  │
                  ▼
                OPEN

* **CLOSED** — provider works. Each error increments a sliding-window
  counter in Redis. Reaching the threshold flips to OPEN.
* **OPEN** — every call fails immediately with ``CircuitOpenError``
  (mapped by ``failover.py`` to ``ProviderServerError`` so the existing
  failover semantics still apply — we just skip the wasted retries on
  this provider). Stays open for ``RESET_SECONDS``.
* **HALF_OPEN** — one probe call is allowed. Success closes; failure
  reopens. We use a Redis SET-NX with TTL=RESET_SECONDS as the "this
  request is the probe" lease so concurrent requests don't all try to
  probe at once.

Storage
-------

All state lives in Redis with short TTLs so a Redis outage degrades to
"breaker permanently closed" — i.e. the original failover behaviour. We
never throw on Redis errors; the breaker is a perf optimisation, not a
safety property.

Keys (all prefixed with ``cb:``):

* ``cb:state:{provider}`` — string ``open`` (TTL=RESET_SECONDS), absent
  means CLOSED. HALF_OPEN is implicit and stateless: after the OPEN
  key expires, the next call goes through, and if it fails the
  counter increments naturally; once the threshold is re-crossed the
  breaker opens again.
* ``cb:errors:{provider}`` — integer counter, TTL=WINDOW_SECONDS,
  incremented on each call to ``record_error``. Reset (DEL) on
  ``record_success``.

Configuration
-------------

Read from ``Settings`` at construction time. Tests can build their own
``CircuitBreaker(...)`` with smaller windows.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass

from redis.asyncio import Redis

from voltari_gateway.utils.logging import get_logger

log = get_logger(__name__)


# ---- defaults ---------------------------------------------------------------

DEFAULT_THRESHOLD = 10
DEFAULT_WINDOW_SECONDS = 60
DEFAULT_RESET_SECONDS = 30


# ---- types ------------------------------------------------------------------


class CircuitState(enum.StrEnum):
    """Externally-observable state of a single provider's breaker."""

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitOpenError(Exception):
    """Raised by ``before_call`` when the breaker is OPEN and we have no
    probe lease.

    Caller (failover.py) treats this as a non-retryable signal to skip to
    the next provider in the chain. ``provider`` lets observability tell
    apart "openai is open" vs "anthropic is open".
    """

    def __init__(self, provider: str, *, retry_after_s: int) -> None:
        super().__init__(f"circuit_open: {provider}")
        self.provider = provider
        self.retry_after_s = retry_after_s


@dataclass(frozen=True, slots=True)
class CircuitConfig:
    """Tunables for a single ``CircuitBreaker`` instance."""

    threshold: int = DEFAULT_THRESHOLD
    window_seconds: int = DEFAULT_WINDOW_SECONDS
    reset_seconds: int = DEFAULT_RESET_SECONDS


# ---- breaker ----------------------------------------------------------------


class CircuitBreaker:
    """Provider-aware circuit breaker backed by Redis.

    All public methods are no-throw against Redis I/O — on Redis failure
    we degrade to "breaker is CLOSED" so traffic still flows. The trade-
    off: if Redis is down we lose the fast-fail benefit, but we never
    block legitimate traffic.

    A single instance handles every provider; the provider name is
    passed into each call. This matches the registry's "providers as
    keyed strings" model.
    """

    def __init__(
        self,
        redis: Redis | None,
        *,
        config: CircuitConfig | None = None,
        prefix: str = "cb",
    ) -> None:
        self._redis = redis
        self._cfg = config or CircuitConfig()
        self._prefix = prefix

    @property
    def config(self) -> CircuitConfig:
        return self._cfg

    # --- key naming -----------------------------------------------------

    def _state_key(self, provider: str) -> str:
        return f"{self._prefix}:state:{provider}"

    def _errors_key(self, provider: str) -> str:
        return f"{self._prefix}:errors:{provider}"

    # --- introspection --------------------------------------------------

    async def state(self, provider: str) -> CircuitState:
        """Current state for ``provider``. CLOSED on Redis miss / failure."""
        if self._redis is None:
            return CircuitState.CLOSED
        try:
            val = await self._redis.get(self._state_key(provider))
        except Exception as exc:
            log.warning("circuit_breaker_redis_error", op="state", error=str(exc))
            return CircuitState.CLOSED
        if val is None:
            return CircuitState.CLOSED
        decoded = val if isinstance(val, str) else val.decode("utf-8")
        if decoded == "open":
            return CircuitState.OPEN
        return CircuitState.CLOSED  # unknown values default to safe

    async def error_count(self, provider: str) -> int:
        """Current error count in the sliding window. 0 on Redis miss."""
        if self._redis is None:
            return 0
        try:
            val = await self._redis.get(self._errors_key(provider))
        except Exception:
            return 0
        if val is None:
            return 0
        try:
            return int(val if isinstance(val, str) else val.decode("utf-8"))
        except (ValueError, TypeError):
            return 0

    # --- gating ---------------------------------------------------------

    async def before_call(self, provider: str) -> None:
        """Raise ``CircuitOpenError`` if the breaker is OPEN.

        Call this RIGHT BEFORE invoking the provider. Pair with
        ``record_success`` on success or ``record_error`` on failure.

        State machine simplified for storage simplicity:

        * OPEN — reject everything. State key has TTL = ``reset_seconds``
          and naturally expires.
        * After expiry — state key is gone, ``state()`` returns CLOSED.
          The next caller is effectively the half-open probe: they're
          allowed through, and either succeed (record_success keeps us
          CLOSED) or fail (record_error increments counter and once we
          re-cross the threshold the breaker opens again).

        We *don't* gate concurrent half-open callers behind a separate
        SET-NX lease — the call rate at MVP scale is low enough that
        a brief "all retry once when the OPEN expires" stampede isn't
        worth the extra Redis round-trip. If we hit that scale we add
        the lease back; the public API doesn't change.
        """
        if self._redis is None:
            return

        try:
            state_val = await self._redis.get(self._state_key(provider))
        except Exception as exc:
            log.warning("circuit_breaker_redis_error", op="before_call", error=str(exc))
            return  # fail-open

        if state_val is None:
            return  # CLOSED

        decoded = state_val if isinstance(state_val, str) else state_val.decode("utf-8")
        if decoded != "open":
            return

        # OPEN — reject. TTL of the state key is the time until reset.
        try:
            ttl = await self._redis.ttl(self._state_key(provider))
        except Exception:
            ttl = self._cfg.reset_seconds
        retry_after = max(
            1,
            int(ttl) if ttl is not None and ttl > 0 else self._cfg.reset_seconds,
        )
        raise CircuitOpenError(provider, retry_after_s=retry_after)

    # --- recording ------------------------------------------------------

    async def record_success(self, provider: str) -> None:
        """Reset the breaker for ``provider`` after a successful call.

        Closes the breaker (if it was OPEN/HALF_OPEN) and clears the
        error counter. No-op on Redis failure.
        """
        if self._redis is None:
            return
        try:
            keys = [
                self._state_key(provider),
                self._errors_key(provider),
            ]
            await self._redis.delete(*keys)
        except Exception as exc:
            log.warning("circuit_breaker_redis_error", op="record_success", error=str(exc))

    async def record_error(self, provider: str) -> None:
        """Increment the error counter; flip to OPEN if threshold reached.

        The counter has a TTL = WINDOW_SECONDS so it resets automatically
        when the burst subsides. If the breaker is already OPEN this is
        cheap — the counter doesn't matter until the OPEN expires.
        """
        if self._redis is None:
            return

        errors_key = self._errors_key(provider)
        try:
            count = await self._redis.incr(errors_key)
            # Set TTL only on first increment (don't extend on every call,
            # so the window slides cleanly).
            if count == 1:
                await self._redis.expire(errors_key, self._cfg.window_seconds)
        except Exception as exc:
            log.warning("circuit_breaker_redis_error", op="record_error", error=str(exc))
            return

        if count < self._cfg.threshold:
            return

        # Threshold reached — open the breaker.
        try:
            await self._redis.set(
                self._state_key(provider),
                "open",
                ex=self._cfg.reset_seconds,
            )
            # Clear the error counter so when the breaker resets we start
            # fresh; otherwise a half-open probe failure would compound
            # leftover errors and reopen instantly.
            await self._redis.delete(errors_key)
        except Exception as exc:
            log.warning("circuit_breaker_redis_error", op="open_breaker", error=str(exc))
            return

        log.warning(
            "circuit_breaker_opened",
            provider=provider,
            errors=count,
            reset_seconds=self._cfg.reset_seconds,
        )


__all__ = [
    "DEFAULT_RESET_SECONDS",
    "DEFAULT_THRESHOLD",
    "DEFAULT_WINDOW_SECONDS",
    "CircuitBreaker",
    "CircuitConfig",
    "CircuitOpenError",
    "CircuitState",
]
