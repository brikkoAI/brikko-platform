"""Per-request trace logger for BrikkoLens.

Phase 5 #4 Sprint 1б (2026-05-09). Insert one row per gateway request
into ``gateway_request_log`` after the upstream provider responded.
Frontend ``/app/traces`` reads from this table to give clients a
visible activity log + cost/latency breakdown.

Design contract
---------------

* **Fire-and-forget.** A failure to write the trace MUST NOT propagate
  to the user — we always release the hold + return the response, then
  best-effort log the trace. If the insert raises, we log a warning
  and move on.
* **No double-billing.** This module never debits the account; the
  billing path (UsageEvent + commit_hold_to_debit) stays the source of
  truth for cost. We just denormalise the same numbers into a
  observability-shaped row.
* **No bodies by default.** ``request_body`` and ``response_body`` are
  populated only when ``account.store_prompts=True`` (existing toggle).
  Even with logging on, we cap to the first ~32KB to bound storage.

Why not async/queue
-------------------

We considered Redis Streams + worker but rejected for MVP:
* +1 moving piece (queue can stop, queue can drift).
* INSERT under our load (<100 RPS until M6) is sub-millisecond on
  Postgres with an existing connection.
* Failure mode of "queue lost some events" is harder to reason about
  than "DB insert failed for this one request, log+continue".

If we hit RPS where post-flight DB insert becomes the bottleneck,
we'll move to a Redis Streams + consumer at that point. Until then
synchronous write inside the same transaction as UsageEvent is fine.
"""

from __future__ import annotations

import contextlib
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from voltari_gateway.db.models import Account, GatewayRequestLog
from voltari_gateway.utils.logging import get_logger

log = get_logger(__name__)

# Hard cap on stored body size in bytes. 32 KiB covers typical prompts
# + responses without bloating the table on accidentally-stored binary
# blobs. Trim happens at the JSON-string level, so the dict itself may
# slightly exceed when serialised — but Postgres jsonb has no inline
# limit so this is just self-defence.
BODY_SIZE_CAP_BYTES = 32 * 1024


def _truncate_jsonb(value: dict[str, Any] | None) -> dict[str, Any] | None:
    """Best-effort cap on jsonb columns.

    If the dict serialises larger than BODY_SIZE_CAP_BYTES, replace it
    with a marker dict so we keep the row intact and don't blow up the
    table on a 1MB prompt.
    """
    if value is None:
        return None
    try:
        import json

        size = len(json.dumps(value, ensure_ascii=False).encode("utf-8"))
    except Exception:
        return {"_truncated": True, "reason": "serialisation_failed"}
    if size > BODY_SIZE_CAP_BYTES:
        return {
            "_truncated": True,
            "size_bytes": size,
            "reason": "body_size_cap",
        }
    return value


async def record_request_log(
    db: AsyncSession,
    *,
    account_id: uuid.UUID,
    api_key_id: uuid.UUID | None,
    request_id: str,
    provider: str,
    model: str,
    routed_from: str | None,
    started_at: datetime,
    finished_at: datetime,
    latency_ms: int,
    ttft_ms: int | None,
    prompt_tokens: int,
    completion_tokens: int,
    cached_tokens: int,
    reasoning_tokens: int,
    cost_kop: int,
    fx_usd_rub: float | None,
    status: str,
    http_code: int | None,
    error_code: str | None,
    error_message: str | None,
    is_streaming: bool,
    cache_hit: bool,
    pii_masked: bool,
    tools_used: bool,
    request_body: dict[str, Any] | None = None,
    response_body: dict[str, Any] | None = None,
    model_params: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
    store_bodies: bool = False,
) -> None:
    """Insert one row into gateway_request_log. Never raises.

    Caller MUST NOT have unflushed changes that depend on the trace
    insert succeeding — if it raises, we swallow the error and log.
    Recommended: call AFTER ``db.commit()`` of the billing path.
    """
    try:
        # Bodies — only when account opted in.
        rb = _truncate_jsonb(request_body) if store_bodies else None
        sb = _truncate_jsonb(response_body) if store_bodies else None

        row = GatewayRequestLog(
            account_id=account_id,
            api_key_id=api_key_id,
            request_id=request_id,
            provider=provider,
            model=model,
            routed_from=routed_from,
            started_at=started_at,
            finished_at=finished_at,
            latency_ms=latency_ms,
            ttft_ms=ttft_ms,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cached_tokens=cached_tokens,
            reasoning_tokens=reasoning_tokens,
            cost_kop=cost_kop,
            fx_usd_rub=fx_usd_rub,
            status=status,
            http_code=http_code,
            error_code=error_code,
            error_message=error_message,
            is_streaming=is_streaming,
            cache_hit=cache_hit,
            pii_masked=pii_masked,
            tools_used=tools_used,
            request_body=rb,
            response_body=sb,
            model_params=_truncate_jsonb(model_params),
            metadata_=_truncate_jsonb(metadata),
        )
        db.add(row)
        await db.commit()
    except Exception as exc:
        # Best-effort. We don't want a transient DB hiccup to mask
        # billing success.
        with contextlib.suppress(Exception):
            await db.rollback()
        log.warning(
            "request_log_persist_failed",
            request_id=request_id,
            account_id=str(account_id),
            error=str(exc),
        )


async def should_store_bodies(db: AsyncSession, account_id: uuid.UUID) -> bool:
    """Per-account opt-in to stashing request/response bodies in traces.

    Reuses the existing ``Account.store_prompts`` flag (`prompt_logging_
    enabled` in API surface) — if a customer opts into prompt logging,
    they implicitly want the bodies in traces too. This avoids inventing
    a second toggle.
    """
    try:
        account = await db.get(Account, account_id)
        return bool(account and account.store_prompts)
    except Exception:
        return False
