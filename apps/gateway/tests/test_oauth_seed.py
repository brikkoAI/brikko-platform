"""Idempotent seed of the ``studio`` first-party OAuth client."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from voltari_gateway.db.models import OAuthClient
from voltari_gateway.db.oauth_seed import seed_oauth_clients


@pytest.mark.asyncio
async def test_seed_creates_studio_client(db, session_factory):
    await seed_oauth_clients(session_factory)

    rows = (await db.execute(select(OAuthClient))).scalars().all()
    assert len(rows) == 1
    studio = rows[0]
    assert studio.client_id == "studio"
    assert studio.is_first_party is True
    assert "http://localhost:3737/callback" in studio.redirect_uris
    assert "chat.read" in studio.allowed_scopes
    assert "messages.read" in studio.allowed_scopes
    assert "embeddings.read" in studio.allowed_scopes
    assert "audio.read" in studio.allowed_scopes
    assert "models.read" in studio.allowed_scopes


@pytest.mark.asyncio
async def test_seed_is_idempotent(db, session_factory):
    await seed_oauth_clients(session_factory)
    await seed_oauth_clients(session_factory)
    await seed_oauth_clients(session_factory)

    rows = (await db.execute(select(OAuthClient))).scalars().all()
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_seed_updates_redirect_uris_when_env_changes(db, session_factory, monkeypatch):
    from voltari_gateway.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv(
        "OAUTH_STUDIO_REDIRECT_URIS",
        "http://localhost:3737/callback,http://localhost:9999/cb",
    )
    await seed_oauth_clients(session_factory)

    studio = (
        await db.execute(select(OAuthClient).where(OAuthClient.client_id == "studio"))
    ).scalar_one()
    assert "http://localhost:9999/cb" in studio.redirect_uris

    get_settings.cache_clear()
