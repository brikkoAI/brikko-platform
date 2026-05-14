"""CSRF double-submit token (FE P0-5 + backend coord).

Why this exists
---------------

The original CSRF defence was a hardcoded check: any mutating cookie-auth
request had to carry ``X-Requested-With: voltari-web``. Custom request
headers can't be set cross-origin under the standard CORS rules, so the
header itself is a non-trivial barrier — but its value is a known
constant, which means:

1. A subdomain compromise that lets an attacker run JS on
   ``*.brikko.ru`` would happily set the header.
2. Any tooling that auto-injects custom headers into a "voltari-web"-like
   wrapper would unintentionally pass the gate.

The double-submit pattern is the industry-standard fix:

1. Server hands the SPA a random 64-char token via ``GET /v1/auth/csrf``.
2. The same value is also written to a cookie ``vlt_csrf`` —
   ``HttpOnly=false`` so the SPA can read it back if needed, but in
   practice we cache it from the response body.
3. On every mutating cookie-auth request the SPA sends
   ``X-CSRF-Token: <token>``. The server requires the header value to
   match the cookie value (constant-time compare).

For an attacker this means: even with ``SameSite=Lax``, even with a
header-injection trick, they can't match the cookie value because
they can't read it from the victim's domain (cookie is scoped to
brikko.ru and the attacker is on another origin).

Token mechanics
---------------

* Generated with ``secrets.token_urlsafe(48)`` → 64 characters of
  url-safe base64.
* Stored in cookie + delivered in JSON. The cookie has the same
  lifetime as the refresh token (30 days, ``JWT_REFRESH_TTL_DAYS``).
* Constant-time compare (``hmac.compare_digest``).
* Renewed on every ``/v1/auth/csrf`` call. No persistence — we don't
  need server-side state because the token's role is purely to make
  cross-origin matching impossible.

Sprint 3 cutover (TD-036)
-------------------------

The legacy ``X-Requested-With: voltari-web`` fallback was removed in
Sprint 3 Поток H. Frontend (Поток I) migrated to the double-submit
pair (``X-CSRF-Token`` header + ``vlt_csrf`` cookie) at the end of
Sprint 2. Any client still sending the legacy header alone is rejected
with 403 ``csrf_invalid``; the docstring/protocol doc is updated to
show only the supported flow.
"""

from __future__ import annotations

import hmac
import secrets
from datetime import UTC, datetime, timedelta

from fastapi import Cookie, Header, Request, Response

from voltari_gateway.config import get_settings
from voltari_gateway.utils.errors import GatewayError

CSRF_COOKIE = "vlt_csrf"
CSRF_HEADER = "X-CSRF-Token"
CSRF_TOKEN_BYTES = 48  # → 64 url-safe base64 chars

_MUTATING_METHODS = frozenset({"POST", "PATCH", "PUT", "DELETE"})


def generate_csrf_token() -> str:
    """Mint a new CSRF token. 64 url-safe base64 chars."""
    return secrets.token_urlsafe(CSRF_TOKEN_BYTES)


def attach_csrf_cookie(response: Response, token: str) -> None:
    """Write ``token`` to the ``vlt_csrf`` cookie.

    ``HttpOnly=false`` so the SPA can read the cookie if needed
    (the recommended path is reading the token from the JSON
    response body, but having both belts AND braces avoids a SPA
    refresh losing the token).

    ``SameSite=Strict`` so a third-party site can't even cause the
    cookie to be sent on a top-level navigation. (We're cookie-only;
    no top-level POSTs from third parties.)

    Lifetime mirrors the refresh-cookie window (30 days, configurable
    via ``JWT_REFRESH_TTL_DAYS``) — when the user logs out, both go
    away together.
    """
    settings = get_settings()
    domain = settings.cookie_domain or None
    expires_at = datetime.now(UTC) + timedelta(days=settings.jwt_refresh_ttl_days)
    max_age = max(1, int((expires_at - datetime.now(UTC)).total_seconds()))

    response.set_cookie(
        key=CSRF_COOKIE,
        value=token,
        max_age=max_age,
        domain=domain,
        path="/",
        secure=settings.cookie_secure,
        httponly=False,  # readable by SPA (deliberate)
        samesite="strict",
    )


def clear_csrf_cookie(response: Response) -> None:
    """Drop the CSRF cookie. Called from the logout endpoint."""
    settings = get_settings()
    response.delete_cookie(
        key=CSRF_COOKIE,
        domain=settings.cookie_domain or None,
        path="/",
    )


def _csrf_invalid_error() -> GatewayError:
    return GatewayError(
        status_code=403,
        message="CSRF token missing or invalid.",
        type="invalid_request_error",
        code="csrf_invalid",
    )


def verify_csrf(
    request: Request,
    *,
    csrf_cookie: str | None,
    csrf_header: str | None,
) -> None:
    """Raise 403 if a mutating cookie-auth request fails the CSRF check.

    Decision tree (post-TD-036):

    * Non-mutating method (GET/HEAD/OPTIONS) → no-op. CSRF only matters
      for state changes; safe verbs by definition shouldn't have side
      effects.
    * Mutating method:
        * Both ``X-CSRF-Token`` header and ``vlt_csrf`` cookie present
          AND values match (constant-time) → pass.
        * Otherwise → 403 ``csrf_invalid``.

    The legacy ``X-Requested-With: voltari-web`` fallback was removed
    in Sprint 3 Поток H (TD-036). Clients that haven't migrated to the
    double-submit pair are now rejected; the dashboard mints a token
    with ``GET /v1/auth/csrf`` on first load and reuses it for the rest
    of the session.
    """
    if request.method.upper() not in _MUTATING_METHODS:
        return

    if not csrf_header or not csrf_cookie:
        raise _csrf_invalid_error()

    if not hmac.compare_digest(csrf_header, csrf_cookie):
        raise _csrf_invalid_error()


# ------------------------------------------------------------------------
# FastAPI dependency wrappers
# ------------------------------------------------------------------------


async def csrf_required(
    request: Request,
    csrf_cookie: str | None = Cookie(default=None, alias=CSRF_COOKIE),
    csrf_header: str | None = Header(default=None, alias=CSRF_HEADER),
) -> None:
    """Reusable dependency — call from any endpoint that needs CSRF.

    Most callers don't use this directly; the cookie-auth flow in
    ``require_session`` / ``require_api_key_or_session`` calls
    ``verify_csrf`` itself so the API surface stays the same.
    """
    verify_csrf(
        request,
        csrf_cookie=csrf_cookie,
        csrf_header=csrf_header,
    )


__all__ = [
    "CSRF_COOKIE",
    "CSRF_HEADER",
    "attach_csrf_cookie",
    "clear_csrf_cookie",
    "csrf_required",
    "generate_csrf_token",
    "verify_csrf",
]
