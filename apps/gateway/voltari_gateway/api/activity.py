"""Sprint 8 F6 — unified account-activity feed.

Mounts ``GET /v1/account/activity`` — merges entries from two sources:

* **audit_log** — login, 2FA toggle, key create/revoke/rotate, tariff
  change, session revoke, account closure, routing-preferences edit,
  etc. (the "what the user / system did to the account" log).
* **transactions** — top-up, charge, refund, autorefill (the "money
  moved" log).

The frontend renders a single timeline, grouping by day. We keep the
two SQL queries separate (rather than UNION'ing in SQL) because:

* The two tables don't share a row shape (different columns, different
  PK types). UNION would force a contortion of NULLs and casts.
* Each table has its own index hot-path; querying separately keeps both
  fast. We over-fetch by a small margin and merge in Python — at
  ``limit ≤ 100`` the cost is trivial (~200 rows shuffled).

The merge is stable on ``timestamp DESC, source`` so two entries at the
same instant don't flip order between calls.

Trade-off vs. a real CQRS-style activity_events table:

* This implementation is read-only and additive — no schema migration.
* Worst case (limit=20, account with 500k tx + 200k audit rows over 2y):
  the per-source LIMIT 20 pull is index-served (DESC on
  ``transactions.created_at`` + ``audit_log.created_at``) so the query
  cost is bounded by the LIMIT not the table size.
* If we ever need «filter by event type» across sources, that's the
  point a dedicated activity_events table starts paying for itself.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from voltari_gateway.auth.session_middleware import (
    SessionPrincipal,
    require_session,
)
from voltari_gateway.db.models import AuditLog, Transaction, TransactionKind
from voltari_gateway.db.session import get_db

router = APIRouter(prefix="/v1/account", tags=["account"])


# ---------------------------------------------------------------------------
# Source mapping — audit action → user-facing event type.
#
# audit_log.action is a free-form string written from many call sites. We
# group them into a small UI-friendly bucket set so the dashboard can
# render an icon / colour without knowing every backend action name.
# ---------------------------------------------------------------------------


_AUDIT_TO_EVENT_TYPE: dict[str, str] = {
    # Auth
    "login_ok": "auth_login",
    "login_password_ok_2fa_required": "auth_login_2fa_pending",
    "login_2fa_ok": "auth_login",
    "login_2fa_failed": "auth_login_failed",
    "password_changed": "auth_password_changed",
    "password_change_failed": "auth_password_change_failed",
    "email_verify_resent": "auth_email_verify_resent",
    # 2FA
    "2fa_setup_started": "twofa_setup_started",
    "2fa_setup_verify_failed": "twofa_setup_failed",
    "2fa_enabled": "twofa_enabled",
    "2fa_disabled": "twofa_disabled",
    "2fa_disable_failed": "twofa_disable_failed",
    "recovery_regenerated": "twofa_recovery_regenerated",
    "recovery_regenerate_failed": "twofa_recovery_failed",
    # Sessions
    "session_revoked": "session_revoked",
    "session_revoke_failed": "session_revoke_failed",
    "sessions_revoked_all": "sessions_revoked_all",
    # Tariff
    "tariff_changed": "tariff_changed",
    "tariff_change_denied": "tariff_change_denied",
    # Routing prefs
    "routing_preferences_updated": "routing_preferences_updated",
    # Account closure
    "account_closure_requested": "account_closure_requested",
    "account_closure_cancelled": "account_closure_cancelled",
    "account_closed": "account_closed",
    "account_pii_purged": "account_pii_purged",
    # Data export
    "data_export_requested": "data_export_requested",
    # Keys (Sprint 8)
    "api_keys_bulk_revoked": "keys_bulk_revoked",
    "api_keys_bulk_rotated": "keys_bulk_rotated",
}


_TXN_KIND_TO_EVENT_TYPE: dict[str, str] = {
    TransactionKind.TOPUP.value: "balance_topup",
    TransactionKind.CHARGE.value: "balance_charge",
    TransactionKind.REFUND.value: "balance_refund",
    TransactionKind.SUBSCRIPTION.value: "subscription_charged",
    TransactionKind.AUTOREFILL.value: "balance_autorefill",
}


_TXN_KIND_SUMMARY: dict[str, str] = {
    TransactionKind.TOPUP.value: "Пополнение баланса",
    TransactionKind.CHARGE.value: "Списание за услуги",
    TransactionKind.REFUND.value: "Возврат на баланс",
    TransactionKind.SUBSCRIPTION.value: "Списание абонентской платы",
    TransactionKind.AUTOREFILL.value: "Автопополнение баланса",
}


def _audit_summary(action: str, meta: dict[str, Any] | None) -> str:
    """Render a short Russian-language summary for the timeline row.

    We don't expose every field of the meta blob — it can carry IDs and
    structured data that's noise for a UI feed. Whitelist a couple of
    common patterns; everything else gets the action name humanised.
    """
    meta = meta or {}
    if action == "tariff_changed":
        before = meta.get("before") or "?"
        after = meta.get("after") or "?"
        return f"Тариф изменён: {before} → {after}"
    if action == "api_keys_bulk_revoked":
        return f"Отозвано API-ключей: {meta.get('count', 0)}"
    if action == "api_keys_bulk_rotated":
        return f"Ротация API-ключей: {meta.get('count', 0)}"
    if action == "sessions_revoked_all":
        return "Завершены все активные сессии"
    if action == "account_closure_requested":
        return "Запрос на удаление аккаунта"
    if action == "data_export_requested":
        return "Запрошен экспорт данных"
    if action.startswith("login"):
        return "Вход в аккаунт" if action == "login_ok" else action.replace("_", " ").capitalize()
    return action.replace("_", " ").capitalize()


# ---------------------------------------------------------------------------
# Response schema
# ---------------------------------------------------------------------------


EventSource = Literal["audit", "transaction"]


class ActivityEvent(BaseModel):
    """One timeline row.

    ``id`` is namespaced by source (``audit:<uuid>`` / ``txn:<uuid>``) so
    the SPA can dedupe across pages; the underlying tables have their
    own UUID PKs which would collide if returned bare.
    """

    id: str
    source: EventSource
    type: str
    timestamp: datetime
    summary: str
    details: dict[str, Any] | None = None


class ActivityResponse(BaseModel):
    items: list[ActivityEvent]
    limit: int


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------


@router.get(
    "/activity",
    response_model=ActivityResponse,
    summary="Recent account activity (merged audit + transactions)",
    description=(
        "Returns the last N events for the active account, merging audit_log "
        "(security / config events) with the transactions ledger (money "
        "events). Sorted newest-first. Use this for the dashboard timeline; "
        "for full transaction history see ``GET /v1/billing/transactions`` "
        "and for the security-only audit feed query the dashboard "
        "Sessions/Activity views."
    ),
)
async def get_activity(
    principal: Annotated[SessionPrincipal, Depends(require_session)],
    db: Annotated[AsyncSession, Depends(get_db)],
    limit: int = Query(default=20, ge=1, le=100),
) -> ActivityResponse:
    """Pull the latest ``limit`` rows from each source and merge.

    Cross-account isolation is enforced through both queries' WHERE
    account_id = principal.account.id. The merge is in-memory.

    NOTE on audit_log scope: rows have either user_id, account_id, or
    both. Login events are user-scoped (account_id NULL) — we include
    them by ALSO filtering on user_id, so the user sees their own
    logins even if the row didn't carry an account_id. The DB-level
    audit-log retention purge keeps this query fast (≤90 days hot).
    """
    user_id: uuid.UUID = principal.user.id
    account_id: uuid.UUID = principal.account.id

    # --- audit side -------------------------------------------------------
    from sqlalchemy import or_

    audit_stmt = (
        select(AuditLog)
        .where(or_(AuditLog.account_id == account_id, AuditLog.user_id == user_id))
        .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
        .limit(limit)
    )
    audit_rows = (await db.execute(audit_stmt)).scalars().all()

    # --- transactions side ------------------------------------------------
    txn_stmt = (
        select(Transaction)
        .where(Transaction.account_id == account_id)
        .order_by(Transaction.created_at.desc(), Transaction.id.desc())
        .limit(limit)
    )
    txn_rows = (await db.execute(txn_stmt)).scalars().all()

    # --- merge & cap ------------------------------------------------------
    merged: list[ActivityEvent] = []
    for audit_row in audit_rows:
        merged.append(
            ActivityEvent(
                id=f"audit:{audit_row.id}",
                source="audit",
                type=_AUDIT_TO_EVENT_TYPE.get(audit_row.action, audit_row.action),
                timestamp=audit_row.created_at,
                summary=_audit_summary(audit_row.action, audit_row.meta),
                details={
                    "action": audit_row.action,
                    "outcome": audit_row.outcome,
                    "meta": audit_row.meta,
                },
            )
        )
    for txn_row in txn_rows:
        kind = txn_row.type.value
        meta = txn_row.meta or {}
        amount_kop = abs(txn_row.amount_kopecks)
        summary = _TXN_KIND_SUMMARY.get(kind, kind) + f" — {amount_kop // 100} ₽"
        merged.append(
            ActivityEvent(
                id=f"txn:{txn_row.id}",
                source="transaction",
                type=_TXN_KIND_TO_EVENT_TYPE.get(kind, kind),
                timestamp=txn_row.created_at,
                summary=summary,
                details={
                    "amount_kopecks": txn_row.amount_kopecks,
                    "ref_id": txn_row.ref_id,
                    "model": meta.get("model"),
                    "request_id": meta.get("request_id"),
                },
            )
        )

    # Stable sort: timestamp DESC, then source name (audit < transaction)
    # for deterministic ordering when the two sources hit the same instant.
    merged.sort(key=lambda e: (e.timestamp, e.source), reverse=True)
    return ActivityResponse(items=merged[:limit], limit=limit)


__all__ = ["router"]
