"""Routing strategies for the Voltari smart router.

Each strategy is a pure function that takes a `TaskCategory` plus context
size (and a few constraints) and returns an ordered list of `ModelSpec`
— the *primary* candidate first, then preferred fallbacks. The router
layer assembles these into a final fallback chain with cross-provider
preference.

Design notes:

* Strategies are deterministic and side-effect-free → trivial to test
  and to reason about under load. No I/O, no logging side-effects.
* Categorisation (`categorize_request`) is intentionally primitive —
  regex + length thresholds. We are NOT building a classifier in the
  hot path; mistakes degrade to a higher-tier model, never to an
  outright failure. Trade-off: we'll miss some "code" requests that
  arrive without code fences. Acceptable for MVP — clients who care
  pin the model explicitly.
* Pricing uses a 70/30 input/output blend (per
  `03_Finance/01_cogs_models.md` §3) so a model that is cheap on input
  but expensive on output (e.g. GPT-5.4 mini at 0.75/4.50) is scored
  realistically.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from enum import StrEnum

from voltari_gateway.router.catalog import (
    CATALOG,
    ModelSpec,
    ModelTier,
    Provider,
)


class Strategy(StrEnum):
    """Auto-routing strategies exposed to clients via `model: "auto:..."`."""

    CHEAP = "cheap"  # default — minimise weighted price
    SMART = "smart"  # quality/price optimum
    FAST = "fast"  # minimise p50 latency
    RU_LEGAL = "ru-legal"  # only RU-hosted providers (152-FZ-friendly)
    # Sprint 11.6 — coding-agent stack (Cursor / Claude Code / Codex CLI).
    # Hand-curated chain that prefers Anthropic Sonnet (SWE-bench leader as
    # of 2026-04 per public leaderboards). Distinct from `smart` because
    # the smart score blends price into the rank and would pick DeepSeek
    # V4-Pro for "code" requests — fine for snippets, sub-par for whole-
    # repo agents that benefit from Sonnet's tool-use stability.
    CODE = "code"


class TaskCategory(StrEnum):
    """Coarse buckets used to filter candidates before scoring.

    These are NOT user-visible. They exist to keep the cheap strategy
    from picking a 32k-context budget model for a 100k-token RAG dump.
    """

    CHAT = "chat"  # default conversational
    REASONING = "reasoning"  # CoT-style requests; benefits from o-series
    CODE = "code"  # code generation/review; benefits from Sonnet/o3
    LONG_CONTEXT = "long_context"  # >50k input tokens


# ---------------------------------------------------------------------------
# Categorisation — primitive heuristics. We bias toward CHAT (the cheapest
# default) and only escalate when there's a strong textual signal. False
# negatives just mean a slightly cheaper model handles the request.
# ---------------------------------------------------------------------------

# Markdown code fence (```py ... ```) or 4+ leading spaces over multiple lines.
_CODE_FENCE_RE = re.compile(r"```[\s\S]*?```|(?:^[ ]{4,}.*\n){3,}", re.MULTILINE)

# CoT triggers — case-insensitive. Conservative list to avoid false positives.
_REASONING_PHRASES: tuple[str, ...] = (
    "think step by step",
    "let's think step by step",
    "chain of thought",
    "reason carefully",
    "show your reasoning",
    "step-by-step",
    "пошагово рассуждай",
    "рассуждай по шагам",
    "цепочка рассуждений",
)

# Threshold above which we route to long-context-capable models. Picked at
# 50k because it's the smallest context that exceeds Yandex's 32k window —
# any query over this needs to skip the RU-budget tier anyway.
LONG_CONTEXT_TOKEN_THRESHOLD: int = 50_000


def categorize_request(
    *,
    text: str,
    estimated_input_tokens: int,
    reasoning_effort: str | None = None,
) -> TaskCategory:
    """Classify the request into one of four task categories.

    Order of checks matters: long-context wins because it's a hard
    capability filter; reasoning/code are softer scoring hints. The
    `reasoning_effort` arg mirrors OpenAI's `reasoning_effort` field
    (`"low" | "medium" | "high"`) — if the client set it, we honour it
    regardless of text content.
    """
    if estimated_input_tokens >= LONG_CONTEXT_TOKEN_THRESHOLD:
        return TaskCategory.LONG_CONTEXT

    if reasoning_effort and reasoning_effort.lower() in {"medium", "high"}:
        return TaskCategory.REASONING

    if not text:
        return TaskCategory.CHAT

    # Cheap to scan — we're already past pydantic validation, so text is
    # bounded by `messages[].content` size limits (200k chars max).
    lower = text.lower()
    if any(phrase in lower for phrase in _REASONING_PHRASES):
        return TaskCategory.REASONING

    if _CODE_FENCE_RE.search(text):
        return TaskCategory.CODE

    return TaskCategory.CHAT


# ---------------------------------------------------------------------------
# Filtering — common pre-step shared by all strategies.
# ---------------------------------------------------------------------------

# Provider preference per category. Drives tie-breaking inside the smart
# strategy (and acts as a soft filter for `code`/`reasoning`). Conservative:
# we only EXCLUDE models that are demonstrably weak (e.g. RU-budget on code).
# Explicit include-list is brittle as the catalogue grows — instead we
# express "avoid these tiers/providers" via a small set of denylists.
_DENY_PROVIDERS_FOR_CATEGORY: dict[TaskCategory, frozenset[Provider]] = {
    TaskCategory.CHAT: frozenset(),
    TaskCategory.REASONING: frozenset(),  # We let the smart score handle it
    # RU providers don't beat Western models on code generation in our probes
    # (2026-04 internal evals); we keep them out of the *default* code chain
    # but a client can still pin them via explicit `model:`.
    TaskCategory.CODE: frozenset({Provider.YANDEX, Provider.SBER}),
    TaskCategory.LONG_CONTEXT: frozenset(),
}


def _eligible_models(
    category: TaskCategory,
    ctx_size: int,
    *,
    require_ru_legal: bool = False,
    require_tools: bool = False,
    exclude_providers: frozenset[Provider] = frozenset(),
    catalog: Iterable[ModelSpec] = CATALOG,
) -> list[ModelSpec]:
    """Return models that *can* handle this request at all.

    Filters: context window, RU-legal flag, tools support, category
    denylist, caller exclusions. We add a 1k-token safety buffer to the
    context check because the user's `max_tokens` output is not
    counted in the input estimate.
    """
    deny = _DENY_PROVIDERS_FOR_CATEGORY[category] | exclude_providers
    out: list[ModelSpec] = []
    for m in catalog:
        if m.provider in deny:
            continue
        if require_ru_legal and not m.ru_legal:
            continue
        if require_tools and not m.supports_tools:
            continue
        if m.context_window < ctx_size + 1_000:
            continue
        out.append(m)
    return out


def _weighted_price_kop_per_1k(m: ModelSpec) -> float:
    """Price under a 70/30 input/output mix.

    We use float here only for sort keys — the result never propagates
    into billing. Catalog uses ints; this function exists purely so two
    models can be compared on a single scalar.
    """
    return 0.7 * m.input_price_kop_per_1k + 0.3 * m.output_price_kop_per_1k


# ---------------------------------------------------------------------------
# Strategy implementations — each returns an ordered list, primary first.
# Empty list means "no eligible model"; the router converts that into a
# 4xx response so we never end up calling a wrong-tier model by accident.
# ---------------------------------------------------------------------------


def select_cheap(
    category: TaskCategory,
    ctx_size: int,
    *,
    require_ru_legal: bool = False,
    require_tools: bool = False,
    exclude_providers: frozenset[Provider] = frozenset(),
    catalog: Iterable[ModelSpec] = CATALOG,
) -> list[ModelSpec]:
    """Cheapest-first ordering by 70/30 weighted price.

    Tie-break: higher quality score, then alphabetical id. Determinism
    matters for debug-ability — the same request must produce the same
    primary across pods.
    """
    eligible = _eligible_models(
        category,
        ctx_size,
        require_ru_legal=require_ru_legal,
        require_tools=require_tools,
        exclude_providers=exclude_providers,
        catalog=catalog,
    )
    eligible.sort(
        key=lambda m: (_weighted_price_kop_per_1k(m), -m.quality_score, m.id),
    )
    return eligible


def select_smart(
    category: TaskCategory,
    ctx_size: int,
    *,
    require_ru_legal: bool = False,
    require_tools: bool = False,
    exclude_providers: frozenset[Provider] = frozenset(),
    catalog: Iterable[ModelSpec] = CATALOG,
) -> list[ModelSpec]:
    """Quality-per-kopeck ordering.

    For `chat` and `code` we deliberately exclude the PREMIUM tier
    (o-series, Opus) — they are 5–10× the price for marginal gains on
    everyday traffic. A client who needs them pins the model
    explicitly. For REASONING we keep PREMIUM — that's literally the
    point of the category.

    Trade-off: this is opinionated and could surprise a Pro user who
    sets `auto:smart` and gets Sonnet instead of Opus on a coding task.
    Mitigation: the answer header (`x-router-decision`) names the model
    and reason; the docs explain it; a one-line query parameter
    overrides it.
    """
    eligible = _eligible_models(
        category,
        ctx_size,
        require_ru_legal=require_ru_legal,
        require_tools=require_tools,
        exclude_providers=exclude_providers,
        catalog=catalog,
    )
    if category in (TaskCategory.CHAT, TaskCategory.CODE):
        eligible = [m for m in eligible if m.tier != ModelTier.PREMIUM]

    # Score: quality_score / weighted_price. We add 0.001 to price to
    # avoid div-by-zero (no model is actually free, but it's defensive)
    # and use stable id-tiebreak for determinism.
    eligible.sort(
        key=lambda m: (
            -m.quality_score / (_weighted_price_kop_per_1k(m) + 0.001),
            m.id,
        ),
    )
    return eligible


def select_fast(
    category: TaskCategory,
    ctx_size: int,
    *,
    require_ru_legal: bool = False,
    require_tools: bool = False,
    exclude_providers: frozenset[Provider] = frozenset(),
    catalog: Iterable[ModelSpec] = CATALOG,
) -> list[ModelSpec]:
    """Latency-first ordering.

    Tie-break: weighted price (cheaper wins among equally fast models),
    then id. We do NOT exclude any tier — sometimes the fastest path is
    a flagship via a less-loaded region.
    """
    eligible = _eligible_models(
        category,
        ctx_size,
        require_ru_legal=require_ru_legal,
        require_tools=require_tools,
        exclude_providers=exclude_providers,
        catalog=catalog,
    )
    eligible.sort(
        key=lambda m: (m.latency_p50_ms, _weighted_price_kop_per_1k(m), m.id),
    )
    return eligible


# ---------------------------------------------------------------------------
# select_code — coding-agent stack (Sprint 11.6, Brikko-as-Cursor-backend).
# ---------------------------------------------------------------------------

# Hand-curated coding-stack ordering. Order = preference at primary slot.
# Rationale (snapshot 2026-05-02; refresh quarterly with public bench data):
# * claude-sonnet-4.6  — SWE-bench leader, best tool-use stability, 1M ctx,
#                        Anthropic prompt-caching is a 10× cost lever for
#                        agents reusing the same system prompt.
# * deepseek-v4-pro    — strong cost/quality on code, 1M ctx, native tools.
# * gpt-5.4            — flagship OpenAI, robust function calling, 400k ctx.
# * gemini-3.1-pro     — 1M ctx, decent on code; here as last resort because
#                        published SWE-bench numbers trail the trio above.
_CODE_STACK_ORDER: tuple[str, ...] = (
    "claude-sonnet-4.6",
    "deepseek-v4-pro",
    "gpt-5.4",
    "gemini-3.1-pro",
)

# Above this input size we promote gemini-3.1-pro to primary because the
# trio's effective context drops below the request — Sonnet 4.6 is 1M but
# its quality degrades past ~500k in our internal probes; Gemini holds up
# better at the 200k–1M range. Keep Sonnet as fallback because it's still
# the SWE-bench leader on the slice that fits.
LONG_CODE_PROJECT_TOKEN_THRESHOLD: int = 200_000
_LONG_CODE_STACK_ORDER: tuple[str, ...] = (
    "gemini-3.1-pro",
    "claude-sonnet-4.6",
    "deepseek-v4-pro",
    "gpt-5.4",
)


def select_code(
    category: TaskCategory,
    ctx_size: int,
    *,
    require_ru_legal: bool = False,
    require_tools: bool = False,
    exclude_providers: frozenset[Provider] = frozenset(),
    catalog: Iterable[ModelSpec] = CATALOG,
) -> list[ModelSpec]:
    """Hand-curated coding-stack: Sonnet → DeepSeek V4 Pro → GPT-5.4 → Gemini.

    Rationale: smart-strategy's quality/price score picks DeepSeek for code
    because DeepSeek is cheaper. That's correct for one-shot snippets but
    wrong for repo-scale coding agents (Cursor / Claude Code / Codex CLI)
    where Sonnet's SWE-bench numbers and tool-use stability matter more
    than the per-1k price.

    Long-context projects (>200k tokens) flip primary to ``gemini-3.1-pro``
    because Gemini holds quality better past 500k than Sonnet does in our
    probes, and the 200k threshold matches Google's higher-tier pricing
    cliff (so we're already paying Google premium rates anyway).

    Russian models are intentionally NOT in this chain — Yandex/Sber don't
    have a coding-grade model in the MVP catalogue. ``require_ru_legal=True``
    therefore returns an empty list, and the caller surfaces a 422
    ``no_eligible_model`` rather than silently picking a non-RU provider.
    """
    # Hard guard: no Russian model in MVP matches western coding quality.
    # Returning [] keeps the contract predictable — clients with an RU-legal
    # account who request auto:code get a clear 422 instead of a silent
    # downgrade to GigaChat (which would confuse their compliance audit).
    if require_ru_legal:
        return []

    # Build the eligible set the same way every other strategy does, so the
    # caller's exclude_providers / require_tools / context filters apply
    # uniformly. We then sort by hand-curated preference rather than price.
    eligible = _eligible_models(
        category,
        ctx_size,
        require_ru_legal=False,  # explicit — guard above already returned
        require_tools=require_tools,
        exclude_providers=exclude_providers,
        catalog=catalog,
    )
    if not eligible:
        return []

    by_id: dict[str, ModelSpec] = {m.id: m for m in eligible}

    # Pick the hand-curated order. Long-project flip is purely cosmetic if
    # gemini-3.1-pro happens to be filtered out (e.g. exclude_providers), so
    # we still walk both orderings and dedupe at the end.
    if ctx_size > LONG_CODE_PROJECT_TOKEN_THRESHOLD:
        primary_order = _LONG_CODE_STACK_ORDER
    else:
        primary_order = _CODE_STACK_ORDER

    out: list[ModelSpec] = []
    seen: set[str] = set()
    for mid in primary_order:
        m = by_id.get(mid)
        if m is None or mid in seen:
            continue
        out.append(m)
        seen.add(mid)

    # Top up with any other eligible models so cross-provider failover still
    # has options if the curated four are excluded. Keep them in
    # weighted-price order (cheapest first) — these are last-resort entries.
    leftovers = [m for m in eligible if m.id not in seen]
    leftovers.sort(key=lambda m: (_weighted_price_kop_per_1k(m), -m.quality_score, m.id))
    out.extend(leftovers)
    return out


def select_ru_legal(
    category: TaskCategory,
    ctx_size: int,
    *,
    require_tools: bool = False,
    exclude_providers: frozenset[Provider] = frozenset(),
    catalog: Iterable[ModelSpec] = CATALOG,
) -> list[ModelSpec]:
    """RU-legal-only ordering by weighted price.

    Used by clients who must keep PD inside the RU perimeter (152-FZ
    sensitive workloads). Returns Yandex/Sber models only — never
    falls back to OpenAI/Anthropic even if those would be cheaper.

    Trade-off: if both Yandex and Sber are down, we surface a 503
    rather than silently degrading to a non-RU provider — better to
    fail loudly than to leak PD across the border.
    """
    return select_cheap(
        category,
        ctx_size,
        require_ru_legal=True,
        require_tools=require_tools,
        exclude_providers=exclude_providers,
        catalog=catalog,
    )


# ---------------------------------------------------------------------------
# Strategy registry — the router uses this to dispatch by `Strategy`.
# Keeps the router code free of branchy if/elif ladders and makes it
# trivial to plug a new strategy (e.g. `auto:long-context`) later.
# ---------------------------------------------------------------------------


# Each strategy callable accepts the same kwargs; ru_legal is implicit in
# `select_ru_legal` so we wrap to a uniform signature.
def _ru_legal_wrapped(
    category: TaskCategory,
    ctx_size: int,
    *,
    require_ru_legal: bool = False,  # ignored — always True
    require_tools: bool = False,
    exclude_providers: frozenset[Provider] = frozenset(),
    catalog: Iterable[ModelSpec] = CATALOG,
) -> list[ModelSpec]:
    return select_ru_legal(
        category,
        ctx_size,
        require_tools=require_tools,
        exclude_providers=exclude_providers,
        catalog=catalog,
    )


STRATEGIES = {
    Strategy.CHEAP: select_cheap,
    Strategy.SMART: select_smart,
    Strategy.FAST: select_fast,
    Strategy.RU_LEGAL: _ru_legal_wrapped,
    Strategy.CODE: select_code,
}


__all__ = [
    "LONG_CODE_PROJECT_TOKEN_THRESHOLD",
    "LONG_CONTEXT_TOKEN_THRESHOLD",
    "STRATEGIES",
    "Strategy",
    "TaskCategory",
    "categorize_request",
    "select_cheap",
    "select_code",
    "select_fast",
    "select_ru_legal",
    "select_smart",
]
