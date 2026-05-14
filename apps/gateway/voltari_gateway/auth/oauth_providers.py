"""Google + Yandex OAuth2 clients.

Brikko is the OAuth client here (we redirect the user out, exchange
the code for a token, fetch userinfo, and discard the upstream
tokens). We deliberately do NOT persist provider access/refresh
tokens — the only post-callback action is a userinfo fetch, and we
have no plans to re-call provider APIs.

Each provider exposes the same surface:

* :func:`authorize_url` — build the URL we 302 the user to.
* :func:`exchange_code_for_userinfo` — POST /token + GET /userinfo,
  returning a normalised :class:`OAuthUserInfo`.

Both calls run with a strict timeout (``OAUTH_LOGIN_HTTP_TIMEOUT_SECONDS``,
default 10 s). Any networking, JSON-decoding, or HTTP-non-2xx error is
mapped to :class:`OAuthProviderError` with a short, *non-leaky* code so
the API layer can return a generic 502 ``oauth_provider_error`` without
exposing which step failed.

Email-verified semantics
------------------------

* Google's ID-token / userinfo response includes ``email_verified``
  (bool). We pass it through as-is.
* Yandex's userinfo returns ``default_email`` and ``emails[]`` but
  **no explicit verified flag**. Per CEO 2026-05-06, treated as
  *unverified* — the API layer prevents auto-link to an existing user
  in that case.

PII minimisation
----------------

We only return ``provider_subject``, ``email``, ``email_verified``,
``display_name``, ``avatar_url``. Anything else the provider returns
(locale, gender, friend lists) is dropped on the floor at this layer.
"""

from __future__ import annotations

import dataclasses
from typing import Final
from urllib.parse import urlencode

import httpx

from voltari_gateway.config import get_settings
from voltari_gateway.utils.logging import get_logger

log = get_logger(__name__)


GOOGLE_AUTHORIZE_URL: Final[str] = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL: Final[str] = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL: Final[str] = "https://www.googleapis.com/oauth2/v3/userinfo"
GOOGLE_SCOPES: Final[str] = "openid email profile"

YANDEX_AUTHORIZE_URL: Final[str] = "https://oauth.yandex.ru/authorize"
YANDEX_TOKEN_URL: Final[str] = "https://oauth.yandex.ru/token"
YANDEX_USERINFO_URL: Final[str] = "https://login.yandex.ru/info"
# Yandex scopes:
#   login:email   — default_email field
#   login:info    — display_name, avatar
# We do NOT request login:avatar (heavier, requires extra approval) —
# avatar URL comes back via login:info by default.
YANDEX_SCOPES: Final[str] = "login:email login:info"


class OAuthProviderError(Exception):
    """Raised on any provider-side failure (HTTP, JSON, schema).

    The ``code`` attribute is intentionally coarse so the API layer
    can return a single ``oauth_provider_error`` to the user without
    leaking provider internals.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclasses.dataclass(slots=True)
class OAuthUserInfo:
    provider: str  # 'google' | 'yandex'
    subject: str
    email: str | None
    email_verified: bool
    display_name: str | None
    avatar_url: str | None


# ---------------------------------------------------------------------------
# Public surface
# ---------------------------------------------------------------------------


def is_provider_configured(provider: str) -> bool:
    """Return True iff the env carries client_id + client_secret."""
    settings = get_settings()
    if provider == "google":
        return bool(
            settings.google_oauth_client_id
            and settings.google_oauth_client_secret.get_secret_value()
        )
    if provider == "yandex":
        return bool(
            settings.yandex_oauth_client_id
            and settings.yandex_oauth_client_secret.get_secret_value()
        )
    return False


def callback_url(provider: str) -> str:
    """Public callback URL we register with the provider."""
    base = get_settings().oauth_login_redirect_base.rstrip("/")
    return f"{base}/v1/auth/oauth/{provider}/callback"


def authorize_url(provider: str, *, state: str) -> str:
    """Build the consent URL we 302 the user to."""
    settings = get_settings()
    if provider == "google":
        params = {
            "response_type": "code",
            "client_id": settings.google_oauth_client_id,
            "redirect_uri": callback_url("google"),
            "scope": GOOGLE_SCOPES,
            "state": state,
            # Force the consent screen the first time so we have a
            # chance to surface the email scope explicitly. After the
            # first grant Google falls back to silent re-auth anyway.
            "access_type": "online",
            "prompt": "select_account",
            "include_granted_scopes": "true",
        }
        return f"{GOOGLE_AUTHORIZE_URL}?{urlencode(params)}"
    if provider == "yandex":
        params = {
            "response_type": "code",
            "client_id": settings.yandex_oauth_client_id,
            "redirect_uri": callback_url("yandex"),
            "scope": YANDEX_SCOPES,
            "state": state,
            # Yandex doesn't honour ``prompt`` — they always show the
            # account picker on first grant and silent-pass after.
        }
        return f"{YANDEX_AUTHORIZE_URL}?{urlencode(params)}"
    msg = f"Unsupported provider: {provider}"
    raise ValueError(msg)


async def exchange_code_for_userinfo(provider: str, *, code: str) -> OAuthUserInfo:
    """Run the full token + userinfo dance.

    On the wire:
        1. POST <token_url> with ``grant_type=authorization_code`` →
           ``access_token``
        2. GET  <userinfo_url> with ``Authorization: Bearer …``    →
           normalised profile

    Raises :class:`OAuthProviderError` on any failure.
    """
    if provider == "google":
        return await _google_exchange(code=code)
    if provider == "yandex":
        return await _yandex_exchange(code=code)
    msg = f"Unsupported provider: {provider}"
    raise ValueError(msg)


# ---------------------------------------------------------------------------
# Google
# ---------------------------------------------------------------------------


async def _google_exchange(*, code: str) -> OAuthUserInfo:
    settings = get_settings()
    timeout = settings.oauth_login_http_timeout_seconds
    token_form = {
        "code": code,
        "client_id": settings.google_oauth_client_id,
        "client_secret": settings.google_oauth_client_secret.get_secret_value(),
        "redirect_uri": callback_url("google"),
        "grant_type": "authorization_code",
    }
    async with httpx.AsyncClient(timeout=timeout) as http:
        try:
            resp = await http.post(
                GOOGLE_TOKEN_URL,
                data=token_form,
                headers={"Accept": "application/json"},
            )
        except httpx.HTTPError as exc:
            log.warning("oauth_google_token_network_error", error=str(exc))
            raise OAuthProviderError("token_network", "Network error talking to Google") from exc
        if resp.status_code != 200:
            log.warning(
                "oauth_google_token_http_error",
                status=resp.status_code,
                body_first_chars=resp.text[:200],
            )
            raise OAuthProviderError("token_rejected", "Google rejected the code")
        try:
            tok = resp.json()
        except ValueError as exc:
            raise OAuthProviderError("token_parse", "Google returned non-JSON") from exc
        access_token = tok.get("access_token")
        if not isinstance(access_token, str) or not access_token:
            raise OAuthProviderError("token_missing", "Google response missing access_token")

        try:
            ui_resp = await http.get(
                GOOGLE_USERINFO_URL,
                headers={"Authorization": f"Bearer {access_token}"},
            )
        except httpx.HTTPError as exc:
            log.warning("oauth_google_userinfo_network_error", error=str(exc))
            raise OAuthProviderError(
                "userinfo_network", "Network error fetching Google userinfo"
            ) from exc
    if ui_resp.status_code != 200:
        log.warning(
            "oauth_google_userinfo_http_error",
            status=ui_resp.status_code,
            body_first_chars=ui_resp.text[:200],
        )
        raise OAuthProviderError("userinfo_rejected", "Google userinfo rejected")
    try:
        info = ui_resp.json()
    except ValueError as exc:
        raise OAuthProviderError("userinfo_parse", "Google userinfo non-JSON") from exc

    sub = info.get("sub")
    if not isinstance(sub, str) or not sub:
        raise OAuthProviderError("userinfo_no_sub", "Google userinfo missing sub")
    email_raw = info.get("email")
    email = email_raw if isinstance(email_raw, str) and "@" in email_raw else None
    email_verified = bool(info.get("email_verified")) if email else False
    name = info.get("name")
    picture = info.get("picture")

    return OAuthUserInfo(
        provider="google",
        subject=sub,
        email=email.lower() if email else None,
        email_verified=email_verified,
        display_name=name if isinstance(name, str) else None,
        avatar_url=picture if isinstance(picture, str) else None,
    )


# ---------------------------------------------------------------------------
# Yandex
# ---------------------------------------------------------------------------


async def _yandex_exchange(*, code: str) -> OAuthUserInfo:
    settings = get_settings()
    timeout = settings.oauth_login_http_timeout_seconds
    token_form = {
        "grant_type": "authorization_code",
        "code": code,
        "client_id": settings.yandex_oauth_client_id,
        "client_secret": settings.yandex_oauth_client_secret.get_secret_value(),
        "redirect_uri": callback_url("yandex"),
    }
    async with httpx.AsyncClient(timeout=timeout) as http:
        try:
            resp = await http.post(
                YANDEX_TOKEN_URL,
                data=token_form,
                headers={"Accept": "application/json"},
            )
        except httpx.HTTPError as exc:
            log.warning("oauth_yandex_token_network_error", error=str(exc))
            raise OAuthProviderError("token_network", "Network error talking to Yandex") from exc
        if resp.status_code != 200:
            log.warning(
                "oauth_yandex_token_http_error",
                status=resp.status_code,
                body_first_chars=resp.text[:200],
            )
            raise OAuthProviderError("token_rejected", "Yandex rejected the code")
        try:
            tok = resp.json()
        except ValueError as exc:
            raise OAuthProviderError("token_parse", "Yandex returned non-JSON") from exc
        access_token = tok.get("access_token")
        if not isinstance(access_token, str) or not access_token:
            raise OAuthProviderError("token_missing", "Yandex response missing access_token")

        try:
            # Yandex requires ``OAuth <token>`` (custom scheme), not Bearer.
            ui_resp = await http.get(
                YANDEX_USERINFO_URL,
                headers={"Authorization": f"OAuth {access_token}"},
                params={"format": "json"},
            )
        except httpx.HTTPError as exc:
            log.warning("oauth_yandex_userinfo_network_error", error=str(exc))
            raise OAuthProviderError(
                "userinfo_network", "Network error fetching Yandex userinfo"
            ) from exc
    if ui_resp.status_code != 200:
        log.warning(
            "oauth_yandex_userinfo_http_error",
            status=ui_resp.status_code,
            body_first_chars=ui_resp.text[:200],
        )
        raise OAuthProviderError("userinfo_rejected", "Yandex userinfo rejected")
    try:
        info = ui_resp.json()
    except ValueError as exc:
        raise OAuthProviderError("userinfo_parse", "Yandex userinfo non-JSON") from exc

    sub = info.get("id")
    if not isinstance(sub, str) or not sub:
        raise OAuthProviderError("userinfo_no_sub", "Yandex userinfo missing id")

    email_raw = info.get("default_email")
    email = email_raw if isinstance(email_raw, str) and "@" in email_raw else None
    # Yandex userinfo carries no per-email verified flag. Per CEO
    # 2026-05-06: treat as unverified — the callback path forces a
    # manual link via password if the email already exists in our DB.
    email_verified = False

    display_name = info.get("real_name") or info.get("display_name") or info.get("login")
    avatar_id = info.get("default_avatar_id")
    avatar_url: str | None = None
    if isinstance(avatar_id, str) and avatar_id:
        # Public avatar URL pattern documented at
        # https://yandex.ru/dev/id/doc/dg/oauth/concepts/avatars.html
        avatar_url = f"https://avatars.yandex.net/get-yapic/{avatar_id}/islands-200"

    return OAuthUserInfo(
        provider="yandex",
        subject=sub,
        email=email.lower() if email else None,
        email_verified=email_verified,
        display_name=display_name if isinstance(display_name, str) else None,
        avatar_url=avatar_url,
    )


__all__ = [
    "OAuthProviderError",
    "OAuthUserInfo",
    "authorize_url",
    "callback_url",
    "exchange_code_for_userinfo",
    "is_provider_configured",
]
