"""``recommend_model`` MCP tool — router-driven model recommendation.

**S3 upgrade — replaces the S2 heuristic with a real Router dry-run.**

Why this is a real dry-run, not a heuristic
-------------------------------------------

``Router.route_request`` was designed from day one as a pure, I/O-free
decision engine (see ``voltari_gateway/router/router.py`` docstring).
Given a ``RouterRequest`` + ``AccountContext`` it returns a
``RoutingDecision`` without ever calling out to a provider — the API
layer is responsible for the HTTP. That's exactly the contract a
"recommend a model for this task" MCP tool wants. We don't need to add
a ``dry_run`` flag to the selector — the selector IS the dry-run.

What we feed the router
-----------------------

We synthesise a ``RouterRequest`` from the agent's free-form
``task_description``:

* ``model_tag`` — derived from ``prefer_speed_over_quality``:
  ``auto:fast`` if true, ``auto:smart`` if false (smart is the right
  default; cheap routinely picks a too-small budget model).
* ``estimated_input_tokens`` — len(task)/4 (a "good enough" rule of
  thumb for English; agents pass short task descriptions, so the
  estimate is rarely load-bearing).
* ``prompt_text`` — the task description itself, so the categoriser
  (regex + length thresholds) can pick CODE / REASONING / LONG_CONTEXT.
* ``max_cost_kop`` — set from ``budget_kopecks_per_request`` so the
  router's existing budget filter does the work.

``AccountContext`` comes from the calling principal — we hydrate it
from the DB so per-account routing prefs (``routing_mode``,
``routing_strategy``, allowed providers / models) are honoured.

S2 fallback
-----------

If the router raises ``RoutingError`` (e.g. ``no_eligible_model`` for a
weird budget cap), we surface a clean 422-style error instead of
silently swapping to a heuristic. The agent should narrate "no model
fits your constraints" rather than pretending we found one.

Output shape
------------

* ``recommended_model_id`` — the primary pick id.
* ``reasoning`` — the router's human-readable reason, plus a per-model
  capability hint.
* ``estimated_cost_kopecks`` — from ``ModelSpec.expected_cost_kop``
  with a typical 1k input / 500 output request size.
* ``fallback_chain`` — up to 3 cross-provider fallbacks the router
  picked (S2 didn't expose these; S3 does so an agent can preflight
  outage scenarios).
* ``alternatives`` — same shape as fallback_chain but framed as
  "different trade-off" rather than "outage swap".
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from voltari_gateway.db.models import Account
from voltari_gateway.mcp_server.context import current_principal
from voltari_gateway.router.catalog import CATALOG, ModelSpec
from voltari_gateway.router.router import (
    AccountContext,
    Router,
    RouterRequest,
    RoutingError,
)
from voltari_gateway.utils.errors import GatewayError

NAME = "recommend_model"
DESCRIPTION = (
    "Recommend a Brikko model id for a free-form task description. Calls "
    "the real router engine in dry-run mode (no provider HTTP, no billing), "
    "so the answer matches what the gateway would do for an actual request. "
    "Optional budget_kopecks_per_request narrows to models that fit. "
    "prefer_speed_over_quality flips the strategy from 'smart' to 'fast'."
)
INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "task_description": {
            "type": "string",
            "minLength": 5,
            "maxLength": 2000,
            "description": (
                "Free-form description of the task. The router's categoriser "
                "scans this for code/reasoning/long-context markers, so include "
                "any relevant phrasing (e.g. 'analyze this contract for risks')."
            ),
        },
        "budget_kopecks_per_request": {
            "type": ["integer", "null"],
            "minimum": 1,
            "description": (
                "Optional cost cap in kopecks for a typical ~1.5k-token "
                "request. Models exceeding the cap are dropped from the "
                "candidate list."
            ),
        },
        "prefer_speed_over_quality": {
            "type": "boolean",
            "default": False,
            "description": (
                "When true, the router uses the 'fast' strategy (lowest p50 "
                "latency). Default false → 'smart' strategy."
            ),
        },
    },
    "required": ["task_description"],
    "additionalProperties": False,
}


# Tokens-per-character heuristic. Loose — agents pass short task
# descriptions, so the estimate is rarely load-bearing for routing
# decisions. The categoriser only escalates to LONG_CONTEXT past 50k
# tokens, and 50k×4 = 200KB of text isn't going to fit in
# ``task_description`` anyway (we cap at 2000 chars).
_CHARS_PER_TOKEN = 4

# Typical request size used for the cost estimate we surface back. The
# router uses 1000 output tokens internally for its budget filter, so
# we match that to avoid surprising the agent ("you said 12k kop but
# the router refused because of 22k kop budget filter").
_TYPICAL_INPUT_TOKENS = 1000
_TYPICAL_OUTPUT_TOKENS = 1000


_CATALOG_BY_ID: dict[str, ModelSpec] = {m.id: m for m in CATALOG}


def _estimate_cost_kopecks(spec: ModelSpec) -> int:
    """Approximate kopecks for a single typical request.

    Uses ``expected_cost_kop`` (the same method the router and billing
    layer call), so this number is consistent with what gets charged at
    request time. The router itself uses the same estimate when applying
    ``max_cost_kop``, which keeps the agent's mental model honest.
    """
    return int(
        spec.expected_cost_kop(
            input_tokens=_TYPICAL_INPUT_TOKENS,
            output_tokens=_TYPICAL_OUTPUT_TOKENS,
        )
    )


def _build_account_context(account: Account) -> AccountContext:
    """Hydrate ``AccountContext`` from the live account row.

    ``daily_spent_kop`` is intentionally 0 here — we don't want a
    near-budget-cap account to silently see its recommendation degrade
    to a cheaper model just because the actual request would. The
    recommendation is a planning aid; the actual route at request time
    applies the budget degrade.
    """
    return AccountContext(
        account_id=account.id,
        tariff=account.tariff.value,
        balance_kop=int(account.balance_kopecks),
        require_ru_legal=False,  # explicit user opt-in via task wording, V2
        daily_spent_kop=0,
        daily_budget_kop=None,
        routing_mode=getattr(account, "routing_mode", "smart") or "smart",
        routing_strategy=getattr(account, "routing_strategy", "smart") or "smart",
        routing_allowed_providers=(
            frozenset(getattr(account, "routing_allowed_providers", None) or []) or None
        ),
        routing_allowed_models=(
            frozenset(getattr(account, "routing_allowed_models", None) or []) or None
        ),
    )


def _build_router_request(
    *,
    task: str,
    prefer_speed: bool,
    budget_kopecks: int | None,
) -> RouterRequest:
    """Translate the MCP arguments into the router's dataclass."""
    model_tag = "auto:fast" if prefer_speed else "auto:smart"
    return RouterRequest(
        model_tag=model_tag,
        estimated_input_tokens=max(len(task) // _CHARS_PER_TOKEN, 1),
        prompt_text=task,
        reasoning_effort=None,
        failover_enabled=True,
        max_cost_kop=budget_kopecks,
        require_tools=False,
    )


def _render_alternative(spec: ModelSpec, note: str) -> dict[str, Any]:
    return {
        "model_id": spec.id,
        "provider": spec.provider.value,
        "tier": spec.tier.value,
        "estimated_cost_kopecks": _estimate_cost_kopecks(spec),
        "note": note,
    }


async def handler(arguments: dict[str, Any], db: AsyncSession) -> dict[str, Any]:
    """Run the router in dry-run mode and return its decision."""
    principal = current_principal()

    task = str(arguments.get("task_description", "")).strip()
    if not task or len(task) < 5:
        raise GatewayError(
            status_code=400,
            message="task_description must be at least 5 characters.",
            type="invalid_request_error",
            code="invalid_task_description",
        )

    budget = arguments.get("budget_kopecks_per_request")
    if budget is not None and not isinstance(budget, int):
        raise GatewayError(
            status_code=400,
            message="budget_kopecks_per_request must be an integer or null.",
            type="invalid_request_error",
            code="invalid_budget",
        )
    if isinstance(budget, int) and budget < 1:
        raise GatewayError(
            status_code=400,
            message="budget_kopecks_per_request must be >= 1.",
            type="invalid_request_error",
            code="invalid_budget",
        )

    prefer_speed = bool(arguments.get("prefer_speed_over_quality", False))

    account = await db.get(Account, principal.account_id)
    if account is None:
        raise GatewayError(
            status_code=500,
            message="Account row vanished between auth and tool dispatch.",
            type="internal_error",
            code="account_not_found_post_auth",
        )

    router = Router()
    req = _build_router_request(
        task=task,
        prefer_speed=prefer_speed,
        budget_kopecks=budget,
    )
    ctx = _build_account_context(account)

    try:
        decision = await router.route_request(req, ctx)
    except RoutingError as exc:
        # Common cases:
        #   * no_eligible_model — the budget cap left zero candidates.
        #   * model_not_ru_legal — pinned model conflict (we don't pin).
        # Surface as a structured 422-style error so the agent narrates
        # "no model fits these constraints" instead of inventing one.
        raise GatewayError(
            status_code=422,
            message=f"Router rejected request: {exc.message}",
            type="invalid_request_error",
            code=exc.reason_code,
        ) from exc

    primary = decision.primary
    estimated = _estimate_cost_kopecks(primary)

    fallbacks = [
        _render_alternative(
            m,
            f"failover candidate ({m.provider.value}) — used if {primary.provider.value} is unavailable",
        )
        for m in decision.fallback_chain[:3]
    ]

    # Alternatives = cross-tier siblings the agent might consider for a
    # different trade-off. Curated per primary so the comparison set is
    # stable across calls. Falls back to empty list — fallback_chain
    # already carries the most useful options for failover-style asks.
    alternatives = _build_curated_alternatives(primary.id)

    return {
        "recommended_model_id": primary.id,
        "provider": primary.provider.value,
        "tier": primary.tier.value,
        "context_window": primary.context_window,
        "reasoning": (
            f"{decision.reason} — category={decision.category.value}, "
            f"strategy={decision.strategy_used.value if decision.strategy_used else 'pinned'}"
        ),
        "estimated_cost_kopecks": estimated,
        "estimated_cost_rub": round(estimated / 100.0, 4),
        "fallback_chain": fallbacks,
        "alternatives": alternatives,
        "router_engine": "router.route_request",
    }


# ---------------------------------------------------------------------------
# Curated alternatives — same data shape as S2's _ALTERNATIVES but kept
# trimmed because the router already surfaces fallback_chain. We only
# expose alternatives the router *wouldn't* pick (cross-tier, different
# strategy) so the agent has something to offer beyond failover.
# ---------------------------------------------------------------------------


_ALT_HINTS: dict[str, tuple[tuple[str, str], ...]] = {
    "claude-opus-4.7": (
        ("claude-sonnet-4.6", "cheaper Anthropic sibling — 4× cheaper, ~85% capability"),
        ("gpt-5.5", "OpenAI flagship with similar reasoning depth"),
    ),
    "claude-sonnet-4.6": (
        ("claude-haiku-4.5", "cheaper Anthropic sibling for simpler subtasks"),
        ("gpt-5.4", "OpenAI alternative if you need strict_json"),
    ),
    "claude-haiku-4.5": (
        ("deepseek-v3.2-chat", "cheaper option if structured JSON isn't critical"),
        ("gpt-5.4-mini", "OpenAI mini with similar latency"),
    ),
    "gpt-5.5": (
        ("claude-sonnet-4.6", "Anthropic alternative with similar capability"),
        ("gpt-5.4", "older flagship — cheaper if vision not needed"),
    ),
    "gpt-5.4": (
        ("gpt-5.4-mini", "cheaper sibling if Pro tier is overkill"),
        ("claude-sonnet-4.6", "Anthropic alternative on reasoning tasks"),
    ),
    "gpt-5.4-mini": (
        ("deepseek-v3.2-chat", "cheaper still if Chinese-provider routing is acceptable"),
        ("claude-haiku-4.5", "Anthropic mini with similar latency"),
    ),
    "deepseek-v3.2-chat": (
        ("gpt-5.4-mini", "OpenAI mini if you need stronger English instruction-following"),
        ("claude-haiku-4.5", "Anthropic mini if you need strict JSON output"),
    ),
}


def _build_curated_alternatives(primary_id: str) -> list[dict[str, Any]]:
    """Curated 'consider these for a different trade-off' set, not failover."""
    out: list[dict[str, Any]] = []
    for alt_id, note in _ALT_HINTS.get(primary_id, ()):
        spec = _CATALOG_BY_ID.get(alt_id)
        if spec is None:
            continue
        out.append(_render_alternative(spec, note))
    return out


__all__ = ["DESCRIPTION", "INPUT_SCHEMA", "NAME", "handler"]
