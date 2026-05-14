"""Per-account rate limiting for /v1/chat/completions (BE P1-21).

Without this, a customer with a large balance can stampede a single
provider down to its rate limits before the breaker even kicks in: at
$0.0001 / req on a cheap model and a 100 ₽ welcome bonus, that's
~10 000 requests / min if nothing slows them down. Provider rate-limits
fire first, but they fire AT US and our other customers eat the
collateral degradation.

Algorithm
---------

Token bucket with capacity = ``burst`` and refill rate = ``rate_per_second``.
Implemented as a Redis transaction (``MULTI/EXEC`` with ``WATCH``):

1. ``WATCH`` the bucket key.
2. ``HMGET`` the current ``tokens`` and ``ts``.
3. Compute new ``tokens = min(burst, tokens + elapsed * rate)``.
4. If ``tokens >= 1`` consume one; otherwise mark ``denied``.
5. ``MULTI`` → ``HSET`` the new state, ``EXPIRE`` for cleanup, ``EXEC``.
6. If ``WATCH`` saw a concurrent write, retry up to 3 times.

Slightly more round-trips than a Lua script, but works without server-
side scripting (handy for fakeredis in tests, and for managed Redis
products that lock down EVAL). At MVP scale the difference is below
the noise floor of a single SQL query.

Tariff scaling
--------------

The base rate is ``CHAT_RATE_PER_SECOND`` / ``CHAT_RATE_BURST``. Higher
tariffs get multipliers — see ``_TARIFF_MULTIPLIERS``. Defaults match
the brief:

* PAYG       → 1× (60/120)
* PRO        → 2× (120/240)
* TEAM       → 4× (240/480)
* BUSINESS+  → 10× (600/1200)

Failure mode
------------

If Redis is unreachable we fail OPEN (allow the request) and log a
warning. The worst case is unlimited burst until Redis recovers —
preferable to denying every request when Redis goes down for a minute.
The provider-side rate limits and breaker still bound damage.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, cast

from fastapi import Request
from redis.asyncio import Redis

from voltari_gateway.auth.middleware import AuthPrincipal
from voltari_gateway.config import get_settings
from voltari_gateway.utils.errors import GatewayError
from voltari_gateway.utils.logging import get_logger

log = get_logger(__name__)


# ---- Tariff multipliers -----------------------------------------------------

# Multiply base ``CHAT_RATE_PER_SECOND`` / ``CHAT_RATE_BURST`` by this for
# the caller's tariff. Unknown tariffs fall back to 1× (PAYG).
_TARIFF_MULTIPLIERS: dict[str, float] = {
    "payg": 1.0,
    "pro": 2.0,
    "team": 4.0,
    "business": 10.0,
    "business_plus": 10.0,
}


@dataclass(frozen=True, slots=True)
class RateLimitDecision:
    """What ``check`` returns. Lets the caller emit the OpenAI-compatible
    headers without re-querying Redis."""

    allowed: bool
    limit_per_second: int
    burst: int
    remaining: int
    reset_in_seconds: int


# ---- Limiter ---------------------------------------------------------------


# Number of WATCH/MULTI retries before we fail-open. 3 is more than
# enough for low-contention workloads; pathological contention on a
# single key (one customer hammering us) will see a few retries but not
# block the request.
_MAX_RETRIES = 3


class ChatRateLimiter:
    """Per-account token bucket on Redis. Fail-open on Redis errors.

    Singleton-style: instantiate once in app startup, share across
    handlers.
    """

    def __init__(self, redis: Redis | None) -> None:
        self._redis = redis

    @staticmethod
    def _resolve_for_tariff(tariff: str) -> tuple[int, int]:
        """Effective (rate_per_second, burst) for ``tariff``."""
        settings = get_settings()
        mult = _TARIFF_MULTIPLIERS.get(tariff.lower(), 1.0)
        rate = max(1, int(settings.chat_rate_per_second * mult))
        burst = max(rate, int(settings.chat_rate_burst * mult))
        return rate, burst

    async def _take_one_token(self, key: str, rate: int, burst: int) -> tuple[bool, int, int]:
        """Atomic-via-WATCH version of the token bucket. Returns
        ``(allowed, remaining, reset_in_seconds)``.

        Fails open on any Redis error.
        """
        if self._redis is None:
            return True, burst, 0

        for _ in range(_MAX_RETRIES):
            try:
                async with self._redis.pipeline(transaction=True) as pipe:
                    await pipe.watch(key)
                    # ``hmget`` returns Awaitable[list] in immediate-mode
                    # (after WATCH but before MULTI). The redis-py stub
                    # union-types it, so we cast.
                    hmget_result: Any = pipe.hmget(key, ["tokens", "ts"])
                    raw = cast(list[Any], await hmget_result)
                    now = time.time()

                    tokens_raw, ts_raw = raw[0], raw[1]
                    if tokens_raw is None:
                        tokens = float(burst)
                        ts = now
                    else:
                        try:
                            tokens = float(tokens_raw)
                            ts = float(ts_raw) if ts_raw is not None else now
                        except (TypeError, ValueError):
                            tokens = float(burst)
                            ts = now

                    elapsed = max(0.0, now - ts)
                    tokens = min(float(burst), tokens + elapsed * rate)

                    allowed = tokens >= 1.0
                    if allowed:
                        tokens -= 1.0

                    # TTL = 2× refill window so an idle bucket eventually
                    # evicts itself but rarely during normal use. Floor at
                    # 60s.
                    ttl = max(60, int((burst / max(rate, 1)) * 2))

                    # ``multi`` is documented but redis-py stubs don't
                    # publish a typing for it on the async client.
                    pipe.multi()  # type: ignore[no-untyped-call]
                    pipe.hset(key, mapping={"tokens": tokens, "ts": now})
                    pipe.expire(key, ttl)
                    await pipe.execute()

                    remaining = int(tokens)
                    reset_in = 0 if allowed else max(1, int((1.0 - tokens) / max(rate, 1)) + 1)
                    return allowed, remaining, reset_in
            except Exception as exc:
                # WatchError, ConnectionError, etc. Fail-open.
                log.warning("rate_limit_pipeline_failed", error=str(exc))
                return True, burst, 0

        # Exhausted retries — allow.
        return True, burst, 0

    async def check(self, account_id: str, *, tariff: str) -> RateLimitDecision:
        """Take one token from ``account_id``'s bucket.

        Returns ``allowed=True`` when a token was available (and
        consumed); ``False`` otherwise. ``remaining`` is the bucket level
        AFTER consumption (or 0 if denied). ``reset_in_seconds`` is the
        seconds until at least 1 token will be available.
        """
        rate, burst = self._resolve_for_tariff(tariff)

        if self._redis is None:
            return RateLimitDecision(
                allowed=True,
                limit_per_second=rate,
                burst=burst,
                remaining=burst,
                reset_in_seconds=0,
            )

        key = f"rl:chat:{account_id}"
        allowed, remaining, reset_in = await self._take_one_token(key, rate, burst)
        return RateLimitDecision(
            allowed=allowed,
            limit_per_second=rate,
            burst=burst,
            remaining=max(0, remaining),
            reset_in_seconds=reset_in,
        )


# ---- FastAPI dependency -----------------------------------------------------


_RATE_LIMITER: ChatRateLimiter | None = None


def set_rate_limiter(limiter: ChatRateLimiter | None) -> None:
    """Inject the singleton limiter (called from app startup / tests)."""
    global _RATE_LIMITER
    _RATE_LIMITER = limiter


def get_rate_limiter() -> ChatRateLimiter | None:
    return _RATE_LIMITER


def _too_many_requests(decision: RateLimitDecision) -> GatewayError:
    """Build the OpenAI-style 429 envelope, with rate-limit headers."""
    return GatewayError(
        status_code=429,
        message=(
            f"Too many requests. Limit {decision.limit_per_second}/s "
            f"(burst {decision.burst}). Retry in {decision.reset_in_seconds}s."
        ),
        type="rate_limit_error",
        code="rate_limit_exceeded",
        headers={
            "Retry-After": str(decision.reset_in_seconds),
            "X-RateLimit-Limit": str(decision.limit_per_second),
            "X-RateLimit-Burst": str(decision.burst),
            "X-RateLimit-Remaining": str(decision.remaining),
            "X-RateLimit-Reset": str(decision.reset_in_seconds),
        },
    )


async def enforce_chat_rate_limit(request: Request, principal: AuthPrincipal) -> RateLimitDecision:
    """Apply the per-account chat rate limit. Raise 429 on deny.

    Sets ``X-RateLimit-*`` headers via ``request.state.rate_limit_headers``
    so the chat endpoint can attach them to the successful response too.
    """
    limiter = _RATE_LIMITER
    if limiter is None:
        # Limiter not wired (e.g. very early in startup). Allow through.
        return RateLimitDecision(
            allowed=True,
            limit_per_second=0,
            burst=0,
            remaining=0,
            reset_in_seconds=0,
        )

    decision = await limiter.check(str(principal.account_id), tariff=principal.tariff)

    # Stash for the success path so the JSON response can also carry the
    # X-RateLimit-* headers.
    request.state.rate_limit_headers = {
        "X-RateLimit-Limit": str(decision.limit_per_second),
        "X-RateLimit-Burst": str(decision.burst),
        "X-RateLimit-Remaining": str(decision.remaining),
    }

    if not decision.allowed:
        log.info(
            "chat_rate_limited",
            account_id=str(principal.account_id),
            tariff=principal.tariff,
            limit_per_second=decision.limit_per_second,
            reset_in=decision.reset_in_seconds,
        )
        raise _too_many_requests(decision)

    return decision


__all__ = [
    "ChatRateLimiter",
    "RateLimitDecision",
    "enforce_chat_rate_limit",
    "get_rate_limiter",
    "set_rate_limiter",
]
