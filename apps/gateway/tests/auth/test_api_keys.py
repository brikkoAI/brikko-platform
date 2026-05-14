"""Integration tests for `/v1/keys/*`.

Strategy: log in via /v1/auth/login → cookies set → CRUD on /v1/keys.
We also exercise the cross-cut into the bearer-auth pipeline at
/v1/chat/completions to verify a revoke is picked up within < 60 s.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from voltari_gateway.auth.password import hash_password
from voltari_gateway.db.models import (
    Account,
    AccountStatus,
    ApiKey,
    ApiKeyStatus,
    Tariff,
    User,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_PASSWORD = "correct horse battery staple"

# TD-036 (Sprint 3 Поток H) — legacy ``X-Requested-With`` fallback removed.
# Helper below mints a double-submit token via ``GET /v1/auth/csrf``; httpx
# automatically stores the matching ``vlt_csrf`` cookie so callers only
# need the header.
from tests.auth.conftest import csrf_headers as _csrf_headers  # noqa: E402


async def _seed_user(db, *, tariff: Tariff = Tariff.PRO, balance_kop: int = 100_000) -> User:
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
            balance_kopecks=balance_kop,
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


async def _create(client, *, name: str = "default", scope: str = "write") -> dict:
    r = await client.post(
        "/v1/keys",
        json={"name": name, "scope": scope},
        headers=await _csrf_headers(client),
    )
    assert r.status_code == 201, r.text
    return r.json()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_key_returns_full_once(client, db, redis_client):
    user = await _seed_user(db)
    await _login(client, user)

    body = await _create(client, name="cli")
    full = body["full_key"]
    assert full.startswith("sk-brk-")
    assert body["prefix"] == full[:14]
    assert body["scope"] == "write"
    assert body["name"] == "cli"
    assert uuid.UUID(body["id"])

    # The list endpoint must NOT echo the plaintext.
    list_r = await client.get("/v1/keys")
    assert list_r.status_code == 200
    items = list_r.json()
    assert len(items) == 1
    assert "full_key" not in items[0]
    assert items[0]["prefix"] == body["prefix"]


@pytest.mark.asyncio
async def test_get_keys_returns_list_without_full(client, db, redis_client):
    user = await _seed_user(db)
    await _login(client, user)
    await _create(client, name="k1")
    await _create(client, name="k2")

    r = await client.get("/v1/keys")
    assert r.status_code == 200, r.text
    items = r.json()
    assert {it["name"] for it in items} == {"k1", "k2"}
    for it in items:
        assert "full_key" not in it
        assert it["prefix"].startswith("sk-brk-")
        assert it["status"] == "active"
        assert it["revoked_at"] is None


@pytest.mark.asyncio
async def test_get_keys_requires_session(client):
    r = await client.get("/v1/keys")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_patch_key_renames(client, db, redis_client):
    user = await _seed_user(db)
    await _login(client, user)
    body = await _create(client, name="orig")

    r = await client.patch(
        f"/v1/keys/{body['id']}",
        json={"name": "renamed"},
        headers=await _csrf_headers(client),
    )
    assert r.status_code == 200, r.text
    assert r.json()["name"] == "renamed"

    fresh = await db.execute(select(ApiKey).where(ApiKey.id == uuid.UUID(body["id"])))
    refreshed = fresh.scalar_one()
    await db.refresh(refreshed)
    assert refreshed.name == "renamed"


@pytest.mark.asyncio
async def test_patch_key_scope_immutable_via_patch(client, db, redis_client):
    """Sending `scope` in PATCH body must NOT change scope. We use
    ``extra='forbid'`` so the request hard-fails (400) instead of silently
    accepting and ignoring — the SPA needs to see the error to surface it.
    """
    user = await _seed_user(db)
    await _login(client, user)
    body = await _create(client, name="scoped", scope="write")

    r = await client.patch(
        f"/v1/keys/{body['id']}",
        json={"name": "ok", "scope": "read"},
        headers=await _csrf_headers(client),
    )
    assert r.status_code == 400, r.text

    # Confirm scope unchanged.
    fresh = await db.execute(select(ApiKey).where(ApiKey.id == uuid.UUID(body["id"])))
    refreshed = fresh.scalar_one()
    await db.refresh(refreshed)
    assert refreshed.scope.value == "write"


@pytest.mark.asyncio
async def test_delete_key_marks_revoked_at(client, db, redis_client):
    user = await _seed_user(db)
    await _login(client, user)
    body = await _create(client)

    r = await client.delete(f"/v1/keys/{body['id']}", headers=await _csrf_headers(client))
    assert r.status_code == 204, r.text

    fresh = await db.execute(select(ApiKey).where(ApiKey.id == uuid.UUID(body["id"])))
    refreshed = fresh.scalar_one()
    await db.refresh(refreshed)
    assert refreshed.status == ApiKeyStatus.REVOKED
    assert refreshed.revoked_at is not None


@pytest.mark.asyncio
async def test_delete_revoked_key_returns_404(client, db, redis_client):
    user = await _seed_user(db)
    await _login(client, user)
    body = await _create(client)

    r1 = await client.delete(f"/v1/keys/{body['id']}", headers=await _csrf_headers(client))
    assert r1.status_code == 204

    r2 = await client.delete(f"/v1/keys/{body['id']}", headers=await _csrf_headers(client))
    assert r2.status_code == 404
    assert r2.json()["error"]["code"] == "key_not_found"


@pytest.mark.asyncio
async def test_create_key_respects_tariff_limit(client, db, redis_client):
    """PAYG → 3 active keys max. 4th create call must 403."""
    user = await _seed_user(db, tariff=Tariff.PAYG)
    await _login(client, user)

    for i in range(3):
        await _create(client, name=f"k{i}")

    r = await client.post(
        "/v1/keys",
        json={"name": "overflow", "scope": "write"},
        headers=await _csrf_headers(client),
    )
    assert r.status_code == 403
    body = r.json()
    assert body["error"]["code"] == "key_limit_reached"

    # Revoking a key frees a slot — next create succeeds.
    list_r = await client.get("/v1/keys")
    first_id = list_r.json()[0]["id"]
    await client.delete(f"/v1/keys/{first_id}", headers=await _csrf_headers(client))

    r2 = await client.post(
        "/v1/keys",
        json={"name": "after-revoke", "scope": "write"},
        headers=await _csrf_headers(client),
    )
    assert r2.status_code == 201, r2.text


@pytest.mark.asyncio
async def test_user_cannot_access_other_account_keys(client, db, redis_client, app):
    """PATCH/DELETE on a key owned by another account → 404 (not 403, to
    avoid leaking key existence)."""
    # Seed two users; user_a logs in, then we create a key owned by user_b
    # directly in the DB.
    user_a = await _seed_user(db)
    user_b = await _seed_user(db)
    await _login(client, user_a)

    # Create user_b's key directly.
    from voltari_gateway.auth.keys import generate_api_key

    other_account = (
        await db.execute(select(Account).where(Account.owner_id == user_b.id))
    ).scalar_one()
    gen = generate_api_key()
    other_key = ApiKey(
        account_id=other_account.id,
        name="other",
        key_hash=gen.key_hash,
        key_prefix=gen.prefix,
        status=ApiKeyStatus.ACTIVE,
    )
    db.add(other_key)
    await db.commit()
    await db.refresh(other_key)

    # PATCH attempt → 404
    r_patch = await client.patch(
        f"/v1/keys/{other_key.id}",
        json={"name": "hijacked"},
        headers=await _csrf_headers(client),
    )
    assert r_patch.status_code == 404

    # DELETE attempt → 404
    r_del = await client.delete(f"/v1/keys/{other_key.id}", headers=await _csrf_headers(client))
    assert r_del.status_code == 404

    # And the key is untouched in the DB.
    fresh = await db.execute(select(ApiKey).where(ApiKey.id == other_key.id))
    refreshed = fresh.scalar_one()
    await db.refresh(refreshed)
    assert refreshed.name == "other"
    assert refreshed.status == ApiKeyStatus.ACTIVE


@pytest.mark.asyncio
async def test_revoked_key_fails_at_chat_endpoint(client, db, redis_client):
    """Cross-cut: revoke via /v1/keys, then bearer call to /v1/chat/completions
    must return 401. Tests both the DB-side ``status=REVOKED`` filter in the
    auth middleware and the cache-invalidation index path.
    """
    user = await _seed_user(db)
    await _login(client, user)
    created = await _create(client, name="for-chat")
    plaintext = created["full_key"]

    # Pre-warm the auth cache with one successful chat call (uses the stub
    # OpenAI provider in conftest).
    chat_body = {
        "model": "gpt-5.4-mini",
        "messages": [{"role": "user", "content": "hi"}],
    }
    pre = await client.post(
        "/v1/chat/completions",
        json=chat_body,
        headers={"Authorization": f"Bearer {plaintext}"},
    )
    assert pre.status_code == 200, pre.text

    # Revoke via the management API.
    r = await client.delete(
        f"/v1/keys/{created['id']}",
        headers=await _csrf_headers(client),
    )
    assert r.status_code == 204

    # Subsequent chat call must 401 — even though the auth cache had a fresh
    # entry (the index purge wipes it on revoke).
    after = await client.post(
        "/v1/chat/completions",
        json=chat_body,
        headers={"Authorization": f"Bearer {plaintext}"},
    )
    assert after.status_code == 401
    assert after.json()["error"]["type"] == "authentication_error"
