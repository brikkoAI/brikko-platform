"""Scope enum + dependency factory."""

from __future__ import annotations

import pytest

from voltari_gateway.auth.oauth_scopes import OAuthScope, parse_scope_string, serialize_scopes
from voltari_gateway.auth.oauth_tokens import create_oauth_access_token


def test_scope_enum_values_match_spec():
    assert OAuthScope.CHAT_READ.value == "chat.read"
    assert OAuthScope.MESSAGES_READ.value == "messages.read"
    assert OAuthScope.EMBEDDINGS_READ.value == "embeddings.read"
    assert OAuthScope.AUDIO_READ.value == "audio.read"


def test_parse_scope_string_handles_pluses_and_spaces():
    assert parse_scope_string("chat.read messages.read") == [
        OAuthScope.CHAT_READ,
        OAuthScope.MESSAGES_READ,
    ]
    assert parse_scope_string("chat.read+audio.read") == [
        OAuthScope.CHAT_READ,
        OAuthScope.AUDIO_READ,
    ]
    assert parse_scope_string("") == []


def test_parse_scope_string_drops_unknown_silently():
    """Unknown scopes are dropped — strict rejection happens at /authorize."""
    assert parse_scope_string("chat.read bogus.write") == [OAuthScope.CHAT_READ]


def test_serialize_round_trip():
    scopes = [OAuthScope.CHAT_READ, OAuthScope.MESSAGES_READ]
    assert parse_scope_string(serialize_scopes(scopes)) == scopes


@pytest.mark.asyncio
async def test_dependency_passes_when_scope_present(client, db, api_key_fixture, monkeypatch):
    """Token with chat.read passes the chat dep; token without it gets 403."""
    # We test by minting a token and checking the dep raw — endpoint wiring is Task 11.

    from voltari_gateway.auth.oauth_dependency import require_oauth_scope

    token, _exp, _ = create_oauth_access_token(
        user_id=api_key_fixture.user.id,
        account_id=api_key_fixture.account.id,
        client_id="studio",
        scopes=["chat.read"],
    )

    dep = require_oauth_scope(OAuthScope.CHAT_READ)

    class _StubReq:
        state = type("S", (), {})()
        headers = {"authorization": f"Bearer {token}"}  # noqa: RUF012

    principal = await dep(authorization=f"Bearer {token}", db=db)  # type: ignore[arg-type]
    assert principal.scopes == [OAuthScope.CHAT_READ]
    assert principal.account_id == api_key_fixture.account.id


@pytest.mark.asyncio
async def test_dependency_rejects_when_scope_missing(db, api_key_fixture):
    from voltari_gateway.auth.oauth_dependency import require_oauth_scope
    from voltari_gateway.utils.errors import GatewayError

    token, _exp, _ = create_oauth_access_token(
        user_id=api_key_fixture.user.id,
        account_id=api_key_fixture.account.id,
        client_id="studio",
        scopes=["audio.read"],
    )
    dep = require_oauth_scope(OAuthScope.CHAT_READ)
    with pytest.raises(GatewayError) as exc_info:
        await dep(authorization=f"Bearer {token}", db=db)  # type: ignore[arg-type]
    assert exc_info.value.status_code == 403
    # GatewayError stores OpenAI-envelope `code` as `error_code` on the exception
    # instance (see voltari_gateway.utils.errors.GatewayError.__init__).
    assert exc_info.value.error_code == "insufficient_scope"


@pytest.mark.asyncio
async def test_dependency_rejects_dashboard_token(db, api_key_fixture):
    """A dashboard JWT must not be accepted as an OAuth token."""
    from voltari_gateway.auth.oauth_dependency import require_oauth_scope
    from voltari_gateway.auth.session import create_access_token
    from voltari_gateway.utils.errors import GatewayError

    dashboard_token, _ = create_access_token(api_key_fixture.user.id, api_key_fixture.account.id)
    dep = require_oauth_scope(OAuthScope.CHAT_READ)
    with pytest.raises(GatewayError) as exc_info:
        await dep(authorization=f"Bearer {dashboard_token}", db=db)  # type: ignore[arg-type]
    assert exc_info.value.status_code == 401
