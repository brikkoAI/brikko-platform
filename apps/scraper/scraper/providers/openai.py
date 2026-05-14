"""OpenAI platform.openai.com billing scraper.

TODO(CEO): Verify against live dashboard. As of 2025-2026 the credit-balance
widget on ``/account/billing/overview`` is rendered server-side React; the
heading "Credit balance" is a stable text anchor while the dollar value
sits in a sibling element.  We use a relatively forgiving locator chain
that falls back to scraping the entire billing page body for a "$X.XX"
pattern if the structured locator misses.

Selectors are educated guesses — if the page renders an entirely
different layout for your account type (org vs personal), update the
selector and redeploy.

Cookies expected: ``__Host-next-auth.session-token`` (and friends).
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

BILLING_URL = "https://platform.openai.com/settings/organization/billing/overview"

# Selectors tried in order — first hit wins.  Each is a *guess* and will
# likely drift; the loose page-text fallback below is the safety net.
_BALANCE_SELECTORS: tuple[str, ...] = (
    # 2025 layout: heading "Credit balance" then a $X.XX sibling div.
    "div:has(> :text-is('Credit balance')) >> .. >> css=div",
    # Pre-2025 layout: "Available balance" tile.
    "[data-testid='available-credit']",
    # Generic: any element with text starting with $ near a "balance" header.
    "section:has-text('Credit') :text-matches('^\\$')",
)


async def scrape(page: Page) -> ScrapeResult:
    try:
        await page.goto(BILLING_URL, wait_until="domcontentloaded")
        # SPA renders the credit balance async — wait for $X.XX to actually appear.
        await page.wait_for_function(
            "() => /\\$\\d+\\.\\d{2}/.test(document.body.innerText)",
            timeout=15_000,
        )
    except PlaywrightTimeoutError as exc:
        raise DomChangedError(f"openai billing page load timeout: {exc}") from exc

    # If we landed on /auth/login or saw an OpenAI login redirect — cookies
    # are dead.  OpenAI uses ``platform.openai.com/login`` on session expiry.
    current_url = page.url
    if "/login" in current_url or "/auth/" in current_url:
        raise CookieExpiredError(f"redirected to login URL {current_url!r}")

    # Try structured selectors first.
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

    # Loose fallback — slurp the visible page text and regex-hunt.
    body_text = await page.locator("body").inner_text()
    return parse_balance_text(body_text, default_currency="USD")
