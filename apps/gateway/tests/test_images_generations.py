"""Tests for POST /v1/images/generations (Sprint M2 — image gen).

Strategy: stub ``provider.image_generate`` via AsyncMock — same pattern as
TTS. Verify the endpoint forwards args correctly, returns the OpenAI-shape
JSON, debits the right amount, and writes a UsageEvent.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from voltari_gateway.db.models import UsageEvent
from voltari_gateway.providers.base import ProviderError


def _stub_image_response(n: int = 1) -> dict:
    """OpenAI-shape image response with ``n`` items."""
    return {
        "created": 1730000000,
        "data": [
            {
                "url": f"https://oaicdn.example/img/{i}.png",
                "revised_prompt": "A cat sitting on a rocket in space, photorealistic.",
            }
            for i in range(n)
        ],
        "usage": {"input_tokens": 10, "output_tokens": 0, "total_tokens": 10},
    }


@pytest.mark.asyncio
async def test_images_happy_path_returns_payload_and_writes_usage(client, app, api_key_fixture, db):
    """Happy path — gpt-image-1, n=1, default quality+size — returns 200 with
    JSON payload, writes a UsageEvent, debits the account."""
    upstream = _stub_image_response(n=1)
    stub = app.state.openai_provider
    stub.image_generate = AsyncMock(return_value=upstream)

    pre_balance = api_key_fixture.account.balance_kopecks

    r = await client.post(
        "/v1/images/generations",
        json={
            "model": "gpt-image-1",
            "prompt": "A cat in space",
            "n": 1,
        },
        headers=api_key_fixture.auth_header,
    )
    assert r.status_code == 200, r.text
    assert r.json() == upstream
    assert r.headers["X-Gateway-Modality"] == "image"
    assert r.headers["X-Gateway-Provider"] == "openai"
    assert int(r.headers["X-Gateway-Image-Count"]) == 1
    cost = int(r.headers["X-Gateway-Cost-Kop"])
    assert cost > 0

    # Provider invoked with the resolved upstream id + defaults.
    stub.image_generate.assert_awaited_once()
    kwargs = stub.image_generate.await_args.kwargs
    assert kwargs["model"] == "gpt-image-1"
    assert kwargs["prompt"] == "A cat in space"
    assert kwargs["n"] == 1
    assert kwargs["quality"] == "auto"  # default for gpt-image-1
    assert kwargs["size"] == "1024x1024"  # default for gpt-image-1

    rows = (await db.execute(select(UsageEvent))).scalars().all()
    assert len(rows) == 1
    event = rows[0]
    assert event.model == "gpt-image-1"
    assert event.provider == "openai"
    assert event.modality == "image"
    assert event.unit == "image"
    assert event.cost_kopecks == cost
    assert event.output_tokens == 1  # one image generated

    from voltari_gateway.db.models import Account

    refreshed = await db.get(Account, api_key_fixture.account.id)
    assert refreshed is not None
    await db.refresh(refreshed)
    assert refreshed.balance_kopecks == pre_balance - cost


@pytest.mark.asyncio
async def test_images_n_multiplies_cost(client, app, api_key_fixture):
    """n=3 should cost ~3× n=1 (modulo rounding)."""
    stub = app.state.openai_provider
    stub.image_generate = AsyncMock(return_value=_stub_image_response(n=3))

    r1 = await client.post(
        "/v1/images/generations",
        json={"model": "gpt-image-1", "prompt": "A cat", "n": 1},
        headers=api_key_fixture.auth_header,
    )
    assert r1.status_code == 200
    cost_1 = int(r1.headers["X-Gateway-Cost-Kop"])

    stub.image_generate = AsyncMock(return_value=_stub_image_response(n=3))
    r3 = await client.post(
        "/v1/images/generations",
        json={"model": "gpt-image-1", "prompt": "A cat", "n": 3},
        headers=api_key_fixture.auth_header,
    )
    assert r3.status_code == 200
    cost_3 = int(r3.headers["X-Gateway-Cost-Kop"])
    # 3× ± 1 kopeck rounding tolerance.
    assert abs(cost_3 - cost_1 * 3) <= 1


@pytest.mark.asyncio
async def test_images_dalle3_n_too_large_returns_400(client, app, api_key_fixture):
    """DALL·E 3 has max_n=1 — n=2 must reject before forwarding."""
    stub = app.state.openai_provider
    stub.image_generate = AsyncMock()

    r = await client.post(
        "/v1/images/generations",
        json={"model": "dall-e-3", "prompt": "A cat", "n": 2},
        headers=api_key_fixture.auth_header,
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "n_too_large"
    stub.image_generate.assert_not_awaited()


@pytest.mark.asyncio
async def test_images_unknown_model_returns_404(client, app, api_key_fixture):
    stub = app.state.openai_provider
    stub.image_generate = AsyncMock()
    r = await client.post(
        "/v1/images/generations",
        json={"model": "midjourney-v6", "prompt": "A cat", "n": 1},
        headers=api_key_fixture.auth_header,
    )
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "model_not_found"


@pytest.mark.asyncio
async def test_images_invalid_quality_returns_400(client, app, api_key_fixture):
    stub = app.state.openai_provider
    stub.image_generate = AsyncMock()
    r = await client.post(
        "/v1/images/generations",
        json={
            "model": "gpt-image-1",
            "prompt": "A cat",
            "n": 1,
            "quality": "ultra-mega",
        },
        headers=api_key_fixture.auth_header,
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "invalid_quality"
    stub.image_generate.assert_not_awaited()


@pytest.mark.asyncio
async def test_images_invalid_size_returns_400(client, app, api_key_fixture):
    stub = app.state.openai_provider
    stub.image_generate = AsyncMock()
    r = await client.post(
        "/v1/images/generations",
        json={
            "model": "dall-e-3",
            "prompt": "A cat",
            "n": 1,
            "size": "16384x16384",
        },
        headers=api_key_fixture.auth_header,
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "invalid_size"


@pytest.mark.asyncio
async def test_images_no_auth_returns_401(client):
    r = await client.post(
        "/v1/images/generations",
        json={"model": "gpt-image-1", "prompt": "A cat", "n": 1},
    )
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_images_empty_prompt_returns_400(client, api_key_fixture):
    r = await client.post(
        "/v1/images/generations",
        json={"model": "gpt-image-1", "prompt": "   ", "n": 1},
        headers=api_key_fixture.auth_header,
    )
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_images_upstream_error_releases_hold(client, app, api_key_fixture, db):
    """Upstream ProviderError → hold released, no UsageEvent, 502."""
    stub = app.state.openai_provider
    stub.image_generate = AsyncMock(side_effect=ProviderError("boom"))

    pre_balance = api_key_fixture.account.balance_kopecks

    r = await client.post(
        "/v1/images/generations",
        json={"model": "gpt-image-1", "prompt": "A cat", "n": 1},
        headers=api_key_fixture.auth_header,
    )
    assert r.status_code == 502

    rows = (await db.execute(select(UsageEvent))).scalars().all()
    assert rows == []

    from voltari_gateway.db.models import Account

    refreshed = await db.get(Account, api_key_fixture.account.id)
    assert refreshed is not None
    await db.refresh(refreshed)
    assert refreshed.balance_kopecks == pre_balance


@pytest.mark.asyncio
async def test_images_pii_masking_strips_pii_from_prompt(client, app, api_key_fixture):
    """X-PII-Protect: true → upstream sees masked prompt, not raw PII."""
    stub = app.state.openai_provider
    stub.image_generate = AsyncMock(return_value=_stub_image_response(n=1))

    r = await client.post(
        "/v1/images/generations",
        json={
            "model": "gpt-image-1",
            "prompt": "Portrait of Иванов Иван by email ivan@example.com",
            "n": 1,
        },
        headers={**api_key_fixture.auth_header, "X-PII-Protect": "true"},
    )
    assert r.status_code == 200
    sent_prompt = stub.image_generate.await_args.kwargs["prompt"]
    assert "ivan@example.com" not in sent_prompt


@pytest.mark.asyncio
async def test_images_dalle3_resolves_upstream_id(client, app, api_key_fixture):
    """DALL·E 3 catalog entry has upstream_id == 'dall-e-3' — verify forward."""
    stub = app.state.openai_provider
    stub.image_generate = AsyncMock(return_value=_stub_image_response(n=1))

    r = await client.post(
        "/v1/images/generations",
        json={
            "model": "dall-e-3",
            "prompt": "A cat",
            "n": 1,
            "quality": "hd",
            "size": "1024x1024",
        },
        headers=api_key_fixture.auth_header,
    )
    assert r.status_code == 200
    kwargs = stub.image_generate.await_args.kwargs
    assert kwargs["model"] == "dall-e-3"
    assert kwargs["quality"] == "hd"
    assert kwargs["size"] == "1024x1024"


@pytest.mark.asyncio
async def test_images_provider_not_configured_returns_502(client, app, api_key_fixture):
    app.state.provider_registry = None
    app.state.openai_provider = None

    r = await client.post(
        "/v1/images/generations",
        json={"model": "gpt-image-1", "prompt": "A cat", "n": 1},
        headers=api_key_fixture.auth_header,
    )
    assert r.status_code == 502
