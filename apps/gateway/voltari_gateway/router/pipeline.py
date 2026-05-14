"""RouterPipeline — Smart Router v2 orchestrator.

Sprint S1 — scaffolding only. See
``docs/superpowers/specs/2026-05-12-smart-router-v2-design.md`` for the
full v2 design and S1 scope statement.

S1 behaviour summary
--------------------

With ``settings.smart_router_v2_enabled = False`` (production default) the
API handler calls the legacy ``router_engine.route_request(...)`` path
verbatim and ``RouterPipeline`` is never instantiated.

With the flag on, the API handler builds a ``PipelineContext`` and calls
``RouterPipeline.run(ctx)``. The pipeline executes six stages in order
(see ``stages/__init__.py``). In S1 only ``BaseRouterStage`` does real
work — the other five are NO-OP placeholders. The net effect is that
``ctx.decision`` after the pipeline equals what v1 would have produced
for the same inputs (verified by regression tests).

Per-account opt-in
------------------

CEO answer to design-doc Q7: rollout is "flag-off prod + per-account
admin flip". So:

* ``SMART_ROUTER_V2_ENABLED`` (env) defaults to ``false`` in production
  and is the global kill-switch.
* ``Account.smart_router_v2_enabled`` (DB column, migration 0022) is
  the per-account opt-in.
* ``should_use_pipeline()`` returns True iff EITHER the env flag is
  on OR the account flag is on. Env-flag does NOT override account
  flag (env is the "global enable for everyone"; account-flag is the
  "opt-in this one account").

Pipeline composition
--------------------

The pipeline is instantiated once at app startup with the v1 Router
engine injected into ``BaseRouterStage``. Stages themselves are
stateless (read from ``ctx``, write to ``ctx``) so the pipeline is
thread-safe and can be reused across concurrent requests.

Observability
-------------

Each stage call is wrapped with a structured-log entry tagged
``stage=<name>`` so an operator can see exactly which stage short-
circuited a request. We deliberately don't add a per-stage metric in
S1 — until stages do real work the metric is just noise; S2 onwards
will add ``brikko_router_stage_latency_seconds`` histograms.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import structlog

from voltari_gateway.router.context import PipelineContext
from voltari_gateway.router.router import (
    AccountContext,
    RoutingDecision,
)
from voltari_gateway.router.router import (
    Router as RouterEngine,
)
from voltari_gateway.router.stages import default_stages
from voltari_gateway.router.stages.base import RouterStage
from voltari_gateway.router.stages.base_router import BaseRouterStage

log = structlog.get_logger(__name__)


@dataclass(slots=True)
class PipelineResult:
    """The output of ``RouterPipeline.run()``.

    Kept distinct from ``RoutingDecision`` because v2 surfaces extra
    metadata (cache hit / rule id / routed_from) that v1's decision
    doesn't carry. In S1 only ``decision`` is populated; later sprints
    fill in the optional fields.

    The ``stages_executed`` list is the sequence of stage names that
    actually ran (i.e., stages between the start and the
    ``short_circuit`` flip). Useful for tests + dashboards.
    """

    decision: RoutingDecision
    cache_hit: bool = False
    rule_id: str | None = None
    routed_from: str | None = None
    stages_executed: list[str] = field(default_factory=list)
    metadata: dict[str, object] = field(default_factory=dict)


class RouterPipeline:
    """Orchestrates the six-stage Smart Router v2 pipeline.

    Construction
    ------------

    ``RouterPipeline(router_engine)`` builds the default S1 pipeline
    (six stages, BaseRouterStage wired to ``router_engine``). Tests
    can pass a custom ``stages`` list for stage-by-stage assertions.

    Concurrency
    -----------

    Both the pipeline and its stages are stateless — they read from
    and write to the per-request ``PipelineContext``. Safe to share
    a single ``RouterPipeline`` instance across all FastAPI workers
    (typically attached to ``app.state.router_pipeline`` in
    ``main.lifespan``).
    """

    def __init__(
        self,
        router_engine: RouterEngine,
        *,
        stages: list[RouterStage] | None = None,
    ) -> None:
        self._stages: list[RouterStage] = stages if stages is not None else default_stages()
        # Wire the v1 router into the BaseRouterStage. We do this here
        # rather than in default_stages() so tests can also call
        # default_stages() to inspect the structure without an engine.
        for stage in self._stages:
            if isinstance(stage, BaseRouterStage):
                stage.router = router_engine
        self._router_engine = router_engine

    @property
    def stages(self) -> list[RouterStage]:
        """Return the stage list — read-only by convention."""
        return list(self._stages)

    async def run(self, ctx: PipelineContext) -> PipelineResult:
        """Execute every stage in order, stopping on ``ctx.short_circuit``.

        Stage exceptions are NOT caught here: a programmer error in a
        stage MUST surface to the caller (the API handler) so it can
        translate to an HTTP error. The S2-S4 stages will catch their
        own runtime failures (Redis down, jsonschema invalid) and
        downgrade to no-op so we never block routing on cache trouble.

        Returns
        -------
        PipelineResult
            Carries the final ``RoutingDecision`` plus any metadata
            stages collected on ``ctx``.

        Raises
        ------
        RuntimeError
            If no stage produced a ``RoutingDecision`` — that's a
            scaffolding bug (BaseRouterStage was missing), not a
            normal failure mode. The API handler treats it as 500.
        """
        executed: list[str] = []
        for stage in self._stages:
            if ctx.short_circuit:
                log.debug(
                    "pipeline.short_circuited",
                    request_id=ctx.request_id,
                    stopped_at=stage.name,
                )
                break
            await stage.apply(ctx)
            executed.append(stage.name)

        if ctx.decision is None and not ctx.cache_hit:
            # Hot failure mode: BaseRouterStage didn't run (probably a
            # mis-configured pipeline). Surface loudly.
            raise RuntimeError(
                "RouterPipeline finished without producing a RoutingDecision — "
                "is BaseRouterStage in the stage list?"
            )

        # In S1 ``ctx.decision`` is always populated (no cache stage
        # short-circuits yet). The `assert` is a guard for mypy: the
        # ``if`` above already raised RuntimeError if both decision and
        # cache_hit are missing.
        assert ctx.decision is not None
        return PipelineResult(
            decision=ctx.decision,
            cache_hit=ctx.cache_hit,
            rule_id=str(ctx.rule_id) if ctx.rule_id is not None else None,
            routed_from=ctx.routed_from,
            stages_executed=executed,
            metadata=dict(ctx.metadata),
        )


def parse_budget_kopecks_header(value: str | None) -> int | None:
    """Parse the ``x-budget-kopecks`` header into an int or None.

    Helper shared by ``api/chat.py``, ``api/messages.py``, and
    ``api/playground.py`` so all three endpoints handle the header
    identically when v2 is enabled. Returns ``None`` for missing,
    malformed, or non-positive values — the S2 budget filter treats
    ``None`` as "no budget cap" and a strict 4xx for malformed
    headers is its job to add when the stage is non-NO-OP.
    """
    if not value:
        return None
    try:
        parsed = int(value.strip())
    except ValueError:
        return None
    return parsed if parsed > 0 else None


def should_use_pipeline(
    *,
    env_flag: bool,
    account_flag: bool,
) -> bool:
    """Return True iff the v2 pipeline should run for this request.

    Logic per CEO Q7 answer:

    * ``env_flag=True``  → all requests use pipeline (global enable).
    * ``env_flag=False``, ``account_flag=True`` → this account uses pipeline.
    * Both False → legacy v1 path.

    The function is intentionally pure (no DB / Redis I/O) so the API
    handler can call it on every request without overhead.
    """
    return bool(env_flag or account_flag)


__all__ = [
    "AccountContext",
    "PipelineContext",
    "PipelineResult",
    "RouterPipeline",
    "parse_budget_kopecks_header",
    "should_use_pipeline",
]
