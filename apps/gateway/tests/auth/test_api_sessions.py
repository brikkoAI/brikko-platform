"""Tests for ``/v1/auth/sessions`` (Sprint 6, Блок 8)."""

from __future__ import annotations

import uuid

import pytest

from voltari_gateway.auth.password import hash_password
from voltari_gateway.auth.session import (
    create_refresh_token,
    is_refresh_token_active,
    register_refresh_token,
)
from voltari_gateway.auth.sessions_store import record_session
from voltari_gateway.db.models import (
    Account,
    AccountStatus,
    Tariff,
    User,
)


async def _make_user(db) -> User:
    user = User(
        email=f"user-{uuid.uuid4().hex[:10]}@example.com",
        password_hash=hash_password("correct horse battery"),
        email_verified=True,
    )
    db.add(user)
    await db.flush()
    db.add(
        Account(
            owner_id=user.id,
            name="acc",
            balance_kopecks=0,
            tariff=Tariff.PAYG,
            status=AccountStatus.ACTIVE,
            store_prompts=True,
        )
    )
    await db.commit()
    await db.refresh(user)
    return user


async def _login(client, user: User) -> None:
    r = await client.post(
        "/v1/auth/login",
        json={"email": user.email, "password": "correct horse battery"},
    )
    assert r.status_code == 200, r.text


async def _csrf(client) -> dict[str, str]:
    r = await client.get("/v1/auth/csrf")
    return {"X-CSRF-Token": r.json()["csrf_token"]}


@pytest.mark.asyncio
async def test_list_sessions_returns_current_marked(client, db):
    user = await _make_user(db)
    await _login(client, user)
    r = await client.get("/v1/auth/sessions")
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    assert len(items) == 1
    assert items[0]["is_current"] is True
    assert items[0]["revoked"] is False


@pytest.mark.asyncio
async def test_list_sessions_includes_other_devices(client, db, redis_client):
    user = await _make_user(db)
    await _login(client, user)
    # Add an "other-device" Postgres row + Redis whitelist entry.
    _, exp, jti = create_refresh_token(user.id)
    await register_refresh_token(redis_client, user.id, jti, exp)
    await record_session(db, user_id=user.id, refresh_jti=jti, expires_at=exp)
    await db.commit()

    r = await client.get("/v1/auth/sessions")
    items = r.json()["items"]
    assert len(items) == 2
    # Exactly one is_current=True.
    assert sum(1 for i in items if i["is_current"]) == 1


@pytest.mark.asyncio
async def test_revoke_specific_session_drops_redis_whitelist(client, db, redis_client):
    user = await _make_user(db)
    await _login(client, user)
    _, exp, other_jti = create_refresh_token(user.id)
    await register_refresh_token(redis_client, user.id, other_jti, exp)
    other_row = await record_session(db, user_id=user.id, refresh_jti=other_jti, expires_at=exp)
    await db.commit()

    r = await client.delete(
        f"/v1/auth/sessions/{other_row.id}",
        headers=await _csrf(client),
    )
    assert r.status_code == 200, r.text
    assert not await is_refresh_token_active(redis_client, user.id, other_jti)


@pytest.mark.asyncio
async def test_cannot_revoke_current_session_400(client, db):
    user = await _make_user(db)
    await _login(client, user)
    r0 = await client.get("/v1/auth/sessions")
    items = r0.json()["items"]
    current = next(i for i in items if i["is_current"])

    r = await client.delete(
        f"/v1/auth/sessions/{current['id']}",
        headers=await _csrf(client),
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "cannot_revoke_current"


@pytest.mark.asyncio
async def test_revoke_foreign_session_returns_404_no_disclosure(client, db):
    """IDOR: another user's session_id must look exactly like a missing one."""
    user_a = await _make_user(db)
    user_b = await _make_user(db)
    # Create a session row for user B.
    from voltari_gateway.auth.sessions_store import default_refresh_expires_at

    _, exp, jti = create_refresh_token(user_b.id)
    b_row = await record_session(
        db, user_id=user_b.id, refresh_jti=jti, expires_at=default_refresh_expires_at()
    )
    await db.commit()

    await _login(client, user_a)
    r = await client.delete(
        f"/v1/auth/sessions/{b_row.id}",
        headers=await _csrf(client),
    )
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "not_found"


@pytest.mark.asyncio
async def test_revoke_all_other_sessions_keeps_current(client, db, redis_client):
    user = await _make_user(db)
    await _login(client, user)
    # Add two more "other" sessions.
    for _ in range(2):
        _, exp, jti = create_refresh_token(user.id)
        await register_refresh_token(redis_client, user.id, jti, exp)
        await record_session(db, user_id=user.id, refresh_jti=jti, expires_at=exp)
    await db.commit()

    r = await client.delete("/v1/auth/sessions", headers=await _csrf(client))
    assert r.status_code == 200
    assert r.json()["revoked"] == 2

    # Listing now shows only the current one as active.
    r2 = await client.get("/v1/auth/sessions")
    active = [i for i in r2.json()["items"] if not i["revoked"]]
    assert len(active) == 1
    assert active[0]["is_current"] is True


@pytest.mark.asyncio
async def test_sessions_endpoints_require_auth(client):
    r = await client.get("/v1/auth/sessions")
    assert r.status_code in (401, 403)
