"""End-to-end: OAuth onboarding then call /v1/chat with a scoped token.

Covers Task 11 of the gateway-oauth-pre-m0 plan:

* A client that completes the full OAuth dance (with only ``chat.read``)
  can call /v1/chat/completions.
* The same token is rejected with ``insufficient_scope`` on /v1/messages
  (which requires ``messages.read``).
* Pre-existing ``sk-vlt-`` / ``sk-brk-`` API keys keep working unchanged
  on /v1/chat/completions — this is the regression guard for the additive
  switch.
"""

from __future__ import annotations

import base64
import hashlib
import re
from urllib.parse import parse_qs, urlparse

import pytest

from voltari_gateway.auth.session import create_access_token
from voltari_gateway.db.models import OAuthClient


@pytest.fixture(autouse=True)
def _reset_oauth_rate_limit():
    """The /v1/oauth/* per-IP limiter is module-global (5/min). Tests all
    look like 127.0.0.1; reset the counter between tests so back-to-back
    runs don't trip the limiter."""
    from voltari_gateway.auth.rate_limit import reset_all

    reset_all()
    yield
    reset_all()


def _pkce() -> tuple[str, str]:
    verifier = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQR0123456789-._~"
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


@pytest.fixture
async def oauth_token(client, db, api_key_fixture):
    """Run the full OAuth dance and return ``(access_token, refresh_token)``.

    The client requests ONLY ``chat.read`` so we can also assert that
    /v1/messages — which needs ``messages.read`` — is rejected.
    """
    db.add(
        OAuthClient(
            client_id="studio",
            name="Brikko Studio",
            redirect_uris=["http://localhost:3737/callback"],
            allowed_scopes=["chat.read", "messages.read", "embeddings.read", "audio.read"],
            is_first_party=True,
        )
    )
    await db.commit()

    sess, _ = create_access_token(api_key_fixture.user.id, api_key_fixture.account.id)
    cookies = {"vlt_access": sess}

    verifier, challenge = _pkce()
    r1 = await client.get(
        "/v1/oauth/authorize",
        params={
            "response_type": "code",
            "client_id": "studio",
            "redirect_uri": "http://localhost:3737/callback",
            "scope": "chat.read",
            "state": "s",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        },
        cookies=cookies,
    )
    assert r1.status_code == 200, r1.text
    csrf = re.search(r'name="csrf_token" value="([^"]+)"', r1.text).group(1)

    r2 = await client.post(
        "/v1/oauth/authorize",
        data={
            "client_id": "studio",
            "redirect_uri": "http://localhost:3737/callback",
            "scope": "chat.read",
            "state": "s",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "csrf_token": csrf,
            "decision": "approve",
        },
        cookies={**cookies, "vlt_csrf": csrf},
        headers={"X-CSRF-Token": csrf},
        follow_redirects=False,
    )
    assert r2.status_code in (302, 303), r2.text
    code = parse_qs(urlparse(r2.headers["location"]).query)["code"][0]

    r3 = await client.post(
        "/v1/oauth/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": "http://localhost:3737/callback",
            "client_id": "studio",
            "code_verifier": verifier,
        },
    )
    assert r3.status_code == 200, r3.text
    body = r3.json()
    return body["access_token"], body["refresh_token"]


@pytest.mark.asyncio
async def test_oauth_token_can_call_chat_completions(client, oauth_token):
    """Scoped OAuth token (chat.read) works on /v1/chat/completions."""
    access, _ = oauth_token
    r = await client.post(
        "/v1/chat/completions",
        json={
            "model": "gpt-5.4-mini",
            "messages": [{"role": "user", "content": "hello"}],
        },
        headers={"Authorization": f"Bearer {access}"},
    )
    assert r.status_code == 200, r.text
    assert "choices" in r.json()


@pytest.mark.asyncio
async def test_oauth_token_without_scope_403_on_messages(client, oauth_token):
    """The fixture requested only chat.read; /v1/messages requires messages.read."""
    access, _ = oauth_token
    r = await client.post(
        "/v1/messages",
        json={
            "model": "claude-sonnet-4-6",
            "messages": [{"role": "user", "content": "hi"}],
            "max_tokens": 8,
        },
        headers={"Authorization": f"Bearer {access}"},
    )
    assert r.status_code == 403, r.text
    assert r.json()["error"]["code"] == "insufficient_scope"


@pytest.mark.asyncio
async def test_existing_api_key_still_works(client, api_key_fixture):
    """Regression guard: pre-existing sk-vlt-/sk-brk- API keys MUST keep working."""
    r = await client.post(
        "/v1/chat/completions",
        json={
            "model": "gpt-5.4-mini",
            "messages": [{"role": "user", "content": "hi"}],
        },
        headers=api_key_fixture.auth_header,
    )
    assert r.status_code == 200, r.text


@pytest.mark.asyncio
async def test_oauth_token_with_models_scope_can_list_models(client, db, api_key_fixture):
    """Studio token with models.read can call GET /v1/models."""
    db.add(
        OAuthClient(
            client_id="studio",
            name="Brikko Studio",
            redirect_uris=["http://localhost:3737/callback"],
            allowed_scopes=[
                "chat.read",
                "messages.read",
                "embeddings.read",
                "audio.read",
                "models.read",
            ],
            is_first_party=True,
        )
    )
    await db.commit()
    sess, _ = create_access_token(api_key_fixture.user.id, api_key_fixture.account.id)
    cookies = {"vlt_access": sess}

    v, c = _pkce()
    r1 = await client.get(
        "/v1/oauth/authorize",
        params={
            "response_type": "code",
            "client_id": "studio",
            "redirect_uri": "http://localhost:3737/callback",
            "scope": "models.read",
            "state": "s",
            "code_challenge": c,
            "code_challenge_method": "S256",
        },
        cookies=cookies,
    )
    csrf = re.search(r'name="csrf_token" value="([^"]+)"', r1.text).group(1)
    r2 = await client.post(
        "/v1/oauth/authorize",
        data={
            "client_id": "studio",
            "redirect_uri": "http://localhost:3737/callback",
            "scope": "models.read",
            "state": "s",
            "code_challenge": c,
            "code_challenge_method": "S256",
            "csrf_token": csrf,
            "decision": "approve",
        },
        cookies={**cookies, "vlt_csrf": csrf},
        headers={"X-CSRF-Token": csrf},
        follow_redirects=False,
    )
    code = parse_qs(urlparse(r2.headers["location"]).query)["code"][0]
    r3 = await client.post(
        "/v1/oauth/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": "http://localhost:3737/callback",
            "client_id": "studio",
            "code_verifier": v,
        },
    )
    access = r3.json()["access_token"]

    r = await client.get("/v1/models", headers={"Authorization": f"Bearer {access}"})
    assert r.status_code == 200
    assert r.json()["object"] == "list"
