"""POST /v1/images/generations — OpenAI-compatible image generation.

Sprint M2 (2026-05-09). gpt-image-1 + DALL·E 3/2 only (OpenAI is the
single image-gen provider in MVP). Stable Diffusion / Flux land in M3
when we have a fall-back tier.

Pipeline per request
--------------------

    1. Auth (require_api_key — same Bearer ``sk-vlt-...`` as chat).
    2. Validate Pydantic body (model id, prompt non-empty, n in [1, max_n],
       size + quality in the per-model allowlist).
    3. PII-mask the prompt before forwarding (Privacy v2 Phase 4 — three-
       way gate). The image is generated from the masked text — placeholders
       like ``<NAME_1>`` may not produce a useful image, but customers who
       opt into masking accepted that trade.
    4. Pre-flight billing hold = ``n × catalog_cost(quality, size)``.
       Cost is exact (no token estimation needed); we know n upfront.
    5. Forward to ``POST https://api.openai.com/v1/images/generations``
       via ``OpenAIProvider.image_generate``.
    6. Post-flight: commit hold to debit, write a UsageEvent with
       ``modality='image'``, ``unit='image'``. Fire BrikkoLens trace.
    7. Return upstream JSON verbatim (preserves ``revised_prompt``,
       ``b64_json``, ``url`` fields the SDK expects).

Trade-offs
----------

* Flat per-image billing (vs. OpenAI's per-token + per-image billing for
  gpt-image-1). Simpler ledger, deterministic cost surface for clients.
  We over-bill on tiny prompts and under-bill on novel-length prompts;
  averaged across our distribution this is a net win for predictability.
* No streaming. Image gen is naturally request/response (10-30 s typical).
* No failover — single provider for image gen in MVP.
* Output URLs (when ``response_format=url``) are OpenAI-hosted with a
  short TTL (~1 hour). We DON'T proxy them through the gateway for MVP;
  the customer downloads directly from OpenAI's CDN. This means the URL
  is briefly visible to OpenAI's analytics — acceptable for MVP, will be
  proxied in M3 when we add a Brikko CDN.

Failure modes
-------------

* Upstream 4xx/5xx → release hold, surface 502 with sanitised message.
* Network timeout → release hold, surface 502.
* OpenAI returns invalid JSON → release hold, 502 with
  ``upstream_invalid``.
"""

from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from fastapi.requests import Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from voltari_gateway.auth.middleware import AuthPrincipal
from voltari_gateway.auth.oauth_dependency import require_api_key_or_oauth_scope
from voltari_gateway.auth.oauth_scopes import OAuthScope
from voltari_gateway.billing.engine import (
    DEFAULT_HOLD_TTL_SECONDS,
    BillingError,
    HoldHandle,
    InsufficientBalanceError,
    commit_hold_to_debit,
    hold_amount,
    release_hold,
)
from voltari_gateway.db.models import UsageEvent
from voltari_gateway.db.session import get_db
from voltari_gateway.pii import (
    PiiMapping,
    compute_pii_flags,
    mask_text,
    resolve_account_pii_flag,
)
from voltari_gateway.pii import (
    audit_summary as _pii_audit_summary,
)
from voltari_gateway.providers.base import (
    ProviderAuthError,
    ProviderError,
    ProviderRateLimitError,
    ProviderTimeoutError,
)
from voltari_gateway.router.modality_catalog import (
    ImageModelSpec,
    get_image_model,
)
from voltari_gateway.utils.errors import (
    GatewayError,
    insufficient_quota,
    invalid_request,
    model_not_found,
    upstream_error,
)
from voltari_gateway.utils.logging import get_logger

router = APIRouter()
log = get_logger(__name__)


# Hard cap on prompt length — guards against a 100 KB prompt DoSing the
# gateway. OpenAI's documented ceiling is 32 000 chars for gpt-image-1
# and 4 000 for DALL·E 3; we cap at 32 000 globally and let the per-model
# upstream enforce its own ceiling.
MAX_PROMPT_CHARS = 32_000

# Hard global ceiling on n. Per-model max_n in the catalog is the source
# of truth; this is just a sanity gate before we even resolve the spec.
MAX_N_GLOBAL = 10


class ImagesRequestBody(BaseModel):
    """OpenAI-compatible body for /v1/images/generations.

    Field validators reject obviously-bad inputs before we resolve the
    catalog spec, so we get a clean 400 for clients regardless of which
    model they pinned.
    """

    model: str
    prompt: str
    n: int = Field(default=1, ge=1, le=MAX_N_GLOBAL)
    size: str | None = None
    quality: str | None = None
    # OpenAI accepts ``url`` (default for dall-e-*) or ``b64_json``.
    # gpt-image-1 returns ``b64_json`` only; we don't override the upstream
    # default — let it surface its own behaviour.
    response_format: str | None = None
    user: str | None = None
    # Sprint 13 / Privacy v2 — Phase 4. Per-request opt-in for PII masking
    # on the prompt. Account flag still applies via the three-way gate.
    pii_protect: bool | None = None

    model_config = {"extra": "ignore"}

    @field_validator("prompt")
    @classmethod
    def _validate_prompt(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("prompt cannot be empty")
        if len(v) > MAX_PROMPT_CHARS:
            raise ValueError(f"prompt exceeds {MAX_PROMPT_CHARS} characters")
        return v


def _validate_against_model(body: ImagesRequestBody, spec: ImageModelSpec) -> tuple[str, str]:
    """Validate quality + size against the per-model allowlist.

    Returns the resolved (quality, size) pair. Raises ``invalid_request``
    on any catalog-mismatch — we want clean 400s, not pay-for-upstream-400s.
    """
    quality = body.quality or spec.default_quality
    size = body.size or spec.default_size

    if quality not in spec.valid_qualities:
        raise invalid_request(
            f"quality must be one of {spec.valid_qualities} for {spec.id}",
            param="quality",
            code="invalid_quality",
        )
    if size not in spec.valid_sizes:
        raise invalid_request(
            f"size must be one of {spec.valid_sizes} for {spec.id}",
            param="size",
            code="invalid_size",
        )
    if body.n > spec.max_n:
        raise invalid_request(
            f"n must be ≤ {spec.max_n} for {spec.id}",
            param="n",
            code="n_too_large",
        )
    return quality, size


@router.post(
    "/v1/images/generations",
    tags=["images"],
    summary="Generate images (OpenAI-compatible)",
    description=(
        "OpenAI-compatible image generation endpoint. Returns a JSON object "
        "with ``data`` (list of generated images) and ``usage`` totals.\n\n"
        "**Auth**: Bearer ``sk-vlt-...``.\n\n"
        "**Billing**: per generated image, with the standard +15% Brikko "
        "markup. Cost varies by (model, quality, size). Recorded as "
        "``modality='image'``, ``unit='image'`` in usage_events."
    ),
    responses={
        200: {"description": "Images generated successfully."},
        400: {"description": "Bad request (unknown model/quality/size, prompt too long, …)."},
        401: {"description": "Missing or invalid Bearer token."},
        402: {"description": "Insufficient balance for the estimated cost."},
        502: {"description": "Upstream image-gen provider returned an error."},
    },
)
async def generate_images(
    body: ImagesRequestBody,
    request: Request,
    principal: Annotated[
        AuthPrincipal, Depends(require_api_key_or_oauth_scope(OAuthScope.AUDIO_READ))
    ],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> JSONResponse:
    spec = get_image_model(body.model)
    if spec is None:
        raise model_not_found(body.model)

    quality, size = _validate_against_model(body, spec)

    started_at_dt = datetime.now(UTC)

    # --- PII masking on prompt (Privacy v2 Phase 4 — three-way gate) -------
    # Account-level flag is honoured. Image-gen output is bytes, not text,
    # so there's nothing to unmask — the masked prompt produces a masked
    # image (or rather, an image without the PII that was in the prompt).
    pii_account_on = await resolve_account_pii_flag(
        db, principal.account_id, cached=principal.pii_masking_enabled
    )
    pii_enabled = compute_pii_flags(
        header_value=request.headers.get("x-pii-protect"),
        body_flag=body.pii_protect,
        account_flag=pii_account_on,
    )
    masked_prompt = body.prompt
    pii_masked = False
    if pii_enabled:
        per_request_mapping = PiiMapping()
        masked_prompt = mask_text(body.prompt, per_request_mapping)
        if not per_request_mapping.is_empty():
            pii_masked = True
            log.info(
                "images_pii_masked",
                account_id=str(principal.account_id),
                pii_summary=[
                    {"type": e.pii_type, "count": e.count}
                    for e in _pii_audit_summary(per_request_mapping)
                ],
            )

    # --- billing -----------------------------------------------------------
    cost = max(1, spec.cost_kopecks(n=body.n, quality=quality, size=size))

    if principal.balance_kopecks <= 0 and principal.tariff == "payg":
        raise insufficient_quota("Top up your balance to continue.")

    request_id = uuid.uuid4().hex
    log.info(
        "images_request",
        request_id=request_id,
        model=spec.id,
        n=body.n,
        quality=quality,
        size=size,
        prompt_chars=len(masked_prompt),
        cost_kopecks=cost,
        account_id=str(principal.account_id),
    )

    try:
        hold = await hold_amount(
            db,
            account_id=principal.account_id,
            amount_kopecks=cost,
            ref_id=request_id,
            ttl_seconds=DEFAULT_HOLD_TTL_SECONDS,
        )
        await db.commit()
    except InsufficientBalanceError as exc:
        await db.rollback()
        log.info(
            "images_preflight_402",
            request_id=request_id,
            balance_kopecks=exc.balance_kopecks,
            required_kopecks=exc.required_kopecks,
        )
        raise insufficient_quota("Top up your balance to continue.") from exc
    except BillingError as exc:
        await db.rollback()
        log.error("images_preflight_billing_error", request_id=request_id, error=str(exc))
        raise GatewayError(
            status_code=500,
            message="Billing system unavailable.",
            type="api_error",
            code="billing_unavailable",
        ) from exc

    # --- forward via OpenAIProvider ----------------------------------------
    registry = getattr(request.app.state, "provider_registry", None)
    provider = None
    if registry is not None:
        try:
            provider = registry.get(spec.provider)
        except Exception:
            provider = None
    if provider is None:
        provider = getattr(request.app.state, "openai_provider", None)
    if provider is None or not hasattr(provider, "image_generate"):
        await _release_hold_safely(db, hold, request_id=request_id)
        log.error("images_provider_unavailable", request_id=request_id, model=spec.id)
        raise upstream_error("Image-gen provider not configured.")

    try:
        t0 = time.perf_counter()
        upstream_payload: dict[str, Any] = await provider.image_generate(
            model=spec.upstream_id,
            prompt=masked_prompt,
            n=body.n,
            size=size,
            quality=quality,
            response_format=body.response_format,
            user=body.user,
        )
        latency_ms = int((time.perf_counter() - t0) * 1000)
    except ProviderAuthError as exc:
        await _release_hold_safely(db, hold, request_id=request_id)
        log.warning("images_upstream_auth_error", request_id=request_id, error=str(exc))
        raise upstream_error("Upstream authentication failed.") from exc
    except ProviderRateLimitError as exc:
        await _release_hold_safely(db, hold, request_id=request_id)
        log.warning("images_upstream_rate_limit", request_id=request_id, error=str(exc))
        raise upstream_error("Upstream rate limit reached. Try again shortly.") from exc
    except ProviderTimeoutError as exc:
        await _release_hold_safely(db, hold, request_id=request_id)
        log.warning("images_upstream_timeout", request_id=request_id, error=str(exc))
        raise upstream_error("Upstream image-gen provider timed out.") from exc
    except ProviderError as exc:
        await _release_hold_safely(db, hold, request_id=request_id)
        log.warning("images_upstream_error", request_id=request_id, error=str(exc))
        raise upstream_error("Upstream image-gen provider error.") from exc
    except Exception:
        await _release_hold_safely(db, hold, request_id=request_id)
        log.exception("images_unexpected_error", request_id=request_id)
        raise

    # --- post-flight billing -----------------------------------------------
    try:
        await commit_hold_to_debit(
            db,
            handle=hold,
            actual_amount_kopecks=cost,
            meta={
                "request_id": request_id,
                "model": spec.id,
                "modality": "image",
                "n": body.n,
                "quality": quality,
                "size": size,
            },
        )
        # ``input_tokens`` carries the prompt char count, ``output_tokens``
        # carries the image count — analytics tooling slices on either.
        event = UsageEvent(
            account_id=principal.account_id,
            api_key_id=principal.api_key_id,
            model=spec.id,
            provider=spec.provider.value,
            modality="image",
            unit="image",
            input_tokens=len(masked_prompt),
            output_tokens=body.n,
            cached_tokens=0,
            cost_kopecks=cost,
            request_id=request_id,
        )
        db.add(event)
        await db.commit()
        from voltari_gateway.utils.observability import record_provider_call

        record_provider_call(
            request.app,
            provider=spec.provider.value,
            model=spec.id,
            status="success",
            latency_seconds=latency_ms / 1000.0,
            output_tokens=body.n,
            billed_kopecks=cost,
        )
    except Exception as exc:
        await db.rollback()
        await _release_hold_safely(db, hold, request_id=request_id)
        log.exception("images_postflight_persist_failed", request_id=request_id)
        raise GatewayError(
            status_code=500,
            message="Failed to record usage.",
            type="api_error",
            code="billing_unavailable",
        ) from exc

    # --- BrikkoLens trace (fire-and-forget) --------------------------------
    try:
        from voltari_gateway.observability import record_request_log

        finished_at_dt = datetime.now(UTC)
        await record_request_log(
            db,
            account_id=principal.account_id,
            api_key_id=principal.api_key_id,
            request_id=request_id,
            provider=spec.provider.value,
            model=spec.id,
            routed_from=None,
            started_at=started_at_dt,
            finished_at=finished_at_dt,
            latency_ms=latency_ms,
            ttft_ms=None,
            prompt_tokens=len(masked_prompt),
            completion_tokens=body.n,  # repurposed for image count
            cached_tokens=0,
            reasoning_tokens=0,
            cost_kop=cost,
            fx_usd_rub=None,
            status="ok",
            http_code=200,
            error_code=None,
            error_message=None,
            is_streaming=False,
            cache_hit=False,
            pii_masked=pii_masked,
            tools_used=False,
            request_body=None,  # binary URLs / b64 don't belong in jsonb
            response_body=None,
            model_params={
                "n": body.n,
                "quality": quality,
                "size": size,
                "response_format": body.response_format,
            },
            store_bodies=False,
        )
    except Exception as exc:
        log.warning("images_trace_failed", request_id=request_id, error=str(exc))

    # --- response ----------------------------------------------------------
    response_headers = {
        "X-Request-Id": request_id,
        "X-Gateway-Provider": spec.provider.value,
        "X-Gateway-Cost-Kop": str(cost),
        "X-Gateway-Modality": "image",
        "X-Gateway-Image-Count": str(body.n),
    }
    return JSONResponse(content=upstream_payload, status_code=200, headers=response_headers)


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
