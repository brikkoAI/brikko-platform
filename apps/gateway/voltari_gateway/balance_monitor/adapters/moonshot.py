"""Moonshot AI (Kimi) balance adapter.

Endpoint (наблюдаемый по состоянию на 2026-05-10):
    GET https://api.moonshot.ai/v1/users/me/balance
    Authorization: Bearer <MOONSHOT_API_KEY>

Note: точная форма ответа не задокументирована публично — Moonshot API
docs упоминают ``available_balance`` / ``voucher_balance`` / ``cash_balance``,
но фактическое поле может отличаться. Адаптер устойчив к нескольким
вариантам shape:

    {"data": {"available_balance": 50.0, "currency": "USD"}}     # docs
    {"available_balance": 50.0, "currency": "USD"}               # flat shape
    {"balance": "50.0", "currency": "USD"}                       # legacy

Если schema поменяется — последний raw_response уйдёт в БД и появится
в admin UI как «error: parse_failed», CEO увидит и обновит адаптер.
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

MOONSHOT_BALANCE_URL = "https://api.moonshot.ai/v1/users/me/balance"


def _extract_balance(data: dict[str, Any]) -> tuple[Decimal | None, str | None, str | None]:
    """Return (balance, currency, error) из любой из трёх известных форм.

    Returns:
        balance: Decimal или None.
        currency: строка ('USD'/'CNY'/'EUR'/...) или None.
        error: пояснение если parse не удался; None если всё ок.
    """
    # Shape 1: docs — {"data": {"available_balance": ..., "currency": ...}}
    inner: Any = data.get("data") if isinstance(data, dict) else None
    if isinstance(inner, dict):
        return _extract_from(inner)

    # Shape 2/3 — поля прямо в top-level.
    return _extract_from(data)


def _extract_from(obj: dict[str, Any]) -> tuple[Decimal | None, str | None, str | None]:
    raw_balance = obj.get("available_balance")
    if raw_balance is None:
        raw_balance = obj.get("balance")
    if raw_balance is None:
        raw_balance = obj.get("cash_balance")
    if raw_balance is None:
        return None, None, "balance_field_missing"

    currency = obj.get("currency")
    currency_str = str(currency).upper() if currency else "USD"

    try:
        return Decimal(str(raw_balance)), currency_str, None
    except (InvalidOperation, ValueError):
        return None, currency_str, f"invalid_balance_value: {raw_balance!r}"


class MoonshotBalanceAdapter(BalanceAdapter):
    provider_name = "moonshot"
    is_remote = True

    def __init__(
        self,
        *,
        api_key: str,
        timeout_seconds: float = 10.0,
        outbound_proxy: str | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("Moonshot api_key required")
        self._api_key = api_key
        kwargs: dict[str, Any] = {
            "timeout": httpx.Timeout(timeout_seconds, connect=5.0),
            "trust_env": False,
        }
        if outbound_proxy:
            # Moonshot — Chinese provider; CEO 2026-05-10: РФ-прямой доступ.
            # Параметр оставлен на случай on-prem-сетки.
            kwargs["proxy"] = outbound_proxy
        self._http = httpx.AsyncClient(**kwargs)

    async def aclose(self) -> None:
        await self._http.aclose()

    async def fetch(self) -> BalanceSnapshot:
        try:
            resp = await self._http.get(
                MOONSHOT_BALANCE_URL,
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
            data: Any = resp.json()
        except (json.JSONDecodeError, ValueError) as exc:
            return BalanceSnapshot.error_for(self.provider_name, f"json_decode: {exc}")

        if not isinstance(data, dict):
            return BalanceSnapshot(
                provider=self.provider_name,
                balance_native=None,
                currency=None,
                fetch_method="api",
                raw={"raw": data} if data is not None else None,
                error="non_dict_response",
            )

        balance, currency, parse_err = _extract_balance(data)
        return BalanceSnapshot(
            provider=self.provider_name,
            balance_native=balance,
            currency=currency,
            fetch_method="api",
            raw=data,
            error=parse_err,
        )
