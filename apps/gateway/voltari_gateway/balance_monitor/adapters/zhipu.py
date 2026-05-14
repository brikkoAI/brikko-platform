"""Zhipu AI (BigModel / GLM) balance adapter.

Endpoint (наблюдаемый по docs на 2026-05-12):
    GET https://open.bigmodel.cn/api/paas/v4/account/balance
    Authorization: Bearer <JWT>

**Important — JWT signing nuance:**

Unlike Moonshot / DeepSeek / MiniMax (where Bearer = api_key as-is), Zhipu's
billing endpoints REQUIRE a JWT-signed token even though the chat-completions
endpoint accepts direct Bearer. The key format from Zhipu console is
``<id>.<secret>``:

    api_key = "12345abc.S3CR3T"
    kid, secret = api_key.split(".", 1)
    payload = {
        "api_key": kid,
        "exp": int(time.time() * 1000) + 3600 * 1000,   # ms-since-epoch
        "timestamp": int(time.time() * 1000),
    }
    header = {"alg": "HS256", "sign_type": "SIGN"}
    token = jwt.encode(payload, secret, algorithm="HS256", headers=header)

NB: exp/timestamp в **миллисекундах** (Zhipu quirk — стандартный JWT использует
seconds, но Zhipu валидирует в ms). PyJWT принимает любое int значение в exp,
проверка стороной Zhipu — не PyJWT.

Response shape (observed in community issues 2026-05):
    {"code": 200, "msg": "success", "data": {"balance": 12.34, "currency": "CNY"}}
    {"balance": "12.34"}                            # legacy flat
    {"data": [{"balance": "...", "currency": "..."}]}  # account-list style

Если schema поменяется — raw_response уйдёт в БД. TODO: проверить актуальный
shape после первого реального запроса (https://open.bigmodel.cn/dev/api#sdk).

Currency: всегда CNY/RMB (Zhipu — domestic CN provider, USD-аккаунтов нет).
"""

from __future__ import annotations

import json
import time
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx
import jwt

from voltari_gateway.balance_monitor.adapters.base import (
    BalanceAdapter,
    BalanceSnapshot,
)
from voltari_gateway.utils.logging import get_logger

log = get_logger(__name__)

ZHIPU_BALANCE_URL = "https://open.bigmodel.cn/api/paas/v4/account/balance"

# JWT TTL: 1 час (по docs Zhipu — server-side check). Хранить и переиспользовать
# не нужно — генерация дешёвая, balance refresh раз в 6 часов.
_JWT_TTL_MS = 3600 * 1000


def _make_zhipu_jwt(api_key: str, *, now_ms: int | None = None) -> str:
    """Generate a Zhipu-compatible JWT from ``<id>.<secret>`` api_key.

    Args:
        api_key: Full key from Zhipu console (``<id>.<secret>`` form).
        now_ms: Override "now" in ms-since-epoch (for deterministic tests).

    Raises:
        ValueError: api_key is empty or not in ``<id>.<secret>`` form.
    """
    if not api_key or "." not in api_key:
        raise ValueError("Zhipu api_key must be in '<id>.<secret>' form")
    kid, secret = api_key.split(".", 1)
    if not kid or not secret:
        raise ValueError("Zhipu api_key has empty id or secret half")
    now = now_ms if now_ms is not None else int(time.time() * 1000)
    payload: dict[str, Any] = {
        "api_key": kid,
        "exp": now + _JWT_TTL_MS,
        "timestamp": now,
    }
    headers = {"alg": "HS256", "sign_type": "SIGN"}
    return jwt.encode(payload, secret, algorithm="HS256", headers=headers)


def _extract_balance(
    data: dict[str, Any],
) -> tuple[Decimal | None, str | None, str | None]:
    """Return ``(balance, currency, error)`` из любой из известных форм.

    Знаемые формы:
    * ``{"code": 200, "data": {"balance": ..., "currency": ...}}``
    * ``{"data": [{"balance": ..., "currency": ...}]}``  — account-list
    * ``{"balance": ..., "currency": ...}``               — flat shape
    """
    # API-level error code first.
    code = data.get("code")
    if isinstance(code, int) and code != 200 and code != 0:
        msg = data.get("msg") or data.get("message") or ""
        return None, None, f"api_error_{code}: {str(msg)[:200]}"

    # Shape 1: docs — {"data": {"balance": ..., "currency": ...}}
    inner: Any = data.get("data") if isinstance(data, dict) else None
    if isinstance(inner, dict):
        result = _extract_from(inner)
        if result[0] is not None or result[2] != "balance_field_missing":
            return result

    # Shape 2: account-list — {"data": [{...}, ...]}
    if isinstance(inner, list) and inner:
        first = inner[0]
        if isinstance(first, dict):
            result = _extract_from(first)
            if result[0] is not None or result[2] != "balance_field_missing":
                return result

    # Shape 3: flat — поля прямо в top-level.
    return _extract_from(data)


def _extract_from(obj: dict[str, Any]) -> tuple[Decimal | None, str | None, str | None]:
    raw_balance = obj.get("balance")
    if raw_balance is None:
        raw_balance = obj.get("available_balance")
    if raw_balance is None:
        raw_balance = obj.get("amount")
    if raw_balance is None:
        return None, None, "balance_field_missing"

    currency = obj.get("currency") or obj.get("currency_code")
    currency_str = _normalize_currency(currency)

    try:
        return Decimal(str(raw_balance)), currency_str, None
    except (InvalidOperation, ValueError):
        return None, currency_str, f"invalid_balance_value: {raw_balance!r}"


def _normalize_currency(currency: Any) -> str:
    """Map Zhipu currency hints → ISO ``CNY``.

    Zhipu is domestic CN — RMB/CNY only. Default CNY when пусто.
    """
    if not currency:
        return "CNY"
    cur = str(currency).upper().strip()
    if cur == "RMB":
        return "CNY"
    return cur


class ZhipuBalanceAdapter(BalanceAdapter):
    provider_name = "zhipu"
    is_remote = True

    def __init__(
        self,
        *,
        api_key: str,
        timeout_seconds: float = 10.0,
        outbound_proxy: str | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("Zhipu api_key required")
        if "." not in api_key:
            raise ValueError("Zhipu api_key must be in '<id>.<secret>' form")
        self._api_key = api_key
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
            token = _make_zhipu_jwt(self._api_key)
        except ValueError as exc:
            return BalanceSnapshot.error_for(self.provider_name, f"jwt_sign: {exc}")
        except Exception as exc:  # pragma: no cover — defensive
            return BalanceSnapshot.error_for(self.provider_name, f"jwt_sign: {exc}")

        try:
            resp = await self._http.get(
                ZHIPU_BALANCE_URL,
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
