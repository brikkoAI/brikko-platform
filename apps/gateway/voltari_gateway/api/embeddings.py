"""POST /v1/embeddings — OpenAI-compatible embeddings endpoint.

Sprint M1 — second non-chat modality. ``text-embedding-3-small`` /
``-3-large`` only (research-отчёт §A.5).

Pipeline per request
--------------------

    1. Auth (require_api_key — same Bearer ``sk-vlt-...`` as chat).
    2. Validate Pydantic body. ``input`` accepts the OpenAI shape:
       a single string OR an array of strings (batch).
    3. Count input tokens locally with tiktoken — this is the source of
       truth for billing because we want to pre-flight the hold BEFORE
       paying for the upstream call. OpenAI's ``usage.prompt_tokens`` in
       the response is sanity-checked at post-flight; if it diverges
       from our count by >10%, we log a warning and bill on the
       provider's number (not ours) so the ledger never under-counts.
    4. Pre-flight hold based on local token count × catalog price.
    5. Forward to ``POST https://api.openai.com/v1/embeddings`` via
       httpx (plain JSON, no streaming).
    6. Post-flight: commit hold to debit using ``usage.prompt_tokens``
       from the response (or the local count, whichever is greater),
       write a UsageEvent with ``modality='embeddings'``, ``unit='token'``.

Trade-offs
----------

* tiktoken's ``cl100k_base`` encoding is what text-embedding-3 uses
  upstream. We don't ship per-model encodings — one BPE table covers
  the entire embedding-3 family. If OpenAI ever ships a model with a
  different tokenizer, ``encoding_for_model`` will return the right
  one and we'll just download a new BPE; until then this is fine.
* No streaming, no batching across requests, no caching of identical
  inputs. Embeddings are deterministic, so a per-account input-hash →
  vector cache is a real win — but it's a V2 feature, not M1.
* PII masking is **opt-in only** — and **does NOT** follow the
  ``Account.pii_masking_enabled`` flag the way chat / messages / audio
  do. Reason: a vector for ``"<NAME_1>"`` is **mathematically different**
  from a vector for ``"Иванов Иван Иванович"``. If we silently masked
  for ``PRO_PRIVACY`` accounts, their RAG pipeline (which embeds the
  same texts at index time and query time) would suddenly stop matching.
  Customers who genuinely want masked-vector retrieval ask for it
  explicitly per-call via header or body flag. There is no unmask path
  for embeddings — the response is a vector of floats, not text.
"""

from __future__ import annotations

import json
import time
import uuid
from decimal import Decimal
from typing import Annotated, Any

import httpx
import tiktoken
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
from voltari_gateway.config import get_settings
from voltari_gateway.db.models import UsageEvent
from voltari_gateway.db.session import get_db
from voltari_gateway.pii import (
    PiiMapping,
    compute_pii_flags_optin_only,
    mask_text,
)
from voltari_gateway.pii import (
    audit_summary as _pii_audit_summary,
)
from voltari_gateway.router.modality_catalog import get_embedding_model
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

# Hold buffer: embeddings are deterministic — local token count and the
# upstream's ``usage.prompt_tokens`` agree to within ~1% under normal
# conditions. We still apply 1.1× to absorb the edge case (BOM bytes,
# surrogate pairs in user-supplied text) where tiktoken under-counts.
HOLD_BUFFER_MULTIPLIER: Decimal = Decimal("1.1")

# Aggregate input cap. OpenAI accepts an array; without a ceiling, a
# 10k-element batch could DoS the gateway with a single 50 MB request.
# 8192 inputs × 8192 tokens each = 67M tokens worst case. Plenty for
# any legitimate batch.
MAX_INPUT_ITEMS = 2048


# ---------- request / response models -----------------------------------------


class EmbeddingsRequestBody(BaseModel):
    """OpenAI-compatible body shape.

    ``input`` is the OpenAI-mandated polymorphic field — string or array
    of strings. We don't accept the ``array of int / array of array of
    int`` paths (token-id input) because (a) it's rare, (b) it
    short-circuits our token counting, (c) it's almost never what
    customers want.
    """

    model: str
    input: str | list[str]
    encoding_format: str | None = Field(default=None)
    dimensions: int | None = Field(default=None, ge=1, le=3072)
    user: str | None = None

    # Sprint 13 / Privacy v2 — Phase 4. Opt-in PII masking for the
    # embedding input. Note: account-level ``pii_masking_enabled`` is
    # deliberately ignored here — see module docstring for the
    # retrieval-semantics rationale. Customers that want masked vectors
    # set ``pii_protect: true`` per call (or ``X-PII-Protect: true``
    # header).
    pii_protect: bool | None = None

    model_config = {"extra": "ignore"}

    @field_validator("input")
    @classmethod
    def _validate_input(cls, v: str | list[str]) -> str | list[str]:
        if isinstance(v, str):
            if not v:
                raise ValueError("input cannot be empty")
            return v
        if not v:
            raise ValueError("input array cannot be empty")
        if len(v) > MAX_INPUT_ITEMS:
            raise ValueError(f"input array exceeds {MAX_INPUT_ITEMS} items")
        for i, item in enumerate(v):
            if not isinstance(item, str):
                raise ValueError(f"input[{i}] must be a string")
            if not item:
                raise ValueError(f"input[{i}] cannot be empty")
        return v


# ---------- helpers -----------------------------------------------------------


# Cached BPE encoder. ``cl100k_base`` is the tokenizer for text-embedding-3
# and (almost) every modern OpenAI text model. We load lazily so import
# of this module doesn't block on a network roundtrip when tiktoken's
# vocab cache is cold.
_ENCODING: tiktoken.Encoding | None = None


def _get_encoding() -> tiktoken.Encoding:
    global _ENCODING
    if _ENCODING is None:
        # ``cl100k_base`` matches the embedding-3 family; if a future
        # model uses a different one, ``encoding_for_model`` returns it
        # but for now we don't pay the per-call dispatch.
        _ENCODING = tiktoken.get_encoding("cl100k_base")
    return _ENCODING


def _count_tokens(input_value: str | list[str]) -> int:
    """Total prompt tokens across one or many strings."""
    enc = _get_encoding()
    if isinstance(input_value, str):
        return len(enc.encode(input_value))
    # Batched encode is faster than per-string when the batch is large.
    return sum(len(s) for s in enc.encode_batch(input_value))


async def _forward_to_openai(
    *,
    body: dict[str, Any],
    timeout_seconds: float,
) -> tuple[int, dict[str, Any]]:
    """POST to OpenAI Embeddings. Returns (status, parsed_body).

    On any non-JSON body we surface a 502 with a generic message —
    this happens only on network corruption / cold-cache OpenAI
    weirdness, never on the documented contract.
    """
    settings = get_settings()
    api_key = settings.openai_api_key.get_secret_value()
    if not api_key:
        raise upstream_error("Embeddings provider not configured.")

    base_url = settings.openai_base_url.rstrip("/")
    url = f"{base_url}/embeddings"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    proxy = settings.outbound_http_proxy
    timeout = httpx.Timeout(timeout_seconds, read=None, connect=10.0)

    async with httpx.AsyncClient(proxy=proxy, timeout=timeout) as client:
        resp = await client.post(url, headers=headers, json=body)
        try:
            parsed = resp.json()
        except json.JSONDecodeError:
            parsed = {"_raw": resp.text}
        return resp.status_code, parsed


# ---------- endpoint ----------------------------------------------------------


@router.post(
    "/v1/embeddings",
    tags=["embeddings"],
    summary="Create embeddings (OpenAI-compatible)",
    description=(
        "OpenAI-compatible embeddings endpoint. Accepts a single string or an "
        "array of strings (≤2048 items) in ``input`` and returns the vector(s) "
        "in the same shape OpenAI does.\n\n"
        "**Auth**: Bearer ``sk-vlt-...``.\n\n"
        "**Billing**: per input token (counted locally with tiktoken), with "
        "the standard +15% Brikko markup. Recorded as ``modality='embeddings'``, "
        "``unit='token'`` in usage_events."
    ),
    responses={
        200: {"description": "Embeddings generated successfully."},
        400: {"description": "Bad request (unknown model, empty input, …)."},
        401: {"description": "Missing or invalid Bearer token."},
        402: {"description": "Insufficient balance for the estimated cost."},
        502: {"description": "Upstream embeddings provider returned an error."},
    },
)
async def create_embeddings(
    body: EmbeddingsRequestBody,
    request: Request,
    principal: Annotated[
        AuthPrincipal, Depends(require_api_key_or_oauth_scope(OAuthScope.EMBEDDINGS_READ))
    ],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> JSONResponse:
    spec = get_embedding_model(body.model)
    if spec is None:
        raise model_not_found(body.model)

    # --- PII masking (Sprint 13 / Privacy v2 — Phase 4, opt-in only) -----
    # Embeddings semantics break under masking by design — replacing PII
    # with placeholders changes the vector. We therefore IGNORE the
    # account-level flag here (see module docstring) and gate only on the
    # explicit per-request signals (``X-PII-Protect`` header or
    # ``pii_protect`` body field). No unmask path — embeddings response
    # is a vector of floats.
    pii_enabled_embeddings = compute_pii_flags_optin_only(
        header_value=request.headers.get("x-pii-protect"),
        body_flag=body.pii_protect,
    )
    if pii_enabled_embeddings:
        per_request_mapping = PiiMapping()
        if isinstance(body.input, str):
            body.input = mask_text(body.input, per_request_mapping)
        else:
            body.input = [mask_text(s, per_request_mapping) for s in body.input]
        if not per_request_mapping.is_empty():
            log.info(
                "embeddings_pii_masked",
                account_id=str(principal.account_id),
                input_count=1 if isinstance(body.input, str) else len(body.input),
                pii_summary=[
                    {"type": e.pii_type, "count": e.count}
                    for e in _pii_audit_summary(per_request_mapping)
                ],
            )

    # Local token count — drives the hold. We log both this and the
    # provider's count post-flight so any divergence is visible in
    # observability without burning storage on the happy path. Token
    # count happens AFTER masking so the hold is sized against the
    # actual bytes we forward upstream.
    local_tokens = _count_tokens(body.input)
    if local_tokens == 0:
        # Shouldn't be reachable (validator rejects empty strings) but
        # belt-and-braces against future tokenizer regressions returning
        # zero on whitespace-only input.
        raise invalid_request(
            "Input contains no billable tokens.",
            param="input",
            code="empty_input",
        )

    estimated_cost = spec.cost_kopecks(local_tokens)
    # Apply hold buffer + minimum-1-kopeck floor (mirrors chat).
    hold_amount_kop = max(1, int(Decimal(estimated_cost) * HOLD_BUFFER_MULTIPLIER))

    if principal.balance_kopecks <= 0 and principal.tariff == "payg":
        raise insufficient_quota("Top up your balance to continue.")

    settings = get_settings()
    request_id = uuid.uuid4().hex
    log.info(
        "embeddings_request",
        request_id=request_id,
        model=spec.id,
        input_count=1 if isinstance(body.input, str) else len(body.input),
        local_tokens=local_tokens,
        hold_kopecks=hold_amount_kop,
        account_id=str(principal.account_id),
    )

    # --- pre-flight hold -------------------------------------------------
    try:
        hold = await hold_amount(
            db,
            account_id=principal.account_id,
            amount_kopecks=hold_amount_kop,
            ref_id=request_id,
            ttl_seconds=DEFAULT_HOLD_TTL_SECONDS,
        )
        await db.commit()
    except InsufficientBalanceError as exc:
        await db.rollback()
        log.info(
            "embeddings_preflight_402",
            request_id=request_id,
            account_id=str(principal.account_id),
            balance_kopecks=exc.balance_kopecks,
            required_kopecks=exc.required_kopecks,
        )
        raise insufficient_quota("Top up your balance to continue.") from exc
    except BillingError as exc:
        await db.rollback()
        log.error("embeddings_preflight_billing_error", request_id=request_id, error=str(exc))
        raise GatewayError(
            status_code=500,
            message="Billing system unavailable.",
            type="api_error",
            code="billing_unavailable",
        ) from exc

    # --- forward ---------------------------------------------------------
    upstream_body: dict[str, Any] = {
        "model": spec.upstream_id,
        "input": body.input,
    }
    if body.encoding_format is not None:
        upstream_body["encoding_format"] = body.encoding_format
    if body.dimensions is not None:
        upstream_body["dimensions"] = body.dimensions
    if body.user is not None:
        upstream_body["user"] = body.user

    try:
        t0 = time.perf_counter()
        status, parsed = await _forward_to_openai(
            body=upstream_body,
            timeout_seconds=settings.openai_timeout_seconds,
        )
        latency_ms = int((time.perf_counter() - t0) * 1000)
    except (httpx.TimeoutException, httpx.ConnectError) as exc:
        await _release_hold_safely(db, hold, request_id=request_id)
        log.warning("embeddings_upstream_unavailable", request_id=request_id, error=str(exc))
        raise upstream_error("Upstream embeddings provider unreachable.") from exc
    except Exception:
        await _release_hold_safely(db, hold, request_id=request_id)
        log.exception("embeddings_unexpected_error", request_id=request_id)
        raise

    if status >= 400:
        await _release_hold_safely(db, hold, request_id=request_id)
        log.warning(
            "embeddings_upstream_error",
            request_id=request_id,
            upstream_status=status,
            latency_ms=latency_ms,
        )
        if status == 401:
            raise upstream_error("Upstream authentication failed.")
        if status == 429:
            raise upstream_error("Upstream rate limit reached. Try again shortly.")
        if 400 <= status < 500:
            raise invalid_request(
                "Upstream rejected the embeddings request.",
                code="upstream_invalid",
            )
        raise upstream_error("Upstream embeddings provider error.")

    # --- post-flight billing --------------------------------------------
    # Trust the provider's prompt_tokens when present, else fall back to
    # our local count. We bill on max(local, provider) so a tokenizer
    # disagreement never under-charges us.
    provider_usage = parsed.get("usage") or {}
    provider_tokens = int(provider_usage.get("prompt_tokens") or 0)
    billed_tokens = max(local_tokens, provider_tokens)
    if provider_tokens and abs(provider_tokens - local_tokens) > max(1, local_tokens // 10):
        log.warning(
            "embeddings_token_count_divergence",
            request_id=request_id,
            local=local_tokens,
            provider=provider_tokens,
        )

    actual_cost = max(1, spec.cost_kopecks(billed_tokens))

    try:
        if actual_cost <= hold.amount_kopecks:
            await commit_hold_to_debit(
                db,
                handle=hold,
                actual_amount_kopecks=actual_cost,
                meta={
                    "request_id": request_id,
                    "model": spec.id,
                    "modality": "embeddings",
                    "billed_tokens": billed_tokens,
                    "local_tokens": local_tokens,
                    "provider_tokens": provider_tokens,
                },
            )
        else:
            # Provider counted more tokens than our buffer covered — release
            # the hold and debit the actual cost (which may now fail with
            # InsufficientBalance; that's correct, the customer just barely
            # missed having enough to pay for the bigger bill).
            await release_hold(db, hold)
            from voltari_gateway.billing.engine import debit_account

            await debit_account(
                db,
                account_id=principal.account_id,
                amount_kopecks=actual_cost,
                ref_id=request_id,
                meta={
                    "request_id": request_id,
                    "model": spec.id,
                    "modality": "embeddings",
                    "billed_tokens": billed_tokens,
                    "post_hold_overage": True,
                },
            )

        event = UsageEvent(
            account_id=principal.account_id,
            api_key_id=principal.api_key_id,
            model=spec.id,
            provider=spec.provider.value,
            modality="embeddings",
            unit="token",
            input_tokens=billed_tokens,
            output_tokens=0,
            cached_tokens=0,
            cost_kopecks=actual_cost,
            request_id=request_id,
        )
        db.add(event)
        await db.commit()
        # Prometheus — record after persistence.
        from voltari_gateway.utils.observability import record_provider_call

        record_provider_call(
            request.app,
            provider=spec.provider.value,
            model=spec.id,
            status="success",
            latency_seconds=latency_ms / 1000.0,
            input_tokens=billed_tokens,
            billed_kopecks=actual_cost,
        )
    except InsufficientBalanceError as exc:
        # Provider over-counted past our hold + balance. We've already
        # received the result; log and let the customer settle this with
        # support. Release whatever's left of the hold.
        await db.rollback()
        await _release_hold_safely(db, hold, request_id=request_id)
        log.error(
            "embeddings_postflight_under_held",
            request_id=request_id,
            balance=exc.balance_kopecks,
            required=exc.required_kopecks,
        )
        raise insufficient_quota("Top up your balance to continue.") from exc
    except Exception as exc:
        await db.rollback()
        await _release_hold_safely(db, hold, request_id=request_id)
        log.exception("embeddings_postflight_persist_failed", request_id=request_id)
        raise GatewayError(
            status_code=500,
            message="Failed to record usage.",
            type="api_error",
            code="billing_unavailable",
        ) from exc

    response_headers = {
        "X-Request-Id": request_id,
        "X-Gateway-Provider": spec.provider.value,
        "X-Gateway-Cost-Kop": str(actual_cost),
        "X-Gateway-Modality": "embeddings",
        "X-Gateway-Tokens": str(billed_tokens),
    }
    return JSONResponse(content=parsed, status_code=200, headers=response_headers)


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
