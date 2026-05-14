"""Sprint 3 Поток H — full reset-password flow tests.

Augments the basic happy-path coverage in ``test_api_auth.py`` with the
edge cases that the audit (QA P1-14) flagged as missing:

* ``forgot_password`` with EMAIL_BACKEND=console actually queues an email
  for an existing verified user (smoke).
* ``reset_password`` with a token whose signature is valid but DB hash
  was wiped (e.g. older link superseded) → 400.
* Replay attack: using the same valid token twice — second call must be
  rejected (single-use).
* Expired token (forged with TTL=0) → 400.
* Wrong-email path: ``/forgot-password`` with an unknown email returns
  200 silently (no enumeration leak).
* On reset, ALL active refresh JTIs are revoked (already in
  test_api_auth, but we re-pin it here as the canonical reset
  invariant).

These are the contract tests the security review will look at when we
move to Stripe quality gate.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from unittest.mock import patch

import pytest
from sqlalchemy import select

from voltari_gateway.auth.email_verification import (
    generate_password_reset_token,
    hash_token,
)
from voltari_gateway.auth.password import hash_password
from voltari_gateway.auth.session import (
    create_refresh_token,
    is_refresh_token_active,
    register_refresh_token,
)
from voltari_gateway.db.models import (
    Account,
    AccountStatus,
    Tariff,
    User,
)

_PASSWORD = "correct horse battery staple"
_NEW_PASSWORD = "fresh new password 1234"


async def _make_user(db, *, verified: bool = True) -> User:
    user = User(
        email=f"reset-{uuid.uuid4().hex[:10]}@example.com",
        password_hash=hash_password(_PASSWORD),
        email_verified=verified,
    )
    db.add(user)
    await db.flush()
    db.add(
        Account(
            owner_id=user.id,
            name="Reset acc",
            balance_kopecks=0,
            tariff=Tariff.PAYG,
            status=AccountStatus.ACTIVE,
            store_prompts=True,
            settings={},
        )
    )
    await db.commit()
    await db.refresh(user)
    return user


# ---------- /forgot-password ----------


@pytest.mark.asyncio
async def test_forgot_password_wrong_email_returns_200_silently(client, monkeypatch):
    """Unknown email → 200, no email sent. Anti-enumeration invariant."""
    captured: list[dict[str, str]] = []

    async def _fake_send(to: str, subject: str, body: str) -> None:
        captured.append({"to": to})

    from voltari_gateway.email import client as email_client

    monkeypatch.setattr(email_client, "send_email", _fake_send)

    r = await client.post(
        "/v1/auth/forgot-password",
        json={"email": f"ghost-{uuid.uuid4().hex[:8]}@example.com"},
    )
    assert r.status_code == 200
    # Body is a sentinel object, no information leak.
    assert r.json() == {}
    # No email queued.
    assert captured == []


@pytest.mark.asyncio
async def test_forgot_password_creates_hashed_token_in_db(client, db, monkeypatch):
    """For an existing verified user, /forgot-password persists a HMAC-hashed
    token (NOT the plaintext) and emits the email."""
    captured: list[dict[str, str]] = []

    async def _fake_send(to: str, subject: str, body: str) -> None:
        captured.append({"to": to, "subject": subject, "body": body})

    # Patch in BOTH the email module AND the api/auth module — the latter
    # rebinds ``send_email`` at import time, so the email-module-only swap
    # would miss the actual call site.
    from voltari_gateway.api import auth as auth_api
    from voltari_gateway.email import client as email_client

    monkeypatch.setattr(email_client, "send_email", _fake_send)
    monkeypatch.setattr(auth_api, "send_email", _fake_send)

    user = await _make_user(db, verified=True)
    r = await client.post("/v1/auth/forgot-password", json={"email": user.email})
    assert r.status_code == 200

    # Email queued.
    assert any(e["to"] == user.email for e in captured)

    # DB row has the hash, not plaintext.
    fresh = await db.execute(select(User).where(User.id == user.id))
    refreshed = fresh.scalar_one()
    await db.refresh(refreshed)
    assert refreshed.password_reset_token is not None
    # Hash is HMAC-SHA-256 hex (64 chars), not an itsdangerous token.
    assert len(refreshed.password_reset_token) == 64
    assert refreshed.password_reset_token.isalnum()


# ---------- /reset-password edge cases ----------


@pytest.mark.asyncio
async def test_reset_password_replay_attack_rejected(client, db, redis_client):
    """Single-use token: second call with the same plaintext → 400."""
    user = await _make_user(db, verified=True)

    plain = generate_password_reset_token(user.id)
    user.password_reset_token = hash_token(plain)
    await db.commit()

    # First use — succeeds.
    r1 = await client.post(
        "/v1/auth/reset-password",
        json={"token": plain, "new_password": _NEW_PASSWORD},
    )
    assert r1.status_code == 200, r1.text

    # Second use — must be rejected. The handler clears
    # ``password_reset_token`` after successful reset, so the DB-hash
    # comparison fails on the replay.
    r2 = await client.post(
        "/v1/auth/reset-password",
        json={"token": plain, "new_password": "another-different-password"},
    )
    assert r2.status_code == 400
    assert r2.json()["error"]["code"] == "invalid_token"

    # Sanity: the *first* new password still works.
    login_r = await client.post(
        "/v1/auth/login",
        json={"email": user.email, "password": _NEW_PASSWORD},
    )
    assert login_r.status_code == 200


@pytest.mark.asyncio
async def test_reset_password_unknown_token_rejected(client, db):
    """A signature-valid token for a user that doesn't exist → 400.

    Forge a token for a random UUID; its signature is valid but no user
    has it stored. The DB-hash check catches this.
    """
    fake_user_id = uuid.uuid4()
    forged = generate_password_reset_token(fake_user_id)

    r = await client.post(
        "/v1/auth/reset-password",
        json={"token": forged, "new_password": _NEW_PASSWORD},
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "invalid_token"


@pytest.mark.asyncio
async def test_reset_password_token_with_no_db_hash_rejected(client, db):
    """User exists but ``password_reset_token`` column is NULL (e.g. they
    never asked for a reset) → 400. Defends against an attacker who
    somehow got a signature-valid token but the user superseded it via a
    fresh /forgot-password → /reset-password cycle.
    """
    user = await _make_user(db, verified=True)
    plain = generate_password_reset_token(user.id)
    # NB: do NOT set ``user.password_reset_token``.

    r = await client.post(
        "/v1/auth/reset-password",
        json={"token": plain, "new_password": _NEW_PASSWORD},
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "invalid_token"


@pytest.mark.asyncio
async def test_reset_password_garbage_token_rejected(client):
    """Random non-token string → 400. itsdangerous BadData path."""
    r = await client.post(
        "/v1/auth/reset-password",
        json={"token": "not-a-real-token-xyz", "new_password": _NEW_PASSWORD},
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "invalid_token"


@pytest.mark.asyncio
async def test_reset_password_expired_token_rejected(client, db, monkeypatch):
    """Token issued past the TTL window → 400.

    We mint a token at a frozen "past" timestamp, then verify the
    handler rejects it. itsdangerous compares signature timestamp to
    ``time.time()`` so we patch the time used at sign-time to be in the
    distant past.
    """
    user = await _make_user(db, verified=True)

    # Make the signing time T-1 day from now. ``URLSafeTimedSerializer``
    # reads ``time.time()`` at sign time — patching it produces an
    # already-stale token that the verify call (using real time) will
    # reject as expired.
    one_day_ago = time.time() - 24 * 3600 * 2  # 2 days back; TTL is 30min default

    with patch("itsdangerous.timed.time.time", return_value=one_day_ago):
        plain = generate_password_reset_token(user.id)

    user.password_reset_token = hash_token(plain)
    await db.commit()

    r = await client.post(
        "/v1/auth/reset-password",
        json={"token": plain, "new_password": _NEW_PASSWORD},
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "invalid_token"


@pytest.mark.asyncio
async def test_reset_password_revokes_every_refresh_jti(client, db, redis_client):
    """All active refresh JTIs (across devices) are killed on successful reset.

    Pin in this dedicated test so the contract is loud and obvious — a
    stolen refresh-cookie cannot survive a password reset.
    """
    user = await _make_user(db, verified=True)

    n_devices = 4
    jtis: list[tuple[str, int]] = []
    for _ in range(n_devices):
        _, exp, jti = create_refresh_token(user.id)
        await register_refresh_token(redis_client, user.id, jti, exp)
        jtis.append((jti, exp))
    # Sanity precondition: all JTIs active.
    for jti, _ in jtis:
        assert await is_refresh_token_active(redis_client, user.id, jti)

    plain = generate_password_reset_token(user.id)
    user.password_reset_token = hash_token(plain)
    await db.commit()

    r = await client.post(
        "/v1/auth/reset-password",
        json={"token": plain, "new_password": _NEW_PASSWORD},
    )
    assert r.status_code == 200, r.text

    # All JTIs revoked.
    for jti, _ in jtis:
        # Allow a tiny propagation slack for fakeredis.
        for _ in range(3):
            if not await is_refresh_token_active(redis_client, user.id, jti):
                break
            await asyncio.sleep(0.01)
        assert not await is_refresh_token_active(redis_client, user.id, jti)


@pytest.mark.asyncio
async def test_reset_password_short_password_rejected(client, db):
    """Password validator runs after token check; short password → 400."""
    user = await _make_user(db, verified=True)
    plain = generate_password_reset_token(user.id)
    user.password_reset_token = hash_token(plain)
    await db.commit()

    r = await client.post(
        "/v1/auth/reset-password",
        json={"token": plain, "new_password": "short"},  # too short
    )
    assert r.status_code == 400
