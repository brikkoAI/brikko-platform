"""High-level Router class — the public entry point for routing decisions.

The router is intentionally pure-Python with no I/O: given a request and
an account context, it returns a `RoutingDecision`. The caller (the API
layer in `voltari_gateway/api/v1/chat.py`, written by part-1 of the
backend work) is responsible for actually invoking the provider via
`with_failover` and persisting the resulting `usage_event`.

This separation keeps the router unit-testable without mocks and lets us
swap providers, billing, or audit-log targets independently.

Decision flow:

    +---------------------------+
    |   parse model tag         |   "auto" / "auto:cheap" / "gpt-5.4-mini"
    +-------------+-------------+
                  |
                  v
    +---------------------------+
    |   categorize_request      |   chat | code | reasoning | long_context
    +-------------+-------------+
                  |
                  v
    +---------------------------+
    |  apply account constraints|   ru_legal, exclude_providers, max_cost
    +-------------+-------------+
                  |
                  v
    +---------------------------+
    |  strategy.select_*        |   ordered list of eligible models
    +-------------+-------------+
                  |
                  v
    +---------------------------+
    |  build_chain (cross-prov) |   primary + fallbacks (diff providers)
    +-------------+-------------+
                  |
                  v
              RoutingDecision
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

import structlog

from voltari_gateway.router.catalog import (
    CATALOG,
    ModelSpec,
    Provider,
)
from voltari_gateway.router.strategies import (
    STRATEGIES,
    Strategy,
    TaskCategory,
    categorize_request,
)

log = structlog.get_logger(__name__)

# Per CEO decision (mvp_scope.md §0): bare `auto` resolves to `cheap`.
DEFAULT_AUTO_STRATEGY: Strategy = Strategy.CHEAP

# Hard cap on fallback chain length. Beyond 3 we just burn time on a
# provider outage with no realistic recovery — better to surface 503
# than spend 60s walking a chain of 8 models.
MAX_FALLBACK_CHAIN_LENGTH: int = 3


# ---------------------------------------------------------------------------
# Inputs / outputs. Plain dataclasses — no pydantic at this layer to keep
# the router callable from non-API contexts (workers, CLI, tests).
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AccountContext:
    """Account-level constraints applied to every request.

    `daily_spent_kop` and `daily_budget_kop` enable a soft "approaching
    cap → bias to cheap" behaviour without hard-blocking the request.

    Sprint 7 — routing preferences (spec §4.4) move the per-account
    policy onto this dataclass. Defaults preserve pre-Sprint-7 behaviour
    (mode=smart, strategy=cheap, allowed=None/None == DEFAULT_AUTO_STRATEGY).
    The chat handler populates these from ``Account.routing_*`` columns
    on every request.
    """

    # uuid.UUID matches the canonical account identifier in Postgres.
    # We previously narrowed this to ``int`` and lossily packed UUIDs into
    # 31 bits — a 50% collision rate after ~65k accounts (BE P0-5). Keeping
    # the full 128-bit type means per-account metrics, routing rules and
    # log lines stay unique for the whole account population.
    account_id: uuid.UUID
    tariff: str  # 'payg' | 'pro' | 'team' | 'business' | 'business_plus'
    balance_kop: int
    require_ru_legal: bool = False  # account-level forced RU
    daily_spent_kop: int = 0
    daily_budget_kop: int | None = None  # None = unlimited

    # Sprint 7 routing preferences. Strings rather than enums so the
    # dataclass stays trivially hashable and JSON-friendly for logs.
    routing_mode: str = "smart"  # 'manual' | 'smart'
    routing_strategy: str = "cheap"  # 'cheap'|'smart'|'fast'|'ru_legal'|'custom'
    routing_allowed_providers: frozenset[str] | None = None
    routing_allowed_models: frozenset[str] | None = None


@dataclass(frozen=True, slots=True)
class RouterRequest:
    """Routing inputs.

    `prompt_text` is the concatenated user/system content used purely for
    categorisation. The caller MUST NOT pass any sensitive system prompt
    that we wouldn't want to log — categorisation is a pure-function
    text scan, but the value may end up in error traces.
    """

    model_tag: str  # 'auto' | 'auto:cheap' | 'gpt-5.4-mini' | ...
    estimated_input_tokens: int
    prompt_text: str = ""  # for categorisation
    reasoning_effort: str | None = None  # OpenAI-style hint
    failover_enabled: bool = True
    exclude_providers: frozenset[Provider] = frozenset()
    max_cost_kop: int | None = None  # client-side budget per-request
    require_tools: bool = False  # request uses tool/function calling


@dataclass(slots=True)
class RoutingDecision:
    """The result the router hands back to the API layer."""

    primary: ModelSpec
    fallback_chain: list[ModelSpec]
    strategy_used: Strategy | None  # None for explicitly pinned models
    category: TaskCategory
    reason: str  # human-readable, for x-router-decision header

    # Effective tag the client supplied (kept for logs / metrics labels).
    requested_model_tag: str = ""

    def header_value(self, *, failover_used: bool = False) -> str:
        """Render the `x-router-decision` header value.

        Format (per task brief Артефакт 6):
            <model_id>;reason=<text>;failover_used=<true|false>

        Non-ASCII chars are stripped from `reason` to keep header safe;
        we don't expect them in our reason strings but it's cheap insurance.
        """
        clean_reason = self.reason.encode("ascii", "ignore").decode("ascii")
        return (
            f"{self.primary.id};"
            f"reason={clean_reason};"
            f"failover_used={'true' if failover_used else 'false'}"
        )


@dataclass(slots=True)
class RoutingError(Exception):
    """No eligible model found for this request.

    The API layer translates this into HTTP 422 (request) or 503 (when
    all models are filtered out by an outage list). Distinguished by
    `reason_code`.
    """

    reason_code: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)

    def __str__(self) -> str:
        return f"{self.reason_code}: {self.message}"


# ---------------------------------------------------------------------------
# Router proper.
# ---------------------------------------------------------------------------


class Router:
    """Stateless routing decision engine.

    Holds a reference to the catalogue (default = module-level `CATALOG`)
    so tests can inject a smaller catalogue without monkeypatching. All
    methods are pure — concurrent calls are safe.
    """

    def __init__(
        self,
        *,
        catalog: tuple[ModelSpec, ...] = CATALOG,
        max_fallback: int = MAX_FALLBACK_CHAIN_LENGTH,
        default_auto_strategy: Strategy = DEFAULT_AUTO_STRATEGY,
    ) -> None:
        if not catalog:
            raise ValueError("Router requires a non-empty catalogue")
        self._catalog: tuple[ModelSpec, ...] = catalog
        # Build O(1) id-lookup matching the (possibly injected) catalogue.
        # We can't rely on the module-level `get_model` because tests
        # inject smaller catalogues for assertion clarity.
        self._catalog_by_id: dict[str, ModelSpec] = {m.id: m for m in catalog}
        self._max_fallback: int = max_fallback
        self._default_auto: Strategy = default_auto_strategy

    # -- public ----------------------------------------------------------

    async def route_request(
        self,
        req: RouterRequest,
        account: AccountContext,
    ) -> RoutingDecision:
        """Return a routing decision for the given request + account.

        Async only because the consuming API is async; the body is sync.
        Keeping the signature async leaves room for V2 enrichment (e.g.
        per-account routing rules from Postgres) without breaking
        callers.
        """
        # Sprint 7 — manual mode forbids auto:* tags; the user must pin a
        # model id explicitly. This is a hard contract: the whole point
        # of manual mode is "no surprises from the smart router".
        if account.routing_mode == "manual" and req.model_tag.startswith("auto"):
            raise RoutingError(
                reason_code="manual_mode_requires_pinned_model",
                message=(
                    "Account is in manual routing mode; pass an explicit "
                    "model id (e.g. 'gpt-5.4-mini') instead of an auto:* tag."
                ),
            )

        # Step 1 — parse the model tag.
        if not req.model_tag.startswith("auto"):
            return self._route_pinned(req, account)

        # Sprint 7 — extend the per-request exclude_providers with the
        # account's allowed_providers complement. After this point the
        # rest of the strategy code already handles the filter.
        if account.routing_allowed_providers is not None:
            allowed_provider_enums: frozenset[Provider] = frozenset(
                Provider(p) for p in account.routing_allowed_providers
            )
            disallowed = frozenset(Provider) - allowed_provider_enums
            req = RouterRequest(
                model_tag=req.model_tag,
                estimated_input_tokens=req.estimated_input_tokens,
                prompt_text=req.prompt_text,
                reasoning_effort=req.reasoning_effort,
                failover_enabled=req.failover_enabled,
                exclude_providers=req.exclude_providers | disallowed,
                max_cost_kop=req.max_cost_kop,
                require_tools=req.require_tools,
            )

        strategy = self._parse_auto_tag_with_account(req.model_tag, account)

        # Step 2 — categorise.
        category = categorize_request(
            text=req.prompt_text,
            estimated_input_tokens=req.estimated_input_tokens,
            reasoning_effort=req.reasoning_effort,
        )

        # Step 3 — apply account-level constraints.
        require_ru_legal = account.require_ru_legal or strategy is Strategy.RU_LEGAL

        # Step 4 — strategy → ordered candidate list.
        select = STRATEGIES[strategy]
        candidates = select(
            category,
            req.estimated_input_tokens,
            require_ru_legal=require_ru_legal,
            require_tools=req.require_tools,
            exclude_providers=req.exclude_providers,
            catalog=self._catalog,
        )

        # Sprint 7 — apply allowed_models whitelist (custom strategy).
        # The whitelist is post-strategy because it cuts at model id
        # granularity, which the strategies don't see (they pick by tier
        # / price). Empty result here means «no model in your whitelist
        # can handle this category» — the standard 422 fall-through.
        if account.routing_allowed_models is not None:
            candidates = [m for m in candidates if m.id in account.routing_allowed_models]

        # Step 5 — apply per-request cost cap.
        if req.max_cost_kop is not None:
            candidates = [
                m
                for m in candidates
                if m.expected_cost_kop(
                    input_tokens=req.estimated_input_tokens,
                    output_tokens=1_000,  # conservative output assumption
                )
                <= req.max_cost_kop
            ]

        # Step 6 — soft-degrade to cheaper if account near daily budget.
        # Triggers at 90% utilisation. We re-sort by cheap regardless of
        # original strategy — protecting the customer's wallet trumps
        # their preference for `smart`/`fast` here.
        if (
            account.daily_budget_kop is not None
            and account.daily_budget_kop > 0
            and account.daily_spent_kop > account.daily_budget_kop * 0.9
        ):
            candidates = STRATEGIES[Strategy.CHEAP](
                category,
                req.estimated_input_tokens,
                require_ru_legal=require_ru_legal,
                require_tools=req.require_tools,
                exclude_providers=req.exclude_providers,
                catalog=tuple(candidates),  # already filtered set
            )
            log.info(
                "router.budget_degrade",
                account_id=str(account.account_id),
                strategy=str(strategy),
                spent_kop=account.daily_spent_kop,
                budget_kop=account.daily_budget_kop,
            )

        if not candidates:
            raise RoutingError(
                reason_code="no_eligible_model",
                message=(
                    f"no model in catalogue can handle this request "
                    f"(category={category}, ctx={req.estimated_input_tokens})"
                ),
                details={
                    "strategy": str(strategy),
                    "require_ru_legal": require_ru_legal,
                    "exclude_providers": [p.value for p in req.exclude_providers],
                },
            )

        primary = candidates[0]
        fallback_chain = self._build_cross_provider_chain(primary, candidates[1:])

        return RoutingDecision(
            primary=primary,
            fallback_chain=fallback_chain,
            strategy_used=strategy,
            category=category,
            reason=f"auto:{strategy}-{category}",
            requested_model_tag=req.model_tag,
        )

    # -- internal --------------------------------------------------------

    def _parse_auto_tag(self, tag: str) -> Strategy:
        """Parse `auto`, `auto:cheap`, `auto:smart`, `auto:fast`, `auto:ru-legal`."""
        if tag == "auto":
            return self._default_auto
        if ":" not in tag:
            raise RoutingError(
                reason_code="invalid_model_tag",
                message=f"unknown model tag: {tag!r}",
            )
        suffix = tag.split(":", 1)[1]
        # Tolerate ``auto:default`` as an alias for ``auto`` — the spec
        # surfaces it as a synonym (§4.2.2b).
        if suffix == "default":
            return self._default_auto
        try:
            return Strategy(suffix)
        except ValueError as err:
            raise RoutingError(
                reason_code="invalid_strategy",
                message=(f"unknown strategy {suffix!r}; valid: cheap, smart, fast, ru-legal, code"),
            ) from err

    def _parse_auto_tag_with_account(self, tag: str, account: AccountContext) -> Strategy:
        """Like ``_parse_auto_tag`` but ``auto`` defers to the account strategy.

        Spec §4.2.2b/c:

        * ``auto`` / ``auto:default`` → use ``account.routing_strategy``
        * ``auto:cheap`` / etc.       → per-request override of the
                                        account strategy (still wins).
        * Custom strategy at account level falls back to SMART semantics
          for the model-selection step; the catalogue filter on
          ``allowed_*`` is what makes it "custom".
        """
        if tag in ("auto", "auto:default"):
            account_strat = account.routing_strategy
            if account_strat == "ru_legal":
                return Strategy.RU_LEGAL
            if account_strat == "custom":
                # Treat custom as "smart over the user's whitelist". The
                # whitelist itself is applied via exclude_providers +
                # allowed_models post-filter.
                return Strategy.SMART
            try:
                return Strategy(account_strat)
            except ValueError:
                # Forward-compat: an unknown strategy on disk degrades to
                # the global default rather than failing the request.
                return self._default_auto
        return self._parse_auto_tag(tag)

    def _route_pinned(
        self,
        req: RouterRequest,
        account: AccountContext,
    ) -> RoutingDecision:
        """Route to an explicitly-named model with a same-tier fallback."""
        primary = self._catalog_by_id.get(req.model_tag)
        if primary is None:
            raise RoutingError(
                reason_code="unknown_model",
                message=f"model {req.model_tag!r} is not in the catalogue",
            )

        # If the account is RU-legal-locked but the user pinned a non-RU
        # model — refuse rather than silently swap. Surprises in this
        # direction are a compliance risk.
        if account.require_ru_legal and not primary.ru_legal:
            raise RoutingError(
                reason_code="model_not_ru_legal",
                message=(f"account requires RU-legal models; {primary.id!r} is hosted outside RU"),
            )

        # Sprint 7 — custom whitelist is a stone wall. A pinned model
        # outside the whitelist must be refused, not silently rerouted.
        # See spec §4.3 / §5.2 for the rationale (whitelist == compliance
        # gate, must not be bypassable).
        if account.routing_strategy == "custom":
            if (
                account.routing_allowed_providers is not None
                and primary.provider.value not in account.routing_allowed_providers
            ):
                raise RoutingError(
                    reason_code="model_not_in_allowed_providers",
                    message=(f"Model {primary.id!r} is not in your account's allowed providers."),
                    details={
                        "allowed_providers": sorted(account.routing_allowed_providers),
                    },
                )
            if (
                account.routing_allowed_models is not None
                and primary.id not in account.routing_allowed_models
            ):
                raise RoutingError(
                    reason_code="model_not_in_allowed_models",
                    message=(f"Model {primary.id!r} is not in your account's allowed models."),
                    details={
                        "allowed_models": sorted(account.routing_allowed_models),
                    },
                )

        # Context window check — pinned model must actually fit.
        if primary.context_window < req.estimated_input_tokens + 1_000:
            raise RoutingError(
                reason_code="context_too_large",
                message=(
                    f"model {primary.id!r} context window "
                    f"{primary.context_window} < request {req.estimated_input_tokens}"
                ),
            )

        # Build a fallback chain from same-tier models on different providers.
        # Sprint 7 — custom whitelist also constrains the fallback so
        # a single-provider whitelist gets an empty chain and surfaces
        # 503 on outage rather than silently leaking to an upstream the
        # user explicitly excluded.
        allowed_providers_set = (
            account.routing_allowed_providers
            if account.routing_allowed_providers is not None
            else None
        )
        allowed_models_set = (
            account.routing_allowed_models if account.routing_allowed_models is not None else None
        )
        same_tier_others = [
            m
            for m in self._catalog
            if m.tier is primary.tier
            and m.provider is not primary.provider
            and m.context_window >= req.estimated_input_tokens + 1_000
            and m.provider not in req.exclude_providers
            and (not account.require_ru_legal or m.ru_legal)
            and (not req.require_tools or m.supports_tools)
            and (allowed_providers_set is None or m.provider.value in allowed_providers_set)
            and (allowed_models_set is None or m.id in allowed_models_set)
        ]
        # Cheapest fallback first within the same tier.
        same_tier_others.sort(
            key=lambda m: (
                0.7 * m.input_price_kop_per_1k + 0.3 * m.output_price_kop_per_1k,
                m.id,
            ),
        )
        fallback_chain = self._build_cross_provider_chain(primary, same_tier_others)

        # Categorise even for pinned models — useful for analytics later.
        category = categorize_request(
            text=req.prompt_text,
            estimated_input_tokens=req.estimated_input_tokens,
            reasoning_effort=req.reasoning_effort,
        )

        return RoutingDecision(
            primary=primary,
            fallback_chain=fallback_chain,
            strategy_used=None,
            category=category,
            reason=f"pinned:{primary.id}",
            requested_model_tag=req.model_tag,
        )

    def _build_cross_provider_chain(
        self,
        primary: ModelSpec,
        candidates: list[ModelSpec],
    ) -> list[ModelSpec]:
        """Greedy cross-provider fallback chain.

        Algorithm: walk the candidate list in their existing strategy
        order and pick the first model from each provider we haven't
        seen yet. This maximises the chance that a single-provider
        outage (OpenAI API down) doesn't take out our entire chain.
        Fall back to same-provider models only if cross-provider slots
        are exhausted.

        Trade-off: we're not optimising globally — a strict "cheapest 3
        across providers" would require a second sort. Greedy is fine
        because the candidate list is already sorted by the chosen
        strategy, so cross-provider picks are still strategy-optimal
        within their provider.
        """
        chain: list[ModelSpec] = []
        seen_providers: set[Provider] = {primary.provider}
        leftovers: list[ModelSpec] = []

        for cand in candidates:
            if len(chain) >= self._max_fallback:
                break
            if cand.provider not in seen_providers:
                chain.append(cand)
                seen_providers.add(cand.provider)
            else:
                leftovers.append(cand)

        # Top up with same-provider candidates if we still have slots.
        for cand in leftovers:
            if len(chain) >= self._max_fallback:
                break
            chain.append(cand)

        return chain


__all__ = [
    "DEFAULT_AUTO_STRATEGY",
    "MAX_FALLBACK_CHAIN_LENGTH",
    "AccountContext",
    "Router",
    "RouterRequest",
    "RoutingDecision",
    "RoutingError",
]
