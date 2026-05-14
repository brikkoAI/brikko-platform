"""Integration tests for the management auth API (`/v1/auth/*`).

Strategy:
- Real ASGI transport via httpx.AsyncClient (from the parent ``client`` fixture).
- In-memory SQLite (``engine``/``db``) shared with the app via the global
  session factory hook.
- fakeredis injected via ``set_redis``.
- Email backend stays ``console`` so we can monkeypatch ``send_email`` to
  capture outbound mail without spawning an SMTP server.

Cookie sharing: httpx ``AsyncClient`` ships with cookies enabled by default,
so cookies set by /login persist on subsequent calls inside the same test.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import select

from voltari_gateway.auth import rate_limit as rl
from voltari_gateway.auth.email_verification import (
    generate_password_reset_token,
    generate_verification_token,
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
    Transaction,
    User,
    WelcomeCreditsLog,
)
from voltari_gateway.email import client as email_client

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _capture_emails(monkeypatch) -> list[dict[str, str]]:
    """Replace ``send_email`` with an in-memory list collector."""
    captured: list[dict[str, str]] = []

    async def _fake_send(to: str, subject: str, body: str) -> None:
        captured.append({"to": to, "subject": subject, "body": body})

    monkeypatch.setattr(email_client, "send_email", _fake_send)
    # The api/auth.py module bound ``send_email`` at import time; patch there
    # too to make the swap visible.
    from voltari_gateway.api import auth as auth_api

    monkeypatch.setattr(auth_api, "send_email", _fake_send)
    return captured


async def _signup(client, email: str, password: str = "correct horse battery") -> dict[str, Any]:
    r = await client.post(
        "/v1/auth/signup",
        json={"email": email, "password": password},
    )
    return {"status": r.status_code, "body": r.json(), "headers": dict(r.headers)}


async def _verify_user_directly(db, user_id: uuid.UUID) -> None:
    """Set email_verified=True on a user — bypassing the API for fixtures
    that don't need to test the verification flow itself."""
    user = await db.get(User, user_id)
    assert user is not None
    user.email_verified = True
    user.verification_token = None
    await db.commit()


async def _make_user(db, *, password: str = "correct horse battery", verified: bool = True) -> User:
    """Create a user + primary account via the ORM (skips signup endpoint)."""
    user = User(
        email=f"user-{uuid.uuid4().hex[:10]}@example.com",
        password_hash=hash_password(password),
        email_verified=verified,
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


# ---------------------------------------------------------------------------
# 1) signup
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_signup_creates_user_and_account(client, db, monkeypatch):
    captured = _capture_emails(monkeypatch)
    email = f"signup-{uuid.uuid4().hex[:8]}@example.com"

    r = await client.post(
        "/v1/auth/signup",
        json={"email": email, "password": "correct horse battery"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["email"] == email
    assert body["verification_required"] is True
    assert uuid.UUID(body["user_id"])  # parses

    # User is in DB, unverified.
    result = await db.execute(select(User).where(User.email == email))
    user = result.scalar_one()
    assert user.email_verified is False
    assert user.verification_token is not None  # hash stored
    assert len(user.verification_token) == 64  # sha256 hex

    # Primary account exists, balance = 100 ₽ (anonymize welcome bonus per
    # BRIEF v2 §5 — separate from the 200 ₽ verify-email welcome, which
    # still only lands after /verify-email).
    acc = await db.execute(select(Account).where(Account.owner_id == user.id))
    account = acc.scalar_one()
    assert account.balance_kopecks == 10_000
    assert account.tariff == Tariff.PAYG

    # Verification email captured.
    assert any(e["to"] == email and "verify" in e["body"].lower() for e in captured)


@pytest.mark.asyncio
async def test_signup_duplicate_email_returns_409(client, db, monkeypatch):
    _capture_emails(monkeypatch)
    email = f"dup-{uuid.uuid4().hex[:8]}@example.com"

    r1 = await client.post(
        "/v1/auth/signup",
        json={"email": email, "password": "correct horse battery"},
    )
    assert r1.status_code == 200

    r2 = await client.post(
        "/v1/auth/signup",
        json={"email": email, "password": "another correct password"},
    )
    assert r2.status_code == 409
    body = r2.json()
    assert body["error"]["code"] == "email_taken"


@pytest.mark.asyncio
async def test_signup_weak_password_returns_400(client, monkeypatch):
    _capture_emails(monkeypatch)
    r = await client.post(
        "/v1/auth/signup",
        json={"email": f"x-{uuid.uuid4().hex[:6]}@example.com", "password": "short"},
    )
    assert r.status_code == 400
    body = r.json()
    assert body["error"]["type"] == "invalid_request_error"


@pytest.mark.asyncio
async def test_signup_rate_limit(client, monkeypatch):
    _capture_emails(monkeypatch)
    # Default limit = 5 / minute. Drive it past with single distinct emails.
    rl.reload_from_settings()
    for i in range(5):
        r = await client.post(
            "/v1/auth/signup",
            json={
                "email": f"rl-{i}-{uuid.uuid4().hex[:6]}@example.com",
                "password": "correct horse battery",
            },
        )
        assert r.status_code == 200, r.text

    # 6th hit from the same IP should 429.
    r6 = await client.post(
        "/v1/auth/signup",
        json={
            "email": f"rl-x-{uuid.uuid4().hex[:6]}@example.com",
            "password": "correct horse battery",
        },
    )
    assert r6.status_code == 429
    assert r6.json()["error"]["code"] == "rate_limited"


# ---------------------------------------------------------------------------
# 2) verify-email — welcome credit
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_verify_email_grants_welcome_credit(client, db, monkeypatch):
    _capture_emails(monkeypatch)
    email = f"v-{uuid.uuid4().hex[:8]}@example.com"

    s = await client.post(
        "/v1/auth/signup",
        json={"email": email, "password": "correct horse battery"},
    )
    user_id = uuid.UUID(s.json()["user_id"])

    # Re-mint the same token plaintext (deterministic given fixed secret).
    plain = generate_verification_token(user_id, email)

    v = await client.post("/v1/auth/verify-email", json={"token": plain})
    assert v.status_code == 200, v.text
    body = v.json()
    assert body["verified"] is True
    assert body["welcome_credit_kop"] == 20_000

    # Balance = 200 ₽ (verify-email credit) + 100 ₽ (anonymize signup bonus,
    # BRIEF v2 §5) = 300 ₽.
    result = await db.execute(select(Account).where(Account.owner_id == user_id))
    account = result.scalar_one()
    assert account.balance_kopecks == 30_000

    # Welcome row exists.
    log_row = await db.execute(select(WelcomeCreditsLog))
    assert log_row.scalar_one_or_none() is not None

    # Two TOPUP rows on the ledger now: the anonymize signup bonus
    # (ref_id welcome-anonymize:*) + the verify-email welcome (ref_id welcome:*).
    tx_rows = (
        (await db.execute(select(Transaction).where(Transaction.account_id == account.id)))
        .scalars()
        .all()
    )
    refs = {(r.ref_id or "") for r in tx_rows}
    assert any(ref.startswith("welcome:") for ref in refs)
    assert any(ref.startswith("welcome-anonymize:") for ref in refs)
    assert sum(r.amount_kopecks for r in tx_rows) == 30_000


@pytest.mark.asyncio
async def test_verify_email_idempotent(client, db, monkeypatch):
    """Second verify with a fresh token does NOT re-credit the bonus."""
    _capture_emails(monkeypatch)
    email = f"v2-{uuid.uuid4().hex[:8]}@example.com"

    s = await client.post(
        "/v1/auth/signup",
        json={"email": email, "password": "correct horse battery"},
    )
    user_id = uuid.UUID(s.json()["user_id"])
    token = generate_verification_token(user_id, email)

    v1 = await client.post("/v1/auth/verify-email", json={"token": token})
    assert v1.status_code == 200
    assert v1.json()["welcome_credit_kop"] == 20_000

    # Mint a *fresh* token to bypass our "consumed token" branch and exercise
    # the dedup log. The user is already email_verified so the second call
    # is a no-op credit-wise.
    token2 = generate_verification_token(user_id, email)
    v2 = await client.post("/v1/auth/verify-email", json={"token": token2})
    assert v2.status_code == 200
    assert v2.json()["welcome_credit_kop"] == 0

    result = await db.execute(select(Account).where(Account.owner_id == user_id))
    account = result.scalar_one()
    # 200 ₽ verify-email welcome (not doubled by the second verify) +
    # 100 ₽ anonymize signup bonus (BRIEF v2 §5) = 300 ₽.
    assert account.balance_kopecks == 30_000


@pytest.mark.asyncio
async def test_verify_email_invalid_token_returns_400(client):
    r = await client.post("/v1/auth/verify-email", json={"token": "not-a-real-token"})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "invalid_token"


@pytest.mark.asyncio
async def test_verify_email_expired_token_returns_400(client, db, monkeypatch):
    """Mint a token, then jump system clock forward past the TTL window."""
    _capture_emails(monkeypatch)
    email = f"vex-{uuid.uuid4().hex[:8]}@example.com"
    s = await client.post(
        "/v1/auth/signup",
        json={"email": email, "password": "correct horse battery"},
    )
    user_id = uuid.UUID(s.json()["user_id"])
    token = generate_verification_token(user_id, email)

    # itsdangerous reads `time.time()` for both signing & verification; jump
    # forward 25 h to push the token past EMAIL_VERIFICATION_TTL_HOURS=24.
    import time as _time

    real = _time.time()
    future = real + 25 * 3600
    monkeypatch.setattr(_time, "time", lambda: future)

    r = await client.post("/v1/auth/verify-email", json={"token": token})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "invalid_token"


# ---------------------------------------------------------------------------
# 3) login
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_login_returns_cookies(client, db, redis_client):
    user = await _make_user(db, verified=True)

    r = await client.post(
        "/v1/auth/login",
        json={"email": user.email, "password": "correct horse battery"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["user_id"] == str(user.id)
    assert "expires_at" in body

    set_cookies = (
        r.headers.get_list("set-cookie")
        if hasattr(r.headers, "get_list")
        else r.headers.get("set-cookie", "")
    )
    if isinstance(set_cookies, str):
        set_cookies = [set_cookies]
    blob = "\n".join(set_cookies)
    assert "vlt_access" in blob
    assert "vlt_refresh" in blob
    assert "HttpOnly" in blob


@pytest.mark.asyncio
async def test_login_rejects_unverified_email(client, db):
    user = await _make_user(db, verified=False)

    r = await client.post(
        "/v1/auth/login",
        json={"email": user.email, "password": "correct horse battery"},
    )
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "email_not_verified"


@pytest.mark.asyncio
async def test_login_rejects_wrong_password(client, db):
    user = await _make_user(db, verified=True)

    r = await client.post(
        "/v1/auth/login",
        json={"email": user.email, "password": "definitely wrong password"},
    )
    assert r.status_code == 401
    assert r.json()["error"]["type"] == "authentication_error"


# ---------------------------------------------------------------------------
# 4) logout
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_logout_revokes_refresh_in_redis(client, db, redis_client):
    user = await _make_user(db, verified=True)

    r = await client.post(
        "/v1/auth/login",
        json={"email": user.email, "password": "correct horse battery"},
    )
    assert r.status_code == 200

    # Identify which JTI was set — read from cookies.
    refresh_cookie = client.cookies.get("vlt_refresh")
    assert refresh_cookie is not None
    from voltari_gateway.auth.session import verify_refresh_token

    claims = verify_refresh_token(refresh_cookie)
    assert claims is not None
    assert await is_refresh_token_active(redis_client, claims.user_id, claims.jti)

    out = await client.post("/v1/auth/logout")
    assert out.status_code == 200
    assert await is_refresh_token_active(redis_client, claims.user_id, claims.jti) is False


# ---------------------------------------------------------------------------
# 5) refresh
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_refresh_rotates_jti(client, db, redis_client):
    user = await _make_user(db, verified=True)

    await client.post(
        "/v1/auth/login",
        json={"email": user.email, "password": "correct horse battery"},
    )
    old_refresh = client.cookies.get("vlt_refresh")
    from voltari_gateway.auth.session import verify_refresh_token

    old_claims = verify_refresh_token(old_refresh)
    assert old_claims is not None

    r = await client.post("/v1/auth/refresh")
    assert r.status_code == 200, r.text

    new_refresh = client.cookies.get("vlt_refresh")
    new_claims = verify_refresh_token(new_refresh)
    assert new_claims is not None
    assert new_claims.jti != old_claims.jti

    # Old JTI revoked; new one active.
    assert await is_refresh_token_active(redis_client, old_claims.user_id, old_claims.jti) is False
    assert await is_refresh_token_active(redis_client, new_claims.user_id, new_claims.jti) is True


@pytest.mark.asyncio
async def test_refresh_with_revoked_jti_returns_401(client, db, redis_client):
    user = await _make_user(db, verified=True)
    await client.post(
        "/v1/auth/login",
        json={"email": user.email, "password": "correct horse battery"},
    )
    refresh_cookie = client.cookies.get("vlt_refresh")
    from voltari_gateway.auth.session import (
        revoke_refresh_token,
        verify_refresh_token,
    )

    claims = verify_refresh_token(refresh_cookie)
    await revoke_refresh_token(redis_client, claims.user_id, claims.jti)

    r = await client.post("/v1/auth/refresh")
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# 6) forgot-password
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_forgot_password_always_returns_200(client):
    r = await client.post(
        "/v1/auth/forgot-password",
        json={"email": f"nobody-{uuid.uuid4().hex[:6]}@example.com"},
    )
    assert r.status_code == 200
    assert r.json() == {}


@pytest.mark.asyncio
async def test_forgot_password_sends_email_when_user_exists(client, db, monkeypatch):
    captured = _capture_emails(monkeypatch)
    user = await _make_user(db, verified=True)

    r = await client.post("/v1/auth/forgot-password", json={"email": user.email})
    assert r.status_code == 200

    assert any(e["to"] == user.email and "сброс" in e["body"].lower() for e in captured)

    # password_reset_token hash stored. Re-read with a fresh query so we
    # don't hit the session's identity-map cache from the seeding phase.
    fresh = await db.execute(select(User).where(User.id == user.id))
    refreshed = fresh.scalar_one()
    await db.refresh(refreshed)
    assert refreshed.password_reset_token is not None


@pytest.mark.asyncio
async def test_forgot_password_no_email_when_user_not_exists(client, monkeypatch):
    captured = _capture_emails(monkeypatch)

    r = await client.post(
        "/v1/auth/forgot-password",
        json={"email": f"ghost-{uuid.uuid4().hex[:8]}@example.com"},
    )
    assert r.status_code == 200
    assert captured == []


# ---------------------------------------------------------------------------
# 7) reset-password
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reset_password_revokes_all_refresh_tokens(client, db, redis_client):
    user = await _make_user(db, verified=True)

    # Issue two refresh tokens (simulate two devices logged in).
    _, exp_a, jti_a = create_refresh_token(user.id)
    _, exp_b, jti_b = create_refresh_token(user.id)
    await register_refresh_token(redis_client, user.id, jti_a, exp_a)
    await register_refresh_token(redis_client, user.id, jti_b, exp_b)

    # Plant a reset token + DB hash.
    plain = generate_password_reset_token(user.id)
    user.password_reset_token = hash_token(plain)
    await db.commit()

    r = await client.post(
        "/v1/auth/reset-password",
        json={"token": plain, "new_password": "brand new password!"},
    )
    assert r.status_code == 200, r.text

    # Both refresh JTIs gone.
    assert not await is_refresh_token_active(redis_client, user.id, jti_a)
    assert not await is_refresh_token_active(redis_client, user.id, jti_b)

    # New password works.
    login_r = await client.post(
        "/v1/auth/login",
        json={"email": user.email, "password": "brand new password!"},
    )
    assert login_r.status_code == 200


# ---------------------------------------------------------------------------
# 8) change-password
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_change_password_requires_session(client):
    """No cookies + no CSRF header → 401 (or 403 for missing CSRF)."""
    r = await client.post(
        "/v1/auth/change-password",
        json={"old_password": "x" * 8, "new_password": "y" * 12},
    )
    assert r.status_code in (401, 403)


@pytest.mark.asyncio
async def test_change_password_revokes_other_sessions_keeps_current(client, db, redis_client):
    """Sprint 6 Блок 12 — change-password preserves the caller's own session
    (so the SPA doesn't bounce to /login) and revokes every OTHER device.
    """
    user = await _make_user(db, verified=True)

    # Login → cookies set + Postgres ``sessions`` row created for caller.
    await client.post(
        "/v1/auth/login",
        json={"email": user.email, "password": "correct horse battery"},
    )
    refresh_cookie = client.cookies.get("vlt_refresh")
    from voltari_gateway.auth.session import verify_refresh_token
    from voltari_gateway.auth.sessions_store import (
        record_session,
    )

    claims = verify_refresh_token(refresh_cookie)

    # Issue one extra "other-device" JTI + mirror in Postgres so the
    # change-password handler sees it via ``revoke_all_other_sessions``.
    _, exp_other, jti_other = create_refresh_token(user.id)
    await register_refresh_token(redis_client, user.id, jti_other, exp_other)
    await record_session(
        db,
        user_id=user.id,
        refresh_jti=jti_other,
        expires_at=exp_other,
    )
    await db.commit()

    # TD-036: legacy CSRF removed; mint a double-submit token.
    csrf_resp = await client.get("/v1/auth/csrf")
    csrf_token = csrf_resp.json()["csrf_token"]
    r = await client.post(
        "/v1/auth/change-password",
        json={
            "old_password": "correct horse battery",
            "new_password": "brand new password!",
        },
        headers={"X-CSRF-Token": csrf_token},
    )
    assert r.status_code == 200, r.text
    assert r.json()["revoked_other_sessions"] == 1

    # Caller's own JTI is still valid; other-device JTI revoked.
    assert await is_refresh_token_active(redis_client, user.id, claims.jti)
    assert not await is_refresh_token_active(redis_client, user.id, jti_other)

    # Old password no longer works.
    r_old = await client.post(
        "/v1/auth/login",
        json={"email": user.email, "password": "correct horse battery"},
    )
    assert r_old.status_code == 401
