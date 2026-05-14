"""Integration tests for seats / invites — the team management API.

Covers:

* GET /v1/account/seats — list owner + members.
* POST /v1/account/seats/invite — happy path, role validation, idempotent
  re-send, already-member 409, member-cannot-invite 403.
* GET /v1/account/invites — only pending, non-expired.
* DELETE /v1/account/invites/{id} — revoke.
* POST /v1/account/seats/accept — anonymous; existing-user, new-user (welcome
  credit), expired, already-accepted.
* DELETE /v1/account/seats/{user_id} — owner removes member, admin removes
  member, member cannot remove, owner cannot remove self.

Strategy mirrors test_api_auth / test_api_account: ASGITransport client,
in-memory SQLite, fakeredis, captured email backend.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from voltari_gateway.auth.password import hash_password
from voltari_gateway.db.models import (
    Account,
    AccountStatus,
    EmailInvite,
    Seat,
    SeatRole,
    Tariff,
    Transaction,
    User,
    WelcomeCreditsLog,
)
from voltari_gateway.email import client as email_client

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_PASSWORD = "correct horse battery staple"

# TD-036 (Sprint 3 Поток H) — legacy CSRF fallback removed.
from tests.auth.conftest import csrf_headers as _csrf_headers  # noqa: E402


def _capture_emails(monkeypatch) -> list[dict[str, str]]:
    """Replace ``send_email`` with an in-memory list collector everywhere it's
    bound at import time (api/auth.py, api/account.py, api/seats.py)."""
    captured: list[dict[str, str]] = []

    async def _fake_send(to: str, subject: str, body: str) -> None:
        captured.append({"to": to, "subject": subject, "body": body})

    monkeypatch.setattr(email_client, "send_email", _fake_send)
    from voltari_gateway.api import account as account_api
    from voltari_gateway.api import auth as auth_api
    from voltari_gateway.api import seats as seats_api

    monkeypatch.setattr(auth_api, "send_email", _fake_send)
    monkeypatch.setattr(account_api, "send_email", _fake_send)
    monkeypatch.setattr(seats_api, "send_email", _fake_send)
    return captured


async def _seed_owner(db, *, balance_kop: int = 0, name: str = "Acme") -> tuple[User, Account]:
    """Owner user + their primary account."""
    user = User(
        email=f"owner-{uuid.uuid4().hex[:10]}@example.com",
        password_hash=hash_password(_PASSWORD),
        email_verified=True,
    )
    db.add(user)
    await db.flush()
    account = Account(
        owner_id=user.id,
        name=name,
        balance_kopecks=balance_kop,
        tariff=Tariff.PRO,
        status=AccountStatus.ACTIVE,
        store_prompts=True,
        settings={},
    )
    db.add(account)
    await db.commit()
    await db.refresh(user)
    await db.refresh(account)
    return user, account


async def _seed_member(
    db, *, account: Account, role: SeatRole, email: str | None = None
) -> tuple[User, Seat]:
    """User with a Seat in the given account."""
    user = User(
        email=email or f"member-{uuid.uuid4().hex[:10]}@example.com",
        password_hash=hash_password(_PASSWORD),
        email_verified=True,
    )
    db.add(user)
    await db.flush()
    seat = Seat(account_id=account.id, user_id=user.id, role=role)
    db.add(seat)
    await db.commit()
    await db.refresh(user)
    await db.refresh(seat)
    return user, seat


async def _login(client, user: User) -> None:
    """Note: /login mints a session for the user's *primary* account (the one
    they own). For a member-of-someone-else's-account login we'd need an
    account switcher (V1.5). Tests that need a member-as-caller log in via
    that member's own primary account first; the cookies still pass require_session
    because each member has their own account where they're the owner."""
    r = await client.post(
        "/v1/auth/login",
        json={"email": user.email, "password": _PASSWORD},
    )
    assert r.status_code == 200, r.text


async def _login_into_account(client, user: User, account: Account) -> None:
    """Force-login as ``user`` *with cookies pointing at ``account``*.

    Helper for tests where the caller is a seat-member of someone else's
    account. We hand-mint the JWT + cookie because /login always picks the
    user's own primary account.
    """
    from fastapi import Response

    from voltari_gateway.auth.cookies import set_session_cookies
    from voltari_gateway.auth.middleware import get_redis
    from voltari_gateway.auth.session import (
        create_access_token,
        create_refresh_token,
    )

    redis = get_redis()
    access_token, access_exp = create_access_token(user.id, account.id)
    refresh_token, refresh_exp, refresh_jti = create_refresh_token(user.id)
    resp = Response()
    await set_session_cookies(
        response=resp,
        redis=redis,
        user_id=user.id,
        access_token=access_token,
        access_expires_at=access_exp,
        refresh_token=refresh_token,
        refresh_expires_at=refresh_exp,
        refresh_jti=refresh_jti,
    )
    # Inject Set-Cookie into the httpx jar — strip path/domain so they apply
    # to ASGITransport's http://test.
    client.cookies.set("vlt_access", access_token)
    client.cookies.set("vlt_refresh", refresh_token)


def _hash_token(plain: str) -> str:
    return hashlib.sha256(plain.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# 1) invite — creates pending record
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_invite_creates_pending_record(client, db, redis_client, monkeypatch):
    _capture_emails(monkeypatch)
    owner, account = await _seed_owner(db)
    await _login(client, owner)

    target = f"newbie-{uuid.uuid4().hex[:8]}@example.com"
    r = await client.post(
        "/v1/account/seats/invite",
        json={"email": target, "role": "member"},
        headers=await _csrf_headers(client),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert uuid.UUID(body["invite_id"])
    assert "expires_at" in body

    # DB row exists with hashed token, role, account_id.
    res = await db.execute(select(EmailInvite).where(EmailInvite.email == target))
    invite = res.scalar_one()
    assert invite.account_id == account.id
    assert invite.role == "member"
    assert invite.token_hash and len(invite.token_hash) == 64  # sha256 hex
    assert invite.accepted_at is None
    # SQLite drops tz on read — normalize before comparing.
    exp = invite.expires_at
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=UTC)
    assert exp > datetime.now(UTC)


# ---------------------------------------------------------------------------
# 2) invite — sends email
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_invite_sends_email(client, db, redis_client, monkeypatch):
    captured = _capture_emails(monkeypatch)
    owner, account = await _seed_owner(db, name="Voltari Team")
    await _login(client, owner)

    target = f"sendme-{uuid.uuid4().hex[:8]}@example.com"
    r = await client.post(
        "/v1/account/seats/invite",
        json={"email": target, "role": "admin"},
        headers=await _csrf_headers(client),
    )
    assert r.status_code == 200, r.text

    # Exactly one email captured to ``target`` containing the workspace name.
    matching = [e for e in captured if e["to"] == target]
    assert len(matching) == 1
    assert "Voltari Team" in matching[0]["body"]
    # Plaintext token must appear in the link — never the hash.
    assert "/accept-invite?token=" in matching[0]["body"]


# ---------------------------------------------------------------------------
# 3) invite — already member → 409
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_invite_already_member_returns_409(client, db, redis_client, monkeypatch):
    _capture_emails(monkeypatch)
    owner, account = await _seed_owner(db)
    member, _ = await _seed_member(db, account=account, role=SeatRole.MEMBER)
    await _login(client, owner)

    r = await client.post(
        "/v1/account/seats/invite",
        json={"email": member.email, "role": "member"},
        headers=await _csrf_headers(client),
    )
    assert r.status_code == 409
    body = r.json()
    assert body["error"]["code"] == "already_member"


# ---------------------------------------------------------------------------
# 4) invite — role="owner" → 400
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_invite_with_role_owner_returns_400(client, db, redis_client, monkeypatch):
    _capture_emails(monkeypatch)
    owner, _ = await _seed_owner(db)
    await _login(client, owner)

    r = await client.post(
        "/v1/account/seats/invite",
        json={"email": f"x-{uuid.uuid4().hex[:6]}@example.com", "role": "owner"},
        headers=await _csrf_headers(client),
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "invalid_role"


# ---------------------------------------------------------------------------
# 5) invite — member cannot invite (only owner / admin)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_invite_member_role_cannot_invite(client, db, redis_client, monkeypatch):
    _capture_emails(monkeypatch)
    owner, account = await _seed_owner(db)
    member, _ = await _seed_member(db, account=account, role=SeatRole.MEMBER)

    # Login member into the OWNER's account (member doesn't have their own
    # primary account in this fixture; we hand-mint cookies).
    await _login_into_account(client, member, account)

    r = await client.post(
        "/v1/account/seats/invite",
        json={"email": f"y-{uuid.uuid4().hex[:6]}@example.com", "role": "member"},
        headers=await _csrf_headers(client),
    )
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "forbidden"


# ---------------------------------------------------------------------------
# 6) GET seats — owner + members
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_seats_returns_owner_and_members(client, db, redis_client, monkeypatch):
    _capture_emails(monkeypatch)
    owner, account = await _seed_owner(db)
    member1, _ = await _seed_member(db, account=account, role=SeatRole.MEMBER)
    member2, _ = await _seed_member(db, account=account, role=SeatRole.ADMIN)

    await _login(client, owner)

    r = await client.get("/v1/account/seats")
    assert r.status_code == 200, r.text
    items = r.json()
    assert len(items) == 3

    by_email = {item["email"]: item for item in items}
    assert by_email[owner.email]["role"] == "owner"
    assert by_email[member1.email]["role"] == "member"
    assert by_email[member2.email]["role"] == "admin"


# ---------------------------------------------------------------------------
# 7) GET invites — pending + non-expired only
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_invites_only_pending_non_expired(client, db, redis_client, monkeypatch):
    _capture_emails(monkeypatch)
    owner, account = await _seed_owner(db)
    await _login(client, owner)

    now = datetime.now(UTC)

    # One pending non-expired (created via API).
    r = await client.post(
        "/v1/account/seats/invite",
        json={"email": "live@example.com", "role": "member"},
        headers=await _csrf_headers(client),
    )
    assert r.status_code == 200

    # One expired (forge directly into DB).
    db.add(
        EmailInvite(
            account_id=account.id,
            email="expired@example.com",
            role="member",
            token_hash=_hash_token("expired-token"),
            expires_at=now - timedelta(days=1),
        )
    )
    # One already accepted.
    db.add(
        EmailInvite(
            account_id=account.id,
            email="done@example.com",
            role="member",
            token_hash=_hash_token("accepted-token"),
            expires_at=now + timedelta(days=7),
            accepted_at=now,
        )
    )
    await db.commit()

    r = await client.get("/v1/account/invites")
    assert r.status_code == 200, r.text
    items = r.json()
    assert len(items) == 1
    assert items[0]["email"] == "live@example.com"


# ---------------------------------------------------------------------------
# 8) DELETE invite
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_invite_marks_revoked(client, db, redis_client, monkeypatch):
    _capture_emails(monkeypatch)
    owner, account = await _seed_owner(db)
    await _login(client, owner)

    r = await client.post(
        "/v1/account/seats/invite",
        json={"email": "del@example.com", "role": "member"},
        headers=await _csrf_headers(client),
    )
    invite_id = r.json()["invite_id"]

    d = await client.delete(f"/v1/account/invites/{invite_id}", headers=await _csrf_headers(client))
    assert d.status_code == 204, d.text

    # Row physically removed (we hard-delete pending invites).
    res = await db.execute(select(EmailInvite).where(EmailInvite.id == uuid.UUID(invite_id)))
    assert res.scalar_one_or_none() is None


# ---------------------------------------------------------------------------
# 9) accept — existing user → seat created, no cookies
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_accept_invite_existing_user_creates_seat(client, db, redis_client, monkeypatch):
    _capture_emails(monkeypatch)
    owner, account = await _seed_owner(db)

    # Existing user with their own primary account already.
    existing = User(
        email=f"existing-{uuid.uuid4().hex[:8]}@example.com",
        password_hash=hash_password(_PASSWORD),
        email_verified=True,
    )
    db.add(existing)
    await db.flush()
    db.add(
        Account(
            owner_id=existing.id,
            name="Existing's own",
            balance_kopecks=0,
            tariff=Tariff.PAYG,
            status=AccountStatus.ACTIVE,
            store_prompts=True,
            settings={},
        )
    )
    await db.commit()

    # Forge an invite directly so we know the plaintext token.
    plain = "existing-test-token-" + uuid.uuid4().hex
    db.add(
        EmailInvite(
            account_id=account.id,
            email=existing.email,
            role="member",
            token_hash=_hash_token(plain),
            expires_at=datetime.now(UTC) + timedelta(days=7),
        )
    )
    await db.commit()

    # Anonymous accept — no cookies, no CSRF (it's a public endpoint).
    fresh_client_cookies = client.cookies.jar
    fresh_client_cookies.clear()
    r = await client.post("/v1/account/seats/accept", json={"token": plain})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["user_id"] == str(existing.id)
    assert body["account_id"] == str(account.id)
    assert body["autologin"] is False  # existing user — no Set-Cookie

    # Seat row exists in inviter's account.
    seat = (
        await db.execute(
            select(Seat).where(
                Seat.account_id == account.id,
                Seat.user_id == existing.id,
            )
        )
    ).scalar_one()
    assert seat.role == SeatRole.MEMBER

    # Invite is consumed.
    inv = (
        await db.execute(select(EmailInvite).where(EmailInvite.token_hash == _hash_token(plain)))
    ).scalar_one()
    assert inv.accepted_at is not None


# ---------------------------------------------------------------------------
# 10) accept — new user → User + primary Account + welcome credit
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_accept_invite_new_user_signup_with_welcome(client, db, redis_client, monkeypatch):
    _capture_emails(monkeypatch)
    owner, account = await _seed_owner(db)

    new_email = f"brand-new-{uuid.uuid4().hex[:8]}@example.com"
    plain = "newuser-token-" + uuid.uuid4().hex
    db.add(
        EmailInvite(
            account_id=account.id,
            email=new_email,
            role="admin",
            token_hash=_hash_token(plain),
            expires_at=datetime.now(UTC) + timedelta(days=7),
        )
    )
    await db.commit()

    client.cookies.jar.clear()
    r = await client.post("/v1/account/seats/accept", json={"token": plain})
    assert r.status_code == 200, r.text
    body = r.json()
    new_user_id = uuid.UUID(body["user_id"])

    # User created + email_verified.
    new_user = await db.get(User, new_user_id)
    assert new_user is not None
    assert new_user.email == new_email
    assert new_user.email_verified is True

    # Their own primary account got +200 ₽ welcome credit.
    primary = (
        await db.execute(select(Account).where(Account.owner_id == new_user_id))
    ).scalar_one()
    assert primary.balance_kopecks == 20_000

    # welcome_credits_log row + matching transaction.
    wlog = (await db.execute(select(WelcomeCreditsLog))).scalars().all()
    assert len(wlog) == 1

    tx = (
        await db.execute(select(Transaction).where(Transaction.account_id == primary.id))
    ).scalar_one()
    assert tx.amount_kopecks == 20_000
    assert (tx.ref_id or "").startswith("welcome:")

    # Seat in inviter's account, role=admin.
    seat = (
        await db.execute(
            select(Seat).where(Seat.account_id == account.id, Seat.user_id == new_user_id)
        )
    ).scalar_one()
    assert seat.role == SeatRole.ADMIN

    # Autologin worked → cookies on the response.
    assert body["autologin"] is True
    assert client.cookies.get("vlt_access") is not None


# ---------------------------------------------------------------------------
# 11) accept — expired → 400
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_accept_invite_expired_returns_400(client, db, redis_client, monkeypatch):
    _capture_emails(monkeypatch)
    owner, account = await _seed_owner(db)

    plain = "expired-token-" + uuid.uuid4().hex
    db.add(
        EmailInvite(
            account_id=account.id,
            email=f"e-{uuid.uuid4().hex[:6]}@example.com",
            role="member",
            token_hash=_hash_token(plain),
            expires_at=datetime.now(UTC) - timedelta(hours=1),
        )
    )
    await db.commit()

    client.cookies.jar.clear()
    r = await client.post("/v1/account/seats/accept", json={"token": plain})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "invite_expired"


# ---------------------------------------------------------------------------
# 12) accept — already accepted → 410
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_accept_invite_already_accepted_returns_410(client, db, redis_client, monkeypatch):
    _capture_emails(monkeypatch)
    owner, account = await _seed_owner(db)

    plain = "consumed-token-" + uuid.uuid4().hex
    db.add(
        EmailInvite(
            account_id=account.id,
            email=f"c-{uuid.uuid4().hex[:6]}@example.com",
            role="member",
            token_hash=_hash_token(plain),
            expires_at=datetime.now(UTC) + timedelta(days=7),
            accepted_at=datetime.now(UTC),
        )
    )
    await db.commit()

    client.cookies.jar.clear()
    r = await client.post("/v1/account/seats/accept", json={"token": plain})
    assert r.status_code == 410
    assert r.json()["error"]["code"] == "invite_accepted"


# ---------------------------------------------------------------------------
# 13) DELETE seat — owner can remove member
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_seat_owner_can_remove_member(client, db, redis_client, monkeypatch):
    _capture_emails(monkeypatch)
    owner, account = await _seed_owner(db)
    member, _ = await _seed_member(db, account=account, role=SeatRole.MEMBER)
    await _login(client, owner)

    r = await client.delete(f"/v1/account/seats/{member.id}", headers=await _csrf_headers(client))
    assert r.status_code == 204, r.text

    # Seat row gone.
    seat = (
        await db.execute(
            select(Seat).where(Seat.account_id == account.id, Seat.user_id == member.id)
        )
    ).scalar_one_or_none()
    assert seat is None


# ---------------------------------------------------------------------------
# 14) DELETE seat — admin can remove member
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_seat_admin_can_remove_member(client, db, redis_client, monkeypatch):
    _capture_emails(monkeypatch)
    owner, account = await _seed_owner(db)
    admin, _ = await _seed_member(db, account=account, role=SeatRole.ADMIN)
    member, _ = await _seed_member(db, account=account, role=SeatRole.MEMBER)
    await _login_into_account(client, admin, account)

    r = await client.delete(f"/v1/account/seats/{member.id}", headers=await _csrf_headers(client))
    assert r.status_code == 204, r.text


# ---------------------------------------------------------------------------
# 15) DELETE seat — member cannot remove
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_seat_member_cannot_remove(client, db, redis_client, monkeypatch):
    _capture_emails(monkeypatch)
    owner, account = await _seed_owner(db)
    m1, _ = await _seed_member(db, account=account, role=SeatRole.MEMBER)
    m2, _ = await _seed_member(db, account=account, role=SeatRole.MEMBER)
    await _login_into_account(client, m1, account)

    r = await client.delete(f"/v1/account/seats/{m2.id}", headers=await _csrf_headers(client))
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "forbidden"


# ---------------------------------------------------------------------------
# 16) DELETE seat — owner cannot remove self
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_seat_owner_cannot_remove_self(client, db, redis_client, monkeypatch):
    _capture_emails(monkeypatch)
    owner, account = await _seed_owner(db)
    await _login(client, owner)

    r = await client.delete(f"/v1/account/seats/{owner.id}", headers=await _csrf_headers(client))
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "cannot_remove_owner"


# ---------------------------------------------------------------------------
# Extra: invite re-send is idempotent (rotates token, doesn't create a 2nd row)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_invite_resend_is_idempotent(client, db, redis_client, monkeypatch):
    captured = _capture_emails(monkeypatch)
    owner, account = await _seed_owner(db)
    await _login(client, owner)

    target = f"resend-{uuid.uuid4().hex[:8]}@example.com"
    r1 = await client.post(
        "/v1/account/seats/invite",
        json={"email": target, "role": "member"},
        headers=await _csrf_headers(client),
    )
    r2 = await client.post(
        "/v1/account/seats/invite",
        json={"email": target, "role": "admin"},  # also escalate role
        headers=await _csrf_headers(client),
    )
    assert r1.status_code == 200
    assert r2.status_code == 200
    # Same invite_id (idempotent on (account, email) for pending invite).
    assert r1.json()["invite_id"] == r2.json()["invite_id"]

    # Exactly one row, role updated to admin.
    rows = (
        (await db.execute(select(EmailInvite).where(EmailInvite.email == target))).scalars().all()
    )
    assert len(rows) == 1
    assert rows[0].role == "admin"

    # Two emails fired (one per call).
    assert len([e for e in captured if e["to"] == target]) == 2
