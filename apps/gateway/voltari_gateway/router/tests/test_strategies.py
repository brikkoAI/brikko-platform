"""Strategy-level tests — pure functions, no async.

Coverage:
* Each `select_*` returns a deterministic ordering.
* Cheap picks the lowest weighted-price model.
* Smart avoids PREMIUM tier for plain chat / code.
* Fast picks the lowest p50 latency.
* RU-legal returns only Yandex/Sber.
* Long-context filters out short-context models.
* Categorisation routes CoT-marker text to REASONING.
* Categorisation routes code-fenced text to CODE.
"""

from __future__ import annotations

import pytest

from voltari_gateway.router.catalog import CATALOG, ModelTier, Provider
from voltari_gateway.router.strategies import (
    LONG_CONTEXT_TOKEN_THRESHOLD,
    TaskCategory,
    categorize_request,
    select_cheap,
    select_code,
    select_fast,
    select_ru_legal,
    select_smart,
)

# ---------------------------------------------------------------------------
# categorize_request
# ---------------------------------------------------------------------------


class TestCategorize:
    def test_default_is_chat(self) -> None:
        assert (
            categorize_request(text="привет, как дела?", estimated_input_tokens=10)
            is TaskCategory.CHAT
        )

    def test_long_context_overrides_everything(self) -> None:
        assert (
            categorize_request(
                text="```python\ndef x(): pass\n```",
                estimated_input_tokens=LONG_CONTEXT_TOKEN_THRESHOLD + 1,
            )
            is TaskCategory.LONG_CONTEXT
        )

    def test_reasoning_effort_overrides_text(self) -> None:
        assert (
            categorize_request(text="hi", estimated_input_tokens=5, reasoning_effort="high")
            is TaskCategory.REASONING
        )

    def test_low_reasoning_effort_does_not_force_reasoning(self) -> None:
        # "low" is OpenAI's default-ish; not enough to escalate.
        assert (
            categorize_request(text="hi", estimated_input_tokens=5, reasoning_effort="low")
            is TaskCategory.CHAT
        )

    @pytest.mark.parametrize(
        "phrase",
        [
            "Please think step by step about this.",
            "Let's think step by step.",
            "Сначала пошагово рассуждай, потом отвечай.",
            "Show your reasoning before the answer.",
        ],
    )
    def test_cot_markers_route_to_reasoning(self, phrase: str) -> None:
        assert categorize_request(text=phrase, estimated_input_tokens=20) is TaskCategory.REASONING

    def test_code_fence_routes_to_code(self) -> None:
        text = "Help me debug this:\n```python\ndef f(): return 1\n```"
        assert categorize_request(text=text, estimated_input_tokens=20) is TaskCategory.CODE

    def test_empty_text_short_request_is_chat(self) -> None:
        assert categorize_request(text="", estimated_input_tokens=0) is TaskCategory.CHAT


# ---------------------------------------------------------------------------
# select_cheap
# ---------------------------------------------------------------------------


class TestSelectCheap:
    def test_strategy_cheap_selects_lowest_cost(self, synthetic_catalog) -> None:
        result = select_cheap(TaskCategory.CHAT, ctx_size=1_000, catalog=synthetic_catalog)
        assert result, "should return at least one candidate"
        # cheap-1 has inp=2, out=3 → weighted = 0.7*2 + 0.3*3 = 2.3 (lowest).
        assert result[0].id == "cheap-1"

    def test_cheap_is_deterministic(self, synthetic_catalog) -> None:
        first = select_cheap(TaskCategory.CHAT, ctx_size=1_000, catalog=synthetic_catalog)
        second = select_cheap(TaskCategory.CHAT, ctx_size=1_000, catalog=synthetic_catalog)
        assert [m.id for m in first] == [m.id for m in second]

    def test_cheap_respects_exclude_providers(self, synthetic_catalog) -> None:
        result = select_cheap(
            TaskCategory.CHAT,
            ctx_size=1_000,
            exclude_providers=frozenset({Provider.DEEPSEEK}),
            catalog=synthetic_catalog,
        )
        assert all(m.provider is not Provider.DEEPSEEK for m in result)

    def test_cheap_filters_out_short_context(self, synthetic_catalog) -> None:
        """A 100k-token request should drop yandex-1 (32k context)."""
        result = select_cheap(TaskCategory.CHAT, ctx_size=100_000, catalog=synthetic_catalog)
        assert all(m.context_window >= 101_000 for m in result)
        assert not any(m.id == "yandex-1" for m in result)


# ---------------------------------------------------------------------------
# select_smart
# ---------------------------------------------------------------------------


class TestSelectSmart:
    def test_strategy_smart_avoids_premium_for_simple_chat(self, synthetic_catalog) -> None:
        result = select_smart(TaskCategory.CHAT, ctx_size=1_000, catalog=synthetic_catalog)
        assert all(m.tier is not ModelTier.PREMIUM for m in result)

    def test_smart_keeps_premium_for_reasoning(self, synthetic_catalog) -> None:
        result = select_smart(TaskCategory.REASONING, ctx_size=1_000, catalog=synthetic_catalog)
        # premium-1 (quality=95) should be in the candidate set.
        assert any(m.id == "premium-1" for m in result)

    def test_smart_picks_quality_per_kop(self, synthetic_catalog) -> None:
        # mid-2: q=78, weighted = 0.7*15 + 0.3*80 = 34.5 → 78/34.5 = 2.26
        # cheap-2-openai: q=72, weighted = 0.7*5 + 0.3*10 = 6.5 → 72/6.5 = 11.07
        # cheap-1: q=60, weighted = 2.3 → 60/2.3 = 26.09 (highest score)
        # mid-1: q=85, weighted = 0.7*20 + 0.3*100 = 44 → 85/44 = 1.93
        # Smart should put cheap-1 first by score
        result = select_smart(TaskCategory.CHAT, ctx_size=1_000, catalog=synthetic_catalog)
        assert result[0].id == "cheap-1"


# ---------------------------------------------------------------------------
# select_fast
# ---------------------------------------------------------------------------


class TestSelectFast:
    def test_fast_picks_lowest_p50(self, synthetic_catalog) -> None:
        result = select_fast(TaskCategory.CHAT, ctx_size=1_000, catalog=synthetic_catalog)
        # mid-2 has p50=600ms — fastest in synthetic catalogue.
        assert result[0].id == "mid-2"


# ---------------------------------------------------------------------------
# select_ru_legal
# ---------------------------------------------------------------------------


class TestSelectRuLegal:
    def test_strategy_ru_legal_only_yandex_sber(self, synthetic_catalog) -> None:
        result = select_ru_legal(TaskCategory.CHAT, ctx_size=1_000, catalog=synthetic_catalog)
        assert result, "should have at least one ru-legal model"
        assert all(m.ru_legal for m in result)
        assert all(m.provider in {Provider.YANDEX, Provider.SBER} for m in result)

    def test_ru_legal_returns_empty_when_ctx_exceeds_yandex(self, synthetic_catalog) -> None:
        # yandex-1 = 32k, sber-1 = 131k. 200k request → only sber would pass
        # if it had enough context, but 200k > 131k so sber fails too.
        result = select_ru_legal(TaskCategory.CHAT, ctx_size=200_000, catalog=synthetic_catalog)
        assert result == []


# ---------------------------------------------------------------------------
# Long-context behaviour shared across strategies
# ---------------------------------------------------------------------------


class TestLongContext:
    def test_long_context_filters_out_short_models(self, synthetic_catalog) -> None:
        result = select_cheap(
            TaskCategory.LONG_CONTEXT,
            ctx_size=80_000,
            catalog=synthetic_catalog,
        )
        # Only models with ctx >= 81k survive: long-ctx-1 (1M), mid-2 (128k),
        # mid-1 (128k), cheap-2-openai (128k), cheap-1 (128k), premium-1 (128k).
        # yandex-1 (32k) and sber-1 (131k — >= 81k? yes) — sber-1 passes.
        for m in result:
            assert m.context_window >= 81_000

    def test_smart_long_context_keeps_premium_or_not(self, synthetic_catalog) -> None:
        # Smart still excludes PREMIUM for non-reasoning categories, even
        # for long context. Behaviour is intentional — the fact that the
        # input is long doesn't mean the user wants to pay for o3.
        result = select_smart(
            TaskCategory.LONG_CONTEXT,
            ctx_size=80_000,
            catalog=synthetic_catalog,
        )
        # LONG_CONTEXT is neither CHAT nor CODE — smart keeps premium here.
        # Verify behaviour is documented: at least one premium is allowed.
        # (We don't assert "must include" because the synthetic premium-1
        # has ctx=128k which satisfies 81k.)
        assert any(m.tier is ModelTier.PREMIUM for m in result)


# ---------------------------------------------------------------------------
# select_code  (Sprint 11.6 — Brikko-as-Cursor-backend)
# ---------------------------------------------------------------------------
#
# These tests run against the **real** CATALOG (not the synthetic one)
# because ``select_code`` is a hand-curated list of specific model ids
# (claude-sonnet-4.6, deepseek-v4-pro, gpt-5.4, gemini-3.1-pro). Asserting
# the curated chain against synthetic models would be testing nothing —
# we want to lock in the production primary order.


class TestSelectCode:
    def test_select_code_picks_claude_sonnet_default(self) -> None:
        """Default short-context coding request → Sonnet 4.6 first."""
        result = select_code(TaskCategory.CODE, ctx_size=10_000, catalog=CATALOG)
        assert result, "real catalogue must produce at least one candidate"
        assert result[0].id == "claude-sonnet-4.6"
        # Fallback chain order — the next three are the curated ones.
        ids = [m.id for m in result[:4]]
        assert ids == [
            "claude-sonnet-4.6",
            "deepseek-v4-pro",
            "gpt-5.4",
            "gemini-3.1-pro",
        ]

    def test_select_code_long_context_picks_gemini(self) -> None:
        """Above 200k tokens → Gemini primary (best long-context coding)."""
        result = select_code(TaskCategory.CODE, ctx_size=300_000, catalog=CATALOG)
        assert result, "must keep candidates for a 300k coding request"
        assert result[0].id == "gemini-3.1-pro"
        # Sonnet is still in the chain — kept as the first non-Gemini fallback
        # because SWE-bench leadership stays valuable on the slice that fits.
        assert any(m.id == "claude-sonnet-4.6" for m in result)

    def test_select_code_excludes_providers(self) -> None:
        """Excluding Anthropic → DeepSeek V4 Pro takes the primary slot."""
        result = select_code(
            TaskCategory.CODE,
            ctx_size=10_000,
            exclude_providers=frozenset({Provider.ANTHROPIC}),
            catalog=CATALOG,
        )
        assert result
        assert result[0].id == "deepseek-v4-pro"
        # Sonnet must not appear at all when its provider is excluded.
        assert all(m.provider is not Provider.ANTHROPIC for m in result)

    def test_select_code_ru_legal_returns_empty(self) -> None:
        """No Russian coding model in MVP — RU-legal account gets empty list."""
        result = select_code(
            TaskCategory.CODE,
            ctx_size=10_000,
            require_ru_legal=True,
            catalog=CATALOG,
        )
        assert result == []

    def test_select_code_filters_short_context_models(self) -> None:
        """Insufficient context window → model dropped even if curated."""
        # 500k-token request — gpt-5.4 (400k ctx) cannot hold it, so it
        # must drop out of the chain even though it's in the curated list.
        result = select_code(TaskCategory.CODE, ctx_size=500_000, catalog=CATALOG)
        assert result
        assert all(m.context_window >= 501_000 for m in result)
        assert not any(m.id == "gpt-5.4" for m in result)
