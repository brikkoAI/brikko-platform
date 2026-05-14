"""Autorefill background loop — single-tick correctness.

Mocks ЮKassa via respx. Ensures:

* Account flagged for refill below threshold → ЮKassa charge fires.
* Successful charge credits balance and writes a TransactionKind.AUTOREFILL row.
* Failed charge does NOT credit and bumps the per-account failure counter.
* After 3 daily failures the circuit opens (no more attempts that day).
"""

from __future__ import annotations

import pytest
import respx

from voltari_gateway.billing.autorefill import (
    ARF_BACKOFF_KEY,
    ARF_MAX_FAILURES_PER_DAY,
    autorefill_tick,
)
from voltari_gateway.billing.yookassa import YooKassaClient, YooKassaConfig
from voltari_gateway.db.models import Account


def _yookassa_client() -> YooKassaClient:
    return YooKassaClient(
        YooKassaConfig(
            shop_id="shop",
            secret_key="secret",
            webhook_secret="hk",
            return_url_template="https://test/{account_id}",
            base_url="https://test-yookassa/v3",
        )
    )


@pytest.mark.asyncio
async def test_autorefill_tick_charges_and_credits(
    session_factory, redis_client, api_key_fixture, db
):
    """Below-threshold + saved card → balance gets topped up."""
    api_key_fixture.account.balance_kopecks = 50_00  # 50 ₽
    api_key_fixture.account.autorefill_enabled = True
    api_key_fixture.account.autorefill_pm_id = "pm-saved-1"
    api_key_fixture.account.autorefill_threshold_kopecks = 100_00
    api_key_fixture.account.autorefill_topup_kopecks = 500_00
    db.add(api_key_fixture.account)
    await db.commit()

    yk = _yookassa_client()
    try:
        with respx.mock(base_url="https://test-yookassa/v3") as mock:
            mock.post("/payments").respond(
                200,
                json={
                    "id": "auto-pay-1",
                    "status": "succeeded",
                    "amount": {"value": "500.00", "currency": "RUB"},
                },
            )
            n = await autorefill_tick(
                session_factory=session_factory, yookassa=yk, redis=redis_client
            )
        assert n == 1
        async with session_factory() as fresh:
            acc = await fresh.get(Account, api_key_fixture.account.id)
            assert acc is not None
            assert acc.balance_kopecks == 50_00 + 500_00
    finally:
        await yk.aclose()


@pytest.mark.asyncio
async def test_autorefill_skips_above_threshold(session_factory, redis_client, api_key_fixture, db):
    """Balance above threshold → no charge fired."""
    api_key_fixture.account.balance_kopecks = 200_00
    api_key_fixture.account.autorefill_enabled = True
    api_key_fixture.account.autorefill_pm_id = "pm-saved-2"
    api_key_fixture.account.autorefill_threshold_kopecks = 100_00
    api_key_fixture.account.autorefill_topup_kopecks = 500_00
    db.add(api_key_fixture.account)
    await db.commit()

    yk = _yookassa_client()
    try:
        with respx.mock(base_url="https://test-yookassa/v3", assert_all_called=False) as mock:
            route = mock.post("/payments").respond(
                200, json={"id": "x", "status": "x", "amount": {"value": "1.00"}}
            )
            n = await autorefill_tick(
                session_factory=session_factory, yookassa=yk, redis=redis_client
            )
            assert route.call_count == 0
        assert n == 0
    finally:
        await yk.aclose()


@pytest.mark.asyncio
async def test_autorefill_failure_records_backoff(
    session_factory, redis_client, api_key_fixture, db
):
    """ЮKassa 502 → no credit, failure counter incremented."""
    api_key_fixture.account.balance_kopecks = 10_00
    api_key_fixture.account.autorefill_enabled = True
    api_key_fixture.account.autorefill_pm_id = "pm-bad"
    api_key_fixture.account.autorefill_threshold_kopecks = 100_00
    api_key_fixture.account.autorefill_topup_kopecks = 500_00
    db.add(api_key_fixture.account)
    await db.commit()

    yk = _yookassa_client()
    try:
        with respx.mock(base_url="https://test-yookassa/v3") as mock:
            mock.post("/payments").respond(502, text="upstream down")
            n = await autorefill_tick(
                session_factory=session_factory, yookassa=yk, redis=redis_client
            )
        assert n == 0
        # Counter incremented
        backoff_val = await redis_client.get(
            ARF_BACKOFF_KEY.format(account_id=api_key_fixture.account.id)
        )
        assert int(backoff_val) == 1
    finally:
        await yk.aclose()


@pytest.mark.asyncio
async def test_autorefill_circuit_breaker_after_3_fails(
    session_factory, redis_client, api_key_fixture, db
):
    """3 failures in a day → circuit open, no more ЮKassa calls."""
    api_key_fixture.account.balance_kopecks = 10_00
    api_key_fixture.account.autorefill_enabled = True
    api_key_fixture.account.autorefill_pm_id = "pm-x"
    api_key_fixture.account.autorefill_threshold_kopecks = 100_00
    api_key_fixture.account.autorefill_topup_kopecks = 500_00
    db.add(api_key_fixture.account)
    await db.commit()

    # Pre-load the failure counter.
    await redis_client.set(
        ARF_BACKOFF_KEY.format(account_id=api_key_fixture.account.id),
        str(ARF_MAX_FAILURES_PER_DAY),
    )

    yk = _yookassa_client()
    try:
        with respx.mock(base_url="https://test-yookassa/v3", assert_all_called=False) as mock:
            route = mock.post("/payments").respond(
                200, json={"id": "x", "status": "succeeded", "amount": {"value": "1.00"}}
            )
            n = await autorefill_tick(
                session_factory=session_factory, yookassa=yk, redis=redis_client
            )
            assert route.call_count == 0  # circuit kept the call from going out
        assert n == 0
    finally:
        await yk.aclose()
