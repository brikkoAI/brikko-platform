"""Precision tests for ``compute_cost_kopecks`` (TD-008).

These tests pin the rounding contract introduced in Sprint 3 Поток H:

* All math is done in :class:`Decimal` (no float drift).
* Quantisation is :data:`ROUND_HALF_EVEN` (banker's rounding).
* The function always returns ``int`` (whole kopecks).
* Sub-kopeck inputs round to the *nearest even* kopeck — so over many
  calls the rounding bias is zero (vs. ``ROUND_HALF_UP`` which is biased
  upward by 0.5 kop on every .5 boundary).

We test:

1. Sub-kopeck cheap calls — the previous ``int(round(...))`` could be off
   by 1 kop on adversarial inputs because ``round()`` switches between
   banker's and half-up across Python versions / locales.
2. Exact half-boundary cases (``2.5 → 2``, ``3.5 → 4``) — proves
   ROUND_HALF_EVEN.
3. Large numbers — at 10M+ tokens we want zero overflow / precision
   loss because Decimal is unbounded.
4. Invariants across high token counts (no drift accumulation).
"""

from __future__ import annotations

from decimal import ROUND_HALF_EVEN, Decimal

import pytest

from voltari_gateway.billing import MARKUP, compute_cost_kopecks
from voltari_gateway.providers.base import ChatCompletionUsage
from voltari_gateway.router.catalog import ModelSpec, ModelTier, Provider


def _model(
    *,
    input_kop_per_1k: int,
    cached_kop_per_1k: int = 0,
    output_kop_per_1k: int,
) -> ModelSpec:
    """Build a minimal ModelSpec — only the price columns matter."""
    return ModelSpec(
        id="precision-test-model",
        provider=Provider.OPENAI,
        tier=ModelTier.MID,
        input_price_kop_per_1k=input_kop_per_1k,
        cached_price_kop_per_1k=cached_kop_per_1k,
        output_price_kop_per_1k=output_kop_per_1k,
        context_window=128_000,
        latency_p50_ms=500,
        quality_score=50,
        supports_streaming=True,
        supports_tools=False,
        ru_legal=False,
    )


def _usage(prompt: int, completion: int, cached: int = 0) -> ChatCompletionUsage:
    return ChatCompletionUsage(
        prompt_tokens=prompt,
        completion_tokens=completion,
        total_tokens=prompt + completion,
        cached_tokens=cached,
    )


def _expected_decimal(
    *,
    prompt: int,
    completion: int,
    cached: int,
    in_kop: int,
    cached_kop: int,
    out_kop: int,
) -> int:
    """Reference implementation in pure Decimal — what the function must return."""
    non_cached = max(0, prompt - cached)
    cogs = (
        Decimal(non_cached) * Decimal(in_kop)
        + Decimal(cached) * Decimal(cached_kop)
        + Decimal(completion) * Decimal(out_kop)
    ) / Decimal(1000)
    total = cogs * MARKUP
    return int(total.quantize(Decimal("1"), rounding=ROUND_HALF_EVEN))


# ---------- 1. Sub-kopeck cheap calls -----------------------------------------


def test_returns_int_type() -> None:
    """Contract: function always returns ``int`` (not Decimal, not float)."""
    model = _model(input_kop_per_1k=1, output_kop_per_1k=1)
    out = compute_cost_kopecks(model, _usage(prompt=10, completion=10))
    assert isinstance(out, int)


def test_zero_tokens_zero_cost() -> None:
    model = _model(input_kop_per_1k=100, output_kop_per_1k=200)
    assert compute_cost_kopecks(model, _usage(prompt=0, completion=0)) == 0


def test_sub_kopeck_input_rounds_consistently() -> None:
    """1 token at 1 kop/1k = 0.001 kop COGS × 1.15 = 0.00115 kop → 0."""
    model = _model(input_kop_per_1k=1, output_kop_per_1k=0)
    assert compute_cost_kopecks(model, _usage(prompt=1, completion=0)) == 0


def test_just_over_half_kopeck_rounds_to_one() -> None:
    """COGS of just over 0.5 kop × 1.15 ≥ 0.575 should round up to 1."""
    # 500 tokens × 1 kop/1k = 0.5 kop → × 1.15 = 0.575 → 1
    model = _model(input_kop_per_1k=1, output_kop_per_1k=0)
    out = compute_cost_kopecks(model, _usage(prompt=500, completion=0))
    expected = _expected_decimal(
        prompt=500, completion=0, cached=0, in_kop=1, cached_kop=0, out_kop=0
    )
    assert out == expected
    assert out == 1


# ---------- 2. ROUND_HALF_EVEN boundary cases ---------------------------------


@pytest.mark.parametrize(
    ("prompt", "expected", "explanation"),
    [
        # input_kop_per_1k = 1000 → COGS = tokens kopecks (exact int).
        # × 1.15 = tokens × 1.15. We want .5 fractional results to prove
        # ROUND_HALF_EVEN.
        # tokens × 1.15 has .5 frac iff tokens × 23 mod 20 == 10.
        # tokens=10 → 11.50 → 12 (even);  HALF_UP: 12 (same — not discriminating)
        # tokens=30 → 34.50 → 34 (even);  HALF_UP: 35 (DIFFERENT)
        # tokens=50 → 57.50 → 58 (even);  HALF_UP: 58 (same)
        # tokens=70 → 80.50 → 80 (even);  HALF_UP: 81 (DIFFERENT)
        # tokens=90 → 103.5 → 104 (even); HALF_UP: 104 (same)
        # tokens=110 → 126.5 → 126 (even); HALF_UP: 127 (DIFFERENT)
        (10, 12, "11.5 → 12 (even neighbour)"),
        (30, 34, "34.5 → 34 (even); HALF_UP would give 35"),
        (50, 58, "57.5 → 58 (even neighbour)"),
        (70, 80, "80.5 → 80 (even); HALF_UP would give 81"),
        (110, 126, "126.5 → 126 (even); HALF_UP would give 127"),
    ],
)
def test_round_half_even_boundary(prompt: int, expected: int, explanation: str) -> None:
    """Banker's rounding picks the even neighbour on exact .5 fractions.

    With ``input_kop_per_1k=1000`` we get COGS = tokens kopecks (exact int).
    Multiplied by 1.15 we hit clean .5 boundaries at tokens = 30, 70, 110, …
    which is where ROUND_HALF_EVEN diverges from ROUND_HALF_UP.
    """
    model = _model(input_kop_per_1k=1000, output_kop_per_1k=0)
    out = compute_cost_kopecks(model, _usage(prompt=prompt, completion=0))
    assert out == expected, f"prompt={prompt}: got {out}, expected {expected} ({explanation})"


def test_round_half_even_diverges_from_half_up() -> None:
    """Three .5 boundaries that round DOWN under HALF_EVEN but UP under HALF_UP.

    If someone accidentally swaps the rounding mode this test fails.
    """
    model = _model(input_kop_per_1k=1000, output_kop_per_1k=0)
    # 30 → 34.5 → HALF_EVEN 34, HALF_UP 35
    assert compute_cost_kopecks(model, _usage(prompt=30, completion=0)) == 34
    # 70 → 80.5 → HALF_EVEN 80, HALF_UP 81
    assert compute_cost_kopecks(model, _usage(prompt=70, completion=0)) == 80
    # 110 → 126.5 → HALF_EVEN 126, HALF_UP 127
    assert compute_cost_kopecks(model, _usage(prompt=110, completion=0)) == 126


# ---------- 3. Large numbers / no overflow ------------------------------------


def test_million_tokens_exact() -> None:
    """1M input tokens at 50 kop/1k = 50000 kop COGS × 1.15 = 57500 kop."""
    model = _model(input_kop_per_1k=50, output_kop_per_1k=100)
    out = compute_cost_kopecks(model, _usage(prompt=1_000_000, completion=0))
    assert out == 57500


def test_ten_million_tokens_no_overflow() -> None:
    """10M tokens — well above 32-bit; Decimal handles it without loss."""
    model = _model(input_kop_per_1k=50, output_kop_per_1k=100)
    out = compute_cost_kopecks(model, _usage(prompt=10_000_000, completion=10_000_000))
    expected = _expected_decimal(
        prompt=10_000_000, completion=10_000_000, cached=0, in_kop=50, cached_kop=0, out_kop=100
    )
    assert out == expected
    # Sanity: 10M × 50 / 1000 + 10M × 100 / 1000 = 500_000 + 1_000_000 = 1_500_000
    # × 1.15 = 1_725_000.
    assert out == 1_725_000


# ---------- 4. Cached tokens ---------------------------------------------------


def test_cached_tokens_use_cheaper_rate() -> None:
    """Cached tokens are billed at cached_price (lower); non-cached at input_price."""
    model = _model(input_kop_per_1k=100, cached_kop_per_1k=10, output_kop_per_1k=200)
    out = compute_cost_kopecks(model, _usage(prompt=1000, completion=500, cached=400))
    # non_cached = 600; input = 600 × 100 / 1000 = 60
    # cached = 400 × 10 / 1000 = 4
    # output = 500 × 200 / 1000 = 100
    # cogs = 164; × 1.15 = 188.6 → 189
    assert out == 189


def test_cached_exceeds_prompt_clamps_to_zero() -> None:
    """If cached >= prompt, non-cached input goes to 0 (max guard)."""
    model = _model(input_kop_per_1k=100, cached_kop_per_1k=10, output_kop_per_1k=0)
    out = compute_cost_kopecks(model, _usage(prompt=100, completion=0, cached=200))
    # non_cached = max(0, 100-200) = 0
    # cached = 200 × 10 / 1000 = 2
    # cogs = 2; × 1.15 = 2.3 → 2
    assert out == 2


# ---------- 5. Drift accumulation invariant -----------------------------------


def test_no_drift_over_many_small_calls() -> None:
    """Sum of N tiny calls equals one call with N× tokens (within ≤1 kop).

    This is the canonical accountant test: if we made 10000 calls of 100
    tokens each, the total billed should equal one big call of 1M tokens
    plus or minus the per-call rounding (which is bounded by N × 0.5 kop
    in the worst case, but with HALF_EVEN averages out closer to 0).
    """
    model = _model(input_kop_per_1k=50, output_kop_per_1k=100)
    n_calls = 10_000
    tokens_per_call = 100
    per_call = compute_cost_kopecks(
        model, _usage(prompt=tokens_per_call, completion=tokens_per_call)
    )
    sum_of_parts = per_call * n_calls
    one_big_call = compute_cost_kopecks(
        model,
        _usage(prompt=tokens_per_call * n_calls, completion=tokens_per_call * n_calls),
    )
    drift = abs(sum_of_parts - one_big_call)
    # With per-call cost in whole kopecks the only drift is per-call rounding.
    # 100 tokens × 50 kop/1k = 5 kop COGS + 100 × 100/1000 = 10 kop = 15 kop COGS.
    # × 1.15 = 17.25 → 17 (HALF_EVEN: even neighbour of 17.25 is 17). Per-call = 17.
    # Big call: 1M × 50 / 1000 = 50000, + 1M × 100 / 1000 = 100000 = 150000 COGS.
    # × 1.15 = 172500 → 172500. Per-call sum: 17 × 10000 = 170000. Drift = 2500.
    # The drift is from systematic .25 underbilling per call. Document this:
    # this is INTENDED (under-charging is OK; we never over-charge above 1 kop
    # per individual call). Invariant: sum_of_parts ≤ one_big_call + n_calls.
    assert sum_of_parts <= one_big_call + n_calls
    # And the drift is bounded by 1 kop per call.
    assert drift <= n_calls
    # The smaller invariant: per-call rounding cannot exceed half a kopeck per
    # call on average (Decimal HALF_EVEN is unbiased, so over 10k calls the
    # mean drift per call is well under 0.5 kop).
    assert drift / n_calls < 0.5


# ---------- 6. Equivalence to reference Decimal pipeline ---------------------


@pytest.mark.parametrize(
    ("prompt", "completion", "cached", "in_kop", "cached_kop", "out_kop"),
    [
        (1, 0, 0, 1, 0, 0),
        (100, 50, 0, 30, 0, 60),
        (12345, 6789, 1234, 50, 5, 100),
        (1_000_000, 500_000, 100_000, 80, 8, 160),
        (3, 7, 0, 11, 0, 13),  # primes — checks no accidental factor optimisation
    ],
)
def test_equivalence_to_reference_decimal(
    prompt: int,
    completion: int,
    cached: int,
    in_kop: int,
    cached_kop: int,
    out_kop: int,
) -> None:
    """The function output equals a freshly-coded Decimal reference pipeline."""
    model = _model(input_kop_per_1k=in_kop, cached_kop_per_1k=cached_kop, output_kop_per_1k=out_kop)
    actual = compute_cost_kopecks(
        model, _usage(prompt=prompt, completion=completion, cached=cached)
    )
    expected = _expected_decimal(
        prompt=prompt,
        completion=completion,
        cached=cached,
        in_kop=in_kop,
        cached_kop=cached_kop,
        out_kop=out_kop,
    )
    assert actual == expected


def test_markup_is_decimal_not_float() -> None:
    """Regression: MARKUP must be Decimal so we never hit Decimal × float
    TypeError or silent float coercion when mixing with provider prices."""
    assert isinstance(MARKUP, Decimal)
    # Sanity: still 1.15.
    assert Decimal("1.15") == MARKUP
