"""BrikkoLens — analytics endpoints.

Phase 5 #4 Sprint 2 (2026-05-09).

Что отдаём:
  * ``GET /v1/account/analytics/summary`` — KPI totals + daily series +
    breakdowns (by_model, by_provider, by_status). Окно по умолчанию
    14 дней, диапазон управляется ``from`` / ``to``.

Решения:

  * **Читаем напрямую из ``gateway_request_log``** (а не из
    ``gateway_request_daily`` materialized view).  MV даёт скорость
    при >100k rows, но требует cron-refresh; на MVP-объёмах (<10k
    rows/день) on-the-fly aggregation быстрее ввода ((<10мс) и
    показывает реальное состояние без задержки.  Когда упрёмся в
    p95 этого endpoint'а, переключимся на MV (см. Alembic 0017).
  * **Account isolation** через ``principal.account.id``.  Никакого
    cross-account leak'а не делаем даже для admin-роли — analytics
    привязаны к аккаунту того, кто залогинен.
  * **Tariff-gating: minimum.** Базовый dashboard — commodity.  Для
    Pro+ позже добавим: custom-period (>30 дней), exports CSV/Parquet,
    cohort analysis.
  * **Latency p50/p95** считаем через ``percentile_cont`` —
    точное, но дорого; на 1k rows тривиально.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, literal_column, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute

from voltari_gateway.auth.session_middleware import (
    SessionPrincipal,
    require_session,
)
from voltari_gateway.db.models import GatewayRequestLog
from voltari_gateway.db.session import get_db

router = APIRouter(prefix="/v1/account", tags=["account"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class AnalyticsTotals(BaseModel):
    """KPI totals по выбранному периоду."""

    requests: int
    errors: int
    prompt_tokens: int
    completion_tokens: int
    cached_tokens: int
    cost_kop: int
    cache_hits: int
    tool_calls: int
    avg_latency_ms: int
    p50_latency_ms: int
    p95_latency_ms: int


class AnalyticsDaily(BaseModel):
    """Одна точка во time-series."""

    day: date
    requests: int
    errors: int
    cost_kop: int
    prompt_tokens: int
    completion_tokens: int
    p50_latency_ms: int | None
    p95_latency_ms: int | None


class AnalyticsBreakdownItem(BaseModel):
    """Группировка по модели / провайдеру / статусу."""

    key: str
    requests: int
    cost_kop: int
    share: float = Field(..., description="Доля от total requests, 0..1.")


class AnalyticsSummaryResponse(BaseModel):
    """GET /v1/account/analytics/summary."""

    range_from: date
    range_to: date
    totals: AnalyticsTotals
    daily: list[AnalyticsDaily]
    by_model: list[AnalyticsBreakdownItem]
    by_provider: list[AnalyticsBreakdownItem]
    by_status: list[AnalyticsBreakdownItem]


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------


# Жёсткий cap на окно: 90 дней. Защищает от случайного DOS'а при
# /summary?from=2020-01-01.
MAX_RANGE_DAYS = 90
DEFAULT_RANGE_DAYS = 14


@router.get("/analytics/summary", response_model=AnalyticsSummaryResponse)
async def get_analytics_summary(
    principal: Annotated[SessionPrincipal, Depends(require_session)],
    db: Annotated[AsyncSession, Depends(get_db)],
    range_from: Annotated[
        date | None,
        Query(
            alias="from",
            description="Начало периода (дата ISO).  По умолчанию: today - 14 дней.",
        ),
    ] = None,
    range_to: Annotated[
        date | None,
        Query(
            alias="to",
            description="Конец периода (включительно).  По умолчанию: today.",
        ),
    ] = None,
) -> AnalyticsSummaryResponse:
    """Сводка по запросам клиента за период.

    Если диапазон не указан — последние 14 дней включительно по UTC.
    Cap: 90 дней (запрос длиннее автоматически режется до 90 от ``to``).
    """
    today = datetime.now(UTC).date()
    if range_to is None:
        range_to = today
    if range_from is None:
        range_from = range_to - timedelta(days=DEFAULT_RANGE_DAYS - 1)
    # Cap безопасности.
    if (range_to - range_from).days >= MAX_RANGE_DAYS:
        range_from = range_to - timedelta(days=MAX_RANGE_DAYS - 1)
    # Нормализация: range_from <= range_to.
    if range_from > range_to:
        range_from, range_to = range_to, range_from

    start_dt = datetime.combine(range_from, datetime.min.time(), tzinfo=UTC)
    end_dt = datetime.combine(range_to + timedelta(days=1), datetime.min.time(), tzinfo=UTC)

    account_id = principal.account.id

    base_filters = [
        GatewayRequestLog.account_id == account_id,
        GatewayRequestLog.created_at >= start_dt,
        GatewayRequestLog.created_at < end_dt,
    ]

    # `percentile_cont` есть только в Postgres — SQLite (используется в
    # тестах) даст SQLAlchemyError.  В таких случаях падаем на p50/p95=0
    # и выводим только avg.
    dialect_name = db.bind.dialect.name if db.bind is not None else ""
    use_percentile = dialect_name == "postgresql"

    # --- Totals ---------------------------------------------------------
    totals_cols = [
        func.count().label("requests"),
        func.count().filter(GatewayRequestLog.status == "error").label("errors"),
        func.coalesce(func.sum(GatewayRequestLog.prompt_tokens), 0).label("prompt_tokens"),
        func.coalesce(func.sum(GatewayRequestLog.completion_tokens), 0).label("completion_tokens"),
        func.coalesce(func.sum(GatewayRequestLog.cached_tokens), 0).label("cached_tokens"),
        func.coalesce(func.sum(GatewayRequestLog.cost_kop), 0).label("cost_kop"),
        func.count().filter(GatewayRequestLog.cache_hit.is_(True)).label("cache_hits"),
        func.count().filter(GatewayRequestLog.tools_used.is_(True)).label("tool_calls"),
        func.coalesce(func.avg(GatewayRequestLog.latency_ms), 0).label("avg_latency_ms"),
    ]
    if use_percentile:
        totals_cols.extend(
            [
                func.coalesce(
                    func.percentile_cont(0.5).within_group(GatewayRequestLog.latency_ms),
                    0,
                ).label("p50_latency_ms"),
                func.coalesce(
                    func.percentile_cont(0.95).within_group(GatewayRequestLog.latency_ms),
                    0,
                ).label("p95_latency_ms"),
            ]
        )
    totals_q = select(*totals_cols).where(*base_filters)
    totals_row = (await db.execute(totals_q)).first()
    if totals_row is None:
        totals = AnalyticsTotals(
            requests=0,
            errors=0,
            prompt_tokens=0,
            completion_tokens=0,
            cached_tokens=0,
            cost_kop=0,
            cache_hits=0,
            tool_calls=0,
            avg_latency_ms=0,
            p50_latency_ms=0,
            p95_latency_ms=0,
        )
    else:
        totals = AnalyticsTotals(
            requests=int(totals_row.requests or 0),
            errors=int(totals_row.errors or 0),
            prompt_tokens=int(totals_row.prompt_tokens or 0),
            completion_tokens=int(totals_row.completion_tokens or 0),
            cached_tokens=int(totals_row.cached_tokens or 0),
            cost_kop=int(totals_row.cost_kop or 0),
            cache_hits=int(totals_row.cache_hits or 0),
            tool_calls=int(totals_row.tool_calls or 0),
            avg_latency_ms=int(totals_row.avg_latency_ms or 0),
            p50_latency_ms=int(getattr(totals_row, "p50_latency_ms", 0) or 0),
            p95_latency_ms=int(getattr(totals_row, "p95_latency_ms", 0) or 0),
        )

    # --- Daily ----------------------------------------------------------
    if use_percentile:
        day_col = func.date_trunc("day", GatewayRequestLog.created_at).label("day")
    else:
        # SQLite: date(created_at) даёт `YYYY-MM-DD`-строку, потом нормализуем.
        day_col = func.date(GatewayRequestLog.created_at).label("day")
    daily_cols = [
        day_col,
        func.count().label("requests"),
        func.count().filter(GatewayRequestLog.status == "error").label("errors"),
        func.coalesce(func.sum(GatewayRequestLog.cost_kop), 0).label("cost_kop"),
        func.coalesce(func.sum(GatewayRequestLog.prompt_tokens), 0).label("prompt_tokens"),
        func.coalesce(func.sum(GatewayRequestLog.completion_tokens), 0).label("completion_tokens"),
    ]
    if use_percentile:
        daily_cols.extend(
            [
                func.percentile_cont(0.5).within_group(GatewayRequestLog.latency_ms).label("p50"),
                func.percentile_cont(0.95).within_group(GatewayRequestLog.latency_ms).label("p95"),
            ]
        )
    daily_q = select(*daily_cols).where(*base_filters).group_by(day_col).order_by(day_col)
    daily_rows = (await db.execute(daily_q)).all()

    def _normalise_day(value: object) -> date | None:
        if value is None:
            return None
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
        if isinstance(value, str):
            try:
                return date.fromisoformat(value[:10])
            except ValueError:
                return None
        return None

    daily_by_day = {
        norm: row for row in daily_rows if (norm := _normalise_day(row.day)) is not None
    }
    # Заполняем пробелы — клиенту проще рисовать без пропусков.
    daily: list[AnalyticsDaily] = []
    cursor = range_from
    while cursor <= range_to:
        row = daily_by_day.get(cursor)
        if row is None:
            daily.append(
                AnalyticsDaily(
                    day=cursor,
                    requests=0,
                    errors=0,
                    cost_kop=0,
                    prompt_tokens=0,
                    completion_tokens=0,
                    p50_latency_ms=None,
                    p95_latency_ms=None,
                )
            )
        else:
            p50_val = getattr(row, "p50", None)
            p95_val = getattr(row, "p95", None)
            daily.append(
                AnalyticsDaily(
                    day=cursor,
                    requests=int(row.requests or 0),
                    errors=int(row.errors or 0),
                    cost_kop=int(row.cost_kop or 0),
                    prompt_tokens=int(row.prompt_tokens or 0),
                    completion_tokens=int(row.completion_tokens or 0),
                    p50_latency_ms=int(p50_val) if p50_val is not None else None,
                    p95_latency_ms=int(p95_val) if p95_val is not None else None,
                )
            )
        cursor += timedelta(days=1)

    # --- Breakdowns -----------------------------------------------------
    total_requests = totals.requests or 1  # деление на 0 защита

    async def _breakdown(
        column: InstrumentedAttribute[Any], limit: int
    ) -> list[AnalyticsBreakdownItem]:
        q = (
            select(
                column.label("key"),
                func.count().label("requests"),
                func.coalesce(func.sum(GatewayRequestLog.cost_kop), 0).label("cost_kop"),
            )
            .where(*base_filters)
            .group_by(column)
            .order_by(literal_column("requests").desc())
            .limit(limit)
        )
        rows = (await db.execute(q)).all()
        return [
            AnalyticsBreakdownItem(
                key=str(r.key),
                requests=int(r.requests or 0),
                cost_kop=int(r.cost_kop or 0),
                share=round((r.requests or 0) / total_requests, 4),
            )
            for r in rows
        ]

    by_model = await _breakdown(GatewayRequestLog.model, limit=10)
    by_provider = await _breakdown(GatewayRequestLog.provider, limit=10)
    by_status = await _breakdown(GatewayRequestLog.status, limit=5)

    return AnalyticsSummaryResponse(
        range_from=range_from,
        range_to=range_to,
        totals=totals,
        daily=daily,
        by_model=by_model,
        by_provider=by_provider,
        by_status=by_status,
    )


__all__ = ["router"]
