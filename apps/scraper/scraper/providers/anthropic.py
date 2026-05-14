"""Anthropic console.anthropic.com billing scraper.

TODO(CEO): Verify against live dashboard. The Anthropic console shows
remaining credits on ``/settings/billing`` under a "Credit balance" heading
(observed in screenshots through 2025-12).

Cookies expected: ``__Secure-next-auth.session-token`` plus the Anthropic
session cookie (varies by region).
"""

from __future__ import annotations

from playwright.async_api import Page
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from scraper.providers.base import (
    CookieExpiredError,
    DomChangedError,
    ScrapeResult,
    parse_balance_text,
)

BILLING_URL = "https://console.anthropic.com/settings/billing"

_BALANCE_SELECTORS: tuple[str, ...] = (
    # Stable text anchor "Credit balance" → sibling with value.
    "div:has(> :text-is('Credit balance')) >> css=div:has-text('$')",
    # Older naming "Remaining credits".
    "div:has(> :text-is('Remaining credits')) >> css=div:has-text('$')",
    # Generic credits widget.
    "[data-testid='credits-balance']",
)


async def scrape(page: Page) -> ScrapeResult:
    try:
        await page.goto(BILLING_URL, wait_until="domcontentloaded")
        # Anthropic redirects console.anthropic.com → platform.claude.com.
        # Wait for the $X.XX text to appear (SPA renders billing async).
        await page.wait_for_function(
            "() => /\\$\\d+\\.\\d{2}/.test(document.body.innerText)",
            timeout=15_000,
        )
    except PlaywrightTimeoutError as exc:
        raise DomChangedError(f"anthropic billing page load timeout: {exc}") from exc

    if "/login" in page.url or page.url.startswith("https://console.anthropic.com/login"):
        raise CookieExpiredError(f"redirected to login URL {page.url!r}")

    for selector in _BALANCE_SELECTORS:
        try:
            locator = page.locator(selector).first
            await locator.wait_for(state="visible", timeout=5_000)
            text = (await locator.inner_text()).strip()
            if text and ("$" in text or "USD" in text.upper()):
                return parse_balance_text(text, default_currency="USD")
        except PlaywrightTimeoutError:
            continue
        except Exception:
            continue

    body_text = await page.locator("body").inner_text()
    return parse_balance_text(body_text, default_currency="USD")
