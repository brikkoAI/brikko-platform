"""Edge-case tests for ``voltari_gateway.billing.autorefill``.

Targets the lines the happy-path suite (``test_billing_autorefill.py``)
doesn't reach:

* ``_circuit_open`` — Redis-None, garbage value, threshold semantics.
* ``_record_failure`` — Redis-None no-op, TTL set on first hit.
* ``_acquire_lock`` — Redis-None returns True, normal case.
* ``autorefill_loop`` — single-tick stop_event happy path.
* ``_try_refill_one`` — pending status (no credit), payment-method missing.

Bumps ``billing/autorefill.py`` 68% → ~90%.
"""

from __future__ import annotations

import asyncio
import uuid
from unittest.mock import AsyncMock

import fakeredis.aioredis
import pytest

from voltari_gateway.billing import autorefill as ar
from voltari_gateway.billing.autorefill import (
    ARF_BACKOFF_KEY,
    ARF_LOCK_KEY,
    ARF_MAX_FAILURES_PER_DAY,
    _acquire_lock,
    _circuit_open,
    _record_failure,
    _try_refill_one,
    autorefill_loop,
    autorefill_tick,
)
from voltari_gateway.billing.yookassa import (
    PaymentURL,
    YooKassaClient,
    YooKassaConfig,
)


def _yk() -> YooKassaClient:
    return YooKassaClient(
        YooKassaConfig(
            shop_id="shop",
            secret_key="secret",
            webhook_secret="hk",
            return_url_template="https://test/{account_id}",
            base_url="https://test-yookassa/v3",
        )
    )


# ---------- _circuit_open ----------------------------------------------------


@pytest.mark.asyncio
async def test_circuit_open_returns_false_when_redis_none():
    assert (await _circuit_open(None, uuid.uuid4())) is False


@pytest.mark.asyncio
async def test_circuit_open_returns_false_below_threshold():
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    try:
        acc = uuid.uuid4()
        await redis.set(ARF_BACKOFF_KEY.format(account_id=acc), "1")
        assert (await _circuit_open(redis, acc)) is False
    finally:
        await redis.aclose()


@pytest.mark.asyncio
async def test_circuit_open_returns_true_at_or_above_threshold():
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    try:
        acc = uuid.uuid4()
        await redis.set(ARF_BACKOFF_KEY.format(account_id=acc), str(ARF_MAX_FAILURES_PER_DAY))
        assert (await _circuit_open(redis, acc)) is True
    finally:
        await redis.aclose()


@pytest.mark.asyncio
async def test_circuit_open_handles_garbage_value():
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    try:
        acc = uuid.uuid4()
        await redis.set(ARF_BACKOFF_KEY.format(account_id=acc), "garbage-not-int")
        assert (await _circuit_open(redis, acc)) is False
    finally:
        await redis.aclose()


# ---------- _record_failure --------------------------------------------------


@pytest.mark.asyncio
async def test_record_failure_no_op_when_redis_none():
    # Nothing to assert except "doesn't crash".
    await _record_failure(None, uuid.uuid4())


@pytest.mark.asyncio
async def test_record_failure_sets_ttl_on_first_hit():
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    try:
        acc = uuid.uuid4()
        key = ARF_BACKOFF_KEY.format(account_id=acc)
        await _record_failure(redis, acc)
        # Counter at 1, TTL set (positive integer).
        assert await redis.get(key) == "1"
        ttl = await redis.ttl(key)
        assert ttl > 0
        # Second hit: counter increments, TTL is NOT reset.
        await _record_failure(redis, acc)
        assert await redis.get(key) == "2"
    finally:
        await redis.aclose()


# ---------- _acquire_lock ----------------------------------------------------


@pytest.mark.asyncio
async def test_acquire_lock_when_redis_none_returns_true():
    assert (await _acquire_lock(None)) is True


@pytest.mark.asyncio
async def test_acquire_lock_first_caller_wins_then_blocks():
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    try:
        assert (await _acquire_lock(redis)) is True
        # Second caller: NX fails, returns False.
        assert (await _acquire_lock(redis)) is False
    finally:
        await redis.delete(ARF_LOCK_KEY)
        await redis.aclose()


# ---------- _try_refill_one edge cases --------------------------------------


@pytest.mark.asyncio
async def test_try_refill_one_skips_when_payment_method_missing(
    session_factory, redis_client, api_key_fixture, db
):
    """Account flagged enabled but ``autorefill_pm_id`` is None → skip."""
    acc = api_key_fixture.account
    acc.autorefill_enabled = True
    acc.autorefill_pm_id = None  # missing
    acc.autorefill_threshold_kopecks = 1_000_00
    acc.autorefill_topup_kopecks = 5_000_00
    db.add(acc)
    await db.commit()

    yk = _yk()
    async with session_factory() as s:
        ok = await _try_refill_one(db=s, yookassa=yk, redis=redis_client, account=acc)
    assert ok is False


@pytest.mark.asyncio
async def test_try_refill_one_skips_when_circuit_open(
    session_factory, redis_client, api_key_fixture, db
):
    acc = api_key_fixture.account
    acc.autorefill_enabled = True
    acc.autorefill_pm_id = "pm-saved-1"
    acc.balance_kopecks = 50_00
    db.add(acc)
    await db.commit()

    # Flip the breaker for this account.
    await redis_client.set(ARF_BACKOFF_KEY.format(account_id=acc.id), str(ARF_MAX_FAILURES_PER_DAY))

    yk = _yk()
    async with session_factory() as s:
        ok = await _try_refill_one(db=s, yookassa=yk, redis=redis_client, account=acc)
    assert ok is False


@pytest.mark.asyncio
async def test_try_refill_one_pending_status_does_not_credit(
    session_factory, redis_client, api_key_fixture, db, monkeypatch
):
    """ЮKassa returns 'pending' → we don't credit the balance until webhook."""
    acc = api_key_fixture.account
    starting_balance = acc.balance_kopecks
    acc.autorefill_enabled = True
    acc.autorefill_pm_id = "pm-saved-1"
    acc.autorefill_threshold_kopecks = starting_balance + 1
    acc.autorefill_topup_kopecks = 1_000_00
    db.add(acc)
    await db.commit()

    yk = _yk()
    monkeypatch.setattr(
        yk,
        "charge_recurring",
        AsyncMock(
            return_value=PaymentURL(
                payment_id="pay_pending",
                confirmation_url="",
                amount_kopecks=1_000_00,
                status="pending",
            )
        ),
    )

    async with session_factory() as s:
        ok = await _try_refill_one(db=s, yookassa=yk, redis=redis_client, account=acc)
    assert ok is False

    # Balance unchanged.
    async with session_factory() as s:
        from voltari_gateway.db.models import Account

        refreshed = await s.get(Account, acc.id)
        assert refreshed is not None
        assert refreshed.balance_kopecks == starting_balance


# ---------- autorefill_tick — empty candidates path -------------------------


@pytest.mark.asyncio
async def test_autorefill_tick_with_no_candidates_returns_zero(session_factory, redis_client):
    """No account has autorefill_enabled → no work done."""
    yk = _yk()
    refilled = await autorefill_tick(
        session_factory=session_factory, yookassa=yk, redis=redis_client
    )
    assert refilled == 0


# ---------- autorefill_loop — single tick exit path -------------------------


@pytest.mark.asyncio
async def test_autorefill_loop_stops_on_event(session_factory, redis_client, monkeypatch):
    """Setting ``stop_event`` causes the loop to exit cleanly within one tick."""
    yk = _yk()
    stop = asyncio.Event()

    # Patch tick to a no-op that immediately signals stop.
    async def fake_tick(**_kwargs):
        stop.set()
        return 0

    monkeypatch.setattr(ar, "autorefill_tick", fake_tick)

    # Use a tiny interval so wait_for unblocks fast on stop.
    await asyncio.wait_for(
        autorefill_loop(
            session_factory=session_factory,
            yookassa=yk,
            redis=redis_client,
            interval_seconds=10,
            stop_event=stop,
        ),
        timeout=2.0,
    )


@pytest.mark.asyncio
async def test_autorefill_loop_handles_tick_exception_and_continues(session_factory, monkeypatch):
    """A crash in autorefill_tick must not propagate; the loop logs and waits.

    The loop's ``except Exception: log.exception(...)`` branch is what we
    verify — without it the whole task would die on a transient ЮKassa
    error and autorefill would silently stop working.
    """
    yk = _yk()
    stop = asyncio.Event()
    call_count = {"n": 0}

    async def boom_tick(**_kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise RuntimeError("ЮKassa flaked")
        stop.set()
        return 0

    monkeypatch.setattr(ar, "autorefill_tick", boom_tick)

    # ``redis=None`` so ``_acquire_lock`` always returns True — otherwise the
    # second iteration would be locked out by the first iteration's NX lock.
    try:
        await asyncio.wait_for(
            autorefill_loop(
                session_factory=session_factory,
                yookassa=yk,
                redis=None,
                interval_seconds=0.02,
                stop_event=stop,
            ),
            timeout=5.0,
        )
    except TimeoutError:  # pragma: no cover — diagnostic
        pytest.fail(f"loop did not exit; call_count={call_count}")
    # Tick called at least twice: once for the crash, once that signals stop.
    assert call_count["n"] >= 2
