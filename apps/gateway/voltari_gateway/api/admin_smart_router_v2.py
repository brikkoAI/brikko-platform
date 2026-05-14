"""Admin endpoint — per-account Smart Router v2 opt-in flip.

Sprint S1 (2026-05-13). Design doc §6 / Q7 answer.

What this endpoint does
-----------------------

``POST /v1/account/admin/smart_router_v2/{account_id}``

Body::

    {"enabled": true}    # or false

Effect: sets ``accounts.smart_router_v2_enabled`` for the target
account. Returns the new value plus a timestamp. Writes an audit-log
row tagged ``smart_router_v2_flag_changed``.

Auth
----

Reuses the existing ``require_admin`` dependency from
``api.admin_status`` — cookie session + email in the
``ADMIN_EMAILS`` env list. Empty ``ADMIN_EMAILS`` means the
endpoint refuses everyone (closed by default), same as
``/v1/account/admin/status``.

Why platform-admin and not the per-account dashboard
----------------------------------------------------

Per CEO 2026-05-13 the rollout is "flip per-account from admin",
not "let every user toggle from their own dashboard". The flag is
a soft beta gate, not a user-controllable preference. When v2 is
stable we'll lift this into the user-facing dashboard, but for S1
admin-only keeps the blast radius tight.

Audit
-----

Every flip writes an ``audit_log`` row with the old + new value and
the admin user's id. ``write_audit`` records IP + user-agent so we
can trace who enabled/disabled it.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from voltari_gateway.api.admin_status import require_admin
from voltari_gateway.auth.audit import write_audit
from voltari_gateway.auth.session_middleware import SessionPrincipal
from voltari_gateway.db.models import Account
from voltari_gateway.db.session import get_db
from voltari_gateway.utils.errors import GatewayError
from voltari_gateway.utils.logging import get_logger

router = APIRouter(prefix="/v1/account/admin", tags=["admin"])
log = get_logger(__name__)


class SmartRouterV2FlipBody(BaseModel):
    """Request body — single boolean."""

    enabled: bool = Field(
        ...,
        description="True to opt this account into Smart Router v2; False to revert.",
    )


class SmartRouterV2FlipResponse(BaseModel):
    """Response payload — confirms the new state."""

    account_id: uuid.UUID
    smart_router_v2_enabled: bool
    changed_at: datetime
    changed: bool = Field(
        description="False if the requested value matched the existing value (no-op)."
    )


@router.post(
    "/smart_router_v2/{account_id}",
    response_model=SmartRouterV2FlipResponse,
    summary="Toggle the Smart Router v2 opt-in for a specific account.",
    description=(
        "Admin-only endpoint that flips ``accounts.smart_router_v2_enabled`` "
        "for a single account. Used during the gradual rollout of Smart "
        "Router v2 to pilot accounts. Writes an audit-log row "
        "``smart_router_v2_flag_changed`` on every flip."
    ),
    responses={
        200: {"description": "Flag updated (or already in target state — see ``changed``)."},
        401: {"description": "No active session cookie."},
        403: {"description": "Caller's email is not in ``ADMIN_EMAILS``."},
        404: {"description": "Account id does not exist."},
    },
)
async def flip_smart_router_v2(
    body: SmartRouterV2FlipBody,
    request: Request,
    account_id: Annotated[uuid.UUID, Path(description="Target account UUID.")],
    principal: Annotated[SessionPrincipal, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> SmartRouterV2FlipResponse:
    """Set ``account.smart_router_v2_enabled`` and audit the change."""
    account = (
        await db.execute(select(Account).where(Account.id == account_id))
    ).scalar_one_or_none()
    if account is None:
        raise GatewayError(
            status_code=404,
            message=f"Account {account_id} not found.",
            type="invalid_request_error",
            code="account_not_found",
        )

    previous = account.smart_router_v2_enabled
    new_value = body.enabled
    changed = previous != new_value
    now = datetime.now(UTC)

    if changed:
        account.smart_router_v2_enabled = new_value
        await write_audit(
            db,
            user_id=principal.user.id,
            account_id=account.id,
            action="smart_router_v2_flag_changed",
            request=request,
            meta={
                "before": previous,
                "after": new_value,
                "admin_user_id": str(principal.user.id),
                "admin_email": principal.user.email,
            },
        )
        await db.commit()
        log.info(
            "smart_router_v2_flag_changed",
            account_id=str(account_id),
            before=previous,
            after=new_value,
            admin_user_id=str(principal.user.id),
        )
    else:
        log.info(
            "smart_router_v2_flag_noop",
            account_id=str(account_id),
            value=new_value,
            admin_user_id=str(principal.user.id),
        )

    return SmartRouterV2FlipResponse(
        account_id=account.id,
        smart_router_v2_enabled=account.smart_router_v2_enabled,
        changed_at=now,
        changed=changed,
    )


__all__ = ["router"]
