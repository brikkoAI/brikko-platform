"""Tests for the ``list_cookbook_recipes`` MCP tool (S3)."""

from __future__ import annotations

import pytest

from voltari_gateway.mcp_server.context import MCP_PRINCIPAL_CTX
from voltari_gateway.mcp_server.tools.list_cookbook_recipes import (
    _RECIPES,
    handler,
)


async def test_returns_all_recipes(db, mcp_token_fixture, bind_principal):
    bind_principal(mcp_token_fixture)
    result = await handler({}, db)
    assert result["total"] == len(_RECIPES)
    assert len(result["recipes"]) == result["total"]


async def test_each_recipe_has_required_keys(db, mcp_token_fixture, bind_principal):
    bind_principal(mcp_token_fixture)
    result = await handler({}, db)
    expected = {
        "slug",
        "title",
        "summary",
        "model_recommended",
        "audience",
        "tags",
        "estimated_cost_rub_per_1k_requests",
        "estimated_cost_kopecks_per_1k_requests",
        "prompt_template_url",
    }
    for recipe in result["recipes"]:
        missing = expected - set(recipe)
        assert not missing, f"missing keys: {missing}"


async def test_kopecks_match_rub(db, mcp_token_fixture, bind_principal):
    """kopecks_per_1k must be RUB × 100 (no precision loss possible
    because RUB values are integers in the cookbook frontmatter)."""
    bind_principal(mcp_token_fixture)
    result = await handler({}, db)
    for recipe in result["recipes"]:
        rub = recipe["estimated_cost_rub_per_1k_requests"]
        kop = recipe["estimated_cost_kopecks_per_1k_requests"]
        assert kop == rub * 100, f"{recipe['slug']}: {kop} != {rub}*100"


async def test_prompt_template_url_points_at_brikko(db, mcp_token_fixture, bind_principal):
    bind_principal(mcp_token_fixture)
    result = await handler({}, db)
    for recipe in result["recipes"]:
        url = recipe["prompt_template_url"]
        assert url.startswith("https://brikko.ru/cookbook/")
        assert url.endswith(recipe["slug"])


async def test_slugs_are_unique(db, mcp_token_fixture, bind_principal):
    bind_principal(mcp_token_fixture)
    result = await handler({}, db)
    slugs = [r["slug"] for r in result["recipes"]]
    assert len(slugs) == len(set(slugs)), "duplicate slugs in cookbook"


async def test_model_recommended_references_real_or_auto_tag(db, mcp_token_fixture, bind_principal):
    """Each recipe must reference either a catalog model id or an auto:* tag."""
    from voltari_gateway.router.catalog import CATALOG

    bind_principal(mcp_token_fixture)
    catalog_ids = {m.id for m in CATALOG}
    result = await handler({}, db)
    for recipe in result["recipes"]:
        ref = recipe["model_recommended"]
        if ref.startswith("auto:") or ref == "auto":
            continue
        assert ref in catalog_ids, f"{recipe['slug']} references '{ref}' which is not in catalog"


async def test_raises_without_principal(db, mcp_token_fixture):
    token = MCP_PRINCIPAL_CTX.set(None)
    try:
        with pytest.raises(RuntimeError, match="without an authenticated"):
            await handler({}, db)
    finally:
        MCP_PRINCIPAL_CTX.reset(token)
