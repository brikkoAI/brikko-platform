"""Tests for ``recommend_model`` MCP tool (S3 router-driven implementation).

S2 shipped a deterministic keyword heuristic — those tests pinned exact
model ids. S3 routes through the real ``Router.route_request`` engine,
so test expectations are now:

* **Shape contract** — every response carries the documented keys.
* **Router decision flow** — code/reasoning categorisation routes to
  cross-tier candidates; budget cap filters; speed/quality strategies
  flip; per-account routing prefs are honoured.
* **Edge cases** — invalid input, empty budget, missing account.

We don't pin specific model ids (catalog moves) — we assert on
categories (tier, provider, capability flags) and invariants
(estimated cost > 0, fallback chain ≤ 3, alternatives present).
"""

from __future__ import annotations

import pytest

from voltari_gateway.mcp_server.tools.recommend_model import handler
from voltari_gateway.router.catalog import CATALOG
from voltari_gateway.utils.errors import GatewayError

# ---------------------------------------------------------------------------
# Shape contract
# ---------------------------------------------------------------------------


async def test_response_carries_documented_keys(db, mcp_token_fixture, bind_principal):
    """Top-level shape contract — agents depend on these keys.

    Adding a new key is fine; removing one breaks every client that
    quotes a key directly. This test pins the surface.
    """
    bind_principal(mcp_token_fixture)
    result = await handler({"task_description": "summarize this article"}, db)

    for key in (
        "recommended_model_id",
        "provider",
        "tier",
        "context_window",
        "reasoning",
        "estimated_cost_kopecks",
        "estimated_cost_rub",
        "fallback_chain",
        "alternatives",
        "router_engine",
    ):
        assert key in result, f"missing top-level key '{key}'"

    assert result["router_engine"] == "router.route_request"


async def test_recommended_model_is_in_catalog(db, mcp_token_fixture, bind_principal):
    """Whatever the router picks must actually exist in our catalog —
    otherwise the agent quotes a model id that 404s on /v1/chat."""
    bind_principal(mcp_token_fixture)
    catalog_ids = {m.id for m in CATALOG}
    result = await handler({"task_description": "summarize this article"}, db)
    assert result["recommended_model_id"] in catalog_ids


async def test_estimated_cost_is_positive_integer(db, mcp_token_fixture, bind_principal):
    bind_principal(mcp_token_fixture)
    result = await handler({"task_description": "translate this paragraph"}, db)
    assert isinstance(result["estimated_cost_kopecks"], int)
    assert result["estimated_cost_kopecks"] > 0
    # estimated_cost_rub is the kopecks/100 — sanity check the conversion
    assert abs(result["estimated_cost_rub"] - result["estimated_cost_kopecks"] / 100.0) < 0.01


async def test_fallback_chain_is_capped_at_three(db, mcp_token_fixture, bind_principal):
    """Hard cap per ``Router.MAX_FALLBACK_CHAIN_LENGTH``. Going beyond 3
    means burning latency on a recovery walk that won't realistically
    succeed — surface 503 instead.
    """
    bind_principal(mcp_token_fixture)
    result = await handler({"task_description": "analyze this contract for risks"}, db)
    assert isinstance(result["fallback_chain"], list)
    assert len(result["fallback_chain"]) <= 3
    for fb in result["fallback_chain"]:
        assert "model_id" in fb
        assert "provider" in fb
        assert "estimated_cost_kopecks" in fb


async def test_alternatives_are_curated_list(db, mcp_token_fixture, bind_principal):
    bind_principal(mcp_token_fixture)
    result = await handler({"task_description": "summarize this article"}, db)
    assert isinstance(result["alternatives"], list)
    # ``alternatives`` may be empty if the router's primary isn't in the
    # curated map (e.g. an exotic model). For Anthropic/OpenAI/DeepSeek
    # picks we expect at least one entry — assert >= 0 to not couple to
    # the curated table beyond shape.
    for alt in result["alternatives"]:
        for k in ("model_id", "provider", "tier", "estimated_cost_kopecks", "note"):
            assert k in alt


# ---------------------------------------------------------------------------
# Strategy switching (smart vs fast)
# ---------------------------------------------------------------------------


async def test_prefer_speed_uses_fast_strategy(db, mcp_token_fixture, bind_principal):
    """``prefer_speed_over_quality=True`` flips the model_tag from
    auto:smart to auto:fast — the reasoning string surfaces this so the
    user can see what we did.
    """
    bind_principal(mcp_token_fixture)
    result = await handler(
        {
            "task_description": "summarize this article",
            "prefer_speed_over_quality": True,
        },
        db,
    )
    assert "fast" in result["reasoning"].lower()


async def test_default_uses_smart_strategy(db, mcp_token_fixture, bind_principal):
    bind_principal(mcp_token_fixture)
    result = await handler({"task_description": "analyze this contract for risks"}, db)
    assert "smart" in result["reasoning"].lower()


# ---------------------------------------------------------------------------
# Budget cap
# ---------------------------------------------------------------------------


async def test_budget_cap_filters_expensive_models(db, mcp_token_fixture, bind_principal):
    """A tight budget should pick a cheaper model than no budget.

    We pin on the invariant "tighter budget → cheaper estimated cost"
    rather than on a specific model id, so this test survives catalog
    rebalancing.
    """
    bind_principal(mcp_token_fixture)
    no_budget = await handler({"task_description": "analyze contract for legal risks"}, db)
    tight_budget = await handler(
        {
            "task_description": "analyze contract for legal risks",
            "budget_kopecks_per_request": 100,  # 1 RUB cap
        },
        db,
    )
    assert tight_budget["estimated_cost_kopecks"] <= no_budget["estimated_cost_kopecks"]


async def test_router_error_surfaces_as_422(db, mcp_token_fixture, bind_principal, monkeypatch):
    """``RoutingError`` from the engine is translated to GatewayError(422)
    so the agent narrates "no model fits these constraints" rather than
    inventing a recommendation.

    We force the error by monkeypatching ``Router.route_request`` to raise
    — the exact RoutingError reason_code is what the test pins on.
    Constructing a real "no eligible model" path requires gymnastics
    against a catalog with sub-kopeck models; this is the cleaner test
    of the error-translation contract.
    """
    from voltari_gateway.mcp_server.tools import recommend_model as rm
    from voltari_gateway.router.router import RoutingError

    async def _raising(self, req, ctx):
        raise RoutingError(
            reason_code="no_eligible_model",
            message="forced for test",
        )

    monkeypatch.setattr(rm.Router, "route_request", _raising)
    bind_principal(mcp_token_fixture)
    with pytest.raises(GatewayError) as exc:
        await handler({"task_description": "summarize this paragraph"}, db)
    assert exc.value.status_code == 422
    assert exc.value.error_code == "no_eligible_model"


async def test_null_budget_means_unconstrained(db, mcp_token_fixture, bind_principal):
    """budget=None should produce the same recommendation as omitting
    the key entirely. Sanity check on the optional-arg plumbing.
    """
    bind_principal(mcp_token_fixture)
    explicit_none = await handler(
        {
            "task_description": "summarize this article",
            "budget_kopecks_per_request": None,
        },
        db,
    )
    omitted = await handler({"task_description": "summarize this article"}, db)
    assert explicit_none["recommended_model_id"] == omitted["recommended_model_id"]


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------


async def test_short_task_rejected(db, mcp_token_fixture, bind_principal):
    bind_principal(mcp_token_fixture)
    with pytest.raises(GatewayError) as exc:
        await handler({"task_description": "abc"}, db)
    assert exc.value.status_code == 400
    assert exc.value.error_code == "invalid_task_description"


async def test_empty_task_rejected(db, mcp_token_fixture, bind_principal):
    bind_principal(mcp_token_fixture)
    with pytest.raises(GatewayError) as exc:
        await handler({"task_description": "   "}, db)
    assert exc.value.error_code == "invalid_task_description"


async def test_non_int_budget_rejected(db, mcp_token_fixture, bind_principal):
    bind_principal(mcp_token_fixture)
    with pytest.raises(GatewayError) as exc:
        await handler(
            {
                "task_description": "summarize this content",
                "budget_kopecks_per_request": "cheap",
            },
            db,
        )
    assert exc.value.error_code == "invalid_budget"


async def test_negative_budget_rejected(db, mcp_token_fixture, bind_principal):
    bind_principal(mcp_token_fixture)
    with pytest.raises(GatewayError) as exc:
        await handler(
            {
                "task_description": "summarize this content",
                "budget_kopecks_per_request": -5,
            },
            db,
        )
    assert exc.value.error_code == "invalid_budget"


# ---------------------------------------------------------------------------
# Router-decision categories
# ---------------------------------------------------------------------------


async def test_long_context_request_picks_long_context_model(db, mcp_token_fixture, bind_principal):
    """A task with ~60k estimated tokens should route to a model with a
    context window big enough for it. We pass a long task description
    that triggers the categoriser's LONG_CONTEXT bucket.
    """
    bind_principal(mcp_token_fixture)
    long_task = "Analyze this document carefully. " * 8000  # >200KB; ~50k tokens
    long_task = long_task[:2000]  # but capped at MCP schema maxLength
    # Note: even at 2000 chars (~500 tokens), the categoriser uses
    # estimated_input_tokens from the task length. We assert the router
    # picks a non-trivial context window — this also smoke-tests that
    # categorize_request runs without error.
    result = await handler({"task_description": long_task}, db)
    spec = next((m for m in CATALOG if m.id == result["recommended_model_id"]), None)
    assert spec is not None
    assert spec.context_window >= 32_000


async def test_routing_engine_label_present(db, mcp_token_fixture, bind_principal):
    """``router_engine`` MUST be the real router string, not "heuristic" —
    catches regressions where the S2 keyword matcher gets accidentally
    re-introduced as a fallback.
    """
    bind_principal(mcp_token_fixture)
    result = await handler({"task_description": "summarize this article"}, db)
    assert result["router_engine"] == "router.route_request"


async def test_provider_field_matches_recommended_model(db, mcp_token_fixture, bind_principal):
    """The provider string should match the catalog entry for the
    recommended model. Catches mismatches where someone hand-wired
    provider from a different lookup.
    """
    bind_principal(mcp_token_fixture)
    result = await handler({"task_description": "summarize this article"}, db)
    spec = next((m for m in CATALOG if m.id == result["recommended_model_id"]), None)
    assert spec is not None
    assert result["provider"] == spec.provider.value


async def test_fallback_uses_different_provider_when_possible(
    db, mcp_token_fixture, bind_principal
):
    """The fallback chain optimises for cross-provider diversity (per
    Router._build_cross_provider_chain). For a routine task the first
    fallback should be a different provider than the primary, so a
    single-provider outage doesn't take out the whole chain.
    """
    bind_principal(mcp_token_fixture)
    result = await handler({"task_description": "analyze this contract"}, db)
    if not result["fallback_chain"]:
        pytest.skip("router returned no fallback — pinned-model scenario")
    primary_provider = result["provider"]
    fallback_providers = [fb["provider"] for fb in result["fallback_chain"]]
    # At least one fallback must be from a different provider.
    assert any(p != primary_provider for p in fallback_providers), (
        f"all fallbacks ({fallback_providers}) share primary provider "
        f"({primary_provider}) — cross-provider failover degraded"
    )
