"""Tests for the ``list_integrations`` MCP tool (S3)."""

from __future__ import annotations

import pytest

from voltari_gateway.mcp_server.context import MCP_PRINCIPAL_CTX
from voltari_gateway.mcp_server.tools.list_integrations import _INTEGRATIONS, handler


async def test_returns_all_integrations(db, mcp_token_fixture, bind_principal):
    bind_principal(mcp_token_fixture)
    result = await handler({}, db)
    assert result["total"] == len(_INTEGRATIONS)
    assert len(result["integrations"]) == result["total"]


async def test_each_integration_has_required_keys(db, mcp_token_fixture, bind_principal):
    bind_principal(mcp_token_fixture)
    result = await handler({}, db)
    expected = {"slug", "name", "base_url", "api_format", "description", "setup_url"}
    for entry in result["integrations"]:
        missing = expected - set(entry)
        assert not missing, f"missing keys: {missing}"


async def test_api_format_is_one_of_three(db, mcp_token_fixture, bind_principal):
    bind_principal(mcp_token_fixture)
    result = await handler({}, db)
    for entry in result["integrations"]:
        assert entry["api_format"] in {"openai", "anthropic", "google"}


async def test_base_url_points_at_brikko(db, mcp_token_fixture, bind_principal):
    bind_principal(mcp_token_fixture)
    result = await handler({}, db)
    for entry in result["integrations"]:
        assert entry["base_url"].startswith("https://api.brikko.ru/")


async def test_setup_url_points_at_brikko_integrations(db, mcp_token_fixture, bind_principal):
    bind_principal(mcp_token_fixture)
    result = await handler({}, db)
    for entry in result["integrations"]:
        url = entry["setup_url"]
        assert url.startswith("https://brikko.ru/integrations/")
        assert url.endswith(entry["slug"])


async def test_slugs_unique(db, mcp_token_fixture, bind_principal):
    bind_principal(mcp_token_fixture)
    result = await handler({}, db)
    slugs = [i["slug"] for i in result["integrations"]]
    assert len(slugs) == len(set(slugs))


async def test_claude_code_uses_anthropic_endpoint(db, mcp_token_fixture, bind_principal):
    """Spot-check: Claude Code must point at /v1/anthropic (Anthropic
    wire format), not /v1. Wrong endpoint here is a silent breakage —
    the agent would write a bad config into the user's claude-code .env.
    """
    bind_principal(mcp_token_fixture)
    result = await handler({}, db)
    claude = next(i for i in result["integrations"] if i["slug"] == "claude-code")
    assert claude["api_format"] == "anthropic"
    assert claude["base_url"] == "https://api.brikko.ru/v1/anthropic"


async def test_cursor_uses_openai_endpoint(db, mcp_token_fixture, bind_principal):
    bind_principal(mcp_token_fixture)
    result = await handler({}, db)
    cursor = next(i for i in result["integrations"] if i["slug"] == "cursor")
    assert cursor["api_format"] == "openai"
    assert cursor["base_url"] == "https://api.brikko.ru/v1"


async def test_raises_without_principal(db, mcp_token_fixture):
    token = MCP_PRINCIPAL_CTX.set(None)
    try:
        with pytest.raises(RuntimeError, match="without an authenticated"):
            await handler({}, db)
    finally:
        MCP_PRINCIPAL_CTX.reset(token)
