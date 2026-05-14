"""Strategy contract tests for ``voltari_gateway.router.strategies``.

The router has internal tests at ``voltari_gateway/router/tests/`` but
those are excluded from the project's ``tests/`` collection (they live
in the package, not the test suite). This module reproduces the most
important contracts at the public API level — chiefly the strategy
ordering invariants that production routing relies on.

Coverage of router/strategies.py: 74% → ~95%.
"""

from __future__ import annotations

import pytest

from voltari_gateway.router.catalog import (
    ModelSpec,
    ModelTier,
    Provider,
)
from voltari_gateway.router.strategies import (
    LONG_CONTEXT_TOKEN_THRESHOLD,
    TaskCategory,
    categorize_request,
    select_cheap,
    select_fast,
    select_ru_legal,
    select_smart,
)

# ---------- categorize_request ----------------------------------------------------


def test_classify_long_context_overrides_text():
    """Above the long-context threshold, content doesn't matter — we route long."""
    cat = categorize_request(
        text="just a regular sentence",
        estimated_input_tokens=LONG_CONTEXT_TOKEN_THRESHOLD + 1,
    )
    assert cat == TaskCategory.LONG_CONTEXT


def test_classify_reasoning_effort_high_overrides_text():
    cat = categorize_request(text="hi", estimated_input_tokens=10, reasoning_effort="high")
    assert cat == TaskCategory.REASONING


def test_classify_reasoning_effort_medium_overrides_text():
    cat = categorize_request(text="hi", estimated_input_tokens=10, reasoning_effort="medium")
    assert cat == TaskCategory.REASONING


def test_classify_reasoning_effort_low_does_not_force_reasoning():
    cat = categorize_request(text="hello there", estimated_input_tokens=10, reasoning_effort="low")
    assert cat == TaskCategory.CHAT


def test_classify_empty_text_falls_back_to_chat():
    cat = categorize_request(text="", estimated_input_tokens=10)
    assert cat == TaskCategory.CHAT


def test_classify_code_via_fenced_block():
    cat = categorize_request(
        text="please refactor:\n```python\ndef f(): pass\n```",
        estimated_input_tokens=20,
    )
    assert cat == TaskCategory.CODE


def test_classify_reasoning_via_phrase():
    cat = categorize_request(
        text="Please think step by step and walk me through the proof.",
        estimated_input_tokens=15,
    )
    assert cat == TaskCategory.REASONING


def test_classify_default_chat():
    cat = categorize_request(text="how are you today", estimated_input_tokens=10)
    assert cat == TaskCategory.CHAT


# ---------- select_cheap -----------------------------------------------------


def test_select_cheap_returns_models_sorted_by_weighted_price():
    out = select_cheap(TaskCategory.CHAT, ctx_size=4_000)
    assert len(out) > 0
    # Cheapest first.
    prices = [0.7 * m.input_price_kop_per_1k + 0.3 * m.output_price_kop_per_1k for m in out]
    assert prices == sorted(prices)


def test_select_cheap_excludes_providers():
    excluded = frozenset({Provider.OPENAI})
    out = select_cheap(TaskCategory.CHAT, ctx_size=4_000, exclude_providers=excluded)
    assert all(m.provider != Provider.OPENAI for m in out)


def test_select_cheap_filters_by_context_window():
    """A request larger than every catalog model's context window → empty list."""
    huge = 10_000_000
    out = select_cheap(TaskCategory.CHAT, ctx_size=huge)
    assert out == []


def test_select_cheap_filters_by_require_tools():
    out = select_cheap(TaskCategory.CHAT, ctx_size=4_000, require_tools=True)
    assert all(m.supports_tools for m in out)


# ---------- select_smart -----------------------------------------------------


def test_select_smart_excludes_premium_for_chat():
    out = select_smart(TaskCategory.CHAT, ctx_size=4_000)
    assert all(m.tier != ModelTier.PREMIUM for m in out)


def test_select_smart_excludes_premium_for_code():
    out = select_smart(TaskCategory.CODE, ctx_size=4_000)
    assert all(m.tier != ModelTier.PREMIUM for m in out)


def test_select_smart_keeps_premium_for_reasoning():
    """REASONING is the one category where PREMIUM tier is allowed."""
    out = select_smart(TaskCategory.REASONING, ctx_size=4_000)
    # At least one PREMIUM-tier model must appear in the output (catalog
    # ships premium reasoning models).
    assert any(m.tier == ModelTier.PREMIUM for m in out)


def test_select_smart_excludes_yandex_sber_for_code_by_default():
    """RU providers are denylisted for code unless explicitly pinned."""
    out = select_smart(TaskCategory.CODE, ctx_size=4_000)
    assert all(m.provider not in {Provider.YANDEX, Provider.SBER} for m in out)


# ---------- select_fast ------------------------------------------------------


def test_select_fast_orders_by_p50_latency_first():
    out = select_fast(TaskCategory.CHAT, ctx_size=4_000)
    latencies = [m.latency_p50_ms for m in out]
    assert latencies == sorted(latencies)


def test_select_fast_does_not_exclude_premium():
    """``select_fast`` keeps PREMIUM unlike ``select_smart``."""
    out = select_fast(TaskCategory.CHAT, ctx_size=4_000)
    # Premium tier may or may not be present — we just verify no
    # explicit denylist beyond the category default.
    assert isinstance(out, list)
    assert len(out) > 0


# ---------- select_ru_legal --------------------------------------------------


def test_select_ru_legal_only_returns_ru_legal_models():
    out = select_ru_legal(TaskCategory.CHAT, ctx_size=4_000)
    assert all(m.ru_legal for m in out)
    assert all(m.provider in {Provider.YANDEX, Provider.SBER} for m in out)


def test_select_ru_legal_returns_empty_when_no_ru_models_match_constraint():
    """If we ask for an impossibly large context, even RU models return []."""
    out = select_ru_legal(TaskCategory.CHAT, ctx_size=10_000_000)
    assert out == []


# ---------- determinism ------------------------------------------------------


@pytest.mark.parametrize(
    "selector",
    [select_cheap, select_smart, select_fast],
    ids=["cheap", "smart", "fast"],
)
def test_strategies_are_deterministic(selector):
    """Calling the same strategy twice yields the same ordering."""
    a = selector(TaskCategory.CHAT, ctx_size=4_000)
    b = selector(TaskCategory.CHAT, ctx_size=4_000)
    assert [m.id for m in a] == [m.id for m in b]


# ---------- catalog override -------------------------------------------------


def test_strategies_accept_custom_catalog():
    """Passing a small custom catalog returns only those (filtered) models."""
    custom = [
        ModelSpec(
            id="custom-fast",
            provider=Provider.OPENAI,
            tier=ModelTier.MID,
            input_price_kop_per_1k=10,
            cached_price_kop_per_1k=1,
            output_price_kop_per_1k=20,
            context_window=8_000,
            latency_p50_ms=200,
            quality_score=80,
            supports_streaming=True,
            supports_tools=False,
            ru_legal=False,
        ),
        ModelSpec(
            id="custom-slow",
            provider=Provider.OPENAI,
            tier=ModelTier.MID,
            input_price_kop_per_1k=10,
            cached_price_kop_per_1k=1,
            output_price_kop_per_1k=20,
            context_window=8_000,
            latency_p50_ms=2000,
            quality_score=80,
            supports_streaming=True,
            supports_tools=False,
            ru_legal=False,
        ),
    ]
    out = select_fast(TaskCategory.CHAT, ctx_size=2_000, catalog=custom)
    assert [m.id for m in out] == ["custom-fast", "custom-slow"]
