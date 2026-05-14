"""Smoke tests for the Prometheus /metrics endpoint (Sprint 13).

The endpoint and the request-instrumentation middleware both come from
``voltari_gateway.utils.observability.setup_prometheus``. ``api/metrics.py``
is a thin re-export router that reads from ``app.state.metrics_registry`` —
both should respond with the same exposition body.

Tests intentionally hit `/healthz` rather than `/v1/chat/completions` to
avoid pulling in the chat fixtures (provider stubs, hold engine, etc.) —
the goal here is endpoint hygiene, not chat behaviour.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient


@pytest_asyncio.fixture(scope="function")
async def metrics_client() -> AsyncIterator[AsyncClient]:
    """Boot a fresh app with METRICS_ENABLED=true.

    Local import so the fixture only fires for tests that actually need
    the metrics endpoint — rest of the suite sees the default (off) state
    via the session conftest.
    """
    prev = os.environ.get("METRICS_ENABLED")
    os.environ["METRICS_ENABLED"] = "true"
    try:
        # Bust the lru_cache on get_settings so the new env var is picked up.
        from voltari_gateway.config import get_settings

        get_settings.cache_clear()
        from voltari_gateway.main import create_app

        application = create_app()
        transport = ASGITransport(app=application)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac
    finally:
        if prev is None:
            os.environ.pop("METRICS_ENABLED", None)
        else:
            os.environ["METRICS_ENABLED"] = prev
        from voltari_gateway.config import get_settings

        get_settings.cache_clear()


@pytest.mark.asyncio
async def test_metrics_endpoint_responds_with_prometheus_format(
    metrics_client: AsyncClient,
) -> None:
    """GET /metrics → 200 + Prometheus exposition Content-Type."""
    resp = await metrics_client.get("/metrics")
    assert resp.status_code == 200, resp.text
    # prometheus_client emits ``text/plain; version=0.0.4; charset=utf-8``
    # on the legacy exposition format and ``version=1.0.0`` on the
    # OpenMetrics path. Both are valid Prometheus scrape targets — accept
    # either rather than pinning the test to a specific client release.
    ct = resp.headers.get("content-type", "")
    assert "text/plain" in ct, ct
    assert "version=" in ct, ct

    body = resp.text
    # Sanity — the build_info gauge is set at registry creation, so it
    # must be present on the first scrape.
    assert "voltari_build_info" in body, body[:500]
    # Sprint 13 contract metrics must all be defined (Counter without samples
    # still emits a # HELP / # TYPE block).
    for name in [
        "voltari_http_requests_total",
        "voltari_http_request_duration_seconds",
        "voltari_provider_requests_total",
        "voltari_provider_latency_seconds",
        "voltari_provider_tokens_total",
        "voltari_billing_kopeck_total",
        "voltari_cache_hits_total",
        "voltari_cache_misses_total",
    ]:
        assert f"# HELP {name}" in body or f"# TYPE {name}" in body, (
            f"{name} missing from /metrics body"
        )


@pytest.mark.asyncio
async def test_http_requests_counter_increments(metrics_client: AsyncClient) -> None:
    """N hits on /healthz → counter for /healthz route increases by N."""
    # Warm-up scrape — captures the baseline (other tests may have hit
    # /healthz already through the same registry).
    body0 = (await metrics_client.get("/metrics")).text
    baseline = _count_healthz_200(body0)

    n = 5
    for _ in range(n):
        r = await metrics_client.get("/healthz")
        assert r.status_code == 200, r.text

    body1 = (await metrics_client.get("/metrics")).text
    final = _count_healthz_200(body1)
    delta = final - baseline
    # ``/metrics`` itself is excluded from instrumentation, so the delta
    # equals exactly the number of healthz hits.
    assert delta == n, (
        f"expected /healthz counter to grow by {n}, got {delta}\n--- body ---\n{body1[:2000]}"
    )


@pytest.mark.asyncio
async def test_request_duration_histogram_buckets(metrics_client: AsyncClient) -> None:
    """Histogram exposes the agreed buckets and accumulates observations."""
    await metrics_client.get("/healthz")
    body = (await metrics_client.get("/metrics")).text

    # Required bucket boundaries from the Sprint 13 contract.
    # prometheus_client renders integral floats as "1.0" / "5.0", but
    # 0.05 / 0.25 / 2.5 stay as-is — match against both the integer
    # and the ".0" suffixed form so the assertion stays robust against
    # client-library formatting changes.
    expected_buckets = ["0.05", "0.1", "0.25", "0.5", "1.0", "2.5", "5.0"]
    for le in expected_buckets:
        prefix = f'voltari_http_request_duration_seconds_bucket{{le="{le}"'
        assert prefix in body, f"missing histogram bucket le={le}\n{body[:2000]}"

    # The histogram must have at least one observation (on /healthz at least
    # one bucket count > 0).
    assert "voltari_http_request_duration_seconds_count" in body


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _count_healthz_200(body: str) -> float:
    """Sum the counter samples for the ``/healthz`` 200 series.

    The Prometheus exposition format spells the line as
    ``voltari_http_requests_total{method="GET",route="/healthz",status="200"} 5.0``.
    We parse loosely so label ordering changes don't trip us up.
    """
    total = 0.0
    for line in body.splitlines():
        if not line.startswith("voltari_http_requests_total{"):
            continue
        if 'route="/healthz"' not in line or 'status="200"' not in line:
            continue
        try:
            total += float(line.rsplit(" ", 1)[1])
        except (ValueError, IndexError):
            continue
    return total
