"""Integration tests for ``/v1/auth/2fa/*`` endpoints (Sprint 6, Блок 6)."""

from __future__ import annotations

import json
import uuid

import pyotp
import pytest

from voltari_gateway.auth import rate_limit as rl
from voltari_gateway.auth.password import hash_password
from voltari_gateway.auth.totp import (
    decrypt_secret,
)
from voltari_gateway.db.models import (
    Account,
    AccountStatus,
    Tariff,
    User,
)


async def _make_user(db, *, password: str = "correct horse battery") -> User:
    user = User(
        email=f"user-{uuid.uuid4().hex[:10]}@example.com",
        password_hash=hash_password(password),
        email_verified=True,
    )
    db.add(user)
    await db.flush()
    db.add(
        Account(
            owner_id=user.id,
            name="Test acc",
            balance_kopecks=0,
            tariff=Tariff.PAYG,
            status=AccountStatus.ACTIVE,
            store_prompts=True,
        )
    )
    await db.commit()
    await db.refresh(user)
    return user


async def _login(client, user: User, password: str = "correct horse battery") -> dict:
    r = await client.post(
        "/v1/auth/login",
        json={"email": user.email, "password": password},
    )
    assert r.status_code == 200, r.text
    return r.json()


async def _csrf(client) -> dict[str, str]:
    r = await client.get("/v1/auth/csrf")
    return {"X-CSRF-Token": r.json()["csrf_token"]}


# ---------------------------------------------------------------------------
# /setup
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_2fa_setup_returns_qr_and_recovery_codes(client, db, redis_client):
    user = await _make_user(db)
    await _login(client, user)

    r = await client.post("/v1/auth/2fa/setup", headers=await _csrf(client))
    assert r.status_code == 200, r.text
    body = r.json()
    assert "secret" in body and len(body["secret"]) == 32
    assert body["provisioning_uri"].startswith("otpauth://")
    assert len(body["recovery_codes"]) == 8
    assert body["expires_in"] >= 60

    # Pending state in Redis but user.totp_enabled still False.
    pending_raw = await redis_client.get(f"totp:setup:{user.id}")
    assert pending_raw is not None
    pending = json.loads(pending_raw)
    assert pending["secret_b32"] == body["secret"]
    await db.refresh(user)
    assert user.totp_enabled is False


@pytest.mark.asyncio
async def test_2fa_setup_rejected_if_already_enabled(client, db):
    """A logged-in user with 2FA already enabled cannot re-enroll."""
    user = await _make_user(db)
    await _login(client, user)
    # Enable 2FA via the actual flow (which keeps the session alive).
    setup = (await client.post("/v1/auth/2fa/setup", headers=await _csrf(client))).json()
    code = pyotp.TOTP(setup["secret"]).now()
    r = await client.post(
        "/v1/auth/2fa/verify",
        json={"code": code},
        headers=await _csrf(client),
    )
    assert r.status_code == 200

    # Second attempt to /setup must be rejected.
    r2 = await client.post("/v1/auth/2fa/setup", headers=await _csrf(client))
    assert r2.status_code == 400
    assert r2.json()["error"]["code"] == "2fa_already_enabled"


# ---------------------------------------------------------------------------
# /verify
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_2fa_verify_enables_with_correct_code(client, db, redis_client):
    user = await _make_user(db)
    await _login(client, user)
    setup = (await client.post("/v1/auth/2fa/setup", headers=await _csrf(client))).json()
    code = pyotp.TOTP(setup["secret"]).now()

    r = await client.post(
        "/v1/auth/2fa/verify",
        json={"code": code},
        headers=await _csrf(client),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["enabled"] is True
    assert body["recovery_codes_remaining"] == 8

    await db.refresh(user)
    assert user.totp_enabled is True
    assert user.totp_secret_encrypted is not None
    assert decrypt_secret(user.totp_secret_encrypted) == setup["secret"]
    # Pending wiped from Redis.
    assert await redis_client.get(f"totp:setup:{user.id}") is None


@pytest.mark.asyncio
async def test_2fa_verify_wrong_code_returns_400(client, db):
    user = await _make_user(db)
    await _login(client, user)
    await client.post("/v1/auth/2fa/setup", headers=await _csrf(client))
    r = await client.post(
        "/v1/auth/2fa/verify",
        json={"code": "000000"},
        headers=await _csrf(client),
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "invalid_totp"


@pytest.mark.asyncio
async def test_2fa_verify_410_when_setup_expired(client, db, redis_client):
    user = await _make_user(db)
    await _login(client, user)
    # Don't run setup — verify against empty Redis state.
    r = await client.post(
        "/v1/auth/2fa/verify",
        json={"code": "123456"},
        headers=await _csrf(client),
    )
    assert r.status_code == 410
    assert r.json()["error"]["code"] == "2fa_setup_expired"


# ---------------------------------------------------------------------------
# Lockout — rate_limit integration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_2fa_verify_locks_after_5_failures(client, db):
    user = await _make_user(db)
    await _login(client, user)
    await client.post("/v1/auth/2fa/setup", headers=await _csrf(client))
    for _ in range(rl.TOTP_MAX_FAILURES):
        await client.post(
            "/v1/auth/2fa/verify",
            json={"code": "000000"},
            headers=await _csrf(client),
        )
    # 6-th attempt → 429
    r = await client.post(
        "/v1/auth/2fa/verify",
        json={"code": "000000"},
        headers=await _csrf(client),
    )
    assert r.status_code == 429
    assert r.json()["error"]["code"] == "totp_locked"
    assert "Retry-After" in r.headers


# ---------------------------------------------------------------------------
# /disable
# ---------------------------------------------------------------------------


async def _enable_2fa(client, db, user, password="correct horse battery") -> str:
    """End-to-end enroll + return the active secret."""
    setup = (await client.post("/v1/auth/2fa/setup", headers=await _csrf(client))).json()
    code = pyotp.TOTP(setup["secret"]).now()
    r = await client.post(
        "/v1/auth/2fa/verify",
        json={"code": code},
        headers=await _csrf(client),
    )
    assert r.status_code == 200, r.text
    return setup["secret"]


@pytest.mark.asyncio
async def test_2fa_disable_with_password_and_totp(client, db, redis_client):
    user = await _make_user(db)
    await _login(client, user)
    secret = await _enable_2fa(client, db, user)

    # /verify above consumed a code → drop the replay-set so we can use a
    # fresh code without sleeping 31 seconds.
    async for key in redis_client.scan_iter(match=f"totp:used:{user.id}:*"):
        await redis_client.delete(key)

    code = pyotp.TOTP(secret).now()
    r = await client.post(
        "/v1/auth/2fa/disable",
        json={"password": "correct horse battery", "code": code},
        headers=await _csrf(client),
    )
    assert r.status_code == 200, r.text
    assert r.json() == {"enabled": False}

    await db.refresh(user)
    assert user.totp_enabled is False
    assert user.totp_secret_encrypted is None


@pytest.mark.asyncio
async def test_2fa_disable_wrong_password_401(client, db):
    user = await _make_user(db)
    await _login(client, user)
    await _enable_2fa(client, db, user)

    r = await client.post(
        "/v1/auth/2fa/disable",
        json={"password": "wrong-password!!", "code": "111111"},
        headers=await _csrf(client),
    )
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# /recovery-codes/regenerate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_recovery_codes_regenerate_returns_8_fresh(client, db, redis_client):
    user = await _make_user(db)
    await _login(client, user)
    secret = await _enable_2fa(client, db, user)

    # /verify above consumed the current code → it lives in the replay set
    # for 90 s. Clear that key so we can submit the same code to regenerate
    # without falsely tripping replay detection.
    async for key in redis_client.scan_iter(match=f"totp:used:{user.id}:*"):
        await redis_client.delete(key)

    code = pyotp.TOTP(secret).now()
    r = await client.post(
        "/v1/auth/2fa/recovery-codes/regenerate",
        json={"password": "correct horse battery", "code": code},
        headers=await _csrf(client),
    )
    assert r.status_code == 200, r.text
    fresh = r.json()["recovery_codes"]
    assert len(fresh) == 8
    assert len(set(fresh)) == 8
