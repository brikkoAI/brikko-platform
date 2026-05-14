"""Tests for POST /v1/audio/speech (Sprint M2 — TTS).

Strategy: stub the provider's ``audio_speech`` method via AsyncMock — the
endpoint code path looks up the provider through ``app.state.provider_registry``
(which conftest seeds with a StubProvider for OpenAI), so we monkey-patch
the stub to expose ``audio_speech`` and verify the endpoint returns audio
bytes + writes a UsageEvent.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from voltari_gateway.db.models import UsageEvent
from voltari_gateway.providers.base import (
    ProviderError,
    ProviderRateLimitError,
)


@pytest.mark.asyncio
async def test_tts_happy_path_returns_audio_bytes_and_writes_usage(
    client, app, api_key_fixture, db
):
    """Valid TTS call → 200 with audio body, UsageEvent persisted, hold committed."""
    fake_audio = b"\xff\xfb\x90\x44" + b"\x00" * 100  # mp3 magic + filler
    stub = app.state.openai_provider
    stub.audio_speech = AsyncMock(return_value=(fake_audio, "audio/mpeg"))

    pre_balance = api_key_fixture.account.balance_kopecks

    r = await client.post(
        "/v1/audio/speech",
        json={
            "model": "gpt-4o-mini-tts",
            "input": "Hello, world. Welcome to Brikko.",
            "voice": "alloy",
            "response_format": "mp3",
            "speed": 1.0,
        },
        headers=api_key_fixture.auth_header,
    )
    assert r.status_code == 200, r.text
    assert r.content == fake_audio
    assert r.headers["content-type"].startswith("audio/")
    assert r.headers["X-Gateway-Modality"] == "tts"
    assert r.headers["X-Gateway-Provider"] == "openai"
    cost = int(r.headers["X-Gateway-Cost-Kop"])
    assert cost > 0
    # 32 chars × ~5520 kop/1M chars ≈ 0.18 kop → minimum 1 kop floor.
    assert int(r.headers["X-Gateway-Input-Chars"]) == len("Hello, world. Welcome to Brikko.")

    # Provider was called with the right kwargs.
    stub.audio_speech.assert_awaited_once()
    call_kwargs = stub.audio_speech.await_args.kwargs
    assert call_kwargs["model"] == "gpt-4o-mini-tts"
    assert call_kwargs["voice"] == "alloy"
    assert call_kwargs["response_format"] == "mp3"
    assert call_kwargs["speed"] == 1.0
    assert call_kwargs["input_text"] == "Hello, world. Welcome to Brikko."

    # Usage event written.
    rows = (await db.execute(select(UsageEvent))).scalars().all()
    assert len(rows) == 1
    event = rows[0]
    assert event.model == "gpt-4o-mini-tts"
    assert event.provider == "openai"
    assert event.modality == "tts"
    assert event.unit == "char"
    assert event.cost_kopecks == cost
    assert event.input_tokens == len("Hello, world. Welcome to Brikko.")
    assert event.output_tokens == 0

    # Balance decremented by the committed cost — no phantom hold left over.
    from voltari_gateway.db.models import Account

    refreshed = await db.get(Account, api_key_fixture.account.id)
    assert refreshed is not None
    await db.refresh(refreshed)
    assert refreshed.balance_kopecks == pre_balance - cost


@pytest.mark.asyncio
async def test_tts_unknown_model_returns_404(client, app, api_key_fixture):
    stub = app.state.openai_provider
    stub.audio_speech = AsyncMock()
    r = await client.post(
        "/v1/audio/speech",
        json={
            "model": "tts-nope",
            "input": "Hi.",
            "voice": "alloy",
        },
        headers=api_key_fixture.auth_header,
    )
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "model_not_found"
    stub.audio_speech.assert_not_awaited()


@pytest.mark.asyncio
async def test_tts_unknown_voice_returns_400(client, api_key_fixture):
    r = await client.post(
        "/v1/audio/speech",
        json={
            "model": "gpt-4o-mini-tts",
            "input": "Hi.",
            "voice": "definitely-not-a-voice",
        },
        headers=api_key_fixture.auth_header,
    )
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_tts_no_auth_returns_401(client):
    r = await client.post(
        "/v1/audio/speech",
        json={
            "model": "gpt-4o-mini-tts",
            "input": "Hi.",
            "voice": "alloy",
        },
    )
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_tts_empty_input_returns_400(client, api_key_fixture):
    r = await client.post(
        "/v1/audio/speech",
        json={
            "model": "gpt-4o-mini-tts",
            "input": "   ",
            "voice": "alloy",
        },
        headers=api_key_fixture.auth_header,
    )
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_tts_input_too_long_returns_400(client, api_key_fixture):
    r = await client.post(
        "/v1/audio/speech",
        json={
            "model": "gpt-4o-mini-tts",
            "input": "A" * 5000,
            "voice": "alloy",
        },
        headers=api_key_fixture.auth_header,
    )
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_tts_speed_out_of_range_returns_400(client, api_key_fixture):
    r = await client.post(
        "/v1/audio/speech",
        json={
            "model": "gpt-4o-mini-tts",
            "input": "Hi.",
            "voice": "alloy",
            "speed": 10.0,
        },
        headers=api_key_fixture.auth_header,
    )
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_tts_upstream_error_releases_hold(client, app, api_key_fixture, db):
    """Provider raises ProviderError → hold released, no UsageEvent, 502."""
    stub = app.state.openai_provider
    stub.audio_speech = AsyncMock(side_effect=ProviderError("upstream boom"))

    pre_balance = api_key_fixture.account.balance_kopecks

    r = await client.post(
        "/v1/audio/speech",
        json={
            "model": "gpt-4o-mini-tts",
            "input": "Hi.",
            "voice": "alloy",
        },
        headers=api_key_fixture.auth_header,
    )
    assert r.status_code == 502

    # No usage event written on upstream failure.
    rows = (await db.execute(select(UsageEvent))).scalars().all()
    assert rows == []

    # Balance unchanged — hold was released, not committed.
    from voltari_gateway.db.models import Account

    refreshed = await db.get(Account, api_key_fixture.account.id)
    assert refreshed is not None
    await db.refresh(refreshed)
    assert refreshed.balance_kopecks == pre_balance


@pytest.mark.asyncio
async def test_tts_upstream_rate_limit_releases_hold(client, app, api_key_fixture, db):
    stub = app.state.openai_provider
    stub.audio_speech = AsyncMock(side_effect=ProviderRateLimitError("rate-limited"))

    r = await client.post(
        "/v1/audio/speech",
        json={
            "model": "gpt-4o-mini-tts",
            "input": "Hi.",
            "voice": "alloy",
        },
        headers=api_key_fixture.auth_header,
    )
    assert r.status_code == 502

    rows = (await db.execute(select(UsageEvent))).scalars().all()
    assert rows == []


@pytest.mark.asyncio
async def test_tts_pii_masking_via_header_strips_pii_before_upstream(client, app, api_key_fixture):
    """X-PII-Protect: true → upstream sees masked text, not raw PII."""
    fake_audio = b"\xff\xfb\x90\x44"
    stub = app.state.openai_provider
    stub.audio_speech = AsyncMock(return_value=(fake_audio, "audio/mpeg"))

    raw_input = "Контакт: +7 (905) 123-45-67, email@example.com"
    r = await client.post(
        "/v1/audio/speech",
        json={
            "model": "gpt-4o-mini-tts",
            "input": raw_input,
            "voice": "alloy",
        },
        headers={**api_key_fixture.auth_header, "X-PII-Protect": "true"},
    )
    assert r.status_code == 200
    sent_input = stub.audio_speech.await_args.kwargs["input_text"]
    # The masker should have replaced the phone + email with placeholders.
    assert "+7 (905)" not in sent_input
    assert "email@example.com" not in sent_input


@pytest.mark.asyncio
async def test_tts_instructions_stripped_for_legacy_models(client, app, api_key_fixture):
    """tts-1 doesn't accept ``instructions`` — gateway must strip it."""
    fake_audio = b"\xff\xfb\x90\x44"
    stub = app.state.openai_provider
    stub.audio_speech = AsyncMock(return_value=(fake_audio, "audio/mpeg"))

    r = await client.post(
        "/v1/audio/speech",
        json={
            "model": "tts-1",
            "input": "Hi.",
            "voice": "alloy",
            "instructions": "Speak slowly.",
        },
        headers=api_key_fixture.auth_header,
    )
    assert r.status_code == 200
    assert stub.audio_speech.await_args.kwargs["instructions"] is None


@pytest.mark.asyncio
async def test_tts_instructions_forwarded_for_gpt4o(client, app, api_key_fixture):
    fake_audio = b"\xff\xfb\x90\x44"
    stub = app.state.openai_provider
    stub.audio_speech = AsyncMock(return_value=(fake_audio, "audio/mpeg"))

    r = await client.post(
        "/v1/audio/speech",
        json={
            "model": "gpt-4o-mini-tts",
            "input": "Hi.",
            "voice": "alloy",
            "instructions": "Speak slowly.",
        },
        headers=api_key_fixture.auth_header,
    )
    assert r.status_code == 200
    assert stub.audio_speech.await_args.kwargs["instructions"] == "Speak slowly."


@pytest.mark.asyncio
async def test_tts_provider_not_configured_returns_502(client, app, api_key_fixture):
    """If the OpenAI provider is missing, surface 502 cleanly (no traceback)."""
    # Drop the provider — the registry-based lookup falls through to None.
    app.state.provider_registry = None
    app.state.openai_provider = None

    r = await client.post(
        "/v1/audio/speech",
        json={
            "model": "gpt-4o-mini-tts",
            "input": "Hi.",
            "voice": "alloy",
        },
        headers=api_key_fixture.auth_header,
    )
    assert r.status_code == 502
