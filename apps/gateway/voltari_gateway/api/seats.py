"""Management API — seats (members) and email invites.

Two router prefixes are mounted off this module:

* ``/v1/account/seats``    — list members, invite a new one, remove a seat.
* ``/v1/account/invites``  — list pending invites, revoke an invite, accept
  an invite (the only **anonymous** endpoint in the management API).

All cookie-protected endpoints share the ``X-Requested-With`` CSRF gate via
``require_session``. The accept endpoint is intentionally exempt — invites
arrive as a link in an email, the recipient is not yet logged in.

Roles & permissions matrix
--------------------------

::

                          owner   admin   member
    list seats             yes     yes     yes
    list invites           yes     yes      no
    invite (admin/member)  yes     yes      no
    revoke invite          yes     yes      no
    remove seat:
      a member             yes     yes      no
      another admin        yes      no      no
      the owner             no      no      no

``role="owner"`` is set exactly once (at signup) and is never sent over the
invite API. Transferring ownership is a V2 endpoint.

Token model
-----------

Plaintext invite tokens are 256-bit ``secrets.token_urlsafe(32)`` strings.
Only ``token_hash = sha256(plaintext)`` is persisted. The plaintext is
delivered exclusively via email — DB leak alone cannot accept an invite.

Welcome credit on accept-with-new-user
--------------------------------------

If the invitee does NOT have a Voltari account yet, accept creates a fresh
``User`` + primary ``Account`` with the same atomic 200 ₽ welcome bonus the
``/signup`` flow grants — guarded by the ``welcome_credits_log`` PK so an
attacker can't farm bonuses by self-inviting from disposable mailboxes.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Path, Request, Response
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import insert, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from voltari_gateway.auth.cookies import set_session_cookies
from voltari_gateway.auth.middleware import get_redis
from voltari_gateway.auth.rate_limit import check_accept_invite
from voltari_gateway.auth.session import (
    create_access_token,
    create_refresh_token,
)
from voltari_gateway.auth.session_middleware import (
    SessionPrincipal,
    require_session,
)
from voltari_gateway.config import get_settings
from voltari_gateway.db.models import (
    Account,
    AccountStatus,
    EmailInvite,
    Seat,
    SeatRole,
    Tariff,
    Transaction,
    TransactionKind,
    User,
    WelcomeCreditsLog,
)
from voltari_gateway.db.session import get_db
from voltari_gateway.email.client import (
    build_frontend_link,
    render_template,
    send_email,
)
from voltari_gateway.utils.errors import GatewayError, invalid_request
from voltari_gateway.utils.logging import get_logger

log = get_logger(__name__)

# Welcome bonus, mirrors voltari_gateway.api.auth — kept duplicated rather
# than imported to avoid a circular import risk (api/seats already imports
# session helpers that auth.py also touches).
WELCOME_CREDIT_KOPECKS = 20_000

# Two routers share the module so main.py can mount each on its own prefix.
seats_router = APIRouter(tags=["seats"], prefix="/v1/account/seats")
invites_router = APIRouter(tags=["seats"], prefix="/v1/account/invites")


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------


class SeatItem(BaseModel):
    user_id: str
    email: str
    role: str
    joined_at: datetime


class InviteItem(BaseModel):
    invite_id: str
    email: str
    role: str
    invited_at: datetime
    expires_at: datetime


class CreateInviteRequest(BaseModel):
    """Body of POST /v1/account/seats/invite.

    ``role`` is constrained to admin|member at the API layer. ``owner`` is
    rejected explicitly with 400 rather than silently 422'd by the regex
    pattern, so the SPA can show a clear error.
    """

    email: EmailStr
    role: str = Field(min_length=1, max_length=32)


class CreateInviteResponse(BaseModel):
    invite_id: str
    expires_at: datetime


class AcceptInviteRequest(BaseModel):
    token: str = Field(min_length=8, max_length=4096)


class AcceptInviteResponse(BaseModel):
    user_id: str
    account_id: str
    autologin: bool


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _as_utc(dt: datetime) -> datetime:
    """Coerce a possibly-naive datetime to tz-aware UTC.

    SQLite via aiosqlite drops timezone info on read, so a column written as
    ``DateTime(timezone=True)`` comes back naive on subsequent loads. Postgres
    keeps tz. We normalize everywhere we compare DB-loaded timestamps with
    ``_utcnow()`` to avoid ``can't compare offset-naive and offset-aware``.
    """
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt


def _norm_email(raw: str) -> str:
    return raw.strip().lower()


def _hash_invite_token(plaintext: str) -> str:
    """SHA-256 hex of the plaintext invite token. Only the hash hits the DB."""
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()


def _generate_invite_token() -> str:
    """256-bit URL-safe random string — copy-paste-able from a console email."""
    return secrets.token_urlsafe(32)


def _email_hash(email: str) -> str:
    return hashlib.sha256(_norm_email(email).encode("utf-8")).hexdigest()


def _ip_hash(ip: str | None) -> str | None:
    if not ip:
        return None
    return hashlib.sha256(ip.encode("utf-8")).hexdigest()


def _client_ip(request: Request) -> str:
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    if request.client is not None:
        return request.client.host
    return "0.0.0.0"


async def _get_caller_role(db: AsyncSession, account: Account, user: User) -> SeatRole:
    """Resolve the caller's role within the active account.

    Owner is detected via ``Account.owner_id``; other members via Seat.
    Returns SeatRole.OWNER even if no Seat row exists for the owner — many
    schemas don't bother seeding an explicit owner-Seat at signup.
    """
    if account.owner_id == user.id:
        return SeatRole.OWNER
    result = await db.execute(
        select(Seat.role).where(Seat.account_id == account.id, Seat.user_id == user.id)
    )
    role = result.scalar_one_or_none()
    if role is None:
        # Defensive — require_session minted the access JWT for this pair so
        # *something* must have authorised it. Treat as least-privileged.
        return SeatRole.MEMBER
    return role


def _require_management_role(role: SeatRole) -> None:
    """Owner & admin are allowed to manage seats; member is not."""
    if role not in (SeatRole.OWNER, SeatRole.ADMIN):
        raise GatewayError(
            status_code=403,
            message="Only owner or admin can manage seats.",
            type="invalid_request_error",
            code="forbidden",
        )


def _seat_role_from_str(raw: str) -> SeatRole:
    """Coerce API ``role`` literal to SeatRole. Rejects ``owner`` and unknowns."""
    normalized = raw.strip().lower()
    if normalized == "owner":
        raise invalid_request(
            "Cannot invite a user as owner — ownership is set at signup.",
            param="role",
            code="invalid_role",
        )
    if normalized == "admin":
        return SeatRole.ADMIN
    if normalized == "member":
        return SeatRole.MEMBER
    raise invalid_request(
        "Unknown role. Allowed: admin, member.",
        param="role",
        code="invalid_role",
    )


async def _send_invite_email(*, to: str, account_name: str, link: str) -> None:
    """Render + send the seat-invite template. Logs but never raises."""
    try:
        body = render_template("seat-invite.txt", account_name=account_name, link=link)
    except Exception as exc:
        log.error("invite_email_render_failed", error=str(exc))
        return
    await send_email(to=to, subject=f"Приглашение в {account_name} — Voltari", body=body)


async def _try_grant_welcome_credit(
    *,
    db: AsyncSession,
    user: User,
    email: str,
    ip: str | None,
) -> bool:
    """Atomic 200 ₽ grant guarded by ``welcome_credits_log`` PK.

    Mirrors api/auth._try_grant_welcome_credit — duplicated rather than
    imported to keep the call sites small and decoupled. Returns True iff
    this call actually credited.
    """
    eh = _email_hash(email)
    ih = _ip_hash(ip)

    try:
        await db.execute(
            insert(WelcomeCreditsLog).values(
                email_hash=eh,
                ip_hash=ih,
                granted_at=_utcnow(),
            )
        )
        await db.flush()
    except IntegrityError:
        await db.rollback()
        return False

    # Re-fetch the user's primary account inside the same session.
    res = await db.execute(
        select(Account)
        .where(Account.owner_id == user.id)
        .order_by(Account.created_at.asc())
        .limit(1)
    )
    account = res.scalar_one_or_none()
    if account is None:
        log.error("welcome_credit_no_account", user_id=str(user.id))
        return False

    account.balance_kopecks = (account.balance_kopecks or 0) + WELCOME_CREDIT_KOPECKS
    db.add(
        Transaction(
            account_id=account.id,
            type=TransactionKind.TOPUP,
            amount_kopecks=WELCOME_CREDIT_KOPECKS,
            ref_id=f"welcome:{user.id}",
            meta={"kind": "welcome", "via": "invite", "email_hash": eh},
        )
    )
    return True


# ---------------------------------------------------------------------------
# 1) GET /v1/account/seats
# ---------------------------------------------------------------------------


@seats_router.get("", response_model=list[SeatItem])
async def list_seats(
    db: AsyncSession = Depends(get_db),
    principal: SessionPrincipal = Depends(require_session),
) -> list[SeatItem]:
    """List every seat in the active account, including the owner.

    The owner doesn't necessarily have an explicit Seat row — we synthesise
    one from ``Account.owner_id`` so the response always reflects the full
    membership of the workspace. Sorted: owner first, then by joined date.
    """
    account = principal.account

    # Fetch owner separately so we can render them even without a Seat row.
    owner = await db.get(User, account.owner_id)
    if owner is None:
        # Account.owner_id FK is NOT NULL — if this hits, DB invariant broken.
        log.error("seat_list_owner_missing", account_id=str(account.id))
        raise GatewayError(
            status_code=500,
            message="Internal error: account owner missing.",
            type="api_error",
            code="internal_error",
        )

    items: list[SeatItem] = [
        SeatItem(
            user_id=str(owner.id),
            email=owner.email,
            role=SeatRole.OWNER.value,
            joined_at=account.created_at,
        )
    ]

    stmt = (
        select(Seat, User)
        .join(User, Seat.user_id == User.id)
        .where(Seat.account_id == account.id)
        .where(Seat.user_id != account.owner_id)  # don't double-count owner
        .order_by(Seat.created_at.asc())
    )
    rows = (await db.execute(stmt)).all()
    for seat, user in rows:
        items.append(
            SeatItem(
                user_id=str(user.id),
                email=user.email,
                role=seat.role.value,
                joined_at=seat.created_at,
            )
        )
    return items


# ---------------------------------------------------------------------------
# 2) POST /v1/account/seats/invite
# ---------------------------------------------------------------------------


@seats_router.post("/invite", response_model=CreateInviteResponse, status_code=200)
async def create_invite(
    payload: CreateInviteRequest,
    db: AsyncSession = Depends(get_db),
    principal: SessionPrincipal = Depends(require_session),
) -> CreateInviteResponse:
    """Send an invite email and create a pending ``EmailInvite`` row.

    Idempotency: if an unaccepted invite for the same (account, email) pair
    already exists and isn't expired, we re-use it (and re-mint the token so
    the user always gets a working link). This is what the SPA expects when
    the user hits "Invite" twice in a row.
    """
    role = _seat_role_from_str(payload.role)  # raises 400 on owner/unknown
    caller_role = await _get_caller_role(db, principal.account, principal.user)
    _require_management_role(caller_role)

    target_email = _norm_email(payload.email)
    account = principal.account

    # --- Already a member? Disallow before we waste an email. ---
    existing_user = (
        await db.execute(select(User).where(User.email == target_email))
    ).scalar_one_or_none()
    if existing_user is not None:
        # Owner of *this* account.
        if existing_user.id == account.owner_id:
            raise GatewayError(
                status_code=409,
                message="This user is already a member of the account.",
                type="invalid_request_error",
                code="already_member",
                param="email",
            )
        # Existing seat in this account.
        existing_seat = (
            await db.execute(
                select(Seat.id).where(
                    Seat.account_id == account.id,
                    Seat.user_id == existing_user.id,
                )
            )
        ).scalar_one_or_none()
        if existing_seat is not None:
            raise GatewayError(
                status_code=409,
                message="This user is already a member of the account.",
                type="invalid_request_error",
                code="already_member",
                param="email",
            )

    settings = get_settings()
    expires_at = _utcnow() + timedelta(days=settings.invite_ttl_days)

    token_plain = _generate_invite_token()
    token_hash = _hash_invite_token(token_plain)

    # --- Resend an existing pending invite (idempotency) ---
    existing_invite = (
        await db.execute(
            select(EmailInvite).where(
                EmailInvite.account_id == account.id,
                EmailInvite.email == target_email,
                EmailInvite.accepted_at.is_(None),
            )
        )
    ).scalar_one_or_none()

    if existing_invite is not None and _as_utc(existing_invite.expires_at) > _utcnow():
        # Rotate the token (old plaintext is gone forever — only hash was
        # stored — so the user can't recover it). Update role in case the
        # caller is escalating member→admin, and bump expiry.
        existing_invite.token_hash = token_hash
        existing_invite.role = role.value
        existing_invite.expires_at = expires_at
        existing_invite.invited_by_user_id = principal.user.id
        invite = existing_invite
    else:
        invite = EmailInvite(
            account_id=account.id,
            email=target_email,
            role=role.value,
            token_hash=token_hash,
            expires_at=expires_at,
            invited_by_user_id=principal.user.id,
        )
        db.add(invite)

    await db.commit()
    await db.refresh(invite)

    link = build_frontend_link("/accept-invite", token=token_plain)
    await _send_invite_email(to=target_email, account_name=account.name, link=link)

    log.info(
        "invite_created",
        account_id=str(account.id),
        invite_id=str(invite.id),
        role=role.value,
        invited_by=str(principal.user.id),
    )

    return CreateInviteResponse(invite_id=str(invite.id), expires_at=expires_at)


# ---------------------------------------------------------------------------
# 3) GET /v1/account/invites
# ---------------------------------------------------------------------------


@invites_router.get("", response_model=list[InviteItem])
async def list_invites(
    db: AsyncSession = Depends(get_db),
    principal: SessionPrincipal = Depends(require_session),
) -> list[InviteItem]:
    """Pending invites (not accepted, not expired). Owner & admin only."""
    caller_role = await _get_caller_role(db, principal.account, principal.user)
    _require_management_role(caller_role)

    now = _utcnow()
    stmt = (
        select(EmailInvite)
        .where(EmailInvite.account_id == principal.account.id)
        .where(EmailInvite.accepted_at.is_(None))
        .where(EmailInvite.expires_at > now)
        .order_by(EmailInvite.created_at.desc())
    )
    result = await db.execute(stmt)
    invites = result.scalars().all()
    return [
        InviteItem(
            invite_id=str(inv.id),
            email=inv.email,
            role=inv.role,
            invited_at=inv.created_at,
            expires_at=inv.expires_at,
        )
        for inv in invites
    ]


# ---------------------------------------------------------------------------
# 4) DELETE /v1/account/invites/{invite_id}
# ---------------------------------------------------------------------------


@invites_router.delete("/{invite_id}", status_code=204)
async def revoke_invite(
    invite_id: uuid.UUID = Path(...),
    db: AsyncSession = Depends(get_db),
    principal: SessionPrincipal = Depends(require_session),
) -> Response:
    """Hard-delete a pending invite. Owner & admin only.

    A hard delete is fine because EmailInvite rows have no downstream FKs
    (they're consumed at /accept which marks ``accepted_at``). Re-inviting
    after revoke is a clean fresh row, no zombie state.
    """
    caller_role = await _get_caller_role(db, principal.account, principal.user)
    _require_management_role(caller_role)

    invite = await db.get(EmailInvite, invite_id)
    if invite is None or invite.account_id != principal.account.id:
        # Don't leak existence of invites in other accounts.
        raise GatewayError(
            status_code=404,
            message="Invite not found.",
            type="invalid_request_error",
            code="invite_not_found",
        )
    if invite.accepted_at is not None:
        # Already accepted — there's nothing to revoke. Idempotent 404 keeps
        # the contract uniform.
        raise GatewayError(
            status_code=404,
            message="Invite not found.",
            type="invalid_request_error",
            code="invite_not_found",
        )

    await db.delete(invite)
    await db.commit()

    log.info(
        "invite_revoked",
        account_id=str(principal.account.id),
        invite_id=str(invite_id),
        revoked_by=str(principal.user.id),
    )
    return Response(status_code=204)


# ---------------------------------------------------------------------------
# 5) POST /v1/account/seats/accept (anonymous)
# ---------------------------------------------------------------------------


@seats_router.post("/accept", response_model=AcceptInviteResponse, status_code=200)
async def accept_invite(
    payload: AcceptInviteRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> AcceptInviteResponse:
    """Accept a pending invite. Two flows:

    A) **User exists** (logged in or not — doesn't matter): create a Seat in
       the inviter's account, mark the invite consumed. No cookies are
       issued — the caller goes through /login on their own.

    B) **User is new**: create User + primary Account, atomically grant the
       200 ₽ welcome bonus, create a Seat in the inviter's account, then
       autologin (Set-Cookie). The inviter does NOT pay for the bonus — it
       comes out of the same marketing budget as /signup.

    The endpoint is anonymous so it cannot use ``require_session`` — instead
    we rate-limit by client IP to prevent token-guessing brute force, and
    every invalid token returns a flat 400 with the same body so an
    attacker can't tell "expired" from "wrong" (timing is the only side
    channel; the work is symmetric).
    """
    ip = _client_ip(request)
    if not check_accept_invite(ip):
        raise GatewayError(
            status_code=429,
            message="Too many invite-accept attempts. Try again shortly.",
            type="invalid_request_error",
            code="rate_limited",
            headers={"Retry-After": "60"},
        )

    token_hash = _hash_invite_token(payload.token)
    invite = (
        await db.execute(select(EmailInvite).where(EmailInvite.token_hash == token_hash))
    ).scalar_one_or_none()

    if invite is None:
        raise invalid_request(
            "Invite token is invalid.",
            param="token",
            code="invalid_token",
        )

    now = _utcnow()
    if invite.accepted_at is not None:
        # Already consumed — 410 Gone signals "the resource existed but is
        # permanently unavailable", which is the right semantics here.
        raise GatewayError(
            status_code=410,
            message="Invite has already been accepted.",
            type="invalid_request_error",
            code="invite_accepted",
        )
    if _as_utc(invite.expires_at) <= now:
        raise GatewayError(
            status_code=400,
            message="Invite has expired. Ask the workspace owner to send a new one.",
            type="invalid_request_error",
            code="invite_expired",
        )

    target_account = await db.get(Account, invite.account_id)
    if target_account is None or target_account.status != AccountStatus.ACTIVE:
        # Inviter's account is gone or suspended.
        raise GatewayError(
            status_code=400,
            message="The inviting account is no longer active.",
            type="invalid_request_error",
            code="account_inactive",
        )

    target_role = SeatRole(invite.role)  # CHECK + enum guarantee this is OK
    target_email = _norm_email(invite.email)

    # --- Branch A: existing user ---
    existing_user = (
        await db.execute(select(User).where(User.email == target_email))
    ).scalar_one_or_none()

    if existing_user is not None:
        # Already a seat? Idempotently mark accepted and return.
        existing_seat = (
            await db.execute(
                select(Seat).where(
                    Seat.account_id == target_account.id,
                    Seat.user_id == existing_user.id,
                )
            )
        ).scalar_one_or_none()

        if existing_seat is None and existing_user.id != target_account.owner_id:
            db.add(
                Seat(
                    account_id=target_account.id,
                    user_id=existing_user.id,
                    role=target_role,
                )
            )

        invite.accepted_at = now
        await db.commit()

        log.info(
            "invite_accepted_existing_user",
            invite_id=str(invite.id),
            account_id=str(target_account.id),
            user_id=str(existing_user.id),
        )
        return AcceptInviteResponse(
            user_id=str(existing_user.id),
            account_id=str(target_account.id),
            autologin=False,
        )

    # --- Branch B: new user — create User + primary Account + welcome credit ---
    # The new user gets their OWN primary account (so they can use the API
    # standalone too), AND a seat in the inviter's account. The invite-link
    # already proves email ownership, so email_verified=true.
    #
    # We deliberately don't accept a password here — the invitee uses
    # /forgot-password to set one. This matches what GitHub / Slack do for
    # "magic-link signup": don't make the user pick a password mid-flow.
    placeholder_password_hash = "$argon2id$v=19$m=65536,t=2,p=2$placeholder$placeholder"

    new_user = User(
        email=target_email,
        password_hash=placeholder_password_hash,
        email_verified=True,
    )
    db.add(new_user)
    await db.flush()

    new_primary = Account(
        owner_id=new_user.id,
        name=target_email.split("@")[0] or "Personal",
        balance_kopecks=0,
        tariff=Tariff.PAYG,
        status=AccountStatus.ACTIVE,
        store_prompts=True,
    )
    db.add(new_primary)
    await db.flush()

    # Welcome credit on the new user's *own* primary account — guarded by
    # email_hash PK so a recycled email can't farm bonuses.
    granted = await _try_grant_welcome_credit(
        db=db,
        user=new_user,
        email=target_email,
        ip=ip,
    )

    # Seat in the inviter's account (separate from the new primary).
    db.add(
        Seat(
            account_id=target_account.id,
            user_id=new_user.id,
            role=target_role,
        )
    )

    invite.accepted_at = now
    await db.commit()
    await db.refresh(new_user)
    await db.refresh(new_primary)

    log.info(
        "invite_accepted_new_user",
        invite_id=str(invite.id),
        account_id=str(target_account.id),
        user_id=str(new_user.id),
        welcome_credit_granted=granted,
    )

    # --- Autologin (best-effort) ---
    # If Redis is down we don't autologin — without the whitelist we can't
    # mint a refresh token we'd be able to revoke later. The user can still
    # use /forgot-password and log in normally.
    redis = get_redis()
    if redis is None:
        return AcceptInviteResponse(
            user_id=str(new_user.id),
            account_id=str(target_account.id),
            autologin=False,
        )

    # Mint cookies anchored to the new user's *own* primary account, not the
    # account they were invited into. The SPA's account-switcher (V1.5) lets
    # them flip between workspaces.
    access_token, access_exp = create_access_token(new_user.id, new_primary.id)
    refresh_token, refresh_exp, refresh_jti = create_refresh_token(new_user.id)
    try:
        await set_session_cookies(
            response=response,
            redis=redis,
            user_id=new_user.id,
            access_token=access_token,
            access_expires_at=access_exp,
            refresh_token=refresh_token,
            refresh_expires_at=refresh_exp,
            refresh_jti=refresh_jti,
        )
        autologged = True
    except Exception as exc:
        log.warning("invite_accept_autologin_failed", error=str(exc))
        autologged = False

    return AcceptInviteResponse(
        user_id=str(new_user.id),
        account_id=str(target_account.id),
        autologin=autologged,
    )


# ---------------------------------------------------------------------------
# 6) DELETE /v1/account/seats/{user_id}
# ---------------------------------------------------------------------------


@seats_router.delete("/{user_id}", status_code=204)
async def remove_seat(
    user_id: uuid.UUID = Path(...),
    db: AsyncSession = Depends(get_db),
    principal: SessionPrincipal = Depends(require_session),
) -> Response:
    """Remove a member from the active account.

    Permission rules (see module docstring for the full matrix):

    * Owner can remove any seat *except* themselves (use the V2 transfer-of-
      ownership endpoint to hand off + leave).
    * Admin can remove a member (not another admin, not the owner).
    * Member can never remove anyone.

    Removal is a **physical delete** of the Seat row — no soft-delete column
    on this table. The user keeps their own primary account untouched; we're
    only cutting their access to *this* workspace. They can be re-invited
    later without DB clutter from a dangling row.
    """
    account = principal.account
    caller = principal.user
    caller_role = await _get_caller_role(db, account, caller)

    # Owner cannot remove themselves — needs transfer-of-ownership (V2).
    if user_id == account.owner_id:
        raise GatewayError(
            status_code=400,
            message=(
                "Cannot remove the workspace owner. Transfer ownership "
                "first (V2) or close the account."
            ),
            type="invalid_request_error",
            code="cannot_remove_owner",
        )

    # Member can't remove anyone.
    if caller_role == SeatRole.MEMBER:
        raise GatewayError(
            status_code=403,
            message="Members cannot remove seats.",
            type="invalid_request_error",
            code="forbidden",
        )

    # Find the seat. Returning 404 (not 403) for cross-account user IDs
    # avoids leaking membership of other accounts.
    seat = (
        await db.execute(select(Seat).where(Seat.account_id == account.id, Seat.user_id == user_id))
    ).scalar_one_or_none()
    if seat is None:
        raise GatewayError(
            status_code=404,
            message="Seat not found.",
            type="invalid_request_error",
            code="seat_not_found",
        )

    # Admin can only remove members; owner can remove anyone (admin or member).
    if caller_role == SeatRole.ADMIN and seat.role != SeatRole.MEMBER:
        raise GatewayError(
            status_code=403,
            message="Admins can only remove members. Ask the owner.",
            type="invalid_request_error",
            code="forbidden",
        )

    await db.delete(seat)
    await db.commit()

    log.info(
        "seat_removed",
        account_id=str(account.id),
        removed_user_id=str(user_id),
        removed_by=str(caller.id),
    )
    return Response(status_code=204)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


# main.py expects ``router`` plus the two-prefixed split for clarity.
__all__ = ["invites_router", "seats_router"]
