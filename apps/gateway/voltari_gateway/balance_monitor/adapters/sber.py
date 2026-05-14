"""Sber GigaChat balance adapter.

Public docs (по состоянию на 2026-05-10):

    GET https://gigachat.devices.sberbank.ru/api/v1/balance
    Authorization: Bearer <access_token>

Response (массив остатков по scope):

    [
      {"usage": "GigaChat-Pro",  "value": 1000000},
      {"usage": "GigaChat-Lite", "value": 500000},
      ...
    ]

``value`` — остаток токенов (не валюта). Sber не выдаёт RUB-баланс через
API (только в личном кабинете). CEO 2026-05-10: храним native = SUM(value),
currency = "tokens", balance_rub_kopecks = NULL — токены не пересчитываются
в рубли (слишком сложно: разная цена per-scope, не критично для
runway-индикатора).

OAuth flow:
* Берём access_token через ``_SberOAuthCache`` (re-use уже существующего
  кэша из ``providers.sber_provider``). Token cache shared между chat-
  и balance-call'ами — exhauster того же scope, не плодим лишних запросов.
* Если у нас уже crafted SberProvider в registry — переиспользуем его OAuth.
  Если нет — создаём собственный кэш.

Error handling: same shape as DeepSeekBalanceAdapter. Auth-failure /
network / parse — всё в snapshot.error.
"""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

import httpx

from voltari_gateway.balance_monitor.adapters.base import (
    BalanceAdapter,
    BalanceSnapshot,
)
from voltari_gateway.providers.sber_provider import _SberOAuthCache
from voltari_gateway.utils.logging import get_logger

log = get_logger(__name__)

SBER_BALANCE_URL = "https://gigachat.devices.sberbank.ru/api/v1/balance"


class SberBalanceAdapter(BalanceAdapter):
    provider_name = "sber"
    is_remote = True

    def __init__(
        self,
        *,
        auth_key: str,
        scope: str = "GIGACHAT_API_CORP",
        timeout_seconds: float = 10.0,
        oauth_cache: _SberOAuthCache | None = None,
    ) -> None:
        if not auth_key:
            raise ValueError("Sber auth_key required")
        self._oauth = oauth_cache or _SberOAuthCache(auth_key, scope)
        # Sber из РФ доступен напрямую — НЕ проксируем (см. SberProvider).
        # ``trust_env=False`` блокирует HTTPS_PROXY env-vars от других providers.
        self._http = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout_seconds, connect=5.0),
            trust_env=False,
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def fetch(self) -> BalanceSnapshot:
        # 1) OAuth token (cached). Failure → returned as snapshot.error.
        try:
            token = await self._oauth.get(self._http)
        except Exception as exc:  # ProviderAuthError, ProviderTimeoutError, …
            return BalanceSnapshot.error_for(self.provider_name, f"oauth: {exc}")

        # 2) Balance fetch.
        try:
            resp = await self._http.get(
                SBER_BALANCE_URL,
                headers={
                    "Authorization": f"Bearer {token}",
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
            data: Any = resp.json()
        except (json.JSONDecodeError, ValueError) as exc:
            return BalanceSnapshot.error_for(self.provider_name, f"json_decode: {exc}")

        # Sber отдаёт top-level массив. Если кто-то обернул в {"balance": [...]}
        # (наблюдалось в их тестовом окружении), достаём аккуратно.
        rows: list[Any]
        if isinstance(data, list):
            rows = data
        elif isinstance(data, dict) and isinstance(data.get("balance"), list):
            rows = data["balance"]
        else:
            return BalanceSnapshot(
                provider=self.provider_name,
                balance_native=None,
                currency="tokens",
                fetch_method="api",
                raw=data if isinstance(data, dict) else {"raw": data},
                error="unexpected_response_shape",
            )

        total = Decimal(0)
        for row in rows:
            if not isinstance(row, dict):
                continue
            value = row.get("value")
            if value is None:
                continue
            try:
                total += Decimal(str(value))
            except Exception:
                # Skip malformed row, do not fail entire fetch.
                continue

        # raw_response должно быть JSONB-friendly dict. Оборачиваем массив.
        raw_for_storage: dict[str, Any] = {"balance": rows} if isinstance(data, list) else data

        return BalanceSnapshot(
            provider=self.provider_name,
            balance_native=total,
            currency="tokens",
            fetch_method="api",
            raw=raw_for_storage,
            error=None,
        )
