"""Endpoint-level tests for the scraper FastAPI app.

Browser interaction is mocked — we patch the per-provider scrape entrypoint
so tests run on any machine without chromium installed.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from cryptography.fernet import Fernet
from httpx import AsyncClient

from scraper.providers.base import (
    CookieExpiredError,
    DomChangedError,
    ScrapeResult,
)

# ---------------------------------------------------------------------------
# Healthz
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_healthz_no_auth_required(app_client: AsyncClient) -> None:
    r = await app_client.get("/healthz")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert "version" in body


# ---------------------------------------------------------------------------
# Cookie upload
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cookie_upload_requires_token(app_client: AsyncClient, internal_token: str) -> None:
    r = await app_client.post(
        "/cookies/openai",
        content=b"some-cipher",
    )
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_cookie_upload_rejects_invalid_ciphertext(
    app_client: AsyncClient, internal_token: str
) -> None:
    r = await app_client.post(
        "/cookies/openai",
        content=b"not-fernet",
        headers={"X-Internal-Token": internal_token},
    )
    # Round-trip decrypt failed → 400.
    assert r.status_code == 400
    assert "decrypt" in r.json()["error"]["detail"]


@pytest.mark.asyncio
async def test_cookie_upload_unknown_provider(app_client: AsyncClient, internal_token: str) -> None:
    r = await app_client.post(
        "/cookies/unknown",
        content=b"x",
        headers={"X-Internal-Token": internal_token},
    )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_cookie_upload_too_large(app_client: AsyncClient, internal_token: str) -> None:
    r = await app_client.post(
        "/cookies/openai",
        content=b"x" * 100_000,
        headers={"X-Internal-Token": internal_token},
    )
    assert r.status_code == 413


@pytest.mark.asyncio
async def test_cookie_upload_empty(app_client: AsyncClient, internal_token: str) -> None:
    r = await app_client.post(
        "/cookies/openai",
        content=b"",
        headers={"X-Internal-Token": internal_token},
    )
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_cookie_upload_success(
    app_client: AsyncClient,
    internal_token: str,
    env_for_scraper: Path,
    fernet_key: str,
) -> None:
    cookies_payload = json.dumps(
        [
            {
                "name": "session",
                "value": "abc",
                "domain": "platform.openai.com",
                "path": "/",
            }
        ]
    ).encode("utf-8")
    f = Fernet(fernet_key.encode())
    ciphertext = f.encrypt(cookies_payload)

    r = await app_client.post(
        "/cookies/openai",
        content=ciphertext,
        headers={"X-Internal-Token": internal_token},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["provider"] == "openai"
    assert body["size_bytes"] == len(ciphertext)

    # File on disk.
    assert (env_for_scraper / "openai.enc").is_file()


# ---------------------------------------------------------------------------
# Scrape endpoint
# ---------------------------------------------------------------------------


async def _seed_cookies(
    app_client: AsyncClient,
    internal_token: str,
    fernet_key: str,
    provider: str = "openai",
) -> None:
    cookies_payload = json.dumps(
        [
            {
                "name": "session",
                "value": "abc",
                "domain": "platform.openai.com",
                "path": "/",
            }
        ]
    ).encode("utf-8")
    ciphertext = Fernet(fernet_key.encode()).encrypt(cookies_payload)
    r = await app_client.post(
        f"/cookies/{provider}",
        content=ciphertext,
        headers={"X-Internal-Token": internal_token},
    )
    assert r.status_code == 200, r.text


@pytest.mark.asyncio
async def test_scrape_requires_token(app_client: AsyncClient) -> None:
    r = await app_client.post("/scrape/openai")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_scrape_no_cookies_503(app_client: AsyncClient, internal_token: str) -> None:
    r = await app_client.post(
        "/scrape/openai",
        headers={"X-Internal-Token": internal_token},
    )
    assert r.status_code == 503
    assert "not uploaded" in r.json()["error"]["detail"]


@pytest.mark.asyncio
async def test_scrape_unknown_provider(app_client: AsyncClient, internal_token: str) -> None:
    r = await app_client.post(
        "/scrape/unknown",
        headers={"X-Internal-Token": internal_token},
    )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_scrape_ok_path(
    app_client: AsyncClient,
    internal_token: str,
    fernet_key: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _seed_cookies(app_client, internal_token, fernet_key, "openai")

    async def _fake_scrape(_page: Any) -> ScrapeResult:
        return ScrapeResult(
            balance_native=Decimal("42.50"),
            currency="USD",
            raw_text="$42.50",
        )

    from scraper.main import PROVIDER_SCRAPERS

    monkeypatch.setitem(PROVIDER_SCRAPERS, "openai", _fake_scrape)

    r = await app_client.post(
        "/scrape/openai",
        headers={"X-Internal-Token": internal_token},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["provider"] == "openai"
    assert Decimal(body["balance_native"]) == Decimal("42.50")
    assert body["currency"] == "USD"
    assert body["raw"] == "$42.50"


@pytest.mark.asyncio
async def test_scrape_cookie_expired(
    app_client: AsyncClient,
    internal_token: str,
    fernet_key: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _seed_cookies(app_client, internal_token, fernet_key, "openai")

    async def _expired(_page: Any) -> ScrapeResult:
        raise CookieExpiredError("login redirect")

    from scraper.main import PROVIDER_SCRAPERS

    monkeypatch.setitem(PROVIDER_SCRAPERS, "openai", _expired)

    r = await app_client.post(
        "/scrape/openai",
        headers={"X-Internal-Token": internal_token},
    )
    assert r.status_code == 503
    assert "cookie expired" in r.json()["error"]["detail"]


@pytest.mark.asyncio
async def test_scrape_dom_changed(
    app_client: AsyncClient,
    internal_token: str,
    fernet_key: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _seed_cookies(app_client, internal_token, fernet_key, "openai")

    async def _dom(_page: Any) -> ScrapeResult:
        raise DomChangedError("no balance element")

    from scraper.main import PROVIDER_SCRAPERS

    monkeypatch.setitem(PROVIDER_SCRAPERS, "openai", _dom)

    r = await app_client.post(
        "/scrape/openai",
        headers={"X-Internal-Token": internal_token},
    )
    assert r.status_code == 503
    assert "dom_changed" in r.json()["error"]["detail"]
