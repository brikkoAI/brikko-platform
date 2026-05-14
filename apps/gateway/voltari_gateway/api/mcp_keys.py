"""Management API — Brikko-MCP token CRUD (Sprint MCP S1).

Mounted under ``/v1/mcp/tokens``. All endpoints are session-cookie protected
via ``require_session`` and enforce double-submit CSRF on mutating verbs
(same contract as ``/v1/keys``).

Surface:

    GET    /v1/mcp/tokens           — list tokens for the active account
    POST   /v1/mcp/tokens           — create a token (returns plaintext **once**)
    PATCH  /v1/mcp/tokens/{id}      — rename only (scope is immutable post-create)
    DELETE /v1/mcp/tokens/{id}      — soft-delete: status=REVOKED + revoked_at=now()

This is the S1 sister of ``voltari_gateway.api.keys`` — same patterns (argon2
hash, 14-char prefix lookup, soft-revoke, 404-not-403 for cross-account
probes, tariff caps, audit log). The deliberate differences:

* **Distinct plaintext literal** ``mcp-brk-*`` so the credential is visually
  obvious in screen-shares / pasted logs / clipboard managers.
* **Distinct scope enum** (``read_account|read_usage|recommend_model``). Each
  maps to a single MCP tool exposed by the S2 server.
* **No bulk operations in S1**. Bulk-revoke / bulk-rotate matter when a
  team has 50+ keys; MCP tokens are 1-per-developer-machine. KISS for now.
* **Higher tariff caps**. MCP tokens are stateless per-machine — Claude
  Desktop + Cursor + Continue on one laptop = 3 tokens. We don't want a
  Pro user with 3 IDEs to hit the cap immediately.

Tariff limits (S1 v1):

    PAYG = 5, PRO = 15, PRO_PRIVACY = 15, TEAM = 30, BUSINESS = 100,
    BUSINESS_PLUS = unlimited.

Counted against **non-revoked** tokens only — revoking frees a slot.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, Path, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from voltari_gateway.auth.audit import write_audit
from voltari_gateway.auth.keys import generate_mcp_token
from voltari_gateway.auth.session_middleware import (
    SessionPrincipal,
    require_session,
)
from voltari_gateway.db.models import (
    McpScope,
    McpToken,
    McpTokenStatus,
    Tariff,
)
from voltari_gateway.db.session import get_db
from voltari_gateway.utils.errors import GatewayError
from voltari_gateway.utils.logging import get_logger

log = get_logger(__name__)

router = APIRouter(tags=["mcp-tokens"])

# Per-tariff active-token cap. ``None`` = unlimited (Business+ — individually
# negotiated). Counted against status != REVOKED so revoke frees a slot.
#
# CEO 2026-05-11: каждый разработчик ставит MCP в Claude Desktop + Cursor +
# Continue одновременно → 3 токена на одну машину. PAYG=5 даёт запас. PRO=15
# покрывает кейс "три члена команды × 3-5 машин" без апгрейда.
MCP_TOKEN_LIMITS_BY_TARIFF: dict[Tariff, int | None] = {
    Tariff.PAYG: 5,
    Tariff.PRO: 15,
    Tariff.PRO_PRIVACY: 15,
    Tariff.TEAM: 30,
    Tariff.BUSINESS: 100,
    Tariff.BUSINESS_PLUS: None,
}


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------


class McpTokenListItem(BaseModel):
    """Compact token descriptor — never includes plaintext."""

    id: str
    name: str
    prefix: str
    scope: str
    status: str
    last_used_at: datetime | None = None
    created_at: datetime
    revoked_at: datetime | None = None
    expires_at: datetime | None = None


# Allowed expiry presets — mirrors ``api/keys.py``. Open-ended ``int`` is
# rejected to keep the dashboard consistent and policy decisions easy.
McpExpiresInDays = Literal[30, 90, 180, 365]


class CreateMcpTokenRequest(BaseModel):
    """Body of POST /v1/mcp/tokens."""

    name: str = Field(min_length=1, max_length=255)
    # Single scope per token (S1 KISS). Frontend renders a dropdown.
    # S3 (2026-05-12) extends the enum with 4 read-only catalog scopes +
    # ``all`` wildcard. ``all`` is the default for new tokens — matches
    # the helper-skill's one-prompt onboarding flow. Restrictive tokens
    # (single specific scope) remain a valid choice for power users.
    scope: Literal[
        "read_account",
        "read_usage",
        "recommend_model",
        "list_models",
        "read_traces",
        "list_cookbook",
        "list_integrations",
        "all",
    ] = Field(default="all")
    expires_in_days: McpExpiresInDays | None = Field(default=None)

    model_config = {"extra": "forbid"}


class CreateMcpTokenResponse(BaseModel):
    """One-shot reveal of the plaintext. Never log this body."""

    id: str
    name: str
    full_token: str
    prefix: str
    scope: str
    created_at: datetime
    expires_at: datetime | None = None


class UpdateMcpTokenRequest(BaseModel):
    """PATCH body — only ``name`` is honoured.

    ``scope`` is intentionally immutable; to change permissions, create a
    new token and revoke the old one. ``extra='forbid'`` so the SPA fails
    fast (400) instead of silently dropping unknown fields.
    """

    name: str = Field(min_length=1, max_length=255)

    model_config = {"extra": "forbid"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _to_list_item(token: McpToken) -> McpTokenListItem:
    return McpTokenListItem(
        id=str(token.id),
        name=token.name,
        prefix=token.token_prefix,
        scope=token.scope.value,
        status=token.status.value,
        last_used_at=token.last_used_at,
        created_at=token.created_at,
        revoked_at=token.revoked_at,
        expires_at=token.expires_at,
    )


def _resolve_scope(api_scope: str) -> McpScope:
    """Map the API-facing literal onto the DB enum. 1-to-1 mapping in S1."""
    return McpScope(api_scope)


async def _count_active_tokens(db: AsyncSession, account_id: uuid.UUID) -> int:
    """Count non-revoked tokens for the account (limit-check denominator)."""
    stmt = (
        select(func.count())
        .select_from(McpToken)
        .where(McpToken.account_id == account_id)
        .where(McpToken.status != McpTokenStatus.REVOKED)
    )
    return int((await db.execute(stmt)).scalar_one())


async def _get_owned_token(
    db: AsyncSession, token_id: uuid.UUID, account_id: uuid.UUID
) -> McpToken:
    """Fetch a token that belongs to ``account_id`` or 404.

    Returning 404 (not 403) for tokens of *other* accounts avoids leaking
    existence — an attacker probing UUIDs sees the same response regardless
    of whether the token exists.
    """
    token = await db.get(McpToken, token_id)
    if token is None or token.account_id != account_id:
        raise GatewayError(
            status_code=404,
            message="MCP token not found.",
            type="invalid_request_error",
            code="mcp_token_not_found",
        )
    return token


# ---------------------------------------------------------------------------
# 1) GET /v1/mcp/tokens
# ---------------------------------------------------------------------------


@router.get(
    "",
    response_model=list[McpTokenListItem],
    summary="List MCP tokens for the active account",
    description=(
        "Returns active and revoked tokens (sorted by creation time, newest "
        "first). Plaintext is **never** returned here — only ``prefix`` "
        "(first 14 chars). Frontend filters revoked client-side."
    ),
    responses={
        200: {"description": "List of tokens (possibly empty)."},
        401: {"description": "Session cookie missing or expired."},
    },
)
async def list_tokens(
    db: AsyncSession = Depends(get_db),
    principal: SessionPrincipal = Depends(require_session),
) -> list[McpTokenListItem]:
    """List all MCP tokens for the active account, including revoked ones."""
    stmt = (
        select(McpToken)
        .where(McpToken.account_id == principal.account.id)
        .order_by(McpToken.created_at.desc())
    )
    result = await db.execute(stmt)
    return [_to_list_item(t) for t in result.scalars().all()]


# ---------------------------------------------------------------------------
# 2) POST /v1/mcp/tokens
# ---------------------------------------------------------------------------


@router.post(
    "",
    response_model=CreateMcpTokenResponse,
    status_code=201,
    summary="Create a new MCP token",
    description=(
        "Generates a fresh ``mcp-brk-...`` token. The plaintext is shown "
        "**only once** in the response — it's not recoverable later. "
        "Optional ``expires_in_days`` (30/90/180/365) sets a hard expiry; "
        "omit for never-expires."
    ),
    responses={
        201: {"description": "Token created; plaintext returned **once**."},
        401: {"description": "Session cookie missing or expired."},
        403: {"description": "Tariff per-token limit reached."},
    },
)
async def create_token(
    payload: CreateMcpTokenRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    principal: SessionPrincipal = Depends(require_session),
) -> CreateMcpTokenResponse:
    """Create a new MCP token. Plaintext is shown once."""
    account = principal.account

    # --- Tariff cap ---
    limit = MCP_TOKEN_LIMITS_BY_TARIFF.get(account.tariff)
    if limit is not None:
        active = await _count_active_tokens(db, account.id)
        if active >= limit:
            raise GatewayError(
                status_code=403,
                message=(
                    f"Tariff '{account.tariff.value}' allows up to {limit} "
                    f"active MCP tokens. Revoke an existing token or upgrade."
                ),
                type="invalid_request_error",
                code="mcp_token_limit_reached",
            )

    scope_enum = _resolve_scope(payload.scope)

    expires_at: datetime | None = None
    if payload.expires_in_days is not None:
        expires_at = datetime.now(UTC) + timedelta(days=payload.expires_in_days)

    # --- Generate + persist ---
    generated = generate_mcp_token()
    token = McpToken(
        account_id=account.id,
        name=payload.name.strip(),
        token_hash=generated.token_hash,
        token_prefix=generated.prefix,
        scope=scope_enum,
        status=McpTokenStatus.ACTIVE,
        expires_at=expires_at,
    )
    db.add(token)
    await db.flush()  # need id for audit

    await write_audit(
        db,
        user_id=principal.user.id,
        account_id=account.id,
        action="mcp_token_created",
        request=request,
        meta={
            "token_id": str(token.id),
            "scope": scope_enum.value,
            "expires_at": expires_at.isoformat() if expires_at else None,
        },
    )
    await db.commit()
    await db.refresh(token)

    log.info(
        "mcp_token_created",
        account_id=str(account.id),
        token_id=str(token.id),
        scope=scope_enum.value,
        expires_at=expires_at.isoformat() if expires_at else None,
    )

    return CreateMcpTokenResponse(
        id=str(token.id),
        name=token.name,
        full_token=generated.plaintext,
        prefix=token.token_prefix,
        scope=token.scope.value,
        created_at=token.created_at,
        expires_at=token.expires_at,
    )


# ---------------------------------------------------------------------------
# 3) PATCH /v1/mcp/tokens/{token_id}
# ---------------------------------------------------------------------------


@router.patch(
    "/{token_id}",
    response_model=McpTokenListItem,
    summary="Rename an MCP token",
    description=(
        "Updates only the ``name``. ``scope`` is intentionally immutable — "
        "to change permissions, create a new token and revoke the old one."
    ),
    responses={
        200: {"description": "Token renamed."},
        400: {"description": "Body contained ``scope`` (forbidden field)."},
        404: {"description": "Token not found or belongs to another account."},
    },
)
async def update_token(
    payload: UpdateMcpTokenRequest,
    token_id: uuid.UUID = Path(...),
    db: AsyncSession = Depends(get_db),
    principal: SessionPrincipal = Depends(require_session),
) -> McpTokenListItem:
    """Rename a token. Scope is intentionally immutable post-create."""
    token = await _get_owned_token(db, token_id, principal.account.id)
    token.name = payload.name.strip()
    await db.commit()
    await db.refresh(token)
    return _to_list_item(token)


# ---------------------------------------------------------------------------
# 4) DELETE /v1/mcp/tokens/{token_id}
# ---------------------------------------------------------------------------


@router.delete(
    "/{token_id}",
    status_code=204,
    summary="Revoke an MCP token (soft delete)",
    description=(
        "Sets ``status=REVOKED`` + ``revoked_at=now()``. The row is NOT "
        "hard-deleted — it stays for audit/forensics. Once the S2 MCP "
        "server lands its bearer-auth Redis cache will be purged here too."
    ),
    responses={
        204: {"description": "Token revoked."},
        404: {"description": "Token not found, already revoked, or other account."},
    },
)
async def revoke_token(
    request: Request,
    token_id: uuid.UUID = Path(...),
    db: AsyncSession = Depends(get_db),
    principal: SessionPrincipal = Depends(require_session),
) -> Response:
    """Soft-delete an MCP token.

    Hard-delete is intentionally avoided: revoke must be reversible-by-audit
    even if the token itself can never be reactivated. S2 will add Redis
    cache purge for the bearer-auth path; S1 is pure DB.
    """
    token = await _get_owned_token(db, token_id, principal.account.id)

    if token.status == McpTokenStatus.REVOKED:
        # Already revoked — treat as 404 so callers can't probe whether a
        # token *was* revoked (timing-side-channel for enumeration).
        raise GatewayError(
            status_code=404,
            message="MCP token not found.",
            type="invalid_request_error",
            code="mcp_token_not_found",
        )

    token.status = McpTokenStatus.REVOKED
    token.revoked_at = datetime.now(UTC)

    await write_audit(
        db,
        user_id=principal.user.id,
        account_id=principal.account.id,
        action="mcp_token_revoked",
        request=request,
        meta={"token_id": str(token.id)},
    )
    await db.commit()

    log.info(
        "mcp_token_revoked",
        account_id=str(principal.account.id),
        token_id=str(token.id),
    )
    return Response(status_code=204)


__all__ = ["router"]
