"""Health-check endpoints.

Two probes:

* ``GET /healthz`` — process-liveness only. Always 200 if the app is up.
  Used by container orchestrators (Docker, Caddy) to know the process
  hasn't crashed. Cheap, no I/O, no auth.

* ``GET /health/ready`` — readiness with downstream-dependency checks:
  database (``SELECT 1``), Redis (``PING``), each configured upstream
  provider (cheap models-list call when supported, otherwise just the
  configured-flag). Returns:

      200 + {"status": "ready"     , ...}   — everything green.
      200 + {"status": "degraded"  , ...}   — non-critical dep down (e.g.
                                              one provider) but the gateway
                                              can still serve traffic.
      503 + {"status": "down"      , ...}   — DB or Redis unreachable.

  Caddy's ``health_uri`` / UptimeRobot reads the status code; the JSON is
  for humans on dashboards.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, Request, Response
from sqlalchemy import text

from voltari_gateway import __version__
from voltari_gateway.db.session import get_session_factory
from voltari_gateway.utils.logging import get_logger

router = APIRouter(tags=["health"])
log = get_logger(__name__)


HealthStatus = Literal["ok", "down", "not_configured"]


async def _check_db(timeout_s: float = 2.0) -> HealthStatus:
    try:
        factory = get_session_factory()
        async with asyncio.timeout(timeout_s):
            async with factory() as session:
                result = await session.execute(text("SELECT 1"))
                _ = result.scalar()
        return "ok"
    except Exception as exc:
        log.warning("health_db_failed", error=str(exc))
        return "down"


async def _check_redis(request: Request, timeout_s: float = 2.0) -> HealthStatus:
    """Resolve Redis from auth.middleware (the only place we register it)."""
    from voltari_gateway.auth.middleware import get_redis

    redis_client = get_redis()
    if redis_client is None:
        return "not_configured"
    try:
        async with asyncio.timeout(timeout_s):
            await redis_client.ping()
        return "ok"
    except Exception as exc:
        log.warning("health_redis_failed", error=str(exc))
        return "down"


def _check_providers(request: Request) -> dict[str, HealthStatus]:
    """For each configured provider in the registry, return ``"ok"``.

    We deliberately do **not** make synthetic calls to upstreams — that
    burns money on every health check. ``/health/ready`` reports whether
    the adapter is built and registered; live failures will surface
    through the per-request failover/circuit-breaker path instead.
    """
    registry = getattr(request.app.state, "provider_registry", None)
    if registry is None:
        return {}
    out: dict[str, HealthStatus] = {}
    for prov in registry.configured_providers():
        out[prov.value] = "ok"
    return out


# /readyz — k8s-style alias of /health/ready. Используется deploy-pipeline
# и Caddy upstream-checks; внутреняя реализация одна и та же.
@router.get("/readyz", include_in_schema=False)
@router.get("/health/ready", include_in_schema=False)
async def health_ready(request: Request, response: Response) -> dict[str, Any]:
    db_status, redis_status = await asyncio.gather(_check_db(), _check_redis(request))
    providers = _check_providers(request)

    # DB is hard-required. Redis is hard-required because session/auth
    # middleware needs it; if absent we report "down" so Caddy stops
    # routing here.
    hard_down = db_status == "down" or redis_status == "down"
    if hard_down:
        overall: Literal["ready", "degraded", "down"] = "down"
        response.status_code = 503
    elif redis_status == "not_configured":
        # Local dev / smoke without redis — the app will still boot but
        # auth won't function. Mark degraded so ops sees it.
        overall = "degraded"
        response.status_code = 200
    else:
        overall = "ready"
        response.status_code = 200

    return {
        "status": overall,
        "version": __version__,
        "checks": {
            "database": db_status,
            "redis": redis_status,
            "providers": providers,
        },
        "timestamp": datetime.now(UTC).isoformat(),
    }
