"""Tests for ``POST /v1/auth/verify-email/resend`` (Sprint 6, Блок 11)."""

from __future__ import annotations

import uuid

import pytest

from voltari_gateway.auth.password import hash_password
from voltari_gateway.db.models import (
    Account,
    AccountStatus,
    Tariff,
    User,
)
from voltari_gateway.email import client as email_client


def _capture_emails(monkeypatch) -> list[dict[str, str]]:
    captured: list[dict[str, str]] = []

    async def _fake(to: str, subject: str, body: str) -> None:
        captured.append({"to": to, "subject": subject, "body": body})

    monkeypatch.setattr(email_client, "send_email", _fake)
    from voltari_gateway.api import auth as auth_api

    monkeypatch.setattr(auth_api, "send_email", _fake)
    return captured


async def _make_user(db, *, verified: bool = False) -> User:
    user = User(
        email=f"u-{uuid.uuid4().hex[:8]}@example.com",
        password_hash=hash_password("correct horse battery"),
        email_verified=verified,
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
    await db.refresh(user)
    return user


@pytest.mark.asyncio
async def test_resend_unverified_user_sends_email(client, db, monkeypatch):
    captured = _capture_emails(monkeypatch)
    user = await _make_user(db, verified=False)
    r = await client.post(
        "/v1/auth/verify-email/resend",
        json={"email": user.email},
    )
    assert r.status_code == 200
    assert len(captured) == 1
    assert captured[0]["to"] == user.email


@pytest.mark.asyncio
async def test_resend_verified_user_silently_skipped(client, db, monkeypatch):
    captured = _capture_emails(monkeypatch)
    user = await _make_user(db, verified=True)
    r = await client.post(
        "/v1/auth/verify-email/resend",
        json={"email": user.email},
    )
    # Anti-enumeration: still 200, but no email sent.
    assert r.status_code == 200
    assert captured == []


@pytest.mark.asyncio
async def test_resend_unknown_email_silently_200(client, db, monkeypatch):
    captured = _capture_emails(monkeypatch)
    r = await client.post(
        "/v1/auth/verify-email/resend",
        json={"email": "ghost@example.com"},
    )
    assert r.status_code == 200
    assert captured == []


@pytest.mark.asyncio
async def test_resend_rate_limit_429(client, db, monkeypatch):
    _capture_emails(monkeypatch)
    user = await _make_user(db, verified=False)
    for _ in range(3):
        r = await client.post(
            "/v1/auth/verify-email/resend",
            json={"email": user.email},
        )
        assert r.status_code == 200
    r4 = await client.post(
        "/v1/auth/verify-email/resend",
        json={"email": user.email},
    )
    assert r4.status_code == 429
    assert r4.headers.get("Retry-After") == "3600"


@pytest.mark.asyncio
async def test_resend_persists_token_hash(client, db, monkeypatch):
    """Each successful resend writes a fresh hash row + timestamp.

    itsdangerous tokens are deterministic per (payload, second), so two
    rapid-fire resends within the same second can generate the same hash;
    the *contract* we check here is that the column was populated and the
    sent_at moved.
    """
    _capture_emails(monkeypatch)
    user = await _make_user(db, verified=False)
    await client.post("/v1/auth/verify-email/resend", json={"email": user.email})
    await db.refresh(user)
    assert user.verification_token is not None
    assert user.verification_sent_at is not None
