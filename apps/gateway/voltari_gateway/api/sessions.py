"""Management API — sessions list / revoke (Sprint 6).

Mounted under ``/v1/auth/sessions``. All endpoints session-cookie protected.

    GET    /v1/auth/sessions             — list active sessions for user
    DELETE /v1/auth/sessions/{id}        — revoke one (other) session
    DELETE /v1/auth/sessions             — revoke ALL other sessions (keep current)

Postgres ``sessions`` is the source for the list view; Redis remains the
authoritative whitelist used for authorisation. Revoke flow drops both
in lockstep.

IDOR defence: every endpoint filters by ``user_id`` of the caller. A
revoke against a foreign session_id returns 404 (not 403) so existence
of someone else's session is never disclosed. Audit log captures the
attempt.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, Path, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from voltari_gateway.auth.audit import write_audit
from voltari_gateway.auth.middleware import get_redis
from voltari_gateway.auth.session import revoke_refresh_token, verify_refresh_token
from voltari_gateway.auth.session_middleware import (
    SessionPrincipal,
    require_session,
)
from voltari_gateway.auth.sessions_store import (
    revoke_all_other_sessions,
    revoke_session_by_id,
)
from voltari_gateway.db.models import Session as SessionRow
from voltari_gateway.db.session import get_db
from voltari_gateway.utils.errors import GatewayError
from voltari_gateway.utils.logging import get_logger

log = get_logger(__name__)

router = APIRouter(prefix="/v1/auth/sessions", tags=["sessions"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class SessionItem(BaseModel):
    id: uuid.UUID
    device_label: str | None
    ip_address: str | None
    user_agent: str | None
    created_at: datetime
    last_seen_at: datetime
    expires_at: datetime
    is_current: bool
    revoked: bool


class SessionsListResponse(BaseModel):
    items: list[SessionItem]


class RevokeAllResponse(BaseModel):
    revoked: int


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mask_ip(ip: str | None) -> str | None:
    """Mask the last octet of an IPv4 / last segment of IPv6 for display.

    Audit row keeps the full IP — this is purely cosmetic for the dashboard.
    """
    if not ip:
        return None
    if ":" in ip:  # IPv6
        parts = ip.split(":")
        if len(parts) > 2:
            return ":".join(parts[:-2]) + ":*:*"
        return ip
    parts = ip.split(".")
    if len(parts) == 4:
        return f"{parts[0]}.{parts[1]}.*.*"
    return ip


def _current_jti_from_cookie(vlt_refresh: str | None) -> str | None:
    if not vlt_refresh:
        return None
    claims = verify_refresh_token(vlt_refresh)
    return claims.jti if claims is not None else None


# ---------------------------------------------------------------------------
# 1) GET /v1/auth/sessions
# ---------------------------------------------------------------------------


@router.get("", response_model=SessionsListResponse)
async def list_sessions(
    principal: Annotated[SessionPrincipal, Depends(require_session)],
    db: Annotated[AsyncSession, Depends(get_db)],
    vlt_refresh: str | None = Cookie(default=None),
) -> SessionsListResponse:
    """Return active + recently-revoked sessions for the calling user.

    Recently-revoked rows are kept for 7 days post-revoke as a forensic
    surface — UI greys them out. Older revoked rows are filtered out at
    the query level. Active rows are sorted ``last_seen_at desc``.
    """
    now = datetime.now(UTC)
    seven_days_ago = datetime.fromtimestamp(now.timestamp() - 7 * 86400, tz=UTC)
    current_jti = _current_jti_from_cookie(vlt_refresh)

    stmt = (
        select(SessionRow)
        .where(SessionRow.user_id == principal.user.id)
        .where(SessionRow.expires_at >= now)
        # Either active or revoked-recently.
        .where((SessionRow.revoked_at.is_(None)) | (SessionRow.revoked_at >= seven_days_ago))
        .order_by(SessionRow.last_seen_at.desc())
    )
    rows = (await db.execute(stmt)).scalars().all()

    items: list[SessionItem] = []
    for r in rows:
        items.append(
            SessionItem(
                id=r.id,
                device_label=r.device_label,
                ip_address=_mask_ip(r.ip_address),
                user_agent=r.user_agent,
                created_at=r.created_at,
                last_seen_at=r.last_seen_at,
                expires_at=r.expires_at,
                is_current=(current_jti is not None and r.refresh_jti == current_jti),
                revoked=r.revoked_at is not None,
            )
        )
    return SessionsListResponse(items=items)


# ---------------------------------------------------------------------------
# 2) DELETE /v1/auth/sessions/{session_id}
# ---------------------------------------------------------------------------


@router.delete("/{session_id}", status_code=200)
async def revoke_session(
    session_id: Annotated[uuid.UUID, Path(...)],
    request: Request,
    principal: Annotated[SessionPrincipal, Depends(require_session)],
    db: Annotated[AsyncSession, Depends(get_db)],
    vlt_refresh: str | None = Cookie(default=None),
) -> dict[str, object]:
    """Revoke a single session by id.

    Trying to revoke the **current** session: returns 400 with code
    ``cannot_revoke_current``. Use POST /v1/auth/logout instead.
    Trying to revoke another user's session: 404 with audit row.
    """
    redis = get_redis()
    if redis is None:
        raise GatewayError(
            status_code=503,
            message="Session store unavailable.",
            type="api_error",
            code="redis_unavailable",
        )

    current_jti = _current_jti_from_cookie(vlt_refresh)

    # Pre-check: load the row read-only so we can refuse current-session
    # revoke without first mutating ``revoked_at``.
    pre_stmt = select(SessionRow).where(
        SessionRow.id == session_id,
        SessionRow.user_id == principal.user.id,
    )
    pre = (await db.execute(pre_stmt)).scalar_one_or_none()
    if pre is None:
        await write_audit(
            db,
            user_id=principal.user.id,
            account_id=principal.account.id,
            action="session_revoke_failed",
            outcome="denied",
            request=request,
            meta={"reason": "not_found_or_foreign", "session_id": str(session_id)},
        )
        await db.commit()
        raise GatewayError(
            status_code=404,
            message="Session not found.",
            type="invalid_request_error",
            code="not_found",
        )
    if current_jti is not None and pre.refresh_jti == current_jti:
        raise GatewayError(
            status_code=400,
            message="Cannot revoke the current session. Use /logout instead.",
            type="invalid_request_error",
            code="cannot_revoke_current",
        )

    row = await revoke_session_by_id(db, user_id=principal.user.id, session_id=session_id)
    if row is None:  # pragma: no cover — pre-checked above
        raise GatewayError(
            status_code=404,
            message="Session not found.",
            type="invalid_request_error",
            code="not_found",
        )

    # Drop the Redis whitelist entry so the next request from that device
    # gets a 401 within ~5s (the access-token TTL is 15 min — but the
    # browser's refresh request will fail immediately).
    try:
        await revoke_refresh_token(redis, principal.user.id, row.refresh_jti)
    except Exception as exc:
        log.warning("session_revoke_redis_failed", error=str(exc))

    await write_audit(
        db,
        user_id=principal.user.id,
        account_id=principal.account.id,
        action="session_revoked",
        request=request,
        meta={"session_id": str(session_id), "refresh_jti": row.refresh_jti},
    )
    await db.commit()
    return {"ok": True, "session_id": str(session_id)}


# ---------------------------------------------------------------------------
# 3) DELETE /v1/auth/sessions — revoke all OTHER sessions
# ---------------------------------------------------------------------------


@router.delete("", response_model=RevokeAllResponse, status_code=200)
async def revoke_all_other(
    request: Request,
    principal: Annotated[SessionPrincipal, Depends(require_session)],
    db: Annotated[AsyncSession, Depends(get_db)],
    vlt_refresh: str | None = Cookie(default=None),
) -> RevokeAllResponse:
    """Revoke every active session except the current one."""
    redis = get_redis()
    if redis is None:
        raise GatewayError(
            status_code=503,
            message="Session store unavailable.",
            type="api_error",
            code="redis_unavailable",
        )

    current_jti = _current_jti_from_cookie(vlt_refresh)

    revoked_jtis = await revoke_all_other_sessions(
        db, user_id=principal.user.id, keep_jti=current_jti
    )
    for jti in revoked_jtis:
        try:
            await revoke_refresh_token(redis, principal.user.id, jti)
        except Exception as exc:
            log.warning("session_revoke_all_redis_failed", jti=jti, error=str(exc))

    await write_audit(
        db,
        user_id=principal.user.id,
        account_id=principal.account.id,
        action="sessions_revoked_all",
        request=request,
        meta={"revoked_count": len(revoked_jtis)},
    )
    await db.commit()
    return RevokeAllResponse(revoked=len(revoked_jtis))


__all__ = ["router"]
