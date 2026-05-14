"""End-to-end Router tests.

Coverage:
* Bare `auto` resolves to the configured default strategy (cheap).
* Each `auto:*` tag dispatches to the right strategy.
* Pinned model returns it as primary with a same-tier fallback.
* Pinned model with too-small context raises RoutingError.
* RU-legal account refuses non-RU pinned model.
* Daily-budget pressure soft-degrades to cheap.
* `RoutingDecision.header_value` matches the documented format.
* Cross-provider chain construction prefers diversity.
"""

from __future__ import annotations

import uuid

import pytest

from voltari_gateway.router.catalog import Provider
from voltari_gateway.router.router import (
    AccountContext,
    Router,
    RouterRequest,
    RoutingError,
)
from voltari_gateway.router.strategies import Strategy, TaskCategory

# ---------------------------------------------------------------------------
# Tag parsing
# ---------------------------------------------------------------------------


class TestAutoTagParsing:
    @pytest.mark.asyncio
    async def test_bare_auto_uses_default_strategy(
        self, router: Router, account_payg: AccountContext
    ) -> None:
        decision = await router.route_request(
            RouterRequest(model_tag="auto", estimated_input_tokens=100),
            account_payg,
        )
        assert decision.strategy_used is Strategy.CHEAP

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("tag", "strategy"),
        [
            ("auto:cheap", Strategy.CHEAP),
            ("auto:smart", Strategy.SMART),
            ("auto:fast", Strategy.FAST),
            ("auto:ru-legal", Strategy.RU_LEGAL),
            # Sprint 11.6 — coding-agent stack tag.
            ("auto:code", Strategy.CODE),
        ],
    )
    async def test_auto_tag_dispatches_strategy(
        self,
        router: Router,
        account_payg: AccountContext,
        tag: str,
        strategy: Strategy,
    ) -> None:
        decision = await router.route_request(
            RouterRequest(model_tag=tag, estimated_input_tokens=100),
            account_payg,
        )
        assert decision.strategy_used is strategy

    @pytest.mark.asyncio
    async def test_router_parses_auto_code_tag(
        self,
        account_payg: AccountContext,
    ) -> None:
        """Router on the real catalogue picks Sonnet for auto:code by default.

        Uses the production CATALOG (not the synthetic one) so we lock in
        the contract Cursor / Claude Code / Codex CLI users will hit:
        ``model: "auto:code"`` → Sonnet 4.6 primary.
        """
        from voltari_gateway.router.catalog import CATALOG

        prod_router = Router(catalog=CATALOG)
        decision = await prod_router.route_request(
            RouterRequest(
                model_tag="auto:code",
                estimated_input_tokens=4_000,
                prompt_text="```python\ndef foo(): pass\n```",
            ),
            account_payg,
        )
        assert decision.strategy_used is Strategy.CODE
        assert decision.primary.id == "claude-sonnet-4.6"
        assert decision.primary.provider is Provider.ANTHROPIC

    @pytest.mark.asyncio
    async def test_invalid_strategy_raises(
        self, router: Router, account_payg: AccountContext
    ) -> None:
        with pytest.raises(RoutingError) as exc_info:
            await router.route_request(
                RouterRequest(model_tag="auto:wat", estimated_input_tokens=10),
                account_payg,
            )
        assert exc_info.value.reason_code == "invalid_strategy"


# ---------------------------------------------------------------------------
# Pinned models
# ---------------------------------------------------------------------------


class TestPinnedModel:
    @pytest.mark.asyncio
    async def test_pinned_model_returns_as_primary(
        self, router: Router, account_payg: AccountContext
    ) -> None:
        decision = await router.route_request(
            RouterRequest(model_tag="mid-1", estimated_input_tokens=100),
            account_payg,
        )
        assert decision.primary.id == "mid-1"
        assert decision.strategy_used is None
        assert decision.reason == "pinned:mid-1"

    @pytest.mark.asyncio
    async def test_pinned_model_has_same_tier_fallbacks(
        self, router: Router, account_payg: AccountContext
    ) -> None:
        decision = await router.route_request(
            RouterRequest(model_tag="mid-1", estimated_input_tokens=100),
            account_payg,
        )
        # mid-1 is Anthropic — fallbacks should be different providers.
        assert all(m.provider is not Provider.ANTHROPIC for m in decision.fallback_chain)

    @pytest.mark.asyncio
    async def test_pinned_unknown_model_raises(
        self, router: Router, account_payg: AccountContext
    ) -> None:
        with pytest.raises(RoutingError) as exc_info:
            await router.route_request(
                RouterRequest(model_tag="gpt-99-turbo", estimated_input_tokens=10),
                account_payg,
            )
        assert exc_info.value.reason_code == "unknown_model"

    @pytest.mark.asyncio
    async def test_pinned_model_too_small_context_raises(
        self, router: Router, account_payg: AccountContext
    ) -> None:
        # yandex-1 = 32k context; ask for 100k.
        with pytest.raises(RoutingError) as exc_info:
            await router.route_request(
                RouterRequest(model_tag="yandex-1", estimated_input_tokens=100_000),
                account_payg,
            )
        assert exc_info.value.reason_code == "context_too_large"


# ---------------------------------------------------------------------------
# Account-level constraints
# ---------------------------------------------------------------------------


class TestAccountConstraints:
    @pytest.mark.asyncio
    async def test_ru_legal_account_excludes_global_models(
        self, router: Router, account_ru_legal: AccountContext
    ) -> None:
        decision = await router.route_request(
            RouterRequest(model_tag="auto:cheap", estimated_input_tokens=100),
            account_ru_legal,
        )
        # Account is RU-legal → both primary and fallbacks must be RU.
        assert decision.primary.ru_legal
        assert all(m.ru_legal for m in decision.fallback_chain)

    @pytest.mark.asyncio
    async def test_ru_legal_account_refuses_non_ru_pinned(
        self, router: Router, account_ru_legal: AccountContext
    ) -> None:
        with pytest.raises(RoutingError) as exc_info:
            await router.route_request(
                RouterRequest(model_tag="mid-1", estimated_input_tokens=100),
                account_ru_legal,
            )
        assert exc_info.value.reason_code == "model_not_ru_legal"

    @pytest.mark.asyncio
    async def test_exclude_providers_filters_chain(
        self, router: Router, account_payg: AccountContext
    ) -> None:
        decision = await router.route_request(
            RouterRequest(
                model_tag="auto:cheap",
                estimated_input_tokens=100,
                exclude_providers=frozenset({Provider.DEEPSEEK}),
            ),
            account_payg,
        )
        all_chosen = [decision.primary, *decision.fallback_chain]
        assert all(m.provider is not Provider.DEEPSEEK for m in all_chosen)

    @pytest.mark.asyncio
    async def test_max_cost_kop_filters_expensive_models(
        self, router: Router, account_payg: AccountContext
    ) -> None:
        # Set an absurdly tight cap that only the cheapest model can satisfy.
        decision = await router.route_request(
            RouterRequest(
                model_tag="auto:cheap",
                estimated_input_tokens=1_000,
                max_cost_kop=10,  # ~10 kop = 0.1 ₽
            ),
            account_payg,
        )
        # Whatever survives, the primary's expected cost must be ≤10 kop.
        assert decision.primary.expected_cost_kop(input_tokens=1_000, output_tokens=1_000) <= 10

    @pytest.mark.asyncio
    async def test_no_eligible_model_raises(
        self, router: Router, account_payg: AccountContext
    ) -> None:
        # Exclude everyone → no candidates.
        with pytest.raises(RoutingError) as exc_info:
            await router.route_request(
                RouterRequest(
                    model_tag="auto:cheap",
                    estimated_input_tokens=100,
                    exclude_providers=frozenset(Provider),
                ),
                account_payg,
            )
        assert exc_info.value.reason_code == "no_eligible_model"


# ---------------------------------------------------------------------------
# Budget soft-degradation
# ---------------------------------------------------------------------------


class TestBudgetSoftDegrade:
    @pytest.mark.asyncio
    async def test_near_daily_budget_degrades_to_cheap(self, router: Router) -> None:
        """Account at 95% of daily budget on `auto:smart` should re-sort cheap."""
        account = AccountContext(
            account_id=uuid.UUID("00000000-0000-0000-0000-00000000002a"),
            tariff="pro",
            balance_kop=100_000,
            daily_spent_kop=950,
            daily_budget_kop=1_000,
        )
        # `auto:smart` would normally pick by quality/price ratio. With
        # the budget pressure we re-sort the survivors by `cheap`.
        decision = await router.route_request(
            RouterRequest(model_tag="auto:smart", estimated_input_tokens=100),
            account,
        )
        # Primary should be the cheapest non-premium model — cheap-1.
        assert decision.primary.id == "cheap-1"

    @pytest.mark.asyncio
    async def test_below_budget_threshold_uses_original_strategy(self, router: Router) -> None:
        account = AccountContext(
            account_id=uuid.UUID("00000000-0000-0000-0000-00000000002a"),
            tariff="pro",
            balance_kop=100_000,
            daily_spent_kop=100,
            daily_budget_kop=10_000,
        )
        decision = await router.route_request(
            RouterRequest(model_tag="auto:smart", estimated_input_tokens=100),
            account,
        )
        assert decision.strategy_used is Strategy.SMART


# ---------------------------------------------------------------------------
# Categorisation embedded in routing
# ---------------------------------------------------------------------------


class TestCategorisationInRouting:
    @pytest.mark.asyncio
    async def test_long_context_request_picks_long_context_model(
        self, router: Router, account_payg: AccountContext
    ) -> None:
        # 80k tokens → only models with ≥81k context survive.
        decision = await router.route_request(
            RouterRequest(model_tag="auto:cheap", estimated_input_tokens=80_000),
            account_payg,
        )
        assert decision.category is TaskCategory.LONG_CONTEXT
        assert decision.primary.context_window >= 81_000


# ---------------------------------------------------------------------------
# Header serialisation (Артефакт 6)
# ---------------------------------------------------------------------------


class TestHeaderValue:
    @pytest.mark.asyncio
    async def test_header_format_no_failover(
        self, router: Router, account_payg: AccountContext
    ) -> None:
        decision = await router.route_request(
            RouterRequest(model_tag="auto:cheap", estimated_input_tokens=10),
            account_payg,
        )
        header = decision.header_value(failover_used=False)
        assert header.startswith(f"{decision.primary.id};reason=")
        assert "failover_used=false" in header

    @pytest.mark.asyncio
    async def test_header_format_with_failover(
        self, router: Router, account_payg: AccountContext
    ) -> None:
        decision = await router.route_request(
            RouterRequest(model_tag="auto:smart", estimated_input_tokens=10),
            account_payg,
        )
        header = decision.header_value(failover_used=True)
        assert "failover_used=true" in header
        # Header is one ASCII line — no embedded newlines etc.
        assert "\n" not in header
        assert "\r" not in header


# ---------------------------------------------------------------------------
# Cross-provider chain construction
# ---------------------------------------------------------------------------


class TestCrossProviderChain:
    @pytest.mark.asyncio
    async def test_fallback_prefers_different_providers(
        self, router: Router, account_payg: AccountContext
    ) -> None:
        decision = await router.route_request(
            RouterRequest(model_tag="auto:cheap", estimated_input_tokens=100),
            account_payg,
        )
        providers_in_chain = [
            decision.primary.provider,
            *(m.provider for m in decision.fallback_chain),
        ]
        # All providers in the chain should be unique up to MAX_FALLBACK_CHAIN_LENGTH.
        # Since synthetic_catalog has 6 providers, no duplicates should be needed.
        assert len(set(providers_in_chain)) == len(providers_in_chain)

    @pytest.mark.asyncio
    async def test_fallback_chain_capped(
        self, router: Router, account_payg: AccountContext
    ) -> None:
        decision = await router.route_request(
            RouterRequest(model_tag="auto:cheap", estimated_input_tokens=100),
            account_payg,
        )
        assert len(decision.fallback_chain) <= 3
