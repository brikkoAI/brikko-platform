"""MiniMax (Hailuo) balance adapter.

Endpoint (наблюдаемый по docs на 2026-05-12):
    GET https://api.minimaxi.chat/v1/billing/wallet/balance
    Authorization: Bearer <MINIMAX_API_KEY>

Note: точная форма ответа не зафиксирована публично (MiniMax docs покрывают
billing статусы, но не явный shape поля баланса). Шейпы, наблюдаемые в community
issues:

    {"balance": "12.34", "currency": "RMB"}                      # flat shape
    {"data": {"balance": 12.34, "currency": "USD"}}              # docs-style
    {"balance_info": {"available_balance": "...", "currency": ...}}  # wallet API

Если schema поменяется — raw_response уйдёт в БД и появится в admin UI как
«error: parse_failed». TODO: проверить актуальный shape после первого реального
запроса в проде (https://intl.minimaxi.com/document/Billing%20Documentation).

Валюта по умолчанию: международный аккаунт MiniMax платится в USD (UnionPay /
Visa); если CEO платит через локальный CN-канал — то RMB (CNY). Адаптер
читает ``currency`` из ответа и fallback'ит на ``RMB`` (FX через ``cny_to_rub``
в service layer).
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

# Public host = api.minimaxi.chat (note trailing ``i``) — see config.minimax_base_url.
# Hard-coded here (а не из settings) чтобы балансовый adapter был независим
# от runtime-конфига чат-провайдера. Если CEO решит поменять — обновляется
# здесь.
MINIMAX_BALANCE_URL = "https://api.minimaxi.chat/v1/billing/wallet/balance"


def _extract_balance(
    data: dict[str, Any],
) -> tuple[Decimal | None, str | None, str | None]:
    """Return ``(balance, currency, error)`` из любой из известных форм.

    Знаемые формы:
    * ``{"data": {"balance": ..., "currency": ...}}``  — docs-style wrap
    * ``{"balance_info": {...}}``                       — wallet endpoint
    * ``{"balance": ..., "currency": ...}``             — flat shape

    Returns:
        balance: Decimal или None.
        currency: строка ('USD'/'CNY'/'RMB'/...) или None.
        error: пояснение если parse не удался; None если всё ок.
    """
    # Shape 1: docs — {"data": {"balance": ..., "currency": ...}}
    inner: Any = data.get("data") if isinstance(data, dict) else None
    if isinstance(inner, dict):
        result = _extract_from(inner)
        if result[0] is not None:
            return result

    # Shape 2: wallet — {"balance_info": {...}}
    inner_wallet: Any = data.get("balance_info") if isinstance(data, dict) else None
    if isinstance(inner_wallet, dict):
        result = _extract_from(inner_wallet)
        if result[0] is not None:
            return result

    # Shape 3 — поля прямо в top-level.
    return _extract_from(data)


def _extract_from(obj: dict[str, Any]) -> tuple[Decimal | None, str | None, str | None]:
    raw_balance = obj.get("balance")
    if raw_balance is None:
        raw_balance = obj.get("available_balance")
    if raw_balance is None:
        raw_balance = obj.get("balance_amount")
    if raw_balance is None:
        return None, None, "balance_field_missing"

    currency = obj.get("currency") or obj.get("currency_code")
    currency_str = _normalize_currency(currency)

    try:
        return Decimal(str(raw_balance)), currency_str, None
    except (InvalidOperation, ValueError):
        return None, currency_str, f"invalid_balance_value: {raw_balance!r}"


def _normalize_currency(currency: Any) -> str:
    """Map MiniMax currency hints → ISO ``USD`` / ``CNY``.

    MiniMax docs/UI use ``RMB`` interchangeably with ``CNY``; our FX map
    in ``service.native_to_rub_kopecks`` only знает ``CNY``. Default to
    ``CNY`` when ничего не указано (international account is CN-based, RMB
    is the upstream native — USD-balances are explicitly labelled).
    """
    if not currency:
        return "CNY"
    cur = str(currency).upper().strip()
    if cur == "RMB":
        return "CNY"
    return cur


class MiniMaxBalanceAdapter(BalanceAdapter):
    provider_name = "minimax"
    is_remote = True

    def __init__(
        self,
        *,
        api_key: str,
        timeout_seconds: float = 10.0,
        outbound_proxy: str | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("MiniMax api_key required")
        self._api_key = api_key
        kwargs: dict[str, Any] = {
            "timeout": httpx.Timeout(timeout_seconds, connect=5.0),
            "trust_env": False,
        }
        if outbound_proxy:
            # MiniMax — Chinese provider; CEO 2026-05-10: РФ-прямой доступ.
            # Параметр оставлен на случай on-prem-сетки.
            kwargs["proxy"] = outbound_proxy
        self._http = httpx.AsyncClient(**kwargs)

    async def aclose(self) -> None:
        await self._http.aclose()

    async def fetch(self) -> BalanceSnapshot:
        try:
            resp = await self._http.get(
                MINIMAX_BALANCE_URL,
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

        # Some MiniMax endpoints wrap success/error в base_resp:
        #   {"base_resp": {"status_code": 1234, "status_msg": "..."}, ...}
        # status_code != 0 → API-level error, balance fields могут отсутствовать.
        base_resp = data.get("base_resp")
        if isinstance(base_resp, dict):
            status = base_resp.get("status_code")
            if isinstance(status, int) and status != 0:
                return BalanceSnapshot(
                    provider=self.provider_name,
                    balance_native=None,
                    currency=None,
                    fetch_method="api",
                    raw=data,
                    error=f"api_error_{status}: {base_resp.get('status_msg', '')[:200]}",
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
