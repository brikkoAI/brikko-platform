"""Tests for TD-009 — ApiKey.expires_at lifecycle.

Coverage:

1. POST /v1/keys without ``expires_in_days`` → expires_at is NULL
   (backwards-compat). Bearer auth keeps working forever.
2. POST /v1/keys with ``expires_in_days=90`` → expires_at = now+90d.
3. Bearer auth with an expired key → 401 (no leak of "expired" code).
4. Bearer auth with a not-yet-expired key → 200 (smoke).
5. Invalid ``expires_in_days`` (e.g. 7) → 422 (Literal enforcement).
6. GET /v1/keys returns ``expires_at`` field.
7. CreateKeyResponse contains ``expires_at``.

We intentionally don't expose a distinct ``api_key_expired`` error code
to the bearer flow — see ``_verify_against_db`` docstring for why.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from voltari_gateway.auth.keys import generate_api_key
from voltari_gateway.auth.password import hash_password
from voltari_gateway.db.models import (
    Account,
    AccountStatus,
    ApiKey,
    ApiKeyStatus,
    Tariff,
    User,
)

_PASSWORD = "correct horse battery staple"

# TD-036 — see tests/auth/test_api_keys.py for context.
from tests.auth.conftest import csrf_headers as _csrf_headers  # noqa: E402


async def _seed_user(db, *, tariff: Tariff = Tariff.PRO) -> User:
    user = User(
        email=f"u-{uuid.uuid4().hex[:10]}@example.com",
        password_hash=hash_password(_PASSWORD),
        email_verified=True,
    )
    db.add(user)
    await db.flush()
    db.add(
        Account(
            owner_id=user.id,
            name="Test acc",
            balance_kopecks=100_000,
            tariff=tariff,
            status=AccountStatus.ACTIVE,
            store_prompts=True,
            settings={},
        )
    )
    await db.commit()
    await db.refresh(user)
    return user


async def _login(client, user) -> None:
    r = await client.post(
        "/v1/auth/login",
        json={"email": user.email, "password": _PASSWORD},
    )
    assert r.status_code == 200, r.text


# ---------- POST /v1/keys creation -------------------------------------------


@pytest.mark.asyncio
async def test_create_key_without_expires_in_days_has_null_expiry(client, db, redis_client):
    user = await _seed_user(db)
    await _login(client, user)

    r = await client.post(
        "/v1/keys",
        json={"name": "no-expiry", "scope": "write"},
        headers=await _csrf_headers(client),
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["expires_at"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("days", [30, 90, 180, 365])
async def test_create_key_with_expires_in_days(client, db, redis_client, days):
    user = await _seed_user(db)
    await _login(client, user)

    r = await client.post(
        "/v1/keys",
        json={"name": f"expires-{days}", "scope": "write", "expires_in_days": days},
        headers=await _csrf_headers(client),
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["expires_at"] is not None
    expires = datetime.fromisoformat(body["expires_at"])
    if expires.tzinfo is None:  # SQLite serialises without offset
        expires = expires.replace(tzinfo=UTC)
    delta = expires - datetime.now(UTC)
    # Bound the assertion: between (days-1) and (days+0.01) days from now.
    # The test takes well under 1 hour so we're generous on the lower bound.
    assert timedelta(days=days - 1, hours=23) <= delta <= timedelta(days=days, hours=1)


@pytest.mark.asyncio
async def test_create_key_with_invalid_expires_in_days_returns_422(client, db, redis_client):
    user = await _seed_user(db)
    await _login(client, user)

    r = await client.post(
        "/v1/keys",
        json={"name": "bad", "scope": "write", "expires_in_days": 7},  # not allowed
        headers=await _csrf_headers(client),
    )
    # Voltari maps Pydantic validation failures to a 400 ``invalid_body``
    # (see utils.errors handler) rather than the FastAPI default 422.
    assert r.status_code == 400, r.text
    body = r.json()
    assert body["error"]["param"] == "expires_in_days"


# ---------- GET /v1/keys list listing ----------------------------------------


@pytest.mark.asyncio
async def test_list_keys_includes_expires_at(client, db, redis_client):
    user = await _seed_user(db)
    await _login(client, user)

    create_r = await client.post(
        "/v1/keys",
        json={"name": "k1", "scope": "write", "expires_in_days": 90},
        headers=await _csrf_headers(client),
    )
    assert create_r.status_code == 201

    list_r = await client.get("/v1/keys")
    assert list_r.status_code == 200
    items = list_r.json()
    assert len(items) == 1
    assert items[0]["expires_at"] is not None


# ---------- Bearer auth with expired key -------------------------------------


@pytest.mark.asyncio
async def test_chat_with_expired_key_returns_401(client, db, redis_client):
    """Expired key → bearer auth fails with 401 (NOT a distinct code)."""
    user = User(
        email=f"u-{uuid.uuid4().hex[:10]}@test.local",
        password_hash="$argon2id$v=19$m=65536,t=2,p=2$dummy",
        email_verified=True,
    )
    db.add(user)
    await db.flush()
    account = Account(
        owner_id=user.id,
        name="acc",
        balance_kopecks=100_000,
        tariff=Tariff.PRO,
        status=AccountStatus.ACTIVE,
        store_prompts=True,
    )
    db.add(account)
    await db.flush()

    generated = generate_api_key()
    expired_key = ApiKey(
        account_id=account.id,
        name="expired",
        key_hash=generated.key_hash,
        key_prefix=generated.prefix,
        status=ApiKeyStatus.ACTIVE,
        # Expired 1 second ago.
        expires_at=datetime.now(UTC) - timedelta(seconds=1),
    )
    db.add(expired_key)
    await db.commit()

    r = await client.post(
        "/v1/chat/completions",
        json={
            "model": "gpt-5.4-mini",
            "messages": [{"role": "user", "content": "ping"}],
        },
        headers={"Authorization": f"Bearer {generated.plaintext}"},
    )
    assert r.status_code == 401
    body = r.json()
    # Generic message — we don't disclose "expired" to attackers.
    # Type is "authentication_error" (from utils.errors.authentication_error),
    # NOT a distinct ``api_key_expired`` code.
    assert body["error"]["type"] == "authentication_error"
    assert body["error"]["code"] == "invalid_api_key"


@pytest.mark.asyncio
async def test_chat_with_non_expired_key_works(client, db, redis_client):
    """Sanity check: a key with expires_at in the future authenticates fine."""
    user = User(
        email=f"u-{uuid.uuid4().hex[:10]}@test.local",
        password_hash="$argon2id$v=19$m=65536,t=2,p=2$dummy",
        email_verified=True,
    )
    db.add(user)
    await db.flush()
    account = Account(
        owner_id=user.id,
        name="acc",
        balance_kopecks=100_000,
        tariff=Tariff.PRO,
        status=AccountStatus.ACTIVE,
        store_prompts=True,
    )
    db.add(account)
    await db.flush()

    generated = generate_api_key()
    fresh_key = ApiKey(
        account_id=account.id,
        name="fresh",
        key_hash=generated.key_hash,
        key_prefix=generated.prefix,
        status=ApiKeyStatus.ACTIVE,
        expires_at=datetime.now(UTC) + timedelta(days=30),
    )
    db.add(fresh_key)
    await db.commit()

    r = await client.post(
        "/v1/chat/completions",
        json={
            "model": "gpt-5.4-mini",
            "messages": [{"role": "user", "content": "ping"}],
        },
        headers={"Authorization": f"Bearer {generated.plaintext}"},
    )
    # Should succeed (stub provider returns 200).
    assert r.status_code == 200, r.text


@pytest.mark.asyncio
async def test_chat_with_legacy_null_expiry_key_works(client, db, redis_client):
    """Existing keys (created before TD-009) with NULL expires_at must keep working."""
    user = User(
        email=f"u-{uuid.uuid4().hex[:10]}@test.local",
        password_hash="$argon2id$v=19$m=65536,t=2,p=2$dummy",
        email_verified=True,
    )
    db.add(user)
    await db.flush()
    account = Account(
        owner_id=user.id,
        name="acc",
        balance_kopecks=100_000,
        tariff=Tariff.PRO,
        status=AccountStatus.ACTIVE,
        store_prompts=True,
    )
    db.add(account)
    await db.flush()

    generated = generate_api_key()
    legacy_key = ApiKey(
        account_id=account.id,
        name="legacy",
        key_hash=generated.key_hash,
        key_prefix=generated.prefix,
        status=ApiKeyStatus.ACTIVE,
        expires_at=None,  # NULL — never expires
    )
    db.add(legacy_key)
    await db.commit()

    r = await client.post(
        "/v1/chat/completions",
        json={
            "model": "gpt-5.4-mini",
            "messages": [{"role": "user", "content": "ping"}],
        },
        headers={"Authorization": f"Bearer {generated.plaintext}"},
    )
    assert r.status_code == 200, r.text
