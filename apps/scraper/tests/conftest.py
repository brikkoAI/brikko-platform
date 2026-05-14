"""Common fixtures for scraper tests.

We don't spin up a real chromium — Playwright pool is mocked.  Provider-
specific selector tests live in test_parse_balance.py and exercise the
pure-Python parsing layer.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from cryptography.fernet import Fernet
from httpx import ASGITransport, AsyncClient


@pytest.fixture
def fernet_key() -> str:
    return Fernet.generate_key().decode("utf-8")


@pytest.fixture
def internal_token() -> str:
    return "test-internal-token-32-bytes-min-padding-x"


@pytest.fixture
def env_for_scraper(
    tmp_path: Path,
    fernet_key: str,
    internal_token: str,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[Path]:
    """Set the env vars so scraper.config picks them up."""
    cookies_dir = tmp_path / "cookies"
    cookies_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("ENCRYPTION_KEY", fernet_key)
    monkeypatch.setenv("SCRAPER_INTERNAL_TOKEN", internal_token)
    monkeypatch.setenv("COOKIES_VOLUME_PATH", str(cookies_dir))
    monkeypatch.setenv("PLAYWRIGHT_HEADLESS", "true")

    # Reset cache so subsequent get_settings() picks up the new env.
    from scraper.config import get_settings

    get_settings.cache_clear()
    yield cookies_dir
    get_settings.cache_clear()


class _FakePool:
    """Drop-in BrowserPool that doesn't actually start Playwright.

    Tests that exercise /scrape/* monkeypatch the provider scrape_fn to
    skip needing a real Page.  page_with_cookies still validates the
    cookies JSON shape (which we DO want under test).
    """

    def __init__(self) -> None:
        self.started = False
        self.stopped = False

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.stopped = True

    async def page_with_cookies(self, cookies_json: bytes) -> Any:  # pragma: no cover
        # Tests bypass this by mocking scrape_fn directly.  We keep a no-op
        # async context manager shape so accidental real-call surfaces fast.
        import json as _json

        class _Ctx:
            async def __aenter__(self) -> None:
                _json.loads(cookies_json.decode("utf-8"))
                return None

            async def __aexit__(self, *_exc: Any) -> None:
                return None

        return _Ctx()


@pytest_asyncio.fixture()
async def app_client(env_for_scraper: Path) -> AsyncIterator[AsyncClient]:
    """Spin up the FastAPI app with a fake browser pool injected."""
    # Reset config cache first to avoid stale singletons.
    from scraper.config import get_settings

    get_settings.cache_clear()
    settings = get_settings()
    assert settings.internal_token.get_secret_value() == os.environ["SCRAPER_INTERNAL_TOKEN"]

    from scraper.main import create_app

    # Force the lifespan to use our fake pool by monkey-patching the
    # BrowserPool class attribute setter — we can also just bypass the
    # lifespan altogether using ASGITransport without lifespan.  Simplest:
    # build the app, swap out the pool after lifespan startup attempts to
    # create a real one.  We DO bypass lifespan via lifespan="off" by
    # manually constructing app state instead.
    app = create_app()

    # Manually wire app.state without invoking the lifespan (which would
    # try to launch chromium).
    from scraper.config import get_settings as _gs
    from scraper.crypto import CookieCipher
    from scraper.storage import CookieStore

    app.state.settings = _gs()
    app.state.cipher = CookieCipher(app.state.settings.encryption_key.get_secret_value())
    app.state.cookie_store = CookieStore(env_for_scraper)
    app.state.pool = _FakePool()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client
