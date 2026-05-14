"""Cookie upload — gateway side of Phase 2 cookie management.

Flow when CEO uploads cookies for a provider:
    1. Frontend POSTs the **plaintext** Playwright cookies-JSON dump to
       ``/v1/account/admin/provider_cookies/{provider}``.
    2. Gateway encrypts via ``Fernet(ENCRYPTION_KEY)``.
    3. Gateway forwards the encrypted blob to ``brikko-scraper`` over the
       internal network (``X-Internal-Token`` header, POST /cookies/{provider}).
    4. On scraper-200, gateway upserts the ``provider_cookies`` row (metadata).
    5. Audit log entry written (``provider_cookies_uploaded`` action).

Why encrypt on gateway (not on the browser):
* Keeps the browser flow trivial — a textarea + paste, no WebCrypto.
* CEO is the only user who'll ever do this.  The transport is HTTPS, the
  gateway is already trusted with the plaintext PII it holds for billing,
  one more secret moving through the same trust boundary is acceptable.
* age-in-browser via wasm is technically possible but adds 500 KB of WASM
  for a flow that runs ~once a week.

If we ever need to remove gateway-side plaintext access (e.g. on-prem
deployment where ops shouldn't see provider cookies), this is the layer to
swap — encrypt-in-browser becomes a future hardening, not a blocker.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
from cryptography.fernet import Fernet
from cryptography.fernet import InvalidToken as FernetInvalidToken


class CookieUploadError(Exception):
    """Base class for upload failures (caller maps to HTTP envelope)."""


class InvalidCookieJsonError(CookieUploadError):
    """Plaintext payload is not a valid Playwright cookies JSON array."""


class CipherUnavailableError(CookieUploadError):
    """ENCRYPTION_KEY not set / invalid Fernet key — refuse to encrypt."""


class ScraperUnavailableError(CookieUploadError):
    """brikko-scraper rejected the upload or is unreachable."""


def encrypt_cookies(plaintext: bytes, *, fernet_key: str) -> bytes:
    """Validate the JSON shape and encrypt to Fernet ciphertext.

    Raises:
        InvalidCookieJsonError: payload is not a JSON list of objects.
        CipherUnavailableError: ``fernet_key`` is empty or malformed.
    """
    if not fernet_key:
        raise CipherUnavailableError("ENCRYPTION_KEY is empty — refusing to encrypt cookies")

    # Hard cap on plaintext size — same 64 KB ceiling the scraper enforces
    # on its side.  Prevents accidental misuse as a blob store.
    if len(plaintext) > 65_536:
        raise InvalidCookieJsonError(f"cookies blob too large ({len(plaintext)} bytes); max 65536")

    try:
        parsed: Any = json.loads(plaintext.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise InvalidCookieJsonError(f"invalid JSON: {exc}") from exc
    if not isinstance(parsed, list):
        raise InvalidCookieJsonError("cookies payload must be a JSON list")
    if not parsed:
        raise InvalidCookieJsonError("cookies payload must contain at least one cookie")
    for i, item in enumerate(parsed):
        if not isinstance(item, dict):
            raise InvalidCookieJsonError(f"cookie[{i}] must be an object")
        if "name" not in item or "value" not in item:
            raise InvalidCookieJsonError(f"cookie[{i}] must have 'name' and 'value'")

    try:
        fernet = Fernet(fernet_key.encode("utf-8") if isinstance(fernet_key, str) else fernet_key)
    except Exception as exc:
        raise CipherUnavailableError(f"ENCRYPTION_KEY is not a valid Fernet key: {exc}") from exc

    return fernet.encrypt(plaintext)


async def post_to_scraper(
    *,
    scraper_url: str,
    internal_token: str,
    provider: str,
    ciphertext: bytes,
    timeout_seconds: float = 15.0,
) -> dict[str, Any]:
    """Forward the encrypted blob to ``brikko-scraper``.

    Returns the scraper response JSON ``{provider, size_bytes, stored_at}``.

    Raises:
        ScraperUnavailableError: any 4xx/5xx from scraper, or network failure.
    """
    if not scraper_url:
        raise ScraperUnavailableError("SCRAPER_URL not configured — Phase 2 scraping disabled")
    if not internal_token:
        raise ScraperUnavailableError("SCRAPER_INTERNAL_TOKEN not configured — refusing to upload")

    url = scraper_url.rstrip("/") + f"/cookies/{provider}"
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(timeout_seconds, connect=5.0),
            trust_env=False,
        ) as http:
            resp = await http.post(
                url,
                content=ciphertext,
                headers={
                    "X-Internal-Token": internal_token,
                    "Content-Type": "application/octet-stream",
                },
            )
    except httpx.TimeoutException as exc:
        raise ScraperUnavailableError(f"scraper timeout: {exc}") from exc
    except httpx.HTTPError as exc:
        raise ScraperUnavailableError(f"scraper unreachable: {exc}") from exc

    if resp.status_code != 200:
        body = resp.text[:300]
        raise ScraperUnavailableError(f"scraper rejected upload ({resp.status_code}): {body}")

    try:
        return resp.json()  # type: ignore[no-any-return]
    except (json.JSONDecodeError, ValueError) as exc:
        raise ScraperUnavailableError(f"scraper returned non-JSON: {exc}") from exc


def decrypt_cookies(ciphertext: bytes, *, fernet_key: str) -> bytes:
    """Round-trip decrypt — used by tests and dev tools.

    Raises:
        CipherUnavailableError: key missing/malformed.
        InvalidCookieJsonError: ciphertext is not a valid Fernet token.
    """
    if not fernet_key:
        raise CipherUnavailableError("ENCRYPTION_KEY is empty")
    try:
        fernet = Fernet(fernet_key.encode("utf-8") if isinstance(fernet_key, str) else fernet_key)
    except Exception as exc:
        raise CipherUnavailableError(f"invalid Fernet key: {exc}") from exc
    try:
        return fernet.decrypt(ciphertext)
    except FernetInvalidToken as exc:
        raise InvalidCookieJsonError(f"ciphertext does not decrypt: {exc}") from exc
