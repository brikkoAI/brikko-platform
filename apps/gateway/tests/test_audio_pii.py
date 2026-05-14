"""PII masking for /v1/audio/transcriptions (Sprint 13 / Privacy v2 — Phase 4).

Whisper response carries the transcript text. Masking is applied
**post-Whisper, in-memory, before the response leaves the gateway**.
There is no unmask path — STT is a one-way artefact.

Coverage:

* Header opt-in masks the transcript text in JSON responses.
* Form-field opt-in (``pii_protect=true``) does the same.
* Account flag (``Account.pii_masking_enabled=True``) triggers masking
  too — STT, unlike embeddings, doesn't break under masking (text is
  text, semantics are preserved).
* Default → transcript forwarded verbatim.
* Plain-text response (``response_format=text``) is also masked.
* Multi-PII transcript — every detected entity is masked.

Strategy: respx-mock OpenAI's Whisper upstream returning a transcript
that contains PII strings. Inspect the response body to assert masking
happened (or didn't, for the default-disabled case).
"""

from __future__ import annotations

import struct

import httpx
import pytest
import respx

from voltari_gateway.db.models import Account


def _minimal_wav(duration_seconds: float = 1.0, sample_rate: int = 8000) -> bytes:
    """Tiny but valid 8-bit mono PCM WAV. mutagen reports the duration."""
    num_samples = int(duration_seconds * sample_rate)
    samples = b"\x80" * num_samples
    fmt_chunk = struct.pack(
        "<4sIHHIIHH",
        b"fmt ",
        16,
        1,
        1,
        sample_rate,
        sample_rate,
        1,
        8,
    )
    data_chunk = struct.pack("<4sI", b"data", len(samples)) + samples
    riff_size = 4 + len(fmt_chunk) + len(data_chunk)
    header = struct.pack("<4sI4s", b"RIFF", riff_size, b"WAVE")
    return header + fmt_chunk + data_chunk


WHISPER_URL = "https://api.openai.com/v1/audio/transcriptions"


# ---------------------------------------------------------------------------
# Header opt-in (JSON)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pii_header_masks_json_transcript(client, api_key_fixture):
    """X-PII-Protect: true → email/name in transcript replaced with placeholders."""
    wav = _minimal_wav(duration_seconds=4.0)
    upstream = {"text": "Звоните Иванов Иван Иванович на email alice@example.io."}

    with respx.mock(assert_all_called=True) as mock:
        mock.post(WHISPER_URL).mock(return_value=httpx.Response(200, json=upstream))
        r = await client.post(
            "/v1/audio/transcriptions",
            files={"file": ("test.wav", wav, "audio/wav")},
            data={"model": "whisper-1"},
            headers={
                **api_key_fixture.auth_header,
                "X-PII-Protect": "true",
            },
        )
    assert r.status_code == 200, r.text

    body = r.json()
    text = body["text"]
    assert "Иванов Иван Иванович" not in text
    assert "alice@example.io" not in text
    assert "<NAME_" in text
    assert "<EMAIL_" in text


# ---------------------------------------------------------------------------
# Form-field opt-in
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pii_form_field_enables_masking(client, api_key_fixture):
    """``pii_protect=true`` Form field → masking on (no header, no account)."""
    wav = _minimal_wav(duration_seconds=4.0)
    upstream = {"text": "Перезвонить +79161234567 завтра."}

    with respx.mock(assert_all_called=True) as mock:
        mock.post(WHISPER_URL).mock(return_value=httpx.Response(200, json=upstream))
        r = await client.post(
            "/v1/audio/transcriptions",
            files={"file": ("test.wav", wav, "audio/wav")},
            data={"model": "whisper-1", "pii_protect": "true"},
            headers=api_key_fixture.auth_header,
        )
    assert r.status_code == 200, r.text
    text = r.json()["text"]
    assert "+79161234567" not in text
    assert "<PHONE_" in text


# ---------------------------------------------------------------------------
# Account flag — STT permits the strongest commit
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pii_account_flag_enables_without_per_request_optin(client, api_key_fixture, db):
    """STT — unlike embeddings — uses the full three-way gate. Account
    flag set → masking happens without per-request opt-in."""
    account = await db.get(Account, api_key_fixture.account.id)
    account.pii_masking_enabled = True
    db.add(account)
    await db.commit()

    wav = _minimal_wav(duration_seconds=4.0)
    upstream = {"text": "ИНН компании 7707083893 указан в договоре."}

    with respx.mock(assert_all_called=True) as mock:
        mock.post(WHISPER_URL).mock(return_value=httpx.Response(200, json=upstream))
        r = await client.post(
            "/v1/audio/transcriptions",
            files={"file": ("test.wav", wav, "audio/wav")},
            data={"model": "whisper-1"},
            headers=api_key_fixture.auth_header,  # NO X-PII-Protect / pii_protect
        )
    assert r.status_code == 200, r.text
    text = r.json()["text"]
    assert "7707083893" not in text
    assert "<INN_" in text


# ---------------------------------------------------------------------------
# Default — verbatim
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pii_disabled_by_default(client, api_key_fixture):
    """No flags → transcript forwarded verbatim (the legacy contract)."""
    wav = _minimal_wav(duration_seconds=4.0)
    upstream = {"text": "Email is dev@x.io"}

    with respx.mock(assert_all_called=True) as mock:
        mock.post(WHISPER_URL).mock(return_value=httpx.Response(200, json=upstream))
        r = await client.post(
            "/v1/audio/transcriptions",
            files={"file": ("test.wav", wav, "audio/wav")},
            data={"model": "whisper-1"},
            headers=api_key_fixture.auth_header,
        )
    assert r.status_code == 200, r.text
    assert r.json()["text"] == "Email is dev@x.io"


# ---------------------------------------------------------------------------
# Plain-text response (response_format=text)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pii_masks_plain_text_response(client, api_key_fixture):
    """``response_format=text`` → upstream replies text/plain. Mask the
    decoded UTF-8 body in place."""
    wav = _minimal_wav(duration_seconds=4.0)
    plain_text = "Свяжись с alice@example.io по поводу контракта."

    with respx.mock(assert_all_called=True) as mock:
        mock.post(WHISPER_URL).mock(
            return_value=httpx.Response(
                200,
                content=plain_text.encode("utf-8"),
                headers={"content-type": "text/plain; charset=utf-8"},
            )
        )
        r = await client.post(
            "/v1/audio/transcriptions",
            files={"file": ("test.wav", wav, "audio/wav")},
            data={
                "model": "whisper-1",
                "response_format": "text",
                "pii_protect": "true",
            },
            headers=api_key_fixture.auth_header,
        )
    assert r.status_code == 200, r.text
    body_text = r.text
    assert "alice@example.io" not in body_text
    assert "<EMAIL_" in body_text


# ---------------------------------------------------------------------------
# Multi-PII transcript
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pii_multiple_types_in_transcript(client, api_key_fixture):
    """Email + phone + name + INN in transcript → all masked."""
    wav = _minimal_wav(duration_seconds=6.0)
    upstream = {
        "text": (
            "Иванов Иван Сергеевич, ИНН 7707083893, почта ivan@example.io, телефон +79161234567."
        )
    }

    with respx.mock(assert_all_called=True) as mock:
        mock.post(WHISPER_URL).mock(return_value=httpx.Response(200, json=upstream))
        r = await client.post(
            "/v1/audio/transcriptions",
            files={"file": ("test.wav", wav, "audio/wav")},
            data={"model": "whisper-1", "pii_protect": "true"},
            headers=api_key_fixture.auth_header,
        )
    assert r.status_code == 200, r.text
    text = r.json()["text"]
    assert "Иванов Иван Сергеевич" not in text
    assert "7707083893" not in text
    assert "ivan@example.io" not in text
    assert "+79161234567" not in text
    # All four placeholder families present.
    assert "<NAME_" in text
    assert "<INN_" in text
    assert "<EMAIL_" in text
    assert "<PHONE_" in text


# ---------------------------------------------------------------------------
# Segments path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pii_masks_segments_text(client, api_key_fixture):
    """``response_format=verbose_json`` returns ``segments[].text``. Each
    segment text gets masked too."""
    wav = _minimal_wav(duration_seconds=4.0)
    upstream = {
        "text": "Иванов Иван написал.",
        "segments": [
            {
                "id": 0,
                "start": 0.0,
                "end": 2.0,
                "text": "Иванов Иван",
            },
            {
                "id": 1,
                "start": 2.0,
                "end": 4.0,
                "text": "написал письмо на bob@example.io.",
            },
        ],
    }

    with respx.mock(assert_all_called=True) as mock:
        mock.post(WHISPER_URL).mock(return_value=httpx.Response(200, json=upstream))
        r = await client.post(
            "/v1/audio/transcriptions",
            files={"file": ("test.wav", wav, "audio/wav")},
            data={"model": "whisper-1", "pii_protect": "true"},
            headers=api_key_fixture.auth_header,
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert "Иванов Иван" not in body["text"]
    for seg in body["segments"]:
        assert "Иванов Иван" not in seg["text"]
        assert "bob@example.io" not in seg["text"]
