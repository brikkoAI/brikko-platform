"""Provider registry — single instance per process per upstream.

The registry is constructed once at FastAPI startup (in ``main.py`` lifespan)
and torn down at shutdown. All API handlers receive the registry via the
FastAPI app state and resolve a concrete ``Provider`` from a ``ModelSpec``.

Why a registry vs. a factory function:

* Single httpx client per upstream → keep-alive + HTTP/2 across requests.
* A provider that's not configured (e.g. ``ANTHROPIC_API_KEY`` empty) is
  simply absent from the registry; the router pre-emptively excludes its
  models from candidate lists so we never fail mid-flight.
* Unit tests inject a stub registry without monkey-patching imports.
"""

from __future__ import annotations

from voltari_gateway.providers.base import Provider
from voltari_gateway.router.catalog import ModelSpec
from voltari_gateway.router.catalog import Provider as ProviderEnum
from voltari_gateway.utils.logging import get_logger

log = get_logger(__name__)


class ProviderRegistry:
    """Maps ``ProviderEnum`` → concrete ``Provider`` adapter.

    Thread-/coroutine-safe under the assumption that the dict is populated
    once at startup and only read afterwards (dict reads are atomic in
    CPython).
    """

    def __init__(self) -> None:
        self._providers: dict[ProviderEnum, Provider] = {}
        # Sprint 5 perf: cached frozenset of registered keys. ``configured_providers``
        # is called 3-4× per /v1/chat/completions request (chat.py route filter,
        # 503-check, _json_response, _stream_response). Each call previously
        # rebuilt a new frozenset from dict.keys() — cheap individually but
        # noisy in a tight aggregator. Cache and invalidate on mutation.
        self._configured_cache: frozenset[ProviderEnum] = frozenset()

    # ---- mutation (startup-time) ------------------------------------------

    def register(self, key: ProviderEnum, provider: Provider) -> None:
        """Add a provider. Idempotent — replaces any existing entry."""
        self._providers[key] = provider
        self._configured_cache = frozenset(self._providers.keys())
        log.info("provider_registered", provider=key.value)

    def unregister(self, key: ProviderEnum) -> None:
        self._providers.pop(key, None)
        self._configured_cache = frozenset(self._providers.keys())

    # ---- read (hot-path) --------------------------------------------------

    def get(self, key: ProviderEnum) -> Provider | None:
        return self._providers.get(key)

    def get_for_model(self, spec: ModelSpec) -> Provider:
        """Resolve the provider for a given ``ModelSpec``.

        Raises ``LookupError`` if no provider is configured. The router
        filters unconfigured providers out *before* we get here in normal
        operation, so this exception path means a misconfiguration —
        surface it as 503.
        """
        prov = self._providers.get(spec.provider)
        if prov is None:
            raise LookupError(
                f"provider_not_configured: model={spec.id} provider={spec.provider.value}"
            )
        return prov

    def configured_providers(self) -> frozenset[ProviderEnum]:
        """Return providers that are alive — used by the router to filter.

        Returns the cached frozenset; rebuilt on register/unregister only.
        """
        return self._configured_cache

    # ---- shutdown ---------------------------------------------------------

    async def aclose(self) -> None:
        for key, prov in list(self._providers.items()):
            try:
                await prov.aclose()
            except Exception as exc:  # pragma: no cover — defensive
                log.warning("provider_close_failed", provider=key.value, error=str(exc))
        self._providers.clear()
        self._configured_cache = frozenset()


__all__ = ["ProviderRegistry"]
