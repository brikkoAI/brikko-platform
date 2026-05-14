"""Tests for POST /v1/audio/transcriptions (Sprint M1).

We mock OpenAI through ``respx`` — no real network. The audio path uses
its own per-request httpx.AsyncClient (it's not on the OpenAI SDK), so
we match the ``api.openai.com/v1/audio/transcriptions`` URL directly.

Two duration paths are exercised:

* mutagen-friendly file (a synthetic minimal WAV) → 0.1 min minimum.
* mutagen-rejecting bytes (random garbage) → falls back to the byte-rate
  heuristic. Both should bill SOMETHING (we never let a non-empty
  upload bill 0 kopecks).
"""

from __future__ import annotations

import struct
from decimal import Decimal

import httpx
import pytest
import respx
from sqlalchemy import select

from voltari_gateway.db.models import UsageEvent


def _minimal_wav(duration_seconds: float = 1.0, sample_rate: int = 8000) -> bytes:
    """Build a tiny but valid 8-bit mono PCM WAV. mutagen parses it fine.

    Why we don't bundle a real audio fixture: the file would need to live
    in the repo as a binary, which complicates code review and `git diff`.
    A handful of struct.pack calls produces a deterministic, RFC-compliant
    PCM WAV that mutagen reports as the right duration.
    """
    num_samples = int(duration_seconds * sample_rate)
    samples = b"\x80" * num_samples  # silent 8-bit PCM (centre value)
    fmt_chunk = struct.pack(
        "<4sIHHIIHH",
        b"fmt ",
        16,  # PCM fmt chunk size
        1,  # PCM format
        1,  # mono
        sample_rate,
        sample_rate,  # byte rate (8-bit mono)
        1,  # block align
        8,  # bits per sample
    )
    data_chunk = struct.pack("<4sI", b"data", len(samples)) + samples
    riff_size = 4 + len(fmt_chunk) + len(data_chunk)
    header = struct.pack("<4sI4s", b"RIFF", riff_size, b"WAVE")
    return header + fmt_chunk + data_chunk


@pytest.mark.asyncio
async def test_transcription_happy_path_writes_usage_event(client, api_key_fixture, db):
    wav = _minimal_wav(duration_seconds=12.0)  # 0.2 min after round-up
    upstream_body = {"text": "hello world"}

    with respx.mock(assert_all_called=True) as mock:
        mock.post("https://api.openai.com/v1/audio/transcriptions").mock(
            return_value=httpx.Response(200, json=upstream_body)
        )
        r = await client.post(
            "/v1/audio/transcriptions",
            files={"file": ("test.wav", wav, "audio/wav")},
            data={"model": "whisper-1"},
            headers=api_key_fixture.auth_header,
        )

    assert r.status_code == 200, r.text
    assert r.json() == upstream_body
    assert r.headers["X-Gateway-Modality"] == "stt"
    assert r.headers["X-Gateway-Provider"] == "openai"
    # 12 seconds → 0.2 min after round-up; 0.2 × 55.2 kop/min = 11.04 → 11 kop
    cost = int(r.headers["X-Gateway-Cost-Kop"])
    assert cost > 0
    assert Decimal(r.headers["X-Gateway-Minutes"]) == Decimal("0.2")

    rows = (await db.execute(select(UsageEvent))).scalars().all()
    assert len(rows) == 1
    event = rows[0]
    assert event.model == "whisper-1"
    assert event.provider == "openai"
    assert event.modality == "stt"
    assert event.unit == "minute"
    assert event.cost_kopecks == cost
    assert event.input_tokens == 0
    assert event.output_tokens == 0


@pytest.mark.asyncio
async def test_transcription_optional_params_forwarded(client, api_key_fixture):
    wav = _minimal_wav(duration_seconds=6.0)

    with respx.mock(assert_all_called=True) as mock:
        route = mock.post("https://api.openai.com/v1/audio/transcriptions").mock(
            return_value=httpx.Response(200, json={"text": "ок"})
        )
        r = await client.post(
            "/v1/audio/transcriptions",
            files={"file": ("ru.wav", wav, "audio/wav")},
            data={
                "model": "whisper-1",
                "language": "ru",
                "prompt": "Это тест.",
                "response_format": "json",
                "temperature": "0.0",
            },
            headers=api_key_fixture.auth_header,
        )

    assert r.status_code == 200
    # Inspect the multipart body the gateway sent upstream — the language /
    # prompt / response_format flags must round-trip into the form fields.
    sent = route.calls.last.request
    body = sent.read().decode("utf-8", errors="replace")
    assert 'name="language"' in body and "ru" in body
    assert 'name="prompt"' in body and "Это тест." in body
    assert 'name="response_format"' in body and "json" in body
    assert 'name="temperature"' in body and "0.0" in body


@pytest.mark.asyncio
async def test_transcription_no_auth_returns_401(client):
    wav = _minimal_wav(duration_seconds=3.0)
    r = await client.post(
        "/v1/audio/transcriptions",
        files={"file": ("test.wav", wav, "audio/wav")},
        data={"model": "whisper-1"},
    )
    assert r.status_code == 401
    err = r.json()["error"]
    assert err["type"] == "authentication_error"


@pytest.mark.asyncio
async def test_transcription_invalid_bearer_returns_401(client):
    wav = _minimal_wav(duration_seconds=3.0)
    r = await client.post(
        "/v1/audio/transcriptions",
        files={"file": ("test.wav", wav, "audio/wav")},
        data={"model": "whisper-1"},
        headers={"Authorization": "Bearer sk-vlt-bogusbogus"},
    )
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_transcription_oversize_file_returns_413(client, api_key_fixture):
    # 26 MB of zeros — >25 MB cap, so we must reject *before* parsing
    # duration or forwarding upstream.
    payload = b"\x00" * (26 * 1024 * 1024)
    r = await client.post(
        "/v1/audio/transcriptions",
        files={"file": ("big.wav", payload, "audio/wav")},
        data={"model": "whisper-1"},
        headers=api_key_fixture.auth_header,
    )
    assert r.status_code == 413
    err = r.json()["error"]
    assert err["code"] == "file_too_large"


@pytest.mark.asyncio
async def test_transcription_missing_file_returns_400(client, api_key_fixture):
    # Pydantic / FastAPI multipart-validation errors are normalised by
    # the gateway's ``validation_exception_handler`` to a 400 envelope
    # (matches OpenAI's actual behaviour — they don't use 422 either).
    r = await client.post(
        "/v1/audio/transcriptions",
        data={"model": "whisper-1"},
        headers=api_key_fixture.auth_header,
    )
    assert r.status_code == 400
    err = r.json()["error"]
    assert err["type"] == "invalid_request_error"


@pytest.mark.asyncio
async def test_transcription_empty_file_returns_400(client, api_key_fixture):
    r = await client.post(
        "/v1/audio/transcriptions",
        files={"file": ("empty.wav", b"", "audio/wav")},
        data={"model": "whisper-1"},
        headers=api_key_fixture.auth_header,
    )
    assert r.status_code == 400
    err = r.json()["error"]
    assert err["code"] == "empty_file"


@pytest.mark.asyncio
async def test_transcription_unknown_model_returns_404(client, api_key_fixture):
    wav = _minimal_wav(duration_seconds=3.0)
    r = await client.post(
        "/v1/audio/transcriptions",
        files={"file": ("test.wav", wav, "audio/wav")},
        data={"model": "whisper-NOPE"},
        headers=api_key_fixture.auth_header,
    )
    assert r.status_code == 404
    err = r.json()["error"]
    assert err["code"] == "model_not_found"


@pytest.mark.asyncio
async def test_transcription_upstream_429_releases_hold(client, api_key_fixture, db):
    """A 429 from the upstream must release the hold — the customer
    didn't get any work done, so we mustn't keep the balance reserved.
    """
    wav = _minimal_wav(duration_seconds=3.0)

    with respx.mock(assert_all_called=True) as mock:
        mock.post("https://api.openai.com/v1/audio/transcriptions").mock(
            return_value=httpx.Response(429, json={"error": "rate-limited"})
        )
        r = await client.post(
            "/v1/audio/transcriptions",
            files={"file": ("test.wav", wav, "audio/wav")},
            data={"model": "whisper-1"},
            headers=api_key_fixture.auth_header,
        )

    assert r.status_code == 502  # mapped from upstream 429
    # No usage event written on upstream failure
    rows = (await db.execute(select(UsageEvent))).scalars().all()
    assert rows == []


@pytest.mark.asyncio
async def test_transcription_garbage_file_uses_fallback_duration(client, api_key_fixture, db):
    """Mutagen rejects non-audio bytes. The fallback heuristic (bytes ÷ 16k)
    should still produce a positive billable duration so we never let a
    deliberately-malformed file ride for free.
    """
    garbage = b"NOT_AUDIO_AT_ALL_" * 100  # ~1.7 KB of garbage

    with respx.mock(assert_all_called=True) as mock:
        mock.post("https://api.openai.com/v1/audio/transcriptions").mock(
            return_value=httpx.Response(200, json={"text": ""})
        )
        r = await client.post(
            "/v1/audio/transcriptions",
            files={"file": ("garbage.bin", garbage, "audio/mpeg")},
            data={"model": "whisper-1"},
            headers=api_key_fixture.auth_header,
        )

    assert r.status_code == 200
    cost = int(r.headers["X-Gateway-Cost-Kop"])
    assert cost > 0
    rows = (await db.execute(select(UsageEvent))).scalars().all()
    assert len(rows) == 1
    assert rows[0].cost_kopecks == cost
