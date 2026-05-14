"""Base adapter contract for provider balance fetchers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from decimal import Decimal
from typing import Any


@dataclass(frozen=True)
class BalanceSnapshot:
    """Result of one ``BalanceAdapter.fetch()`` call.

    Attributes:
        provider:  Stable provider key, e.g. ``"deepseek"``. Matches the
                   ``provider`` column in ``provider_balances``.
        balance_native:  Balance in upstream currency. None if the adapter
                         could not parse / received non-numeric (e.g. the
                         provider returned an error envelope).
        currency:  ISO-like currency code as the provider reports it
                   (``USD``, ``EUR``, ``CNY``, ``RUB``) или ``"tokens"``
                   когда провайдер выдаёт остаток в токенах (Sber).
        fetch_method:  Always ``"api"`` for adapters that hit a public
                       endpoint. ``ManualAdapter`` overrides to ``"manual"``.
        raw:  Full provider response (parsed JSON). Stored verbatim in
              ``provider_balances.raw_response`` for debugging.
        error:  Non-None when ``fetch()`` could not produce a usable balance.
                In that case ``balance_native`` MUST be None and the
                service layer writes ``fetch_status='error'`` + ``error_message``.
    """

    provider: str
    balance_native: Decimal | None
    currency: str | None
    fetch_method: str = "api"
    raw: dict[str, Any] | None = None
    error: str | None = None

    @classmethod
    def error_for(
        cls, provider: str, message: str, *, fetch_method: str = "api"
    ) -> BalanceSnapshot:
        """Convenience factory for failed fetches."""
        return cls(
            provider=provider,
            balance_native=None,
            currency=None,
            fetch_method=fetch_method,
            raw=None,
            error=message,
        )


class BalanceAdapter(ABC):
    """Abstract base class for one upstream provider balance fetcher.

    Subclasses MUST set ``provider_name`` to a stable key (matches the
    ``provider`` column in ``provider_balances`` / used by routing /
    used by /v1/account/admin/status).

    Lifetime: один экземпляр на процесс. ``fetch()`` is async and must
    be safe to call concurrently with itself (the service layer fans out
    via ``asyncio.gather``). Adapters owning an ``httpx.AsyncClient`` —
    pooling уже concurrency-safe.
    """

    #: Stable provider key. Subclasses MUST override.
    provider_name: str = ""

    #: Whether this adapter actually hits an upstream API. ``ManualAdapter``
    #: overrides to False so ``service.refresh_all()`` skips it.
    is_remote: bool = True

    @abstractmethod
    async def fetch(self) -> BalanceSnapshot:
        """Fetch the current balance. Must not raise — return error in the snapshot."""
        raise NotImplementedError

    async def aclose(self) -> None:
        """Release any resources (e.g. ``httpx.AsyncClient``).

        Default no-op for adapters that don't own resources (manual, etc).
        """
        return None
