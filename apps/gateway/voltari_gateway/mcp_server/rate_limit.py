"""Per-token rate limiting for /mcp tool calls.

A simple sliding-window-ish counter on Redis using INCR + EXPIRE. We do
NOT reuse ``ChatRateLimiter`` because:

* The chat limiter buckets per ACCOUNT (one budget across all api_keys).
  MCP rate-limiting is per TOKEN — Cursor on the laptop and Claude
  Desktop on the desktop are two tokens, each gets its own budget.
* The chat limiter has tariff multipliers (Business gets 10×) tuned for
  inference traffic. MCP tools are metadata-cheap, the right limit is
  flat (60/min) regardless of tariff. A Business user calling
  ``read_account`` 600 times a minute is almost certainly a buggy agent
  loop, not a real workload.

Algorithm
---------

Fixed-window counter per minute:

    key = mcp:rl:<token_id>:<minute_bucket>
    INCR key
    EXPIRE key 90   (slightly > window so a clock skew doesn't kill it)

Burst-friendly enough for legitimate use (Claude Desktop opens with a
``list_tools`` + 1-2 reads = ~5 calls), defends against runaway loops.

Failure mode: fail-open on Redis I/O errors. A 60-rpm limit is not a
security control — it's a UX guard against agent loops. If Redis is
down we'd rather serve the user than 503.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass

from redis.asyncio import Redis

from voltari_gateway.utils.logging import get_logger

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class McpRateDecision:
    allowed: bool
    limit_per_min: int
    remaining: int
    reset_in_seconds: int


class McpRateLimiter:
    """Fixed-window counter, one bucket per minute per token.

    Singleton-style — instantiate once at app startup, share across tool
    invocations. Constructor accepts ``redis=None`` for tests / dev where
    Redis is unavailable; in that case ``check`` always allows.
    """

    def __init__(self, redis: Redis | None, *, limit_per_min: int = 60) -> None:
        self._redis = redis
        self._limit = max(1, limit_per_min)

    async def check(self, token_id: uuid.UUID) -> McpRateDecision:
        """Increment the current-minute bucket; return whether allowed."""
        if self._redis is None:
            return McpRateDecision(
                allowed=True,
                limit_per_min=self._limit,
                remaining=self._limit,
                reset_in_seconds=0,
            )

        now = int(time.time())
        bucket = now // 60
        reset_in = 60 - (now % 60)
        key = f"mcp:rl:{token_id}:{bucket}"

        try:
            async with self._redis.pipeline(transaction=False) as pipe:
                pipe.incr(key)
                # 90s ttl > 60s window so a request right before the boundary
                # never sees a stale bucket from two minutes ago.
                pipe.expire(key, 90)
                result = await pipe.execute()
            count = int(result[0])
        except Exception as exc:
            log.warning("mcp_rate_limit_redis_failed", token_id=str(token_id), error=str(exc))
            # Fail-open. Counter is best-effort UX guard, not security.
            return McpRateDecision(
                allowed=True,
                limit_per_min=self._limit,
                remaining=self._limit,
                reset_in_seconds=0,
            )

        allowed = count <= self._limit
        remaining = max(0, self._limit - count)
        return McpRateDecision(
            allowed=allowed,
            limit_per_min=self._limit,
            remaining=remaining,
            reset_in_seconds=reset_in,
        )


# ---------------------------------------------------------------------------
# Singleton wiring
# ---------------------------------------------------------------------------


_LIMITER: McpRateLimiter | None = None


def set_mcp_rate_limiter(limiter: McpRateLimiter | None) -> None:
    """Inject the singleton limiter (called from app startup / tests)."""
    global _LIMITER
    _LIMITER = limiter


def get_mcp_rate_limiter() -> McpRateLimiter | None:
    return _LIMITER


__all__ = [
    "McpRateDecision",
    "McpRateLimiter",
    "get_mcp_rate_limiter",
    "set_mcp_rate_limiter",
]
