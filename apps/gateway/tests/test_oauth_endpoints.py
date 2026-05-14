"""Integration tests for /v1/oauth/* endpoints."""

from __future__ import annotations

import base64
import hashlib
import re
from urllib.parse import parse_qs, urlparse

import pytest

from voltari_gateway.auth.session import create_access_token
from voltari_gateway.db.models import OAuthClient


def _make_pkce() -> tuple[str, str]:
    verifier = "a" * 64
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


@pytest.fixture(autouse=True)
def _reset_oauth_rate_limit():
    # The OAuth limiter is a module-level singleton with a 5/min cap; tests
    # all call from 127.0.0.1 and would trip it after the first few. Reset
    # before each test so the counter starts clean.
    from voltari_gateway.auth.rate_limit import reset_all

    reset_all()
    yield
    reset_all()


@pytest.fixture
async def studio_client(db):
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


@pytest.fixture
async def session_cookie(api_key_fixture):
    token, _ = create_access_token(api_key_fixture.user.id, api_key_fixture.account.id)
    return {"vlt_access": token}


@pytest.mark.asyncio
async def test_authorize_get_renders_consent(client, studio_client, session_cookie):
    _, challenge = _make_pkce()
    r = await client.get(
        "/v1/oauth/authorize",
        params={
            "client_id": "studio",
            "redirect_uri": "http://localhost:3737/callback",
            "scope": "chat.read messages.read",
            "state": "rnd-state-xyz",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "response_type": "code",
        },
        cookies=session_cookie,
    )
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    body = r.text
    assert "Brikko Studio" in body
    assert "chat.read" in body
    assert 'name="csrf_token"' in body


@pytest.mark.asyncio
async def test_authorize_get_without_session_returns_401(client, studio_client):
    _, challenge = _make_pkce()
    r = await client.get(
        "/v1/oauth/authorize",
        params={
            "client_id": "studio",
            "redirect_uri": "http://localhost:3737/callback",
            "scope": "chat.read",
            "state": "s",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "response_type": "code",
        },
    )
    # Studio onboarding flow expects 401 + ``X-Login-Required: 1`` so the
    # CLI can pop a browser to /login then resume.
    assert r.status_code == 401
    assert r.headers.get("x-login-required") == "1"


@pytest.mark.asyncio
async def test_authorize_unknown_client_400(client, session_cookie):
    _, challenge = _make_pkce()
    r = await client.get(
        "/v1/oauth/authorize",
        params={
            "client_id": "ghost",
            "redirect_uri": "http://localhost:3737/callback",
            "scope": "chat.read",
            "state": "s",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "response_type": "code",
        },
        cookies=session_cookie,
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "invalid_client"


@pytest.mark.asyncio
async def test_authorize_unknown_redirect_400(client, studio_client, session_cookie):
    _, challenge = _make_pkce()
    r = await client.get(
        "/v1/oauth/authorize",
        params={
            "client_id": "studio",
            "redirect_uri": "http://attacker.example/cb",
            "scope": "chat.read",
            "state": "s",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "response_type": "code",
        },
        cookies=session_cookie,
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "invalid_redirect_uri"


@pytest.mark.asyncio
async def test_authorize_plain_method_rejected(client, studio_client, session_cookie):
    r = await client.get(
        "/v1/oauth/authorize",
        params={
            "client_id": "studio",
            "redirect_uri": "http://localhost:3737/callback",
            "scope": "chat.read",
            "state": "s",
            "code_challenge": "anything",
            "code_challenge_method": "plain",
            "response_type": "code",
        },
        cookies=session_cookie,
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "invalid_request"


@pytest.mark.asyncio
async def test_authorize_post_approve_redirects_with_code(
    client, studio_client, session_cookie, api_key_fixture
):
    verifier, challenge = _make_pkce()
    # First GET to obtain CSRF token from the rendered page.
    r1 = await client.get(
        "/v1/oauth/authorize",
        params={
            "client_id": "studio",
            "redirect_uri": "http://localhost:3737/callback",
            "scope": "chat.read",
            "state": "rnd",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "response_type": "code",
        },
        cookies=session_cookie,
    )
    csrf = re.search(r'name="csrf_token" value="([^"]+)"', r1.text).group(1)

    r2 = await client.post(
        "/v1/oauth/authorize",
        data={
            "client_id": "studio",
            "redirect_uri": "http://localhost:3737/callback",
            "scope": "chat.read",
            "state": "rnd",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "csrf_token": csrf,
            "decision": "approve",
        },
        cookies={**session_cookie, "vlt_csrf": csrf},
        headers={"X-CSRF-Token": csrf},
        follow_redirects=False,
    )
    assert r2.status_code in (302, 303)
    location = r2.headers["location"]
    parsed = urlparse(location)
    qs = parse_qs(parsed.query)
    assert "code" in qs
    assert qs["state"] == ["rnd"]


@pytest.mark.asyncio
async def test_authorize_post_deny_redirects_with_error(client, studio_client, session_cookie):
    _, challenge = _make_pkce()
    r1 = await client.get(
        "/v1/oauth/authorize",
        params={
            "client_id": "studio",
            "redirect_uri": "http://localhost:3737/callback",
            "scope": "chat.read",
            "state": "rnd",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "response_type": "code",
        },
        cookies=session_cookie,
    )
    csrf = re.search(r'name="csrf_token" value="([^"]+)"', r1.text).group(1)

    r2 = await client.post(
        "/v1/oauth/authorize",
        data={
            "client_id": "studio",
            "redirect_uri": "http://localhost:3737/callback",
            "scope": "chat.read",
            "state": "rnd",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "csrf_token": csrf,
            "decision": "deny",
        },
        cookies={**session_cookie, "vlt_csrf": csrf},
        headers={"X-CSRF-Token": csrf},
        follow_redirects=False,
    )
    assert r2.status_code in (302, 303)
    qs = parse_qs(urlparse(r2.headers["location"]).query)
    assert qs["error"] == ["access_denied"]


@pytest.mark.asyncio
async def test_token_exchange_returns_tokens(
    client, studio_client, session_cookie, api_key_fixture
):
    verifier, challenge = _make_pkce()
    # GET + POST authorize to acquire a code
    r1 = await client.get(
        "/v1/oauth/authorize",
        params={
            "client_id": "studio",
            "redirect_uri": "http://localhost:3737/callback",
            "scope": "chat.read",
            "state": "s",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "response_type": "code",
        },
        cookies=session_cookie,
    )
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
        cookies={**session_cookie, "vlt_csrf": csrf},
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
            "code_verifier": verifier,
        },
    )
    assert r3.status_code == 200
    body = r3.json()
    assert body["token_type"] == "Bearer"
    assert "access_token" in body
    assert "refresh_token" in body
    assert body["expires_in"] == 900  # 15 min default
    assert body["scope"] == "chat.read"


@pytest.mark.asyncio
async def test_token_replay_rejected(client, studio_client, session_cookie, api_key_fixture):
    verifier, challenge = _make_pkce()
    r1 = await client.get(
        "/v1/oauth/authorize",
        params={
            "client_id": "studio",
            "redirect_uri": "http://localhost:3737/callback",
            "scope": "chat.read",
            "state": "s",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "response_type": "code",
        },
        cookies=session_cookie,
    )
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
        cookies={**session_cookie, "vlt_csrf": csrf},
        headers={"X-CSRF-Token": csrf},
        follow_redirects=False,
    )
    code = parse_qs(urlparse(r2.headers["location"]).query)["code"][0]

    body = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": "http://localhost:3737/callback",
        "client_id": "studio",
        "code_verifier": verifier,
    }
    ok = await client.post("/v1/oauth/token", data=body)
    assert ok.status_code == 200
    replay = await client.post("/v1/oauth/token", data=body)
    assert replay.status_code == 400
    assert replay.json()["error"] == "invalid_grant"


@pytest.mark.asyncio
async def test_token_wrong_pkce_rejected(client, studio_client, session_cookie):
    verifier, challenge = _make_pkce()
    r1 = await client.get(
        "/v1/oauth/authorize",
        params={
            "client_id": "studio",
            "redirect_uri": "http://localhost:3737/callback",
            "scope": "chat.read",
            "state": "s",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "response_type": "code",
        },
        cookies=session_cookie,
    )
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
        cookies={**session_cookie, "vlt_csrf": csrf},
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
            "code_verifier": "WRONG" * 20,
        },
    )
    assert r3.status_code == 400
    assert r3.json()["error"] == "invalid_grant"


@pytest.mark.asyncio
async def test_refresh_token_grant(client, studio_client, session_cookie):
    import asyncio

    verifier, challenge = _make_pkce()
    r1 = await client.get(
        "/v1/oauth/authorize",
        params={
            "client_id": "studio",
            "redirect_uri": "http://localhost:3737/callback",
            "scope": "chat.read",
            "state": "s",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "response_type": "code",
        },
        cookies=session_cookie,
    )
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
        cookies={**session_cookie, "vlt_csrf": csrf},
        headers={"X-CSRF-Token": csrf},
        follow_redirects=False,
    )
    code = parse_qs(urlparse(r2.headers["location"]).query)["code"][0]
    initial = await client.post(
        "/v1/oauth/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": "http://localhost:3737/callback",
            "client_id": "studio",
            "code_verifier": verifier,
        },
    )
    refresh_token = initial.json()["refresh_token"]

    # JWT ``iat`` has 1-second resolution; without this nap the refreshed
    # access token would have an identical payload/signature to the initial
    # one. Production clients refresh long after the original was issued,
    # so the same-second collision is purely a test-timing artefact.
    await asyncio.sleep(1.1)

    r4 = await client.post(
        "/v1/oauth/token",
        data={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": "studio",
        },
    )
    assert r4.status_code == 200
    body = r4.json()
    assert body["scope"] == "chat.read"
    assert body["access_token"] != initial.json()["access_token"]


@pytest.mark.asyncio
async def test_revoke_endpoint_returns_200(client, studio_client, session_cookie):
    verifier, challenge = _make_pkce()
    r1 = await client.get(
        "/v1/oauth/authorize",
        params={
            "client_id": "studio",
            "redirect_uri": "http://localhost:3737/callback",
            "scope": "chat.read",
            "state": "s",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "response_type": "code",
        },
        cookies=session_cookie,
    )
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
        cookies={**session_cookie, "vlt_csrf": csrf},
        headers={"X-CSRF-Token": csrf},
        follow_redirects=False,
    )
    code = parse_qs(urlparse(r2.headers["location"]).query)["code"][0]
    tok = await client.post(
        "/v1/oauth/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": "http://localhost:3737/callback",
            "client_id": "studio",
            "code_verifier": verifier,
        },
    )
    refresh_token = tok.json()["refresh_token"]

    r = await client.post(
        "/v1/oauth/revoke",
        data={"token": refresh_token, "client_id": "studio"},
    )
    # RFC 7009 §2.2 — revoke always returns 200, even on unknown tokens.
    assert r.status_code == 200
