"""Scrape-remote balance adapter — proxies fetch() to the brikko-scraper service.

Sprint 14 Phase 2 (2026-05-11) — OpenAI / Anthropic / Together don't expose
public balance APIs.  Phase 1 used ``ManualAdapter`` (CEO records by hand).
Phase 2 wires a separate ``brikko-scraper`` container (Playwright) that
logs in via stored cookies and parses the dashboard.

This adapter is the **gateway-side** thin HTTP client.  It POSTs to
``{SCRAPER_URL}/scrape/{provider}`` with ``X-Internal-Token`` and converts
the JSON response into ``BalanceSnapshot``.

Error mapping (scraper → snapshot.error):
* 401 → ``scraper_auth_failed``   — internal token mismatch (deploy bug).
* 404 → ``unknown_provider``       — scraper doesn't know this provider.
* 503 → ``cookie_expired`` / ``dom_changed`` / etc. — scraper-side message.
* 5xx other → ``scraper_5xx_{code}``.
* timeout/network → ``scraper_unreachable``.
"""

from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

from voltari_gateway.balance_monitor.adapters.base import (
    BalanceAdapter,
    BalanceSnapshot,
)
from voltari_gateway.utils.logging import get_logger

log = get_logger(__name__)


class ScrapeRemoteAdapter(BalanceAdapter):
    """Calls the brikko-scraper service for one provider.

    The scraper itself does the Playwright work; we just turn its response
    into the unified ``BalanceSnapshot`` shape so the rest of
    ``balance_monitor.service`` doesn't need to care.

    ``provider_name`` is set by the constructor — same instance class is
    used for OpenAI / Anthropic / Together with different provider strings.
    """

    is_remote = True

    def __init__(
        self,
        *,
        provider: str,
        scraper_url: str,
        internal_token: str,
        timeout_seconds: float = 45.0,
    ) -> None:
        if not provider:
            raise ValueError("provider required")
        if not scraper_url:
            raise ValueError("scraper_url required")
        if not internal_token:
            raise ValueError("internal_token required")
        self.provider_name = provider
        self._url = scraper_url.rstrip("/") + f"/scrape/{provider}"
        self._token = internal_token
        # Scraper does Playwright work — 45 s default to cover slow dashboard
        # loads.  Connect-timeout stays short (5 s) so we fail fast on the
        # scraper container being down.
        self._http = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout_seconds, connect=5.0),
            trust_env=False,
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def fetch(self) -> BalanceSnapshot:
        try:
            resp = await self._http.post(
                self._url,
                headers={
                    "X-Internal-Token": self._token,
                    "Accept": "application/json",
                },
            )
        except httpx.TimeoutException as exc:
            return BalanceSnapshot.error_for(
                self.provider_name,
                f"scraper_unreachable_timeout: {exc}",
                fetch_method="scrape",
            )
        except httpx.HTTPError as exc:
            return BalanceSnapshot.error_for(
                self.provider_name,
                f"scraper_unreachable: {exc}",
                fetch_method="scrape",
            )

        if resp.status_code == 401:
            return BalanceSnapshot.error_for(
                self.provider_name,
                "scraper_auth_failed: internal token mismatch",
                fetch_method="scrape",
            )
        if resp.status_code == 404:
            return BalanceSnapshot.error_for(
                self.provider_name,
                f"unknown_provider_at_scraper: {resp.text[:200]}",
                fetch_method="scrape",
            )
        if resp.status_code == 503:
            # Scraper-reported soft failure: cookie_expired / dom_changed /
            # cookies not uploaded.  Pass the detail through.
            try:
                body = resp.json()
                detail = body.get("error", {}).get("detail") or "unavailable"
            except (json.JSONDecodeError, ValueError):
                detail = "unavailable"
            return BalanceSnapshot.error_for(
                self.provider_name,
                f"scrape_unavailable: {detail}",
                fetch_method="scrape",
            )
        if resp.status_code >= 400:
            return BalanceSnapshot.error_for(
                self.provider_name,
                f"scraper_5xx_{resp.status_code}: {resp.text[:200]}",
                fetch_method="scrape",
            )

        try:
            data: dict[str, Any] = resp.json()
        except (json.JSONDecodeError, ValueError) as exc:
            return BalanceSnapshot.error_for(
                self.provider_name,
                f"scraper_invalid_json: {exc}",
                fetch_method="scrape",
            )

        # Expected shape from scraper:
        # {
        #   "provider": "openai",
        #   "balance_native": "42.50",
        #   "currency": "USD",
        #   "raw": "$42.50",
        #   "fetched_at": "2026-05-11T12:34:56Z"
        # }
        raw_amount = data.get("balance_native")
        if raw_amount is None:
            return BalanceSnapshot(
                provider=self.provider_name,
                balance_native=None,
                currency=None,
                fetch_method="scrape",
                raw=data,
                error="missing_balance_native_in_scraper_response",
            )
        try:
            balance = Decimal(str(raw_amount))
        except (InvalidOperation, ValueError):
            return BalanceSnapshot(
                provider=self.provider_name,
                balance_native=None,
                currency=str(data.get("currency") or "USD").upper(),
                fetch_method="scrape",
                raw=data,
                error=f"invalid_balance_value: {raw_amount!r}",
            )

        currency = str(data.get("currency") or "USD").upper()
        return BalanceSnapshot(
            provider=self.provider_name,
            balance_native=balance,
            currency=currency,
            fetch_method="scrape",
            raw=data,
            error=None,
        )
