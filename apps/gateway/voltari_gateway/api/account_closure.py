"""Management API — account closure flow (Sprint 7, 152-ФЗ ст. 21).

Mounted under ``/v1/account``. Endpoints:

    POST   /v1/account/close             — request closure (30d grace)
    POST   /v1/account/close/cancel      — cancel in-grace closure
    GET    /v1/account/closure-status    — public status for the SPA banner

All three require an authenticated cookie session via ``require_session``.
The grace window itself is **soft delete with reminder**: API keys keep
working, balance still spends, the dashboard renders. Only after the
``closure_scheduled_at`` boundary does the cron in
``billing/account_closure_cron.py`` flip the account to ``CLOSED`` and
revoke credentials.

The middleware change (``auth/middleware.py``) only refuses requests
when ``account.closed_at IS NOT NULL`` — that is, the cron has fully
terminated the account.

Per CEO 28.04 (PROJECT_LOG): account closure is irreversible after
``closed_at``. The audit log keeps a tombstone row even after the PII
purge cron anonymises ``users.email`` and friends.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated, Final

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from voltari_gateway.auth.audit import write_audit
from voltari_gateway.auth.session_middleware import (
    SessionPrincipal,
    require_session,
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

# Length of the soft-delete grace window. CEO 28.04: 30 days matches what
# the closure email template + dashboard banner already promise the user.
CLOSURE_GRACE_DAYS: Final[int] = 30

router = APIRouter(prefix="/v1/account", tags=["account"])


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------


class CloseAccountRequest(BaseModel):
    """Body for POST /v1/account/close.

    ``reason`` is optional free-text (≤512 chars to fit
    ``Account.closure_reason``); we keep it for product analytics — why
    are users leaving — and surface it in the audit log.
    """

    reason: str | None = Field(default=None, max_length=512)


class ClosureStatusResponse(BaseModel):
    """Body of GET /v1/account/closure-status."""

    requested: bool
    scheduled_at: datetime | None = None
    days_remaining: int | None = None
    can_cancel: bool


class ClosureActionResponse(BaseModel):
    """Body returned by POST /close and POST /close/cancel."""

    requested: bool
    scheduled_at: datetime | None = None
    days_remaining: int | None = None
    can_cancel: bool
    message: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _normalise_aware(dt: datetime | None) -> datetime | None:
    """SQLite returns naive datetimes; Postgres tz-aware. Normalise."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt


def _days_remaining(scheduled_at: datetime | None) -> int | None:
    """Whole days from now to ``scheduled_at``, rounded up (UX-friendly).

    Returns None if there's no scheduled closure. Rounding up matches
    how the dashboard banner renders «осталось 30 дней» — we never want
    the user to see «29» a microsecond after pressing «Close».
    """
    import math

    sched = _normalise_aware(scheduled_at)
    if sched is None:
        return None
    delta = sched - _utcnow()
    days = max(0, math.ceil(delta.total_seconds() / 86_400))
    return days


def _build_status(
    *,
    closure_requested_at: datetime | None,
    closure_scheduled_at: datetime | None,
    closed_at: datetime | None,
) -> ClosureStatusResponse:
    requested = closure_requested_at is not None and closed_at is None
    sched = _normalise_aware(closure_scheduled_at) if requested else None
    days = _days_remaining(sched) if requested else None
    can_cancel = bool(
        requested and sched is not None and sched > _utcnow() and closed_at is None,
    )
    return ClosureStatusResponse(
        requested=requested,
        scheduled_at=sched,
        days_remaining=days,
        can_cancel=can_cancel,
    )


# ---------------------------------------------------------------------------
# 1) POST /v1/account/close
# ---------------------------------------------------------------------------


@router.post(
    "/close",
    response_model=ClosureActionResponse,
    summary="Request account closure (30-day grace period)",
    description=(
        "Schedules the account for closure ``CLOSURE_GRACE_DAYS`` from "
        "now. During grace the account works normally (API keys valid, "
        "balance spends). The cron in ``account_closure_cron`` flips "
        "``status=CLOSED`` and revokes credentials at "
        "``closure_scheduled_at``. PII purge runs 1 year after that."
    ),
    responses={
        200: {"description": "Closure scheduled."},
        400: {"description": "Closure already requested."},
    },
)
async def close_account(
    payload: CloseAccountRequest,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    principal: Annotated[SessionPrincipal, Depends(require_session)],
) -> ClosureActionResponse:
    account = principal.account
    user = principal.user

    # Idempotency: a re-submission while still in grace is a no-op (we
    # don't extend the grace window — that would let an attacker stuck
    # on the password reset never trigger the cron). Surface 400 so the
    # SPA can show "you've already requested closure".
    if account.closed_at is not None:
        # The middleware should have refused the session already, but be
        # defensive — a stale cookie could land here.
        raise GatewayError(
            status_code=403,
            message="Account is closed.",
            type="invalid_request_error",
            code="account_closed",
        )
    if account.closure_requested_at is not None and account.closure_scheduled_at is not None:
        sched = _normalise_aware(account.closure_scheduled_at)
        if sched is not None and sched > _utcnow():
            raise invalid_request(
                "Closure has already been requested for this account.",
                code="closure_already_requested",
            )

    now = _utcnow()
    scheduled = now + timedelta(days=CLOSURE_GRACE_DAYS)
    account.closure_requested_at = now
    account.closure_scheduled_at = scheduled
    account.closure_reason = (payload.reason or "").strip() or None

    await write_audit(
        db,
        user_id=user.id,
        account_id=account.id,
        action="account_closure_requested",
        request=request,
        meta={
            "scheduled_at": scheduled.isoformat(),
            "reason_provided": bool(payload.reason),
        },
    )
    await db.commit()
    await db.refresh(account)

    # Best-effort confirmation email — failure must not roll back the
    # closure. The audit log + DB row are the source of truth.
    try:
        body = render_template(
            "account_closure_confirmed.txt",
            requested_at=now.isoformat(),
            closure_at=scheduled.isoformat(),
            settings_security_url=build_frontend_link("/settings/security"),
        )
        await send_email(
            to=user.email,
            subject="Запрос на закрытие аккаунта Brikko получен",
            body=body,
        )
    except Exception as exc:
        log.warning("closure_email_failed", error=str(exc))

    return ClosureActionResponse(
        requested=True,
        scheduled_at=scheduled,
        days_remaining=CLOSURE_GRACE_DAYS,
        can_cancel=True,
        message="Account closure scheduled. You can cancel any time before the scheduled date.",
    )


# ---------------------------------------------------------------------------
# 2) POST /v1/account/close/cancel
# ---------------------------------------------------------------------------


@router.post(
    "/close/cancel",
    response_model=ClosureActionResponse,
    summary="Cancel a pending account closure",
    description=(
        "Clears ``closure_requested_at`` / ``closure_scheduled_at``. "
        "Only valid while the closure is still in grace and the cron "
        "hasn't fired yet. Returns 400 if no closure is in flight."
    ),
    responses={
        200: {"description": "Closure cancelled."},
        400: {"description": "No closure in flight, or grace already expired."},
    },
)
async def cancel_account_closure(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    principal: Annotated[SessionPrincipal, Depends(require_session)],
) -> ClosureActionResponse:
    account = principal.account
    user = principal.user

    if account.closed_at is not None:
        # Cron already ran; cancellation is irreversible.
        raise GatewayError(
            status_code=403,
            message="Account is closed.",
            type="invalid_request_error",
            code="account_closed",
        )
    if account.closure_requested_at is None or account.closure_scheduled_at is None:
        raise invalid_request(
            "No closure request is pending for this account.",
            code="no_closure_pending",
        )
    sched = _normalise_aware(account.closure_scheduled_at)
    if sched is not None and sched <= _utcnow():
        # The cron should pick this up imminently — refuse cancel.
        raise invalid_request(
            "Grace period has elapsed; closure can no longer be cancelled.",
            code="closure_grace_elapsed",
        )

    account.closure_requested_at = None
    account.closure_scheduled_at = None
    account.closure_reason = None

    await write_audit(
        db,
        user_id=user.id,
        account_id=account.id,
        action="account_closure_cancelled",
        request=request,
    )
    await db.commit()
    await db.refresh(account)

    return ClosureActionResponse(
        requested=False,
        scheduled_at=None,
        days_remaining=None,
        can_cancel=False,
        message="Account closure cancelled.",
    )


# ---------------------------------------------------------------------------
# 3) GET /v1/account/closure-status
# ---------------------------------------------------------------------------


@router.get(
    "/closure-status",
    response_model=ClosureStatusResponse,
    summary="Return current closure status for the SPA banner",
)
async def closure_status(
    principal: Annotated[SessionPrincipal, Depends(require_session)],
) -> ClosureStatusResponse:
    account = principal.account
    return _build_status(
        closure_requested_at=account.closure_requested_at,
        closure_scheduled_at=account.closure_scheduled_at,
        closed_at=account.closed_at,
    )


__all__ = ["CLOSURE_GRACE_DAYS", "router"]
