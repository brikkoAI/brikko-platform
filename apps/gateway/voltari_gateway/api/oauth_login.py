"""OAuth social login endpoints — Google + Yandex.

Mounted under ``/v1/auth/oauth``:

* ``GET  /v1/auth/oauth/{provider}/start``      — 302 → provider consent
* ``GET  /v1/auth/oauth/{provider}/callback``   — code exchange + cookies
* ``POST /v1/auth/oauth/disconnect``            — unlink (session-protected)
* ``GET  /v1/auth/oauth/identities``            — list linked identities

Three flows are supported on the start endpoint via ``mode``:

* ``mode=login`` — anonymous start. Brand-new user → signup; existing
  user → login. The provider-verified email rule (CEO 2026-05-06)
  governs whether we auto-link to a pre-existing password user.
* ``mode=link``  — caller has an active dashboard session and wants to
  attach a new identity to their existing user.

The CSRF state is HMAC-signed (stateless, see oauth_login_state). The
provider's ``code`` is single-use — even on success the upstream call
mints a one-shot exchange.

PII / 152-ФЗ
-------------

We persist ``email_at_link``, ``email_verified_at_link``,
``display_name``, ``avatar_url`` on the OAuthIdentity row. We do NOT
persist:

* The raw provider profile JSON (would be a 152-ФЗ liability).
* The provider's access or refresh tokens (we never re-call the
  provider; persisting would multiply the credential blast radius).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from voltari_gateway.auth.audit import write_audit
from voltari_gateway.auth.cookies import set_session_cookies
from voltari_gateway.auth.csrf import attach_csrf_cookie, generate_csrf_token
from voltari_gateway.auth.middleware import get_redis
from voltari_gateway.auth.oauth_login_state import (
    StatePayload,
    generate_state,
    verify_state,
)
from voltari_gateway.auth.oauth_providers import (
    OAuthProviderError,
    OAuthUserInfo,
    authorize_url,
    exchange_code_for_userinfo,
    is_provider_configured,
)
from voltari_gateway.auth.session import (
    create_access_token,
    create_refresh_token,
    verify_access_token,
)
from voltari_gateway.auth.session_middleware import SessionPrincipal, require_session
from voltari_gateway.auth.sessions_store import record_session
from voltari_gateway.auth.signup_helpers import (
    create_user_and_primary_account,
    grant_welcome_credit_by_email,
    normalise_email,
)
from voltari_gateway.config import get_settings
from voltari_gateway.db.models import (
    Account,
    AccountStatus,
    OAuthIdentity,
    User,
)
from voltari_gateway.db.session import get_db
from voltari_gateway.utils.errors import GatewayError, invalid_request
from voltari_gateway.utils.logging import get_logger

log = get_logger(__name__)

router = APIRouter(prefix="/v1/auth/oauth", tags=["oauth-login"])

_VALID_PROVIDERS: frozenset[str] = frozenset({"google", "yandex"})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _safe_next_path(raw: str | None) -> str:
    """Sanitise the ``next`` SPA path to defend against open-redirect.

    We accept relative paths starting with a single slash; anything
    else (full URL, scheme-relative, double-slash) is replaced with the
    safe default ``/app``. This runs at /start time so a forged state
    can't smuggle a hostile target through to the redirect on /callback.
    """
    if not raw or not isinstance(raw, str):
        return "/app"
    if not raw.startswith("/"):
        return "/app"
    if raw.startswith("//"):
        return "/app"
    if len(raw) > 256:
        return "/app"
    return raw


def _frontend_url(path: str) -> str:
    """Resolve a SPA path against the frontend base URL."""
    base = get_settings().base_url_frontend.rstrip("/")
    if not path.startswith("/"):
        path = "/" + path
    return f"{base}{path}"


def _redirect_to_login_with_reason(
    reason: str, *, next_path: str | None = None
) -> RedirectResponse:
    """Redirect the user to the SPA /login page with a ``reason`` param.

    The SPA renders an inline notice based on the reason — see
    ``apps/web/src/components/auth/LoginForm.tsx``. We never return a
    rendered HTML error here because the user is mid-redirect and the
    SPA owns the chrome.
    """
    params: dict[str, str] = {"reason": reason}
    if next_path:
        params["next"] = next_path
    url = _frontend_url("/login") + "?" + urlencode(params)
    return RedirectResponse(url, status_code=302)


def _provider_error_redirect(provider: str, code: str) -> RedirectResponse:
    """Generic redirect on provider-side failure — no internals leaked."""
    log.warning("oauth_callback_provider_error", provider=provider, error_code=code)
    return _redirect_to_login_with_reason("oauth_provider_error")


def _client_ip(request: Request) -> str | None:
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    if request.client is not None:
        return request.client.host
    return None


async def _issue_session_cookies(
    *,
    request: Request,
    response: Response,
    db: AsyncSession,
    user: User,
    account: Account,
) -> None:
    """Mint vlt_access + vlt_refresh, mirror to Postgres + Redis, rotate CSRF.

    Mirrors the cookie-issuing tail of POST /v1/auth/login. Kept inline
    rather than abstracted because the password flow has 2FA branches
    we don't share — refactoring would muddy diff.
    """
    redis = get_redis()
    if redis is None:
        # Same 503 contract as ``_require_redis`` in ``api/auth.py``.
        raise GatewayError(
            status_code=503,
            message="Session store unavailable.",
            type="api_error",
            code="redis_unavailable",
        )

    access_token, access_exp = create_access_token(user.id, account.id)
    refresh_token, refresh_exp, refresh_jti = create_refresh_token(user.id)

    await set_session_cookies(
        response=response,
        redis=redis,
        user_id=user.id,
        access_token=access_token,
        access_expires_at=access_exp,
        refresh_token=refresh_token,
        refresh_expires_at=refresh_exp,
        refresh_jti=refresh_jti,
    )
    await record_session(
        db,
        user_id=user.id,
        refresh_jti=refresh_jti,
        expires_at=refresh_exp,
        request=request,
    )
    csrf_token = generate_csrf_token()
    attach_csrf_cookie(response, csrf_token)


# ---------------------------------------------------------------------------
# /start — 302 to provider
# ---------------------------------------------------------------------------


@router.get(
    "/{provider}/start",
    summary="Start the OAuth flow — 302 to Google/Yandex",
    description=(
        "Mints a stateless HMAC-signed ``state`` token, builds the "
        "provider authorize URL, and 302s the user. ``mode=login`` is "
        "the default; ``mode=link`` requires an active dashboard "
        "session. The ``next`` query param is a SPA path the user "
        "returns to on success (sanitised against open-redirect)."
    ),
    responses={
        302: {"description": "Redirect to provider consent screen."},
        404: {"description": "Unknown provider."},
        503: {"description": "Provider not configured (no client_id/secret)."},
    },
)
async def start(
    provider: str,
    request: Request,
    next: str | None = Query(default=None, description="SPA path for post-login redirect"),
    mode: str = Query(default="login", pattern=r"^(login|link)$"),
) -> RedirectResponse:
    if provider not in _VALID_PROVIDERS:
        raise GatewayError(
            status_code=404,
            message="Unknown OAuth provider.",
            type="invalid_request_error",
            code="unknown_provider",
        )
    if not is_provider_configured(provider):
        raise GatewayError(
            status_code=503,
            message=f"OAuth provider '{provider}' is not configured.",
            type="api_error",
            code="oauth_not_configured",
        )

    user_id: uuid.UUID | None = None
    if mode == "link":
        # /start is a GET initiated by the SPA via a regular ``<a href>``,
        # so we can't run the full ``require_session`` (CSRF needs a
        # header that links don't carry). We require only that the
        # access cookie is present and valid; ``link`` mode is a
        # navigation, not a state change — the actual mutation happens
        # on /callback under our HMAC state.
        access_cookie = request.cookies.get("vlt_access")
        if not access_cookie:
            return _redirect_to_login_with_reason("session_expired")
        claims = verify_access_token(access_cookie)
        if claims is None:
            return _redirect_to_login_with_reason("session_expired")
        user_id = claims.user_id

    state_token = generate_state(
        provider=provider,  # type: ignore[arg-type]
        mode=mode,  # type: ignore[arg-type]
        next_path=_safe_next_path(next),
        user_id=user_id,
    )
    url = authorize_url(provider, state=state_token)
    return RedirectResponse(url, status_code=302)


# ---------------------------------------------------------------------------
# /callback — code exchange + login/signup/link
# ---------------------------------------------------------------------------


@router.get(
    "/{provider}/callback",
    summary="OAuth callback — exchange code, set cookies, redirect to SPA",
    description=(
        "Verifies the state token, exchanges the authorization code "
        "for a userinfo blob, then either logs the user in, signs them "
        "up (welcome 200 ₽ credit), or links the identity to the "
        "currently-signed-in user."
    ),
    responses={
        302: {"description": "Redirect to SPA on success/known failures."},
        404: {"description": "Unknown provider."},
    },
)
async def callback(
    provider: str,
    request: Request,
    response: Response,
    code: str | None = Query(default=None),
    state: str | None = Query(default=None),
    error: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
) -> Response:
    if provider not in _VALID_PROVIDERS:
        raise GatewayError(
            status_code=404,
            message="Unknown OAuth provider.",
            type="invalid_request_error",
            code="unknown_provider",
        )

    # User cancelled at the provider's consent screen.
    if error or not code or not state:
        return _redirect_to_login_with_reason("oauth_cancelled")

    payload = verify_state(state)
    if payload is None or payload.provider != provider:
        # Bad signature, expired, or provider mismatch ⇒ generic invalid_state.
        return _redirect_to_login_with_reason("oauth_invalid_state")

    try:
        userinfo = await exchange_code_for_userinfo(provider, code=code)
    except OAuthProviderError as exc:
        return _provider_error_redirect(provider, exc.code)
    except Exception as exc:  # pragma: no cover — defensive
        log.exception("oauth_callback_unexpected", provider=provider, error=str(exc))
        return _provider_error_redirect(provider, "unexpected")

    # Email is required (CEO 2026-05-06: refuse to create a user
    # without an email — would break billing receipts and password
    # recovery). Yandex login without email scope grant lands here.
    if not userinfo.email:
        return _redirect_to_login_with_reason("oauth_email_required")

    # ----- Mode dispatch -------------------------------------------------
    if payload.mode == "link":
        return await _handle_link(
            request=request,
            response=response,
            db=db,
            payload=payload,
            userinfo=userinfo,
        )
    return await _handle_login_or_signup(
        request=request,
        response=response,
        db=db,
        payload=payload,
        userinfo=userinfo,
    )


# ---------------------------------------------------------------------------
# Login / signup branch
# ---------------------------------------------------------------------------


async def _handle_login_or_signup(
    *,
    request: Request,
    response: Response,
    db: AsyncSession,
    payload: StatePayload,
    userinfo: OAuthUserInfo,
) -> Response:
    """Route the callback into login (existing identity) or signup (new)."""
    # 1) Existing identity? → login.
    existing_identity = await db.execute(
        select(OAuthIdentity).where(
            OAuthIdentity.provider == userinfo.provider,
            OAuthIdentity.subject == userinfo.subject,
        )
    )
    identity = existing_identity.scalar_one_or_none()
    if identity is not None:
        return await _login_via_identity(
            request=request,
            response=response,
            db=db,
            identity=identity,
            userinfo=userinfo,
            payload=payload,
        )

    # 2) Pre-existing user with the same email?
    email = normalise_email(userinfo.email or "")
    user_by_email = await db.execute(select(User).where(User.email == email))
    matched_user = user_by_email.scalar_one_or_none()

    if matched_user is not None:
        # CEO 2026-05-06: only auto-link if the provider says
        # email is verified. Yandex never sets the flag → manual link.
        if not userinfo.email_verified:
            return _redirect_to_login_with_reason(
                "oauth_email_taken",
                next_path=payload.next_path,
            )
        # Auto-link via verified email.
        identity = OAuthIdentity(
            user_id=matched_user.id,
            provider=userinfo.provider,
            subject=userinfo.subject,
            email_at_link=email,
            email_verified_at_link=True,
            display_name=userinfo.display_name,
            avatar_url=userinfo.avatar_url,
            linked_via="email_match",
            last_login_at=datetime.now(UTC),
        )
        db.add(identity)
        await write_audit(
            db,
            user_id=matched_user.id,
            account_id=None,
            action="oauth_linked_via_email_match",
            request=request,
            meta={
                "provider": userinfo.provider,
                "subject_hint": userinfo.subject[:8],
            },
        )
        await db.commit()
        return await _login_via_identity(
            request=request,
            response=response,
            db=db,
            identity=identity,
            userinfo=userinfo,
            payload=payload,
            already_audited=True,
        )

    # 3) Brand-new signup.
    return await _signup_via_oauth(
        request=request,
        response=response,
        db=db,
        userinfo=userinfo,
        payload=payload,
    )


async def _login_via_identity(
    *,
    request: Request,
    response: Response,
    db: AsyncSession,
    identity: OAuthIdentity,
    userinfo: OAuthUserInfo,
    payload: StatePayload,
    already_audited: bool = False,
) -> RedirectResponse:
    """Existing identity → mint cookies and 302 to the SPA."""
    user = await db.get(User, identity.user_id)
    if user is None:
        # Tombstoned user — should never happen via the FK CASCADE,
        # but if it does, surface a clean error rather than crash.
        return _redirect_to_login_with_reason("oauth_user_missing")
    # Resolve primary account.
    result = await db.execute(
        select(Account)
        .where(Account.owner_id == user.id)
        .order_by(Account.created_at.asc())
        .limit(1)
    )
    account = result.scalar_one_or_none()
    if account is None or account.status != AccountStatus.ACTIVE:
        return _redirect_to_login_with_reason("account_inactive")

    # Refresh display fields — cheap, keeps the dashboard fresh after
    # the user changes their Google avatar.
    identity.display_name = userinfo.display_name or identity.display_name
    identity.avatar_url = userinfo.avatar_url or identity.avatar_url
    identity.last_login_at = datetime.now(UTC)

    if not already_audited:
        await write_audit(
            db,
            user_id=user.id,
            account_id=account.id,
            action="oauth_login",
            request=request,
            meta={"provider": userinfo.provider},
        )

    # Issue cookies BEFORE commit so a failure in cookie-mint rolls back
    # the audit write too.
    redirect = RedirectResponse(_frontend_url(payload.next_path), status_code=302)
    await _issue_session_cookies(
        request=request,
        response=redirect,
        db=db,
        user=user,
        account=account,
    )
    await db.commit()
    return redirect


async def _signup_via_oauth(
    *,
    request: Request,
    response: Response,
    db: AsyncSession,
    userinfo: OAuthUserInfo,
    payload: StatePayload,
) -> RedirectResponse:
    """Brand-new user → create User + Account + identity + welcome credit."""
    email = normalise_email(userinfo.email or "")
    # Even though this is signup, ``email_verified`` on the user row
    # is set ONLY when the provider declared the email verified. For
    # Yandex (no verified flag), we land here with verified=False —
    # the user can still log in (we accept the OAuth identity as
    # proof of email control), but flows that gate on
    # ``email_verified`` (e.g. password recovery email) will require
    # a normal verify-email round trip if they ever set a password.
    user, account = await create_user_and_primary_account(
        db,
        email=email,
        password_hash=None,
        email_verified=userinfo.email_verified,
    )

    identity = OAuthIdentity(
        user_id=user.id,
        provider=userinfo.provider,
        subject=userinfo.subject,
        email_at_link=email,
        email_verified_at_link=userinfo.email_verified,
        display_name=userinfo.display_name,
        avatar_url=userinfo.avatar_url,
        linked_via="signup",
        last_login_at=datetime.now(UTC),
    )
    db.add(identity)

    # Welcome credit — same primitive that password /verify-email uses.
    granted = await grant_welcome_credit_by_email(
        db=db,
        user=user,
        email=email,
        ip=_client_ip(request),
    )

    await write_audit(
        db,
        user_id=user.id,
        account_id=account.id,
        action="oauth_signup",
        request=request,
        meta={
            "provider": userinfo.provider,
            "welcome_credit_granted": granted,
            "email_verified_by_provider": userinfo.email_verified,
        },
    )

    redirect = RedirectResponse(_frontend_url(payload.next_path), status_code=302)
    await _issue_session_cookies(
        request=request,
        response=redirect,
        db=db,
        user=user,
        account=account,
    )
    await db.commit()
    return redirect


# ---------------------------------------------------------------------------
# Link branch
# ---------------------------------------------------------------------------


async def _handle_link(
    *,
    request: Request,
    response: Response,
    db: AsyncSession,
    payload: StatePayload,
    userinfo: OAuthUserInfo,
) -> RedirectResponse:
    """Authenticated user wants to attach this provider identity."""
    if payload.user_id is None:
        return _redirect_to_login_with_reason("oauth_invalid_state")

    user = await db.get(User, payload.user_id)
    if user is None:
        return _redirect_to_login_with_reason("session_expired")

    # Identity already taken by another user?
    existing = await db.execute(
        select(OAuthIdentity).where(
            OAuthIdentity.provider == userinfo.provider,
            OAuthIdentity.subject == userinfo.subject,
        )
    )
    other = existing.scalar_one_or_none()
    if other is not None:
        if other.user_id == user.id:
            # Already linked — idempotent success.
            return RedirectResponse(
                _frontend_url(payload.next_path) + "?linked=already",
                status_code=302,
            )
        return RedirectResponse(
            _frontend_url("/app/settings/security") + "?error=identity_taken",
            status_code=302,
        )

    identity = OAuthIdentity(
        user_id=user.id,
        provider=userinfo.provider,
        subject=userinfo.subject,
        email_at_link=normalise_email(userinfo.email or "") or None,
        email_verified_at_link=userinfo.email_verified,
        display_name=userinfo.display_name,
        avatar_url=userinfo.avatar_url,
        linked_via="settings",
        last_login_at=datetime.now(UTC),
    )
    db.add(identity)
    await write_audit(
        db,
        user_id=user.id,
        account_id=None,
        action="oauth_linked_via_settings",
        request=request,
        meta={"provider": userinfo.provider},
    )
    await db.commit()
    return RedirectResponse(
        _frontend_url("/app/settings/security") + "?linked=ok",
        status_code=302,
    )


# ---------------------------------------------------------------------------
# /disconnect
# ---------------------------------------------------------------------------


class DisconnectRequest(BaseModel):
    provider: str = Field(min_length=1, max_length=32)


@router.post(
    "/disconnect",
    summary="Disconnect a linked OAuth identity",
    description=(
        "Removes the (provider, subject) row for the signed-in user. "
        "Refuses if it would lock the user out — i.e. they have no "
        "password and no other OAuth identity. The CEO rule "
        "(2026-05-06) is strict: never leave a user without a way to "
        "log in."
    ),
    responses={
        200: {"description": "Identity disconnected."},
        400: {"description": "Cannot disconnect last login method."},
        404: {"description": "No such identity for this user."},
    },
)
async def disconnect(
    payload: DisconnectRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    principal: SessionPrincipal = Depends(require_session),
) -> dict[str, object]:
    if payload.provider not in _VALID_PROVIDERS:
        raise invalid_request(
            "Unknown OAuth provider.",
            param="provider",
            code="unknown_provider",
        )

    user = principal.user

    target = await db.execute(
        select(OAuthIdentity).where(
            OAuthIdentity.user_id == user.id,
            OAuthIdentity.provider == payload.provider,
        )
    )
    identity = target.scalar_one_or_none()
    if identity is None:
        raise GatewayError(
            status_code=404,
            message="No such linked identity.",
            type="invalid_request_error",
            code="identity_not_found",
        )

    # Lockout guard — count remaining login methods after deletion.
    has_password = _has_usable_password(user)
    other_identities = await db.execute(
        select(OAuthIdentity.id).where(
            OAuthIdentity.user_id == user.id,
            OAuthIdentity.provider != payload.provider,
        )
    )
    other_count = len(other_identities.scalars().all())
    if not has_password and other_count == 0:
        raise GatewayError(
            status_code=400,
            message=("Set a password before disconnecting your last login method."),
            type="invalid_request_error",
            code="last_login_method",
        )

    await db.delete(identity)
    await write_audit(
        db,
        user_id=user.id,
        account_id=principal.account.id,
        action="oauth_disconnected",
        request=request,
        meta={"provider": payload.provider},
    )
    await db.commit()
    return {"ok": True, "provider": payload.provider}


def _has_usable_password(user: User) -> bool:
    """Return True iff the password hash is non-placeholder.

    OAuth-only accounts get a deterministic placeholder hash (see
    ``signup_helpers._unusable_password_hash``); we identify them by
    the embedded ``b2F1dGgtb25seQ`` (base64 of "oauth-only") salt
    segment. Production password hashes have a random salt, so a
    collision is cryptographically infeasible.
    """
    if not user.password_hash:
        return False
    return "b2F1dGgtb25seQ" not in user.password_hash


# ---------------------------------------------------------------------------
# /identities — list for the security settings page
# ---------------------------------------------------------------------------


class IdentityResponse(BaseModel):
    provider: str
    email_at_link: str | None
    email_verified_at_link: bool
    display_name: str | None
    avatar_url: str | None
    linked_via: str
    linked_at: datetime
    last_login_at: datetime | None


@router.get(
    "/identities",
    summary="List the caller's linked OAuth identities",
    description=(
        "Returns one row per linked (provider, subject). Used by "
        "/app/settings/security to render Connect/Disconnect buttons."
    ),
)
async def list_identities(
    db: AsyncSession = Depends(get_db),
    principal: SessionPrincipal = Depends(require_session),
) -> dict[str, list[IdentityResponse]]:
    rows = await db.execute(
        select(OAuthIdentity)
        .where(OAuthIdentity.user_id == principal.user.id)
        .order_by(OAuthIdentity.created_at.asc())
    )
    items = [
        IdentityResponse(
            provider=r.provider,
            email_at_link=r.email_at_link,
            email_verified_at_link=r.email_verified_at_link,
            display_name=r.display_name,
            avatar_url=r.avatar_url,
            linked_via=r.linked_via,
            linked_at=r.created_at,
            last_login_at=r.last_login_at,
        )
        for r in rows.scalars().all()
    ]
    return {"identities": items}


__all__ = ["router"]
