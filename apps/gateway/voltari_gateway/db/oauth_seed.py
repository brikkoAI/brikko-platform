"""Idempotent seed of first-party OAuth clients on app startup.

Why a seed instead of a migration row?
--------------------------------------

The list of allowed redirect URIs varies per environment (dev =
``localhost:3737``, prod = ``https://studio.brikko.ru/oauth-cb``) and we
don't want a migration per environment. The seed reads the current
``Settings`` object on every boot; an env-var change is reflected after
the next restart.

The seed is safe to run on every boot — it does an UPSERT keyed on
``client_id``. Existing tests share a fresh in-memory DB, so the seed
is a no-op there unless the test calls ``seed_oauth_clients`` itself.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from voltari_gateway.config import get_settings
from voltari_gateway.db.models import OAuthClient
from voltari_gateway.utils.logging import get_logger

log = get_logger(__name__)

STUDIO_CLIENT_ID = "studio"
STUDIO_CLIENT_NAME = "Brikko Studio"
STUDIO_ALLOWED_SCOPES = [
    "chat.read",
    "messages.read",
    "embeddings.read",
    "audio.read",
    "models.read",
]


def _parse_uri_list(raw: str) -> list[str]:
    """Comma-separated → list, trimmed, deduplicated, order-preserving."""
    seen: set[str] = set()
    out: list[str] = []
    for u in raw.split(","):
        u = u.strip()
        if not u or u in seen:
            continue
        seen.add(u)
        out.append(u)
    return out


async def _upsert_studio(db: AsyncSession, redirect_uris: list[str]) -> None:
    existing = (
        await db.execute(select(OAuthClient).where(OAuthClient.client_id == STUDIO_CLIENT_ID))
    ).scalar_one_or_none()

    if existing is None:
        db.add(
            OAuthClient(
                client_id=STUDIO_CLIENT_ID,
                name=STUDIO_CLIENT_NAME,
                redirect_uris=redirect_uris,
                allowed_scopes=STUDIO_ALLOWED_SCOPES,
                is_first_party=True,
            )
        )
        log.info("oauth_seed_created", client_id=STUDIO_CLIENT_ID, redirect_uris=redirect_uris)
        return

    changed = False
    if existing.redirect_uris != redirect_uris:
        existing.redirect_uris = redirect_uris
        changed = True
    if existing.allowed_scopes != STUDIO_ALLOWED_SCOPES:
        existing.allowed_scopes = STUDIO_ALLOWED_SCOPES
        changed = True
    if changed:
        log.info("oauth_seed_updated", client_id=STUDIO_CLIENT_ID, redirect_uris=redirect_uris)


async def seed_oauth_clients(session_factory: async_sessionmaker[AsyncSession]) -> None:
    """Ensure the canonical first-party clients exist. Idempotent."""
    settings = get_settings()
    redirect_uris = _parse_uri_list(settings.oauth_studio_redirect_uris)
    if not redirect_uris:
        log.warning("oauth_seed_no_redirect_uris", env_value=settings.oauth_studio_redirect_uris)
        return
    async with session_factory() as session:
        await _upsert_studio(session, redirect_uris)
        await session.commit()
