"""Unit tests for ScrapeRemoteAdapter.

Mocks the brikko-scraper HTTP service via respx.  All variants:
* 200 happy path → ScrapeSnapshot ok, fetch_method='scrape'
* 503 cookie_expired → error snapshot
* 503 dom_changed → error snapshot
* 404 → unknown_provider_at_scraper
* 401 → scraper_auth_failed
* network error → scraper_unreachable
* invalid JSON → scraper_invalid_json
* missing balance_native field → missing_balance_native_in_scraper_response
"""

from __future__ import annotations

from decimal import Decimal

import httpx
import pytest
import respx

from voltari_gateway.balance_monitor.adapters import ScrapeRemoteAdapter

SCRAPER_URL = "http://brikko-scraper:9100"
TOKEN = "test-internal-token-32-bytes-min"


def _make(provider: str = "openai") -> ScrapeRemoteAdapter:
    return ScrapeRemoteAdapter(
        provider=provider,
        scraper_url=SCRAPER_URL,
        internal_token=TOKEN,
        timeout_seconds=5.0,
    )


@pytest.mark.asyncio
async def test_scrape_remote_happy_path() -> None:
    adapter = _make("openai")
    try:
        with respx.mock(assert_all_called=True) as mock:
            mock.post(f"{SCRAPER_URL}/scrape/openai").respond(
                200,
                json={
                    "provider": "openai",
                    "balance_native": "42.50",
                    "currency": "USD",
                    "raw": "$42.50",
                    "fetched_at": "2026-05-11T12:34:56Z",
                },
            )
            snap = await adapter.fetch()
        assert snap.error is None
        assert snap.provider == "openai"
        assert snap.balance_native == Decimal("42.50")
        assert snap.currency == "USD"
        assert snap.fetch_method == "scrape"
        assert snap.raw is not None
    finally:
        await adapter.aclose()


@pytest.mark.asyncio
async def test_scrape_remote_cookie_expired() -> None:
    adapter = _make("openai")
    try:
        with respx.mock() as mock:
            mock.post(f"{SCRAPER_URL}/scrape/openai").respond(
                503,
                json={"error": {"status": 503, "detail": "cookie expired or DOM changed"}},
            )
            snap = await adapter.fetch()
        assert snap.balance_native is None
        assert snap.error is not None
        assert "scrape_unavailable" in snap.error
        assert "cookie expired" in snap.error
        assert snap.fetch_method == "scrape"
    finally:
        await adapter.aclose()


@pytest.mark.asyncio
async def test_scrape_remote_dom_changed() -> None:
    adapter = _make("anthropic")
    try:
        with respx.mock() as mock:
            mock.post(f"{SCRAPER_URL}/scrape/anthropic").respond(
                503,
                json={"error": {"status": 503, "detail": "dom_changed: no balance element"}},
            )
            snap = await adapter.fetch()
        assert snap.balance_native is None
        assert "dom_changed" in (snap.error or "")
        assert snap.fetch_method == "scrape"
    finally:
        await adapter.aclose()


@pytest.mark.asyncio
async def test_scrape_remote_503_non_json_body() -> None:
    """503 with a non-JSON body still produces a clean error snapshot."""
    adapter = _make("openai")
    try:
        with respx.mock() as mock:
            mock.post(f"{SCRAPER_URL}/scrape/openai").respond(503, text="<html>nginx 503</html>")
            snap = await adapter.fetch()
        assert snap.balance_native is None
        assert snap.error is not None
        assert snap.error.startswith("scrape_unavailable")
    finally:
        await adapter.aclose()


@pytest.mark.asyncio
async def test_scrape_remote_404() -> None:
    adapter = _make("openai")
    try:
        with respx.mock() as mock:
            mock.post(f"{SCRAPER_URL}/scrape/openai").respond(
                404, json={"error": {"status": 404, "detail": "unknown provider"}}
            )
            snap = await adapter.fetch()
        assert snap.balance_native is None
        assert "unknown_provider_at_scraper" in (snap.error or "")
    finally:
        await adapter.aclose()


@pytest.mark.asyncio
async def test_scrape_remote_401() -> None:
    adapter = _make("openai")
    try:
        with respx.mock() as mock:
            mock.post(f"{SCRAPER_URL}/scrape/openai").respond(401, text="unauthorized")
            snap = await adapter.fetch()
        assert snap.balance_native is None
        assert "scraper_auth_failed" in (snap.error or "")
    finally:
        await adapter.aclose()


@pytest.mark.asyncio
async def test_scrape_remote_network_error() -> None:
    adapter = _make("openai")
    try:
        with respx.mock() as mock:
            mock.post(f"{SCRAPER_URL}/scrape/openai").mock(
                side_effect=httpx.ConnectError("dns_fail")
            )
            snap = await adapter.fetch()
        assert snap.balance_native is None
        assert snap.error is not None
        assert snap.error.startswith("scraper_unreachable")
        assert snap.fetch_method == "scrape"
    finally:
        await adapter.aclose()


@pytest.mark.asyncio
async def test_scrape_remote_timeout() -> None:
    adapter = _make("openai")
    try:
        with respx.mock() as mock:
            mock.post(f"{SCRAPER_URL}/scrape/openai").mock(
                side_effect=httpx.TimeoutException("read timeout")
            )
            snap = await adapter.fetch()
        assert snap.balance_native is None
        assert snap.error is not None
        assert snap.error.startswith("scraper_unreachable_timeout")
    finally:
        await adapter.aclose()


@pytest.mark.asyncio
async def test_scrape_remote_invalid_json() -> None:
    adapter = _make("openai")
    try:
        with respx.mock() as mock:
            mock.post(f"{SCRAPER_URL}/scrape/openai").respond(200, text="not json")
            snap = await adapter.fetch()
        assert snap.balance_native is None
        assert snap.error is not None
        assert "scraper_invalid_json" in snap.error
    finally:
        await adapter.aclose()


@pytest.mark.asyncio
async def test_scrape_remote_missing_balance_native() -> None:
    adapter = _make("openai")
    try:
        with respx.mock() as mock:
            mock.post(f"{SCRAPER_URL}/scrape/openai").respond(
                200, json={"provider": "openai", "currency": "USD"}
            )
            snap = await adapter.fetch()
        assert snap.balance_native is None
        assert snap.error == "missing_balance_native_in_scraper_response"
        assert snap.raw is not None
    finally:
        await adapter.aclose()


@pytest.mark.asyncio
async def test_scrape_remote_invalid_balance_value() -> None:
    adapter = _make("openai")
    try:
        with respx.mock() as mock:
            mock.post(f"{SCRAPER_URL}/scrape/openai").respond(
                200, json={"balance_native": "not-a-number", "currency": "USD"}
            )
            snap = await adapter.fetch()
        assert snap.balance_native is None
        assert snap.error is not None and "invalid_balance_value" in snap.error
    finally:
        await adapter.aclose()


def test_scrape_remote_rejects_empty_config() -> None:
    with pytest.raises(ValueError):
        ScrapeRemoteAdapter(provider="", scraper_url=SCRAPER_URL, internal_token=TOKEN)
    with pytest.raises(ValueError):
        ScrapeRemoteAdapter(provider="openai", scraper_url="", internal_token=TOKEN)
    with pytest.raises(ValueError):
        ScrapeRemoteAdapter(provider="openai", scraper_url=SCRAPER_URL, internal_token="")
