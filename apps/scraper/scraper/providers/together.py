"""Together.ai api.together.xyz billing scraper.

TODO(CEO): Verify against live dashboard. Together.ai dashboard uses
``api.together.xyz/settings/billing`` (or ``api.together.ai`` depending
on the staging environment); the displayed "Account balance" tile is a
React component with the dollar value as inner text.
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

# Together.ai migrated some accounts from .xyz to .ai in 2025; we try the
# preferred URL first and fall back to the legacy host.  Whichever returns
# 200 + non-login wins.
PRIMARY_BILLING_URL = "https://api.together.xyz/settings/billing"
FALLBACK_BILLING_URL = "https://api.together.ai/settings/billing"

_BALANCE_SELECTORS: tuple[str, ...] = (
    # 2025 layout: a card with "Balance" label.
    "div:has(> :text-is('Balance')) >> css=div:has-text('$')",
    "div:has(> :text-is('Account balance')) >> css=div:has-text('$')",
    "[data-testid='balance-amount']",
)


async def _open_billing(page: Page) -> str:
    """Navigate to either primary or fallback billing URL.

    Returns the URL we actually ended on, raises if both fail.
    """
    last_err: Exception | None = None
    for url in (PRIMARY_BILLING_URL, FALLBACK_BILLING_URL):
        try:
            await page.goto(url, wait_until="domcontentloaded")
            return page.url
        except PlaywrightTimeoutError as exc:
            last_err = exc
            continue
    raise DomChangedError(f"together billing page load timeout: {last_err}")


async def scrape(page: Page) -> ScrapeResult:
    landed = await _open_billing(page)
    if "/login" in landed or "/auth/" in landed:
        raise CookieExpiredError(f"redirected to login URL {landed!r}")
    # SPA — дождаться $X.XX (Together может показывать $0.00).
    try:
        await page.wait_for_function(
            "() => /\\$\\d+\\.\\d{2}/.test(document.body.innerText)",
            timeout=15_000,
        )
    except PlaywrightTimeoutError as exc:
        raise DomChangedError(f"together: $ amount not found within 15s: {exc}") from exc

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
