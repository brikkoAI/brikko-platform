"""Platform admin status endpoint — single-pane дашборд для CEO.

Sprint 13.7 (2026-05-09).

Что отдаём:
  * ``GET /v1/account/admin/status`` — агрегированный snapshot всей платформы:
    - api_uptime         (gateway процесс)
    - last_24h            (RPS, errors, cost_kop, active_accounts)
    - providers           (статус openai/anthropic/sber/yandex/...)
    - database            (size_bytes, accounts_total, traces_total)
    - last_deploy         (commit sha + time из BRIKKO_GIT_SHA env)
    - storage             (свободное место на VPS — отдельный nice-to-have,
                           отдаётся None в MVP, добавим когда node-exporter
                           будет accessible через Grafana API)

Доступ ограничен env var ``ADMIN_EMAILS`` (CSV).  Только email из этого
списка получают 200; остальные — 403.  Cookie-сессия (require_session)
обязательна.  Если ADMIN_EMAILS пустой — endpoint вернёт 403 всем (closed
by default).

Почему не Grafana:
  * CEO часто открывает app.brikko.ru с мобилы/в браузере без VPN.
  * Single-pane должен быть "одна вкладка с мобилы и видно всё".
  * Grafana stack — для глубоких метрик, этот endpoint — для daily check'а.

Тестирование:
  * Без сессии → 401
  * С обычной сессией (email не в ADMIN_EMAILS) → 403
  * Пустой ADMIN_EMAILS → всем 403
  * С admin сессией → 200 + структурированный snapshot
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from voltari_gateway import __version__
from voltari_gateway.auth.session_middleware import (
    SessionPrincipal,
    require_session,
)
from voltari_gateway.config import get_settings
from voltari_gateway.db.models import Account, GatewayRequestLog, User
from voltari_gateway.db.session import get_db
from voltari_gateway.utils.errors import GatewayError
from voltari_gateway.utils.logging import get_logger

router = APIRouter(prefix="/v1/account", tags=["admin"])
log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class ProviderHealth(BaseModel):
    name: str
    configured: bool
    status: str = Field(description="ok | error | unconfigured")


class Last24h(BaseModel):
    requests: int
    errors: int
    error_rate: float = Field(description="0..1")
    cost_kop: int
    active_accounts: int
    avg_latency_ms: int


class DatabaseStats(BaseModel):
    accounts_total: int
    users_total: int
    traces_total: int
    traces_24h: int


class DeployInfo(BaseModel):
    sha: str | None
    deployed_at: datetime | None
    version: str


class AdminStatusResponse(BaseModel):
    api_status: str = Field(description="ok | degraded | down")
    timestamp: datetime
    last_24h: Last24h
    providers: list[ProviderHealth]
    database: DatabaseStats
    deploy: DeployInfo


# ---------------------------------------------------------------------------
# Authorization helper
# ---------------------------------------------------------------------------


def _is_admin(principal: SessionPrincipal) -> bool:
    """Проверка что email пользователя в ADMIN_EMAILS env-списке."""
    raw = (get_settings().admin_emails or "").strip()
    if not raw:
        return False
    allowed = {e.strip().lower() for e in raw.split(",") if e.strip()}
    return principal.user.email.lower() in allowed


def require_admin(
    principal: Annotated[SessionPrincipal, Depends(require_session)],
) -> SessionPrincipal:
    """403 если email не в ADMIN_EMAILS.  Cookie-сессия обязательна."""
    if not _is_admin(principal):
        raise GatewayError(
            status_code=403,
            message="Admin access required.",
            type="invalid_request_error",
            code="forbidden",
        )
    return principal


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------


@router.get("/admin/status", response_model=AdminStatusResponse)
async def admin_status(
    principal: Annotated[SessionPrincipal, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> AdminStatusResponse:
    """Single-pane snapshot всей платформы для CEO."""
    now = datetime.now(UTC)
    cutoff_24h = now - timedelta(hours=24)

    # --- Last 24h: RPS / errors / cost / active accounts ----------------
    last_24h_q = select(
        func.count().label("requests"),
        func.count().filter(GatewayRequestLog.status == "error").label("errors"),
        func.coalesce(func.sum(GatewayRequestLog.cost_kop), 0).label("cost_kop"),
        func.count(func.distinct(GatewayRequestLog.account_id)).label("active_accounts"),
        func.coalesce(func.avg(GatewayRequestLog.latency_ms), 0).label("avg_latency_ms"),
    ).where(GatewayRequestLog.created_at >= cutoff_24h)
    row = (await db.execute(last_24h_q)).first()
    requests = int(row.requests or 0) if row else 0
    errors = int(row.errors or 0) if row else 0
    last_24h = Last24h(
        requests=requests,
        errors=errors,
        error_rate=(errors / requests) if requests > 0 else 0.0,
        cost_kop=int(row.cost_kop or 0) if row else 0,
        active_accounts=int(row.active_accounts or 0) if row else 0,
        avg_latency_ms=int(row.avg_latency_ms or 0) if row else 0,
    )

    # --- Providers status (reuse existing /health/ready logic) ----------
    # Inline minimal copy: configured-flag check.  Глубокий ping каждого
    # провайдера дорогой (5+ HTTP calls) — повторяем не каждый /admin/status,
    # а при /health/ready (Caddy уже бьёт его регулярно).  Здесь — флажок.
    providers: list[ProviderHealth] = []
    s = get_settings()
    provider_checks = [
        ("openai", bool(s.openai_api_key.get_secret_value())),
        ("anthropic", bool(s.anthropic_api_key.get_secret_value())),
        ("google", bool(s.google_api_key.get_secret_value())),
        ("deepseek", bool(s.deepseek_api_key.get_secret_value())),
        ("yandex", bool(s.yandex_api_key.get_secret_value())),
        ("sber", bool(s.sber_auth_key.get_secret_value())),
    ]
    for name, configured in provider_checks:
        providers.append(
            ProviderHealth(
                name=name,
                configured=configured,
                status="ok" if configured else "unconfigured",
            )
        )

    # --- Database stats -------------------------------------------------
    accounts_total = int(
        (await db.execute(select(func.count()).select_from(Account))).scalar_one() or 0
    )
    users_total = int((await db.execute(select(func.count()).select_from(User))).scalar_one() or 0)
    traces_total = int(
        (await db.execute(select(func.count()).select_from(GatewayRequestLog))).scalar_one() or 0
    )
    traces_24h = int(
        (
            await db.execute(
                select(func.count())
                .select_from(GatewayRequestLog)
                .where(GatewayRequestLog.created_at >= cutoff_24h)
            )
        ).scalar_one()
        or 0
    )

    db_stats = DatabaseStats(
        accounts_total=accounts_total,
        users_total=users_total,
        traces_total=traces_total,
        traces_24h=traces_24h,
    )

    # --- Deploy info из env (CI прокидывает GITHUB_SHA в .env) ----------
    deploy_sha = os.environ.get("BRIKKO_GIT_SHA") or os.environ.get("GIT_COMMIT_SHA")
    deploy_at_raw = os.environ.get("BRIKKO_DEPLOY_AT")
    deploy_at: datetime | None = None
    if deploy_at_raw:
        try:
            deploy_at = datetime.fromisoformat(deploy_at_raw)
            if deploy_at.tzinfo is None:
                deploy_at = deploy_at.replace(tzinfo=UTC)
        except ValueError:
            pass

    deploy_info = DeployInfo(
        sha=deploy_sha[:12] if deploy_sha else None,
        deployed_at=deploy_at,
        version=__version__,
    )

    # --- Aggregate API status -------------------------------------------
    # Logic:
    #   - any error_rate > 5% за 24ч → degraded
    #   - 0 configured providers → down (gateway не может маршрутизировать)
    #   - иначе → ok
    configured_count = sum(1 for p in providers if p.configured)
    if configured_count == 0:
        api_status = "down"
    elif last_24h.error_rate > 0.05:
        api_status = "degraded"
    else:
        api_status = "ok"

    return AdminStatusResponse(
        api_status=api_status,
        timestamp=now,
        last_24h=last_24h,
        providers=providers,
        database=db_stats,
        deploy=deploy_info,
    )


class AdminCheckResponse(BaseModel):
    """GET /v1/account/me/admin-check — для UI nav-toggle.

    Возвращает is_admin без 403 — обычные user'ы получают {is_admin: false}.
    Это позволяет фронту скрыть/показать админский nav-item без error-логов
    и без costly /admin/status вызова.
    """

    is_admin: bool


@router.get("/me/admin-check", response_model=AdminCheckResponse)
async def admin_check(
    principal: Annotated[SessionPrincipal, Depends(require_session)],
) -> AdminCheckResponse:
    """Проверка платформенного admin-уровня (по ADMIN_EMAILS env)."""
    return AdminCheckResponse(is_admin=_is_admin(principal))


__all__ = ["router"]
