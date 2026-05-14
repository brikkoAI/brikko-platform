"""PipelineContext — the shared state object that flows through ``RouterPipeline``.

Sprint S1 — Smart Router v2 scaffolding (see
``docs/superpowers/specs/2026-05-12-smart-router-v2-design.md`` §2).

Why a dataclass and not a plain dict
------------------------------------

The pipeline has six stages (semantic cache → budget → sticky → custom rules
→ base router → provider call). Each stage adds a small, well-defined piece
of information to the context. Using ``dict[str, Any]`` would let typos
slip through and silently produce wrong routing decisions. A frozen
dataclass with ``replace()`` semantics is just as cheap and gives mypy +
ruff a fighting chance.

The class is intentionally **mutable** (no ``frozen=True``) — stages flip
booleans like ``cache_hit`` or set ``selected_model`` in-place. That keeps
the stage signatures readable (``apply(ctx) -> None``) and avoids the
plumbing of returning a brand-new copy six times per request.

S1 scope
--------

In Sprint S1 only ``base_router`` is a real stage. The other five
(semantic cache, budget, sticky, custom rules, provider call) are NO-OP
placeholders so the pipeline machinery is in place but behaviour stays
identical to v1. S2-S4 fill those out — see the design doc.

References
----------

* Design doc: ``docs/superpowers/specs/2026-05-12-smart-router-v2-design.md``
* Existing v1 router: ``voltari_gateway/router/router.py``
* Existing failover engine: ``voltari_gateway/router/failover.py``
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from voltari_gateway.router.router import (
    AccountContext,
    RouterRequest,
    RoutingDecision,
)


@dataclass(slots=True)
class PipelineContext:
    """Mutable per-request state passed between pipeline stages.

    Lifecycle:

    1. Constructed by the caller (typically the API handler) with the
       routing inputs (``router_request``, ``account``) and any
       optional headers (``session_id``, ``budget_kopecks``).
    2. Each stage mutates the context in place. Stages MAY short-circuit
       the pipeline by setting ``short_circuit=True`` — the orchestrator
       stops calling later stages but still records the partial result.
    3. After ``RouterPipeline.run()`` the orchestrator returns a
       ``PipelineResult`` (see ``pipeline.py``) carrying the final
       ``RoutingDecision`` and the metadata stages collected.

    Field groupings:

    * Inputs (set once at construction)
        - ``router_request`` — the v1 ``RouterRequest`` dataclass
        - ``account`` — the v1 ``AccountContext`` dataclass
        - ``request_id`` — correlation id for logs / metrics
        - ``session_id`` — optional sticky-session key (S3)
        - ``budget_kopecks`` — optional per-request cap (S2)

    * Stage outputs (mutated by stages)
        - ``decision`` — populated by ``BaseRouterStage`` (S1)
        - ``cache_hit`` — set by ``SemanticCacheStage`` (S3)
        - ``cached_response`` — populated on cache hit (S3)
        - ``routed_from`` — original model when failover swapped (S2 polish)
        - ``rule_id`` — id of the custom rule that fired, if any (S4)
        - ``short_circuit`` — stop running further stages
        - ``metadata`` — free-form bag for forward-compat headers / audit

    The ``metadata`` dict lets new stages stash arbitrary keys without
    growing this class on every sprint. Convention: namespace keys with
    ``{stage}.{key}`` (e.g. ``cache.hit_count``, ``budget.cheapest_kop``).
    """

    # ---- inputs -----------------------------------------------------
    router_request: RouterRequest
    account: AccountContext
    request_id: str

    # Optional inputs surfaced by future stages. Defaults preserve v1
    # behaviour (no sticky, no budget filter, no custom rules).
    session_id: str | None = None
    budget_kopecks: int | None = None
    task_hint: str | None = None

    # ---- stage outputs ----------------------------------------------
    decision: RoutingDecision | None = None
    cache_hit: bool = False
    cached_response: Any = None
    routed_from: str | None = None  # original primary if failover swapped
    rule_id: uuid.UUID | None = None  # populated by CustomRulesStage (S4)
    short_circuit: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def account_id(self) -> uuid.UUID:
        """Convenience shortcut — many stages need just the account id."""
        return self.account.account_id

    @property
    def model_hint(self) -> str:
        """The model tag the client asked for (``auto:*`` / pinned)."""
        return self.router_request.model_tag


__all__ = ["PipelineContext"]
