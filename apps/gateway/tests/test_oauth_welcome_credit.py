"""Idempotent 200 ₽ Studio welcome credit."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from voltari_gateway.billing.oauth_welcome_credit import (
    STUDIO_WELCOME_CREDIT_KOPECKS,
    grant_studio_welcome_credit_if_first_time,
)
from voltari_gateway.db.models import Account, Transaction, TransactionKind


@pytest.mark.asyncio
async def test_first_call_grants_credit_and_flips_flag(db, api_key_fixture):
    starting_balance = api_key_fixture.account.balance_kopecks
    granted = await grant_studio_welcome_credit_if_first_time(
        db,
        account_id=api_key_fixture.account.id,
        user_id=api_key_fixture.user.id,
        client_id="studio",
    )
    await db.commit()

    assert granted is True
    refreshed = await db.get(Account, api_key_fixture.account.id)
    assert refreshed.is_studio_user is True
    assert refreshed.balance_kopecks == starting_balance + STUDIO_WELCOME_CREDIT_KOPECKS

    txns = (
        (
            await db.execute(
                select(Transaction).where(Transaction.account_id == api_key_fixture.account.id)
            )
        )
        .scalars()
        .all()
    )
    assert any(
        t.type == TransactionKind.TOPUP and t.amount_kopecks == STUDIO_WELCOME_CREDIT_KOPECKS
        for t in txns
    )


@pytest.mark.asyncio
async def test_second_call_is_noop(db, api_key_fixture):
    await grant_studio_welcome_credit_if_first_time(
        db,
        account_id=api_key_fixture.account.id,
        user_id=api_key_fixture.user.id,
        client_id="studio",
    )
    await db.commit()
    starting_balance = (await db.get(Account, api_key_fixture.account.id)).balance_kopecks

    granted = await grant_studio_welcome_credit_if_first_time(
        db,
        account_id=api_key_fixture.account.id,
        user_id=api_key_fixture.user.id,
        client_id="studio",
    )
    await db.commit()

    assert granted is False
    refreshed = await db.get(Account, api_key_fixture.account.id)
    assert refreshed.balance_kopecks == starting_balance


@pytest.mark.asyncio
async def test_non_studio_client_does_not_grant(db, api_key_fixture):
    granted = await grant_studio_welcome_credit_if_first_time(
        db,
        account_id=api_key_fixture.account.id,
        user_id=api_key_fixture.user.id,
        client_id="some-other-client",
    )
    assert granted is False
    refreshed = await db.get(Account, api_key_fixture.account.id)
    assert refreshed.is_studio_user is False
