"""FastAPI dependencies for OAuth scope enforcement.

Two dependencies:

* ``require_oauth_scope(scope)`` — strict OAuth-only. Used by
  Studio-exclusive endpoints (none today, kept for completeness).
* ``require_api_key_or_oauth_scope(scope)`` — additive: existing
  ``sk-vlt-…`` / ``sk-brk-…`` API keys keep working unchanged. If the
  Bearer token parses as an OAuth JWT (correct ``aud``+``type``), we
  enforce the scope; otherwise we fall through to the existing
  ``require_api_key`` dependency. This is what every chat / messages /
  embeddings / audio endpoint uses post-pre-M0.

Why this design rather than a unified principal class:

* Existing ``AuthPrincipal`` is wired through 3500+ LOC of handlers and
  carries cached billing fields (balance, tariff, routing prefs).
  Reshaping it to also carry scopes would be a much bigger PR. Instead
  the new dependency emits an ``AuthPrincipal`` (so the rest of the
  handler doesn't change) but stamps a ``request.state.oauth_scopes``
  list for any code that wants to assert finer-grained authorisation.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Header, Request
from sqlalchemy.ext.asyncio import AsyncSession

from voltari_gateway.auth.middleware import (
    AuthPrincipal,
    _attach_to_request,
    require_api_key,
)
from voltari_gateway.auth.oauth_scopes import OAuthScope
from voltari_gateway.auth.oauth_tokens import (
    verify_oauth_access_token,
)
from voltari_gateway.db.models import Account, AccountStatus, User
from voltari_gateway.db.session import get_db
from voltari_gateway.utils.errors import GatewayError, authentication_error


@dataclass(frozen=True)
class OAuthPrincipal:
    """Resolved OAuth caller — carries scopes alongside billing fields."""

    user_id: uuid.UUID
    account_id: uuid.UUID
    client_id: str
    scopes: list[OAuthScope]


def _extract_bearer(authorization: str | None) -> str | None:
    if not authorization:
        return None
    parts = authorization.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    token = parts[1].strip()
    return token or None


async def _resolve_oauth(
    *,
    authorization: str | None,
    db: AsyncSession,
) -> OAuthPrincipal | None:
    """Try to resolve the Bearer header as an OAuth JWT. None on no/invalid OAuth.

    NB: a Bearer that *might* be an OAuth token but fails the verifier
    returns None — the caller may then try the API-key path. False
    positives (a malformed sk-… that happens to JWT-parse) are
    impossible because ``verify_oauth_access_token`` insists on the
    issuer/audience/type triple.
    """
    plaintext = _extract_bearer(authorization)
    if plaintext is None:
        return None
    claims = verify_oauth_access_token(plaintext)
    if claims is None:
        return None

    # Verify user + account still exist and are active. Mirrors the
    # checks in auth/middleware.py._verify_against_db.
    user = await db.get(User, claims.user_id)
    if user is None:
        raise authentication_error("OAuth token user no longer exists.")
    account = await db.get(Account, claims.account_id)
    if account is None:
        raise authentication_error("OAuth token account no longer exists.")
    if account.closed_at is not None:
        raise authentication_error("Account is closed.")
    if account.status != AccountStatus.ACTIVE:
        raise authentication_error("Account is not active.")

    from voltari_gateway.auth.oauth_scopes import parse_scope_string

    return OAuthPrincipal(
        user_id=claims.user_id,
        account_id=claims.account_id,
        client_id=claims.client_id,
        scopes=parse_scope_string(" ".join(claims.scopes)),
    )


def _insufficient_scope(scope: OAuthScope) -> GatewayError:
    return GatewayError(
        status_code=403,
        message=f"This endpoint requires the '{scope.value}' OAuth scope.",
        type="invalid_request_error",
        code="insufficient_scope",
    )


def require_oauth_scope(scope: OAuthScope) -> Callable[..., Awaitable[OAuthPrincipal]]:
    """Strict OAuth-only dependency factory."""

    async def _dep(
        authorization: Annotated[str | None, Header()] = None,
        db: Annotated[AsyncSession, Depends(get_db)] = ...,  # type: ignore[assignment]
    ) -> OAuthPrincipal:
        principal = await _resolve_oauth(authorization=authorization, db=db)
        if principal is None:
            raise authentication_error("Missing or invalid OAuth Bearer token.")
        if scope not in principal.scopes:
            raise _insufficient_scope(scope)
        return principal

    return _dep


def require_api_key_or_oauth_scope(
    scope: OAuthScope,
) -> Callable[..., Awaitable[AuthPrincipal]]:
    """Additive dependency: OAuth-with-scope OR existing API key.

    Returns the SAME ``AuthPrincipal`` shape regardless of auth path so
    the handler doesn't need a branch. When OAuth was used, the principal
    carries ``request.state.oauth_scopes`` (list of OAuthScope) for any
    code that wants to assert finer-grained authorisation.
    """

    async def _dep(
        request: Request,
        authorization: Annotated[str | None, Header()] = None,
        db: Annotated[AsyncSession, Depends(get_db)] = ...,  # type: ignore[assignment]
    ) -> AuthPrincipal:
        oauth = await _resolve_oauth(authorization=authorization, db=db)
        if oauth is not None:
            if scope not in oauth.scopes:
                raise _insufficient_scope(scope)
            # Build an AuthPrincipal (cached billing fields filled from DB row).
            account = await db.get(Account, oauth.account_id)
            assert account is not None  # _resolve_oauth re-fetched and validated
            principal = AuthPrincipal(
                account_id=account.id,
                api_key_id=oauth.account_id,  # placeholder; not a real key
                user_id=account.owner_id,
                tariff=account.tariff.value,
                balance_kopecks=account.balance_kopecks,
                store_prompts=account.store_prompts,
                pii_masking_enabled=account.pii_masking_enabled,
                routing_mode=account.routing_mode,
                routing_strategy=account.routing_strategy,
                routing_allowed_providers=(
                    tuple(account.routing_allowed_providers)
                    if account.routing_allowed_providers is not None
                    else None
                ),
                routing_allowed_models=(
                    tuple(account.routing_allowed_models)
                    if account.routing_allowed_models is not None
                    else None
                ),
            )
            _attach_to_request(request, principal)
            request.state.oauth_scopes = oauth.scopes
            request.state.oauth_client_id = oauth.client_id
            return principal

        # Fall through to the existing API-key path.
        return await require_api_key(request=request, authorization=authorization, db=db)

    return _dep
