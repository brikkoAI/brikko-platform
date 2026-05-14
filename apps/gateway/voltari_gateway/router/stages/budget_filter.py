"""BudgetFilterStage — filter candidate models by per-request budget cap.

**Sprint S1: NO-OP placeholder.**

Real implementation lands in Sprint S2 (see design doc §3.4). The S2
budget filter will:

* Read ``ctx.budget_kopecks`` (populated from ``x-budget-kopecks``
  header or ``extra_body.budget_kopecks`` body field — CEO answer to
  Q3 was "accept both, header takes precedence").
* After ``BaseRouterStage`` produces a candidate set, drop models
  whose ``expected_cost_kop(input, output)`` exceeds the budget.
* If no model fits, raise a ``BudgetError`` translated to HTTP 402
  ``no_model_in_budget`` by the API layer.

Tariff tiering for the timeout (CEO answer to Q2: 5/3/2s for
Free/Pro/Team+) is a separate concern and lives in
``StickySessionStage``/``ProviderCallStage`` scope; this stage only
deals with cost ceilings.

Why a NO-OP today
-----------------

Same rationale as the other S2-S4 placeholders: lock the stage
position in the pipeline contract so the S2 PR is a single-file diff.
"""

from __future__ import annotations

from dataclasses import dataclass

from voltari_gateway.router.context import PipelineContext
from voltari_gateway.router.stages.base import RouterStage


@dataclass(slots=True)
class BudgetFilterStage(RouterStage):
    """S2 placeholder. Currently passes the context through unchanged."""

    name: str = "budget_filter"

    async def apply(self, ctx: PipelineContext) -> None:
        """No-op in S1. S2 will implement candidate filtering by cost cap."""
        return None


__all__ = ["BudgetFilterStage"]
