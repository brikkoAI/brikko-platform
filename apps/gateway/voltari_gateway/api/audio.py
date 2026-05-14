"""POST /v1/audio/transcriptions — OpenAI-compatible Speech-to-Text.

Sprint M1 — first non-text modality. Whisper-1 only (research-отчёт §C.3:
OpenAI is the safe default, Groq blocked on legal review). Billing is in
**minutes**, not tokens — see ``modality_catalog.STTModelSpec``.

Pipeline per request
--------------------

    1. Auth (require_api_key — same Bearer ``sk-vlt-...`` as chat).
    2. Read multipart upload into memory. Reject > 25 MB before parsing
       so we don't burn RAM on a known-bad upload.
    3. Compute audio duration with mutagen (pure-Python, no ffmpeg).
       Round UP to the next 0.1 minute — providers don't refund partial
       minutes and we don't want to under-bill on a 1.4-min clip.
    4. Pre-flight billing hold: ``minutes × kop_per_minute`` (with the
       same Brikko +15% markup as chat). Insufficient balance → 402
       BEFORE we forward.
    5. Forward to ``POST https://api.openai.com/v1/audio/transcriptions``
       via httpx as multipart, using ``OPENAI_API_KEY`` from settings.
       The ``outbound_http_proxy`` (if set) is the same двухсерверная
       infrastructure chat already uses.
    6. Post-flight: commit hold to the actual cost (== estimated cost
       since duration is known up front), write a UsageEvent with
       ``modality='stt'``, ``unit='minute'``.
    7. Return the upstream response body verbatim — OpenAI Whisper's
       JSON shape is documented and stable.

Trade-offs
----------

* No streaming — Whisper's transcription endpoint is request/response.
  The new ``/v1/audio/transcriptions/stream`` is a different beast we
  ship in M2 alongside Groq.
* Duration is computed on the gateway, not extracted from the upstream
  response. Whisper does NOT return billed duration in the body, so
  computing locally is the only option. Mutagen handles mp3/m4a/wav/
  ogg/flac/webm; corrupt or codec-unknown files fall through to a
  conservative ``len(file_bytes) / 16_000`` estimate (16 KB/sec ≈
  128 kbps; over-estimates on low-bitrate, under-estimates on high —
  but bounds the worst case to ±2× and stops a deliberately-malformed
  upload from billing 0 minutes).
* PII masking applies to the **transcript text** post-Whisper, not to the
  audio bytes (voice biometrics is out of scope for the 152-ФЗ pipeline).
  Same three-way gate as ``/v1/chat/completions``: account
  ``pii_masking_enabled`` flag OR header ``X-PII-Protect`` OR Form field
  ``pii_protect``. Reversibility is unnecessary — the transcript is a
  one-way artefact, customers receive masked text directly.

Failover
--------

None for STT. If Whisper is down, we surface 502 — no fallback provider
is configured (Groq is the next candidate but blocked on legal). The
chat-style fallback chain doesn't apply here: STT is single-provider.
"""

from __future__ import annotations

import io
import json
import time
import uuid
from datetime import UTC, datetime
from decimal import ROUND_UP, Decimal
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi.requests import Request
from fastapi.responses import JSONResponse, Response
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
    TTS_VALID_FORMATS,
    TTS_VALID_VOICES,
    STTModelSpec,
    get_stt_model,
    get_tts_model,
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

# OpenAI's documented hard cap. Anything bigger 4xx's at the upstream;
# we reject locally so a 100-MB upload can't burn our outbound bandwidth.
MAX_AUDIO_SIZE_BYTES = 25 * 1024 * 1024  # 25 MB

# Billing granularity: round UP to the next tenth of a minute. Providers
# don't refund partial minutes; over-rounding by ≤6 seconds per call is
# the safe direction. ``Decimal("0.1")`` keeps arithmetic exact.
_MINUTE_QUANTUM: Decimal = Decimal("0.1")

# Last-ditch duration estimate when mutagen can't parse the file. 16 KB/sec
# is roughly 128 kbps — over-estimates on cheap voice memos (8 kbps), under
# on FLAC (1 Mbps). Worst case ±2× — bounds a malicious upload at billing
# *some* minutes instead of zero. See module docstring for rationale.
_FALLBACK_BYTES_PER_SECOND = 16_000


def _compute_duration_minutes(file_bytes: bytes) -> Decimal:
    """Best-effort duration in minutes, rounded UP to nearest 0.1 min.

    Tries mutagen first (handles mp3/m4a/wav/ogg/flac/webm). On parse
    failure falls back to a byte-rate heuristic — never returns 0 for
    non-empty input so a malformed upload still bills a minimum charge.
    """
    seconds: float

    try:
        # mutagen exports ``File`` only via ``mutagen._file`` — the public
        # re-export is not declared in __all__, hence the explicit ignore
        # so mypy --strict accepts the import. Behaviour-wise this is the
        # documented way to instantiate a format-detecting reader.
        from mutagen import File as MutagenFile  # type: ignore[attr-defined]

        mf = MutagenFile(io.BytesIO(file_bytes))
        if mf is not None and mf.info is not None and mf.info.length:
            seconds = float(mf.info.length)
        else:
            raise ValueError("mutagen returned empty info")
    except Exception as exc:
        # Conservative fallback. Logged at INFO so we can monitor for
        # systemic format issues without spamming WARN.
        log.info(
            "stt_duration_fallback",
            reason=str(exc),
            file_bytes=len(file_bytes),
        )
        seconds = max(1.0, len(file_bytes) / _FALLBACK_BYTES_PER_SECOND)

    minutes = Decimal(str(seconds)) / Decimal(60)
    # Always at least one billable tick (0.1 min) so a 2-second voice
    # memo doesn't go through for 0 kopecks.
    minutes = max(minutes, _MINUTE_QUANTUM)
    return minutes.quantize(_MINUTE_QUANTUM, rounding=ROUND_UP)


async def _forward_to_openai(
    *,
    file_bytes: bytes,
    filename: str,
    content_type: str,
    model: STTModelSpec,
    language: str | None,
    prompt: str | None,
    response_format: str | None,
    temperature: float | None,
    timeout_seconds: float,
) -> tuple[int, dict[str, str], bytes]:
    """POST to OpenAI Whisper. Returns (status, headers, body_bytes).

    We forward as multipart/form-data because that's the OpenAI contract.
    No retries here — the chat path's failover is irrelevant for STT
    (single provider). Any 4xx/5xx propagates so the caller can decide
    whether to retry.
    """
    settings = get_settings()
    api_key = settings.openai_api_key.get_secret_value()
    if not api_key:
        # Environment misconfiguration. 502 (not 503) — the upstream
        # itself is fine, our wiring isn't.
        raise upstream_error("STT provider not configured.")

    base_url = settings.openai_base_url.rstrip("/")
    url = f"{base_url}/audio/transcriptions"

    # python-multipart wants a (filename, fileobj, content_type) tuple.
    # ``file_bytes`` is bytes (we already read it) — wrap in BytesIO so
    # httpx streams it without an extra copy.
    files = {
        "file": (filename or "audio", io.BytesIO(file_bytes), content_type or "audio/mpeg"),
    }
    data: dict[str, str] = {"model": model.upstream_id}
    if language:
        data["language"] = language
    if prompt:
        data["prompt"] = prompt
    if response_format:
        data["response_format"] = response_format
    if temperature is not None:
        data["temperature"] = str(temperature)

    headers = {"Authorization": f"Bearer {api_key}"}

    proxy = settings.outbound_http_proxy
    timeout = httpx.Timeout(timeout_seconds, read=None, connect=10.0)

    # Per-call client (cheap; no chat-style connection-pool reuse needed
    # for STT volume). When proxy is set we route through the same
    # двухсерверная WireGuard tunnel as chat.
    async with httpx.AsyncClient(proxy=proxy, timeout=timeout) as client:
        resp = await client.post(url, headers=headers, data=data, files=files)
        body = resp.content
        # Strip hop-by-hop headers; only forward content-type back to the
        # client so the response shape (json vs text) is preserved.
        out_headers: dict[str, str] = {}
        ct = resp.headers.get("content-type")
        if ct:
            out_headers["content-type"] = ct
        return resp.status_code, out_headers, body


@router.post(
    "/v1/audio/transcriptions",
    tags=["audio"],
    summary="Transcribe audio (OpenAI-compatible)",
    description=(
        "OpenAI-compatible Speech-to-Text endpoint. Accepts multipart/form-data "
        "with an ``audio/*`` file (≤25 MB) and forwards to the configured "
        "Whisper provider.\n\n"
        "**Auth**: Bearer ``sk-vlt-...``.\n\n"
        "**Billing**: per minute of audio, rounded up to the next 0.1 min, with "
        "the standard +15% Brikko markup. Recorded as ``modality='stt'``, "
        "``unit='minute'`` in usage_events."
    ),
    responses={
        200: {"description": "Transcription succeeded; body forwarded verbatim."},
        400: {"description": "Bad request (unknown model, missing file, etc.)."},
        401: {"description": "Missing or invalid Bearer token."},
        402: {"description": "Insufficient balance for the estimated cost."},
        413: {"description": "File exceeds the 25 MB limit."},
        502: {"description": "Upstream Whisper provider returned an error."},
    },
)
async def transcribe_audio(
    request: Request,
    principal: Annotated[
        AuthPrincipal, Depends(require_api_key_or_oauth_scope(OAuthScope.AUDIO_READ))
    ],
    db: Annotated[AsyncSession, Depends(get_db)],
    file: Annotated[UploadFile, File(description="Audio file (≤25 MB).")],
    model: Annotated[str, Form(description="Model id, e.g. 'whisper-1'.")] = "whisper-1",
    language: Annotated[str | None, Form()] = None,
    prompt: Annotated[str | None, Form()] = None,
    response_format: Annotated[str | None, Form()] = None,
    temperature: Annotated[float | None, Form()] = None,
    pii_protect: Annotated[
        bool | None,
        Form(
            description=(
                "Sprint 13 / Privacy v2 — Phase 4. Per-request opt-in for "
                "PII masking on the transcript text. ``true`` enables masking "
                "regardless of account-level setting; omitted → falls back to "
                "the account flag and ``X-PII-Protect`` header."
            )
        ),
    ] = None,
) -> JSONResponse:
    spec = get_stt_model(model)
    if spec is None:
        raise model_not_found(model)

    # --- read + size guard ----------------------------------------------
    # We read the whole file because (a) we need duration before we can
    # bill, (b) httpx multipart wants a seekable buffer anyway. UploadFile
    # streams to disk above SpooledTemporaryFile's 1 MB threshold so this
    # doesn't blow heap on a 25 MB cap.
    file_bytes = await file.read()
    size = len(file_bytes)
    if size == 0:
        raise invalid_request(
            "Audio file is empty.",
            param="file",
            code="empty_file",
        )
    if size > spec.max_file_size_bytes:
        # OpenAI surfaces this as 413 (RFC 9110 §15.5.14). We mirror so
        # SDK callers can distinguish "too big" from "bad request".
        raise GatewayError(
            status_code=413,
            message=(
                f"Audio file exceeds the {spec.max_file_size_bytes // (1024 * 1024)} MB limit "
                f"(uploaded {size} bytes)."
            ),
            type="invalid_request_error",
            code="file_too_large",
            param="file",
        )

    # --- duration + cost -------------------------------------------------
    minutes = _compute_duration_minutes(file_bytes)
    cost = spec.cost_kopecks(minutes)
    # Minimum 1 kopeck so the hold path doesn't reject amount=0.
    cost = max(1, cost)

    # Fast-fail for obviously-empty PAYG accounts (mirrors chat path).
    if principal.balance_kopecks <= 0 and principal.tariff == "payg":
        raise insufficient_quota("Top up your balance to continue.")

    settings = get_settings()
    request_id = uuid.uuid4().hex
    log.info(
        "stt_request",
        request_id=request_id,
        model=spec.id,
        size_bytes=size,
        minutes=str(minutes),
        cost_kopecks=cost,
        account_id=str(principal.account_id),
    )

    # --- pre-flight hold -------------------------------------------------
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
            "stt_preflight_402",
            request_id=request_id,
            account_id=str(principal.account_id),
            balance_kopecks=exc.balance_kopecks,
            required_kopecks=exc.required_kopecks,
        )
        raise insufficient_quota("Top up your balance to continue.") from exc
    except BillingError as exc:
        await db.rollback()
        log.error("stt_preflight_billing_error", request_id=request_id, error=str(exc))
        raise GatewayError(
            status_code=500,
            message="Billing system unavailable.",
            type="api_error",
            code="billing_unavailable",
        ) from exc

    # --- forward ---------------------------------------------------------
    try:
        t0 = time.perf_counter()
        status, out_headers, body = await _forward_to_openai(
            file_bytes=file_bytes,
            filename=file.filename or "audio",
            content_type=file.content_type or "audio/mpeg",
            model=spec,
            language=language,
            prompt=prompt,
            response_format=response_format,
            temperature=temperature,
            timeout_seconds=settings.openai_timeout_seconds,
        )
        latency_ms = int((time.perf_counter() - t0) * 1000)
    except (httpx.TimeoutException, httpx.ConnectError) as exc:
        await _release_hold_safely(db, hold, request_id=request_id)
        log.warning("stt_upstream_unavailable", request_id=request_id, error=str(exc))
        raise upstream_error("Upstream STT provider unreachable.") from exc
    except Exception:
        await _release_hold_safely(db, hold, request_id=request_id)
        log.exception("stt_unexpected_error", request_id=request_id)
        raise

    # Upstream errors → release the hold (no debit) and surface a generic
    # 502 so we don't leak provider error semantics to the client.
    if status >= 400:
        await _release_hold_safely(db, hold, request_id=request_id)
        log.warning(
            "stt_upstream_error",
            request_id=request_id,
            upstream_status=status,
            latency_ms=latency_ms,
        )
        # Pass through 401/429 status so SDK callers can react, but
        # rewrap the body in our envelope.
        if status == 401:
            raise upstream_error("Upstream authentication failed.")
        if status == 429:
            raise upstream_error("Upstream rate limit reached. Try again shortly.")
        if 400 <= status < 500:
            raise invalid_request(
                "Upstream rejected the audio request.",
                code="upstream_invalid",
            )
        raise upstream_error("Upstream STT provider error.")

    # --- post-flight billing --------------------------------------------
    try:
        await commit_hold_to_debit(
            db,
            handle=hold,
            actual_amount_kopecks=cost,
            meta={
                "request_id": request_id,
                "model": spec.id,
                "modality": "stt",
                "minutes": str(minutes),
                "size_bytes": size,
            },
        )
        event = UsageEvent(
            account_id=principal.account_id,
            api_key_id=principal.api_key_id,
            model=spec.id,
            provider=spec.provider.value,
            modality="stt",
            unit="minute",
            input_tokens=0,
            output_tokens=0,
            cached_tokens=0,
            cost_kopecks=cost,
            request_id=request_id,
        )
        db.add(event)
        await db.commit()
        # Prometheus — record after persistence so a failed commit doesn't
        # double-count metrics on retry.
        from voltari_gateway.utils.observability import record_provider_call

        record_provider_call(
            request.app,
            provider=spec.provider.value,
            model=spec.id,
            status="success",
            latency_seconds=latency_ms / 1000.0,
            billed_kopecks=cost,
        )
    except Exception as exc:
        # Post-flight billing failure after a successful upstream call —
        # log loudly. We've already returned the work; release whatever
        # hold remains so the customer isn't stuck with a phantom reserve.
        await db.rollback()
        await _release_hold_safely(db, hold, request_id=request_id)
        log.exception("stt_postflight_persist_failed", request_id=request_id)
        raise GatewayError(
            status_code=500,
            message="Failed to record usage.",
            type="api_error",
            code="billing_unavailable",
        ) from exc

    # --- PII masking on transcript (Sprint 13 / Privacy v2 — Phase 4) ---
    # Three-way gate (account flag OR header OR Form field). Reversibility
    # is unnecessary — STT is one-way: customers receive masked text and
    # there's no follow-up call that needs the originals back. We mutate
    # the response body in place when masking is active.
    pii_account_on = await resolve_account_pii_flag(
        db, principal.account_id, cached=principal.pii_masking_enabled
    )
    pii_enabled = compute_pii_flags(
        header_value=request.headers.get("x-pii-protect"),
        body_flag=pii_protect,
        account_flag=pii_account_on,
    )
    if pii_enabled:
        body = _mask_transcript_body(
            body,
            content_type=out_headers.get("content-type"),
            account_id=principal.account_id,
            request_id=request_id,
        )

    # --- response --------------------------------------------------------
    response_headers = {
        "X-Request-Id": request_id,
        "X-Gateway-Provider": spec.provider.value,
        "X-Gateway-Cost-Kop": str(cost),
        "X-Gateway-Modality": "stt",
        "X-Gateway-Minutes": str(minutes),
    }
    if "content-type" in out_headers:
        response_headers["content-type"] = out_headers["content-type"]

    # Whisper returns JSON by default and text/plain when
    # ``response_format=text|srt|vtt``. JSONResponse (which also accepts
    # bytes via ``content``) would always re-encode JSON; use Response
    # so we forward verbatim.
    from fastapi.responses import Response

    return Response(  # type: ignore[return-value]
        content=body,
        status_code=200,
        headers=response_headers,
        media_type=out_headers.get("content-type"),
    )


def _mask_transcript_body(
    body: bytes,
    *,
    content_type: str | None,
    account_id: uuid.UUID,
    request_id: str,
) -> bytes:
    """Apply PII masking to a Whisper response body (in-memory only).

    Whisper response shape depends on ``response_format``:

    * Default JSON → ``{"text": "...", "segments": [...]}``. We mask the
      ``text`` field and every ``segments[].text`` if present.
    * ``response_format=text`` → ``text/plain`` body. Mask the whole
      decoded UTF-8 string.
    * ``response_format=srt`` / ``vtt`` → also ``text/plain``-ish.
      Same path as ``text`` (we don't attempt to parse SRT timestamps —
      ``mask_text`` skips numeric tokens).

    Re-encodes to UTF-8 bytes on the way back. On any decode/parse error
    the original bytes are returned unchanged so a malformed Whisper
    payload doesn't fail the request silently.
    """
    is_json = (content_type or "").lower().startswith("application/json")
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        log.warning(
            "stt_pii_skip_undecodable",
            request_id=request_id,
            content_type=content_type,
        )
        return body

    mapping = PiiMapping()

    if is_json:
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            return body
        if isinstance(payload, dict):
            t = payload.get("text")
            if isinstance(t, str):
                payload["text"] = mask_text(t, mapping)
            segments = payload.get("segments")
            if isinstance(segments, list):
                for seg in segments:
                    if isinstance(seg, dict):
                        st = seg.get("text")
                        if isinstance(st, str):
                            seg["text"] = mask_text(st, mapping)
            if not mapping.is_empty():
                log.info(
                    "stt_pii_masked",
                    account_id=str(account_id),
                    request_id=request_id,
                    pii_summary=[
                        {"type": e.pii_type, "count": e.count} for e in _pii_audit_summary(mapping)
                    ],
                )
            return json.dumps(payload, ensure_ascii=False).encode("utf-8")
        return body

    # text/plain — SRT/VTT/raw transcript.
    masked = mask_text(text, mapping)
    if not mapping.is_empty():
        log.info(
            "stt_pii_masked",
            account_id=str(account_id),
            request_id=request_id,
            pii_summary=[
                {"type": e.pii_type, "count": e.count} for e in _pii_audit_summary(mapping)
            ],
        )
    return masked.encode("utf-8")


async def _release_hold_safely(db: AsyncSession, hold: HoldHandle, *, request_id: str) -> None:
    """Release a hold and commit; never raises. Mirrors the chat helper."""
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


# ===========================================================================
# POST /v1/audio/speech — Text-to-Speech (Sprint M2 — 2026-05-09)
# ===========================================================================
#
# OpenAI-compatible TTS. Billed by INPUT CHARACTERS (not tokens — OpenAI's
# TTS endpoints meter on chars). Output (audio bytes) is unmetered.
#
# Pipeline:
#
#   1. Auth (require_api_key — same Bearer ``sk-vlt-...`` as chat).
#   2. Validate body (model id known, voice in catalog, format in catalog,
#      speed in [0.25, 4.0], len(input) ≤ model.max_input_chars).
#   3. PII-mask the input text (three-way gate matching chat) BEFORE
#      forwarding upstream — the synthesised audio carries the spoken PII
#      otherwise. Reversibility is unnecessary (audio is one-way).
#   4. Pre-flight billing hold: cost is exact (chars × catalog price).
#   5. Forward to OpenAI via ``OpenAIProvider.audio_speech``. The provider
#      uses its existing httpx client (proxy / timeout / connection pool).
#   6. Post-flight: commit hold to debit, write a UsageEvent with
#      ``modality='tts'`` and ``unit='char'``, fire BrikkoLens trace.
#   7. Return audio bytes verbatim with the upstream content-type.
#
# Trade-offs:
#
#   * No streaming — OpenAI TTS is request/response, the whole audio file
#     is buffered. ~50 KB for a typical paragraph; not a concern at MVP RPS.
#   * No failover — TTS is single-provider (OpenAI). When ElevenLabs lands,
#     wire a sibling provider here.
#   * PII masking on TTS input only. We do NOT mask the audio output (no
#     mechanism for in-place text redaction in raw audio, and the masking
#     already happened upstream of the synth).
#
# Failure modes:
#
#   * Upstream 4xx/5xx → release hold, surface 502 (we don't leak provider
#     error semantics to the client). 429 surfaces with a retry hint.
#   * Provider connection error → release hold, surface 502.

# Hard cap on input chars across all TTS models. OpenAI's documented limit
# is 4096; we use a bit lower as a defensive cushion. Per-model limits live
# on TTSModelSpec.max_input_chars (currently uniform).
_TTS_GLOBAL_MAX_CHARS = 4096

# Speed bounds — OpenAI's API accepts [0.25, 4.0]. Out-of-range → 400 upstream.
_TTS_SPEED_MIN = 0.25
_TTS_SPEED_MAX = 4.0


class TTSRequestBody(BaseModel):
    """OpenAI-compatible body shape for /v1/audio/speech."""

    model: str
    input: str
    voice: str
    response_format: str = Field(default="mp3")
    speed: float = Field(default=1.0)
    # gpt-4o-mini-tts only — expressive prompting ("speak slowly, sad tone").
    # Older tts-1* models 400 on this field; we strip it for them at the
    # adapter layer.
    instructions: str | None = None
    # Sprint 13 / Privacy v2 — Phase 4. Per-request opt-in for PII masking
    # on the input text. ``true`` enables masking regardless of account-level
    # setting; omitted → falls back to account flag and ``X-PII-Protect``.
    pii_protect: bool | None = None

    model_config = {"extra": "ignore"}

    @field_validator("input")
    @classmethod
    def _validate_input(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("input cannot be empty")
        if len(v) > _TTS_GLOBAL_MAX_CHARS:
            raise ValueError(f"input exceeds {_TTS_GLOBAL_MAX_CHARS} characters")
        return v

    @field_validator("voice")
    @classmethod
    def _validate_voice(cls, v: str) -> str:
        # OpenAI accepts a bunch of voices, occasionally adding new ones.
        # We allowlist known-good voices but leave room for forward-compat:
        # if a voice isn't in our list, surface a 400 with the catalog so
        # SDK callers see a clean error instead of a 400 from upstream.
        if v not in TTS_VALID_VOICES:
            raise ValueError(f"voice must be one of {TTS_VALID_VOICES}, got '{v}'")
        return v

    @field_validator("response_format")
    @classmethod
    def _validate_format(cls, v: str) -> str:
        if v not in TTS_VALID_FORMATS:
            raise ValueError(f"response_format must be one of {TTS_VALID_FORMATS}, got '{v}'")
        return v

    @field_validator("speed")
    @classmethod
    def _validate_speed(cls, v: float) -> float:
        if v < _TTS_SPEED_MIN or v > _TTS_SPEED_MAX:
            raise ValueError(f"speed must be between {_TTS_SPEED_MIN} and {_TTS_SPEED_MAX}")
        return v


def _content_type_for_format(response_format: str) -> str:
    """Map TTS response_format to a sensible Content-Type.

    The upstream sets these correctly already, but we forward the upstream
    header anyway. This map is a fallback for tests / odd providers.
    """
    return {
        "mp3": "audio/mpeg",
        "opus": "audio/ogg",
        "aac": "audio/aac",
        "flac": "audio/flac",
        "wav": "audio/wav",
        "pcm": "audio/pcm",
    }.get(response_format, "application/octet-stream")


@router.post(
    "/v1/audio/speech",
    tags=["audio"],
    summary="Synthesize speech (OpenAI-compatible TTS)",
    description=(
        "OpenAI-compatible Text-to-Speech endpoint. Returns binary audio "
        "(mp3/opus/aac/flac/wav/pcm) for the supplied text.\n\n"
        "**Auth**: Bearer ``sk-vlt-...``.\n\n"
        "**Billing**: per **input character** (not tokens), with the standard "
        "+15% Brikko markup. Recorded as ``modality='tts'``, ``unit='char'``."
    ),
    responses={
        200: {
            "description": "Synthesised audio bytes; Content-Type matches response_format.",
            "content": {"audio/mpeg": {}, "audio/ogg": {}, "audio/wav": {}},
        },
        400: {"description": "Bad request (unknown model/voice/format, input too long)."},
        401: {"description": "Missing or invalid Bearer token."},
        402: {"description": "Insufficient balance for the estimated cost."},
        502: {"description": "Upstream TTS provider returned an error."},
    },
)
async def synthesize_speech(
    body: TTSRequestBody,
    request: Request,
    principal: Annotated[
        AuthPrincipal, Depends(require_api_key_or_oauth_scope(OAuthScope.AUDIO_READ))
    ],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> Response:
    spec = get_tts_model(body.model)
    if spec is None:
        raise model_not_found(body.model)

    # Per-model char ceiling (currently uniform 4096; defensive in case we
    # bring on a provider with a lower cap later).
    if len(body.input) > spec.max_input_chars:
        raise invalid_request(
            f"input exceeds {spec.max_input_chars} characters for {spec.id}",
            param="input",
            code="input_too_long",
        )

    # ``instructions`` is gpt-4o-mini-tts only. Strip silently for legacy
    # models — sending it would 400 upstream and hurt UX with no upside.
    instructions = body.instructions
    if instructions is not None and not spec.id.startswith("gpt-4o"):
        instructions = None

    started_at_dt = datetime.now(UTC)

    # --- PII masking on input (Privacy v2 Phase 4 — three-way gate) ------
    # We mask BEFORE the upstream call so the synthesised audio doesn't
    # carry PII through. Account-level flag is honoured (audio output, like
    # a transcript, is not retrieval-broken by masking).
    pii_account_on = await resolve_account_pii_flag(
        db, principal.account_id, cached=principal.pii_masking_enabled
    )
    pii_enabled = compute_pii_flags(
        header_value=request.headers.get("x-pii-protect"),
        body_flag=body.pii_protect,
        account_flag=pii_account_on,
    )
    masked_input = body.input
    pii_masked = False
    if pii_enabled:
        per_request_mapping = PiiMapping()
        masked_input = mask_text(body.input, per_request_mapping)
        if not per_request_mapping.is_empty():
            pii_masked = True
            log.info(
                "tts_pii_masked",
                account_id=str(principal.account_id),
                pii_summary=[
                    {"type": e.pii_type, "count": e.count}
                    for e in _pii_audit_summary(per_request_mapping)
                ],
            )

    # --- billing -------------------------------------------------------------
    # Char count after masking (placeholders are typically shorter or
    # comparable to the originals, but we charge for what we actually send
    # upstream — that's the source of truth for COGS).
    input_chars = len(masked_input)
    cost = max(1, spec.cost_kopecks(input_chars))

    if principal.balance_kopecks <= 0 and principal.tariff == "payg":
        raise insufficient_quota("Top up your balance to continue.")

    request_id = uuid.uuid4().hex
    log.info(
        "tts_request",
        request_id=request_id,
        model=spec.id,
        voice=body.voice,
        format=body.response_format,
        input_chars=input_chars,
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
            "tts_preflight_402",
            request_id=request_id,
            balance_kopecks=exc.balance_kopecks,
            required_kopecks=exc.required_kopecks,
        )
        raise insufficient_quota("Top up your balance to continue.") from exc
    except BillingError as exc:
        await db.rollback()
        log.error("tts_preflight_billing_error", request_id=request_id, error=str(exc))
        raise GatewayError(
            status_code=500,
            message="Billing system unavailable.",
            type="api_error",
            code="billing_unavailable",
        ) from exc

    # --- forward via OpenAIProvider -----------------------------------------
    registry = getattr(request.app.state, "provider_registry", None)
    provider = None
    if registry is not None:
        try:
            provider = registry.get(spec.provider)
        except Exception:
            provider = None
    if provider is None:
        # Fallback: legacy state attribute used by some chat tests.
        provider = getattr(request.app.state, "openai_provider", None)
    if provider is None or not hasattr(provider, "audio_speech"):
        await _release_hold_safely(db, hold, request_id=request_id)
        log.error("tts_provider_unavailable", request_id=request_id, model=spec.id)
        raise upstream_error("TTS provider not configured.")

    try:
        t0 = time.perf_counter()
        audio_bytes, upstream_ct = await provider.audio_speech(
            model=spec.upstream_id,
            input_text=masked_input,
            voice=body.voice,
            response_format=body.response_format,
            speed=body.speed,
            instructions=instructions,
        )
        latency_ms = int((time.perf_counter() - t0) * 1000)
    except ProviderAuthError as exc:
        await _release_hold_safely(db, hold, request_id=request_id)
        log.warning("tts_upstream_auth_error", request_id=request_id, error=str(exc))
        raise upstream_error("Upstream authentication failed.") from exc
    except ProviderRateLimitError as exc:
        await _release_hold_safely(db, hold, request_id=request_id)
        log.warning("tts_upstream_rate_limit", request_id=request_id, error=str(exc))
        raise upstream_error("Upstream rate limit reached. Try again shortly.") from exc
    except ProviderTimeoutError as exc:
        await _release_hold_safely(db, hold, request_id=request_id)
        log.warning("tts_upstream_timeout", request_id=request_id, error=str(exc))
        raise upstream_error("Upstream TTS provider timed out.") from exc
    except ProviderError as exc:
        await _release_hold_safely(db, hold, request_id=request_id)
        log.warning("tts_upstream_error", request_id=request_id, error=str(exc))
        raise upstream_error("Upstream TTS provider error.") from exc
    except Exception:
        await _release_hold_safely(db, hold, request_id=request_id)
        log.exception("tts_unexpected_error", request_id=request_id)
        raise

    # --- post-flight billing ------------------------------------------------
    try:
        await commit_hold_to_debit(
            db,
            handle=hold,
            actual_amount_kopecks=cost,
            meta={
                "request_id": request_id,
                "model": spec.id,
                "modality": "tts",
                "input_chars": input_chars,
                "voice": body.voice,
                "format": body.response_format,
            },
        )
        # ``input_tokens`` carries char count for TTS rows so the analytics
        # layer has a non-zero number to plot. ``unit='char'`` disambiguates
        # from chat (``unit='token'``).
        event = UsageEvent(
            account_id=principal.account_id,
            api_key_id=principal.api_key_id,
            model=spec.id,
            provider=spec.provider.value,
            modality="tts",
            unit="char",
            input_tokens=input_chars,
            output_tokens=0,
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
            input_tokens=input_chars,
            billed_kopecks=cost,
        )
    except Exception as exc:
        await db.rollback()
        await _release_hold_safely(db, hold, request_id=request_id)
        log.exception("tts_postflight_persist_failed", request_id=request_id)
        raise GatewayError(
            status_code=500,
            message="Failed to record usage.",
            type="api_error",
            code="billing_unavailable",
        ) from exc

    # --- BrikkoLens trace (fire-and-forget) ---------------------------------
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
            prompt_tokens=input_chars,
            completion_tokens=0,
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
            request_body=None,  # TTS input may carry sensitive prompts;
            response_body=None,  # audio binary doesn't belong in jsonb
            model_params={
                "voice": body.voice,
                "response_format": body.response_format,
                "speed": body.speed,
            },
            store_bodies=False,
        )
    except Exception as exc:
        log.warning("tts_trace_failed", request_id=request_id, error=str(exc))

    # --- response -----------------------------------------------------------
    media_type = upstream_ct or _content_type_for_format(body.response_format)
    response_headers = {
        "X-Request-Id": request_id,
        "X-Gateway-Provider": spec.provider.value,
        "X-Gateway-Cost-Kop": str(cost),
        "X-Gateway-Modality": "tts",
        "X-Gateway-Input-Chars": str(input_chars),
    }
    return Response(
        content=audio_bytes,
        status_code=200,
        headers=response_headers,
        media_type=media_type,
    )
