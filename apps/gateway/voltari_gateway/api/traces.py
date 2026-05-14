"""BrikkoLens — observability API endpoints.

Phase 5 #4 Sprint 1в (2026-05-09).

* ``GET /v1/account/traces`` — список trace-row'ов аккаунта с
  пагинацией и фильтрами (status, model, api_key_id, time range).
  Поддерживает поиск по конкретному request_id.
* ``GET /v1/account/traces/{request_id}`` — детальный view одного
  trace + opt-in bodies (request/response) если account.store_prompts
  включён.

Frontend ``/app/traces`` строится поверх этих endpoint'ов. Постранично
по 50 записей; фильтры через query-параметры (минимальный сейчас, в
Sprint 3 расширяется до полной search-формы).

Tariff-gating (правило CEO «не gate'ить commodity»):
  * Базовое логирование cost доступно ВСЕМ тарифам — это commodity.
  * Здесь read-side: список traces без ограничения по тарифу.
  * Поиск по metadata, retention >7 дней, графики p50/p95 — это
    Pro+ фичи (Sprint 2-3 будут добавлять gate'ы).

Sprint 1 read API намеренно широкий и без gate'ов — см. CEO patterns
«не tariff-gate'ить commodity», ``feedback_decision_patterns.md``.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Path, Query
from pydantic import BaseModel, Field
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from voltari_gateway.auth.session_middleware import (
    SessionPrincipal,
    require_session,
)
from voltari_gateway.db.models import GatewayRequestLog
from voltari_gateway.db.session import get_db
from voltari_gateway.utils.errors import GatewayError

router = APIRouter(prefix="/v1/account", tags=["account"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class TraceListItem(BaseModel):
    """Compact representation для списка."""

    id: str
    request_id: str
    provider: str
    model: str
    routed_from: str | None
    started_at: datetime
    finished_at: datetime
    latency_ms: int
    ttft_ms: int | None
    prompt_tokens: int
    completion_tokens: int
    cached_tokens: int
    cost_kop: int
    status: str
    http_code: int | None
    error_code: str | None
    is_streaming: bool
    cache_hit: bool
    tools_used: bool
    pii_masked: bool


class TracesListResponse(BaseModel):
    items: list[TraceListItem]
    total: int = Field(description="Total matching rows for these filters.")
    has_more: bool = Field(description="True if next page exists (offset+limit<total).")


class TraceDetail(TraceListItem):
    """Full trace + opt-in bodies."""

    api_key_id: str | None
    error_message: str | None
    reasoning_tokens: int
    fx_usd_rub: float | None
    request_body: dict[str, Any] | None
    response_body: dict[str, Any] | None
    model_params: dict[str, Any] | None
    metadata: dict[str, Any] | None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _row_to_list_item(row: GatewayRequestLog) -> TraceListItem:
    return TraceListItem(
        id=str(row.id),
        request_id=row.request_id,
        provider=row.provider,
        model=row.model,
        routed_from=row.routed_from,
        started_at=row.started_at,
        finished_at=row.finished_at,
        latency_ms=row.latency_ms,
        ttft_ms=row.ttft_ms,
        prompt_tokens=row.prompt_tokens,
        completion_tokens=row.completion_tokens,
        cached_tokens=row.cached_tokens,
        cost_kop=row.cost_kop,
        status=row.status,
        http_code=row.http_code,
        error_code=row.error_code,
        is_streaming=row.is_streaming,
        cache_hit=row.cache_hit,
        tools_used=row.tools_used,
        pii_masked=row.pii_masked,
    )


def _row_to_detail(row: GatewayRequestLog) -> TraceDetail:
    base = _row_to_list_item(row)
    return TraceDetail(
        **base.model_dump(),
        api_key_id=str(row.api_key_id) if row.api_key_id else None,
        error_message=row.error_message,
        reasoning_tokens=row.reasoning_tokens,
        fx_usd_rub=float(row.fx_usd_rub) if row.fx_usd_rub is not None else None,
        request_body=row.request_body,
        response_body=row.response_body,
        model_params=row.model_params,
        metadata=row.metadata_,
    )


# ---------------------------------------------------------------------------
# GET /v1/account/traces
# ---------------------------------------------------------------------------


@router.get(
    "/traces",
    response_model=TracesListResponse,
    summary="List traces (BrikkoLens)",
    description=(
        "Returns paginated list of gateway request traces for the calling "
        "account. Default order: most recent first. Filters: status, "
        "model, api_key_id, time range. Search: pass `q` to substring-"
        "match request_id."
    ),
)
async def list_traces(
    principal: Annotated[SessionPrincipal, Depends(require_session)],
    db: Annotated[AsyncSession, Depends(get_db)],
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    status: Annotated[
        Literal["ok", "error", "timeout", "cache_hit", "rate_limited", "cancelled"] | None,
        Query(description="Filter by status enum."),
    ] = None,
    model: Annotated[str | None, Query(description="Filter by model id (exact).")] = None,
    provider: Annotated[
        str | None, Query(description="Filter by provider id (exact, e.g. openai, anthropic).")
    ] = None,
    api_key_id: Annotated[str | None, Query(description="Filter by api_key_id (UUID).")] = None,
    since: Annotated[
        datetime | None, Query(description="Inclusive lower bound on created_at (ISO8601).")
    ] = None,
    until: Annotated[
        datetime | None, Query(description="Exclusive upper bound on created_at (ISO8601).")
    ] = None,
    min_cost_kop: Annotated[
        int | None, Query(ge=0, description="Min cost in kopecks (inclusive).")
    ] = None,
    max_cost_kop: Annotated[
        int | None, Query(ge=0, description="Max cost in kopecks (inclusive).")
    ] = None,
    min_latency_ms: Annotated[
        int | None, Query(ge=0, description="Min latency in milliseconds (inclusive).")
    ] = None,
    max_latency_ms: Annotated[
        int | None, Query(ge=0, description="Max latency in milliseconds (inclusive).")
    ] = None,
    only_with_tools: Annotated[
        bool | None, Query(description="Only requests that used function calling.")
    ] = None,
    only_with_cache: Annotated[
        bool | None, Query(description="Only requests with cache_hit=True.")
    ] = None,
    q: Annotated[
        str | None,
        Query(
            description=(
                "Substring search across request_id, model, error_message, "
                "and error_code (case-insensitive)."
            ),
            max_length=128,
        ),
    ] = None,
) -> TracesListResponse:
    base_filter = [GatewayRequestLog.account_id == principal.account.id]
    if status is not None:
        base_filter.append(GatewayRequestLog.status == status)
    if model:
        base_filter.append(GatewayRequestLog.model == model)
    if provider:
        base_filter.append(GatewayRequestLog.provider == provider)
    if api_key_id:
        try:
            base_filter.append(GatewayRequestLog.api_key_id == uuid.UUID(api_key_id))
        except ValueError:
            # Invalid UUID — treat as no-match.
            return TracesListResponse(items=[], total=0, has_more=False)
    if since is not None:
        base_filter.append(GatewayRequestLog.created_at >= since)
    if until is not None:
        base_filter.append(GatewayRequestLog.created_at < until)
    if min_cost_kop is not None:
        base_filter.append(GatewayRequestLog.cost_kop >= min_cost_kop)
    if max_cost_kop is not None:
        base_filter.append(GatewayRequestLog.cost_kop <= max_cost_kop)
    if min_latency_ms is not None:
        base_filter.append(GatewayRequestLog.latency_ms >= min_latency_ms)
    if max_latency_ms is not None:
        base_filter.append(GatewayRequestLog.latency_ms <= max_latency_ms)
    if only_with_tools is True:
        base_filter.append(GatewayRequestLog.tools_used.is_(True))
    if only_with_cache is True:
        base_filter.append(GatewayRequestLog.cache_hit.is_(True))
    if q:
        from sqlalchemy import or_

        like_pattern = f"%{q}%"
        base_filter.append(
            or_(
                GatewayRequestLog.request_id.ilike(like_pattern),
                GatewayRequestLog.model.ilike(like_pattern),
                GatewayRequestLog.error_message.ilike(like_pattern),
                GatewayRequestLog.error_code.ilike(like_pattern),
            )
        )

    # Total count для пагинации (cheap при наличии index'а account_recent).
    from sqlalchemy import func

    count_stmt = select(func.count()).select_from(GatewayRequestLog).where(*base_filter)
    total = int((await db.execute(count_stmt)).scalar_one() or 0)

    rows_stmt = (
        select(GatewayRequestLog)
        .where(*base_filter)
        .order_by(desc(GatewayRequestLog.created_at))
        .offset(offset)
        .limit(limit)
    )
    rows = (await db.execute(rows_stmt)).scalars().all()
    items = [_row_to_list_item(r) for r in rows]
    return TracesListResponse(
        items=items,
        total=total,
        has_more=(offset + len(items)) < total,
    )


# ---------------------------------------------------------------------------
# GET /v1/account/traces/{request_id}
# ---------------------------------------------------------------------------


@router.get(
    "/traces/{request_id}",
    response_model=TraceDetail,
    summary="Trace detail (BrikkoLens)",
    description=(
        "Returns the full trace for a single request_id (most recent if "
        "duplicates, though request_id should be unique within account). "
        "Includes request/response bodies if account opted into "
        "prompt logging (Account.store_prompts=True)."
    ),
)
async def get_trace_detail(
    principal: Annotated[SessionPrincipal, Depends(require_session)],
    db: Annotated[AsyncSession, Depends(get_db)],
    request_id: Annotated[str, Path(min_length=1, max_length=256)],
) -> TraceDetail:
    stmt = (
        select(GatewayRequestLog)
        .where(
            GatewayRequestLog.account_id == principal.account.id,
            GatewayRequestLog.request_id == request_id,
        )
        .order_by(desc(GatewayRequestLog.created_at))
        .limit(1)
    )
    row = (await db.execute(stmt)).scalar_one_or_none()
    if row is None:
        raise GatewayError(
            status_code=404,
            message=f"Trace {request_id!r} not found.",
            type="invalid_request_error",
            code="trace_not_found",
        )
    return _row_to_detail(row)


__all__ = ["router"]
