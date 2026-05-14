"""POST /v1/public/playground — anonymous sandbox endpoint (Sprint 12).

PRD: 02_Product/v1.5/31_public_sandbox_prd_2026-05-01.md.

What's different from /v1/chat/completions:

* **No auth** — публичный endpoint, никаких Bearer-token/cookie. Поэтому
  изолирован в отдельный module/router и НЕ зацеплен на ``require_api_key``.
* **Rate-limit по хешу IP** — 5/час и 15/сутки, ключи в Redis. SHA256(ip)
  чтобы не светить IP в логах (PII-friendly).
* **Глобальный budget cap** — 300 ₽/день (целое в копейках) в ключе
  ``sandbox:budget:day:{YYYY-MM-DD}``. При >= лимита 503 + Telegram-alert.
* **Cache** — SHA256(model+prompt) → JSON-ответ, TTL 1ч. Cache hit не
  списывает budget (защита COGS на популярных промптах "hi", "тест").
* **PII masking всегда ON** — защита если ввели email/телефон/паспорт.
* **max_tokens принудительно 150** — даже если клиент попытался обмануть.
* **Не пишем в usage_events** — это публичный endpoint без account_id,
  биллить нечего и некому. COGS считаем через ``sandbox:budget`` counter.
* **Только sync, без streaming** — 150 tokens возвращается за <3 сек.

Sandbox делегирует upstream-вызов через тот же ProviderRegistry +
Router, что и платный API — переиспользуем failover/circuit-breaker и
не дублируем 200 строк сетевого кода. Системный SANDBOX_API_KEY
выступает как marker "настроено" + (в будущем) handle для отдельного
account, к которому будет приклеен COGS.
"""

from __future__ import annotations

import hashlib
import json
import time
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from voltari_gateway.auth.middleware import get_redis
from voltari_gateway.billing import compute_cost_kopecks
from voltari_gateway.config import get_settings
from voltari_gateway.pii import mask_messages
from voltari_gateway.providers.base import (
    ChatCompletionRequest,
    ProviderClientError,
    ProviderError,
)
from voltari_gateway.providers.registry import ProviderRegistry
from voltari_gateway.router.catalog import ModelSpec, get_model
from voltari_gateway.router.failover import FailoverError, with_failover
from voltari_gateway.router.pipeline import (
    PipelineContext,
    RouterPipeline,
    parse_budget_kopecks_header,
    should_use_pipeline,
)
from voltari_gateway.router.router import (
    AccountContext,
    RouterRequest,
)
from voltari_gateway.router.router import (
    Router as RouterEngine,
)
from voltari_gateway.utils.errors import GatewayError, invalid_request
from voltari_gateway.utils.logging import get_logger

router = APIRouter()
log = get_logger(__name__)


# ---------- model whitelist ---------------------------------------------------

# Пять SKU выбраны под бюджет 300 ₽/день — все nano/budget tier (см. PRD §3.2).
# Жёсткий whitelist на уровне Pydantic — даже если в catalog появится более
# дорогая модель с похожим id, sandbox её не пропустит.
SandboxModelId = Literal[
    "deepseek-v4-flash",
    "gpt-5.4-mini",
    "claude-haiku-4.5",
    "gemini-3-flash",
    "yandexgpt-5-lite",
    "gigachat-2-lite",
]

ALLOWED_MODELS: frozenset[str] = frozenset(
    {
        "deepseek-v4-flash",
        "gpt-5.4-mini",
        "claude-haiku-4.5",
        "gemini-3-flash",
        "yandexgpt-5-lite",
        "gigachat-2-lite",
    }
)


# ---------- request body ------------------------------------------------------


class PlaygroundRequestBody(BaseModel):
    """Sandbox-запрос. Минимальный контракт — никаких temperature/tools/etc.

    ``model`` — Literal'ом, чтобы 422 пришёл уже на pydantic-уровне без
    обращения к catalog. Это часть policy: расширение списка моделей
    требует осознанного code-review (не conf-toggle).

    ``prompt`` — 1..N символов, где N контролится через настройку
    ``sandbox_max_prompt_length`` (default 500). Чтобы валидатор знал
    про runtime-лимит, ставим max 4000 на pydantic-уровне как «крыша
    безопасности», а реальное усечение делаем в endpoint'е по
    settings — иначе пришлось бы импортировать settings до schema-build.
    """

    model: SandboxModelId
    prompt: str = Field(min_length=1, max_length=4000)


# ---------- helpers -----------------------------------------------------------


def _client_ip(request: Request) -> str:
    """Pull client IP from CF-Connecting-IP / X-Forwarded-For / direct.

    In production we sit behind Cloudflare (CF-Connecting-IP) and an
    nginx reverse-proxy (X-Forwarded-For). Either being present beats
    ``request.client.host`` (which would be the Cloudflare edge IP).
    Returns ``"unknown"`` only if all three are absent — that path
    isn't reachable in production but keeps the rate-limit logic
    stable in unit tests where the ASGI test-client provides no peer.
    """
    cf_ip = (request.headers.get("cf-connecting-ip") or "").strip()
    if cf_ip:
        return cf_ip
    xff = (request.headers.get("x-forwarded-for") or "").strip()
    if xff:
        # XFF — comma-separated chain "<client>, <proxy1>, <proxy2>".
        # The leftmost token is the originating client.
        return xff.split(",", 1)[0].strip()
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


def _hash_ip(ip: str) -> str:
    """SHA256(IP+pepper). Pepper = JWT_SECRET — stable per-deploy.

    We don't want raw IPs in logs / Redis values for ПДн-friendliness.
    Pepper protects against rainbow-table joining (an attacker with the
    Redis dump and a candidate IP can still test it, but не может
    bulk-deanonymise всех залогированных хешей без pepper).

    16 hex chars (64 bits) is enough — collision probability for the
    scale of /playground (≤10k IPs/day) is essentially zero, and a
    short hash keeps log lines compact.
    """
    settings = get_settings()
    pepper = settings.jwt_secret.get_secret_value().encode("utf-8")
    h = hashlib.sha256(pepper + ip.encode("utf-8")).hexdigest()
    return h[:16]


def _today_msk_iso() -> str:
    """Date string for budget-counter key (UTC; PRD §4 case 2 uses MSK
    "midnight reset" but UTC works fine for a daily counter — we just
    need a stable bucket; the 4-hour skew is harmless)."""
    return datetime.now(UTC).strftime("%Y-%m-%d")


def _cache_key(model: str, prompt: str) -> str:
    digest = hashlib.sha256(f"{model}\x00{prompt}".encode()).hexdigest()
    return f"sandbox:cache:{digest}"


# ---------- ops alert (Telegram or noop) -------------------------------------


async def _alert_budget_exhausted(request: Request, *, spent_kop: int, limit_kop: int) -> None:
    """Best-effort Telegram-alert to CEO ops channel.

    Reuses ``app.state.telegram_bot`` (already wired in main.lifespan).
    If chat_id is unset OR bot isn't configured, just logs a warning —
    a missing alert must not break the endpoint response path.
    """
    settings = get_settings()
    bot = getattr(request.app.state, "telegram_bot", None)
    chat_id = settings.sandbox_ops_chat_id
    if bot is None or chat_id is None:
        log.warning(
            "sandbox_alert_stub",
            reason="telegram_not_configured",
            spent_kop=spent_kop,
            limit_kop=limit_kop,
        )
        return
    try:
        await bot.send_message(
            chat_id,
            (
                f"⚠️ <b>Sandbox budget exhausted</b>\n"
                f"Spent: {spent_kop / 100:.2f} ₽ / {limit_kop / 100:.2f} ₽\n"
                f"Endpoint /v1/public/playground returns 503 until 00:00 UTC."
            ),
        )
    except Exception as exc:
        log.warning("sandbox_alert_failed", error=str(exc))


# ---------- endpoint ----------------------------------------------------------


@router.post(
    "/playground",
    response_model=None,
    tags=["public-sandbox"],
    summary="Anonymous LLM playground (no auth, rate-limited)",
    description=(
        "Anonymous sandbox endpoint that proxies a single prompt to one of 6 "
        "preset cheap models. Rate-limited per-IP (5/h, 15/day) and capped by "
        "a global daily ₽-budget. Intended as zero-friction wow-demo for "
        "landing-page visitors; production traffic uses /v1/chat/completions."
    ),
    responses={
        200: {"description": "Successful completion."},
        422: {"description": "Pydantic validation error (prompt too long, etc.)."},
        429: {"description": "Per-IP rate-limit exhausted (Retry-After header set)."},
        503: {
            "description": (
                "Sandbox not configured (no SANDBOX_API_KEY) OR daily budget "
                "exhausted OR all upstream providers failed."
            )
        },
    },
)
async def playground(
    body: PlaygroundRequestBody,
    request: Request,
) -> JSONResponse:
    settings = get_settings()
    redis = get_redis()

    # --- Sandbox-not-configured guard --------------------------------------
    # Empty SANDBOX_API_KEY means "do not serve sandbox traffic". 503 is
    # graceful (not 500) — the landing page can show "Sandbox temporarily
    # unavailable" instead of a generic error.
    if not settings.sandbox_api_key.get_secret_value():
        log.warning("sandbox_not_configured")
        raise GatewayError(
            status_code=503,
            message="Sandbox not configured.",
            type="api_error",
            code="sandbox_unavailable",
        )

    # --- Pydantic-level prompt-length cap (settings-driven) ----------------
    # We cap at sandbox_max_prompt_length to allow runtime tuning without
    # rebuilding the schema. Pydantic's max=4000 is a safety ceiling.
    max_prompt = settings.sandbox_max_prompt_length
    if len(body.prompt) > max_prompt:
        raise invalid_request(
            f"prompt exceeds sandbox limit of {max_prompt} characters",
            param="prompt",
            code="prompt_too_long",
        )

    # --- Resolve model spec from catalog (whitelist already enforced) ------
    model_spec = get_model(body.model)
    if model_spec is None:
        # Defensive — shouldn't happen given the Literal whitelist but
        # better a clean 503 than a KeyError later in the routing path.
        log.error("sandbox_unknown_model", model=body.model)
        raise GatewayError(
            status_code=503,
            message="Sandbox model not available.",
            type="api_error",
            code="sandbox_unavailable",
        )

    ip = _client_ip(request)
    ip_hash = _hash_ip(ip)

    # --- Rate-limits (per-IP) ---------------------------------------------
    # Counter pattern: INCR + EXPIRE only when the counter is first created.
    # We use a simple GET-then-INCR-EXPIRE — race-safe enough for a 5/hour
    # cap: at worst two concurrent requests both trigger EXPIRE on the same
    # second, no functional difference. Fail-open on Redis-down to keep
    # the endpoint responsive (matches existing chat rate-limiter).
    if redis is not None:
        hour_key = f"sandbox:rate:hour:{ip_hash}"
        day_key = f"sandbox:rate:day:{ip_hash}"
        try:
            hour_count = int(await redis.get(hour_key) or 0)
            day_count = int(await redis.get(day_key) or 0)
        except Exception as exc:
            log.warning("sandbox_rate_check_failed", error=str(exc))
            hour_count = 0
            day_count = 0

        if hour_count >= settings.sandbox_rate_limit_hour:
            log.info(
                "sandbox.ratelimit",
                ip_hash=ip_hash,
                window="hour",
                count=hour_count,
                limit=settings.sandbox_rate_limit_hour,
            )
            raise GatewayError(
                status_code=429,
                message="Hourly sandbox rate-limit reached. Try again later.",
                type="rate_limit_error",
                code="sandbox_rate_limit",
                headers={"Retry-After": "3600"},
            )
        if day_count >= settings.sandbox_rate_limit_day:
            log.info(
                "sandbox.ratelimit",
                ip_hash=ip_hash,
                window="day",
                count=day_count,
                limit=settings.sandbox_rate_limit_day,
            )
            raise GatewayError(
                status_code=429,
                message=(
                    "Daily sandbox rate-limit reached. Sign up for your own balance to continue."
                ),
                type="rate_limit_error",
                code="sandbox_rate_limit",
                headers={"Retry-After": "86400"},
            )

    # --- Budget cap (global, daily) ---------------------------------------
    budget_key = f"sandbox:budget:day:{_today_msk_iso()}"
    budget_spent = 0
    if redis is not None:
        try:
            budget_spent = int(await redis.get(budget_key) or 0)
        except Exception as exc:
            log.warning("sandbox_budget_check_failed", error=str(exc))
            budget_spent = 0

    if budget_spent >= settings.sandbox_budget_day_kop:
        log.warning(
            "sandbox.budget_exhausted",
            spent_kop=budget_spent,
            limit_kop=settings.sandbox_budget_day_kop,
        )
        # Fire-and-forget alert; never blocks the response.
        await _alert_budget_exhausted(
            request,
            spent_kop=budget_spent,
            limit_kop=settings.sandbox_budget_day_kop,
        )
        raise GatewayError(
            status_code=503,
            message=("Sandbox temporarily over budget. Sign up for your own balance to continue."),
            type="api_error",
            code="sandbox_budget_exhausted",
        )

    # --- PII masking (always ON) ------------------------------------------
    # We mask but do NOT need to unmask afterwards: sandbox returns text
    # to an anonymous browser and there's no contract to round-trip
    # original placeholders back. Если ПДн дошли до промпта — лучше отдать
    # обезличенный ответ, чем рисковать раскрытием через провайдерский
    # echo. Plus PII в самом ответе — отдельная (приемлемая) проблема:
    # модель не угадает то, что мы замаскировали на входе.
    masked_messages, _pii_mapping = mask_messages([{"role": "user", "content": body.prompt}])

    # --- Cache lookup (model+prompt → JSON-response) ----------------------
    cached_response: dict[str, Any] | None = None
    if redis is not None:
        try:
            raw_cached = await redis.get(_cache_key(body.model, body.prompt))
            if raw_cached:
                cached_response = json.loads(raw_cached)
        except Exception as exc:
            log.warning("sandbox_cache_read_failed", error=str(exc))

    # Helper to bump rate counters AFTER we commit to serving (cache hit
    # OR successful provider call). We bump on cache hit too — per PRD
    # §3.3 cache only saves COGS, not the rate-limit allowance (otherwise
    # one IP could spam "hi" forever).
    async def _bump_rate_counters() -> None:
        if redis is None:
            return
        try:
            await redis.incr(f"sandbox:rate:hour:{ip_hash}")
            await redis.expire(f"sandbox:rate:hour:{ip_hash}", 3600)
            await redis.incr(f"sandbox:rate:day:{ip_hash}")
            await redis.expire(f"sandbox:rate:day:{ip_hash}", 86400)
        except Exception as exc:
            log.warning("sandbox_rate_bump_failed", error=str(exc))

    # Compute remaining counters AFTER bump for response headers.
    async def _remaining() -> tuple[int, int]:
        if redis is None:
            return settings.sandbox_rate_limit_hour, settings.sandbox_rate_limit_day
        try:
            h = int(await redis.get(f"sandbox:rate:hour:{ip_hash}") or 0)
            d = int(await redis.get(f"sandbox:rate:day:{ip_hash}") or 0)
        except Exception:
            return 0, 0
        return (
            max(0, settings.sandbox_rate_limit_hour - h),
            max(0, settings.sandbox_rate_limit_day - d),
        )

    # ---------- CACHE HIT path -------------------------------------------
    if cached_response is not None:
        await _bump_rate_counters()
        rem_h, rem_d = await _remaining()
        log.info(
            "sandbox.request",
            ip_hash=ip_hash,
            model=body.model,
            prompt_len=len(body.prompt),
            cache_hit=True,
            latency_ms=0,
            cost_kop=0,
        )
        return JSONResponse(
            content=cached_response,
            headers={
                "X-Sandbox-Cache": "hit",
                "X-Sandbox-Remaining-Hour": str(rem_h),
                "X-Sandbox-Remaining-Day": str(rem_d),
            },
        )

    # ---------- CACHE MISS — call provider --------------------------------
    state = request.app.state
    registry: ProviderRegistry | None = getattr(state, "provider_registry", None)
    router_engine: RouterEngine | None = getattr(state, "router_engine", None)
    if registry is None or router_engine is None:
        log.error("sandbox_state_missing")
        raise GatewayError(
            status_code=503,
            message="Sandbox temporarily unavailable.",
            type="api_error",
            code="sandbox_unavailable",
        )

    configured = registry.configured_providers()
    if model_spec.provider not in configured:
        # Provider for chosen model is not wired (no API key). Sandbox
        # whitelist is small enough that we don't try to silently swap —
        # the user picked the model intentionally.
        log.warning(
            "sandbox.unavailable",
            reason="provider_not_configured",
            model=body.model,
            provider=model_spec.provider.value,
        )
        raise GatewayError(
            status_code=503,
            message=(
                "Selected model is temporarily unavailable. "
                "Try a different one (e.g. deepseek-v4-flash)."
            ),
            type="api_error",
            code="sandbox_provider_unavailable",
        )

    # We pin the model exactly — no router strategy / failover-chain. The
    # PRD says: "при ошибке — НЕ переключаем модель (юзер выбрал её
    # специально)". This keeps semantics aligned with the UI dropdown.
    router_req = RouterRequest(
        model_tag=body.model,
        estimated_input_tokens=max(1, len(body.prompt) // 3),
        prompt_text=body.prompt,
        failover_enabled=False,
        exclude_providers=frozenset(),
        require_tools=False,
    )
    # Synthetic AccountContext — only used for routing decisions, not
    # billing. UUID is a fixed sentinel so router caches don't churn.
    import uuid as _uuid

    sandbox_account_ctx = AccountContext(
        account_id=_uuid.UUID("00000000-0000-0000-0000-000000000000"),
        tariff="payg",
        balance_kop=settings.sandbox_budget_day_kop,
    )

    # Sprint S1 — Smart Router v2 pipeline gate. The sandbox account
    # is synthetic so there's no per-account flag; we look at the env
    # flag only. Flag-on routes through the v2 pipeline (currently
    # behaviour-identical to v1).
    use_pipeline_v2 = should_use_pipeline(
        env_flag=settings.smart_router_v2_enabled,
        account_flag=False,
    )
    try:
        if use_pipeline_v2:
            pipeline: RouterPipeline | None = getattr(state, "router_pipeline", None)
            if pipeline is None:
                pipeline = RouterPipeline(router_engine)
            pipeline_ctx = PipelineContext(
                router_request=router_req,
                account=sandbox_account_ctx,
                request_id=_uuid.uuid4().hex,
                session_id=request.headers.get("x-session-id"),
                budget_kopecks=parse_budget_kopecks_header(request.headers.get("x-budget-kopecks")),
                task_hint=request.headers.get("x-task-hint"),
            )
            decision = (await pipeline.run(pipeline_ctx)).decision
        else:
            decision = await router_engine.route_request(router_req, sandbox_account_ctx)
    except Exception as exc:
        log.warning("sandbox_route_failed", error=str(exc), model=body.model)
        raise GatewayError(
            status_code=503,
            message="Sandbox routing failed.",
            type="api_error",
            code="sandbox_unavailable",
        ) from exc

    chosen_model: ModelSpec = decision.primary

    # --- Build the upstream request ---------------------------------------
    # max_tokens is HARD-FORCED to settings.sandbox_max_tokens (150 by
    # default). PRD §3.3 guarantees this; the sandbox API accepts no client
    # override at all (request body has no max_tokens field).
    upstream_req = ChatCompletionRequest(
        model=chosen_model,
        messages=masked_messages,
        temperature=0.7,
        top_p=None,
        max_tokens=settings.sandbox_max_tokens,
        stream=False,
        stop=None,
        extra={},
    )

    async def _call(model: ModelSpec) -> tuple[Any, int]:
        provider = registry.get_for_model(model)
        t0 = time.perf_counter()
        resp = await provider.chat_completion(upstream_req)
        latency = int((time.perf_counter() - t0) * 1000)
        return resp, latency

    request_id = hashlib.sha256(f"{ip_hash}{time.time()}{body.prompt}".encode()).hexdigest()[:24]
    try:
        # ``with_failover`` accepts an empty fallback chain — equivalent to
        # "primary only with retry envelope". Keeps logging/metrics paths
        # consistent with /v1/chat/completions без duplicate boilerplate.
        result = await with_failover(
            chosen_model,
            [],
            _call,
            failover_enabled=False,
            request_id=request_id,
        )
    except ProviderClientError as exc:
        log.info(
            "sandbox.upstream_client_error",
            request_id=request_id,
            error=str(exc),
            model=chosen_model.id,
        )
        raise GatewayError(
            status_code=503,
            message="Upstream rejected the request. Try a different prompt.",
            type="api_error",
            code="sandbox_upstream_error",
        ) from exc
    except FailoverError as exc:
        last = exc.last_error
        log.warning(
            "sandbox.upstream_failed",
            request_id=request_id,
            error=str(last) if last else "unknown",
            model=chosen_model.id,
        )
        raise GatewayError(
            status_code=503,
            message="Upstream provider unavailable. Try again in a moment.",
            type="api_error",
            code="sandbox_upstream_error",
        ) from exc
    except ProviderError as exc:
        log.warning(
            "sandbox.upstream_failed",
            request_id=request_id,
            error=str(exc),
            model=chosen_model.id,
        )
        raise GatewayError(
            status_code=503,
            message="Upstream provider unavailable. Try again in a moment.",
            type="api_error",
            code="sandbox_upstream_error",
        ) from exc

    response_obj = result.response
    latency_ms = result.events[-1].latency_ms if result.events else 0
    cost_kop = compute_cost_kopecks(chosen_model, response_obj.usage)

    # --- Update budget counter (atomic INCRBY + EXPIRE) -------------------
    # Use INCRBY so concurrent requests никогда не теряют копейки.
    if redis is not None and cost_kop > 0:
        try:
            await redis.incrby(budget_key, cost_kop)
            await redis.expire(budget_key, 86400)
        except Exception as exc:
            log.warning("sandbox_budget_update_failed", error=str(exc))

    # --- Bump rate counters and write cache -------------------------------
    await _bump_rate_counters()
    rem_h, rem_d = await _remaining()

    # Build OpenAI-shaped response. We strip x_gateway / routing internal
    # fields — public sandbox surface is intentionally minimal.
    response_body = dict(response_obj.raw)
    # Some adapters echo provider's own model id; normalise back to the
    # public catalog id for transparency.
    response_body["model"] = chosen_model.id

    if redis is not None:
        try:
            await redis.set(
                _cache_key(body.model, body.prompt),
                json.dumps(response_body, ensure_ascii=False),
                ex=3600,
            )
        except Exception as exc:
            log.warning("sandbox_cache_write_failed", error=str(exc))

    log.info(
        "sandbox.request",
        ip_hash=ip_hash,
        model=body.model,
        prompt_len=len(body.prompt),
        cache_hit=False,
        latency_ms=latency_ms,
        cost_kop=cost_kop,
    )

    return JSONResponse(
        content=response_body,
        headers={
            "X-Sandbox-Cache": "miss",
            "X-Sandbox-Remaining-Hour": str(rem_h),
            "X-Sandbox-Remaining-Day": str(rem_d),
        },
    )


__all__ = ["router"]
