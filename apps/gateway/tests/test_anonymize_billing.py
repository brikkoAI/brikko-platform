"""Tests for the pay-per-use billing layer on POST /v1/anonymize.

Coverage (BRIEF_v2_pivot.md § 5, CEO decision 2026-05-14):

1. First 100 requests / day fall inside the free quota — no balance debit.
2. The 101st request consumes 2 kopecks (= 0.02 ₽) from the account
   balance via a standard ``transactions`` row.
3. When the daily quota is used and balance is 0, the endpoint returns
   402 with a ``topup_url`` pointing at brikko.ru/app/billing.
4. New accounts created via POST /signup receive a 100 ₽
   (= 10_000 kopecks) welcome bonus on the anonymize ledger, recorded as
   a separate Transaction with ``meta.kind == "welcome_anonymize"`` so it
   never collides with the older 200 ₽ verify-email welcome credit.
5. The daily counter key includes the Москва-local calendar date — so a
   tick past 00:00 МСК gets the user a fresh quota.

Notes
-----
Sub-test #1 makes 100 real HTTP calls; on the in-memory SQLite + fakeredis
stack this comes in well under a second on developer laptops. If that
ever flips, drop the test to 5 + a direct Redis counter assertion — the
gate logic is fully covered by tests #2 and #3 either way.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from voltari_gateway.billing.anonymize_billing import (
    ANONYMIZE_FREE_DAILY_QUOTA,
    ANONYMIZE_PRICE_KOPECKS,
    ANONYMIZE_WELCOME_BONUS_KOPECKS,
    _counter_key,
    _msk_date_key,
)
from voltari_gateway.db.models import Account, Transaction, TransactionKind


def _msk_now() -> datetime:
    return datetime.now(timezone(timedelta(hours=3)))


# ---------------------------------------------------------------------------
# 1) Free quota: first 100 requests pass without touching the balance
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_free_quota_first_100_requests_pass(
    client, api_key_fixture, session_factory, redis_client
):
    initial_balance = api_key_fixture.account.balance_kopecks
    payload = {"text": "ИНН 7707083893"}  # PII text → mapping gets persisted

    for i in range(ANONYMIZE_FREE_DAILY_QUOTA):
        r = await client.post(
            "/v1/anonymize",
            json=payload,
            headers=api_key_fixture.auth_header,
        )
        assert r.status_code == 200, f"request #{i + 1}: {r.text}"

    # Open a fresh session for assertions — the fixture session caches the
    # Account row with expire_on_commit=False, so a SELECT through it would
    # serve a stale identity-map hit instead of the post-handler state.
    async with session_factory() as s:
        refreshed = (
            await s.execute(select(Account).where(Account.id == api_key_fixture.account.id))
        ).scalar_one()
        assert refreshed.balance_kopecks == initial_balance

        # No charge transactions written for this account.
        charge_rows = (
            await s.execute(
                select(Transaction).where(
                    Transaction.account_id == api_key_fixture.account.id,
                    Transaction.type == TransactionKind.CHARGE,
                )
            )
        ).all()
        assert charge_rows == []

    # Counter sits exactly at the quota.
    counter_raw = await redis_client.get(_counter_key(api_key_fixture.account.id, _msk_date_key()))
    assert int(counter_raw) == ANONYMIZE_FREE_DAILY_QUOTA


# ---------------------------------------------------------------------------
# 2) Post-quota request charges 0.02 ₽ to the balance
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_free_quota_101st_request_charges_balance(
    client, api_key_fixture, session_factory, redis_client
):
    initial_balance = api_key_fixture.account.balance_kopecks

    # Preset the counter at the quota so the next request is paid.
    counter = _counter_key(api_key_fixture.account.id, _msk_date_key())
    await redis_client.set(counter, str(ANONYMIZE_FREE_DAILY_QUOTA))

    r = await client.post(
        "/v1/anonymize",
        json={"text": "ИНН 7707083893"},
        headers=api_key_fixture.auth_header,
    )
    assert r.status_code == 200, r.text

    # Fresh session — see test_free_quota_first_100 for rationale.
    async with session_factory() as s:
        refreshed = (
            await s.execute(select(Account).where(Account.id == api_key_fixture.account.id))
        ).scalar_one()
        assert refreshed.balance_kopecks == initial_balance - ANONYMIZE_PRICE_KOPECKS

        charges = (
            (
                await s.execute(
                    select(Transaction).where(
                        Transaction.account_id == api_key_fixture.account.id,
                        Transaction.type == TransactionKind.CHARGE,
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(charges) == 1
        assert charges[0].amount_kopecks == -ANONYMIZE_PRICE_KOPECKS
        assert charges[0].meta is not None
        assert charges[0].meta.get("kind") == "anonymize"


# ---------------------------------------------------------------------------
# 3) Zero balance after quota → 402 with topup_url
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_zero_balance_returns_402(client, api_key_fixture, session_factory, redis_client):
    # Zero out the balance, then exhaust the quota.
    async with session_factory() as s:
        account = (
            await s.execute(select(Account).where(Account.id == api_key_fixture.account.id))
        ).scalar_one()
        account.balance_kopecks = 0
        s.add(account)
        await s.commit()

    counter = _counter_key(api_key_fixture.account.id, _msk_date_key())
    await redis_client.set(counter, str(ANONYMIZE_FREE_DAILY_QUOTA))

    r = await client.post(
        "/v1/anonymize",
        json={"text": "ИНН 7707083893"},
        headers=api_key_fixture.auth_header,
    )
    # Pivot 2026-05-15 (BRIEF_v2_pivot.md): 402 message swap
    # quota_exceeded/"Top up" → subscription_required/"Subscribe to Pro/Team".
    # The old top-up path is gone — PAYG = welcome credits only.
    assert r.status_code == 402, r.text
    body = r.json()
    assert body["error"] == "subscription_required"
    assert "brikko.ru/app/billing" in body["subscribe_url"]
    assert "Subscribe to Pro" in body["message"]

    # Balance untouched (no debit even attempted past the InsufficientBalance
    # error inside check_and_charge_anonymize).
    async with session_factory() as s:
        refreshed = (
            await s.execute(select(Account).where(Account.id == api_key_fixture.account.id))
        ).scalar_one()
        assert refreshed.balance_kopecks == 0


# ---------------------------------------------------------------------------
# 4) New signup receives the 100 ₽ welcome bonus
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_welcome_bonus_100_rub_on_signup(client, session_factory):
    # POST /signup creates User+Account in a single transaction. We don't
    # bother with verify-email here — the 100 ₽ bonus lands at signup
    # time, the older 200 ₽ bonus is a separate flow tied to verify.
    r = await client.post(
        "/v1/auth/signup",
        json={
            "email": "newbie-anonymize@example.com",
            "password": "VeryStrongPassword-2026!",
        },
    )
    assert r.status_code == 200, r.text
    user_id = r.json()["user_id"]

    async with session_factory() as s:
        account = (await s.execute(select(Account).where(Account.owner_id == user_id))).scalar_one()

        assert account.balance_kopecks == ANONYMIZE_WELCOME_BONUS_KOPECKS

        # A matching Transaction row exists, tagged welcome_anonymize.
        welcome_tx = (
            await s.execute(
                select(Transaction).where(
                    Transaction.account_id == account.id,
                    Transaction.ref_id == f"welcome-anonymize:{account.id}",
                )
            )
        ).scalar_one()
        assert welcome_tx.type == TransactionKind.TOPUP
        assert welcome_tx.amount_kopecks == ANONYMIZE_WELCOME_BONUS_KOPECKS
        assert welcome_tx.meta is not None
        assert welcome_tx.meta.get("kind") == "welcome_anonymize"


# ---------------------------------------------------------------------------
# 5) Daily counter rolls over at 00:00 МСК (not UTC)
# ---------------------------------------------------------------------------


def test_daily_counter_resets_at_midnight_msk():
    # МСК midnight = UTC 21:00 the previous day. We pick a moment 30
    # minutes BEFORE midnight МСК (= 20:30 UTC) and 30 minutes AFTER
    # midnight МСК (= 21:30 UTC) and confirm the keys differ.
    before_midnight_utc = datetime(2026, 5, 14, 20, 30, tzinfo=UTC)
    after_midnight_utc = datetime(2026, 5, 14, 21, 30, tzinfo=UTC)

    key_before = _msk_date_key(before_midnight_utc)
    key_after = _msk_date_key(after_midnight_utc)

    # Sanity: МСК is UTC+3, so 20:30 UTC = 23:30 МСК (May 14) and 21:30
    # UTC = 00:30 МСК (May 15).
    assert key_before == "2026-05-14"
    assert key_after == "2026-05-15"
    assert key_before != key_after

    # Same instant of UTC noon resolves consistently regardless of where
    # we are in the day → key is deterministic.
    utc_noon = datetime(2026, 5, 14, 12, 0, tzinfo=UTC)
    assert _msk_date_key(utc_noon) == "2026-05-14"
