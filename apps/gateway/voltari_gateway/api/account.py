"""Management API — account profile + settings endpoints.

Mounted under ``/v1/account``. Three endpoints, all session-cookie protected
through ``require_session`` (which also enforces the ``X-Requested-With``
CSRF header on mutating methods).

    GET    /v1/account             — profile + balance + flags snapshot
    PATCH  /v1/account/profile     — name / email update
    PATCH  /v1/account/settings    — prompt-logging flag + free-form prefs

Field mapping (per CEO 29.04 — no schema renaming):

* ``prompt_logging_enabled`` (API)  ⇄  ``Account.store_prompts`` (DB).
* ``notifications`` (API)            ⇄  ``Account.settings["notifications"]``.

Email change: we re-issue the verification mail and **immediately demote**
``email_verified`` to False. The user keeps their session for the current
device (cookies are still valid) but won't be able to /login from another
device until they verify the new address. That matches what every major SaaS
does (Slack, Notion, GitHub) and avoids leaving an unverified email as the
account's login channel indefinitely.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from voltari_gateway.auth.cookies import clear_session_cookies
from voltari_gateway.auth.email_verification import (
    generate_verification_token,
    hash_token,
)
from voltari_gateway.auth.middleware import get_redis
from voltari_gateway.auth.session import revoke_all_refresh_tokens
from voltari_gateway.auth.session_middleware import (
    SessionPrincipal,
    require_session,
)
from voltari_gateway.db.models import Account, User
from voltari_gateway.db.session import get_db
from voltari_gateway.email.client import (
    build_frontend_link,
    render_template,
    send_email,
)
from voltari_gateway.utils.errors import GatewayError
from voltari_gateway.utils.logging import get_logger

log = get_logger(__name__)

router = APIRouter(tags=["account"])


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------


class TelegramLinkStatus(BaseModel):
    """Live pairing state for the SPA's Settings → Telegram block.

    ``linked`` flips to ``True`` once the bot has consumed a /start <token>
    deep-link (or /link <token> manual fallback) and stamped
    ``users.telegram_chat_id``. The frontend uses this to swap between the
    "Подключить" empty state and the green "Подключён" badge.

    ``chat_id`` is the Telegram chat the bot will sendMessage into. Stored
    as a string for the SPA — JS numbers can't safely round-trip int64
    chat IDs (Telegram allocates beyond 2^53).
    """

    linked: bool
    chat_id: str | None = None


class AccountResponse(BaseModel):
    """Snapshot returned by GET / PATCH endpoints.

    ``prompt_logging_enabled`` mirrors ``Account.store_prompts`` — the API uses
    the human-readable name, the DB the historical column. CEO 29.04 decided
    against a column rename (no migration cost, no broken tests).

    ``requires_pii_setup`` (Sprint 6) — surface a UI hint when the
    account is on PRO_PRIVACY tariff but PII masking hasn't been
    enabled yet. The frontend uses it to nudge with an inline banner;
    the backend never enforces — the user actively toggles the masking
    in /settings/privacy to flip ``pii_masking_enabled``.

    ``telegram_link`` (Sprint 4 / Поток M, fixed 2026-05-08) — pairing
    state. The SPA renders the "Подключить / Подключён" widget off this.
    """

    user_id: str
    email: str
    account_id: str
    name: str
    tariff: str
    balance_kopecks: int
    prompt_logging_enabled: bool
    notifications: dict[str, object]
    created_at: datetime
    email_verified: bool
    requires_pii_setup: bool
    tariff_active_until: datetime | None
    telegram_link: TelegramLinkStatus


class UpdateProfileRequest(BaseModel):
    """Profile patch — both fields optional. Pydantic v2 distinguishes
    ``None`` (explicit clear, currently unsupported) from missing (no-op)
    via ``model_fields_set``.
    """

    name: str | None = Field(default=None, min_length=1, max_length=255)
    email: EmailStr | None = None


class UpdateSettingsRequest(BaseModel):
    """Settings patch. Both keys are optional; only the supplied ones change.

    ``notifications`` is a free-form JSONB blob — we don't constrain the shape
    here so the SPA can ship new toggles without a server-side schema bump.
    """

    prompt_logging_enabled: bool | None = None
    notifications: dict[str, object] | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _norm_email(raw: str) -> str:
    return raw.strip().lower()


def _account_to_response(user: User, account: Account) -> AccountResponse:
    """Render the canonical account snapshot used by GET / PATCH responses."""
    # Sprint 6 Блок 14 — UI hint for "you bought Pro Privacy but didn't
    # turn on the masking yet". Backend stays advisory.
    from voltari_gateway.db.models import Tariff as _Tariff

    requires_pii_setup = account.tariff == _Tariff.PRO_PRIVACY and not account.pii_masking_enabled
    tg_chat_id = user.telegram_chat_id
    return AccountResponse(
        user_id=str(user.id),
        email=user.email,
        account_id=str(account.id),
        name=account.name,
        tariff=account.tariff.value,
        balance_kopecks=int(account.balance_kopecks),
        prompt_logging_enabled=bool(account.store_prompts),
        notifications=dict((account.settings or {}).get("notifications", {}) or {}),
        created_at=account.created_at,
        email_verified=bool(user.email_verified),
        requires_pii_setup=requires_pii_setup,
        tariff_active_until=account.tariff_active_until,
        telegram_link=TelegramLinkStatus(
            linked=tg_chat_id is not None,
            chat_id=str(tg_chat_id) if tg_chat_id is not None else None,
        ),
    )


async def _send_verification_email(user_id: uuid.UUID, email: str) -> str:
    """Mint + email a fresh verification token. Returns the plaintext token
    (caller stores the hash). Email failure is logged but does not raise —
    matches /signup behaviour.
    """
    token_plain = generate_verification_token(user_id, email)
    # SPA route /signup/verify-email handles the ?token= landing.
    link = build_frontend_link("/signup/verify-email", token=token_plain)
    try:
        body = render_template("verify-email.txt", link=link)
        await send_email(to=email, subject="Подтверди email — Brikko", body=body)
    except Exception as exc:
        log.warning("verify_email_send_failed", error=str(exc))
    return token_plain


# ---------------------------------------------------------------------------
# 1) GET /v1/account
# ---------------------------------------------------------------------------


@router.get("", response_model=AccountResponse)
async def get_account(
    principal: SessionPrincipal = Depends(require_session),
) -> AccountResponse:
    """Return the caller's profile + account snapshot."""
    return _account_to_response(principal.user, principal.account)


# ---------------------------------------------------------------------------
# 2) PATCH /v1/account/profile
# ---------------------------------------------------------------------------


@router.patch("/profile", response_model=AccountResponse)
async def update_profile(
    payload: UpdateProfileRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
    principal: SessionPrincipal = Depends(require_session),
) -> AccountResponse:
    """Update human-friendly name and / or email.

    Email change semantics:
    1. Lower-cased + collision-checked against the global ``users.email`` UNIQUE
       constraint. We do an explicit pre-check rather than relying on the DB
       error so we can return a clean ``email_taken`` body.
    2. ``email_verified=False`` until the new address is re-verified.
    3. Old verification token (if any) is wiped — new one is in-flight.
    4. New verification email dispatched best-effort.
    5. **All active refresh tokens are revoked** — the old email is the login
       channel. If it was compromised (or simply no longer accessible), the
       attacker who could read the inbox should not keep the SPA session on
       another device. Force re-login on every device, including this one
       (so cookies are also cleared on the response).
    """
    user = principal.user
    account = principal.account

    fields = payload.model_fields_set
    if not fields:
        # No-op PATCH is fine — still return the current snapshot for the SPA.
        return _account_to_response(user, account)

    if "name" in fields and payload.name is not None:
        # ``Account.name`` is the workspace label; mirrors what /signup seeded
        # from the local-part of the email.
        account.name = payload.name.strip()

    email_changed = False
    if "email" in fields and payload.email is not None:
        new_email = _norm_email(payload.email)
        if new_email != user.email:
            # Pre-check uniqueness — defends against the DB UNIQUE bubbling up
            # as a 500 with a noisy IntegrityError.
            existing = await db.execute(select(User.id).where(User.email == new_email))
            if existing.scalar_one_or_none() is not None:
                raise GatewayError(
                    status_code=409,
                    message="An account with this email already exists.",
                    type="invalid_request_error",
                    code="email_taken",
                    param="email",
                )

            user.email = new_email
            user.email_verified = False
            # Persist the hash now so a leak before the email lands still
            # can't be replayed without the plaintext from the user's inbox.
            token_plain = generate_verification_token(user.id, new_email)
            user.verification_token = hash_token(token_plain)
            user.verification_sent_at = datetime.now(UTC)

            await db.flush()  # flush before sending so DB changes are committed
            # SPA route /signup/verify-email handles the ?token= landing.
            link = build_frontend_link("/signup/verify-email", token=token_plain)
            try:
                body = render_template("verify-email.txt", link=link)
                await send_email(
                    to=new_email,
                    subject="Подтверди email — Brikko",
                    body=body,
                )
            except Exception as exc:
                log.warning("verify_email_send_failed", error=str(exc))
            email_changed = True

    await db.commit()
    await db.refresh(user)
    await db.refresh(account)

    if email_changed:
        # Security: the old email was the login channel. Kill every active
        # session so an attacker who only had cookies (not the password) is
        # logged out everywhere — and the user is forced to re-verify the new
        # address before they can log in again.
        redis = get_redis()
        if redis is not None:
            try:
                await revoke_all_refresh_tokens(redis, user.id)
            except Exception as exc:
                log.warning("revoke_refresh_failed", error=str(exc))
        # Drop the caller's cookies so the SPA bounces to /login on the next
        # request (the access JWT will still verify until exp, but without the
        # refresh JTI in the whitelist they can't roll forward).
        # redis assert: при проверке выше `redis is not None` — но т.к. mypy не
        # проводит аналитику через if/try без флага, дублируем явно для clarity.
        if redis is not None:
            await clear_session_cookies(response, redis=redis, user_id=None, refresh_jti=None)

    return _account_to_response(user, account)


# ---------------------------------------------------------------------------
# 3) PATCH /v1/account/settings
# ---------------------------------------------------------------------------


@router.patch("/settings", response_model=AccountResponse)
async def update_settings(
    payload: UpdateSettingsRequest,
    db: AsyncSession = Depends(get_db),
    principal: SessionPrincipal = Depends(require_session),
) -> AccountResponse:
    """Update ``prompt_logging_enabled`` and / or the ``notifications`` blob."""
    account = principal.account

    fields = payload.model_fields_set
    if not fields:
        return _account_to_response(principal.user, account)

    if "prompt_logging_enabled" in fields and payload.prompt_logging_enabled is not None:
        # Direct mapping per CEO 29.04 — the column kept its historical name.
        account.store_prompts = bool(payload.prompt_logging_enabled)

    if "notifications" in fields and payload.notifications is not None:
        # JSONB column on PG, JSON on SQLite. SQLAlchemy detects mutations on
        # the typed dict only via reassignment — copy + re-assign so the change
        # is flushed.
        current = dict(account.settings or {})
        current["notifications"] = payload.notifications
        account.settings = current

    await db.commit()
    await db.refresh(account)
    return _account_to_response(principal.user, account)


__all__ = ["router"]
