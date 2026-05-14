"""Tests for ``voltari_gateway.middleware.rate_limit`` (BE P1-21).

We test the limiter directly (no FastAPI plumbing) — that's the
critical contract: 429 after burst exhaustion, refill over time, tariff
multipliers. The end-to-end "/v1/chat/completions returns 429" test is
exercised via ``test_chat_completions.py`` because it needs the chat
fixture stack.

We freeze ``time.time`` via monkeypatch to make burst-exhaustion
deterministic — a 60/s refill rate would otherwise paper over the
deny path between two real-clock requests.
"""

from __future__ import annotations

import os

import fakeredis.aioredis
import pytest

# Set env BEFORE importing the limiter — settings are cached.
os.environ.setdefault("CHAT_RATE_PER_SECOND", "10")
os.environ.setdefault("CHAT_RATE_BURST", "20")

from voltari_gateway.config import get_settings
from voltari_gateway.middleware import rate_limit as rl_module
from voltari_gateway.middleware.rate_limit import (
    ChatRateLimiter,
)

get_settings.cache_clear()


@pytest.fixture
async def redis_client():
    client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    yield client
    await client.aclose()


@pytest.fixture
async def limiter(redis_client) -> ChatRateLimiter:
    return ChatRateLimiter(redis_client)


@pytest.fixture
def freeze_clock(monkeypatch):
    """Pin ``time.time`` so refill math is deterministic."""

    class _Clock:
        def __init__(self, t: float = 1_700_000_000.0) -> None:
            self.t = t

        def now(self) -> float:
            return self.t

        def advance(self, seconds: float) -> None:
            self.t += seconds

    clk = _Clock()
    monkeypatch.setattr(rl_module.time, "time", clk.now)
    return clk


# ---- core token-bucket tests ------------------------------------------------


@pytest.mark.asyncio
async def test_chat_rate_limit_allows_within_burst(limiter: ChatRateLimiter, freeze_clock) -> None:
    """First N=burst calls all succeed; the (N+1)-th is denied."""
    rate, burst = ChatRateLimiter._resolve_for_tariff("payg")
    account_id = "acct-1"

    # Drain the bucket (we should be allowed exactly ``burst`` requests).
    allowed_count = 0
    for _ in range(burst):
        decision = await limiter.check(account_id, tariff="payg")
        if decision.allowed:
            allowed_count += 1
        else:
            break

    assert allowed_count == burst

    # Next call must be denied (clock frozen → no refill).
    decision = await limiter.check(account_id, tariff="payg")
    assert decision.allowed is False
    assert decision.reset_in_seconds >= 1
    assert decision.limit_per_second == rate
    assert decision.burst == burst


@pytest.mark.asyncio
async def test_chat_rate_limit_resets_after_window(limiter: ChatRateLimiter, freeze_clock) -> None:
    """After advancing the clock past 1 / rate seconds, we get a token back."""
    account_id = "acct-reset"
    rate, burst = ChatRateLimiter._resolve_for_tariff("payg")

    # Drain — frozen clock so all denials persist.
    for _ in range(burst + 1):
        await limiter.check(account_id, tariff="payg")

    # Now advance the clock by 1 second (should refill ``rate`` tokens).
    freeze_clock.advance(1.0)

    decision = await limiter.check(account_id, tariff="payg")
    assert decision.allowed is True


@pytest.mark.asyncio
async def test_chat_rate_limit_per_account_isolated(limiter: ChatRateLimiter, freeze_clock) -> None:
    """One account hitting the limit doesn't affect another."""
    rate, burst = ChatRateLimiter._resolve_for_tariff("payg")

    # Drain account A.
    for _ in range(burst + 1):
        await limiter.check("acct-A", tariff="payg")

    # Account A is rate-limited.
    a = await limiter.check("acct-A", tariff="payg")
    assert a.allowed is False

    # Account B is unaffected.
    b = await limiter.check("acct-B", tariff="payg")
    assert b.allowed is True


@pytest.mark.asyncio
async def test_chat_rate_limit_per_tariff(limiter: ChatRateLimiter, freeze_clock) -> None:
    """Pro tariff (2× multiplier) gets a strictly larger bucket than PAYG."""
    payg_rate, payg_burst = ChatRateLimiter._resolve_for_tariff("payg")
    pro_rate, pro_burst = ChatRateLimiter._resolve_for_tariff("pro")
    team_rate, team_burst = ChatRateLimiter._resolve_for_tariff("team")
    biz_rate, biz_burst = ChatRateLimiter._resolve_for_tariff("business")

    assert pro_rate > payg_rate
    assert team_rate > pro_rate
    assert biz_rate > team_rate
    assert pro_burst > payg_burst
    assert team_burst > pro_burst

    # Drain a Pro account up to PAYG burst — must still allow.
    for _ in range(payg_burst):
        d = await limiter.check("acct-pro-1", tariff="pro")
        assert d.allowed is True

    # The (payg_burst+1)-th call would deny on PAYG. On Pro it should
    # still allow because the bucket is bigger.
    d = await limiter.check("acct-pro-1", tariff="pro")
    assert d.allowed is True


@pytest.mark.asyncio
async def test_chat_rate_limit_unknown_tariff_falls_to_payg(
    limiter: ChatRateLimiter,
) -> None:
    """An unknown tariff string is treated as PAYG (1× multiplier)."""
    payg_rate, _ = ChatRateLimiter._resolve_for_tariff("payg")
    # Some made-up value.
    unknown_rate, _ = ChatRateLimiter._resolve_for_tariff("does-not-exist")
    assert unknown_rate == payg_rate


@pytest.mark.asyncio
async def test_chat_rate_limit_redis_unreachable_fails_open() -> None:
    """If Redis is None the limiter must allow every request."""
    limiter = ChatRateLimiter(redis=None)
    for _ in range(500):
        d = await limiter.check("acct-X", tariff="payg")
        assert d.allowed is True


@pytest.mark.asyncio
async def test_chat_rate_limit_pipeline_failure_fails_open(
    limiter: ChatRateLimiter,
) -> None:
    """If the pipeline raises (network blip, server reset), allow."""

    # Replace the redis client with one whose pipeline always raises.
    class _BoomRedis:
        def pipeline(self, transaction: bool = True):
            raise RuntimeError("simulated redis outage")

    limiter._redis = _BoomRedis()  # type: ignore[assignment]
    d = await limiter.check("acct-after-boom", tariff="payg")
    assert d.allowed is True


@pytest.mark.asyncio
async def test_chat_rate_limit_decision_headers(
    limiter: ChatRateLimiter,
) -> None:
    """Decision must carry sensible header values for the OpenAI envelope."""
    d = await limiter.check("acct-headers", tariff="pro")
    rate, burst = ChatRateLimiter._resolve_for_tariff("pro")
    assert d.limit_per_second == rate
    assert d.burst == burst
    assert d.remaining >= 0
    assert d.reset_in_seconds == 0  # allowed → no wait


def test_oauth_per_ip_5_per_minute():
    from voltari_gateway.auth.rate_limit import check_oauth, reset_all

    reset_all()
    ip = "203.0.113.42"
    for _ in range(5):
        assert check_oauth(ip) is True
    assert check_oauth(ip) is False  # 6th call within the minute is denied
