"""Tests for ``voltari_gateway.providers.registry``.

Pin the registry contracts:

* ``register`` is idempotent (a second call replaces).
* ``unregister`` is no-op when the key isn't there.
* ``get_for_model`` raises ``LookupError`` for unconfigured providers.
* ``configured_providers`` reflects current state.
* ``aclose`` calls ``aclose`` on each registered provider and empties the map.

Bumps ``providers/registry.py`` 76% → ~100%.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from voltari_gateway.providers.base import Provider
from voltari_gateway.providers.registry import ProviderRegistry
from voltari_gateway.router.catalog import ModelSpec, ModelTier
from voltari_gateway.router.catalog import Provider as ProviderEnum


class _StubProvider(Provider):
    """Minimal provider that records aclose calls."""

    name = "stub"

    def __init__(self) -> None:
        self.closed = False

    async def chat_completion(self, req):  # pragma: no cover — not exercised here
        raise NotImplementedError

    async def chat_completion_stream(self, req):  # pragma: no cover
        raise NotImplementedError

    async def aclose(self) -> None:
        self.closed = True


def _model(provider: ProviderEnum) -> ModelSpec:
    return ModelSpec(
        id=f"{provider.value}-test",
        provider=provider,
        tier=ModelTier.MID,
        input_price_kop_per_1k=10,
        cached_price_kop_per_1k=1,
        output_price_kop_per_1k=20,
        context_window=4096,
        latency_p50_ms=500,
        quality_score=70,
        supports_streaming=True,
        supports_tools=False,
        ru_legal=False,
    )


def test_get_returns_none_for_missing_provider():
    reg = ProviderRegistry()
    assert reg.get(ProviderEnum.OPENAI) is None


def test_register_then_get_returns_same_instance():
    reg = ProviderRegistry()
    p = _StubProvider()
    reg.register(ProviderEnum.OPENAI, p)
    assert reg.get(ProviderEnum.OPENAI) is p


def test_register_is_idempotent_replaces_existing():
    reg = ProviderRegistry()
    p1 = _StubProvider()
    p2 = _StubProvider()
    reg.register(ProviderEnum.OPENAI, p1)
    reg.register(ProviderEnum.OPENAI, p2)
    assert reg.get(ProviderEnum.OPENAI) is p2


def test_unregister_missing_key_is_noop():
    reg = ProviderRegistry()
    reg.unregister(ProviderEnum.OPENAI)  # nothing to remove — no exception


def test_unregister_existing_key_removes():
    reg = ProviderRegistry()
    reg.register(ProviderEnum.OPENAI, _StubProvider())
    reg.unregister(ProviderEnum.OPENAI)
    assert reg.get(ProviderEnum.OPENAI) is None


def test_get_for_model_raises_lookup_error_when_unconfigured():
    reg = ProviderRegistry()
    with pytest.raises(LookupError, match="provider_not_configured"):
        reg.get_for_model(_model(ProviderEnum.ANTHROPIC))


def test_get_for_model_returns_provider_when_configured():
    reg = ProviderRegistry()
    p = _StubProvider()
    reg.register(ProviderEnum.OPENAI, p)
    assert reg.get_for_model(_model(ProviderEnum.OPENAI)) is p


def test_configured_providers_returns_current_keys():
    reg = ProviderRegistry()
    assert reg.configured_providers() == frozenset()
    reg.register(ProviderEnum.OPENAI, _StubProvider())
    reg.register(ProviderEnum.ANTHROPIC, _StubProvider())
    assert reg.configured_providers() == frozenset({ProviderEnum.OPENAI, ProviderEnum.ANTHROPIC})


@pytest.mark.asyncio
async def test_aclose_closes_each_provider_and_empties():
    reg = ProviderRegistry()
    a = _StubProvider()
    b = _StubProvider()
    reg.register(ProviderEnum.OPENAI, a)
    reg.register(ProviderEnum.ANTHROPIC, b)
    await reg.aclose()
    assert a.closed is True
    assert b.closed is True
    assert reg.configured_providers() == frozenset()


@pytest.mark.asyncio
async def test_aclose_swallows_provider_errors():
    """If one provider fails to close, the others must still be closed."""
    reg = ProviderRegistry()
    crash = _StubProvider()
    crash.aclose = AsyncMock(side_effect=RuntimeError("close blew up"))  # type: ignore[method-assign]
    quiet = _StubProvider()
    reg.register(ProviderEnum.OPENAI, crash)
    reg.register(ProviderEnum.ANTHROPIC, quiet)
    await reg.aclose()  # must not raise
    assert quiet.closed is True
    assert reg.configured_providers() == frozenset()
