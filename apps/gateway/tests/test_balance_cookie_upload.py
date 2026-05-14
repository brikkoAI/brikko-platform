"""Unit tests for balance_monitor.cookie_upload helpers."""

from __future__ import annotations

import json

import httpx
import pytest
import respx
from cryptography.fernet import Fernet

from voltari_gateway.balance_monitor.cookie_upload import (
    CipherUnavailableError,
    InvalidCookieJsonError,
    ScraperUnavailableError,
    decrypt_cookies,
    encrypt_cookies,
    post_to_scraper,
)


def _key() -> str:
    return Fernet.generate_key().decode("utf-8")


# ---------------------------------------------------------------------------
# encrypt_cookies
# ---------------------------------------------------------------------------


def test_encrypt_round_trip() -> None:
    key = _key()
    payload = json.dumps([{"name": "s", "value": "v"}]).encode("utf-8")
    ciphertext = encrypt_cookies(payload, fernet_key=key)
    assert ciphertext != payload
    # Round-trip with same key.
    plain = decrypt_cookies(ciphertext, fernet_key=key)
    assert plain == payload


def test_encrypt_rejects_empty_key() -> None:
    with pytest.raises(CipherUnavailableError):
        encrypt_cookies(b"[]", fernet_key="")


def test_encrypt_rejects_invalid_key() -> None:
    with pytest.raises(CipherUnavailableError):
        encrypt_cookies(b'[{"name":"s","value":"v"}]', fernet_key="not-a-fernet-key")


def test_encrypt_rejects_non_json() -> None:
    key = _key()
    with pytest.raises(InvalidCookieJsonError):
        encrypt_cookies(b"not json", fernet_key=key)


def test_encrypt_rejects_non_list() -> None:
    key = _key()
    with pytest.raises(InvalidCookieJsonError):
        encrypt_cookies(b'{"name": "s"}', fernet_key=key)


def test_encrypt_rejects_empty_list() -> None:
    key = _key()
    with pytest.raises(InvalidCookieJsonError):
        encrypt_cookies(b"[]", fernet_key=key)


def test_encrypt_rejects_missing_required_fields() -> None:
    key = _key()
    with pytest.raises(InvalidCookieJsonError):
        encrypt_cookies(b'[{"name": "s"}]', fernet_key=key)
    with pytest.raises(InvalidCookieJsonError):
        encrypt_cookies(b'[{"value": "v"}]', fernet_key=key)


def test_encrypt_rejects_too_large() -> None:
    key = _key()
    # 70 KB exceeds the 64 KB cap.
    huge = b'[{"name":"s","value":"' + b"x" * 70_000 + b'"}]'
    with pytest.raises(InvalidCookieJsonError):
        encrypt_cookies(huge, fernet_key=key)


# ---------------------------------------------------------------------------
# post_to_scraper
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_to_scraper_happy_path() -> None:
    with respx.mock(assert_all_called=True) as mock:
        mock.post("http://scraper:9100/cookies/openai").respond(
            200,
            json={
                "provider": "openai",
                "size_bytes": 100,
                "stored_at": "2026-05-11T00:00:00Z",
            },
        )
        result = await post_to_scraper(
            scraper_url="http://scraper:9100",
            internal_token="tok",
            provider="openai",
            ciphertext=b"cipher",
        )
    assert result["provider"] == "openai"
    assert result["size_bytes"] == 100


@pytest.mark.asyncio
async def test_post_to_scraper_500_raises() -> None:
    with respx.mock() as mock:
        mock.post("http://scraper:9100/cookies/openai").respond(500, text="oops")
        with pytest.raises(ScraperUnavailableError):
            await post_to_scraper(
                scraper_url="http://scraper:9100",
                internal_token="tok",
                provider="openai",
                ciphertext=b"cipher",
            )


@pytest.mark.asyncio
async def test_post_to_scraper_network_error_raises() -> None:
    with respx.mock() as mock:
        mock.post("http://scraper:9100/cookies/openai").mock(
            side_effect=httpx.ConnectError("refused")
        )
        with pytest.raises(ScraperUnavailableError):
            await post_to_scraper(
                scraper_url="http://scraper:9100",
                internal_token="tok",
                provider="openai",
                ciphertext=b"cipher",
            )


@pytest.mark.asyncio
async def test_post_to_scraper_empty_url_raises() -> None:
    with pytest.raises(ScraperUnavailableError):
        await post_to_scraper(
            scraper_url="",
            internal_token="tok",
            provider="openai",
            ciphertext=b"cipher",
        )


@pytest.mark.asyncio
async def test_post_to_scraper_empty_token_raises() -> None:
    with pytest.raises(ScraperUnavailableError):
        await post_to_scraper(
            scraper_url="http://scraper:9100",
            internal_token="",
            provider="openai",
            ciphertext=b"cipher",
        )
