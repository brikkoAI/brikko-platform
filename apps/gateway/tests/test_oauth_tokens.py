"""Unit tests for OAuth-specific JWT helpers."""

from __future__ import annotations

import uuid

from voltari_gateway.auth import oauth_tokens
from voltari_gateway.auth import session as dashboard_session


def test_create_and_verify_oauth_access():
    user_id = uuid.uuid4()
    account_id = uuid.uuid4()
    token, exp, _claims = oauth_tokens.create_oauth_access_token(
        user_id=user_id,
        account_id=account_id,
        client_id="studio",
        scopes=["chat.read", "messages.read"],
    )
    parsed = oauth_tokens.verify_oauth_access_token(token)
    assert parsed is not None
    assert parsed.user_id == user_id
    assert parsed.account_id == account_id
    assert parsed.client_id == "studio"
    assert parsed.scopes == ["chat.read", "messages.read"]


def test_oauth_access_token_rejected_as_dashboard():
    """An OAuth access token must NOT be accepted by the dashboard verifier."""
    token, _exp, _ = oauth_tokens.create_oauth_access_token(
        user_id=uuid.uuid4(),
        account_id=uuid.uuid4(),
        client_id="studio",
        scopes=["chat.read"],
    )
    assert dashboard_session.verify_access_token(token) is None


def test_dashboard_token_rejected_as_oauth():
    """A dashboard token must NOT be accepted by the OAuth verifier."""
    token, _exp = dashboard_session.create_access_token(uuid.uuid4(), uuid.uuid4())
    assert oauth_tokens.verify_oauth_access_token(token) is None


def test_oauth_refresh_round_trip():
    token, _exp, jti = oauth_tokens.create_oauth_refresh_token(
        user_id=uuid.uuid4(),
        client_id="studio",
        scopes=["chat.read"],
    )
    parsed = oauth_tokens.verify_oauth_refresh_token(token)
    assert parsed is not None
    assert parsed.jti == jti
    assert parsed.scopes == ["chat.read"]
    assert parsed.client_id == "studio"


def test_garbage_token_returns_none():
    assert oauth_tokens.verify_oauth_access_token("not-a-jwt") is None
    assert oauth_tokens.verify_oauth_refresh_token("not-a-jwt") is None
    assert oauth_tokens.verify_oauth_access_token("") is None
