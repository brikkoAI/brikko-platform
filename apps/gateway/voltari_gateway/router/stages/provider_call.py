"""ProviderCallStage — placeholder for future provider-invocation hook.

**Sprint S1: NO-OP placeholder. The actual provider call still lives in
``api/chat.py`` + ``with_failover`` and is untouched in S1.**

The design doc lists six stages and reserves the sixth slot for the
provider invocation itself. In S1 we keep the provider call in the
API layer because moving it into the pipeline would require:

* Re-hosting billing pre-flight / post-flight inside the pipeline.
* Re-hosting PII masking and unmasking (which currently sits between
  the routing decision and the provider call).
* Re-hosting streaming response generation + cancel handling.
* Re-hosting the ``compute_cost_kopecks`` + ``UsageEvent`` writes.

That is roughly 600 LOC of well-tested code that S1 has no reason to
touch. The slot exists so future sprints can lift the call into the
pipeline incrementally (likely S5 along with observability polish).

In S1 the API handler reads ``ctx.decision`` after ``pipeline.run()``
returns, then continues with its existing failover + billing logic —
exactly the v1 code path.
"""

from __future__ import annotations

from dataclasses import dataclass

from voltari_gateway.router.context import PipelineContext
from voltari_gateway.router.stages.base import RouterStage


@dataclass(slots=True)
class ProviderCallStage(RouterStage):
    """S5+ placeholder. NO-OP — provider call still lives in api/chat.py."""

    name: str = "provider_call"

    async def apply(self, ctx: PipelineContext) -> None:
        """No-op in S1. Provider invocation remains in the API layer."""
        return None


__all__ = ["ProviderCallStage"]
