"""Manual balance adapter — placeholder for providers без public API.

Поддерживает OpenAI, Anthropic, Together, MiniMax, Zhipu, Yandex, Google.
В фазе 1 эти провайдеры обновляются вручную через POST endpoint:

    POST /v1/account/admin/provider_balances/openai/manual
    {"balance_native": "20.50", "currency": "USD", "notes": "топап 10.05"}

В фазе 2 (Playwright) для OpenAI/Anthropic появится автоматический scrape;
``ManualAdapter`` останется для оставшихся.

Адаптер не делает HTTP-вызовов: ``fetch()`` возвращает ``BalanceSnapshot``
с ``balance_native=None`` и ``error="manual_only"``. ``service.refresh_all()``
проверяет ``adapter.is_remote`` и не вызывает ``fetch()`` для manual-адаптеров.
"""

from __future__ import annotations

from voltari_gateway.balance_monitor.adapters.base import (
    BalanceAdapter,
    BalanceSnapshot,
)


class ManualAdapter(BalanceAdapter):
    """No-op adapter для провайдеров без публичного balance API."""

    is_remote = False

    def __init__(self, provider_name: str) -> None:
        if not provider_name:
            raise ValueError("provider_name required")
        # Cannot assign to class attribute ``provider_name``: shadow on instance.
        self.provider_name = provider_name

    async def fetch(self) -> BalanceSnapshot:  # pragma: no cover — never called
        return BalanceSnapshot(
            provider=self.provider_name,
            balance_native=None,
            currency=None,
            fetch_method="manual",
            raw=None,
            error="manual_only",
        )
