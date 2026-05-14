"""POST /v1/messages — native Anthropic Messages API endpoint.

Lets clients that speak Anthropic's wire format (Claude Code SDK,
Anthropic Python SDK with ``ANTHROPIC_BASE_URL`` set, Cursor when
configured for direct Anthropic) point at Brikko by changing only the
base URL — no code changes.

Pipeline mirrors ``/v1/chat/completions``:

    1. Auth (require_api_key dependency) — same Bearer ``sk-vlt-...``.
    2. Validate Pydantic body (Anthropic Messages shape).
    3. Pre-flight billing hold (same engine, same MARKUP).
    4. Route via Router — Anthropic-shaped request gets categorised by
       a synthesised prompt-text and routed identically to /chat/completions.
    5. Dispatch:
       - Routed primary == Anthropic → call
         ``AnthropicProvider.chat_completion_anthropic_native()`` and pass
         the body 1:1 (no OpenAI-shape detour). Streaming pass-through.
       - Routed primary != Anthropic → translate body to OpenAI shape,
         call the chosen adapter via ``provider.chat_completion()``, then
         re-shape the response back to Anthropic Messages shape.
    6. Post-flight billing (commit hold → debit + UsageEvent).

We deliberately do NOT add OpenAI's ``Responses API`` (``/v1/responses``)
in this sprint — see Sprint 11.6 brief. A TODO marker at the bottom of
``main.py`` tracks it.

Cross-provider failover trade-off: when the primary is Anthropic and a
fallback is non-Anthropic, the fallback path requires the
Anthropic→OpenAI translation. We currently disable cross-shape failover
to keep this endpoint simple — the failover chain is filtered to
Anthropic-only providers when the primary is Anthropic. If Anthropic is
down entirely we surface 503; clients can retry with explicit
``model: deepseek-v4-pro`` to force translation. This avoids the
debugging hell of "the response shape changed mid-failover" and matches
the contract Anthropic's own SDK callers expect.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends
from fastapi.requests import Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field, StrictBool, field_validator
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from voltari_gateway.auth.middleware import AuthPrincipal
from voltari_gateway.auth.middleware import get_redis as get_pii_redis
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
    stream_unmask_anthropic_event,
    unmask_payload_anthropic,
)
from voltari_gateway.pii import (
    audit_summary as _pii_audit_summary,
)
from voltari_gateway.providers.anthropic_provider import AnthropicProvider
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
from voltari_gateway.router.catalog import Provider as ProviderEnum
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
from voltari_gateway.router.router import Router as RouterEngine
from voltari_gateway.utils.errors import (
    GatewayError,
    insufficient_quota,
    invalid_request,
    model_not_found,
    upstream_error,
)
from voltari_gateway.utils.logging import get_logger
from voltari_gateway.utils.messages_translation import (
    anthropic_to_openai_messages,
    anthropic_tool_choice_to_openai,
    anthropic_tools_to_openai,
    openai_response_to_anthropic_message,
    openai_stream_to_anthropic_events,
)

router = APIRouter()
log = get_logger(__name__)

# Anthropic spec: ``max_tokens`` is REQUIRED (unlike OpenAI which defaults).
# We keep it required at the pydantic layer so callers see a clear 422
# instead of a billing/spend surprise.

DEFAULT_HOLD_BUFFER: Decimal = Decimal("1.5")


# ---------- pydantic body ----------------------------------------------------


class AnthropicMessage(BaseModel):
    role: Literal["user", "assistant"]
    # Anthropic spec: content is either a string OR a list of content
    # blocks. We don't tighten the block schema here — provider adapters
    # validate further, and an over-strict schema would break legitimate
    # multimodal payloads we haven't enumerated.
    content: str | list[dict[str, Any]]


class MessagesRequestBody(BaseModel):
    model: str
    messages: list[AnthropicMessage] = Field(min_length=1)
    max_tokens: int = Field(ge=1, le=200_000)

    # Optional Anthropic fields.
    system: str | list[dict[str, Any]] | None = None
    temperature: float | None = Field(default=None, ge=0.0, le=1.0)
    top_p: float | None = Field(default=None, ge=0.0, le=1.0)
    top_k: int | None = Field(default=None, ge=0)
    stop_sequences: list[str] | None = None
    stream: StrictBool = False
    tools: list[dict[str, Any]] | None = None
    tool_choice: dict[str, Any] | None = None
    metadata: dict[str, Any] | None = None
    # Anthropic 4.7+ extended thinking — passed through when primary is
    # Anthropic, dropped otherwise (no equivalent on other providers).
    thinking: dict[str, Any] | None = None

    # Voltari routing knobs (ignored by stock Anthropic clients; we accept
    # them for parity with /v1/chat/completions so power users have the
    # same overrides on either endpoint).
    failover: bool = True
    exclude_providers: list[str] | None = None

    # Sprint 13 / Privacy v2 — Phase 4. Per-request PII opt-in (parity with
    # /v1/chat/completions). Three-way gate: this body field OR the
    # ``X-PII-Protect`` header OR the account-level ``pii_masking_enabled``
    # flag enables masking for the request. Unknown to stock Anthropic
    # clients (``model_config = extra="ignore"`` keeps them happy).
    pii_protect: bool | None = None

    model_config = {"extra": "ignore"}

    @field_validator("messages")
    @classmethod
    def _validate_messages(cls, v: list[AnthropicMessage]) -> list[AnthropicMessage]:
        # Same caps as /v1/chat/completions to deny multimegabyte abuse.
        max_per_block = 200_000
        max_total = 400_000
        total = 0
        for m in v:
            c = m.content
            if isinstance(c, str):
                if len(c) > max_per_block:
                    raise ValueError("messages[].content exceeds 200000 chars")
                total += len(c)
            elif isinstance(c, list):
                for part in c:
                    if not isinstance(part, dict):
                        continue
                    text = part.get("text")
                    if isinstance(text, str):
                        if len(text) > max_per_block:
                            raise ValueError("messages[].content[].text exceeds 200000 chars")
                        total += len(text)
        if total > max_total:
            raise ValueError(f"messages aggregate text exceeds {max_total} chars")
        return v


# ---------- helpers ----------------------------------------------------------


def _gather_prompt_text(body: MessagesRequestBody) -> str:
    parts: list[str] = []
    if isinstance(body.system, str):
        parts.append(body.system)
    elif isinstance(body.system, list):
        for block in body.system:
            if isinstance(block, dict) and block.get("type") == "text":
                txt = block.get("text", "")
                if isinstance(txt, str):
                    parts.append(txt)
    for m in body.messages:
        c = m.content
        if isinstance(c, str):
            parts.append(c)
        elif isinstance(c, list):
            for part in c:
                if isinstance(part, dict):
                    text = part.get("text")
                    if isinstance(text, str):
                        parts.append(text)
    return "\n".join(parts)[:4000]


def _estimate_input_tokens(body: MessagesRequestBody) -> int:
    chars = 0
    if isinstance(body.system, str):
        chars += len(body.system)
    elif isinstance(body.system, list):
        for block in body.system:
            if isinstance(block, dict):
                text = block.get("text") or ""
                chars += len(str(text))
    for m in body.messages:
        c = m.content
        if isinstance(c, str):
            chars += len(c)
        elif isinstance(c, list):
            for part in c:
                if isinstance(part, dict):
                    text = part.get("text") or ""
                    chars += len(str(text))
    return max(1, chars // 3)


def _estimate_hold_kopecks(*, primary: ModelSpec, body: MessagesRequestBody) -> int:
    from voltari_gateway.billing import MARKUP

    cogs = primary.expected_cost_kop(
        input_tokens=_estimate_input_tokens(body),
        output_tokens=body.max_tokens,
    )
    return max(1, int(cogs * MARKUP * DEFAULT_HOLD_BUFFER))


def _exclude_set(values: list[str] | None) -> frozenset[ProviderEnum]:
    if not values:
        return frozenset()
    out: list[ProviderEnum] = []
    for v in values:
        try:
            out.append(ProviderEnum(v))
        except ValueError:
            continue
    return frozenset(out)


def _wrap_provider_error(exc: ProviderError) -> GatewayError:
    if isinstance(exc, ProviderAuthError):
        return upstream_error("Upstream authentication failed.")
    if isinstance(exc, ProviderRateLimitError):
        return upstream_error("Upstream rate limit reached. Try again shortly.")
    if isinstance(exc, ProviderTimeoutError):
        return upstream_error("Upstream provider timed out.")
    if isinstance(exc, ProviderClientError):
        return invalid_request(f"Upstream rejected the request: {exc}", code="upstream_invalid")
    if isinstance(exc, ProviderServerError):
        return upstream_error("Upstream provider error.")
    return upstream_error(str(exc) or "Upstream provider error.")


def _build_account_ctx(principal: AuthPrincipal) -> AccountContext:
    return AccountContext(
        account_id=principal.account_id,
        tariff=principal.tariff,
        balance_kop=principal.balance_kopecks,
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


@asynccontextmanager
async def _fresh_session(
    factory: async_sessionmaker[AsyncSession] | None = None,
) -> AsyncIterator[AsyncSession]:
    """Mirror of chat.py — fresh session for stream post-flight billing."""
    f = factory if factory is not None else get_session_factory()
    async with f() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


async def _release_hold_safely(db: AsyncSession, hold: HoldHandle, *, request_id: str) -> None:
    try:
        await release_hold(db, hold)
        await db.commit()
    except Exception as exc:
        log.warning(
            "messages_hold_release_failed",
            request_id=request_id,
            ref_id=hold.ref_id,
            error=str(exc),
        )
        await db.rollback()


# ---------- endpoint ---------------------------------------------------------


@router.post(
    "/v1/messages",
    response_model=None,
    tags=["messages"],
    summary="Anthropic Messages API (native shape)",
    description=(
        "Native Anthropic Messages API endpoint — point ``ANTHROPIC_BASE_URL`` "
        "at this URL and existing Anthropic SDK / Claude Code SDK clients "
        "work unchanged.\n\n"
        "**Auth**: Bearer ``sk-vlt-...`` (NOT Anthropic's ``x-api-key`` "
        "header — we keep the Brikko auth contract uniform across endpoints).\n\n"
        "**Routing**: pass ``model='auto:code'`` / pinned Anthropic id "
        "(``claude-sonnet-4.6``) / any Brikko-cataloged model id. When the "
        "routed primary isn't Anthropic, the body is translated to "
        "OpenAI-shape, called against the chosen provider, and the "
        "response is re-shaped into Anthropic Messages shape on the way "
        "out.\n\n"
        "**Billing**: same pre-flight hold + post-flight commit pipeline "
        "as ``/v1/chat/completions``."
    ),
    responses={
        200: {"description": "Message response (or SSE stream when stream=true)."},
        401: {"description": "Missing or invalid Bearer token."},
        402: {"description": "Insufficient balance — top up via /v1/billing/topup."},
        429: {"description": "Per-account rate limit exceeded."},
        503: {"description": "All providers in the failover chain failed."},
    },
)
async def messages_create(
    body: MessagesRequestBody,
    request: Request,
    principal: Annotated[
        AuthPrincipal, Depends(require_api_key_or_oauth_scope(OAuthScope.MESSAGES_READ))
    ],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> JSONResponse | StreamingResponse:
    from voltari_gateway.middleware.rate_limit import enforce_chat_rate_limit

    await enforce_chat_rate_limit(request, principal)

    # --- PII masking gate (Sprint 13 / Privacy v2 — Phase 4) -------------
    # Three-way trigger (any → enabled): account flag, X-PII-Protect
    # header, or body field ``pii_protect``. Mirrors /v1/chat/completions.
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
        # Mask the messages list. ``mask_messages`` already handles both
        # Anthropic block-list ``content`` (``[{"type":"text","text":...}]``)
        # and the simpler string ``content`` shape because
        # ``_mask_content_block`` recurses into the ``text`` field of any
        # block dict. We feed plain dicts (not pydantic objects) because
        # masking returns dicts.
        masked, pii_mapping = mask_messages([m.model_dump() for m in body.messages])

        # Anthropic ships ``system`` as a top-level field, not inside the
        # messages list. Mask it via the same helper if present so PII in
        # system prompts gets the same protection.
        if body.system is not None:
            sys_masked, pii_mapping = mask_messages(
                [{"role": "system", "content": body.system}], mapping=pii_mapping
            )
            body.system = sys_masked[0]["content"]

        if not pii_mapping.is_empty():
            body.messages = [AnthropicMessage(**m) for m in masked]
            log.info(
                "messages_pii_masked",
                account_id=str(principal.account_id),
                pii_summary=[
                    {"type": e.pii_type, "count": e.count} for e in _pii_audit_summary(pii_mapping)
                ],
            )

    # Fast-fail PAYG with no balance.
    if principal.balance_kopecks <= 0 and principal.tariff == "payg":
        raise insufficient_quota("Top up your balance to continue.")

    state = request.app.state
    registry: ProviderRegistry = getattr(state, "provider_registry", None)  # type: ignore[assignment]
    router_engine: RouterEngine = getattr(state, "router_engine", None)  # type: ignore[assignment]
    if registry is None:
        registry = ProviderRegistry()
    if router_engine is None:
        router_engine = RouterEngine()

    # --- Routing ---------------------------------------------------------
    estimated_input_tokens = _estimate_input_tokens(body)
    prompt_text = _gather_prompt_text(body)

    all_providers = frozenset(ProviderEnum)
    configured = registry.configured_providers()
    pinned_spec = get_model(body.model) if not body.model.startswith("auto") else None
    auto_exclude = all_providers - configured
    if pinned_spec is not None:
        auto_exclude = auto_exclude - {pinned_spec.provider}
    user_exclude = _exclude_set(body.exclude_providers)

    router_req = RouterRequest(
        model_tag=body.model,
        estimated_input_tokens=estimated_input_tokens,
        prompt_text=prompt_text,
        failover_enabled=body.failover,
        exclude_providers=user_exclude | auto_exclude,
        require_tools=bool(body.tools),
    )
    account_ctx = _build_account_ctx(principal)

    # Sprint S1 — Smart Router v2 pipeline gate (same logic as
    # ``api/chat.py``). Flag-off (production default) → legacy v1
    # path. Flag-on (env or per-account) → run pipeline; in S1 it's
    # behaviour-identical to v1 (only BaseRouterStage is active).
    use_pipeline_v2 = should_use_pipeline(
        env_flag=get_settings().smart_router_v2_enabled,
        account_flag=principal.smart_router_v2_enabled,
    )
    try:
        if use_pipeline_v2:
            pipeline: RouterPipeline | None = getattr(state, "router_pipeline", None)
            if pipeline is None:
                pipeline = RouterPipeline(router_engine)
            pipeline_ctx = PipelineContext(
                router_request=router_req,
                account=account_ctx,
                request_id=uuid.uuid4().hex,
                session_id=request.headers.get("x-session-id"),
                budget_kopecks=parse_budget_kopecks_header(request.headers.get("x-budget-kopecks")),
                task_hint=request.headers.get("x-task-hint"),
            )
            decision = (await pipeline.run(pipeline_ctx)).decision
        else:
            decision = await router_engine.route_request(router_req, account_ctx)
    except RoutingError as err:
        if err.reason_code == "unknown_model":
            raise model_not_found(body.model) from err
        if err.reason_code == "context_too_large":
            raise invalid_request(err.message, code=err.reason_code) from err
        raise invalid_request(err.message, code=err.reason_code) from err

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

    # Refuse calls to a deprecated model past sunset (mirrors chat.py).
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
        "messages_request",
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
    if pii_mapping is not None and not pii_mapping.is_empty():
        pii_store = PiiMappingStore(get_pii_redis())
        await pii_store.save(request_id, pii_mapping)

    # --- max_tokens cap по тарифу (Phase 4 P0 — ту же стенку что в chat.py).
    # Anthropic-spec требует max_tokens (всегда передаётся), поэтому здесь
    # просто валидируем верхнюю границу.
    from voltari_gateway.api.chat import (
        DEFAULT_TARIFF_MAX_OUTPUT_TOKENS,
        TARIFF_MAX_OUTPUT_TOKENS,
    )

    tariff_cap = TARIFF_MAX_OUTPUT_TOKENS.get(principal.tariff, DEFAULT_TARIFF_MAX_OUTPUT_TOKENS)
    if body.max_tokens > tariff_cap:
        log.info(
            "messages_max_tokens_cap_exceeded",
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

    # --- Pre-flight hold -------------------------------------------------
    estimated_hold = _estimate_hold_kopecks(primary=decision.primary, body=body)
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
        raise insufficient_quota("Top up your balance to continue.") from exc
    except BillingError as exc:
        await db.rollback()
        log.error("messages_preflight_billing_error", request_id=request_id, error=str(exc))
        raise GatewayError(
            status_code=500,
            message="Billing system unavailable.",
            type="api_error",
            code="billing_unavailable",
        ) from exc

    # --- Dispatch --------------------------------------------------------
    if body.stream:
        return await _stream_response(
            registry=registry,
            decision=decision,
            body=body,
            request=request,
            principal=principal,
            request_id=request_id,
            hold=hold,
            pii_mapping=pii_mapping,
        )
    return await _json_response(
        registry=registry,
        decision=decision,
        body=body,
        principal=principal,
        db=db,
        request_id=request_id,
        hold=hold,
        pii_mapping=pii_mapping,
    )


# ---------- non-stream -------------------------------------------------------


def _resolve_anthropic_provider(
    registry: ProviderRegistry,
) -> AnthropicProvider:
    """Return the Anthropic adapter — typed narrowly for the native call."""
    p = registry.get(ProviderEnum.ANTHROPIC)
    if not isinstance(p, AnthropicProvider):
        # The registry-level check upstream already guarantees Anthropic
        # is configured; this branch is purely a type narrowing aid.
        raise GatewayError(
            status_code=503,
            message="Anthropic adapter unavailable.",
            type="api_error",
            code="provider_unavailable",
        )
    return p


async def _call_anthropic_native(
    *,
    registry: ProviderRegistry,
    body: MessagesRequestBody,
    model: ModelSpec,
) -> tuple[dict[str, Any], ChatCompletionUsage, int]:
    """Pass body 1:1 to Anthropic; return (raw_message, usage, latency_ms)."""
    provider = _resolve_anthropic_provider(registry)
    t0 = time.perf_counter()
    raw = await provider.chat_completion_anthropic_native(
        model_upstream_id=model.upstream_id,
        messages=[m.model_dump() for m in body.messages],
        max_tokens=body.max_tokens,
        system=body.system,
        temperature=body.temperature,
        top_p=body.top_p,
        top_k=body.top_k,
        stop_sequences=body.stop_sequences,
        tools=body.tools,
        tool_choice=body.tool_choice,
        metadata=body.metadata,
        thinking=body.thinking,
    )
    latency_ms = int((time.perf_counter() - t0) * 1000)

    usage_dict = raw.get("usage") or {}
    input_tokens = int(usage_dict.get("input_tokens") or 0)
    cache_read = int(usage_dict.get("cache_read_input_tokens") or 0)
    cache_creation = int(usage_dict.get("cache_creation_input_tokens") or 0)
    output_tokens = int(usage_dict.get("output_tokens") or 0)
    usage = ChatCompletionUsage(
        prompt_tokens=input_tokens + cache_creation,
        completion_tokens=output_tokens,
        total_tokens=input_tokens + cache_creation + output_tokens,
        cached_tokens=cache_read,
    )
    # Stamp the public Brikko model id so SDK callers see what they asked
    # for, not Anthropic's dashed upstream id.
    raw["model"] = model.id
    return raw, usage, latency_ms


async def _call_translated(
    *,
    registry: ProviderRegistry,
    body: MessagesRequestBody,
    model: ModelSpec,
) -> tuple[dict[str, Any], ChatCompletionUsage, int]:
    """Translate Anthropic body → OpenAI shape, call provider, re-shape response."""
    provider = registry.get_for_model(model)

    openai_messages = anthropic_to_openai_messages(
        system=body.system,
        messages=[m.model_dump() for m in body.messages],
    )
    extra: dict[str, Any] = {}
    if body.tools:
        extra["tools"] = anthropic_tools_to_openai(body.tools)
    if body.tool_choice is not None:
        extra["tool_choice"] = anthropic_tool_choice_to_openai(body.tool_choice)
    if body.metadata is not None:
        extra["user"] = body.metadata.get("user_id")  # closest OpenAI analog

    provider_req = ChatCompletionRequest(
        model=model,
        messages=openai_messages,
        temperature=body.temperature,
        top_p=body.top_p,
        max_tokens=body.max_tokens,
        stream=False,
        stop=body.stop_sequences,
        extra={k: v for k, v in extra.items() if v is not None},
    )
    t0 = time.perf_counter()
    response: ChatCompletionResponse = await provider.chat_completion(provider_req)
    latency_ms = int((time.perf_counter() - t0) * 1000)
    raw = openai_response_to_anthropic_message(response.raw, model_id=model.id)
    return raw, response.usage, latency_ms


async def _json_response(
    *,
    registry: ProviderRegistry,
    decision: RoutingDecision,
    body: MessagesRequestBody,
    principal: AuthPrincipal,
    db: AsyncSession,
    request_id: str,
    hold: HoldHandle,
    pii_mapping: PiiMapping | None = None,
) -> JSONResponse:
    chosen = decision.primary

    # Cross-shape failover trade-off: if primary is Anthropic, we restrict
    # the fallback chain to other Anthropic models (Haiku / Opus) to keep
    # the response shape contract stable. If primary is non-Anthropic, we
    # walk the full chain — the response shape is always Anthropic on
    # output regardless, the translation cost is the same.
    if chosen.provider is ProviderEnum.ANTHROPIC:
        chain_candidates = [
            m for m in decision.fallback_chain if m.provider is ProviderEnum.ANTHROPIC
        ]
    else:
        chain_candidates = list(decision.fallback_chain)

    configured = registry.configured_providers()
    chain_candidates = [m for m in chain_candidates if m.provider in configured]

    # Phase 5 #4 BrikkoLens — start_at для observability log.
    # Берём ДО первого upstream-call. Покрывает ALL failover attempts.
    started_at_dt = datetime.now(UTC)

    last_error: ProviderError | None = None
    raw_message: dict[str, Any] | None = None
    usage: ChatCompletionUsage | None = None
    failover_used = False

    for attempt_idx, model in enumerate([chosen, *chain_candidates]):
        try:
            if model.provider is ProviderEnum.ANTHROPIC:
                raw_message, usage, _latency = await _call_anthropic_native(
                    registry=registry, body=body, model=model
                )
            else:
                raw_message, usage, _latency = await _call_translated(
                    registry=registry, body=body, model=model
                )
            chosen = model
            failover_used = attempt_idx > 0
            break
        except ProviderClientError as exc:
            # 4xx from upstream — don't try fallbacks (the body is broken),
            # release hold and surface to client.
            await _release_hold_safely(db, hold, request_id=request_id)
            raise _wrap_provider_error(exc) from exc
        except ProviderError as exc:
            last_error = exc
            log.warning(
                "messages_provider_error",
                request_id=request_id,
                model=model.id,
                error=str(exc),
            )
            if not body.failover:
                break
            continue
        except LookupError as exc:
            # Provider not configured — try next.
            log.warning(
                "messages_provider_not_configured",
                request_id=request_id,
                model=model.id,
                error=str(exc),
            )
            continue

    if raw_message is None or usage is None:
        await _release_hold_safely(db, hold, request_id=request_id)
        if last_error is not None:
            raise _wrap_provider_error(last_error)
        raise upstream_error("All upstream providers failed.")

    # --- Post-flight billing -----------------------------------------
    cost = compute_cost_kopecks(chosen, usage)
    try:
        if cost > 0:
            await commit_hold_to_debit(
                db,
                handle=hold,
                actual_amount_kopecks=cost,
                meta={
                    "request_id": request_id,
                    "model": chosen.id,
                    "input_tokens": usage.prompt_tokens,
                    "output_tokens": usage.completion_tokens,
                    "endpoint": "messages",
                },
            )
        else:
            await release_hold(db, hold)
        event = UsageEvent(
            account_id=principal.account_id,
            api_key_id=principal.api_key_id,
            model=chosen.id,
            provider=chosen.provider.value,
            input_tokens=usage.prompt_tokens,
            output_tokens=usage.completion_tokens,
            cached_tokens=usage.cached_tokens,
            cost_kopecks=cost,
            request_id=request_id,
        )
        db.add(event)
        await db.commit()
    except InsufficientBalanceError as exc:
        await db.rollback()
        await _release_hold_safely(db, hold, request_id=request_id)
        raise insufficient_quota("Top up your balance to continue.") from exc
    except Exception as exc:
        await db.rollback()
        await _release_hold_safely(db, hold, request_id=request_id)
        log.exception("messages_postflight_persist_failed", request_id=request_id)
        raise GatewayError(
            status_code=500,
            message="Failed to record usage.",
            type="api_error",
            code="billing_unavailable",
        ) from exc

    # Phase 5 #4 BrikkoLens — запись trace в gateway_request_log.
    # ВАЖНО: до PII unmask чтобы в логе осталась masked-версия — это
    # совпадает с поведением chat.py (см. там line 1029).  Fire-and-forget.
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
        store_bodies = await should_store_bodies(db, principal.account_id)
        body_dump_for_log = body.model_dump() if store_bodies else None
        response_for_log = dict(raw_message) if (store_bodies and raw_message is not None) else None
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
            started_at=started_at_dt,
            finished_at=finished_at_dt,
            latency_ms=latency_ms_log,
            ttft_ms=None,
            prompt_tokens=usage.prompt_tokens,
            completion_tokens=usage.completion_tokens,
            cached_tokens=usage.cached_tokens,
            reasoning_tokens=getattr(usage, "reasoning_tokens", 0) or 0,
            cost_kop=cost,
            fx_usd_rub=None,
            status="ok",
            http_code=200,
            error_code=None,
            error_message=None,
            is_streaming=False,
            cache_hit=(usage.cached_tokens or 0) > 0,
            pii_masked=pii_mapping is not None and not pii_mapping.is_empty(),
            tools_used=bool(getattr(body, "tools", None)),
            request_body=body_dump_for_log,
            response_body=response_for_log,
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
            store_bodies=store_bodies,
        )
    except Exception:
        log.warning(
            "messages_request_log_failed",
            request_id=request_id,
            exc_info=True,
        )

    # --- PII unmask (Sprint 13 / Privacy v2 — Phase 4) -------------------
    # Reload mapping from Redis (in case the in-memory copy was dropped on
    # a different worker). Falls back to the local mapping if Redis lookup
    # returns empty. Eagerly delete the Redis row after unmask to free
    # memory before its TTL expires. Anthropic-shape unmask walks
    # ``content[].text`` blocks and ``tool_use.input`` JSON sub-trees.
    if pii_mapping is not None and not pii_mapping.is_empty():
        pii_store = PiiMappingStore(get_pii_redis())
        store_mapping = await pii_store.load(request_id)
        active_mapping = store_mapping if not store_mapping.is_empty() else pii_mapping
        unmask_payload_anthropic(raw_message, active_mapping)
        await pii_store.delete(request_id)

    headers = {
        "X-Request-Id": request_id,
        "X-Gateway-Provider": chosen.provider.value,
        "X-Gateway-Cost-Kop": str(cost),
        "X-Router-Strategy": (str(decision.strategy_used) if decision.strategy_used else "pinned"),
        "X-Router-Fallback-Used": "true" if failover_used else "false",
    }
    if chosen.deprecated_at is not None:
        headers["X-Brikko-Deprecated"] = (
            f"model={chosen.id};retires={chosen.deprecated_at.isoformat()}"
        )
    return JSONResponse(content=raw_message, headers=headers)


# ---------- stream -----------------------------------------------------------


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
) -> None:
    if not (usage_input or usage_output):
        async with _fresh_session(factory) as s:
            await release_hold(s, hold)
            await s.commit()
        return

    usage = ChatCompletionUsage(
        prompt_tokens=usage_input,
        completion_tokens=usage_output,
        total_tokens=usage_input + usage_output,
        cached_tokens=usage_cached,
    )
    cost = compute_cost_kopecks(chosen, usage)
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
                        "endpoint": "messages",
                    },
                )
            else:
                await release_hold(s, hold)
            event = UsageEvent(
                account_id=account_id,
                api_key_id=api_key_id,
                model=chosen.id,
                provider=chosen.provider.value,
                input_tokens=usage_input,
                output_tokens=usage_output,
                cached_tokens=usage_cached,
                cost_kopecks=cost,
                request_id=request_id,
            )
            s.add(event)
            await s.commit()
        except Exception:
            await s.rollback()
            async with _fresh_session(factory) as s2:
                await release_hold(s2, hold)
                await s2.commit()
            log.exception(
                "messages_stream_billing_failed",
                request_id=request_id,
                account_id=str(account_id),
            )


async def _open_native_stream(
    *,
    registry: ProviderRegistry,
    body: MessagesRequestBody,
    model: ModelSpec,
) -> AsyncIterator[bytes]:
    provider = _resolve_anthropic_provider(registry)
    return await provider.chat_completion_anthropic_native_stream(
        model_upstream_id=model.upstream_id,
        messages=[m.model_dump() for m in body.messages],
        max_tokens=body.max_tokens,
        system=body.system,
        temperature=body.temperature,
        top_p=body.top_p,
        top_k=body.top_k,
        stop_sequences=body.stop_sequences,
        tools=body.tools,
        tool_choice=body.tool_choice,
        metadata=body.metadata,
        thinking=body.thinking,
    )


async def _open_translated_stream(
    *,
    registry: ProviderRegistry,
    body: MessagesRequestBody,
    model: ModelSpec,
) -> AsyncIterator[bytes]:
    provider = registry.get_for_model(model)
    openai_messages = anthropic_to_openai_messages(
        system=body.system,
        messages=[m.model_dump() for m in body.messages],
    )
    extra: dict[str, Any] = {}
    if body.tools:
        extra["tools"] = anthropic_tools_to_openai(body.tools)
    if body.tool_choice is not None:
        extra["tool_choice"] = anthropic_tool_choice_to_openai(body.tool_choice)

    provider_req = ChatCompletionRequest(
        model=model,
        messages=openai_messages,
        temperature=body.temperature,
        top_p=body.top_p,
        max_tokens=body.max_tokens,
        stream=True,
        stop=body.stop_sequences,
        extra=extra,
    )
    openai_chunks = await provider.chat_completion_stream(provider_req)
    return openai_stream_to_anthropic_events(openai_chunks, model_id=model.id)


async def _stream_response(
    *,
    registry: ProviderRegistry,
    decision: RoutingDecision,
    body: MessagesRequestBody,
    request: Request,
    principal: AuthPrincipal,
    request_id: str,
    hold: HoldHandle,
    pii_mapping: PiiMapping | None = None,
) -> StreamingResponse:
    chosen = decision.primary
    factory = get_session_factory()

    # Phase 5 #4 BrikkoLens — snapshot started_at для observability log.
    started_at_dt = datetime.now(UTC)

    # Open the upstream stream BEFORE returning — so connection/auth errors
    # surface as a sync 4xx/5xx, not as an empty SSE response.
    try:
        if chosen.provider is ProviderEnum.ANTHROPIC:
            upstream = await _open_native_stream(registry=registry, body=body, model=chosen)
        else:
            upstream = await _open_translated_stream(registry=registry, body=body, model=chosen)
    except ProviderClientError as exc:
        async with _fresh_session(factory) as s:
            await release_hold(s, hold)
            await s.commit()
        raise _wrap_provider_error(exc) from exc
    except ProviderError as exc:
        async with _fresh_session(factory) as s:
            await release_hold(s, hold)
            await s.commit()
        raise _wrap_provider_error(exc) from exc

    account_id = principal.account_id
    api_key_id = principal.api_key_id

    pii_active = pii_mapping is not None and not pii_mapping.is_empty()

    # Phase 5 #4 BrikkoLens — snapshot для записи trace.
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
    # Sprint 13 / Privacy v2 — Phase 4. Carry-buffer unmasker for
    # ``content_block_delta.delta.text`` so a placeholder split across
    # SSE chunks (``<NAM`` + ``E_1>``) is restored atomically. Same
    # algorithm as /v1/chat/completions, just on the Anthropic event
    # shape — see ``stream_unmask_anthropic_event``.
    stream_unmasker = (
        StreamUnmasker(mapping=pii_mapping) if pii_active and pii_mapping is not None else None
    )

    async def _gen() -> AsyncIterator[bytes]:
        usage_input = 0
        usage_output = 0
        usage_cached = 0
        try:
            async for raw in upstream:
                if await request.is_disconnected():
                    break
                # Two paths:
                #   * PII off → peek-only (zero mutation, byte-perfect).
                #   * PII on  → parse → unmask → re-encode each SSE frame.
                # The peek path is unchanged — that's the byte-identical
                # contract for clients that didn't opt into masking.
                import json as _json

                if not pii_active:
                    # Peek-only path: extract usage, forward bytes unchanged.
                    try:
                        text = raw.decode("utf-8") if isinstance(raw, bytes) else raw
                        for line in text.splitlines():
                            if not line.startswith("data: "):
                                continue
                            try:
                                payload = _json.loads(line[len("data: ") :])
                            except _json.JSONDecodeError:
                                continue
                            if not isinstance(payload, dict):
                                continue
                            ptype = payload.get("type")
                            if ptype == "message_start":
                                mu = (payload.get("message") or {}).get("usage") or {}
                                usage_input = int(mu.get("input_tokens") or usage_input)
                                usage_cached = int(
                                    mu.get("cache_read_input_tokens") or usage_cached
                                )
                            elif ptype == "message_delta":
                                mu = payload.get("usage") or {}
                                usage_output = int(mu.get("output_tokens") or usage_output)
                    except Exception:  # pragma: no cover — peek is best-effort
                        pass
                    yield raw
                    continue

                # PII-active path: re-encode each SSE frame after unmask.
                # Anthropic frames are ``event: <name>\ndata: <json>\n\n``;
                # we keep ``event:`` lines verbatim and only mutate the
                # JSON payload of ``data:`` lines.
                try:
                    text = raw.decode("utf-8") if isinstance(raw, bytes) else raw
                except UnicodeDecodeError:
                    yield raw
                    continue

                out_lines: list[str] = []
                for line in text.split("\n"):
                    if not line.startswith("data: "):
                        out_lines.append(line)
                        continue
                    body_str = line[len("data: ") :]
                    try:
                        payload = _json.loads(body_str)
                    except _json.JSONDecodeError:
                        out_lines.append(line)
                        continue
                    if isinstance(payload, dict):
                        ptype = payload.get("type")
                        if ptype == "message_start":
                            mu = (payload.get("message") or {}).get("usage") or {}
                            usage_input = int(mu.get("input_tokens") or usage_input)
                            usage_cached = int(mu.get("cache_read_input_tokens") or usage_cached)
                        elif ptype == "message_delta":
                            mu = payload.get("usage") or {}
                            usage_output = int(mu.get("output_tokens") or usage_output)
                        # Apply unmask in place.
                        if stream_unmasker is not None:
                            stream_unmask_anthropic_event(payload, stream_unmasker)
                    out_lines.append("data: " + _json.dumps(payload, ensure_ascii=False))
                yield ("\n".join(out_lines)).encode("utf-8")
            # End-of-stream: flush any held-over carry buffer as a final
            # synthetic content_block_delta so the client never loses bytes.
            if stream_unmasker is not None:
                tail = stream_unmasker.flush()
                if tail:
                    final_event = {
                        "type": "content_block_delta",
                        "index": 0,
                        "delta": {"type": "text_delta", "text": tail},
                    }
                    yield (
                        "event: content_block_delta\n"
                        f"data: {_json.dumps(final_event, ensure_ascii=False)}\n\n"
                    ).encode()
        except ProviderError as exc:
            log.warning(
                "messages_stream_provider_error",
                request_id=request_id,
                error=str(exc),
            )
            err = {
                "type": "error",
                "error": {
                    "type": "api_error",
                    "message": str(exc) or "Upstream provider error.",
                },
            }
            import json as _json

            yield (f"event: error\ndata: {_json.dumps(err, ensure_ascii=False)}\n\n").encode()
        finally:
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
            )

            # Phase 5 #4 BrikkoLens — write trace row на fresh session ПОСЛЕ
            # settle. Fire-and-forget. Логику статусов смотри в chat.py
            # _stream_response — здесь упрощённый вариант: messages.py
            # _settle_stream_billing не отдаёт provider_failed/disconnected
            # отдельно, поэтому status выводим из usage.
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
                            ttft_ms=None,
                            prompt_tokens=usage_input,
                            completion_tokens=usage_output,
                            cached_tokens=usage_cached,
                            reasoning_tokens=0,
                            cost_kop=cost_for_log,
                            fx_usd_rub=None,
                            status="ok",
                            http_code=200,
                            error_code=None,
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
                log.warning(
                    "messages_stream_request_log_failed",
                    request_id=request_id,
                    exc_info=True,
                )

            # Eagerly free the PII Redis row.
            if pii_active:
                stream_pii_store = PiiMappingStore(get_pii_redis())
                await stream_pii_store.delete(request_id)

    headers = {
        "X-Request-Id": request_id,
        "X-Gateway-Provider": chosen.provider.value,
        "X-Router-Strategy": (str(decision.strategy_used) if decision.strategy_used else "pinned"),
        "X-Router-Fallback-Used": "false",
        "Cache-Control": "no-cache",
    }
    if chosen.deprecated_at is not None:
        headers["X-Brikko-Deprecated"] = (
            f"model={chosen.id};retires={chosen.deprecated_at.isoformat()}"
        )
    return StreamingResponse(
        _gen(),
        media_type="text/event-stream",
        headers=headers,
    )


# TODO(sprint-12+): native OpenAI Responses API endpoint /v1/responses.
# Same shape transformation pattern as this file, mirrored for OpenAI's
# new Responses API. Scoped out of Sprint 11.6 per task brief — Claude
# Code SDK is the higher-leverage integration to ship first.

__all__ = ["router"]
