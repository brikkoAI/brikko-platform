"""Management API — per-account routing preferences (Sprint 7, spec v1.5/22).

Mounted at ``/v1/account/routing-preferences``. Three endpoints:

    GET    /v1/account/routing-preferences      — current settings + preview
    PUT    /v1/account/routing-preferences      — full replace
    PATCH  /v1/account/routing-preferences      — partial update

All three require an authenticated cookie session via ``require_session``.

The response body's ``preview`` block surfaces the side effects of the
current filter — how many models are still active, the average price,
and a list of warnings (single-provider failover, custom-strategy with
empty allowed_models, etc.). Frontend uses this to render the
real-time preview pane in Settings → Smart Routing.

Public-facing strategy strings differ from the existing internal
``Strategy`` enum (``ru_legal`` here vs ``ru-legal`` there). We
translate at this boundary; downstream router code stays unchanged.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any, Final, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from voltari_gateway.auth.audit import write_audit
from voltari_gateway.auth.session_middleware import (
    SessionPrincipal,
    require_session,
)
from voltari_gateway.db.models import Account
from voltari_gateway.db.session import get_db
from voltari_gateway.router.catalog import CATALOG, ModelSpec, Provider
from voltari_gateway.router.strategies import (
    STRATEGIES,
    Strategy,
    TaskCategory,
)
from voltari_gateway.utils.errors import invalid_request

router = APIRouter(prefix="/v1/account", tags=["account"])


# ---------------------------------------------------------------------------
# Public enums (mirrors of internal Strategy / catalog Provider but with
# the underscore-form ``ru_legal`` the spec uses).
# ---------------------------------------------------------------------------

RoutingMode = Literal["manual", "smart"]
RoutingStrategy = Literal["cheap", "smart", "fast", "ru_legal", "custom"]

_VALID_STRATEGIES: Final[set[str]] = {"cheap", "smart", "fast", "ru_legal", "custom"}
_VALID_PROVIDERS: Final[set[str]] = {p.value for p in Provider}
_VALID_MODEL_IDS: Final[set[str]] = {m.id for m in CATALOG}

# Hard cap on filter list sizes — prevents JSONB bloat / slow scans
# (R4 in the spec). 50 covers any realistic real-world filter.
_MAX_ALLOWED_MODELS: Final[int] = 50


def _strategy_public_to_internal(s: str) -> Strategy:
    """Map the public ``ru_legal`` string to the internal ``ru-legal`` Strategy.

    Used at the boundary between the API and the router engine.
    """
    if s == "ru_legal":
        return Strategy.RU_LEGAL
    if s == "custom":
        # Custom is handled at the router level — for previewing we use
        # "smart" semantics on the filtered catalogue.
        return Strategy.SMART
    return Strategy(s)


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------


class _BasePreferences(BaseModel):
    """Common shape for PUT / PATCH bodies. PATCH allows None, PUT requires."""

    routing_mode: RoutingMode | None = None
    routing_strategy: RoutingStrategy | None = None
    allowed_providers: list[str] | None = None
    allowed_models: list[str] | None = None

    @field_validator("allowed_providers")
    @classmethod
    def _check_providers(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return v
        for p in v:
            if p not in _VALID_PROVIDERS:
                raise ValueError(f"unknown provider {p!r}; valid: {sorted(_VALID_PROVIDERS)}")
        # De-dupe but preserve user's order for stable diff/audit.
        seen: set[str] = set()
        out: list[str] = []
        for p in v:
            if p not in seen:
                seen.add(p)
                out.append(p)
        return out

    @field_validator("allowed_models")
    @classmethod
    def _check_models(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return v
        if len(v) > _MAX_ALLOWED_MODELS:
            raise ValueError(f"allowed_models has {len(v)} entries; max {_MAX_ALLOWED_MODELS}")
        for m in v:
            if m not in _VALID_MODEL_IDS:
                raise ValueError(f"unknown model id {m!r}")
        seen: set[str] = set()
        out: list[str] = []
        for m in v:
            if m not in seen:
                seen.add(m)
                out.append(m)
        return out


class PutRoutingPreferences(_BasePreferences):
    """Body for PUT — full replace. ``routing_mode`` is required."""

    routing_mode: RoutingMode = Field(...)


class PatchRoutingPreferences(_BasePreferences):
    """Body for PATCH — every field optional."""


class RoutingPreferencesPreview(BaseModel):
    active_models: list[str]
    active_models_total: int
    estimated_avg_cost_kop_per_1m_tokens: int
    warnings: list[str]


class AvailableProvider(BaseModel):
    id: str
    label: str
    model_count: int


class RoutingPreferencesResponse(BaseModel):
    routing_mode: RoutingMode
    routing_strategy: RoutingStrategy
    allowed_providers: list[str] | None
    allowed_models: list[str] | None
    preview: RoutingPreferencesPreview
    available_providers: list[AvailableProvider]
    updated_at: datetime | None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_PROVIDER_LABELS: Final[dict[str, str]] = {
    "openai": "OpenAI",
    "anthropic": "Anthropic",
    "google": "Google",
    "deepseek": "DeepSeek",
    "yandex": "Yandex",
    "sber": "GigaChat",
}


def _available_providers() -> list[AvailableProvider]:
    counts: dict[str, int] = dict.fromkeys(_VALID_PROVIDERS, 0)
    for m in CATALOG:
        counts[m.provider.value] += 1
    return [
        AvailableProvider(id=p, label=_PROVIDER_LABELS.get(p, p), model_count=counts[p])
        for p in sorted(_VALID_PROVIDERS)
    ]


def _filter_catalog(
    *,
    allowed_providers: list[str] | None,
    allowed_models: list[str] | None,
) -> list[ModelSpec]:
    """Return models that survive the user's whitelist."""
    out: list[ModelSpec] = []
    for m in CATALOG:
        if allowed_providers is not None and m.provider.value not in allowed_providers:
            continue
        if allowed_models is not None and m.id not in allowed_models:
            continue
        out.append(m)
    return out


def _build_preview(
    *,
    routing_mode: str,
    routing_strategy: str,
    allowed_providers: list[str] | None,
    allowed_models: list[str] | None,
) -> RoutingPreferencesPreview:
    """Compute the preview block surfaced by GET / PUT / PATCH."""
    warnings: list[str] = []

    if routing_mode == "manual":
        # In manual mode the smart router is dormant — preview is moot.
        return RoutingPreferencesPreview(
            active_models=[],
            active_models_total=0,
            estimated_avg_cost_kop_per_1m_tokens=0,
            warnings=["Manual mode active — set the model on every request."],
        )

    # Run a CHAT-category smart selection over the filter to produce the
    # active-models preview. CHAT is the most common category — what the
    # user sees here is what they'll get for typical traffic.
    strategy = _strategy_public_to_internal(routing_strategy)
    select = STRATEGIES[strategy]
    filtered = _filter_catalog(
        allowed_providers=allowed_providers,
        allowed_models=allowed_models,
    )
    active = select(
        TaskCategory.CHAT,
        ctx_size=2_000,
        require_ru_legal=(routing_strategy == "ru_legal"),
        catalog=tuple(filtered),
    )

    if not active:
        warnings.append(
            "No model matches your filter for chat traffic — check the "
            "providers and strategy combination."
        )

    if allowed_providers is not None and len(allowed_providers) == 1:
        warnings.append(
            "Failover is limited — with one provider, a single outage will "
            "surface as a 503 to your callers."
        )

    if routing_strategy == "custom" and not allowed_providers:
        warnings.append("Custom strategy requires at least one allowed provider.")

    # Cost estimate — mean weighted price over top-5 cheapest active
    # models (per spec §3.4 (a) for MVP).
    top_n = sorted(
        active, key=lambda m: 0.7 * m.input_price_kop_per_1k + 0.3 * m.output_price_kop_per_1k
    )[:5]
    if top_n:
        avg = sum(
            0.7 * m.input_price_kop_per_1k + 0.3 * m.output_price_kop_per_1k for m in top_n
        ) / len(top_n)
        # Convert kopecks-per-1k → kopecks-per-1M.
        cost_kop_per_1m = int(avg * 1000)
    else:
        cost_kop_per_1m = 0

    return RoutingPreferencesPreview(
        active_models=[m.id for m in active[:5]],
        active_models_total=len(active),
        estimated_avg_cost_kop_per_1m_tokens=cost_kop_per_1m,
        warnings=warnings,
    )


def _to_response(account: Account) -> RoutingPreferencesResponse:
    # ``cast`` rather than type:ignore — the column type is plain ``str``,
    # but Pydantic wants the Literal alias. We trust the DB-level CHECK
    # plus the application validators above to keep these in range.
    from typing import cast

    return RoutingPreferencesResponse(
        routing_mode=cast(RoutingMode, account.routing_mode),
        routing_strategy=cast(RoutingStrategy, account.routing_strategy),
        allowed_providers=account.routing_allowed_providers,
        allowed_models=account.routing_allowed_models,
        preview=_build_preview(
            routing_mode=account.routing_mode,
            routing_strategy=account.routing_strategy,
            allowed_providers=account.routing_allowed_providers,
            allowed_models=account.routing_allowed_models,
        ),
        available_providers=_available_providers(),
        updated_at=account.routing_updated_at,
    )


def _validate_business_rules(
    *,
    routing_mode: str,
    routing_strategy: str,
    allowed_providers: list[str] | None,
    allowed_models: list[str] | None,
) -> None:
    """Cross-field validation that doesn't fit in Pydantic field validators."""
    if routing_strategy not in _VALID_STRATEGIES:
        raise invalid_request(
            f"unknown routing_strategy {routing_strategy!r}",
            code="invalid_routing_strategy",
        )

    if routing_strategy == "custom":
        if not allowed_providers:
            raise invalid_request(
                "Custom strategy requires at least one allowed provider.",
                code="custom_requires_providers",
                param="allowed_providers",
            )
        # If allowed_models is set, every entry must come from one of the
        # allowed providers.
        if allowed_models:
            allowed_provider_set = set(allowed_providers)
            for mid in allowed_models:
                m = next((c for c in CATALOG if c.id == mid), None)
                if m is None:
                    # Caught earlier by Pydantic, but defensive.
                    raise invalid_request(
                        f"unknown model id {mid!r}",
                        code="unknown_model",
                        param="allowed_models",
                    )
                if m.provider.value not in allowed_provider_set:
                    raise invalid_request(
                        f"Model {mid!r} (provider {m.provider.value!r}) "
                        f"is not in allowed_providers.",
                        code="model_not_in_allowed_providers",
                        param="allowed_models",
                    )
    else:
        # Non-custom strategies must not carry whitelists — they would be
        # silently ignored by the router and the user would never know.
        if allowed_providers is not None or allowed_models is not None:
            raise invalid_request(
                "allowed_providers / allowed_models are only valid with routing_strategy='custom'.",
                code="filter_requires_custom",
            )


def _diff(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    """Return ``{field: {"before": ..., "after": ...}}`` for changed keys."""
    out: dict[str, Any] = {}
    for k in set(before) | set(after):
        if before.get(k) != after.get(k):
            out[k] = {"before": before.get(k), "after": after.get(k)}
    return out


def _snapshot(account: Account) -> dict[str, Any]:
    return {
        "routing_mode": account.routing_mode,
        "routing_strategy": account.routing_strategy,
        "allowed_providers": account.routing_allowed_providers,
        "allowed_models": account.routing_allowed_models,
    }


# ---------------------------------------------------------------------------
# 1) GET
# ---------------------------------------------------------------------------


@router.get(
    "/routing-preferences",
    response_model=RoutingPreferencesResponse,
    summary="Return per-account routing preferences with preview",
)
async def get_routing_preferences(
    principal: Annotated[SessionPrincipal, Depends(require_session)],
) -> RoutingPreferencesResponse:
    return _to_response(principal.account)


# ---------------------------------------------------------------------------
# 2) PUT — full replace
# ---------------------------------------------------------------------------


@router.put(
    "/routing-preferences",
    response_model=RoutingPreferencesResponse,
    summary="Replace per-account routing preferences",
)
async def put_routing_preferences(
    payload: PutRoutingPreferences,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    principal: Annotated[SessionPrincipal, Depends(require_session)],
) -> RoutingPreferencesResponse:
    account = principal.account
    user = principal.user

    # PUT defaults missing optional fields. routing_strategy is required
    # whenever mode is smart; in manual mode it's effectively ignored.
    new_mode = payload.routing_mode
    new_strategy = payload.routing_strategy or "smart"
    new_providers = payload.allowed_providers
    new_models = payload.allowed_models

    if new_mode == "manual":
        # Strategy / whitelists are dormant in manual mode. Reset to
        # defaults so subsequent toggles don't leak stale custom config.
        new_strategy = "smart"
        new_providers = None
        new_models = None

    _validate_business_rules(
        routing_mode=new_mode,
        routing_strategy=new_strategy,
        allowed_providers=new_providers,
        allowed_models=new_models,
    )

    before = _snapshot(account)
    account.routing_mode = new_mode
    account.routing_strategy = new_strategy
    account.routing_allowed_providers = new_providers
    account.routing_allowed_models = new_models
    account.routing_updated_at = datetime.now(UTC)
    after = _snapshot(account)

    diff = _diff(before, after)
    if diff:
        await write_audit(
            db,
            user_id=user.id,
            account_id=account.id,
            action="routing_preferences_updated",
            request=request,
            meta={"diff": diff},
        )
    await db.commit()
    await db.refresh(account)
    return _to_response(account)


# ---------------------------------------------------------------------------
# 3) PATCH — partial update
# ---------------------------------------------------------------------------


@router.patch(
    "/routing-preferences",
    response_model=RoutingPreferencesResponse,
    summary="Partially update per-account routing preferences",
)
async def patch_routing_preferences(
    payload: PatchRoutingPreferences,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    principal: Annotated[SessionPrincipal, Depends(require_session)],
) -> RoutingPreferencesResponse:
    account = principal.account
    user = principal.user

    fields = payload.model_fields_set
    if not fields:
        return _to_response(account)

    # PATCH semantics: a missing field keeps the current value; an
    # explicit None on a non-nullable field (mode/strategy) is treated
    # as «keep current» rather than «clear» — the API doesn't expose
    # a way to clear those.
    new_mode_in: str | None = (
        payload.routing_mode if "routing_mode" in fields else account.routing_mode
    )
    new_mode: str = new_mode_in if new_mode_in is not None else account.routing_mode

    new_strategy_in: str | None = (
        payload.routing_strategy if "routing_strategy" in fields else account.routing_strategy
    )
    new_strategy: str = new_strategy_in if new_strategy_in is not None else account.routing_strategy

    new_providers: list[str] | None = (
        payload.allowed_providers
        if "allowed_providers" in fields
        else account.routing_allowed_providers
    )
    new_models: list[str] | None = (
        payload.allowed_models if "allowed_models" in fields else account.routing_allowed_models
    )

    if new_mode == "manual":
        new_strategy = "smart"
        new_providers = None
        new_models = None

    _validate_business_rules(
        routing_mode=new_mode,
        routing_strategy=new_strategy,
        allowed_providers=new_providers,
        allowed_models=new_models,
    )

    before = _snapshot(account)
    account.routing_mode = new_mode
    account.routing_strategy = new_strategy
    account.routing_allowed_providers = new_providers
    account.routing_allowed_models = new_models
    account.routing_updated_at = datetime.now(UTC)
    after = _snapshot(account)

    diff = _diff(before, after)
    if diff:
        await write_audit(
            db,
            user_id=user.id,
            account_id=account.id,
            action="routing_preferences_updated",
            request=request,
            meta={"diff": diff},
        )
    await db.commit()
    await db.refresh(account)
    return _to_response(account)


__all__ = ["router"]
