"""StickySessionStage — pin a conversation to the same provider across turns.

**Sprint S1: NO-OP placeholder.**

Real implementation lands in Sprint S3 (see design doc §3.3). The S3
sticky session stage will:

* Look up ``brikko:sticky:{account_id}:{session_id}`` in Redis
  (TTL 600s, refreshed on every hit).
* On hit AND the model is still in catalog AND not in
  ``req.exclude_providers`` AND fits the request: pin the cached
  model as ``ctx.router_request.model_tag`` so ``BaseRouterStage``
  treats it as an explicit pin.
* On miss: leave the request alone; ``BaseRouterStage`` routes
  normally and a downstream hook writes the chosen model to the
  sticky key (write-back happens outside the pipeline, in the
  API-layer success handler).

Granularity (CEO answer to Q5): **per ``session_id``**, not per
``account_id + task_category`` — explicit session id matches what
clients already pass.

Privacy (CEO answer to Q6): per-account scope only, key shape
includes ``account_id`` so cross-account leakage is impossible.

Why a NO-OP today
-----------------

Same as the other placeholders. The Redis key shape is fixed in
the design doc; S3 only needs to implement the read/write here.
"""

from __future__ import annotations

from dataclasses import dataclass

from voltari_gateway.router.context import PipelineContext
from voltari_gateway.router.stages.base import RouterStage


@dataclass(slots=True)
class StickySessionStage(RouterStage):
    """S3 placeholder. Currently does nothing."""

    name: str = "sticky_session"

    async def apply(self, ctx: PipelineContext) -> None:
        """No-op in S1. S3 will implement the Redis sticky lookup."""
        return None


__all__ = ["StickySessionStage"]
