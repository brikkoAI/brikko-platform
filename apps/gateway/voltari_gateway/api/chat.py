"""POST /v1/chat/completions — OpenAI-compatible chat endpoint.

Pipeline per request:

    1. Auth (require_api_key dependency).
    2. Validate Pydantic body.
    3. **Pre-flight billing**: lock the account row, compute an estimated
       cost from ``prompt_tokens × input_price + max_tokens × output_price``
       (multiplied by a 1.5 safety buffer), and place a Postgres-backed
       hold for that amount. If the available balance (``balance - sum_holds``)
       is below the hold, return 402 ``insufficient_quota`` and DO NOT call
       any provider.
    4. Build a ``RouterRequest`` and let ``Router.route_request()`` pick a
       primary + fallback chain.
    5. Wrap the upstream call in ``with_failover(...)``.
    6. **Post-flight billing**: compute the actual ``compute_cost_kopecks``
       and call ``commit_hold_to_debit`` to settle the hold against the
       real cost. If the upstream call raised — release the hold so the
       customer gets the reserved balance back.
    7. Persist the ``UsageEvent`` only when we have real provider usage.
    8. Set ``X-Router-Decision``, ``X-Request-Id``, ``X-Gateway-Cost-Kop``.

Streaming follows the same pipeline but the failover wrap returns a single
async-iterator of SSE bytes (failover only fires before the first byte;
mid-stream cross-provider switch is out of scope). Stream cancellation by
the client is detected via ``request.is_disconnected()`` — when that fires
we close the upstream iterator, debit only the tokens delivered so far,
and write a partial ``UsageEvent``. Importantly the post-flight DB work
runs on a **fresh AsyncSession** because FastAPI has already disposed the
request-scoped session by the time the generator's ``finally`` runs.
"""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends
from fastapi.requests import Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, StrictBool, field_validator
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sse_starlette.sse import EventSourceResponse

from voltari_gateway.auth.middleware import AuthPrincipal, get_redis
from voltari_gateway.auth.oauth_dependency import require_api_key_or_oauth_scope
from voltari_gateway.auth.oauth_scopes import OAuthScope
from voltari_gateway.billing import compute_cost_kopecks
from voltari_gateway.billing.engine import (
    DEFAULT_HOLD_TTL_SECONDS,
    BillingError,
    HoldHandle,
    InsufficientBalanceError,
    commit_hold_to_debit,
    hold_amount,
    release_hold,
)
from voltari_gateway.config import get_settings
from voltari_gateway.db.models import UsageEvent
from voltari_gateway.db.session import get_db, get_session_factory
from voltari_gateway.pii import (
    PiiMapping,
    PiiMappingStore,
    StreamUnmasker,
    compute_pii_flags,
    mask_messages,
    resolve_account_pii_flag,
    stream_unmask_chunk_openai,
    unmask_payload_openai,
)
from voltari_gateway.pii import (
    audit_summary as _pii_audit_summary,
)
from voltari_gateway.providers.base import (
    ChatCompletionRequest,
    ChatCompletionResponse,
    ChatCompletionUsage,
    ProviderAuthError,
    ProviderClientError,
    ProviderError,
    ProviderRateLimitError,
    ProviderServerError,
    ProviderTimeoutError,
)
from voltari_gateway.providers.registry import ProviderRegistry
from voltari_gateway.router.catalog import ModelSpec, get_model
from voltari_gateway.router.failover import (
    FailoverError,
    FailoverEvent,
    FailoverResult,
    with_failover,
)
from voltari_gateway.router.pipeline import (
    PipelineContext,
    RouterPipeline,
    parse_budget_kopecks_header,
    should_use_pipeline,
)
from voltari_gateway.router.router import (
    AccountContext,
    RouterRequest,
    RoutingDecision,
    RoutingError,
)
from voltari_gateway.router.router import (
    Router as RouterEngine,
)
from voltari_gateway.utils.errors import (
    GatewayError,
    insufficient_quota,
    invalid_request,
    model_not_found,
    upstream_error,
)
from voltari_gateway.utils.logging import get_logger
from voltari_gateway.utils.response_format import validate_response_format

router = APIRouter()
log = get_logger(__name__)

# Default output budget when the client doesn't pass max_tokens. Used only
# for the pre-flight estimate — the actual debit is for whatever the
# provider reports in usage. Picked to cover a typical 1-2 page reply while
# not bloating the hold for a one-line answer.
DEFAULT_ESTIMATED_OUTPUT_TOKENS = 1_024

# Per-tariff hard cap on `max_tokens` (Phase 4, P0 security 2026-05-09 —
# защита от token-cost DoS, см. 06_Operations/2026-05-09-night-research/
# 10-security-threats-2026.md). Атакующий с welcome-200 ₽ балансом без
# капа может попросить max_tokens=131_072 на премиум-модели и сжечь $50+
# за один запрос. Cap привязан к тарифу: PAYG жёстко ограничен, Pro/Team
# щедро. Legacy Business / Business+ — full ceiling 131k.
TARIFF_MAX_OUTPUT_TOKENS: dict[str, int] = {
    "payg": 4_096,
    "pro": 16_384,
    "pro_privacy": 16_384,
    "team": 32_768,
    "business": 131_072,
    "business_plus": 131_072,
}
DEFAULT_TARIFF_MAX_OUTPUT_TOKENS = 4_096

# Multiplier applied on top of the COGS estimate when placing the hold.
# Provider usage often comes back 10-30% higher than our token estimate
# (chat templates, function calls, system prompts not counted in our
# rough char/3 heuristic). 1.5× covers the long tail without locking up
# more balance than the customer would conceivably spend in one call.
#
# Stored as Decimal so multiplying by ``billing.MARKUP`` (also Decimal,
# TD-008) doesn't raise TypeError. This is purely an estimate path —
# the final ``compute_cost_kopecks`` after the call drives real billing.
HOLD_BUFFER_MULTIPLIER: Decimal = Decimal("1.5")


# ---------- request / response models -----------------------------------------


class ChatMessage(BaseModel):
    role: Literal["system", "user", "assistant", "tool", "developer"]
    content: str | list[dict[str, Any]] | None = None
    name: str | None = None
    tool_call_id: str | None = None
    tool_calls: list[dict[str, Any]] | None = None


class ChatCompletionRequestBody(BaseModel):
    model: str
    messages: list[ChatMessage] = Field(min_length=1)
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    top_p: float | None = Field(default=None, ge=0.0, le=1.0)
    max_tokens: int | None = Field(default=None, ge=1, le=131_072)
    # TD-047: StrictBool — pydantic v2 lax-mode иначе приводит "yes"/"true"/1
    # к True, что нарушает OpenAI spec и ломает streaming в неожиданных местах
    # (клиент посылает stream=1, мы возвращаем SSE — он не готов парсить).
    stream: StrictBool = False
    stop: list[str] | str | None = None

    # Pass-through fields. Listing them here gives clear validation errors
    # and lets us forward to the SDK without a free-form `extra` dict.
    response_format: dict[str, Any] | None = None
    tools: list[dict[str, Any]] | None = None
    tool_choice: dict[str, Any] | str | None = None
    seed: int | None = None
    user: str | None = None
    presence_penalty: float | None = Field(default=None, ge=-2.0, le=2.0)
    frequency_penalty: float | None = Field(default=None, ge=-2.0, le=2.0)
    logprobs: bool | None = None
    top_logprobs: int | None = Field(default=None, ge=0, le=20)
    stream_options: dict[str, Any] | None = None
    reasoning_effort: Literal["low", "medium", "high"] | None = None

    # Voltari-specific routing knobs (ignored by stock OpenAI clients).
    failover: bool = True
    exclude_providers: list[str] | None = None

    # Sprint 4 Поток M — 152-ФЗ PII-маскинг (per-request opt-in).
    # Когда True, gateway маскирует ПДн в prompt'е перед отправкой
    # провайдеру и раз-маскирует в ответе. Account-level флаг
    # ``pii_masking_enabled`` overrides — если глобально включено,
    # этот флаг можно не передавать.
    pii_protect: bool | None = None

    model_config = {"extra": "ignore"}

    @field_validator("messages")
    @classmethod
    def _validate_messages(cls, v: list[ChatMessage]) -> list[ChatMessage]:
        # Per-block AND aggregate caps. The string path was already
        # checked, but a multimodal payload (``content`` as a list of
        # blocks) could smuggle 50× 100k blocks past the simple
        # ``isinstance(str)`` guard — that pattern was used to send 5 MB
        # of text inside what looked like a valid chat-completions request.
        # Sprint 5 hardening: each text block ≤200k, total across all
        # blocks/messages ≤400k (allow legitimate vision-RAG payloads,
        # block obvious abuse).
        max_per_block = 200_000
        max_total = 400_000
        total = 0
        for m in v:
            c = m.content
            if isinstance(c, str):
                if len(c) > max_per_block:
                    raise ValueError("message.content exceeds 200000 chars")
                total += len(c)
            elif isinstance(c, list):
                for part in c:
                    if not isinstance(part, dict):
                        continue
                    text = part.get("text")
                    if isinstance(text, str):
                        if len(text) > max_per_block:
                            raise ValueError("message.content[].text exceeds 200000 chars")
                        total += len(text)
        if total > max_total:
            raise ValueError(f"messages aggregate text exceeds {max_total} chars")
        return v


# ---------- helpers -----------------------------------------------------------


def _estimate_input_tokens(messages: list[ChatMessage]) -> int:
    """Rough token estimate for routing — chars / 4 (English) / 2 (Cyrillic).

    Routing only needs an order-of-magnitude estimate (it picks tier and
    long-context branch). We undercount Cyrillic by treating every char as
    ~half a token, since Russian text averages 2 chars/token in BPE
    tokenisers. Better to slightly overshoot — the strategy filters out
    short-context models with a 1k safety buffer anyway.
    """
    total_chars = 0
    for m in messages:
        c = m.content
        if isinstance(c, str):
            total_chars += len(c)
        elif isinstance(c, list):
            for part in c:
                if isinstance(part, dict):
                    text = part.get("text") or ""
                    total_chars += len(str(text))
    # Conservative: ~3 chars/token average across mixed-language traffic.
    return max(1, total_chars // 3)


def _gather_prompt_text(messages: list[ChatMessage]) -> str:
    parts: list[str] = []
    for m in messages:
        c = m.content
        if isinstance(c, str):
            parts.append(c)
        elif isinstance(c, list):
            for part in c:
                if isinstance(part, dict):
                    text = part.get("text") or ""
                    if text:
                        parts.append(str(text))
    # Cap to 4k chars for categorisation — anything past that won't change
    # the classifier's verdict and we don't want to scan a 200k-token RAG
    # prompt every time.
    joined = "\n".join(parts)
    return joined[:4000]


def _estimate_hold_kopecks(
    *,
    primary: ModelSpec,
    estimated_input_tokens: int,
    requested_max_tokens: int | None,
) -> int:
    """Best-guess hold for a single chat call.

    Uses the *primary* model's pricing because that's what we'll most likely
    bill at; the failover chain may touch a different model but pricing
    across same-tier models is within a small margin. Output assumption is
    either the client's ``max_tokens`` (their explicit budget) or
    ``DEFAULT_ESTIMATED_OUTPUT_TOKENS``.

    Result is multiplied by ``HOLD_BUFFER_MULTIPLIER`` to absorb token-count
    estimation error. We also enforce a minimum hold of 1 kopeck so a tiny
    estimate doesn't accidentally bypass the pre-flight gate.
    """
    output_tokens = requested_max_tokens or DEFAULT_ESTIMATED_OUTPUT_TOKENS
    cogs_kop = primary.expected_cost_kop(
        input_tokens=estimated_input_tokens,
        output_tokens=output_tokens,
    )
    # Apply gateway markup (matches compute_cost_kopecks) plus the safety
    # buffer. Markup pulled from the billing module so they stay in sync.
    from voltari_gateway.billing import MARKUP

    estimate = int(cogs_kop * MARKUP * HOLD_BUFFER_MULTIPLIER)
    return max(1, estimate)


def _build_provider_request(
    body: ChatCompletionRequestBody,
    model: ModelSpec,
    *,
    extended_anthropic_cache: bool = False,
) -> ChatCompletionRequest:
    extra: dict[str, Any] = {}
    for fld in (
        "response_format",
        "tools",
        "tool_choice",
        "seed",
        "user",
        "presence_penalty",
        "frequency_penalty",
        "logprobs",
        "top_logprobs",
        "stream_options",
        "reasoning_effort",
    ):
        value = getattr(body, fld)
        if value is not None:
            extra[fld] = value
    if extended_anthropic_cache:
        # Anthropic adapter reads this and rewrites cache_control TTL.
        extra["_brikko_anthropic_cache_extended"] = True

    return ChatCompletionRequest(
        model=model,
        messages=[m.model_dump(exclude_none=True) for m in body.messages],
        temperature=body.temperature,
        top_p=body.top_p,
        max_tokens=body.max_tokens,
        stream=body.stream,
        stop=body.stop,
        extra=extra,
    )


def _wrap_provider_error(exc: ProviderError) -> GatewayError:
    if isinstance(exc, ProviderAuthError):
        # Provider-side auth failure → return 502 to the client; do not
        # expose upstream auth specifics.
        return upstream_error("Upstream authentication failed.")
    if isinstance(exc, ProviderRateLimitError):
        return upstream_error("Upstream rate limit reached. Try again shortly.")
    if isinstance(exc, ProviderTimeoutError):
        return upstream_error("Upstream provider timed out.")
    if isinstance(exc, ProviderClientError):
        # Upstream said "your request is malformed" — reflect as 400 so
        # the client can fix it instead of retrying.
        return invalid_request(f"Upstream rejected the request: {exc}", code="upstream_invalid")
    if isinstance(exc, ProviderServerError):
        return upstream_error("Upstream provider error.")
    return upstream_error(str(exc) or "Upstream provider error.")


def _exclude_set(values: list[str] | None) -> frozenset[Any]:
    """Translate the public ``exclude_providers`` list into the router's enum set."""
    if not values:
        return frozenset()
    from voltari_gateway.router.catalog import Provider as ProviderEnum

    out: list[ProviderEnum] = []
    for v in values:
        try:
            out.append(ProviderEnum(v))
        except ValueError:
            # Silently drop unknown providers; clients shouldn't crash on typos.
            continue
    return frozenset(out)


def _decision_header(decision: RoutingDecision, *, failover_used: bool) -> str:
    """Render the X-Router-Decision header."""
    return decision.header_value(failover_used=failover_used)


def _ascii_header(value: str) -> str:
    """Strip non-ASCII chars so a Cyrillic ``reason`` doesn't crash Starlette.

    Headers are latin-1 framed (RFC 7230 §3.2.4); arbitrary UTF-8 in a
    response header trips Starlette's encode pass with a 500. Our reason
    strings are usually ASCII (``auto:cheap-CHAT``, ``pinned:gpt-...``)
    but the function is forward-defensive against future categorisers.
    """
    return value.encode("ascii", "ignore").decode("ascii")


def _failover_overrides(state: Any) -> dict[str, Any]:
    """Return kwargs for ``with_failover`` overridable via ``app.state``.

    Tests set ``app.state.failover_backoffs_ms`` and ``app.state.failover_timeout_s``
    to keep retry storms from taking 30s. In production neither is set and
    ``with_failover`` uses its own defaults.

    The circuit breaker (BE P1-20) is also pulled from ``app.state`` so
    tests can swap in their own (or a None to disable). It's optional;
    ``with_failover`` works fine without it.
    """
    out: dict[str, Any] = {}
    backoffs = getattr(state, "failover_backoffs_ms", None)
    if backoffs is not None:
        out["backoffs_ms"] = backoffs
    timeout = getattr(state, "failover_timeout_s", None)
    if timeout is not None:
        out["timeout_s"] = timeout
    breaker = getattr(state, "circuit_breaker", None)
    if breaker is not None:
        out["circuit_breaker"] = breaker
    return out


def _smart_router_v2_active(principal: AuthPrincipal) -> bool:
    """Return True iff this request should use the Smart Router v2 pipeline.

    Sprint S1. ``should_use_pipeline`` is pure (no I/O) — see
    ``router.pipeline``. We call it once per request; either flag set
    routes through the pipeline, both off routes through v1 verbatim.
    """
    settings = get_settings()
    return should_use_pipeline(
        env_flag=settings.smart_router_v2_enabled,
        account_flag=principal.smart_router_v2_enabled,
    )


async def _route_via_pipeline(
    *,
    state: Any,
    router_engine: RouterEngine,
    router_req: RouterRequest,
    account_ctx: AccountContext,
    headers: Any,
) -> RoutingDecision:
    """Execute the Smart Router v2 pipeline and return the v1 RoutingDecision.

    The pipeline instance is fetched from ``app.state.router_pipeline``
    if present (built once in ``main.lifespan``); otherwise we
    fallback to constructing one on the spot. The fallback path is
    primarily for tests that don't bother wiring lifespan state.

    S1 NOTE: the returned ``decision`` is identical to what
    ``router_engine.route_request`` would have produced — this is
    asserted by the regression-baseline test in ``test_pipeline.py``.
    The pipeline metadata (cache_hit / rule_id / routed_from) is
    currently ignored by the API handler; later sprints will start
    surfacing it via response headers.
    """
    pipeline: RouterPipeline | None = getattr(state, "router_pipeline", None)
    if pipeline is None:
        pipeline = RouterPipeline(router_engine)
    ctx = PipelineContext(
        router_request=router_req,
        account=account_ctx,
        request_id=uuid.uuid4().hex,
        session_id=headers.get("x-session-id"),
        budget_kopecks=parse_budget_kopecks_header(headers.get("x-budget-kopecks")),
        task_hint=headers.get("x-task-hint"),
    )
    result = await pipeline.run(ctx)
    return result.decision


def _events_to_dicts(events: list[FailoverEvent]) -> list[dict[str, Any]]:
    return [
        {
            "model_id": e.model_id,
            "provider": e.provider,
            "attempt": e.attempt,
            "succeeded": e.succeeded,
            "error_type": e.error_type,
            "latency_ms": e.latency_ms,
        }
        for e in events
    ]


# Sprint 13 / Privacy v2 — Phase 4: the gate / unmask / stream-unmask
# helpers were lifted into ``voltari_gateway.pii.integration`` so the
# additional endpoints (/v1/messages, /v1/embeddings,
# /v1/audio/transcriptions) can share them. The aliases below keep the
# original module-private names callable so existing tests
# (``test_chat_pii.py``, internal regressions) don't have to import-rename
# at the same time.
_resolve_account_pii_flag = resolve_account_pii_flag
_unmask_response_payload = unmask_payload_openai
_stream_unmask_chunk = stream_unmask_chunk_openai


@asynccontextmanager
async def _fresh_session(
    factory: async_sessionmaker[AsyncSession] | None = None,
) -> AsyncIterator[AsyncSession]:
    """Open a brand-new session decoupled from FastAPI's request lifecycle.

    Streaming responses run their generator AFTER FastAPI has yielded the
    response, by which point the request-scoped DB session has been closed
    and rolled back. Writing usage_events through that session silently
    fails (BE P0-15) — which let stream traffic skip billing entirely.

    This helper opens a fresh session against the same engine. Callers
    must commit explicitly; rollback on exception is automatic via the
    standard session protocol.

    The optional ``factory`` argument lets the stream handler snapshot the
    session factory at request-start time, decoupling the post-flight
    billing from a global that may have been swapped out (notably between
    tests, where each fixture rebuilds the engine).
    """
    f = factory if factory is not None else get_session_factory()
    async with f() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


# ---------- endpoint ----------------------------------------------------------


@router.post(
    "/v1/chat/completions",
    response_model=None,
    tags=["chat"],
    summary="Create chat completion (OpenAI-compatible)",
    description=(
        "OpenAI-compatible chat completions endpoint. Supports both non-streaming "
        "(JSON response) and streaming (Server-Sent Events) modes via the "
        "``stream`` flag.\n\n"
        "**Auth**: Bearer ``sk-vlt-...`` (cookie-session not accepted on this "
        "endpoint by design — chat is M2M only).\n\n"
        "**Routing**: pass ``model='auto:cheap'`` / ``'auto:smart'`` / "
        "``'auto:fast'`` to delegate model choice to the smart router, or pin a "
        "specific model id from ``GET /v1/models``.\n\n"
        "**Billing**: cost is computed from provider-reported tokens × catalogue "
        "pricing × 1.15 markup, debited atomically via a pre-flight hold + "
        "post-flight commit (TD-029)."
    ),
    responses={
        200: {"description": "Successful completion (or SSE stream when stream=true)."},
        401: {"description": "Missing or invalid Bearer token."},
        402: {"description": "Insufficient balance — top up via /v1/billing/topup."},
        429: {"description": "Per-account chat rate limit exceeded."},
        503: {"description": "All providers in the failover chain failed."},
    },
)
async def chat_completions(
    body: ChatCompletionRequestBody,
    request: Request,
    principal: Annotated[
        AuthPrincipal, Depends(require_api_key_or_oauth_scope(OAuthScope.CHAT_READ))
    ],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> JSONResponse | EventSourceResponse:
    # Per-account chat rate limit (BE P1-21). Runs BEFORE balance / hold
    # work so a customer hammering the endpoint can't even reach the DB.
    # Fail-open on Redis errors — see middleware/rate_limit.py.
    from voltari_gateway.middleware.rate_limit import enforce_chat_rate_limit

    await enforce_chat_rate_limit(request, principal)

    # --- PII masking gate (Sprint 4 Поток M) -----------------------------
    # Three-way trigger (any → enabled):
    #   1. Account.pii_masking_enabled (tariff Pro Privacy / Enterprise)
    #   2. Header X-PII-Protect: true
    #   3. Body field pii_protect: true
    # Account flag is the strongest commit ("always mask"); per-request
    # flags allow opt-in for default tariffs without touching account state.
    # Hot path: prefer the principal-cached flag (set during auth lookup)
    # so we don't issue a `SELECT pii_masking_enabled FROM accounts` on every
    # /v1/chat/completions call. Falls back to a DB read only when the
    # auth-cache entry predates the Sprint-5 schema bump.
    pii_account_on = await resolve_account_pii_flag(
        db, principal.account_id, cached=principal.pii_masking_enabled
    )
    pii_enabled = compute_pii_flags(
        header_value=request.headers.get("x-pii-protect"),
        body_flag=body.pii_protect,
        account_flag=pii_account_on,
    )

    pii_mapping: PiiMapping | None = None
    if pii_enabled:
        masked, pii_mapping = mask_messages(
            [m.model_dump(exclude_none=True) for m in body.messages]
        )
        if not pii_mapping.is_empty():
            # Replace body.messages with masked counterparts so the rest of
            # the pipeline (token estimate, provider call) sees ONLY masked
            # text. ChatMessage validates on construction — masked text is
            # still valid (placeholders fit any length constraint).
            body.messages = [ChatMessage(**m) for m in masked]
            log.info(
                "pii_masked",
                account_id=str(principal.account_id),
                pii_summary=[
                    {"type": e.pii_type, "count": e.count} for e in _pii_audit_summary(pii_mapping)
                ],
            )

    # Fast-fail for obviously-out-of-balance PAYG accounts. The principal's
    # ``balance_kopecks`` is cached (TTL 60s) so this is a hint, not a
    # source of truth — the authoritative check is done under FOR UPDATE
    # in the pre-flight hold below.
    if principal.balance_kopecks <= 0 and principal.tariff == "payg":
        raise insufficient_quota("Top up your balance to continue.")

    # Fetch shared services from app state. Router/registry are built once
    # in lifespan; tests inject stubs by setting these attributes.
    state = request.app.state
    registry: ProviderRegistry = getattr(state, "provider_registry", None)  # type: ignore[assignment]
    router_engine: RouterEngine = getattr(state, "router_engine", None)  # type: ignore[assignment]

    if registry is None:
        # Backwards-compat path for tests that only set ``state.openai_provider``.
        registry = ProviderRegistry()
        from voltari_gateway.router.catalog import Provider as ProviderEnum

        legacy = getattr(state, "openai_provider", None)
        if legacy is not None:
            registry.register(ProviderEnum.OPENAI, legacy)
    if router_engine is None:
        router_engine = RouterEngine()

    # --- Build the routing inputs -----------------------------------------
    estimated_input_tokens = _estimate_input_tokens(body.messages)
    prompt_text = _gather_prompt_text(body.messages)

    # Auto-exclude providers that have no credentials configured. This
    # applies to both ``auto:*`` and pinned-mode routing.
    from voltari_gateway.router.catalog import Provider as ProviderEnum

    all_providers = frozenset(ProviderEnum)
    configured = registry.configured_providers()
    pinned_spec = get_model(body.model) if not body.model.startswith("auto") else None

    # Sprint M3.1 — pinned model whose provider has no API key on this
    # deployment is refused IMMEDIATELY with 400 ``model_not_available``.
    # Previously we let the router silently fall over to a same-tier model
    # on a different provider, which surprised customers (they pinned X,
    # we billed/served Y). Combined with the /v1/models filter, the model
    # is now both invisible AND unrequestable when the key is missing —
    # consistent client experience. CEO scenario: shipping without
    # TOGETHER_API_KEY → ``model: "llama-3.3-70b"`` → 400, client picks
    # something else from /v1/models.
    if pinned_spec is not None and pinned_spec.provider not in configured:
        raise invalid_request(
            (
                f"Model {pinned_spec.id!r} is not available on this deployment "
                "(provider not configured). Use GET /v1/models to see what's "
                "available."
            ),
            code="model_not_available",
            param="model",
        )

    auto_exclude = all_providers - configured
    if pinned_spec is not None:
        # Don't exclude the pinned primary's provider — the router needs
        # to be able to return it as primary. (We've already verified above
        # that the provider IS configured, so this is just a safety belt.)
        auto_exclude = auto_exclude - {pinned_spec.provider}
    user_exclude = _exclude_set(body.exclude_providers)
    router_req = RouterRequest(
        model_tag=body.model,
        estimated_input_tokens=estimated_input_tokens,
        prompt_text=prompt_text,
        reasoning_effort=body.reasoning_effort,
        failover_enabled=body.failover,
        exclude_providers=user_exclude | auto_exclude,
        require_tools=bool(body.tools),
    )
    account_ctx = AccountContext(
        # UUID stays full-width — no more lossy ``& ((1<<31)-1)`` trick
        # (BE P0-5). Per-account routing rules now address the right account
        # for the entire population.
        account_id=principal.account_id,
        tariff=principal.tariff,
        balance_kop=principal.balance_kopecks,
        # Sprint 7 — per-account routing preferences. Read from the
        # principal cache (TTL 60s) so chat hot path stays SELECT-free.
        routing_mode=principal.routing_mode,
        routing_strategy=principal.routing_strategy,
        routing_allowed_providers=(
            frozenset(principal.routing_allowed_providers)
            if principal.routing_allowed_providers is not None
            else None
        ),
        routing_allowed_models=(
            frozenset(principal.routing_allowed_models)
            if principal.routing_allowed_models is not None
            else None
        ),
    )

    # --- Route -----------------------------------------------------------
    # Sprint S1 — Smart Router v2 pipeline (design doc 2026-05-12).
    # When BOTH env-flag and per-account flag are off (production
    # default) we take the legacy v1 path verbatim. When EITHER is on
    # we run the pipeline; in S1 the pipeline's BaseRouterStage just
    # delegates to the same v1 ``route_request`` so behaviour is
    # identical — scaffolding lands without behaviour change.
    use_pipeline = _smart_router_v2_active(principal)
    try:
        if use_pipeline:
            decision = await _route_via_pipeline(
                state=state,
                router_engine=router_engine,
                router_req=router_req,
                account_ctx=account_ctx,
                headers=request.headers,
            )
        else:
            decision = await router_engine.route_request(router_req, account_ctx)
    except RoutingError as err:
        if err.reason_code == "unknown_model":
            raise model_not_found(body.model) from err
        if err.reason_code == "context_too_large":
            raise invalid_request(err.message, code=err.reason_code) from err
        # Sprint 7 — manual mode + auto:* and custom-whitelist refusals
        # both surface as 400 with their specific reason_code preserved
        # so SDK callers can branch on the error.
        raise invalid_request(err.message, code=err.reason_code) from err

    # Early reject if the chosen primary's provider is not configured *and*
    # there is no fallback to a configured provider — surface 503.
    # ``configured`` is the same frozenset computed above. Pinned primaries
    # are already handled by the 400 ``model_not_available`` guard above
    # (Sprint M3.1); this branch only catches the auto-strategy edge case
    # where every candidate provider is unreachable.
    if decision.primary.provider not in configured and not any(
        m.provider in configured for m in decision.fallback_chain
    ):
        raise GatewayError(
            status_code=503,
            message=(
                f"No configured provider can serve this request "
                f"(primary={decision.primary.id}, fallbacks exhausted)"
            ),
            type="api_error",
            code="provider_unavailable",
        )

    # --- response_format validation (Sprint 9, Task 3) ------------------
    # Validate the schema NOW (after routing chose a primary model so we
    # can refuse strict=true on incapable providers like Yandex/Sber). We
    # also stop a malformed schema from being forwarded — saves an OpenAI
    # 4xx round-trip and gives the client a precise error.
    validate_response_format(body.response_format, model=decision.primary)

    # --- Anthropic extended cache (Sprint 9, Task 4) ---------------------
    # X-Brikko-Cache: anthropic-extended → use Anthropic's 1h ephemeral
    # cache TTL instead of the default 5m. Caller still has to mark the
    # blocks themselves with cache_control (we don't auto-mark — the
    # block boundaries change cache hit-rate semantics, only the caller
    # knows their reuse pattern). Stashed on the provider request's
    # ``extra`` dict so the Anthropic adapter can rewrite the ttl on
    # outbound blocks.
    cache_header = (request.headers.get("x-brikko-cache") or "").strip().lower()
    extended_anthropic_cache = cache_header == "anthropic-extended"

    # Refuse calls to a deprecated model past its sunset date. Before the
    # date we let the call through (deprecated_at is informational) — the
    # X-Brikko-Deprecated header on the response carries the warning.
    if decision.primary.deprecated_at is not None and date.today() > decision.primary.deprecated_at:
        raise invalid_request(
            (
                f"Model {decision.primary.id!r} retired on "
                f"{decision.primary.deprecated_at.isoformat()}. "
                f"Migrate to a current model (see GET /v1/models)."
            ),
            param="model",
            code="model_deprecated",
        )

    request_id = uuid.uuid4().hex
    log.info(
        "chat_request",
        request_id=request_id,
        model_tag=body.model,
        primary=decision.primary.id,
        strategy=str(decision.strategy_used) if decision.strategy_used else "pinned",
        category=str(decision.category),
        stream=body.stream,
        account_id=str(principal.account_id),
        pii_masked=pii_mapping.size if pii_mapping is not None else 0,
    )

    # Persist PII mapping in Redis under request_id (TTL 1h) so the
    # post-flight unmask path can recover it. Empty mapping is a no-op.
    pii_store = PiiMappingStore(get_redis())
    if pii_mapping is not None and not pii_mapping.is_empty():
        await pii_store.save(request_id, pii_mapping)

    failover_kwargs = _failover_overrides(state)

    # --- max_tokens cap по тарифу (Phase 4 P0 — защита от token-cost DoS).
    # Если клиент явно прислал max_tokens > cap его тарифа — 400. Если не
    # прислал — сетка cogs-estimate работает на DEFAULT_ESTIMATED_OUTPUT_TOKENS,
    # реальный размер режется провайдером в default-budget модели (обычно
    # 4-16k). Этот cap предотвращает атаки через welcome-200₽ + max_tokens=131k.
    tariff_cap = TARIFF_MAX_OUTPUT_TOKENS.get(principal.tariff, DEFAULT_TARIFF_MAX_OUTPUT_TOKENS)
    if body.max_tokens is not None and body.max_tokens > tariff_cap:
        log.info(
            "chat_max_tokens_cap_exceeded",
            account_id=str(principal.account_id),
            tariff=principal.tariff,
            requested=body.max_tokens,
            cap=tariff_cap,
        )
        raise invalid_request(
            f"max_tokens={body.max_tokens} exceeds your tariff cap "
            f"({principal.tariff}: {tariff_cap}). Either lower max_tokens or "
            f"upgrade tariff.",
            param="max_tokens",
            code="max_tokens_tariff_cap",
        )

    # --- Pre-flight hold --------------------------------------------------
    # Lock the account row, sum existing holds, and reserve our estimate.
    # Insufficient available balance → 402 BEFORE the upstream call.
    estimated_hold = _estimate_hold_kopecks(
        primary=decision.primary,
        estimated_input_tokens=estimated_input_tokens,
        requested_max_tokens=body.max_tokens,
    )
    try:
        hold = await hold_amount(
            db,
            account_id=principal.account_id,
            amount_kopecks=estimated_hold,
            ref_id=request_id,
            ttl_seconds=DEFAULT_HOLD_TTL_SECONDS,
        )
        await db.commit()
    except InsufficientBalanceError as exc:
        await db.rollback()
        log.info(
            "chat_preflight_402",
            request_id=request_id,
            account_id=str(principal.account_id),
            balance_kopecks=exc.balance_kopecks,
            required_kopecks=exc.required_kopecks,
        )
        raise insufficient_quota("Top up your balance to continue.") from exc
    except BillingError as exc:
        await db.rollback()
        log.error(
            "chat_preflight_billing_error",
            request_id=request_id,
            error=str(exc),
        )
        raise GatewayError(
            status_code=500,
            message="Billing system unavailable.",
            type="api_error",
            code="billing_unavailable",
        ) from exc

    # --- Dispatch ---------------------------------------------------------
    if body.stream:
        return await _stream_response(
            registry=registry,
            decision=decision,
            body=body,
            request=request,
            principal=principal,
            request_id=request_id,
            hold=hold,
            failover_kwargs=failover_kwargs,
            pii_mapping=pii_mapping,
            extended_anthropic_cache=extended_anthropic_cache,
        )

    return await _json_response(
        registry=registry,
        decision=decision,
        body=body,
        principal=principal,
        db=db,
        request_id=request_id,
        hold=hold,
        failover_kwargs=failover_kwargs,
        pii_mapping=pii_mapping,
        extended_anthropic_cache=extended_anthropic_cache,
        request=request,
    )


# ---------- non-stream --------------------------------------------------------


async def _resolve_and_call(
    registry: ProviderRegistry,
    body: ChatCompletionRequestBody,
    model: ModelSpec,
    *,
    extended_anthropic_cache: bool = False,
) -> tuple[ChatCompletionResponse, int]:
    provider = registry.get_for_model(model)
    provider_req = _build_provider_request(
        body, model, extended_anthropic_cache=extended_anthropic_cache
    )
    t0 = time.perf_counter()
    response = await provider.chat_completion(provider_req)
    latency_ms = int((time.perf_counter() - t0) * 1000)
    return response, latency_ms


async def _release_hold_safely(db: AsyncSession, hold: HoldHandle, *, request_id: str) -> None:
    """Release a hold and commit; never raises."""
    try:
        await release_hold(db, hold)
        await db.commit()
    except Exception as exc:
        log.warning(
            "hold_release_failed",
            request_id=request_id,
            ref_id=hold.ref_id,
            error=str(exc),
        )
        await db.rollback()


async def _json_response(
    *,
    registry: ProviderRegistry,
    decision: RoutingDecision,
    body: ChatCompletionRequestBody,
    principal: AuthPrincipal,
    db: AsyncSession,
    request_id: str,
    hold: HoldHandle,
    failover_kwargs: dict[str, Any],
    pii_mapping: PiiMapping | None = None,
    extended_anthropic_cache: bool = False,
    request: Request | None = None,
) -> JSONResponse:
    # Filter the chain to providers we have credentials for.
    configured = registry.configured_providers()
    if decision.primary.provider not in configured:
        # Primary not configured — promote first configured fallback, if any.
        for cand in decision.fallback_chain:
            if cand.provider in configured:
                decision = RoutingDecision(
                    primary=cand,
                    fallback_chain=[
                        m
                        for m in ([decision.primary, *decision.fallback_chain])
                        if m.id != cand.id and m.provider in configured
                    ],
                    strategy_used=decision.strategy_used,
                    category=decision.category,
                    reason=f"{decision.reason}+promoted",
                    requested_model_tag=decision.requested_model_tag,
                )
                break
    chain = [m for m in decision.fallback_chain if m.provider in configured]

    async def _call(model: ModelSpec) -> tuple[ChatCompletionResponse, int]:
        return await _resolve_and_call(
            registry, body, model, extended_anthropic_cache=extended_anthropic_cache
        )

    # --- Provider call. On any failure we must release the hold so the
    # customer's reserved balance is returned. ---
    try:
        try:
            result: FailoverResult[ChatCompletionResponse] = await with_failover(
                decision.primary,
                chain,
                _call,
                failover_enabled=body.failover,
                request_id=request_id,
                **failover_kwargs,
            )
        except ProviderClientError as exc:
            await _release_hold_safely(db, hold, request_id=request_id)
            raise _wrap_provider_error(exc) from exc
        except FailoverError as exc:
            await _release_hold_safely(db, hold, request_id=request_id)
            last = exc.last_error
            if isinstance(last, ProviderError):
                raise _wrap_provider_error(last) from exc
            raise upstream_error("All upstream providers failed.") from exc
    except GatewayError:
        raise
    except Exception:
        # Any unforeseen failure (programmer error, OOM, etc.) — release
        # the hold first so we never silently lock up balance.
        await _release_hold_safely(db, hold, request_id=request_id)
        raise

    response = result.response
    chosen = result.model
    cost = compute_cost_kopecks(chosen, response.usage)
    # Phase 5 #4 BrikkoLens — capture timing snapshot для observability log.
    # Записываем после post-flight commit (см. ниже), чтобы trace
    # отражал успешно списанный запрос.
    finished_at_dt = datetime.now(UTC)
    success_ev = next(
        (ev for ev in result.events if ev.succeeded),
        None,
    )
    obs_latency_ms = success_ev.latency_ms if success_ev and success_ev.latency_ms else 0
    # ``started_at`` not part of FailoverEvent dataclass yet — read via getattr
    # so a future field addition becomes a no-op here.
    _started_at_attr = getattr(success_ev, "started_at", None) if success_ev else None
    obs_started_at = (
        _started_at_attr
        if _started_at_attr is not None
        else finished_at_dt - timedelta(milliseconds=obs_latency_ms or 0)
    )

    # --- Prometheus: record provider call success ----------------------
    # Done before the DB post-flight because we want metrics regardless
    # of whether usage_event persistence succeeds. Latency is the sum of
    # successful-attempt latencies recorded by the failover engine.
    if request is not None:
        from voltari_gateway.utils.observability import record_provider_call

        success_latency_ms = next(
            (ev.latency_ms for ev in result.events if ev.succeeded and ev.latency_ms is not None),
            0,
        )
        record_provider_call(
            request.app,
            provider=chosen.provider.value,
            model=chosen.id,
            status="success",
            latency_seconds=(success_latency_ms or 0) / 1000.0,
            input_tokens=response.usage.prompt_tokens,
            output_tokens=response.usage.completion_tokens,
            billed_kopecks=cost,
        )

    # --- Post-flight: settle hold against actual cost -------------------
    # ``commit_hold_to_debit`` deletes the hold and creates the charge in
    # one transaction. If the actual cost rounds to 0 kopecks (a tiny call
    # under our minimum-billable threshold) we just release the hold —
    # ``debit_account`` rejects amount_kopecks=0. The usage_event is still
    # written so analytics see the request.
    try:
        if cost > 0:
            await commit_hold_to_debit(
                db,
                handle=hold,
                actual_amount_kopecks=cost,
                meta={
                    "request_id": request_id,
                    "model": chosen.id,
                    "input_tokens": response.usage.prompt_tokens,
                    "output_tokens": response.usage.completion_tokens,
                },
            )
        else:
            await release_hold(db, hold)
        event = UsageEvent(
            account_id=principal.account_id,
            api_key_id=principal.api_key_id,
            model=chosen.id,
            # Sprint 11 — denormalised provider for analytics
            # (Alembic 0013). ``ModelSpec.provider`` is a Provider
            # StrEnum so ``.value`` gives the lowercase tag the
            # dashboard groups by.
            provider=chosen.provider.value,
            input_tokens=response.usage.prompt_tokens,
            output_tokens=response.usage.completion_tokens,
            cached_tokens=response.usage.cached_tokens,
            cost_kopecks=cost,
            request_id=request_id,
        )
        db.add(event)
        await db.commit()
    except InsufficientBalanceError as exc:
        # Caught only if the hold was somehow under-sized. Release whatever
        # hold remains (defensive — commit_hold_to_debit deletes it before
        # debit) and return 402.
        await db.rollback()
        await _release_hold_safely(db, hold, request_id=request_id)
        log.error(
            "chat_postflight_under_held",
            request_id=request_id,
            balance=exc.balance_kopecks,
            required=exc.required_kopecks,
        )
        raise insufficient_quota("Top up your balance to continue.") from exc
    except Exception as exc:
        await db.rollback()
        await _release_hold_safely(db, hold, request_id=request_id)
        log.exception("chat_postflight_persist_failed", request_id=request_id)
        raise GatewayError(
            status_code=500,
            message="Failed to record usage.",
            type="api_error",
            code="billing_unavailable",
        ) from exc

    # Phase 5 #4 BrikkoLens — запись trace в gateway_request_log.
    # Fire-and-forget: failure не пробрасывается клиенту (см.
    # observability/request_log.py). Делается ПОСЛЕ billing-commit
    # чтобы trace отражал успешно списанный запрос.
    from voltari_gateway.observability import record_request_log, should_store_bodies

    body_for_trace = (
        body.model_dump() if await should_store_bodies(db, principal.account_id) else None
    )
    await record_request_log(
        db,
        account_id=principal.account_id,
        api_key_id=principal.api_key_id,
        request_id=request_id,
        provider=chosen.provider.value,
        model=chosen.id,
        routed_from=(
            decision.requested_model_tag
            if decision.requested_model_tag and decision.requested_model_tag != chosen.id
            else None
        ),
        started_at=obs_started_at,
        finished_at=finished_at_dt,
        latency_ms=obs_latency_ms or 0,
        ttft_ms=None,
        prompt_tokens=response.usage.prompt_tokens,
        completion_tokens=response.usage.completion_tokens,
        cached_tokens=response.usage.cached_tokens,
        reasoning_tokens=getattr(response.usage, "reasoning_tokens", 0) or 0,
        cost_kop=cost,
        fx_usd_rub=None,
        status="ok",
        http_code=200,
        error_code=None,
        error_message=None,
        is_streaming=False,
        cache_hit=(response.usage.cached_tokens or 0) > 0,
        pii_masked=pii_mapping is not None and not pii_mapping.is_empty(),
        tools_used=bool(body.tools),
        request_body=body_for_trace,
        response_body=dict(response.raw) if body_for_trace else None,
        model_params={
            k: v
            for k, v in {
                "temperature": body.temperature,
                "top_p": body.top_p,
                "max_tokens": body.max_tokens,
                "stream": False,
            }.items()
            if v is not None
        },
        store_bodies=body_for_trace is not None,
    )

    body_out = dict(response.raw)

    # --- PII unmask (Sprint 4 Поток M) -------------------------------------
    # Reload mapping from Redis (in case the in-memory copy got dropped on a
    # different worker). Falls back to the local mapping if Redis lookup
    # returns empty. After unmask we delete the Redis row eagerly to free
    # memory before its TTL.
    if pii_mapping is not None and not pii_mapping.is_empty():
        pii_store = PiiMappingStore(get_redis())
        store_mapping = await pii_store.load(request_id)
        active_mapping = store_mapping if not store_mapping.is_empty() else pii_mapping
        _unmask_response_payload(body_out, active_mapping)
        await pii_store.delete(request_id)

    strategy_label = str(decision.strategy_used) if decision.strategy_used else "pinned"
    routing_block = {
        "strategy": strategy_label,
        "model_chosen": chosen.id,
        "model_requested": decision.requested_model_tag or body.model,
        "category": str(decision.category),
        "fallback_used": result.failover_used,
        "reason": decision.reason,
    }
    body_out["x_gateway"] = {
        "cost_kop": cost,
        "provider": response.provider,
        "model": chosen.id,
        "request_id": request_id,
        "router": {
            "reason": decision.reason,
            "strategy": str(decision.strategy_used) if decision.strategy_used else None,
            "category": str(decision.category),
            "failover_used": result.failover_used,
            "events": _events_to_dicts(result.events),
        },
    }
    # Sprint 8 — top-level ``routing`` mirror of the per-decision metadata.
    # ``x_gateway`` is older/internal (carries cost/events); ``routing`` is
    # the documented client-facing surface (UX-аудит P2-3 — клиенты не
    # видели какую модель мы реально использовали). OpenAI clients ignore
    # unknown top-level keys; SDK callers parse it explicitly.
    body_out["routing"] = routing_block
    headers = {
        "X-Request-Id": request_id,
        "X-Gateway-Provider": response.provider,
        "X-Gateway-Cost-Kop": str(cost),
        "X-Router-Decision": _decision_header(
            RoutingDecision(
                primary=chosen,
                fallback_chain=decision.fallback_chain,
                strategy_used=decision.strategy_used,
                category=decision.category,
                reason=decision.reason,
                requested_model_tag=decision.requested_model_tag,
            ),
            failover_used=result.failover_used,
        ),
        # Sprint 8 — discrete headers complement X-Router-Decision so
        # logging tools / curl users can read individual fields without
        # parsing the semicolon-encoded composite header. Strategy stays
        # ``pinned`` for explicit-model requests (matches body.routing).
        "X-Router-Strategy": strategy_label,
        "X-Router-Reason": _ascii_header(decision.reason),
        "X-Router-Fallback-Used": "true" if result.failover_used else "false",
    }
    # Sprint 9 — surface upstream deprecation as a header on every response
    # using a deprecated model. Clients can cron-grep for it and start
    # migrating. We deliberately don't 4xx until past the date (see chat
    # body) — this is the soft-warning phase.
    if chosen.deprecated_at is not None:
        headers["X-Brikko-Deprecated"] = (
            f"model={chosen.id};retires={chosen.deprecated_at.isoformat()}"
        )
    # Phase 5 — surface prompt-caching hit-rate per response. Anthropic
    # auto-emits cache_control on system >= threshold; the billing engine
    # bills cached_tokens at 10% of input rate. Showing this lets clients
    # verify they're getting the discount and tune their reuse patterns.
    cached_tokens = response.usage.cached_tokens or 0
    if cached_tokens > 0:
        headers["X-Brikko-Cache-Hit-Tokens"] = str(cached_tokens)
    return JSONResponse(content=body_out, headers=headers)


# ---------- stream ------------------------------------------------------------


async def _open_stream(
    registry: ProviderRegistry,
    body: ChatCompletionRequestBody,
    model: ModelSpec,
    *,
    extended_anthropic_cache: bool = False,
) -> tuple[AsyncIterator[bytes], int]:
    """Open a streaming connection — returns the async iterator + latency-to-first-byte.

    For failover purposes we treat the *open* as the call; if the upstream
    rejects the request before it sends any bytes, we can still failover.
    """
    provider = registry.get_for_model(model)
    provider_req = _build_provider_request(
        body, model, extended_anthropic_cache=extended_anthropic_cache
    )
    t0 = time.perf_counter()
    chunk_iter = await provider.chat_completion_stream(provider_req)
    latency_ms = int((time.perf_counter() - t0) * 1000)
    return chunk_iter, latency_ms


async def _settle_stream_billing(
    *,
    factory: async_sessionmaker[AsyncSession],
    account_id: uuid.UUID,
    api_key_id: uuid.UUID,
    chosen: ModelSpec,
    request_id: str,
    hold: HoldHandle,
    usage_input: int,
    usage_output: int,
    usage_cached: int,
    client_disconnected: bool,
    provider_failed: bool,
) -> None:
    """Apply post-flight billing on a fresh DB session.

    Called from the stream generator's ``finally`` block. Cases:

    * Provider failed before any tokens delivered → release hold, no
      usage_event written.
    * Client disconnected mid-stream → debit only the tokens we delivered
      (``usage_output > 0``), write a partial usage_event flagged with
      ``client_disconnected``.
    * Normal completion → debit actual cost, write the usage_event.

    Uses ``_fresh_session()`` because the request-scoped session attached
    to the original FastAPI dependency is already closed by this point
    (BE P0-15). All DB work here is best-effort: any failure is logged and
    the hold is released so balance isn't permanently reserved.
    """
    delivered_tokens = usage_input or usage_output
    if not delivered_tokens:
        # Nothing to bill for — provider didn't return usage and didn't
        # send content. Either way, release the hold and bail.
        async with _fresh_session(factory) as s:
            await release_hold(s, hold)
            await s.commit()
        log.info(
            "stream_billing_no_usage_release",
            request_id=request_id,
            account_id=str(account_id),
            client_disconnected=client_disconnected,
            provider_failed=provider_failed,
        )
        return

    usage_obj = ChatCompletionUsage(
        prompt_tokens=usage_input,
        completion_tokens=usage_output,
        total_tokens=usage_input + usage_output,
        cached_tokens=usage_cached,
    )
    cost = compute_cost_kopecks(chosen, usage_obj)

    async with _fresh_session(factory) as s:
        try:
            if cost > 0:
                await commit_hold_to_debit(
                    s,
                    handle=hold,
                    actual_amount_kopecks=cost,
                    meta={
                        "request_id": request_id,
                        "model": chosen.id,
                        "input_tokens": usage_input,
                        "output_tokens": usage_output,
                        "stream": True,
                        "client_disconnected": client_disconnected,
                    },
                )
            else:
                # Sub-kopeck calls — release the hold without debiting.
                # Usage event is still written so analytics tracks the call.
                await release_hold(s, hold)
            event = UsageEvent(
                account_id=account_id,
                api_key_id=api_key_id,
                model=chosen.id,
                # Sprint 11 — see same field on the non-stream path.
                provider=chosen.provider.value,
                input_tokens=usage_input,
                output_tokens=usage_output,
                cached_tokens=usage_cached,
                cost_kopecks=cost,
                request_id=request_id,
            )
            s.add(event)
            await s.commit()
            log.info(
                "stream_billing_settled",
                request_id=request_id,
                account_id=str(account_id),
                cost_kopecks=cost,
                input_tokens=usage_input,
                output_tokens=usage_output,
                client_disconnected=client_disconnected,
            )
        except InsufficientBalanceError:
            # Hold was under-sized for the actual delivered work — log loudly,
            # release the remaining hold, and let it go. We already shipped
            # bytes to the client, can't refuse retroactively.
            await s.rollback()
            async with _fresh_session(factory) as s2:
                await release_hold(s2, hold)
                await s2.commit()
            log.error(
                "stream_billing_under_held",
                request_id=request_id,
                account_id=str(account_id),
            )
        except Exception:
            await s.rollback()
            async with _fresh_session(factory) as s2:
                await release_hold(s2, hold)
                await s2.commit()
            log.exception(
                "stream_billing_failed",
                request_id=request_id,
                account_id=str(account_id),
            )


async def _stream_response(
    *,
    registry: ProviderRegistry,
    decision: RoutingDecision,
    body: ChatCompletionRequestBody,
    request: Request,
    principal: AuthPrincipal,
    request_id: str,
    hold: HoldHandle,
    failover_kwargs: dict[str, Any],
    pii_mapping: PiiMapping | None = None,
    extended_anthropic_cache: bool = False,
) -> EventSourceResponse:
    configured = registry.configured_providers()
    chain = [m for m in decision.fallback_chain if m.provider in configured]

    async def _call(model: ModelSpec) -> tuple[AsyncIterator[bytes], int]:
        return await _open_stream(
            registry, body, model, extended_anthropic_cache=extended_anthropic_cache
        )

    # Phase 5 #4 BrikkoLens — snapshot started_at для observability log.
    # Берём ДО with_failover чтобы latency_ms покрывал полный цикл от
    # принятия запроса до закрытия стрима.
    started_at_dt = datetime.now(UTC)

    # Snapshot the session factory NOW. By the time the generator's
    # ``finally`` runs (after the client has finished reading the SSE
    # stream), the global may have been swapped — notably between tests
    # where each fixture rebuilds the engine. Holding the factory by
    # reference pins us to the engine in scope at request time.
    factory = get_session_factory()

    try:
        result = await with_failover(
            decision.primary,
            chain,
            _call,
            failover_enabled=body.failover,
            request_id=request_id,
            **failover_kwargs,
        )
    except ProviderClientError as exc:
        # Pre-stream provider failure → release hold via fresh session.
        async with _fresh_session(factory) as s:
            await release_hold(s, hold)
            await s.commit()
        raise _wrap_provider_error(exc) from exc
    except FailoverError as exc:
        async with _fresh_session(factory) as s:
            await release_hold(s, hold)
            await s.commit()
        last = exc.last_error
        if isinstance(last, ProviderError):
            raise _wrap_provider_error(last) from exc
        raise upstream_error("All upstream providers failed.") from exc

    chunk_iter = result.response
    chosen = result.model
    failover_used = result.failover_used

    # Snapshot all values the generator needs — by the time `finally`
    # runs FastAPI may have torn down the request scope.
    account_id = principal.account_id
    api_key_id = principal.api_key_id

    pii_active = pii_mapping is not None and not pii_mapping.is_empty()

    # Phase 5 #4 BrikkoLens — snapshot для записи trace в gateway_request_log.
    # Счётчики usage_input/output/cached накапливаются в _gen() и доступны в
    # finally. Тут фиксируем то, что не зависит от стрима: модель, body, и т.п.
    routed_from_for_log = (
        decision.requested_model_tag
        if decision.requested_model_tag and decision.requested_model_tag != chosen.id
        else None
    )
    body_tools_used = bool(getattr(body, "tools", None))
    model_params_for_log = {
        k: v
        for k, v in {
            "temperature": body.temperature,
            "top_p": body.top_p,
            "max_tokens": body.max_tokens,
            "stream": True,
        }.items()
        if v is not None
    }
    body_dump_for_log = body.model_dump()
    ttft_ms_for_log: int | None = None
    success_ev_stream = next(
        (ev for ev in result.events if ev.succeeded),
        None,
    )
    if success_ev_stream and success_ev_stream.latency_ms is not None:
        ttft_ms_for_log = int(success_ev_stream.latency_ms)
    # Sprint 13 / Privacy v2 — Phase 3.  Carry-buffer unmasker for
    # ``delta.content`` text so a placeholder split across two SSE chunks
    # (e.g. ``<NAM`` + ``E_1>``) is restored atomically.  Tool-call
    # arguments still go through the JSON-level ``_unmask_response_payload``
    # — providers stream them as discrete JSON-string deltas where the
    # split risk is materially lower (placeholders rarely sit inside
    # function args) and per-call buffering would require per-tool-call
    # state.  See ``voltari_gateway.pii.streaming`` for the algorithm.
    stream_unmasker = (
        StreamUnmasker(mapping=pii_mapping) if pii_active and pii_mapping is not None else None
    )

    async def _gen() -> AsyncIterator[dict[str, str]]:
        usage_input = 0
        usage_output = 0
        usage_cached = 0
        client_disconnected = False
        provider_failed = False
        try:
            async for raw in chunk_iter:
                if await request.is_disconnected():
                    client_disconnected = True
                    log.info("client_disconnected", request_id=request_id)
                    break
                text = raw.decode("utf-8") if isinstance(raw, bytes) else raw
                for line in text.splitlines():
                    if not line.startswith("data: "):
                        continue
                    payload = line[len("data: ") :].strip()
                    if payload == "[DONE]":
                        yield {"data": "[DONE]"}
                        continue
                    try:
                        parsed = json.loads(payload)
                        usage = parsed.get("usage")
                        if usage:
                            usage_input = int(usage.get("prompt_tokens") or usage_input)
                            usage_output = int(usage.get("completion_tokens") or usage_output)
                            usage_cached = int(
                                (usage.get("prompt_tokens_details") or {}).get(
                                    "cached_tokens", usage_cached
                                )
                            )
                        # PII unmask on stream chunks.  Two-stage hybrid:
                        #   1. ``StreamUnmasker.feed`` on every
                        #      ``choices[].delta.content`` string — handles
                        #      placeholders split across SSE chunks.
                        #   2. JSON-level ``_unmask_response_payload`` for
                        #      tool_calls and any non-string content blocks
                        #      (vision/multimodal deltas — extremely rare).
                        if stream_unmasker is not None and pii_mapping is not None:
                            _stream_unmask_chunk(parsed, stream_unmasker)
                            _unmask_response_payload(parsed, pii_mapping)
                            payload = json.dumps(parsed, ensure_ascii=False)
                    except Exception:
                        pass
                    yield {"data": payload}
            # End-of-stream: flush any held-over carry buffer as a final
            # synthetic delta chunk so the client never loses bytes.
            if stream_unmasker is not None:
                tail = stream_unmasker.flush()
                if tail:
                    yield {
                        "data": json.dumps(
                            {"choices": [{"index": 0, "delta": {"content": tail}}]},
                            ensure_ascii=False,
                        )
                    }
        except ProviderError as exc:
            provider_failed = True
            log.warning("stream_provider_error", request_id=request_id, error=str(exc))
            err_body = {
                "error": {
                    "message": str(exc) or "Upstream provider error.",
                    "type": "api_error",
                    "code": "upstream_error",
                }
            }
            yield {"data": json.dumps(err_body, ensure_ascii=False)}
        finally:
            # Always settle billing — even on cancel / provider failure /
            # client disconnect. Uses a *fresh* session because the
            # FastAPI-scoped one is already closed by the time we get here.
            await _settle_stream_billing(
                factory=factory,
                account_id=account_id,
                api_key_id=api_key_id,
                chosen=chosen,
                request_id=request_id,
                hold=hold,
                usage_input=usage_input,
                usage_output=usage_output,
                usage_cached=usage_cached,
                client_disconnected=client_disconnected,
                provider_failed=provider_failed,
            )

            # Phase 5 #4 BrikkoLens — write trace row на fresh session ПОСЛЕ
            # settle. Fire-and-forget: failure не блокирует release PII / стрим
            # уже закрыт. Логируем только запросы, где есть бизнес-смысл:
            # успех (есть usage), provider error, или client cancel с usage.
            # Полностью пустые запросы (provider не успел ничего отдать,
            # без ошибки) пропускаем — это шум.
            try:
                from voltari_gateway.observability import (
                    record_request_log,
                    should_store_bodies,
                )

                finished_at_dt = datetime.now(UTC)
                latency_ms_log = max(
                    0,
                    int((finished_at_dt - started_at_dt).total_seconds() * 1000),
                )
                has_usage = (usage_input or usage_output) > 0
                status_log: str | None
                error_code_log: str | None
                http_code_log: int | None
                if provider_failed:
                    status_log = "error"
                    error_code_log = "upstream_error"
                    http_code_log = 502
                elif client_disconnected and not has_usage:
                    status_log = "cancelled"
                    error_code_log = None
                    http_code_log = 499
                elif has_usage:
                    status_log = "ok"
                    error_code_log = None
                    http_code_log = 200
                else:
                    # Empty request, no error, no usage — нечего логировать.
                    status_log = None
                    error_code_log = None
                    http_code_log = None

                if status_log is not None:
                    cost_for_log = 0
                    if has_usage:
                        cost_for_log = compute_cost_kopecks(
                            chosen,
                            ChatCompletionUsage(
                                prompt_tokens=usage_input,
                                completion_tokens=usage_output,
                                total_tokens=usage_input + usage_output,
                                cached_tokens=usage_cached,
                            ),
                        )

                    async with _fresh_session(factory) as log_session:
                        store_bodies = await should_store_bodies(log_session, account_id)
                        await record_request_log(
                            log_session,
                            account_id=account_id,
                            api_key_id=api_key_id,
                            request_id=request_id,
                            provider=chosen.provider.value,
                            model=chosen.id,
                            routed_from=routed_from_for_log,
                            started_at=started_at_dt,
                            finished_at=finished_at_dt,
                            latency_ms=latency_ms_log,
                            ttft_ms=ttft_ms_for_log,
                            prompt_tokens=usage_input,
                            completion_tokens=usage_output,
                            cached_tokens=usage_cached,
                            reasoning_tokens=0,
                            cost_kop=cost_for_log,
                            fx_usd_rub=None,
                            status=status_log,
                            http_code=http_code_log,
                            error_code=error_code_log,
                            error_message=None,
                            is_streaming=True,
                            cache_hit=usage_cached > 0,
                            pii_masked=pii_active,
                            tools_used=body_tools_used,
                            request_body=body_dump_for_log if store_bodies else None,
                            response_body=None,
                            model_params=model_params_for_log,
                            store_bodies=store_bodies,
                        )
            except Exception:
                # Никогда не пробрасываем — стрим уже закрыт у клиента,
                # release_hold уже отработал, мы не имеем права упасть.
                log.warning(
                    "stream_request_log_failed",
                    request_id=request_id,
                    exc_info=True,
                )

            # Eagerly free the PII Redis row — TTL would handle it eventually
            # but releasing on stream-completion keeps Redis tidy.
            if pii_active:
                stream_pii_store = PiiMappingStore(get_redis())
                await stream_pii_store.delete(request_id)

    strategy_label = str(decision.strategy_used) if decision.strategy_used else "pinned"
    headers = {
        "X-Request-Id": request_id,
        "X-Gateway-Provider": chosen.provider.value,
        "X-Router-Decision": _decision_header(
            RoutingDecision(
                primary=chosen,
                fallback_chain=decision.fallback_chain,
                strategy_used=decision.strategy_used,
                category=decision.category,
                reason=decision.reason,
                requested_model_tag=decision.requested_model_tag,
            ),
            failover_used=failover_used,
        ),
        # Sprint 8 — discrete routing-decision headers (see _json_response
        # for the doc-comment). Streamed responses have no body slot to
        # surface the routing block, so headers carry the full picture.
        "X-Router-Strategy": strategy_label,
        "X-Router-Reason": _ascii_header(decision.reason),
        "X-Router-Fallback-Used": "true" if failover_used else "false",
    }
    if chosen.deprecated_at is not None:
        headers["X-Brikko-Deprecated"] = (
            f"model={chosen.id};retires={chosen.deprecated_at.isoformat()}"
        )
    return EventSourceResponse(
        _gen(),
        ping=15,
        headers=headers,
    )
