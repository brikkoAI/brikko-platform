"""GET /v1/usage — aggregated traffic for the calling key/account.

Returns zero-buckets when there is nothing yet so the API contract stays
stable for V0 clients. Real bucketing by day/model arrives with the
analytics consumer (see tech_stack.md §3 partitioning plan and the V2
Clickhouse migration trigger).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from voltari_gateway.auth.middleware import Principal, require_api_key_or_session
from voltari_gateway.db.models import UsageEvent
from voltari_gateway.db.session import get_db

router = APIRouter()


def _parse_iso(value: str, *, default: datetime, end_of_day: bool = False) -> datetime:
    """Парсит ISO-строку или date-only "YYYY-MM-DD".

    `end_of_day=True` — для верхней границы периода: date-only `to=2026-04-29`
    интерпретируется как **весь день включительно** (`23:59:59.999999`), не
    как полночь. Stripe convention: `to=YYYY-MM-DD` означает inclusive end.
    Без этого usage events с `created_at` после полуночи в `to=today`
    не попадали в результат (TD-042).
    """
    if not value:
        return default
    # Accept "2026-04-01" or full ISO; default tz=UTC.
    try:
        if len(value) == 10:
            dt = datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=UTC)
            if end_of_day:
                # +1 day - 1 microsecond = 23:59:59.999999 of the requested date.
                dt = dt + timedelta(days=1) - timedelta(microseconds=1)
            return dt
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return default


@router.get(
    "/v1/usage",
    tags=["billing"],
    summary="Aggregate usage and cost over a time window",
    description=(
        "Returns request count, token totals (input/output/cached), and "
        "billed cost for the active account.\n\n"
        "**Date semantics** (TD-042): ``from`` is inclusive at 00:00:00 UTC, "
        "``to`` is **inclusive end-of-day** at 23:59:59.999999 UTC for "
        "date-only inputs (``YYYY-MM-DD``). Full ISO timestamps are honoured "
        "exactly. Defaults: ``from`` = today 00:00 UTC, ``to`` = now.\n\n"
        "**Field naming**: this response uses ``tokens_in``/``tokens_out``/"
        "``cost_kop`` to match the SPA contract in "
        "``apps/web/src/lib/types.ts::UsageTotals``."
    ),
)
async def usage_endpoint(
    # Dual auth: Bearer (SDK clients) ИЛИ cookie session (web dashboard).
    # Раньше был только require_api_key — это ломало dashboard /app которая
    # ходит за usage по cookie. См. tech_debt_registry.md → TD-031.
    principal: Annotated[Principal, Depends(require_api_key_or_session)],
    db: Annotated[AsyncSession, Depends(get_db)],
    from_: Annotated[str | None, Query(alias="from")] = None,
    to: Annotated[str | None, Query()] = None,
) -> dict[str, object]:
    now = datetime.now(UTC)
    period_from = _parse_iso(
        from_ or "", default=now.replace(hour=0, minute=0, second=0, microsecond=0)
    )
    period_to = _parse_iso(to or "", default=now, end_of_day=True)

    stmt = (
        select(
            func.count(UsageEvent.id).label("request_count"),
            func.coalesce(func.sum(UsageEvent.input_tokens), 0).label("input_tokens"),
            func.coalesce(func.sum(UsageEvent.output_tokens), 0).label("output_tokens"),
            func.coalesce(func.sum(UsageEvent.cached_tokens), 0).label("cached_tokens"),
            func.coalesce(func.sum(UsageEvent.cost_kopecks), 0).label("cost_kopecks"),
        )
        .where(UsageEvent.account_id == principal.account_id)
        .where(UsageEvent.created_at >= period_from)
        .where(UsageEvent.created_at <= period_to)
    )
    row = (await db.execute(stmt)).one()

    # Field names follow the public API contract defined in apps/web/src/lib/types.ts
    # (UsageTotals/UsageBucket) — matches `x_gateway.cost_kop` in /v1/chat/completions
    # responses (Sprint 1). Renaming would be a breaking change for any SDK client
    # already integrated, so backend conforms to the contract.
    return {
        "object": "list",
        "period": {
            "from": period_from.isoformat(),
            "to": period_to.isoformat(),
        },
        "data": [],  # detailed buckets — populated in a follow-up task
        "totals": {
            "request_count": int(row.request_count or 0),
            "tokens_in": int(row.input_tokens or 0),
            "tokens_out": int(row.output_tokens or 0),
            "cached_tokens": int(row.cached_tokens or 0),
            "cost_kop": int(row.cost_kopecks or 0),
        },
    }
