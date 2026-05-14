"""Tests for ``voltari_gateway.api.deps``.

The module wires shared per-process singletons via FastAPI dependencies.
Lifts ``api/deps.py`` from 0% → 100%.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from voltari_gateway.api.deps import get_openai_provider, get_provider_registry
from voltari_gateway.providers.registry import ProviderRegistry
from voltari_gateway.router.catalog import Provider as ProviderEnum


def _fake_request_with_state() -> tuple[MagicMock, SimpleNamespace]:
    state = SimpleNamespace()
    app = SimpleNamespace(state=state)
    request = MagicMock()
    request.app = app
    return request, state


def test_get_provider_registry_creates_when_missing():
    request, state = _fake_request_with_state()
    reg = get_provider_registry(request)
    assert isinstance(reg, ProviderRegistry)
    # Idempotent — second call returns same instance.
    assert get_provider_registry(request) is reg


def test_get_provider_registry_returns_existing():
    request, state = _fake_request_with_state()
    pre = ProviderRegistry()
    state.provider_registry = pre
    assert get_provider_registry(request) is pre


def test_get_openai_provider_returns_existing_state_attribute():
    request, state = _fake_request_with_state()
    sentinel = object()
    state.openai_provider = sentinel
    assert get_openai_provider(request) is sentinel


def test_get_openai_provider_resolves_via_registry_when_present():
    """If the registry already has an OpenAI provider, deps reuses it."""
    request, state = _fake_request_with_state()

    class _Stub:
        async def chat_completion(self, req): ...  # pragma: no cover
        async def chat_completion_stream(self, req): ...  # pragma: no cover
        async def aclose(self): ...  # pragma: no cover

    stub = _Stub()
    reg = ProviderRegistry()
    reg.register(ProviderEnum.OPENAI, stub)  # type: ignore[arg-type]
    state.provider_registry = reg

    out = get_openai_provider(request)
    assert out is stub
    # And cached on state for next call.
    assert state.openai_provider is stub


def test_get_openai_provider_creates_new_when_neither_state_nor_registry_has_one():
    """Fallback path: instantiate OpenAIProvider from settings."""
    request, state = _fake_request_with_state()

    with patch("voltari_gateway.api.deps.OpenAIProvider") as mock_cls:
        mock_instance = MagicMock()
        mock_cls.return_value = mock_instance
        out = get_openai_provider(request)

    assert out is mock_instance
    mock_cls.assert_called_once()
    # Cached on state for next call.
    assert state.openai_provider is mock_instance
    # And registered in the lazily-built registry.
    assert state.provider_registry.get(ProviderEnum.OPENAI) is mock_instance
