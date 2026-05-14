"""Single helper for writing security events to ``audit_log``.

Why a helper module
-------------------

* All call sites use the same shape: ``await write_audit(db, user, action, ...)``.
* Sentry breadcrumbs / structlog are added in one place.
* Test-only ``audit_event_capture`` fixture can monkeypatch this module
  to assert events without scraping the DB.

Performance
-----------

Audit writes are inline (same transaction as the user-facing event) for
correctness — a 2FA login flow that succeeds in DB but fails to log
would be invisible. We accept the extra INSERT cost (~0.3 ms on PG).
The table is append-only and indexed on (user_id, created_at) so reads
stay cheap.

Sentry integration
------------------

Failed events (``outcome="failed"``) and high-severity actions
(``account_closed``, ``2fa_disabled``) emit a Sentry breadcrumb. We do
NOT raise to Sentry on every event — that would burn the free-tier
budget in a day. The ``severity`` argument lets the caller force a
``capture_message`` for a single event without changing the action
contract.
"""

from __future__ import annotations

import uuid
from typing import Any, Final, Literal

import sentry_sdk
from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from voltari_gateway.db.models import AuditLog
from voltari_gateway.utils.logging import get_logger

log = get_logger(__name__)

Outcome = Literal["ok", "denied", "failed"]
Severity = Literal["info", "warning", "error"]


# Actions that imply something serious happened — bump to a Sentry message.
_HIGH_SIGNAL_ACTIONS: Final[frozenset[str]] = frozenset(
    {
        "2fa_disabled",
        "account_closed",
        "data_export_unauthorized",
        "tariff_change_denied",
        "api_key_used_on_purged_account",
    }
)


def _client_ip(request: Request | None) -> str | None:
    if request is None:
        return None
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    if request.client is not None:
        return request.client.host
    return None


def _user_agent(request: Request | None) -> str | None:
    if request is None:
        return None
    ua = request.headers.get("user-agent")
    if ua is None:
        return None
    # Keep DB column happy. Real-world UAs are <300 chars.
    return ua[:512]


async def write_audit(
    db: AsyncSession,
    *,
    user_id: uuid.UUID | None,
    account_id: uuid.UUID | None,
    action: str,
    outcome: Outcome = "ok",
    request: Request | None = None,
    meta: dict[str, Any] | None = None,
    severity: Severity = "info",
) -> AuditLog:
    """Append one ``audit_log`` row.

    Caller MUST ``await db.commit()`` afterwards (we do not commit here so
    the audit row is part of the same transaction as the underlying
    business change — a roll-back wipes both).

    The row is added to the session and flushed (so its ``id`` is
    available for the response or for breadcrumbs). On flush errors we
    log + swallow — audit must never abort a successful business action.
    """
    row = AuditLog(
        user_id=user_id,
        account_id=account_id,
        action=action,
        outcome=outcome,
        ip_address=_client_ip(request),
        user_agent=_user_agent(request),
        meta=meta,
    )
    db.add(row)

    # structlog — always. Log structure is what ops dashboards consume.
    # NB: structlog's bound logger reserves the ``event`` kwarg for the
    # log message; we pass it positionally and use ``audit_action``
    # for the structured field.
    log_payload = {
        "audit_action": action,
        "outcome": outcome,
        "user_id": str(user_id) if user_id else None,
        "account_id": str(account_id) if account_id else None,
        "ip": row.ip_address,
        "meta": meta,
    }
    if outcome == "failed" or action in _HIGH_SIGNAL_ACTIONS:
        log.warning("audit_event", **log_payload)
    else:
        log.info("audit_event", **log_payload)

    # Sentry — breadcrumb on every event (cheap), capture_message on
    # high-signal failures so they get bumped to the issues view.
    try:
        sentry_sdk.add_breadcrumb(
            category="audit",
            message=action,
            level=severity,
            data={
                "outcome": outcome,
                "user_id": str(user_id) if user_id else None,
                "meta": meta,
            },
        )
        if (severity == "error") or (action in _HIGH_SIGNAL_ACTIONS and outcome != "ok"):
            sentry_sdk.capture_message(
                f"audit:{action}:{outcome}",
                level="warning" if outcome == "denied" else "error",
            )
    except Exception as exc:  # pragma: no cover — Sentry-down must not break
        log.debug("audit_sentry_failed", error=str(exc))

    try:
        await db.flush()
    except Exception as exc:  # pragma: no cover — defensive
        log.error("audit_flush_failed", action=action, error=str(exc))
    return row


__all__ = ["Outcome", "Severity", "write_audit"]
