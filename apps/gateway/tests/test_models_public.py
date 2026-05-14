"""GET /v1/models/public tests (Sprint 10).

Public catalog for the marketing landing page. No auth, RUB-converted
prices, capability flags. Distinct from authenticated /v1/models.

Sprint M3.1 — endpoint now filters by registered providers. Tests that
expect the full catalog use the ``all_providers_registered`` fixture from
this module to register every Provider enum value with the OpenAI stub.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
import pytest_asyncio

from voltari_gateway.router.catalog import (
    PUBLIC_MARKUP,
    USD_RUB,
    Provider,
    list_models,
)


@pytest_asyncio.fixture
async def all_providers_registered(app):
    """Register the OpenAI stub against every Provider enum so the
    /v1/models/public filter surfaces the full catalog. Default conftest
    registers OpenAI only, which would hide every non-OpenAI entry."""
    stub = app.state.openai_provider
    registry = app.state.provider_registry
    for prov in Provider:
        registry.register(prov, stub)
    return registry


@pytest.mark.asyncio
async def test_public_endpoint_no_auth_required(client):
    """Endpoint must answer 200 without a Bearer token."""
    r = await client.get("/v1/models/public")
    assert r.status_code == 200
    body = r.json()
    assert body["object"] == "list"
    assert isinstance(body["data"], list)


@pytest.mark.asyncio
async def test_public_endpoint_returns_full_catalog(all_providers_registered, client):
    r = await client.get("/v1/models/public")
    assert r.status_code == 200
    data = r.json()["data"]
    # The contract guarded here is "the public endpoint exposes every chat
    # model in the catalog with no silent drop-outs". Hard-coding the count
    # makes the test brittle when we add aliases (Sprint M2 added gpt-4o,
    # gpt-4o-mini, gemini-2.5 family; M3.1 added the legacy o1/3.5/1.5 sets).
    # Check parity with list_models() and a sane MVP minimum.
    assert len(data) == len(list_models())
    assert len(data) >= 19  # MVP minimum per BRIEF §4


@pytest.mark.asyncio
async def test_public_endpoint_sets_cache_control_header(client):
    r = await client.get("/v1/models/public")
    assert r.headers["Cache-Control"] == "public, max-age=300"


@pytest.mark.asyncio
async def test_public_pricing_uses_usd_x_80_x_markup(all_providers_registered, client):
    """RUB pricing == official_usd × 80 × 1.15, rounded to nearest ruble.

    Pin a known entry (gpt-5.5 input = $5/M) and verify the formula.
    """
    r = await client.get("/v1/models/public")
    by_id = {m["id"]: m for m in r.json()["data"]}

    gpt55 = by_id["gpt-5.5"]
    # $5 × 80 × 1.15 = 460 RUB/1M.
    expected_input = int((Decimal("5.00") * USD_RUB * PUBLIC_MARKUP).quantize(Decimal("1")))
    assert gpt55["pricing_rub_per_1m"]["input"] == expected_input == 460
    # $30 × 80 × 1.15 = 2760 RUB/1M.
    assert gpt55["pricing_rub_per_1m"]["output"] == 2760
    # Cached input for OpenAI: $0.50 × 80 × 1.15 = 46.
    assert gpt55["pricing_rub_per_1m"]["cached_input"] == 46
    # Official USD echoed back so the lander can show "official price".
    assert gpt55["pricing_usd_per_1m_official"]["input"] == 5.0
    assert gpt55["pricing_usd_per_1m_official"]["output"] == 30.0


@pytest.mark.asyncio
async def test_deprecated_models_share_2026_07_24_sunset(all_providers_registered, client):
    """All DeepSeek pre-V4 entries (v3, v3.2-chat) share the 2026-07-24
    upstream sunset — surface them with the same ``deprecated_at`` so the
    UI can group them in one warning. Updated Sprint M3.1: previously the
    catalog had only deepseek-v3.2-chat; we added the short ``deepseek-v3``
    alias for SDK callers who pinned the pre-3.2 name.
    """
    r = await client.get("/v1/models/public")
    deprecated = {
        m["id"]: m["deprecated_at"] for m in r.json()["data"] if m["deprecated_at"] is not None
    }
    assert deprecated == {
        "deepseek-v3.2-chat": "2026-07-24",
        "deepseek-v3": "2026-07-24",
    }


@pytest.mark.asyncio
async def test_ru_legal_only_for_yandex_and_sber(all_providers_registered, client):
    """ru_legal=True only for models hosted in RU under 152-FZ jurisdiction.

    DeepSeek is Chinese — even if reachable from RU without proxy, it's
    not 152-FZ-friendly. Only Yandex (yandex_cloud) and Sber (GigaChat)
    qualify. The marketing UI uses this flag to badge RU-legal models.
    """
    r = await client.get("/v1/models/public")
    ru_legal_ids = {m["id"] for m in r.json()["data"] if m["capabilities"]["ru_legal"]}
    assert ru_legal_ids == {
        "yandexgpt-5.1-pro",
        "yandexgpt-5-lite",
        "gigachat-2-pro",
        "gigachat-2-lite",
        "gigachat-2-max",
    }


@pytest.mark.asyncio
async def test_public_payload_has_required_marketing_fields(all_providers_registered, client):
    """Each item must carry the canonical fields the lander/UI consumes."""
    r = await client.get("/v1/models/public")
    for item in r.json()["data"]:
        # Top-level
        for key in (
            "id",
            "display_name",
            "provider",
            "provider_display_name",
            "category",
            "tier",
            "context_tokens",
            "pricing_rub_per_1m",
            "pricing_usd_per_1m_official",
            "modalities",
            "capabilities",
            "deprecated_at",
            "released_at",
            "description",
            "best_for",
        ):
            assert key in item, f"missing {key} on {item.get('id')}"
        # Nested capabilities — every model must declare every flag (no
        # partial dicts; the UI reads each key directly).
        for cap in (
            "streaming",
            "tool_calling",
            "json_schema_strict",
            "vision",
            "audio",
            "prompt_caching",
            "ru_legal",
        ):
            assert cap in item["capabilities"]
        # tier is upper-case for badge styling.
        assert item["tier"].isupper()


@pytest.mark.asyncio
async def test_deepseek_v4_flash_cached_price_is_not_zero(all_providers_registered, client):
    """Regression — sub-kopeck cached rate ($0.014) must surface non-zero
    in the public payload thanks to the explicit usd_cached_per_1m override
    on deepseek-v4-flash. Without the override, kop rounds to 0 and the
    lander would advertise "0 ₽" cached pricing, which is misleading.
    """
    r = await client.get("/v1/models/public")
    flash = next(m for m in r.json()["data"] if m["id"] == "deepseek-v4-flash")
    cached = flash["pricing_rub_per_1m"]["cached_input"]
    # 0.014 USD × 80 × 1.15 = 1.288 → rounds to 1.
    assert cached == 1
    assert flash["pricing_usd_per_1m_official"]["cached_input"] == 0.014
