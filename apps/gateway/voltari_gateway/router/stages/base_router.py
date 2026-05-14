"""BaseRouterStage — wraps the existing v1 ``Router.route_request``.

**Sprint S1: ACTIVE stage (the only real work in S1).**

Why this is a wrapper, not a rewrite
------------------------------------

The v1 ``Router.route_request`` already does all the heavy lifting:

* Auto-tag parsing (``auto:cheap`` / ``auto:smart`` / ...).
* Account-level constraints (``require_ru_legal``, routing whitelist).
* Strategy → ordered candidate list.
* Daily-budget soft degrade.
* Cross-provider fallback chain construction.
* Pinned-model context-window check.

S1's job is **scaffolding without behaviour change**. So this stage
calls v1 verbatim and stores the result on ``ctx.decision``. S2-S4
will then have hook points BEFORE this stage (budget filter, sticky
session, custom rules) to mutate ``ctx.router_request`` so v1 sees a
modified input but its own decision logic stays untouched.

Regression-safety
-----------------

The pipeline test suite parametrises 5+ representative routing
scenarios and asserts that for every input, the pipeline's
``ctx.decision`` (after running through all stages) is byte-equal
to ``v1_router.route_request(...)`` called directly. This guards
against accidental behaviour drift if a later stage's NO-OP becomes
non-trivial.
"""

from __future__ import annotations

from dataclasses import dataclass

from voltari_gateway.router.context import PipelineContext
from voltari_gateway.router.router import Router as RouterEngine
from voltari_gateway.router.stages.base import RouterStage


@dataclass(slots=True)
class BaseRouterStage(RouterStage):
    """Delegates to the v1 ``Router.route_request`` and stores the decision.

    The ``router`` attribute is injected by ``RouterPipeline.__init__``
    (which receives the singleton ``RouterEngine`` from
    ``app.state.router_engine`` — same instance the v1 code path uses).
    Tests can inject a ``Router`` over a synthetic catalog.

    If ``ctx.short_circuit`` is True (e.g. a future cache hit), this
    stage skips routing entirely. In S1 nothing sets short_circuit so
    BaseRouter always runs.
    """

    router: RouterEngine | None = None
    name: str = "base_router"

    async def apply(self, ctx: PipelineContext) -> None:
        """Run v1 router and stash the decision on ``ctx.decision``."""
        if ctx.short_circuit:
            # A previous stage already produced a result (S3 cache hit).
            return
        if self.router is None:
            # Defensive: pipeline construction should always inject one.
            # Raising here is preferable to silently using a fresh
            # default-catalog Router because that would swallow account
            # config mismatches.
            raise RuntimeError(
                "BaseRouterStage.router is not initialised — "
                "RouterPipeline must inject the v1 RouterEngine on construct."
            )

        decision = await self.router.route_request(ctx.router_request, ctx.account)
        ctx.decision = decision


__all__ = ["BaseRouterStage"]
