"""Smart Router v2 pipeline stages.

Sprint S1 — see
``docs/superpowers/specs/2026-05-12-smart-router-v2-design.md`` §2 and the
S1 scope discussion in §6.

Stage ordering (fixed by the design):

    1. SemanticCacheStage   — S3 (NO-OP in S1)
    2. BudgetFilterStage    — S2 (NO-OP in S1)
    3. StickySessionStage   — S3 (NO-OP in S1)
    4. CustomRulesStage     — S4 (NO-OP in S1)
    5. BaseRouterStage      — S1 (delegates to v1 ``Router.route_request``)
    6. ProviderCallStage    — reserved, NO-OP in S1 (provider invocation
                              still lives in api/chat.py + with_failover)

Each stage implements the ``RouterStage`` protocol defined in
``stages.base``. The orchestrator (``RouterPipeline``) walks the
list in order, short-circuiting if ``ctx.short_circuit`` is set.

Why a separate package and not all in ``pipeline.py``
-----------------------------------------------------

* Each stage will grow non-trivial: budget filter needs token
  estimation, sticky session needs Redis I/O, custom rules needs a
  jsonschema validator. Keeping them in their own files makes each
  one independently testable and reviewable.
* Sprint S2-S4 each touches exactly one stage file plus its tests —
  small, focused PRs.
"""

from voltari_gateway.router.stages.base import RouterStage
from voltari_gateway.router.stages.base_router import BaseRouterStage
from voltari_gateway.router.stages.budget_filter import BudgetFilterStage
from voltari_gateway.router.stages.custom_rules import CustomRulesStage
from voltari_gateway.router.stages.provider_call import ProviderCallStage
from voltari_gateway.router.stages.semantic_cache import SemanticCacheStage
from voltari_gateway.router.stages.sticky_session import StickySessionStage


def default_stages() -> list[RouterStage]:
    """Return the canonical S1 stage list in execution order.

    Splitting the construction into a helper lets tests build a
    pipeline with a subset (e.g. just BaseRouterStage for regression
    tests) without dragging the no-op stages along.
    """
    return [
        SemanticCacheStage(),
        BudgetFilterStage(),
        StickySessionStage(),
        CustomRulesStage(),
        BaseRouterStage(),
        ProviderCallStage(),
    ]


__all__ = [
    "BaseRouterStage",
    "BudgetFilterStage",
    "CustomRulesStage",
    "ProviderCallStage",
    "RouterStage",
    "SemanticCacheStage",
    "StickySessionStage",
    "default_stages",
]
