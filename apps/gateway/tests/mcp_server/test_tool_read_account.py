"""Tests for the ``read_account`` MCP tool.

Cover surface:

* Happy path → all expected keys, RUB float matches kopecks.
* Negative balance (debt edge case) → still returned, not 5xx.
* Zero balance → ``balance_rub`` is 0.0, not "0".
* Welcome bonus field is always ``None`` (S2 schema).
* Tariff serialised as string value, not enum.
* Tariff active-until timestamp surfaces as ISO string.
* Raises if principal is unbound (programmer-error guard).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from voltari_gateway.db.models import Tariff
from voltari_gateway.mcp_server.context import MCP_PRINCIPAL_CTX
from voltari_gateway.mcp_server.tools.read_account import handler


async def test_returns_full_snapshot(db, mcp_token_fixture, bind_principal):
    bind_principal(mcp_token_fixture)
    result = await handler({}, db)

    assert result["account_id"] == str(mcp_token_fixture.account.id)
    assert result["balance_kopecks"] == 100_000
    assert result["balance_rub"] == 1000.0
    assert result["tariff"] == "pro"
    assert result["status"] == "active"
    assert result["welcome_bonus_remaining_kopecks"] is None


async def test_balance_rub_rounds_to_2_decimals(db, mcp_token_fixture, bind_principal):
    mcp_token_fixture.account.balance_kopecks = 12_345
    await db.commit()
    bind_principal(mcp_token_fixture)
    result = await handler({}, db)
    assert result["balance_kopecks"] == 12_345
    assert result["balance_rub"] == 123.45


async def test_zero_balance(db, mcp_token_fixture, bind_principal):
    mcp_token_fixture.account.balance_kopecks = 0
    await db.commit()
    bind_principal(mcp_token_fixture)
    result = await handler({}, db)
    assert result["balance_kopecks"] == 0
    assert result["balance_rub"] == 0.0


async def test_negative_balance_surfaces_cleanly(db, mcp_token_fixture, bind_principal):
    """An over-charged account (provider miscount) should still resolve."""
    mcp_token_fixture.account.balance_kopecks = -150
    await db.commit()
    bind_principal(mcp_token_fixture)
    result = await handler({}, db)
    assert result["balance_kopecks"] == -150
    assert result["balance_rub"] == -1.5


@pytest.mark.parametrize(
    ("tariff", "expected"),
    [
        (Tariff.PAYG, "payg"),
        (Tariff.PRO, "pro"),
        (Tariff.PRO_PRIVACY, "pro_privacy"),
        (Tariff.TEAM, "team"),
        (Tariff.BUSINESS, "business"),
        (Tariff.BUSINESS_PLUS, "business_plus"),
    ],
)
async def test_tariff_serialised_as_value_string(
    db, mcp_token_fixture, bind_principal, tariff, expected
):
    mcp_token_fixture.account.tariff = tariff
    await db.commit()
    bind_principal(mcp_token_fixture)
    result = await handler({}, db)
    assert result["tariff"] == expected


async def test_tariff_active_until_iso_string(db, mcp_token_fixture, bind_principal):
    until = datetime.now(UTC) + timedelta(days=30)
    mcp_token_fixture.account.tariff_active_until = until
    await db.commit()
    bind_principal(mcp_token_fixture)
    result = await handler({}, db)
    assert result["tariff_active_until"] is not None
    assert "T" in result["tariff_active_until"]  # ISO-format sanity


async def test_tariff_active_until_none_when_unset(db, mcp_token_fixture, bind_principal):
    bind_principal(mcp_token_fixture)
    result = await handler({}, db)
    assert result["tariff_active_until"] is None


async def test_raises_without_principal(db, mcp_token_fixture):
    """No bound principal → RuntimeError (programmer-error, not a 4xx)."""
    token = MCP_PRINCIPAL_CTX.set(None)
    try:
        with pytest.raises(RuntimeError, match="without an authenticated principal"):
            await handler({}, db)
    finally:
        MCP_PRINCIPAL_CTX.reset(token)


async def test_ignores_unknown_arguments(db, mcp_token_fixture, bind_principal):
    """Tool accepts the dict (so SDK validator can check shape) — body
    must not actually consume any args. Passing keys we forbid in the
    schema would be 400 at the SDK layer; here we just confirm the
    handler is shape-tolerant in isolation."""
    bind_principal(mcp_token_fixture)
    # In production the SDK validator strips unknown keys; we just
    # confirm the handler doesn't blow up if they leak through.
    result = await handler({"unexpected": "value"}, db)
    assert result["account_id"] == str(mcp_token_fixture.account.id)
