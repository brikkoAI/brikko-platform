"""Pure-Python tests for the balance-text parser.

These run without Playwright or chromium and cover the wire-format
variants we expect from each provider's dashboard.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from scraper.providers.base import DomChangedError, parse_balance_text


@pytest.mark.parametrize(
    "text, expected_amount, expected_currency",
    [
        ("$50.25", Decimal("50.25"), "USD"),
        ("$ 50.25", Decimal("50.25"), "USD"),
        ("$1,234.56", Decimal("1234.56"), "USD"),
        ("$1000", Decimal("1000"), "USD"),
        ("12.34 USD", Decimal("12.34"), "USD"),
        ("USD 12.34", Decimal("12.34"), "USD"),
        ("Credit balance: $42.00 (renews monthly)", Decimal("42.00"), "USD"),
        ("Account balance\n$5.07", Decimal("5.07"), "USD"),
        # Multi-currency: first match wins.  Together.ai sometimes shows
        # both USD and the chunk price; we want the USD remaining.
        ("Remaining: $87.00 — Plan: $20.00/mo", Decimal("87.00"), "USD"),
    ],
)
def test_parse_balance_variants(
    text: str, expected_amount: Decimal, expected_currency: str
) -> None:
    result = parse_balance_text(text)
    assert result.balance_native == expected_amount
    assert result.currency == expected_currency


@pytest.mark.parametrize(
    "text",
    [
        "",
        "   ",
        "Welcome to OpenAI",
        "Some random text without currency",
    ],
)
def test_parse_balance_misses_raise_dom_changed(text: str) -> None:
    with pytest.raises(DomChangedError):
        parse_balance_text(text)


def test_parse_balance_uses_default_currency_when_only_symbol() -> None:
    result = parse_balance_text("$10.00", default_currency="USD")
    assert result.currency == "USD"
