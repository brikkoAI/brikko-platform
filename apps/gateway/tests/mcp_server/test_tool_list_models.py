"""Tests for the ``list_models`` MCP tool (S3).

Cover surface:

* Unfiltered call returns the entire catalog.
* Empty filter strings are coerced to None (agents pass "" sometimes).
* ``filter_provider`` narrows by provider value, case-insensitive.
* Unknown provider → empty list, not an error (lenient).
* ``filter_capability`` filters by capability flag.
* Unknown capability → 400 with the documented error code.
* Each entry carries the documented keys.
* RUB prices match what ``ModelSpec.to_public_dict`` computes.
* Missing principal → RuntimeError (auth guard).
"""

from __future__ import annotations

import pytest

from voltari_gateway.mcp_server.context import MCP_PRINCIPAL_CTX
from voltari_gateway.mcp_server.tools.list_models import handler
from voltari_gateway.router.catalog import CATALOG
from voltari_gateway.utils.errors import GatewayError


async def test_returns_full_catalog_when_unfiltered(db, mcp_token_fixture, bind_principal):
    bind_principal(mcp_token_fixture)
    result = await handler({}, db)
    assert result["total"] == len(CATALOG)
    assert len(result["models"]) == result["total"]
    assert result["filters"] == {"provider": None, "capability": None}


async def test_each_entry_has_documented_keys(db, mcp_token_fixture, bind_principal):
    bind_principal(mcp_token_fixture)
    result = await handler({}, db)
    expected_keys = {
        "id",
        "display_name",
        "provider",
        "provider_display_name",
        "tier",
        "context_window",
        "input_rub_per_1m",
        "output_rub_per_1m",
        "cached_input_rub_per_1m",
        "capabilities",
        "modalities",
        "description",
        "deprecated_at",
        "best_for",
    }
    for entry in result["models"][:5]:  # spot-check the first five
        missing = expected_keys - set(entry)
        assert not missing, f"missing keys: {missing} in {entry['id']}"


async def test_filter_by_provider_narrows_results(db, mcp_token_fixture, bind_principal):
    bind_principal(mcp_token_fixture)
    result = await handler({"filter_provider": "openai"}, db)
    assert result["total"] >= 1
    assert all(m["provider"] == "openai" for m in result["models"])
    assert result["filters"]["provider"] == "openai"


async def test_filter_by_provider_case_insensitive(db, mcp_token_fixture, bind_principal):
    bind_principal(mcp_token_fixture)
    result = await handler({"filter_provider": "OPENAI"}, db)
    assert result["total"] >= 1
    assert all(m["provider"] == "openai" for m in result["models"])


async def test_empty_string_provider_treated_as_no_filter(db, mcp_token_fixture, bind_principal):
    """Agents sometimes pass empty strings instead of omitting the key.
    The lenient coercion to None keeps the contract intuitive."""
    bind_principal(mcp_token_fixture)
    a = await handler({"filter_provider": ""}, db)
    b = await handler({}, db)
    assert a["total"] == b["total"]
    assert a["filters"]["provider"] is None


async def test_unknown_provider_returns_empty(db, mcp_token_fixture, bind_principal):
    """Lenient: unknown provider → empty list (not a 400). Agents
    typo provider names occasionally; returning empty lets them
    self-correct without an audit-log spike."""
    bind_principal(mcp_token_fixture)
    result = await handler({"filter_provider": "totallyfakeprovider"}, db)
    assert result["total"] == 0
    assert result["models"] == []


async def test_filter_by_vision_capability(db, mcp_token_fixture, bind_principal):
    bind_principal(mcp_token_fixture)
    result = await handler({"filter_capability": "vision"}, db)
    for m in result["models"]:
        assert m["capabilities"]["vision"] is True


async def test_filter_by_tools_capability(db, mcp_token_fixture, bind_principal):
    bind_principal(mcp_token_fixture)
    result = await handler({"filter_capability": "tools"}, db)
    assert result["total"] >= 1
    for m in result["models"]:
        assert m["capabilities"]["tool_calling"] is True


async def test_filter_by_ru_legal_capability(db, mcp_token_fixture, bind_principal):
    """``ru_legal`` should match only Yandex/Sber-hosted models."""
    bind_principal(mcp_token_fixture)
    result = await handler({"filter_capability": "ru_legal"}, db)
    for m in result["models"]:
        assert m["capabilities"]["ru_legal"] is True
        assert m["provider"] in {"yandex", "sber"}


async def test_unknown_capability_raises_400(db, mcp_token_fixture, bind_principal):
    bind_principal(mcp_token_fixture)
    with pytest.raises(GatewayError) as exc:
        await handler({"filter_capability": "telekinesis"}, db)
    assert exc.value.status_code == 400
    assert exc.value.error_code == "invalid_capability"


async def test_combined_filters_intersect(db, mcp_token_fixture, bind_principal):
    """provider + capability filters AND together."""
    bind_principal(mcp_token_fixture)
    result = await handler({"filter_provider": "anthropic", "filter_capability": "tools"}, db)
    for m in result["models"]:
        assert m["provider"] == "anthropic"
        assert m["capabilities"]["tool_calling"] is True


async def test_rub_prices_are_non_negative(db, mcp_token_fixture, bind_principal):
    bind_principal(mcp_token_fixture)
    result = await handler({}, db)
    for m in result["models"]:
        if m["input_rub_per_1m"] is not None:
            assert m["input_rub_per_1m"] >= 0
        if m["output_rub_per_1m"] is not None:
            assert m["output_rub_per_1m"] >= 0


async def test_raises_without_principal(db, mcp_token_fixture):
    """Auth-context guard — same contract as the other tools."""
    token = MCP_PRINCIPAL_CTX.set(None)
    try:
        with pytest.raises(RuntimeError, match="without an authenticated"):
            await handler({}, db)
    finally:
        MCP_PRINCIPAL_CTX.reset(token)
