"""Session authentication for the management API (cookie-based, not Bearer).

This is *separate* from ``auth/middleware.py`` — that one authenticates
end-user API traffic (``Authorization: Bearer sk-vlt-...``) for the OpenAI-
compatible endpoints. The management API talks to the SPA dashboard via
HttpOnly cookies + JWT.

Pipeline (per-request):

    1. CSRF gate — for any mutating method (POST/PATCH/PUT/DELETE) we
       require either the new double-submit pattern (``X-CSRF-Token``
       header matching the ``vlt_csrf`` cookie via constant-time
       compare) OR the legacy ``X-Requested-With: voltari-web`` header
       during the migration window (TD-033 schedules the cutover).
       See ``voltari_gateway.auth.csrf`` for the implementation.
    2. Read access cookie ``vlt_access`` → verify JWT → load User + Account.
    3. Attach ``request.state.user`` and ``request.state.account``.

Failures all collapse to OpenAI-style envelopes via ``utils/errors.py`` so
the SPA gets one consistent error format.
"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Cookie, Depends, Header, Request
from sqlalchemy.ext.asyncio import AsyncSession

from voltari_gateway.auth.csrf import CSRF_COOKIE, CSRF_HEADER, verify_csrf
from voltari_gateway.auth.session import verify_access_token
from voltari_gateway.db.models import Account, AccountStatus, User
from voltari_gateway.db.session import get_db
from voltari_gateway.utils.errors import authentication_error
from voltari_gateway.utils.logging import get_logger

log = get_logger(__name__)


@dataclass(frozen=True)
class SessionPrincipal:
    """The fully-resolved cookie-authenticated caller."""

    user: User
    account: Account


def enforce_csrf(
    request: Request,
    *,
    csrf_cookie: str | None = None,
    csrf_header: str | None = None,
) -> None:
    """Run the CSRF gate on a mutating cookie-auth request.

    Thin shim around ``auth.csrf.verify_csrf`` that pulls the cookie /
    header values from the request when not explicitly passed (handy for
    legacy callers that didn't have them in their dependency tree).
    """
    if csrf_cookie is None:
        csrf_cookie = request.cookies.get(CSRF_COOKIE)
    if csrf_header is None:
        csrf_header = request.headers.get(CSRF_HEADER)
    verify_csrf(request, csrf_cookie=csrf_cookie, csrf_header=csrf_header)


async def require_session(
    request: Request,
    vlt_access: str | None = Cookie(default=None),
    csrf_cookie: str | None = Cookie(default=None, alias=CSRF_COOKIE),
    csrf_header: str | None = Header(default=None, alias=CSRF_HEADER),
    db: AsyncSession = Depends(get_db),
) -> SessionPrincipal:
    """FastAPI dependency: resolve a cookie-authenticated session.

    Raises:
        GatewayError 403 — CSRF token missing/invalid on a mutating request.
        GatewayError 401 — no cookie, expired/invalid JWT, deleted user, or
                           suspended/closed account.
    """
    # 1) CSRF gate (cheap; bail out before crypto work).
    enforce_csrf(request, csrf_cookie=csrf_cookie, csrf_header=csrf_header)

    # 2) Cookie present?
    if not vlt_access:
        raise authentication_error("Authentication required.")

    claims = verify_access_token(vlt_access)
    if claims is None:
        raise authentication_error("Session expired or invalid.")

    # 3) User + account still exist and are active?
    user = await db.get(User, claims.user_id)
    if user is None:
        raise authentication_error("Session user no longer exists.")

    account = await db.get(Account, claims.account_id)
    if account is None:
        raise authentication_error("Session account no longer exists.")
    # Sprint 7 — closed_at is post-cron. Surface 403 (matches what the
    # closure POST returns when called on a closed account) so the SPA
    # can redirect to a "this account is closed" landing rather than
    # bouncing to /login. Grace period (closure_scheduled_at set,
    # closed_at NULL) is intentionally NOT blocked.
    if account.closed_at is not None:
        from voltari_gateway.utils.errors import GatewayError

        raise GatewayError(
            status_code=403,
            message="Account is closed.",
            type="invalid_request_error",
            code="account_closed",
        )
    if account.status != AccountStatus.ACTIVE:
        raise authentication_error("Account is not active.")

    # Sanity: claim account must belong to this user (owner *or* via seat).
    # We allow seat-based access without an extra query in the hot path; the
    # cookie was minted by /login which already checked ownership/seat.
    # If you need to defend against stolen access tokens after a seat-revoke,
    # cut TTL or add a Redis blacklist on the access JTI — out of scope here.

    principal = SessionPrincipal(user=user, account=account)
    request.state.user = user
    request.state.account = account
    request.state.session_principal = principal
    return principal
