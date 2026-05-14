"""SemanticCacheStage — short-circuit on cached responses.

**Sprint S1: NO-OP placeholder.**

Real implementation lands in Sprint S3 (see design doc §3.1). The S3
hash-backed Redis cache will:

* Compute ``SHA-256(account_id || model_id || normalised_prompt ||
  catalog_version)`` as the lookup key.
* Return the cached response and set ``ctx.cache_hit = True`` +
  ``ctx.cached_response = ...`` + ``ctx.short_circuit = True``.
* Respect ``x-no-cache: true`` header (caller already strips that
  before constructing the context — TBD in S3).

Why a NO-OP today
-----------------

Sprint S1's goal is the orchestrator scaffolding without behaviour
change. Including this stage now means the pipeline contract is
stable from day-one — S3 only edits this single file plus its tests.
"""

from __future__ import annotations

from dataclasses import dataclass

from voltari_gateway.router.context import PipelineContext
from voltari_gateway.router.stages.base import RouterStage


@dataclass(slots=True)
class SemanticCacheStage(RouterStage):
    """S3 placeholder. Currently does nothing.

    Returning early on cache hit (the real S3 behaviour) is the entire
    point of this stage — but the cache itself is S3 scope.
    """

    name: str = "semantic_cache"

    async def apply(self, ctx: PipelineContext) -> None:
        """No-op in S1. S3 will implement the real cache lookup."""
        return None


__all__ = ["SemanticCacheStage"]
