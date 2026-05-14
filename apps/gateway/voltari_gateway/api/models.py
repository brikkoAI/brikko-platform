"""GET /v1/models — static catalog of MVP models.

Per `02_Product/04_tech_stack.md` §4.3 we extend the OpenAI shape with our
``pricing`` block (kopecks per 1k tokens). Standard OpenAI clients ignore
unknown fields, so this stays compatible.

Sprint 10 — added ``GET /v1/models/public`` (no auth) for the marketing
landing page. Marketing-grade payload (RUB pricing with markup, display
names, capability flags). The authenticated ``/v1/models`` stays as-is for
OpenAI SDK compatibility.

Sprint M3.1 (2026-05-10) — both endpoints now filter by *registered* providers.
A provider is registered iff its API key is set at boot (see main.py lifespan).
Models whose provider has no key surface neither in the SDK ``/v1/models``
listing nor in the marketing public catalog. Concrete consequence: shipping
without ``TOGETHER_API_KEY`` (CEO has no foreign card) hides every Together
model from clients automatically — when the key is added in env, all 24+
Together entries reappear without a code change. The catalog data structure
itself is unchanged; the filter is applied at endpoint render time.

Pinned-model behaviour for an unregistered provider lives in api/chat.py
(returns 400 ``model_not_available`` instead of 503 — the model is "not in
your catalog right now", not "upstream is down").
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response

from voltari_gateway.auth.middleware import AuthPrincipal
from voltari_gateway.auth.oauth_dependency import require_api_key_or_oauth_scope
from voltari_gateway.auth.oauth_scopes import OAuthScope
from voltari_gateway.providers.registry import ProviderRegistry
from voltari_gateway.router.catalog import ModelSpec, get_model, list_models
from voltari_gateway.router.catalog import Provider as ProviderEnum
from voltari_gateway.utils.errors import model_not_found

router = APIRouter()


def _visible_models(registry: ProviderRegistry | None) -> list[ModelSpec]:
    """Filter the static catalog by providers that have credentials configured.

    Why filter here (vs. at catalog import): credentials are loaded at app
    boot and can change per deployment without a code change. Filtering at
    render time keeps a single source of truth (``CATALOG``) while letting
    each environment surface only what it can actually serve. If the registry
    isn't on app.state for any reason (defensive — shouldn't happen post-boot)
    we fall back to the full catalog so the endpoint never 500s on a config
    glitch.
    """
    models = list_models()
    if registry is None:
        return models
    configured = registry.configured_providers()
    return [m for m in models if m.provider in configured]


def _is_visible(spec: ModelSpec, registry: ProviderRegistry | None) -> bool:
    if registry is None:
        return True
    return spec.provider in registry.configured_providers()


@router.get(
    "/v1/models",
    tags=["chat"],
    summary="List available models (OpenAI-compatible)",
    description=(
        "OpenAI-shape ``{object: 'list', data: [...]}`` with the Voltari "
        "extension ``pricing`` (kopecks per 1k tokens, input/cached/output). "
        "Standard OpenAI clients ignore unknown fields, so the endpoint is "
        "drop-in compatible with the Python/JS SDKs. Only models whose "
        "upstream provider has an API key configured for this deployment "
        "are returned — clients never see entries we can't actually serve."
    ),
    responses={
        200: {"description": "Catalog of models the caller can route to."},
        401: {"description": "Bearer token missing or invalid."},
    },
)
async def list_models_endpoint(
    request: Request,
    _principal: AuthPrincipal = Depends(require_api_key_or_oauth_scope(OAuthScope.MODELS_READ)),
) -> dict[str, object]:
    registry: ProviderRegistry | None = getattr(request.app.state, "provider_registry", None)
    return {
        "object": "list",
        "data": [m.to_models_dict() for m in _visible_models(registry)],
    }


# Sprint 10 — Public catalog for the marketing landing page.
#
# Declared BEFORE the ``/v1/models/{model_id:path}`` route below so FastAPI
# matches "/v1/models/public" against this static handler instead of treating
# "public" as a model id (which would 404).
@router.get(
    "/v1/models/public",
    tags=["chat"],
    summary="Public model catalog for marketing pages (no auth)",
    description=(
        "Open endpoint serving the model catalog enriched for marketing UI: "
        "RUB-converted prices with Brikko markup, display names, capability "
        "flags, modality lists. Cached for 5 minutes (``Cache-Control: "
        "public, max-age=300``) — catalog changes are deploy-gated. "
        "Distinct from the authenticated ``/v1/models`` endpoint which "
        "preserves OpenAI SDK compatibility. Same provider-filter applies "
        "as ``/v1/models`` so the lander never advertises a model the "
        "deployment can't serve."
    ),
    responses={
        200: {"description": "Public catalog (always 200; never authed)."},
    },
)
async def list_models_public_endpoint(request: Request, response: Response) -> dict[str, object]:
    response.headers["Cache-Control"] = "public, max-age=300"
    # Hint to CDNs / browsers that this varies by Origin (CORS) but not by
    # auth/cookie — the response is identical for every caller.
    response.headers["Vary"] = "Origin"
    registry: ProviderRegistry | None = getattr(request.app.state, "provider_registry", None)
    return {
        "object": "list",
        "data": [m.to_public_dict() for m in _visible_models(registry)],
    }


@router.get(
    "/v1/models/{model_id:path}",
    tags=["chat"],
    summary="Get a single model by id",
    description=(
        "Returns the OpenAI-style model object for ``model_id`` (e.g. "
        "``gpt-5.4-mini``). 404 if the model isn't in the catalog *or* "
        "its upstream provider has no API key on this deployment."
    ),
    responses={
        200: {"description": "Model description."},
        401: {"description": "Bearer token missing or invalid."},
        404: {"description": "Unknown or unavailable model id."},
    },
)
async def get_model_endpoint(
    model_id: str,
    request: Request,
    _principal: AuthPrincipal = Depends(require_api_key_or_oauth_scope(OAuthScope.MODELS_READ)),
) -> dict[str, object]:
    model = get_model(model_id)
    registry: ProviderRegistry | None = getattr(request.app.state, "provider_registry", None)
    if model is None or not _is_visible(model, registry):
        # 404 either way — we deliberately don't leak "exists but unavailable"
        # vs. "doesn't exist". The client experience is identical: «эта модель
        # тебе сейчас недоступна, выбери другую». ProviderEnum reference kept
        # to silence unused-import warnings on lint configs that flag it.
        _ = ProviderEnum
        raise model_not_found(model_id)
    return model.to_models_dict()
