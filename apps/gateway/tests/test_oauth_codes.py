"""Authorization-code storage helpers."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from voltari_gateway.auth.oauth_codes import (
    OAuthCodeError,
    consume_authorization_code,
    issue_authorization_code,
)


@pytest.mark.asyncio
async def test_issue_returns_plaintext_and_persists_hash(db, api_key_fixture):
    from sqlalchemy import select

    from voltari_gateway.db.models import OAuthAuthorizationCode, OAuthClient

    db.add(
        OAuthClient(
            client_id="studio",
            name="Studio",
            redirect_uris=["http://localhost:3737/callback"],
            allowed_scopes=["chat.read"],
            is_first_party=True,
        )
    )
    await db.flush()

    plaintext = await issue_authorization_code(
        db,
        client_id="studio",
        user_id=api_key_fixture.user.id,
        account_id=api_key_fixture.account.id,
        scopes=["chat.read"],
        redirect_uri="http://localhost:3737/callback",
        code_challenge="x" * 43,
        code_challenge_method="S256",
        ttl_seconds=600,
    )
    assert isinstance(plaintext, str) and len(plaintext) >= 32

    rows = (await db.execute(select(OAuthAuthorizationCode))).scalars().all()
    assert len(rows) == 1
    # Plaintext must NOT appear anywhere in storage.
    assert rows[0].code_hash != plaintext


@pytest.mark.asyncio
async def test_consume_returns_record_and_marks_used(db, api_key_fixture):
    from voltari_gateway.db.models import OAuthClient

    db.add(
        OAuthClient(
            client_id="studio",
            name="Studio",
            redirect_uris=["http://localhost:3737/callback"],
            allowed_scopes=["chat.read"],
            is_first_party=True,
        )
    )
    await db.flush()

    plaintext = await issue_authorization_code(
        db,
        client_id="studio",
        user_id=api_key_fixture.user.id,
        account_id=api_key_fixture.account.id,
        scopes=["chat.read"],
        redirect_uri="http://localhost:3737/callback",
        code_challenge="x" * 43,
        code_challenge_method="S256",
        ttl_seconds=600,
    )
    await db.commit()

    row = await consume_authorization_code(db, plaintext, client_id="studio")
    assert row.scopes == ["chat.read"]
    assert row.used_at is not None


@pytest.mark.asyncio
async def test_consume_rejects_replay(db, api_key_fixture):
    from voltari_gateway.db.models import OAuthClient

    db.add(
        OAuthClient(
            client_id="studio",
            name="Studio",
            redirect_uris=["http://localhost:3737/callback"],
            allowed_scopes=["chat.read"],
            is_first_party=True,
        )
    )
    await db.flush()
    plaintext = await issue_authorization_code(
        db,
        client_id="studio",
        user_id=api_key_fixture.user.id,
        account_id=api_key_fixture.account.id,
        scopes=["chat.read"],
        redirect_uri="http://localhost:3737/callback",
        code_challenge="x" * 43,
        code_challenge_method="S256",
        ttl_seconds=600,
    )
    await db.commit()

    await consume_authorization_code(db, plaintext, client_id="studio")
    with pytest.raises(OAuthCodeError, match="invalid_grant"):
        await consume_authorization_code(db, plaintext, client_id="studio")


@pytest.mark.asyncio
async def test_consume_rejects_expired(db, api_key_fixture):
    from sqlalchemy import update

    from voltari_gateway.db.models import OAuthAuthorizationCode, OAuthClient

    db.add(
        OAuthClient(
            client_id="studio",
            name="Studio",
            redirect_uris=["http://localhost:3737/callback"],
            allowed_scopes=["chat.read"],
            is_first_party=True,
        )
    )
    await db.flush()
    plaintext = await issue_authorization_code(
        db,
        client_id="studio",
        user_id=api_key_fixture.user.id,
        account_id=api_key_fixture.account.id,
        scopes=["chat.read"],
        redirect_uri="http://localhost:3737/callback",
        code_challenge="x" * 43,
        code_challenge_method="S256",
        ttl_seconds=600,
    )
    # Force expiry into the past.
    await db.execute(
        update(OAuthAuthorizationCode).values(expires_at=datetime.now(UTC) - timedelta(minutes=1))
    )
    await db.commit()

    with pytest.raises(OAuthCodeError, match="invalid_grant"):
        await consume_authorization_code(db, plaintext, client_id="studio")


@pytest.mark.asyncio
async def test_consume_rejects_wrong_client(db, api_key_fixture):
    from voltari_gateway.db.models import OAuthClient

    db.add(
        OAuthClient(
            client_id="studio",
            name="Studio",
            redirect_uris=["http://localhost:3737/callback"],
            allowed_scopes=["chat.read"],
            is_first_party=True,
        )
    )
    db.add(
        OAuthClient(
            client_id="other",
            name="Other",
            redirect_uris=["http://x"],
            allowed_scopes=["chat.read"],
            is_first_party=False,
        )
    )
    await db.flush()
    plaintext = await issue_authorization_code(
        db,
        client_id="studio",
        user_id=api_key_fixture.user.id,
        account_id=api_key_fixture.account.id,
        scopes=["chat.read"],
        redirect_uri="http://localhost:3737/callback",
        code_challenge="x" * 43,
        code_challenge_method="S256",
        ttl_seconds=600,
    )
    await db.commit()

    with pytest.raises(OAuthCodeError, match="invalid_grant"):
        await consume_authorization_code(db, plaintext, client_id="other")
