"""``RouterStage`` protocol — every pipeline stage implements this.

Sprint S1.

A stage is an async callable-like object with two responsibilities:

1. Inspect the ``PipelineContext`` and decide whether to act.
2. Mutate the context in place (set ``ctx.decision``, ``ctx.cache_hit``,
   ``ctx.short_circuit``, etc.). Return value is None.

Why ``async def apply`` and not ``__call__``
--------------------------------------------

Stages will eventually do I/O (Redis lookups, jsonschema validation,
DB reads). Forcing an async signature now means we never have to break
the protocol later when a stage grows real work. The S1 NO-OPs are
trivially async (one ``return`` statement) so the overhead is in the
order of nanoseconds.

The named method (``apply``) instead of ``__call__`` is purely a
style choice — it reads more clearly in pipeline traces (
``stage.SemanticCache.apply``) and pairs nicely with the dataclass
pattern stages use for state.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from voltari_gateway.router.context import PipelineContext


@runtime_checkable
class RouterStage(Protocol):
    """Protocol every Smart Router v2 stage MUST implement.

    Methods
    -------
    name : str
        Short identifier used in metrics labels and audit logs. MUST be
        a stable lowercase snake_case string (e.g. ``semantic_cache``,
        ``budget_filter``). Don't change it after a stage is in
        production — it's a metric label.
    apply : async callable
        Mutates ``ctx`` in place. Raises only on programmer errors
        (bad input) — runtime stage failures should fall back to
        no-op semantics so the pipeline always reaches ``BaseRouter``.

    The ``runtime_checkable`` decorator lets tests assert with
    ``isinstance(stage, RouterStage)`` without a registry boilerplate.
    """

    name: str

    async def apply(self, ctx: PipelineContext) -> None: ...


__all__ = ["RouterStage"]
