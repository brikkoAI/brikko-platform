"""``read_account`` MCP tool — balance / tariff snapshot.

Returns a flat dict so Claude / Cursor agents can quote it directly to
the user. Welcome-bonus tracking is **not** a separate field in our
schema: the 200 ₽ welcome credit lands on ``account.balance_kopecks``
and is indistinguishable from regular top-ups by design (CEO 29.04 —
KISS, no separate ledger). We surface a single ``balance_kopecks`` and
let the agent narrate.

Why expose RUB float alongside kopecks: the LLM is bad at int division
("balance is 12345 kopecks, that's 123.45 rubles, no wait..."). Pre-
computing the float removes one class of agent mistakes.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from voltari_gateway.db.models import Account
from voltari_gateway.mcp_server.context import current_principal
from voltari_gateway.utils.errors import GatewayError

NAME = "read_account"
DESCRIPTION = (
    "Return the Brikko account snapshot: current balance (RUB + kopecks), "
    "tariff, and account status. Read-only; no arguments. Use this when "
    "the user asks 'сколько у меня осталось?' / 'на каком я тарифе?'."
)
INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {},
    "additionalProperties": False,
}


async def handler(_arguments: dict[str, Any], db: AsyncSession) -> dict[str, Any]:
    """Resolve the calling account and return its public snapshot.

    Tool args are ignored — auth context (``current_principal``) gives us
    the account id. We DO accept the dict so the SDK validator can still
    enforce ``additionalProperties: false`` against a chatty agent.
    """
    principal = current_principal()

    account = await db.get(Account, principal.account_id)
    if account is None:
        # Should never happen: the auth layer just resolved this account.
        # Raise a 5xx-style internal error rather than returning bad data.
        raise GatewayError(
            status_code=500,
            message="Account row vanished between auth and tool dispatch.",
            type="internal_error",
            code="account_not_found_post_auth",
        )

    return {
        "account_id": str(account.id),
        "balance_kopecks": int(account.balance_kopecks),
        "balance_rub": round(account.balance_kopecks / 100.0, 2),
        "tariff": account.tariff.value,
        "status": account.status.value,
        "tariff_active_until": (
            account.tariff_active_until.isoformat() if account.tariff_active_until else None
        ),
        # Welcome bonus is not tracked separately — see module docstring.
        # We expose ``null`` to make the contract explicit for future
        # consumers (and to keep the JSON shape stable when we add per-
        # bonus tracking in a later sprint).
        "welcome_bonus_remaining_kopecks": None,
    }


__all__ = ["DESCRIPTION", "INPUT_SCHEMA", "NAME", "handler"]
