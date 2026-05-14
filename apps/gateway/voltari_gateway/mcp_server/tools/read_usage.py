"""``read_usage`` MCP tool — usage summary for 24h / 7d / 30d.

Aggregates ``usage_events`` for the calling account and returns:

* ``total_requests`` — row count.
* ``total_kopecks`` / ``total_rub`` — sum of ``cost_kopecks``.
* ``top_models`` — top 5 model ids by request count, with cost.

Read-only; supports exactly three period literals so the agent doesn't
hallucinate "12 hours" or "this year". Adding more presets is a one-line
change in ``_PERIOD_HOURS``.

Performance: the ``ix_usage_events_account_created`` composite index
(see ``db/models.py``) makes the bounded ``created_at >= now-period``
scan cheap even at millions of rows. ``GROUP BY model`` is in memory
after the bounded fetch — fine at MVP scale (worst case ~tens of
thousands of rows / month per account).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from voltari_gateway.db.models import UsageEvent
from voltari_gateway.mcp_server.context import current_principal
from voltari_gateway.utils.errors import GatewayError

NAME = "read_usage"
DESCRIPTION = (
    "Return Brikko usage summary for the calling account over a fixed "
    "window (24h / 7d / 30d). Includes total request count, total cost "
    "in RUB + kopecks, and the top 5 models by request count. Read-only."
)
INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "period": {
            "type": "string",
            "enum": ["24h", "7d", "30d"],
            "description": "Window over which to aggregate usage events.",
            "default": "24h",
        }
    },
    "required": [],
    "additionalProperties": False,
}

# Period literals mapped to absolute hour deltas. Keep this tight — every
# new entry forces a corresponding enum widening in INPUT_SCHEMA and a
# downstream change in the helper-skill (which renders presets verbatim).
_PERIOD_HOURS: dict[str, int] = {
    "24h": 24,
    "7d": 24 * 7,
    "30d": 24 * 30,
}

_TOP_N = 5


async def handler(arguments: dict[str, Any], db: AsyncSession) -> dict[str, Any]:
    """Aggregate usage_events for the calling account over ``arguments['period']``."""
    principal = current_principal()

    period = str(arguments.get("period", "24h"))
    hours = _PERIOD_HOURS.get(period)
    if hours is None:
        # SDK input validation should catch this first; defence-in-depth.
        raise GatewayError(
            status_code=400,
            message=f"Unknown period '{period}'. Use one of: {sorted(_PERIOD_HOURS)}.",
            type="invalid_request_error",
            code="invalid_period",
        )

    cutoff = datetime.now(UTC) - timedelta(hours=hours)

    # 1) Totals (requests + kopecks).
    totals_stmt = (
        select(
            func.count().label("requests"),
            func.coalesce(func.sum(UsageEvent.cost_kopecks), 0).label("kopecks"),
        )
        .where(UsageEvent.account_id == principal.account_id)
        .where(UsageEvent.created_at >= cutoff)
    )
    totals_row = (await db.execute(totals_stmt)).one()
    total_requests = int(totals_row.requests)
    total_kopecks = int(totals_row.kopecks)

    # 2) Top-N models. Aggregate by model, order by request count desc.
    top_stmt = (
        select(
            UsageEvent.model.label("model_id"),
            func.count().label("requests"),
            func.coalesce(func.sum(UsageEvent.cost_kopecks), 0).label("kopecks"),
        )
        .where(UsageEvent.account_id == principal.account_id)
        .where(UsageEvent.created_at >= cutoff)
        .group_by(UsageEvent.model)
        .order_by(func.count().desc())
        .limit(_TOP_N)
    )
    top_rows = (await db.execute(top_stmt)).all()
    top_models = [
        {
            "model_id": row.model_id,
            "requests": int(row.requests),
            "kopecks": int(row.kopecks),
        }
        for row in top_rows
    ]

    return {
        "period": period,
        "total_requests": total_requests,
        "total_kopecks": total_kopecks,
        "total_rub": round(total_kopecks / 100.0, 2),
        "top_models": top_models,
    }


__all__ = ["DESCRIPTION", "INPUT_SCHEMA", "NAME", "handler"]
