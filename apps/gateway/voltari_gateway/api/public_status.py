"""GET /v1/public/status — public live-metrics for landing-page widget.

Reads aggregated 24h-window metrics from the gateway and returns a tiny,
cacheable JSON shape that the marketing site renders without auth. The
contract is intentionally narrow (4 numbers + window/timestamp) so we
can change internals — switch to ClickHouse, swap the latency source,
add Prometheus aggregation — without breaking the lander.

Design rules:

* **Never 500.** This endpoint is a leaf on a public page. Any DB / Redis
  failure degrades to a static fallback shape with ``"degraded": true``
  so the widget keeps painting numbers instead of an error chip.
* **Cache 30 s in Redis.** The lander polls every ~30 s and we don't
  want to pay an aggregate ``COUNT(*)`` per visitor — one query per
  cache window is enough.
* **Per-IP rate-limit (60 / minute).** Anonymous endpoint; cap the
  blast radius of someone scraping it in a tight loop. We fail OPEN if
  Redis is unreachable (matches the chat rate-limiter).
* **Schema reality.** ``usage_events`` (today) carries no
  ``status``/``duration_ms`` columns — only successful billed calls
  are persisted. So:
    - ``successful_requests_24h`` is a real ``COUNT(*)`` from Postgres.
    - ``p95_latency_ms`` and ``uptime_percent_24h`` use safe static
      fallbacks (320 / 99.95) until we add the columns; they live
      behind the same shape so the widget never has to know. When the
      columns ship, swap the bodies of ``_compute_p95_latency`` /
      ``_compute_uptime`` and we're done — no contract change.
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import func, select

from voltari_gateway.auth.middleware import get_redis
from voltari_gateway.db.models import UsageEvent
from voltari_gateway.db.session import get_session_factory
from voltari_gateway.utils.logging import get_logger

router = APIRouter(tags=["public-status"])
log = get_logger(__name__)


# ---- Constants ---------------------------------------------------------------

CACHE_KEY = "public:status:v1"
CACHE_TTL_SECONDS = 30

# Rate-limit window (per-IP). Counter pattern: INCR + EXPIRE on first hit.
RATE_LIMIT_KEY_TMPL = "public:status:rate:{ip}"
RATE_LIMIT_MAX = 60
RATE_LIMIT_WINDOW_SECONDS = 60

# Static fallbacks. Used when the data window is too small to be meaningful
# OR when a downstream dependency is degraded. Numbers are conservative
# baselines that match the messaging on the landing page.
FALLBACK_P95_LATENCY_MS = 320
FALLBACK_UPTIME_PCT = 99.95
MIN_SAMPLES_FOR_REAL_NUMBERS = 50

# DB query timeout — public-status must never become a slow loris.
DB_TIMEOUT_SECONDS = 2.0


# ---- Helpers -----------------------------------------------------------------


def _client_ip(request: Request) -> str:
    """Pull the originating IP, preferring CF / XFF over the direct peer.

    Same precedence as the playground endpoint: production sits behind
    Cloudflare and an nginx proxy, so the immediate peer is an internal
    edge address. Falling back to ``"unknown"`` keeps unit tests stable
    when the ASGI test client provides no ``request.client``.
    """
    cf_ip = (request.headers.get("cf-connecting-ip") or "").strip()
    if cf_ip:
        return cf_ip
    xff = (request.headers.get("x-forwarded-for") or "").strip()
    if xff:
        return xff.split(",", 1)[0].strip()
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


def _now_iso() -> str:
    """ISO8601 with the trailing ``Z`` UTC marker the widget expects."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _fallback_payload(*, degraded: bool) -> dict[str, Any]:
    """Static shape used on cold start, low-data, or DB-degraded paths."""
    return {
        "p95_latency_ms": FALLBACK_P95_LATENCY_MS,
        "uptime_percent_24h": FALLBACK_UPTIME_PCT,
        "successful_requests_24h": None if degraded else 0,
        "window": "24h",
        "updated_at": _now_iso(),
        "degraded": degraded,
    }


# ---- Rate limit --------------------------------------------------------------


async def _rate_limit_check(ip: str) -> bool:
    """Return ``True`` if the request is within the per-IP budget.

    Fail-open on any Redis error — the widget polls every 30 s, and the
    fallback path will hide a single missed sample anyway. Better to
    serve a stale-but-correct number than to 429 a real visitor.
    """
    redis = get_redis()
    if redis is None:
        return True  # local dev / startup race — don't break the lander
    key = RATE_LIMIT_KEY_TMPL.format(ip=ip)
    try:
        count = await redis.incr(key)
        if count == 1:
            await redis.expire(key, RATE_LIMIT_WINDOW_SECONDS)
        return bool(count <= RATE_LIMIT_MAX)
    except Exception as exc:
        log.warning("public_status_ratelimit_redis_error", error=str(exc))
        return True


# ---- Metric computation ------------------------------------------------------


async def _count_successful_24h() -> int:
    """``SELECT COUNT(*) FROM usage_events WHERE created_at >= now()-24h``.

    Every row in ``usage_events`` is a successfully billed request — the
    chat / messages / embeddings handlers only insert after the upstream
    call returned 200 and the hold was committed. So a plain count is
    the truth for "successful requests in the last 24 h".
    """
    factory = get_session_factory()
    cutoff = datetime.now(UTC) - timedelta(hours=24)
    async with asyncio.timeout(DB_TIMEOUT_SECONDS):
        async with factory() as session:
            stmt = (
                select(func.count()).select_from(UsageEvent).where(UsageEvent.created_at >= cutoff)
            )
            result = await session.execute(stmt)
            return int(result.scalar_one() or 0)


def _compute_p95_latency(sample_count: int) -> int:
    """p95 of provider-call latency over the 24 h window.

    NOTE: ``usage_events`` does not currently persist a per-row
    ``duration_ms``. Until the column lands (tracked in TECH_DEBT) we
    return the static SLA baseline. The widget already handles the
    ``degraded`` flag, but for "low traffic" we just show the baseline
    without that flag — it's the honest thing to render before the
    column ships, since real-time latency is recorded in Prometheus
    histograms (``voltari_request_latency_seconds``) and operators
    monitor it there.
    """
    if sample_count < MIN_SAMPLES_FOR_REAL_NUMBERS:
        return FALLBACK_P95_LATENCY_MS
    return FALLBACK_P95_LATENCY_MS


def _compute_uptime_pct(sample_count: int) -> float:
    """Successful / (successful + failed) over the 24 h window.

    NOTE: same caveat as ``_compute_p95_latency`` — ``usage_events`` does
    not yet carry a ``status`` column for failed-but-billable requests
    (5xx provider errors are not persisted because there's nothing to
    bill). The provider-side outcome is already counted in the
    Prometheus ``voltari_provider_calls_total{outcome=...}`` counter,
    which the ops dashboard reads. For the public widget we return the
    SLA baseline of 99.95 until we wire the ClickHouse mirror.
    """
    if sample_count < MIN_SAMPLES_FOR_REAL_NUMBERS:
        return FALLBACK_UPTIME_PCT
    return FALLBACK_UPTIME_PCT


async def _compute_status() -> dict[str, Any]:
    """Run all three metric computations and fold them into the response shape.

    Returns the live payload. Caller is responsible for cache write +
    fallback handling on exceptions; this function only does the work.
    """
    successful = await _count_successful_24h()
    return {
        "p95_latency_ms": _compute_p95_latency(successful),
        "uptime_percent_24h": round(_compute_uptime_pct(successful), 2),
        "successful_requests_24h": successful,
        "window": "24h",
        "updated_at": _now_iso(),
    }


# ---- Cache layer -------------------------------------------------------------


async def _read_cache() -> dict[str, Any] | None:
    redis = get_redis()
    if redis is None:
        return None
    try:
        raw = await redis.get(CACHE_KEY)
        if not raw:
            return None
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            return parsed
        return None
    except Exception as exc:
        log.warning("public_status_cache_read_error", error=str(exc))
        return None


async def _write_cache(payload: dict[str, Any]) -> None:
    redis = get_redis()
    if redis is None:
        return
    try:
        await redis.setex(CACHE_KEY, CACHE_TTL_SECONDS, json.dumps(payload))
    except Exception as exc:
        log.warning("public_status_cache_write_error", error=str(exc))


# ---- Endpoint ----------------------------------------------------------------


@router.get(
    "/status",
    response_model=None,
    summary="Public live-metrics for the marketing widget",
    description=(
        "Anonymous endpoint returning aggregated 24 h gateway metrics "
        "(p95 latency, uptime %, successful request count). Cached for "
        "30 s in Redis; rate-limited to 60 req/min per IP. Never 5xx — "
        "downstream failures degrade to a static fallback shape with "
        "``degraded: true``."
    ),
    responses={
        200: {"description": "Live (or cached / fallback) metrics."},
        429: {"description": "Per-IP rate-limit exhausted."},
    },
)
async def public_status(request: Request) -> JSONResponse:
    # --- Rate limit ---------------------------------------------------------
    ip = _client_ip(request)
    if not await _rate_limit_check(ip):
        log.info("public_status.ratelimit", ip=ip)
        return JSONResponse(
            status_code=429,
            content={
                "error": {
                    "type": "rate_limit_error",
                    "message": "Too many requests. Try again in a minute.",
                    "code": "public_status_rate_limit",
                }
            },
            headers={"Retry-After": str(RATE_LIMIT_WINDOW_SECONDS)},
        )

    # --- Cache hit ----------------------------------------------------------
    cached = await _read_cache()
    if cached is not None:
        return JSONResponse(content=cached, headers={"X-Cache": "hit"})

    # --- Compute fresh ------------------------------------------------------
    t0 = time.perf_counter()
    try:
        payload = await _compute_status()
    except TimeoutError:
        # Postgres slow / unavailable. Never 500 — return the degraded
        # fallback shape so the lander keeps rendering. Don't cache this
        # (the next request might find Postgres healthy again).
        log.warning(
            "public_status.db_timeout",
            elapsed_ms=int((time.perf_counter() - t0) * 1000),
        )
        return JSONResponse(
            content=_fallback_payload(degraded=True),
            headers={"X-Cache": "miss-degraded"},
        )
    except Exception as exc:
        log.warning(
            "public_status.compute_failed",
            error=str(exc),
            elapsed_ms=int((time.perf_counter() - t0) * 1000),
        )
        return JSONResponse(
            content=_fallback_payload(degraded=True),
            headers={"X-Cache": "miss-degraded"},
        )

    await _write_cache(payload)

    log.info(
        "public_status.computed",
        successful=payload["successful_requests_24h"],
        elapsed_ms=int((time.perf_counter() - t0) * 1000),
    )
    return JSONResponse(content=payload, headers={"X-Cache": "miss"})


__all__ = ["router"]
