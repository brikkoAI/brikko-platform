"""Admin provider-balances endpoint — Sprint 14 (2026-05-10).

Routes (все под ``/v1/account/admin``):

* ``GET    /provider_balances``                — список всех (10 провайдеров).
* ``POST   /provider_balances/refresh``        — fan-out refresh API-adapters.
* ``POST   /provider_balances/{provider}/manual`` — записать manual value.

Все три закрыты ``require_admin`` (ADMIN_EMAILS env). Cookie-сессия обязательна.
Без сессии → 401, без admin → 403, пустой ADMIN_EMAILS → 403 всем.
"""

from __future__ import annotations

import contextlib
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Annotated, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from voltari_gateway.api.admin_status import require_admin
from voltari_gateway.auth.session_middleware import SessionPrincipal
from voltari_gateway.balance_monitor.adapters import BalanceAdapter
from voltari_gateway.balance_monitor.service import (
    KNOWN_PROVIDERS,
    ProviderBalanceView,
    build_adapters,
    get_all,
    refresh_all,
    set_manual,
)
from voltari_gateway.config import get_settings
from voltari_gateway.db.session import get_db, get_session_factory
from voltari_gateway.utils.errors import GatewayError, invalid_request
from voltari_gateway.utils.logging import get_logger

router = APIRouter(prefix="/v1/account", tags=["admin"])
log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class ProviderBalanceItem(BaseModel):
    """One provider snapshot as exposed by GET /provider_balances."""

    provider: str
    # NB: serialize Decimal as float for the wire (frontend хочет number).
    # DB-точность не страдает — это только response-shape.
    balance_native: float | None = Field(
        default=None,
        description="Balance в нативной валюте провайдера (USD/EUR/CNY/RUB/tokens). None если ещё не вытянули.",
    )
    balance_currency: str | None = Field(
        default=None,
        description="USD | EUR | CNY | RUB | tokens",
    )
    balance_rub_kopecks: int | None = Field(
        default=None,
        description="Нормализация в ₽-копейки. None для tokens-баланса.",
    )
    last_fetched_at: datetime | None = None
    last_success_at: datetime | None = None
    fetch_status: Literal["ok", "error", "manual", "pending"]
    fetch_method: Literal["api", "manual", "scrape"]
    error_message: str | None = None
    burn_rate_kopecks_per_day: int | None = Field(
        default=None,
        description="Средний расход за последние 7 дней (₽-копейки/день).",
    )
    runway_days: int | None = Field(
        default=None,
        description="balance_rub_kopecks ÷ burn_rate. None если burn=0 или баланс не известен.",
    )
    notes: str | None = None


class ProviderBalancesResponse(BaseModel):
    items: list[ProviderBalanceItem]


class RefreshResponse(BaseModel):
    """Response from POST /provider_balances/refresh."""

    items: list[ProviderBalanceItem]
    refreshed_count: int
    error_count: int


class ManualBalanceRequest(BaseModel):
    """Body for POST /provider_balances/{provider}/manual."""

    balance_native: Decimal = Field(
        ge=0,
        description="Баланс в нативной валюте. Например 50.25.",
    )
    currency: Literal["USD", "EUR", "CNY", "RUB", "tokens"]
    notes: str | None = Field(default=None, max_length=512)


# ---------------------------------------------------------------------------
# Conversion helpers
# ---------------------------------------------------------------------------


def _view_to_item(view: ProviderBalanceView) -> ProviderBalanceItem:
    return ProviderBalanceItem(
        provider=view.provider,
        balance_native=view.balance_native,
        balance_currency=view.balance_currency,
        balance_rub_kopecks=view.balance_rub_kopecks,
        last_fetched_at=view.last_fetched_at,
        last_success_at=view.last_success_at,
        fetch_status=view.fetch_status,
        fetch_method=view.fetch_method,
        error_message=view.error_message,
        burn_rate_kopecks_per_day=view.burn_rate_kopecks_per_day,
        runway_days=view.runway_days,
        notes=view.notes,
    )


def _resolve_adapters(request_state: object) -> dict[str, BalanceAdapter]:
    """Pull ``balance_adapters`` map from ``app.state``, build on the fly if absent.

    Production wires it in ``main.lifespan``. Tests that don't run lifespan
    (most of our test suite uses ``create_app()`` without ASGI lifespan
    triggering) expect ``build_adapters`` to be called lazily here. Building
    is cheap (just constructs httpx clients with empty keys → ManualAdapter).
    """
    adapters = getattr(request_state, "balance_adapters", None)
    if isinstance(adapters, dict):
        return adapters
    return build_adapters(get_settings())


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get(
    "/admin/provider_balances",
    response_model=ProviderBalancesResponse,
)
async def list_provider_balances(
    _: Annotated[SessionPrincipal, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ProviderBalancesResponse:
    views = await get_all(db)
    return ProviderBalancesResponse(items=[_view_to_item(v) for v in views])


@router.post(
    "/admin/provider_balances/refresh",
    response_model=RefreshResponse,
)
async def refresh_provider_balances(
    _: Annotated[SessionPrincipal, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> RefreshResponse:
    """Fire-and-forget refresh для всех API-adapter'ов.

    Manual-balanced провайдеры (OpenAI/Anthropic/...) этим endpoint'ом
    НЕ трогаются — они обновляются через ``/manual``.
    """
    settings = get_settings()
    adapters = build_adapters(settings)
    try:
        results = await refresh_all(
            session_factory=get_session_factory(),
            adapters=adapters,
            settings=settings,
        )
    finally:
        # Always release HTTP clients even when refresh raised.
        for a in adapters.values():
            with contextlib.suppress(Exception):
                await a.aclose()

    err_count = sum(1 for snap in results.values() if snap.error is not None)
    refreshed_count = len(results) - err_count

    # After refresh — read-back the full list (including manual-only
    # providers) so the caller sees a complete admin snapshot.
    views = await get_all(db)
    return RefreshResponse(
        items=[_view_to_item(v) for v in views],
        refreshed_count=refreshed_count,
        error_count=err_count,
    )


@router.post(
    "/admin/provider_balances/{provider}/manual",
    response_model=ProviderBalanceItem,
)
async def set_manual_balance(
    provider: str,
    body: ManualBalanceRequest,
    _: Annotated[SessionPrincipal, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ProviderBalanceItem:
    if provider not in KNOWN_PROVIDERS:
        raise invalid_request(
            f"Unknown provider {provider!r}. Allowed: {sorted(KNOWN_PROVIDERS)}",
            param="provider",
            code="unknown_provider",
        )

    settings = get_settings()
    try:
        view = await set_manual(
            db,
            provider=provider,
            balance_native=body.balance_native,
            currency=body.currency,
            notes=body.notes,
            settings=settings,
        )
    except (ValueError, InvalidOperation) as exc:
        # ValueError raised by ``set_manual`` for invalid currency / negative.
        raise invalid_request(str(exc), code="invalid_balance") from exc
    except Exception as exc:  # pragma: no cover — defensive
        await db.rollback()
        log.exception("set_manual_balance_failed", provider=provider)
        raise GatewayError(
            status_code=500,
            message="Failed to record manual balance.",
            type="api_error",
            code="internal_error",
        ) from exc

    await db.commit()
    return _view_to_item(view)


__all__ = ["router"]
