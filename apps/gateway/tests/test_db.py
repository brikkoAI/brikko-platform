"""DB-layer tests: tables exist after migration, models persist."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import inspect, select

from voltari_gateway.db.models import (
    Account,
    AccountStatus,
    ApiKey,
    ApiKeyStatus,
    Tariff,
    User,
)


@pytest.mark.asyncio
async def test_metadata_creates_all_tables(engine):
    def _names(sync_conn):
        return set(inspect(sync_conn).get_table_names())

    async with engine.connect() as conn:
        names = await conn.run_sync(_names)
    expected = {
        "users",
        "accounts",
        "seats",
        "api_keys",
        "transactions",
        "usage_events",
        "request_payloads",
    }
    assert expected.issubset(names)


@pytest.mark.asyncio
async def test_user_account_apikey_round_trip(db):
    user = User(
        email="alice@example.com",
        password_hash="x",
        email_verified=True,
    )
    db.add(user)
    await db.flush()

    account = Account(
        owner_id=user.id,
        name="Alice's account",
        balance_kopecks=50_000,
        tariff=Tariff.PRO,
        status=AccountStatus.ACTIVE,
    )
    db.add(account)
    await db.flush()

    key = ApiKey(
        account_id=account.id,
        name="primary",
        key_hash="$argon2id$dummy",
        key_prefix="sk-vlt-aB12cd",
        status=ApiKeyStatus.ACTIVE,
    )
    db.add(key)
    await db.commit()

    fetched = (await db.execute(select(User).where(User.email == "alice@example.com"))).scalar_one()
    assert fetched.email_verified is True
    assert isinstance(fetched.id, uuid.UUID)

    fetched_account = (
        await db.execute(select(Account).where(Account.owner_id == fetched.id))
    ).scalar_one()
    assert fetched_account.tariff == Tariff.PRO
    assert fetched_account.balance_kopecks == 50_000
    assert fetched_account.store_prompts is True

    fetched_key = (
        await db.execute(select(ApiKey).where(ApiKey.account_id == fetched_account.id))
    ).scalar_one()
    assert fetched_key.status == ApiKeyStatus.ACTIVE
    assert fetched_key.key_prefix == "sk-vlt-aB12cd"


@pytest.mark.asyncio
async def test_oauth_client_round_trip(db, api_key_fixture):
    from voltari_gateway.db.models import OAuthClient

    client = OAuthClient(
        client_id="studio",
        name="Brikko Studio",
        redirect_uris=["http://localhost:3737/callback"],
        allowed_scopes=["chat.read", "messages.read"],
        is_first_party=True,
    )
    db.add(client)
    await db.commit()

    row = (
        await db.execute(select(OAuthClient).where(OAuthClient.client_id == "studio"))
    ).scalar_one()
    assert row.name == "Brikko Studio"
    assert row.redirect_uris == ["http://localhost:3737/callback"]
    assert row.is_first_party is True


@pytest.mark.asyncio
async def test_oauth_authorization_code_persists(db, api_key_fixture):
    from voltari_gateway.db.models import OAuthAuthorizationCode

    code = OAuthAuthorizationCode(
        code_hash="sha256-deadbeef",
        client_id="studio",
        user_id=api_key_fixture.user.id,
        account_id=api_key_fixture.account.id,
        scopes=["chat.read"],
        redirect_uri="http://localhost:3737/callback",
        code_challenge="abc",
        code_challenge_method="S256",
        expires_at=datetime.now(UTC) + timedelta(minutes=10),
    )
    db.add(code)
    await db.commit()

    row = (
        await db.execute(
            select(OAuthAuthorizationCode).where(
                OAuthAuthorizationCode.code_hash == "sha256-deadbeef"
            )
        )
    ).scalar_one()
    assert row.scopes == ["chat.read"]
    assert row.used_at is None


@pytest.mark.asyncio
async def test_account_has_is_studio_user_default_false(db, api_key_fixture):
    assert api_key_fixture.account.is_studio_user is False
