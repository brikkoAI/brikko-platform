"""Single shared Playwright browser instance + per-scrape context lifecycle.

Why one persistent browser:
* Chromium cold-start is 1-2 s; we don't want to pay it on every /scrape.
* Browser is process-wide; isolation comes from creating a fresh context
  (== fresh cookie jar) per scrape, then closing it.
* Concurrent scrapes use separate contexts so cookies/state don't bleed.

The pool is built lazily at app startup (``startup_browser_pool``) and
closed at shutdown (``shutdown_browser_pool``).
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from playwright.async_api import Browser, BrowserContext, Page, async_playwright

from scraper.config import ScraperSettings


class BrowserPool:
    """Holds a single persistent Browser; mints/disposes contexts on demand."""

    def __init__(self, settings: ScraperSettings) -> None:
        self._settings = settings
        self._playwright: Any | None = None
        self._browser: Browser | None = None

    async def start(self) -> None:
        if self._browser is not None:
            return
        self._playwright = await async_playwright().start()
        launch_kwargs: dict[str, Any] = {
            "headless": self._settings.headless,
            "args": [
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ],
        }
        # Outbound proxy (Aeza tinyproxy) — required для OpenAI/Anthropic/Together,
        # которые блочат РФ-IP на CF-уровне. Если переменная пустая — direct.
        if self._settings.outbound_proxy:
            launch_kwargs["proxy"] = {"server": self._settings.outbound_proxy}
        self._browser = await self._playwright.chromium.launch(**launch_kwargs)

    async def stop(self) -> None:
        if self._browser is not None:
            await self._browser.close()
            self._browser = None
        if self._playwright is not None:
            await self._playwright.stop()
            self._playwright = None

    @asynccontextmanager
    async def page_with_cookies(
        self,
        cookies_json: bytes,
    ) -> AsyncIterator[Page]:
        """Yield a fresh Page with the given cookies injected.

        ``cookies_json`` must be the decrypted JSON payload from a Playwright
        ``context.cookies()`` dump (a list of cookie dicts).  We accept it
        as bytes to avoid intermediate string copies.
        """
        if self._browser is None:
            raise RuntimeError("BrowserPool not started")

        try:
            cookies = json.loads(cookies_json.decode("utf-8"))
            if not isinstance(cookies, list):
                raise ValueError("cookies blob must be a JSON list")
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
            raise ValueError(f"invalid cookies JSON: {exc}") from exc

        context: BrowserContext = await self._browser.new_context(
            viewport={
                "width": self._settings.viewport_width,
                "height": self._settings.viewport_height,
            },
            user_agent=self._settings.user_agent,
            # Russian-ish locale to match the CEO's browser; some dashboards
            # honour Accept-Language for currency display.
            locale="en-US",
            timezone_id="Europe/Moscow",
        )
        context.set_default_navigation_timeout(self._settings.nav_timeout_ms)
        context.set_default_timeout(self._settings.nav_timeout_ms)

        # Normalize sameSite: browser extensions export "no_restriction" /
        # "unspecified" / "lax" / "strict", but Playwright requires exactly
        # "Strict" | "Lax" | "None". Map common variants; drop unknown keys.
        _SAMESITE_MAP = {
            "no_restriction": "None",
            "unspecified": "None",
            "none": "None",
            "lax": "Lax",
            "strict": "Strict",
        }
        for cookie in cookies:
            if "sameSite" in cookie:
                raw = str(cookie["sameSite"]).lower()
                mapped = _SAMESITE_MAP.get(raw)
                if mapped is None:
                    # Unknown value → drop the field entirely (Playwright
                    # treats absent sameSite as "Lax" default).
                    del cookie["sameSite"]
                else:
                    cookie["sameSite"] = mapped
            # Drop browser-extension-specific keys Playwright rejects.
            cookie.pop("hostOnly", None)
            cookie.pop("session", None)
            cookie.pop("storeId", None)
            cookie.pop("id", None)
        try:
            await context.add_cookies(cookies)
        except Exception as exc:
            await context.close()
            raise ValueError(f"failed to inject cookies into context: {exc}") from exc

        page = await context.new_page()
        try:
            yield page
        finally:
            await context.close()
