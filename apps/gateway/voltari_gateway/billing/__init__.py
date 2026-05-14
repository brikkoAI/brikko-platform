"""Billing module — pricing helpers.

The smart router, debit-account stored procedure, autorefill, YooKassa
integration and per-tariff rate-limits live in sibling files (engine.py,
yookassa.py, receipts.py).

The single public helper here is ``compute_cost_kopecks`` — the chat
handler calls it after every request to write a real number into
``usage_events``. ``MARKUP`` is the one place to change the gateway
markup (currently 1.15 per BRIEF §7).

Math contract (TD-008, Sprint 3 Поток H):
    All money math goes through :class:`decimal.Decimal` with
    :data:`decimal.ROUND_HALF_EVEN` (banker's rounding) — the same rule
    Stripe and most accounting systems use. The previous implementation
    multiplied through ``float`` and did ``round()``; on millions of
    cheap calls the per-call sub-kopeck float drift could accumulate to
    real разница и расхождение баланса в копейках на 100k+ операциях.

    ROUND_HALF_EVEN is unbiased over many transactions (vs. ROUND_HALF_UP
    which always rounds .5 up and over time accrues a systematic upward
    bias), which is the rule a bookkeeper expects.
"""

from __future__ import annotations

from decimal import ROUND_HALF_EVEN, Decimal

from voltari_gateway.providers.base import ChatCompletionUsage
from voltari_gateway.router.catalog import ModelSpec

# BRIEF §7 — gateway adds 15% over provider COGS. Stored as Decimal so we
# never multiply Decimal × float (which raises TypeError) and never
# silently coerce to float.
MARKUP: Decimal = Decimal("1.15")
_KOPECK_QUANTUM: Decimal = Decimal("1")
_TOKENS_PER_KOP_UNIT: Decimal = Decimal("1000")


def compute_cost_kopecks(model: ModelSpec, usage: ChatCompletionUsage) -> int:
    """Per-request cost the customer is charged, in kopecks.

    Cached tokens are billed at the cheaper "cached_input" rate per provider.
    Markup is applied on top of COGS. ``model.effective_pricing`` is used so
    Google's tiered pricing (>200k input) is honoured automatically.

    All arithmetic is done in :class:`Decimal`. The final value is quantised
    to whole kopecks with :data:`ROUND_HALF_EVEN` (banker's rounding) so we
    don't systematically over- or under-charge over many requests.
    """
    in_kop, cached_kop, out_kop = model.effective_pricing(usage.prompt_tokens)
    non_cached_input = max(0, usage.prompt_tokens - usage.cached_tokens)

    input_cost = Decimal(non_cached_input) * Decimal(in_kop) / _TOKENS_PER_KOP_UNIT
    cached_cost = Decimal(usage.cached_tokens) * Decimal(cached_kop) / _TOKENS_PER_KOP_UNIT
    output_cost = Decimal(usage.completion_tokens) * Decimal(out_kop) / _TOKENS_PER_KOP_UNIT

    cogs = input_cost + cached_cost + output_cost
    total = cogs * MARKUP
    return int(total.quantize(_KOPECK_QUANTUM, rounding=ROUND_HALF_EVEN))


__all__ = ["MARKUP", "compute_cost_kopecks"]
