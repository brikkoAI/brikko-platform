"""Tests for build_adapters() Phase 2 wiring (SCRAPER_URL flag).

Verifies that:
* Without SCRAPER_URL → openai/anthropic/together still get ManualAdapter
  (Phase 1 behaviour preserved).
* With SCRAPER_URL + SCRAPER_INTERNAL_TOKEN → those three get
  ScrapeRemoteAdapter; deepseek/sber/moonshot retain their API adapters
  when keys are set; the rest remain ManualAdapter.
* MINIMAX_API_KEY / ZHIPU_API_KEY (Sprint 14.2, 2026-05-12) — when set,
  produce MiniMaxBalanceAdapter / ZhipuBalanceAdapter; empty → ManualAdapter.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from pydantic import SecretStr

from voltari_gateway.balance_monitor.adapters import (
    ManualAdapter,
    MiniMaxBalanceAdapter,
    ScrapeRemoteAdapter,
    ZhipuBalanceAdapter,
)
from voltari_gateway.balance_monitor.service import (
    API_ADAPTER_PROVIDERS,
    KNOWN_PROVIDERS,
    SCRAPE_ADAPTER_PROVIDERS,
    build_adapters,
)
from voltari_gateway.config import get_settings


def _patch_setting(name: str, value):
    settings = get_settings()
    return patch.object(settings, name, value)


@pytest.mark.asyncio
async def test_build_adapters_phase1_default() -> None:
    """When SCRAPER_URL is empty (default), Phase 1 behaviour is preserved."""
    settings = get_settings()
    with (
        _patch_setting("scraper_url", ""),
        _patch_setting("scraper_internal_token", SecretStr("")),
    ):
        adapters = build_adapters(settings)
    try:
        # All three "scrape providers" fall back to ManualAdapter.
        for provider in SCRAPE_ADAPTER_PROVIDERS:
            assert provider in adapters
            assert isinstance(adapters[provider], ManualAdapter)
        # Still 10 entries total.
        assert set(adapters.keys()) == set(KNOWN_PROVIDERS)
    finally:
        for a in adapters.values():
            await a.aclose()


@pytest.mark.asyncio
async def test_build_adapters_phase2_wires_scrape_adapters() -> None:
    settings = get_settings()
    with (
        _patch_setting("scraper_url", "http://brikko-scraper:9100"),
        _patch_setting(
            "scraper_internal_token",
            SecretStr("test-internal-token-32-bytes-min"),
        ),
    ):
        adapters = build_adapters(settings)
    try:
        for provider in SCRAPE_ADAPTER_PROVIDERS:
            assert provider in adapters
            assert isinstance(adapters[provider], ScrapeRemoteAdapter), (
                f"{provider} should be ScrapeRemoteAdapter, got {type(adapters[provider])}"
            )
            assert adapters[provider].is_remote is True
            assert adapters[provider].provider_name == provider
        # Non-scrape providers still default ManualAdapter (or API if keys
        # were configured; in the test env they aren't).
        assert isinstance(adapters["yandex"], ManualAdapter)
        assert isinstance(adapters["google"], ManualAdapter)
    finally:
        for a in adapters.values():
            await a.aclose()


@pytest.mark.asyncio
async def test_build_adapters_minimax_wired_when_key_set() -> None:
    """MINIMAX_API_KEY non-empty → MiniMaxBalanceAdapter takes the slot."""
    settings = get_settings()
    with (
        _patch_setting("minimax_api_key", SecretStr("sk-mm-test")),
        _patch_setting("zhipu_api_key", SecretStr("")),
    ):
        adapters = build_adapters(settings)
    try:
        assert isinstance(adapters["minimax"], MiniMaxBalanceAdapter)
        assert adapters["minimax"].is_remote is True
        assert adapters["minimax"].provider_name == "minimax"
        # Zhipu (empty) falls back to manual.
        assert isinstance(adapters["zhipu"], ManualAdapter)
    finally:
        for a in adapters.values():
            await a.aclose()


@pytest.mark.asyncio
async def test_build_adapters_zhipu_wired_when_key_set() -> None:
    """ZHIPU_API_KEY non-empty (in '<id>.<secret>' form) → ZhipuBalanceAdapter."""
    settings = get_settings()
    with (
        _patch_setting("zhipu_api_key", SecretStr("12345abc.secrethalf")),
        _patch_setting("minimax_api_key", SecretStr("")),
    ):
        adapters = build_adapters(settings)
    try:
        assert isinstance(adapters["zhipu"], ZhipuBalanceAdapter)
        assert adapters["zhipu"].is_remote is True
        assert adapters["zhipu"].provider_name == "zhipu"
        # MiniMax (empty) falls back to manual.
        assert isinstance(adapters["minimax"], ManualAdapter)
    finally:
        for a in adapters.values():
            await a.aclose()


@pytest.mark.asyncio
async def test_build_adapters_minimax_zhipu_empty_keys_default_manual() -> None:
    """Both keys empty → ManualAdapter for both, no boot-time errors."""
    settings = get_settings()
    with (
        _patch_setting("minimax_api_key", SecretStr("")),
        _patch_setting("zhipu_api_key", SecretStr("")),
    ):
        adapters = build_adapters(settings)
    try:
        assert isinstance(adapters["minimax"], ManualAdapter)
        assert isinstance(adapters["zhipu"], ManualAdapter)
    finally:
        for a in adapters.values():
            await a.aclose()


def test_api_adapter_providers_includes_minimax_and_zhipu() -> None:
    """Frozenset must reflect the new API-adapter providers (Sprint 14.2)."""
    assert "minimax" in API_ADAPTER_PROVIDERS
    assert "zhipu" in API_ADAPTER_PROVIDERS
    # Sanity — the original three still here.
    assert {"deepseek", "sber", "moonshot"}.issubset(API_ADAPTER_PROVIDERS)


@pytest.mark.asyncio
async def test_build_adapters_scraper_token_missing_disables() -> None:
    """SCRAPER_URL without SCRAPER_INTERNAL_TOKEN must NOT enable scrape adapters.

    Half-configuration is a deploy mistake; we degrade to Phase 1 manual
    rather than ship a guaranteed-401 scraper.
    """
    settings = get_settings()
    with (
        _patch_setting("scraper_url", "http://brikko-scraper:9100"),
        _patch_setting("scraper_internal_token", SecretStr("")),
    ):
        adapters = build_adapters(settings)
    try:
        for provider in SCRAPE_ADAPTER_PROVIDERS:
            assert isinstance(adapters[provider], ManualAdapter), (
                f"{provider} should fall back to Manual when token missing"
            )
    finally:
        for a in adapters.values():
            await a.aclose()
