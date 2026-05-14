"""GET /metrics — Prometheus exposition endpoint (Sprint 13).

The endpoint itself is mounted on the FastAPI app from
``voltari_gateway.utils.observability.setup_prometheus`` so the request-
instrumentation middleware and the registry stay co-located. This module
exists for two reasons:

* Discoverability — a developer grep'ing ``/metrics`` lands here first.
* Tests / docs — import path
  ``from voltari_gateway.api.metrics import router`` resolves to a FastAPI
  ``APIRouter`` that exposes the same handler against ``app.state.metrics_registry``.

Security
--------

The endpoint is unauthenticated by design. Prometheus scrape targets do
not authenticate by HTTP header in our scrape stack (Grafana Alloy on the
same VPC). The Caddyfile MUST whitelist ``/metrics`` to internal IPs only:

    # infra/Caddyfile.prod (TODO — add separately, NOT in this commit):
    @public_metrics_blocked path /metrics
    @internal_only client_ip 10.0.0.0/8 127.0.0.1 ::1
    handle @public_metrics_blocked {
        @internal_only ...
        respond 404
    }

Until that's wired the endpoint is reachable from the public internet, but
exposes no PII — only counters/histograms aggregated by labels we already
log structurally. Risk: a scraper learns roughly how busy we are. We
accept this as low-impact for MVP and track the Caddy work in TECH_DEBT.
"""

from __future__ import annotations

from fastapi import APIRouter, Request, Response

router = APIRouter()


@router.get("/metrics", include_in_schema=False)
async def metrics(request: Request) -> Response:
    """Render the Prometheus exposition format from the app's registry.

    Returns 503 with a plain-text reason if metrics are disabled
    (``METRICS_ENABLED=false``) — this is the contract Grafana Alloy
    expects: 5xx body still parseable, scrape marked as failed.
    """
    registry = getattr(request.app.state, "metrics_registry", None)
    if registry is None:
        return Response(
            content=b"# metrics disabled (set METRICS_ENABLED=true)\n",
            media_type="text/plain; version=0.0.4; charset=utf-8",
            status_code=503,
        )

    # Local import — keeps prometheus_client a soft dependency for
    # environments that build the package without observability extras.
    from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

    body = generate_latest(registry)
    return Response(content=body, media_type=CONTENT_TYPE_LATEST)


__all__ = ["router"]
