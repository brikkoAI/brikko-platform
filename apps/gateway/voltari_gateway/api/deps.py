"""FastAPI dependencies that need a single shared instance per process.

Provider singletons live on ``app.state.provider_registry`` (built in
``main.py`` lifespan). This module exposes a thin getter for handlers
and tests that need a registry reference; for the canonical OpenAI
adapter we keep a backwards-compatible getter so old test code that
pokes ``app.state.openai_provider`` directly keeps working.
"""

from __future__ import annotations

from fastapi import Request

from voltari_gateway.config import get_settings
from voltari_gateway.providers.base import Provider
from voltari_gateway.providers.openai_provider import OpenAIProvider
from voltari_gateway.providers.registry import ProviderRegistry
from voltari_gateway.router.catalog import Provider as ProviderEnum


def get_provider_registry(request: Request) -> ProviderRegistry:
    """Return the per-app singleton ProviderRegistry, creating an empty one on demand."""
    state = request.app.state
    registry = getattr(state, "provider_registry", None)
    if registry is None:
        registry = ProviderRegistry()
        state.provider_registry = registry
    return registry


def get_openai_provider(request: Request) -> Provider:
    """Backwards-compatible OpenAI provider getter (used by older tests)."""
    state = request.app.state
    provider = getattr(state, "openai_provider", None)
    if provider is None:
        registry = get_provider_registry(request)
        provider = registry.get(ProviderEnum.OPENAI)
        if provider is None:
            settings = get_settings()
            provider = OpenAIProvider(
                api_key=settings.openai_api_key.get_secret_value(),
                base_url=settings.openai_base_url,
                timeout_seconds=settings.openai_timeout_seconds,
                outbound_proxy=settings.outbound_http_proxy,
            )
            registry.register(ProviderEnum.OPENAI, provider)
        state.openai_provider = provider
    return provider
