"""Shared types + helpers for per-provider Playwright scrapers."""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation


class ScrapeError(Exception):
    """Base for scrape-time failures."""


class CookieExpiredError(ScrapeError):
    """Provider redirected us to a login page → cookies are stale."""


class DomChangedError(ScrapeError):
    """The DOM selector we expected isn't present — UI shipped a change."""


@dataclass(frozen=True)
class ScrapeResult:
    """One successful balance scrape."""

    balance_native: Decimal
    currency: str
    raw_text: str


# Matches "$X.XX", "X.XX USD", "USD X.XX", "X.XX", with optional spaces / commas
# inside the number ("$1,234.56" → 1234.56).  Currency code restricted to
# the four we care about (USD/EUR/CNY/RUB) plus the $ symbol.
_AMOUNT_PATTERNS: tuple[re.Pattern[str], ...] = (
    # "$ 12.34" or "$12.34"
    re.compile(
        r"\$\s*(?P<amount>[\d,]+(?:\.\d{1,4})?)\b",
        re.IGNORECASE,
    ),
    # "12.34 USD"
    re.compile(
        r"(?P<amount>[\d,]+(?:\.\d{1,4})?)\s*(?P<cur>USD|EUR|CNY|RUB)\b",
        re.IGNORECASE,
    ),
    # "USD 12.34"
    re.compile(
        r"\b(?P<cur>USD|EUR|CNY|RUB)\s*(?P<amount>[\d,]+(?:\.\d{1,4})?)\b",
        re.IGNORECASE,
    ),
)


def parse_balance_text(text: str, *, default_currency: str = "USD") -> ScrapeResult:
    """Best-effort parse of a "$X.YZ" / "X.YZ USD" string.

    Raises ``DomChangedError`` if no recognisable amount is present — that
    means the selector returned text but it's not the balance string we
    expected, which itself signals the DOM has moved.
    """
    if not text:
        raise DomChangedError("empty balance text from page")

    for pattern in _AMOUNT_PATTERNS:
        match = pattern.search(text)
        if match is None:
            continue
        raw_amount = match.group("amount").replace(",", "")
        try:
            amount = Decimal(raw_amount)
        except (InvalidOperation, ValueError) as exc:
            raise DomChangedError(f"unparseable amount {raw_amount!r}: {exc}") from exc
        currency = match.groupdict().get("cur") or default_currency
        return ScrapeResult(
            balance_native=amount,
            currency=currency.upper(),
            raw_text=text.strip()[:200],
        )

    raise DomChangedError(f"no currency amount found in text: {text[:100]!r}")
