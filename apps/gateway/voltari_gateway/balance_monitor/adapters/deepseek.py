"""DeepSeek balance adapter.

Public docs: ``GET https://api.deepseek.com/user/balance``
Auth: ``Authorization: Bearer <DEEPSEEK_API_KEY>`` (same key as chat).

Response shape (observed 2026-05-10):

    {
      "is_available": true,
      "balance_infos": [
        {
          "currency": "USD",
          "total_balance": "5.23",
          "granted_balance": "0.00",
          "topped_up_balance": "5.23"
        }
      ]
    }

Когда у аккаунта несколько currencies — берём первую совпадающую с USD.
Если USD нет — берём первый ряд как-есть. CEO 2026-05-10: для DeepSeek
у нас всегда USD-аккаунт (CN-юзера платят CNY, мы — USD-prepaid через
UnionPay).

Errors:
* 401/403 → ProviderAuthError-equivalent (saved as fetch_status='error',
  error_message='auth: <body>').
* network/timeout → fetch_status='error', error_message='network: <details>'.
* parse failure (схема изменилась) → fetch_status='error', raw сохраняется.
"""

from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

from voltari_gateway.balance_monitor.adapters.base import (
    BalanceAdapter,
    BalanceSnapshot,
)
from voltari_gateway.utils.logging import get_logger

log = get_logger(__name__)

DEEPSEEK_BALANCE_URL = "https://api.deepseek.com/user/balance"


class DeepSeekBalanceAdapter(BalanceAdapter):
    provider_name = "deepseek"
    is_remote = True

    def __init__(
        self,
        *,
        api_key: str,
        timeout_seconds: float = 10.0,
        outbound_proxy: str | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("DeepSeek api_key required")
        self._api_key = api_key
        # Lightweight HTTP client. DeepSeek балансовый endpoint — РФ-доступен
        # напрямую (см. main.py: DeepSeek не проксируется), но разрешаем
        # переопределить proxy для on-prem-сценариев.
        kwargs: dict[str, Any] = {
            "timeout": httpx.Timeout(timeout_seconds, connect=5.0),
            "trust_env": False,
        }
        if outbound_proxy:
            kwargs["proxy"] = outbound_proxy
        self._http = httpx.AsyncClient(**kwargs)

    async def aclose(self) -> None:
        await self._http.aclose()

    async def fetch(self) -> BalanceSnapshot:
        try:
            resp = await self._http.get(
                DEEPSEEK_BALANCE_URL,
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Accept": "application/json",
                },
            )
        except httpx.TimeoutException as exc:
            return BalanceSnapshot.error_for(self.provider_name, f"timeout: {exc}")
        except httpx.HTTPError as exc:
            return BalanceSnapshot.error_for(self.provider_name, f"network: {exc}")

        if resp.status_code in (401, 403):
            return BalanceSnapshot.error_for(
                self.provider_name,
                f"auth_{resp.status_code}: {resp.text[:200]}",
            )
        if resp.status_code >= 400:
            return BalanceSnapshot.error_for(
                self.provider_name,
                f"http_{resp.status_code}: {resp.text[:200]}",
            )

        try:
            data: dict[str, Any] = resp.json()
        except (json.JSONDecodeError, ValueError) as exc:
            return BalanceSnapshot.error_for(self.provider_name, f"json_decode: {exc}")

        infos = data.get("balance_infos") or []
        if not isinstance(infos, list) or not infos:
            return BalanceSnapshot(
                provider=self.provider_name,
                balance_native=None,
                currency=None,
                fetch_method="api",
                raw=data,
                error="no_balance_infos",
            )

        # Prefer USD when present.
        chosen: dict[str, Any] | None = None
        for row in infos:
            if isinstance(row, dict) and str(row.get("currency", "")).upper() == "USD":
                chosen = row
                break
        if chosen is None and isinstance(infos[0], dict):
            chosen = infos[0]
        if chosen is None:
            return BalanceSnapshot(
                provider=self.provider_name,
                balance_native=None,
                currency=None,
                fetch_method="api",
                raw=data,
                error="malformed_balance_infos",
            )

        raw_balance = chosen.get("total_balance")
        currency = str(chosen.get("currency", "USD")).upper()
        try:
            balance = Decimal(str(raw_balance)) if raw_balance is not None else None
        except (InvalidOperation, ValueError):
            return BalanceSnapshot(
                provider=self.provider_name,
                balance_native=None,
                currency=currency,
                fetch_method="api",
                raw=data,
                error=f"invalid_total_balance: {raw_balance!r}",
            )

        return BalanceSnapshot(
            provider=self.provider_name,
            balance_native=balance,
            currency=currency,
            fetch_method="api",
            raw=data,
            error=None,
        )
