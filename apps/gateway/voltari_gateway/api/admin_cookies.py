"""Admin cookie-upload endpoint — Sprint 14 Phase 2 (2026-05-11).

Routes (под ``/v1/account/admin``):

* ``GET   /provider_cookies``               — список (provider, last_uploaded, last_valid).
* ``POST  /provider_cookies/{provider}``    — upload plaintext cookies JSON.
* ``DELETE /provider_cookies/{provider}``   — remove cookies (e.g. revoke).

Все три закрыты ``require_admin``.  Audit-log запись пишется на upload и
delete.  Уплоды только для провайдеров из ``SCRAPE_ADAPTER_PROVIDERS``
(openai / anthropic / together).
"""

from __future__ import annotations

import contextlib
import json
import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from voltari_gateway.api.admin_status import require_admin
from voltari_gateway.auth.audit import write_audit
from voltari_gateway.auth.session_middleware import SessionPrincipal
from voltari_gateway.balance_monitor.cookie_upload import (
    CipherUnavailableError,
    CookieUploadError,
    InvalidCookieJsonError,
    ScraperUnavailableError,
    encrypt_cookies,
    post_to_scraper,
)
from voltari_gateway.balance_monitor.service import SCRAPE_ADAPTER_PROVIDERS
from voltari_gateway.config import get_settings
from voltari_gateway.db.models import ProviderCookies
from voltari_gateway.db.session import get_db
from voltari_gateway.utils.errors import GatewayError, invalid_request
from voltari_gateway.utils.logging import get_logger

router = APIRouter(prefix="/v1/account", tags=["admin"])
log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class ProviderCookieMeta(BaseModel):
    provider: str
    uploaded_at: datetime | None = None
    last_valid_at: datetime | None = None
    last_error: str | None = None
    size_bytes: int | None = None
    has_cookies: bool = False


class ProviderCookiesListResponse(BaseModel):
    items: list[ProviderCookieMeta]


class CookieUploadResponse(BaseModel):
    provider: str
    uploaded_at: datetime
    size_bytes: int


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _require_scrape_provider(provider: str) -> None:
    if provider not in SCRAPE_ADAPTER_PROVIDERS:
        raise invalid_request(
            f"Provider {provider!r} doesn't use cookie scraping. "
            f"Allowed: {sorted(SCRAPE_ADAPTER_PROVIDERS)}",
            param="provider",
            code="unknown_provider",
        )


async def _upsert_cookie_row(
    db: AsyncSession,
    *,
    provider: str,
    cookie_path: str,
    size_bytes: int,
    uploaded_by: uuid.UUID,
    now: datetime,
) -> ProviderCookies:
    bind = db.get_bind()
    dialect = bind.dialect.name
    base_values: dict[str, object] = {
        "provider": provider,
        "cookie_path": cookie_path,
        "size_bytes": size_bytes,
        "uploaded_by": uploaded_by,
        "uploaded_at": now,
        # last_valid_at NOT updated here — only after a successful scrape.
        # last_error reset on upload so stale state from previous attempt
        # doesn't confuse the UI.
        "last_error": None,
        "updated_at": now,
    }

    if dialect == "postgresql":
        stmt = pg_insert(ProviderCookies).values(**base_values)
        update_cols = {k: stmt.excluded[k] for k in base_values if k != "provider"}
        stmt = stmt.on_conflict_do_update(index_elements=["provider"], set_=update_cols)
        await db.execute(stmt)
    elif dialect == "sqlite":
        stmt_lite = sqlite_insert(ProviderCookies).values(**base_values)
        update_cols = {k: stmt_lite.excluded[k] for k in base_values if k != "provider"}
        stmt_lite = stmt_lite.on_conflict_do_update(index_elements=["provider"], set_=update_cols)
        await db.execute(stmt_lite)
    else:
        # Generic fallback (no dialect-specific upsert).
        existing = await db.execute(
            select(ProviderCookies).where(ProviderCookies.provider == provider)
        )
        row = existing.scalar_one_or_none()
        if row is None:
            db.add(ProviderCookies(**base_values))
        else:
            for k, v in base_values.items():
                if k == "provider":
                    continue
                setattr(row, k, v)

    await db.flush()
    fetched = (
        await db.execute(select(ProviderCookies).where(ProviderCookies.provider == provider))
    ).scalar_one()
    return fetched


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get(
    "/admin/provider_cookies",
    response_model=ProviderCookiesListResponse,
)
async def list_provider_cookies(
    _: Annotated[SessionPrincipal, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ProviderCookiesListResponse:
    """Return one row per scrape-capable provider (openai/anthropic/together)."""
    rows = (await db.execute(select(ProviderCookies))).scalars().all()
    by_provider = {row.provider: row for row in rows}
    items: list[ProviderCookieMeta] = []
    for provider in sorted(SCRAPE_ADAPTER_PROVIDERS):
        row = by_provider.get(provider)
        if row is None:
            items.append(
                ProviderCookieMeta(
                    provider=provider,
                    uploaded_at=None,
                    last_valid_at=None,
                    last_error=None,
                    size_bytes=None,
                    has_cookies=False,
                )
            )
        else:
            items.append(
                ProviderCookieMeta(
                    provider=row.provider,
                    uploaded_at=row.uploaded_at,
                    last_valid_at=row.last_valid_at,
                    last_error=row.last_error,
                    size_bytes=row.size_bytes,
                    has_cookies=True,
                )
            )
    return ProviderCookiesListResponse(items=items)


@router.post(
    "/admin/provider_cookies/{provider}",
    response_model=CookieUploadResponse,
)
async def upload_provider_cookies(
    provider: str,
    request: Request,
    principal: Annotated[SessionPrincipal, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> CookieUploadResponse:
    """Encrypt & forward to scraper.

    Request body is the plaintext Playwright cookies JSON (a list of objects
    with at least ``name`` and ``value``).  Content-Type should be
    ``application/json`` but we don't strictly require it — we read raw bytes
    from the body.
    """
    _require_scrape_provider(provider)

    plaintext = await request.body()
    if not plaintext:
        raise invalid_request("empty body", code="empty_cookies", param="body")

    settings = get_settings()
    fernet_key = settings.encryption_key.get_secret_value()

    try:
        ciphertext = encrypt_cookies(plaintext, fernet_key=fernet_key)
    except InvalidCookieJsonError as exc:
        raise invalid_request(str(exc), code="invalid_cookies_json") from exc
    except CipherUnavailableError as exc:
        raise GatewayError(
            status_code=503,
            message=str(exc),
            type="api_error",
            code="encryption_unavailable",
        ) from exc

    try:
        scraper_resp = await post_to_scraper(
            scraper_url=settings.scraper_url,
            internal_token=settings.scraper_internal_token.get_secret_value(),
            provider=provider,
            ciphertext=ciphertext,
        )
    except ScraperUnavailableError as exc:
        raise GatewayError(
            status_code=503,
            message=str(exc),
            type="api_error",
            code="scraper_unavailable",
        ) from exc
    except CookieUploadError as exc:
        raise GatewayError(
            status_code=503,
            message=str(exc),
            type="api_error",
            code="cookie_upload_failed",
        ) from exc

    now = datetime.now(UTC)
    cookie_path = f"{provider}.enc"
    size_from_scraper = int(scraper_resp.get("size_bytes") or len(ciphertext))

    row = await _upsert_cookie_row(
        db,
        provider=provider,
        cookie_path=cookie_path,
        size_bytes=size_from_scraper,
        uploaded_by=principal.user.id,
        now=now,
    )

    # Audit log (committed in the same transaction as the upsert).
    # We capture enough metadata to investigate later (size, cookie names)
    # without storing the plaintext values themselves.
    try:
        parsed = json.loads(plaintext.decode("utf-8"))
        cookie_names = [c.get("name", "?") for c in parsed if isinstance(c, dict)][:30]
    except (json.JSONDecodeError, UnicodeDecodeError):
        cookie_names = []

    with contextlib.suppress(Exception):
        await write_audit(
            db,
            user_id=principal.user.id,
            account_id=None,
            action="provider_cookies_uploaded",
            outcome="ok",
            request=request,
            meta={
                "provider": provider,
                "size_bytes": size_from_scraper,
                "cookie_names": cookie_names,
            },
            severity="warning",
        )

    await db.commit()
    log.info(
        "provider_cookies_uploaded",
        provider=provider,
        size_bytes=size_from_scraper,
        user_id=str(principal.user.id),
    )

    return CookieUploadResponse(
        provider=row.provider,
        uploaded_at=row.uploaded_at,
        size_bytes=row.size_bytes,
    )


@router.delete(
    "/admin/provider_cookies/{provider}",
    response_model=ProviderCookieMeta,
)
async def delete_provider_cookies(
    provider: str,
    request: Request,
    principal: Annotated[SessionPrincipal, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ProviderCookieMeta:
    """Remove the DB row.  The scraper-side file persists until next upload
    (we can't reach into the volume from here, and stale files don't auto-
    activate — the scrape endpoint reads the freshly-named file on demand).

    NB: the encrypted-cookies volume is intentionally append/replace-only —
    if cookies are compromised the right move is to log out of the provider
    in CEO's browser (which invalidates them server-side at OpenAI/etc),
    not to delete the local blob.
    """
    _require_scrape_provider(provider)

    row = (
        await db.execute(select(ProviderCookies).where(ProviderCookies.provider == provider))
    ).scalar_one_or_none()
    if row is None:
        # 404 envelope returned via invalid_request shaping — kept consistent
        # with the rest of the admin endpoints.
        raise invalid_request(
            f"No cookies stored for {provider!r}",
            code="not_found",
            param="provider",
        )

    await db.delete(row)
    with contextlib.suppress(Exception):
        await write_audit(
            db,
            user_id=principal.user.id,
            account_id=None,
            action="provider_cookies_deleted",
            outcome="ok",
            request=request,
            meta={"provider": provider},
            severity="warning",
        )
    await db.commit()

    return ProviderCookieMeta(
        provider=provider,
        uploaded_at=None,
        last_valid_at=None,
        last_error=None,
        size_bytes=None,
        has_cookies=False,
    )


__all__ = ["router"]
