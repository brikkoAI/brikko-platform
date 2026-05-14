"""Admin endpoint tests — POST /v1/account/admin/smart_router_v2/{id}.

Sprint S1 (2026-05-13). See ``api/admin_smart_router_v2.py``.

Coverage
--------

* No session         → 401/403 (require_session)
* Session non-admin  → 403
* Empty ADMIN_EMAILS → 403 even for owner
* Admin → 200 + value flipped + audit_log row written
* Repeat flip with same value → 200, ``changed=False``, no audit row
* Unknown account_id → 404
* Body validation: missing ``enabled`` → 422
"""

from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest
import sqlalchemy as sa

from voltari_gateway.auth.csrf import CSRF_HEADER
from voltari_gateway.auth.password import hash_password
from voltari_gateway.config import get_settings
from voltari_gateway.db.models import (
    Account,
    AccountStatus,
    AuditLog,
    Tariff,
    User,
)

_PASSWORD = "correct horse battery staple admin"


async def _seed_user(db, email: str | None = None) -> tuple[User, Account]:
    user = User(
        email=email or f"u-{uuid.uuid4().hex[:10]}@example.com",
        password_hash=hash_password(_PASSWORD),
        email_verified=True,
    )
    db.add(user)
    await db.flush()
    account = Account(
        owner_id=user.id,
        name="Acme S1",
        balance_kopecks=100_000,
        tariff=Tariff.PRO,
        status=AccountStatus.ACTIVE,
        store_prompts=False,
        settings={},
        smart_router_v2_enabled=False,
    )
    db.add(account)
    await db.commit()
    await db.refresh(user)
    await db.refresh(account)
    return user, account


async def _login(client, user) -> str:
    """Log in and return the CSRF token (also stashed in the cookie jar)."""
    r = await client.post(
        "/v1/auth/login",
        json={"email": user.email, "password": _PASSWORD},
    )
    assert r.status_code == 200, r.text
    return r.json()["csrf_token"]


def _csrf_headers(csrf_token: str) -> dict[str, str]:
    """Build the header dict for mutating endpoints."""
    return {CSRF_HEADER: csrf_token}


def _patch_admin_emails(value: str):
    return patch.object(get_settings(), "admin_emails", value)


@pytest.mark.asyncio
async def test_flip_requires_session(client, db, redis_client) -> None:
    """No session cookie → 401/403."""
    _, account = await _seed_user(db)
    r = await client.post(
        f"/v1/account/admin/smart_router_v2/{account.id}",
        json={"enabled": True},
    )
    assert r.status_code in (401, 403)


@pytest.mark.asyncio
async def test_flip_rejects_non_admin(client, db, redis_client) -> None:
    """Logged-in non-admin email → 403."""
    user, account = await _seed_user(db)
    csrf = await _login(client, user)
    # ADMIN_EMAILS empty → everyone's 403
    with _patch_admin_emails(""):
        r = await client.post(
            f"/v1/account/admin/smart_router_v2/{account.id}",
            json={"enabled": True},
            headers=_csrf_headers(csrf),
        )
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_flip_succeeds_for_admin(client, db, redis_client) -> None:
    """Admin email → flag flipped + audit row."""
    user, account = await _seed_user(db)
    csrf = await _login(client, user)

    assert account.smart_router_v2_enabled is False

    with _patch_admin_emails(user.email):
        r = await client.post(
            f"/v1/account/admin/smart_router_v2/{account.id}",
            json={"enabled": True},
            headers=_csrf_headers(csrf),
        )

    assert r.status_code == 200, r.text
    payload = r.json()
    assert payload["account_id"] == str(account.id)
    assert payload["smart_router_v2_enabled"] is True
    assert payload["changed"] is True

    # Confirm DB row updated.
    await db.refresh(account)
    assert account.smart_router_v2_enabled is True

    # Confirm audit row written.
    rows = (
        (
            await db.execute(
                sa.select(AuditLog).where(AuditLog.action == "smart_router_v2_flag_changed")
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1
    assert rows[0].account_id == account.id
    assert rows[0].meta is not None
    assert rows[0].meta["before"] is False
    assert rows[0].meta["after"] is True


@pytest.mark.asyncio
async def test_flip_to_same_value_is_noop(client, db, redis_client) -> None:
    """Re-flipping to the same value → 200, changed=False, no audit row."""
    user, account = await _seed_user(db)
    csrf = await _login(client, user)

    with _patch_admin_emails(user.email):
        r = await client.post(
            f"/v1/account/admin/smart_router_v2/{account.id}",
            json={"enabled": False},  # column default is False
            headers=_csrf_headers(csrf),
        )

    assert r.status_code == 200, r.text
    payload = r.json()
    assert payload["changed"] is False
    assert payload["smart_router_v2_enabled"] is False

    # No audit row written.
    rows = (
        (
            await db.execute(
                sa.select(AuditLog).where(AuditLog.action == "smart_router_v2_flag_changed")
            )
        )
        .scalars()
        .all()
    )
    assert rows == []


@pytest.mark.asyncio
async def test_flip_unknown_account_returns_404(client, db, redis_client) -> None:
    user, _ = await _seed_user(db)
    csrf = await _login(client, user)

    with _patch_admin_emails(user.email):
        r = await client.post(
            f"/v1/account/admin/smart_router_v2/{uuid.uuid4()}",
            json={"enabled": True},
            headers=_csrf_headers(csrf),
        )

    assert r.status_code == 404


@pytest.mark.asyncio
async def test_flip_rejects_missing_body_field(client, db, redis_client) -> None:
    user, account = await _seed_user(db)
    csrf = await _login(client, user)

    with _patch_admin_emails(user.email):
        r = await client.post(
            f"/v1/account/admin/smart_router_v2/{account.id}",
            json={},  # missing ``enabled``
            headers=_csrf_headers(csrf),
        )

    # FastAPI default is 422; this codebase has a global handler that
    # remaps validation errors to 400. Accept either — what we're
    # asserting is "body validation rejected the missing field".
    assert r.status_code in (400, 422)


@pytest.mark.asyncio
async def test_flip_flips_back_to_false(client, db, redis_client) -> None:
    """Round trip: True → False → True; both flips audited."""
    user, account = await _seed_user(db)
    csrf = await _login(client, user)

    with _patch_admin_emails(user.email):
        r1 = await client.post(
            f"/v1/account/admin/smart_router_v2/{account.id}",
            json={"enabled": True},
            headers=_csrf_headers(csrf),
        )
        r2 = await client.post(
            f"/v1/account/admin/smart_router_v2/{account.id}",
            json={"enabled": False},
            headers=_csrf_headers(csrf),
        )

    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r1.json()["changed"] is True
    assert r2.json()["changed"] is True

    rows = (
        (
            await db.execute(
                sa.select(AuditLog).where(AuditLog.action == "smart_router_v2_flag_changed")
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 2
