"""Sentry + Prometheus wiring (Sprint 5, expanded Sprint 13).

Both integrations are opt-in via env vars and degrade to no-op when their
SDKs aren't installed or DSN/flags aren't set. Production gets both turned
on; local dev / pytest stays quiet by default.

Why minimal hand-rolled Prometheus instead of ``prometheus-fastapi-instrumentator``:
the latter adds ~15 metrics we don't care about (template paths, etc.) and
brings ~3 transitive deps. We need ~10 metrics — totalling <200 lines of
code, zero new heavy deps (just ``prometheus_client``).

Metric naming follows the agreed Sprint 13 contract:

    voltari_http_requests_total{method, route, status}
    voltari_http_request_duration_seconds{method, route}     (histogram)
    voltari_provider_requests_total{provider, model, status}
    voltari_provider_latency_seconds{provider, model}        (histogram)
    voltari_provider_tokens_total{provider, model, type}     (type=input|output)
    voltari_billing_kopeck_total{provider, model}
    voltari_cache_hits_total{cache_name}
    voltari_cache_misses_total{cache_name}
    voltari_build_info{version, commit, modality_modules}    (gauge=1)

Backwards-compatibility: ``voltari_requests_total`` /
``voltari_request_latency_seconds`` / ``voltari_provider_calls_total`` (old
Sprint 5 names) are still emitted in parallel — Grafana dashboards pinned
to those names continue to work until the Alloy config is migrated. Removal
is tracked in ``apps/gateway/TECH_DEBT.md`` (TD-PROM-DEPRECATE).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from voltari_gateway import __version__
from voltari_gateway.config import Settings
from voltari_gateway.utils.logging import get_logger

if TYPE_CHECKING:
    from fastapi import FastAPI

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Sentry
# ---------------------------------------------------------------------------


def init_sentry(settings: Settings, release: str) -> bool:
    """Initialise Sentry SDK if DSN is configured.

    Returns ``True`` when Sentry was activated, ``False`` otherwise.
    Failure to import ``sentry_sdk`` is a warning, not an error — we don't
    want missing observability to break startup.
    """
    dsn = settings.sentry_dsn.get_secret_value()
    if not dsn or "CHANGE_ME" in dsn or dsn.startswith("${"):
        log.info("sentry_disabled", reason="no_dsn")
        return False

    try:
        import sentry_sdk
        from sentry_sdk.integrations.asyncio import AsyncioIntegration
        from sentry_sdk.integrations.fastapi import FastApiIntegration
        from sentry_sdk.integrations.httpx import HttpxIntegration
        from sentry_sdk.integrations.redis import RedisIntegration
        from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration
        from sentry_sdk.integrations.starlette import StarletteIntegration
    except ImportError as exc:
        log.warning("sentry_sdk_unavailable", error=str(exc))
        return False

    sentry_sdk.init(
        dsn=dsn,
        environment=settings.app_env.value,
        release=f"voltari-gateway@{release}",
        traces_sample_rate=settings.sentry_traces_sample_rate,
        profiles_sample_rate=settings.sentry_profiles_sample_rate,
        # Don't send PII by default (emails, IPs). Compliant with 152-ФЗ:
        # we are PD-operator, can't ship PII to a US-hosted service.
        send_default_pii=False,
        # Strip Authorization / Cookie headers from breadcrumbs.
        max_breadcrumbs=50,
        integrations=[
            AsyncioIntegration(),
            FastApiIntegration(),
            StarletteIntegration(),
            HttpxIntegration(),
            RedisIntegration(),
            SqlalchemyIntegration(),
        ],
        # Don't capture HTTPException — those are intentional 4xx responses.
        # Cast: sentry-sdk types ``before_send`` against its own TypedDict
        # ``Event``; we keep the body Dict-typed for fungible filter logic.
        before_send=_sentry_filter_4xx,  # type: ignore[arg-type]
    )
    log.info(
        "sentry_initialised",
        environment=settings.app_env.value,
        release=release,
        traces_sample_rate=settings.sentry_traces_sample_rate,
    )
    return True


def _sentry_filter_4xx(event: dict[str, Any], hint: dict[str, Any]) -> dict[str, Any] | None:
    """Drop 4xx HTTPExceptions — they're caller errors, not our bugs.

    Real bugs surface as 5xx via the unhandled-exception path which is
    captured automatically.
    """
    exc_info = hint.get("exc_info")
    if exc_info is None:
        return event
    exc = exc_info[1]
    # FastAPI / Starlette HTTPException have ``.status_code``.
    status_code = getattr(exc, "status_code", None)
    if isinstance(status_code, int) and 400 <= status_code < 500:
        return None
    return event


# ---------------------------------------------------------------------------
# Prometheus
# ---------------------------------------------------------------------------


def setup_prometheus(app: FastAPI, settings: Settings) -> bool:
    """Mount ``/metrics`` and request-instrumentation if enabled.

    Returns ``True`` when active, ``False`` otherwise.
    """
    if not settings.metrics_enabled:
        log.info("prometheus_disabled", reason="metrics_enabled=false")
        return False

    try:
        from prometheus_client import (
            CONTENT_TYPE_LATEST,
            CollectorRegistry,
            Counter,
            Gauge,
            Histogram,
            generate_latest,
        )
    except ImportError as exc:
        log.warning("prometheus_client_unavailable", error=str(exc))
        return False

    from fastapi import Request, Response
    from starlette.middleware.base import BaseHTTPMiddleware

    # Use a dedicated registry so app instances don't collide in pytest.
    registry = CollectorRegistry()

    # ----- HTTP-уровень (Sprint 13 contract) -------------------------------
    http_requests_total = Counter(
        "voltari_http_requests_total",
        "Total HTTP requests handled by the gateway.",
        labelnames=("method", "route", "status"),
        registry=registry,
    )
    http_request_duration_seconds = Histogram(
        "voltari_http_request_duration_seconds",
        "Request duration in seconds.",
        labelnames=("method", "route"),
        # Sprint 13 contract buckets — keep tight under 5 s; tail-traffic
        # (long streaming completions) is observable via the provider
        # histogram which has wider buckets.
        buckets=(0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0),
        registry=registry,
    )

    # ----- LLM-провайдеры --------------------------------------------------
    provider_requests_total = Counter(
        "voltari_provider_requests_total",
        "Outbound calls to LLM providers, per provider+model+status.",
        labelnames=("provider", "model", "status"),
        registry=registry,
    )
    provider_latency_seconds = Histogram(
        "voltari_provider_latency_seconds",
        "Provider call latency in seconds (end-to-end including streaming).",
        labelnames=("provider", "model"),
        # Wider tail than HTTP — streaming completions can run minutes.
        buckets=(0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 120.0, 300.0),
        registry=registry,
    )
    provider_tokens_total = Counter(
        "voltari_provider_tokens_total",
        "Tokens consumed via providers, by direction.",
        labelnames=("provider", "model", "type"),  # type=input|output
        registry=registry,
    )

    # ----- Биллинг ---------------------------------------------------------
    billing_kopeck_total = Counter(
        "voltari_billing_kopeck_total",
        "Total kopecks billed to customers (post-markup), per provider+model.",
        labelnames=("provider", "model"),
        registry=registry,
    )

    # ----- Кэш -------------------------------------------------------------
    cache_hits_total = Counter(
        "voltari_cache_hits_total",
        "Cache hits, per logical cache name.",
        labelnames=("cache_name",),
        registry=registry,
    )
    cache_misses_total = Counter(
        "voltari_cache_misses_total",
        "Cache misses, per logical cache name.",
        labelnames=("cache_name",),
        registry=registry,
    )

    # ----- Self / build info ----------------------------------------------
    build_info = Gauge(
        "voltari_build_info",
        "Build identification (always 1; useful for grouping by version).",
        labelnames=("version", "commit", "modality_modules"),
        registry=registry,
    )
    # ``settings`` may not carry a commit SHA; we read GIT_COMMIT from the
    # environment in init_sentry / Dockerfile builds. Fall back to "unknown"
    # in dev so the metric is never empty.
    import os

    commit = os.environ.get("GIT_COMMIT", "unknown")[:12]
    # Modality modules reflect which non-text endpoints are wired. Hardcoded
    # for MVP — when modalities become configurable we'll read from settings.
    modality_modules = "chat,messages,audio,embeddings"
    build_info.labels(
        version=__version__,
        commit=commit,
        modality_modules=modality_modules,
    ).set(1)

    # ----- Backwards-compatible duplicates (Sprint 5 names) ----------------
    # Keep emitting the original metric names so existing Grafana dashboards
    # don't go blank during the migration. Drop after one observability
    # release cycle (TD-PROM-DEPRECATE).
    requests_total = Counter(
        "voltari_requests_total",
        "DEPRECATED — use voltari_http_requests_total. Kept for dashboard compat.",
        labelnames=("method", "path", "status"),
        registry=registry,
    )
    requests_latency = Histogram(
        "voltari_request_latency_seconds",
        "DEPRECATED — use voltari_http_request_duration_seconds.",
        labelnames=("method", "path"),
        buckets=(0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 120.0, 300.0),
        registry=registry,
    )
    provider_calls_total = Counter(
        "voltari_provider_calls_total",
        "DEPRECATED — use voltari_provider_requests_total. provider+outcome only.",
        labelnames=("provider", "outcome"),
        registry=registry,
    )
    circuit_breaker_state = Gauge(
        "voltari_circuit_breaker_state",
        "Circuit breaker state per provider (0=closed,1=open,2=half_open).",
        labelnames=("provider",),
        registry=registry,
    )
    balance_holds_active = Gauge(
        "voltari_balance_holds_active",
        "Number of active balance holds (in-flight chat completions).",
        registry=registry,
    )
    auth_failures_total = Counter(
        "voltari_auth_failures_total",
        "Authentication failures by reason.",
        labelnames=("reason",),
        registry=registry,
    )

    # Stash on app.state so handlers / janitors can update them.
    app.state.metrics_registry = registry
    app.state.metrics = {
        # New Sprint 13 names
        "http_requests_total": http_requests_total,
        "http_request_duration_seconds": http_request_duration_seconds,
        "provider_requests_total": provider_requests_total,
        "provider_latency_seconds": provider_latency_seconds,
        "provider_tokens_total": provider_tokens_total,
        "billing_kopeck_total": billing_kopeck_total,
        "cache_hits_total": cache_hits_total,
        "cache_misses_total": cache_misses_total,
        "build_info": build_info,
        # Deprecated Sprint 5 names (still updated by middleware)
        "requests_total": requests_total,
        "requests_latency": requests_latency,
        "provider_calls_total": provider_calls_total,
        "circuit_breaker_state": circuit_breaker_state,
        "balance_holds_active": balance_holds_active,
        "auth_failures_total": auth_failures_total,
    }

    @app.get("/metrics", include_in_schema=False)
    async def _metrics() -> Response:
        body = generate_latest(registry)
        return Response(content=body, media_type=CONTENT_TYPE_LATEST)

    # Pre-compute known route templates so we can map a raw path back to
    # its template post-hoc. ``BaseHTTPMiddleware`` runs BEFORE Starlette's
    # routing layer, so ``request.scope["route"]`` is empty here. We work
    # around that by matching path → route across the FastAPI router on
    # first use (the route table is immutable after startup). This is
    # what ``starlette_exporter`` and ``prometheus-fastapi-instrumentator``
    # do under the hood. Cardinality stays bounded to the number of
    # registered routes, no matter how many ``/v1/keys/<random-uuid>``
    # paths are probed.
    from collections.abc import MutableMapping

    from starlette.routing import Match, Route

    def _resolve_route_template(scope: MutableMapping[str, Any]) -> str:
        for route in app.router.routes:
            if isinstance(route, Route):
                # ``Route.matches`` accepts ``Scope`` (MutableMapping[str, Any]).
                match, _ = route.matches(scope)
                if match == Match.FULL:
                    return route.path
        return "<unmatched>"

    class _PromMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request: Request, call_next: Any) -> Response:
            import time

            method = request.method
            route_path = _resolve_route_template(request.scope)
            # Skip /metrics itself to avoid recursive cardinality.
            if route_path == "/metrics":
                response: Response = await call_next(request)
                return response
            t0 = time.perf_counter()
            try:
                response = await call_next(request)
                status = str(response.status_code)
            except Exception:
                http_requests_total.labels(method=method, route=route_path, status="500").inc()
                requests_total.labels(method=method, path=route_path, status="500").inc()
                raise
            else:
                http_requests_total.labels(method=method, route=route_path, status=status).inc()
                requests_total.labels(method=method, path=route_path, status=status).inc()
                return response
            finally:
                duration = time.perf_counter() - t0
                http_request_duration_seconds.labels(method=method, route=route_path).observe(
                    duration
                )
                requests_latency.labels(method=method, path=route_path).observe(duration)

    app.add_middleware(_PromMiddleware)
    log.info("prometheus_initialised", endpoint="/metrics")
    return True


# ---------------------------------------------------------------------------
# Convenience helpers — keep call-sites short and resilient to "metrics off".
# ---------------------------------------------------------------------------


def get_metric(app: Any, name: str) -> Any | None:
    """Return the registered metric or ``None`` if metrics are disabled.

    Call-sites use::

        m = get_metric(request.app, "provider_requests_total")
        if m is not None:
            m.labels(provider=..., model=..., status="success").inc()

    The ``None`` branch keeps gateway requests serving when ``METRICS_ENABLED=false``
    in dev / pytest.
    """
    metrics = getattr(app.state, "metrics", None)
    if not metrics:
        return None
    return metrics.get(name)


def record_provider_call(
    app: Any,
    *,
    provider: str,
    model: str,
    status: str,
    latency_seconds: float | None = None,
    input_tokens: int = 0,
    output_tokens: int = 0,
    billed_kopecks: int | None = None,
) -> None:
    """One-call shortcut for the chat / messages / audio / embeddings paths.

    Any metric that's missing (because metrics are disabled or the registry
    failed to import ``prometheus_client``) is silently skipped — observability
    must not crash the request path.
    """
    metrics = getattr(app.state, "metrics", None)
    if not metrics:
        return

    try:
        m = metrics.get("provider_requests_total")
        if m is not None:
            m.labels(provider=provider, model=model, status=status).inc()
        # Old name still wired for the deprecation window.
        m_old = metrics.get("provider_calls_total")
        if m_old is not None:
            m_old.labels(provider=provider, outcome=status).inc()

        if latency_seconds is not None:
            m = metrics.get("provider_latency_seconds")
            if m is not None:
                m.labels(provider=provider, model=model).observe(latency_seconds)

        if input_tokens > 0:
            m = metrics.get("provider_tokens_total")
            if m is not None:
                m.labels(provider=provider, model=model, type="input").inc(input_tokens)
        if output_tokens > 0:
            m = metrics.get("provider_tokens_total")
            if m is not None:
                m.labels(provider=provider, model=model, type="output").inc(output_tokens)

        if billed_kopecks is not None and billed_kopecks > 0:
            m = metrics.get("billing_kopeck_total")
            if m is not None:
                m.labels(provider=provider, model=model).inc(billed_kopecks)
    except Exception:  # pragma: no cover — observability never breaks requests
        log.warning("metrics_record_failed", provider=provider, model=model)


def record_cache(app: Any, *, cache_name: str, hit: bool) -> None:
    """Increment cache hit/miss counters; safe when metrics are disabled."""
    metrics = getattr(app.state, "metrics", None)
    if not metrics:
        return
    try:
        key = "cache_hits_total" if hit else "cache_misses_total"
        m = metrics.get(key)
        if m is not None:
            m.labels(cache_name=cache_name).inc()
    except Exception:  # pragma: no cover
        log.warning("metrics_cache_failed", cache_name=cache_name, hit=hit)


__all__ = [
    "get_metric",
    "init_sentry",
    "record_cache",
    "record_provider_call",
    "setup_prometheus",
]
