"""Per-provider Playwright scraping implementations.

Each module exposes ``async def scrape(page) -> ScrapeResult`` that takes a
ready Playwright Page (already authenticated via injected cookies + the
provider's billing URL).  Implementations should:

* Wait for a stable DOM element (avoid races with React hydration).
* Extract a string like "$X.XX" / "X.XX USD" / etc.
* Convert to ``Decimal`` and return inside ``ScrapeResult``.
* On selector miss → raise ``DomChangedError`` (mapped to HTTP 503 upstream).
* On obvious auth failure (redirect to /login) → raise ``CookieExpiredError``.
"""

from __future__ import annotations

from scraper.providers.base import (
    CookieExpiredError,
    DomChangedError,
    ScrapeError,
    ScrapeResult,
)

__all__ = [
    "CookieExpiredError",
    "DomChangedError",
    "ScrapeError",
    "ScrapeResult",
]
