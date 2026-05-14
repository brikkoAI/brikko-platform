"""Tests for the 2FA-aware login flow (Sprint 6, Блок 7).

* /login on a 2FA-enabled user must NOT issue cookies; returns
  ``status: 2fa_required`` + ``pre_auth_token``.
* /login/2fa with a valid TOTP code completes the session.
* Recovery codes work and decrement the available list.
"""

from __future__ import annotations

import uuid

import pyotp
import pytest

from voltari_gateway.auth.password import hash_password
from voltari_gateway.auth.totp import (
    encrypt_secret,
    generate_recovery_codes,
    generate_totp_secret,
    hash_recovery_code,
)
from voltari_gateway.db.models import (
    Account,
    AccountStatus,
    Tariff,
    User,
)


async def _make_user_with_2fa(
    db, *, password: str = "correct horse battery"
) -> tuple[User, str, list[str]]:
    """Create a user already enrolled in 2FA. Returns (user, secret, recovery_codes)."""
    secret = generate_totp_secret()
    recovery_plain = generate_recovery_codes()
    user = User(
        email=f"user-{uuid.uuid4().hex[:10]}@example.com",
        password_hash=hash_password(password),
        email_verified=True,
        totp_secret_encrypted=encrypt_secret(secret),
        totp_enabled=True,
        totp_recovery_codes_hashed=[hash_recovery_code(c) for c in recovery_plain],
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
    return user, secret, recovery_plain


@pytest.mark.asyncio
async def test_login_with_2fa_returns_pre_auth_token(client, db):
    user, _secret, _ = await _make_user_with_2fa(db)
    r = await client.post(
        "/v1/auth/login",
        json={"email": user.email, "password": "correct horse battery"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "2fa_required"
    assert "pre_auth_token" in body
    assert "expires_at" in body
    # No session cookie set yet.
    assert client.cookies.get("vlt_access") is None


@pytest.mark.asyncio
async def test_login_2fa_with_correct_code_issues_session(client, db):
    user, secret, _ = await _make_user_with_2fa(db)
    r = await client.post(
        "/v1/auth/login",
        json={"email": user.email, "password": "correct horse battery"},
    )
    pre_auth = r.json()["pre_auth_token"]
    code = pyotp.TOTP(secret).now()
    r2 = await client.post(
        "/v1/auth/login/2fa",
        json={"pre_auth_token": pre_auth, "code": code},
    )
    assert r2.status_code == 200, r2.text
    body = r2.json()
    assert body["status"] == "authenticated"
    assert body["factor_used"] == "totp"
    assert client.cookies.get("vlt_access") is not None
    assert client.cookies.get("vlt_refresh") is not None


@pytest.mark.asyncio
async def test_login_2fa_with_recovery_code_decrements(client, db):
    user, _secret, recovery = await _make_user_with_2fa(db)
    r = await client.post(
        "/v1/auth/login",
        json={"email": user.email, "password": "correct horse battery"},
    )
    pre_auth = r.json()["pre_auth_token"]
    r2 = await client.post(
        "/v1/auth/login/2fa",
        json={"pre_auth_token": pre_auth, "recovery_code": recovery[0]},
    )
    assert r2.status_code == 200, r2.text
    assert r2.json()["factor_used"] == "recovery"
    assert r2.json()["recovery_codes_remaining"] == 7

    await db.refresh(user)
    assert len(user.totp_recovery_codes_hashed) == 7
    # The used hash is gone.
    assert hash_recovery_code(recovery[0]) not in user.totp_recovery_codes_hashed


@pytest.mark.asyncio
async def test_login_2fa_invalid_code_400(client, db):
    user, _secret, _ = await _make_user_with_2fa(db)
    r = await client.post(
        "/v1/auth/login",
        json={"email": user.email, "password": "correct horse battery"},
    )
    pre_auth = r.json()["pre_auth_token"]
    r2 = await client.post(
        "/v1/auth/login/2fa",
        json={"pre_auth_token": pre_auth, "code": "000000"},
    )
    assert r2.status_code == 400
    assert r2.json()["error"]["code"] == "invalid_totp"


@pytest.mark.asyncio
async def test_login_2fa_invalid_pre_auth_token_401(client, db):
    r = await client.post(
        "/v1/auth/login/2fa",
        json={"pre_auth_token": "garbage.garbage.garbage", "code": "000000"},
    )
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_login_2fa_missing_factor_400(client, db):
    user, _secret, _ = await _make_user_with_2fa(db)
    r = await client.post(
        "/v1/auth/login",
        json={"email": user.email, "password": "correct horse battery"},
    )
    pre_auth = r.json()["pre_auth_token"]
    r2 = await client.post(
        "/v1/auth/login/2fa",
        json={"pre_auth_token": pre_auth},
    )
    assert r2.status_code == 400
    assert r2.json()["error"]["code"] == "totp_or_recovery_required"


@pytest.mark.asyncio
async def test_login_without_2fa_status_authenticated(client, db):
    """Sanity: standard (non-2FA) login keeps the old contract + adds status."""
    user = User(
        email=f"u-{uuid.uuid4().hex[:8]}@example.com",
        password_hash=hash_password("correct horse battery"),
        email_verified=True,
    )
    db.add(user)
    await db.flush()
    db.add(
        Account(
            owner_id=user.id,
            name="x",
            balance_kopecks=0,
            tariff=Tariff.PAYG,
            status=AccountStatus.ACTIVE,
            store_prompts=True,
        )
    )
    await db.commit()
    r = await client.post(
        "/v1/auth/login",
        json={"email": user.email, "password": "correct horse battery"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "authenticated"
    assert "csrf_token" in body
    assert client.cookies.get("vlt_access") is not None
