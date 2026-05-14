"""Smart Router v2 pipeline scaffolding tests — Sprint S1.

What we assert
--------------

1. Pipeline executes all six stages in the documented order.
2. Each NO-OP stage (semantic_cache, budget_filter, sticky_session,
   custom_rules, provider_call) returns the context unchanged.
3. ``BaseRouterStage`` produces exactly the same ``RoutingDecision``
   as a direct call to ``Router.route_request`` (regression baseline).
4. Pipeline result matches the legacy v1 path for a parametrised set
   of representative requests (auto/auto:smart/auto:fast/pinned/
   pinned-context-too-large).
5. ``parse_budget_kopecks_header`` correctly parses headers (positive
   ints accepted, malformed/zero/negative rejected to ``None``).
6. ``should_use_pipeline`` implements the env-OR-account semantics.
7. Pipeline raises ``RuntimeError`` if BaseRouterStage is missing.

These tests use the existing router-test conftest (synthetic catalog
+ AccountContext fixtures). No DB / Redis / HTTP — pure logic.
"""

from __future__ import annotations

import uuid

import pytest

# We re-export the router test conftest fixtures locally so we can run
# alongside ``tests/conftest.py`` (which configures the FastAPI app/DB
# fixtures). Importing the module as a plugin via ``pytest_plugins``
# clashes with concurrent discovery from ``voltari_gateway/router/tests/``.
from voltari_gateway.router.catalog import ModelSpec, ModelTier
from voltari_gateway.router.catalog import Provider as _Provider
from voltari_gateway.router.context import PipelineContext
from voltari_gateway.router.pipeline import (
    PipelineResult,
    RouterPipeline,
    parse_budget_kopecks_header,
    should_use_pipeline,
)
from voltari_gateway.router.router import (
    AccountContext,
    Router,
    RouterRequest,
    RoutingDecision,
    RoutingError,
)
from voltari_gateway.router.stages import default_stages
from voltari_gateway.router.stages.base import RouterStage
from voltari_gateway.router.stages.base_router import BaseRouterStage
from voltari_gateway.router.stages.budget_filter import BudgetFilterStage
from voltari_gateway.router.stages.custom_rules import CustomRulesStage
from voltari_gateway.router.stages.provider_call import ProviderCallStage
from voltari_gateway.router.stages.semantic_cache import SemanticCacheStage
from voltari_gateway.router.stages.sticky_session import StickySessionStage


def _spec(
    *,
    id: str,
    provider: _Provider,
    tier: ModelTier,
    inp: int,
    out: int,
    ctx: int = 128_000,
    p50: int = 1_000,
    quality: int = 70,
    ru_legal: bool = False,
    tools: bool = True,
) -> ModelSpec:
    return ModelSpec(
        id=id,
        provider=provider,
        tier=tier,
        input_price_kop_per_1k=inp,
        cached_price_kop_per_1k=inp // 10 or 1,
        output_price_kop_per_1k=out,
        context_window=ctx,
        latency_p50_ms=p50,
        quality_score=quality,
        supports_streaming=True,
        supports_tools=tools,
        ru_legal=ru_legal,
    )


@pytest.fixture
def synthetic_catalog() -> tuple[ModelSpec, ...]:
    """Compact catalogue for routing assertions — mirrors the one in
    ``voltari_gateway/router/tests/conftest.py``.
    """
    return (
        _spec(
            id="cheap-1",
            provider=_Provider.DEEPSEEK,
            tier=ModelTier.NANO,
            inp=2,
            out=3,
            quality=60,
            p50=1_500,
        ),
        _spec(
            id="cheap-2-openai",
            provider=_Provider.OPENAI,
            tier=ModelTier.BUDGET,
            inp=5,
            out=10,
            quality=72,
            p50=900,
        ),
        _spec(
            id="mid-1",
            provider=_Provider.ANTHROPIC,
            tier=ModelTier.MID,
            inp=20,
            out=100,
            quality=85,
            p50=1_300,
        ),
        _spec(
            id="mid-2",
            provider=_Provider.GOOGLE,
            tier=ModelTier.MID,
            inp=15,
            out=80,
            quality=78,
            p50=600,
        ),
        _spec(
            id="premium-1",
            provider=_Provider.OPENAI,
            tier=ModelTier.PREMIUM,
            inp=100,
            out=400,
            quality=95,
            p50=4_000,
        ),
        _spec(
            id="yandex-1",
            provider=_Provider.YANDEX,
            tier=ModelTier.MID,
            inp=40,
            out=40,
            quality=65,
            p50=1_000,
            ru_legal=True,
            ctx=32_000,
            tools=False,
        ),
        _spec(
            id="sber-1",
            provider=_Provider.SBER,
            tier=ModelTier.BUDGET,
            inp=6,
            out=6,
            quality=55,
            p50=900,
            ru_legal=True,
            ctx=131_000,
            tools=False,
        ),
        _spec(
            id="long-ctx-1",
            provider=_Provider.GOOGLE,
            tier=ModelTier.MID,
            inp=10,
            out=80,
            quality=80,
            p50=1_200,
            ctx=1_000_000,
        ),
    )


@pytest.fixture
def router(synthetic_catalog: tuple[ModelSpec, ...]) -> Router:
    return Router(catalog=synthetic_catalog)


@pytest.fixture
def account_payg() -> AccountContext:
    return AccountContext(
        account_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
        tariff="payg",
        balance_kop=50_000,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_ctx(
    *,
    account: AccountContext,
    model_tag: str = "auto:cheap",
    prompt_tokens: int = 100,
    **overrides: object,
) -> PipelineContext:
    """Tiny ergonomic constructor — fills in the boring fields."""
    req = RouterRequest(
        model_tag=model_tag,
        estimated_input_tokens=prompt_tokens,
        prompt_text="hello world",
    )
    return PipelineContext(
        router_request=req,
        account=account,
        request_id=uuid.uuid4().hex,
        **overrides,  # type: ignore[arg-type]
    )


# ---------------------------------------------------------------------------
# 1. Stage composition
# ---------------------------------------------------------------------------


class TestDefaultStages:
    def test_default_stage_count_is_six(self) -> None:
        stages = default_stages()
        assert len(stages) == 6

    def test_default_stage_order(self) -> None:
        stages = default_stages()
        names = [s.name for s in stages]
        assert names == [
            "semantic_cache",
            "budget_filter",
            "sticky_session",
            "custom_rules",
            "base_router",
            "provider_call",
        ]

    def test_all_stages_implement_router_stage_protocol(self) -> None:
        for stage in default_stages():
            assert isinstance(stage, RouterStage), f"{stage} does not implement RouterStage"

    def test_each_stage_has_unique_name(self) -> None:
        names = [s.name for s in default_stages()]
        assert len(set(names)) == len(names)


# ---------------------------------------------------------------------------
# 2. NO-OP stages do nothing
# ---------------------------------------------------------------------------


class TestNoOpStages:
    """All five S2-S4 placeholders MUST be pure NO-OPs in S1."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "stage_cls",
        [
            SemanticCacheStage,
            BudgetFilterStage,
            StickySessionStage,
            CustomRulesStage,
            ProviderCallStage,
        ],
    )
    async def test_no_op_stage_does_not_mutate_context(
        self,
        stage_cls: type[RouterStage],
        account_payg: AccountContext,
    ) -> None:
        stage = stage_cls()  # type: ignore[call-arg]
        ctx = _make_ctx(account=account_payg)
        # Capture state before the call.
        before_decision = ctx.decision
        before_cache_hit = ctx.cache_hit
        before_short = ctx.short_circuit
        before_meta = dict(ctx.metadata)

        await stage.apply(ctx)

        assert ctx.decision is before_decision
        assert ctx.cache_hit == before_cache_hit
        assert ctx.short_circuit == before_short
        assert ctx.metadata == before_meta


# ---------------------------------------------------------------------------
# 3. BaseRouterStage delegates to v1 Router.route_request
# ---------------------------------------------------------------------------


class TestBaseRouterStage:
    @pytest.mark.asyncio
    async def test_produces_routing_decision(
        self,
        router: Router,
        account_payg: AccountContext,
    ) -> None:
        stage = BaseRouterStage(router=router)
        ctx = _make_ctx(account=account_payg, model_tag="auto:cheap")

        await stage.apply(ctx)

        assert ctx.decision is not None
        assert isinstance(ctx.decision, RoutingDecision)

    @pytest.mark.asyncio
    async def test_decision_matches_direct_v1_call(
        self,
        router: Router,
        account_payg: AccountContext,
    ) -> None:
        """Regression baseline: stage output == direct v1 output."""
        stage = BaseRouterStage(router=router)
        req = RouterRequest(
            model_tag="auto:cheap",
            estimated_input_tokens=100,
            prompt_text="hello",
        )

        direct = await router.route_request(req, account_payg)

        ctx = PipelineContext(
            router_request=req,
            account=account_payg,
            request_id="r-1",
        )
        await stage.apply(ctx)

        assert ctx.decision is not None
        assert ctx.decision.primary.id == direct.primary.id
        assert [m.id for m in ctx.decision.fallback_chain] == [m.id for m in direct.fallback_chain]
        assert ctx.decision.strategy_used == direct.strategy_used
        assert ctx.decision.category == direct.category

    @pytest.mark.asyncio
    async def test_raises_when_router_not_injected(
        self,
        account_payg: AccountContext,
    ) -> None:
        stage = BaseRouterStage()  # no router
        ctx = _make_ctx(account=account_payg)
        with pytest.raises(RuntimeError, match="router is not initialised"):
            await stage.apply(ctx)

    @pytest.mark.asyncio
    async def test_skipped_when_short_circuit(
        self,
        router: Router,
        account_payg: AccountContext,
    ) -> None:
        stage = BaseRouterStage(router=router)
        ctx = _make_ctx(account=account_payg)
        ctx.short_circuit = True

        await stage.apply(ctx)

        assert ctx.decision is None  # base router skipped


# ---------------------------------------------------------------------------
# 4. RouterPipeline orchestration
# ---------------------------------------------------------------------------


class TestRouterPipeline:
    @pytest.mark.asyncio
    async def test_full_pipeline_produces_decision(
        self,
        router: Router,
        account_payg: AccountContext,
    ) -> None:
        pipeline = RouterPipeline(router)
        ctx = _make_ctx(account=account_payg, model_tag="auto:cheap")

        result = await pipeline.run(ctx)

        assert isinstance(result, PipelineResult)
        assert result.decision is not None
        assert result.decision.primary is not None

    @pytest.mark.asyncio
    async def test_pipeline_executes_all_stages_in_order(
        self,
        router: Router,
        account_payg: AccountContext,
    ) -> None:
        pipeline = RouterPipeline(router)
        ctx = _make_ctx(account=account_payg)

        result = await pipeline.run(ctx)

        # All six stages must have run (no short-circuit in S1).
        assert result.stages_executed == [
            "semantic_cache",
            "budget_filter",
            "sticky_session",
            "custom_rules",
            "base_router",
            "provider_call",
        ]

    @pytest.mark.asyncio
    async def test_pipeline_default_flags_are_clean(
        self,
        router: Router,
        account_payg: AccountContext,
    ) -> None:
        """In S1 no stage should flip cache_hit / rule_id / routed_from."""
        pipeline = RouterPipeline(router)
        ctx = _make_ctx(account=account_payg)

        result = await pipeline.run(ctx)

        assert result.cache_hit is False
        assert result.rule_id is None
        assert result.routed_from is None

    @pytest.mark.asyncio
    async def test_pipeline_with_custom_stage_list(
        self,
        router: Router,
        account_payg: AccountContext,
    ) -> None:
        """Tests can pass a stage subset (e.g. only BaseRouter)."""
        pipeline = RouterPipeline(router, stages=[BaseRouterStage()])
        ctx = _make_ctx(account=account_payg)

        result = await pipeline.run(ctx)

        assert result.stages_executed == ["base_router"]
        assert result.decision is not None

    @pytest.mark.asyncio
    async def test_pipeline_raises_without_base_router_stage(
        self,
        router: Router,
        account_payg: AccountContext,
    ) -> None:
        """Misconfigured pipeline (no BaseRouter) surfaces loudly."""
        pipeline = RouterPipeline(
            router,
            stages=[SemanticCacheStage(), BudgetFilterStage()],  # no base router
        )
        ctx = _make_ctx(account=account_payg)

        with pytest.raises(RuntimeError, match="BaseRouterStage"):
            await pipeline.run(ctx)

    @pytest.mark.asyncio
    async def test_short_circuit_stops_at_first_stage(
        self,
        router: Router,
        account_payg: AccountContext,
    ) -> None:
        """A stage flipping short_circuit halts execution but still
        requires either decision or cache_hit — set both here to test
        only the short-circuit semantics.
        """

        class ShortCircuitStage:
            name = "short_circuit_test"

            async def apply(self, ctx: PipelineContext) -> None:
                ctx.cache_hit = True
                ctx.short_circuit = True
                # Provide a synthetic decision so the pipeline doesn't
                # raise its "no decision" guard.
                req = RouterRequest(model_tag="auto:cheap", estimated_input_tokens=10)
                ctx.decision = await router.route_request(req, ctx.account)

        pipeline = RouterPipeline(
            router,
            stages=[ShortCircuitStage(), BaseRouterStage(router=router)],
        )
        ctx = _make_ctx(account=account_payg)

        result = await pipeline.run(ctx)

        assert result.cache_hit is True
        assert result.stages_executed == ["short_circuit_test"]


# ---------------------------------------------------------------------------
# 5. Regression baseline — pipeline vs v1 for representative scenarios
# ---------------------------------------------------------------------------


class TestRegressionBaselineV2EqualsV1:
    """For every input, the pipeline output MUST equal the v1 direct output.

    This is the contract that makes S1 a "scaffolding without behaviour
    change" change. If a future S2-S4 stage accidentally becomes
    non-NO-OP, these tests turn red.
    """

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("model_tag", "tokens"),
        [
            ("auto", 100),
            ("auto:cheap", 100),
            ("auto:smart", 500),
            ("auto:fast", 200),
            ("auto:cheap", 50_000),  # long context
        ],
    )
    async def test_auto_strategies_identical(
        self,
        router: Router,
        account_payg: AccountContext,
        model_tag: str,
        tokens: int,
    ) -> None:
        req = RouterRequest(
            model_tag=model_tag,
            estimated_input_tokens=tokens,
            prompt_text="hello world",
        )

        # v1 direct
        v1 = await router.route_request(req, account_payg)

        # v2 pipeline
        pipeline = RouterPipeline(router)
        ctx = PipelineContext(
            router_request=req,
            account=account_payg,
            request_id="r-test",
        )
        v2 = (await pipeline.run(ctx)).decision

        assert v2 is not None
        assert v2.primary.id == v1.primary.id
        assert [m.id for m in v2.fallback_chain] == [m.id for m in v1.fallback_chain]
        assert v2.strategy_used == v1.strategy_used
        assert v2.category == v1.category
        assert v2.reason == v1.reason

    @pytest.mark.asyncio
    async def test_pinned_model_identical(
        self,
        router: Router,
        account_payg: AccountContext,
    ) -> None:
        """Pin a model and assert pipeline pins it too."""
        req = RouterRequest(
            model_tag="cheap-2-openai",  # exists in synthetic catalog
            estimated_input_tokens=100,
            prompt_text="hello",
        )

        v1 = await router.route_request(req, account_payg)

        pipeline = RouterPipeline(router)
        ctx = PipelineContext(
            router_request=req,
            account=account_payg,
            request_id="r-pinned",
        )
        v2 = (await pipeline.run(ctx)).decision

        assert v2 is not None
        assert v2.primary.id == v1.primary.id == "cheap-2-openai"

    @pytest.mark.asyncio
    async def test_routing_error_propagates_through_pipeline(
        self,
        router: Router,
        account_payg: AccountContext,
    ) -> None:
        """An unknown model in v1 stays an unknown model in v2."""
        req = RouterRequest(
            model_tag="model-that-does-not-exist",
            estimated_input_tokens=10,
            prompt_text="hi",
        )

        pipeline = RouterPipeline(router)
        ctx = PipelineContext(
            router_request=req,
            account=account_payg,
            request_id="r-bad",
        )

        with pytest.raises(RoutingError):
            await pipeline.run(ctx)


# ---------------------------------------------------------------------------
# 6. Header parser
# ---------------------------------------------------------------------------


class TestParseBudgetHeader:
    @pytest.mark.parametrize(
        ("input_", "expected"),
        [
            (None, None),
            ("", None),
            ("   ", None),
            ("100", 100),
            (" 42 ", 42),
            ("0", None),  # zero rejected — equivalent to no budget
            ("-5", None),  # negative rejected
            ("not-an-int", None),
            ("12.5", None),  # float rejected
            ("100000", 100_000),
        ],
    )
    def test_parses_correctly(self, input_: str | None, expected: int | None) -> None:
        assert parse_budget_kopecks_header(input_) == expected


# ---------------------------------------------------------------------------
# 7. ``should_use_pipeline`` semantics
# ---------------------------------------------------------------------------


class TestShouldUsePipeline:
    @pytest.mark.parametrize(
        ("env_flag", "account_flag", "expected"),
        [
            (False, False, False),
            (False, True, True),
            (True, False, True),
            (True, True, True),
        ],
    )
    def test_env_or_account_semantics(
        self, env_flag: bool, account_flag: bool, expected: bool
    ) -> None:
        assert should_use_pipeline(env_flag=env_flag, account_flag=account_flag) is expected


# ---------------------------------------------------------------------------
# 8. PipelineContext sanity
# ---------------------------------------------------------------------------


class TestPipelineContext:
    def test_account_id_shortcut(self, account_payg: AccountContext) -> None:
        ctx = _make_ctx(account=account_payg)
        assert ctx.account_id == account_payg.account_id

    def test_model_hint_shortcut(self, account_payg: AccountContext) -> None:
        ctx = _make_ctx(account=account_payg, model_tag="auto:smart")
        assert ctx.model_hint == "auto:smart"

    def test_defaults_are_sane(self, account_payg: AccountContext) -> None:
        ctx = _make_ctx(account=account_payg)
        assert ctx.decision is None
        assert ctx.cache_hit is False
        assert ctx.cached_response is None
        assert ctx.routed_from is None
        assert ctx.rule_id is None
        assert ctx.short_circuit is False
        assert ctx.metadata == {}
        assert ctx.session_id is None
        assert ctx.budget_kopecks is None
        assert ctx.task_hint is None


# ---------------------------------------------------------------------------
# 9. Pipeline metadata fields
# ---------------------------------------------------------------------------


class TestPipelineResultMetadata:
    @pytest.mark.asyncio
    async def test_metadata_dict_is_independent_per_request(
        self,
        router: Router,
        account_payg: AccountContext,
    ) -> None:
        """Two consecutive runs share neither context nor metadata."""
        pipeline = RouterPipeline(router)
        ctx1 = _make_ctx(account=account_payg)
        ctx2 = _make_ctx(account=account_payg)
        ctx1.metadata["test_key"] = "ctx1-value"

        await pipeline.run(ctx1)
        result2 = await pipeline.run(ctx2)

        assert "test_key" not in result2.metadata

    @pytest.mark.asyncio
    async def test_result_metadata_is_dict_copy_not_reference(
        self,
        router: Router,
        account_payg: AccountContext,
    ) -> None:
        """Mutating ``result.metadata`` does NOT touch ``ctx.metadata``."""
        pipeline = RouterPipeline(router)
        ctx = _make_ctx(account=account_payg)
        ctx.metadata["key"] = "value"

        result = await pipeline.run(ctx)
        result.metadata["new_key"] = "new_value"

        assert "new_key" not in ctx.metadata
