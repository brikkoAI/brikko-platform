"""CustomRulesStage — per-account JSON rule engine for routing overrides.

**Sprint S1: NO-OP placeholder.**

Real implementation lands in Sprint S4 (see design doc §3.5). The S4
custom rules engine will:

* Load enabled rules from ``account_routing_rules`` (cached on the
  ``Principal`` with 60s TTL, same pattern as routing prefs).
* Walk rules ordered by ``priority ASC``, evaluate the first match.
* Apply the matched rule's actions to ``ctx.router_request``:
    - ``force_model``  → rewrite ``model_tag`` to a specific id
    - ``set_strategy`` → rewrite ``model_tag`` to ``auto:<strategy>``
    - ``exclude_providers`` → add to ``router_request.exclude_providers``
    - ``set_budget_kop`` → set ``ctx.budget_kopecks``
    - ``reject``       → raise ``CustomRuleRejectError`` → HTTP 422
* Record the firing rule id in ``ctx.rule_id`` for audit
  (``gateway_request_log.metadata.routing_rule_id``).

DSL is **JSON-only**, whitelisted primitives — CEO answer to Q4. No
Python lambdas, no YAML.

Why a NO-OP today
-----------------

The rules engine is the largest single feature in v2 (jsonschema
validator, CRUD API, table migration). Putting a placeholder here
locks the pipeline position; the S4 PR will replace this one file
with the real engine.
"""

from __future__ import annotations

from dataclasses import dataclass

from voltari_gateway.router.context import PipelineContext
from voltari_gateway.router.stages.base import RouterStage


@dataclass(slots=True)
class CustomRulesStage(RouterStage):
    """S4 placeholder. Currently does nothing."""

    name: str = "custom_rules"

    async def apply(self, ctx: PipelineContext) -> None:
        """No-op in S1. S4 will implement the per-account rules engine."""
        return None


__all__ = ["CustomRulesStage"]
