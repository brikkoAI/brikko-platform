"""Scraper service entrypoint.

Endpoints
---------
* ``GET /healthz`` — liveness probe (always 200 if the process is up).
* ``POST /scrape/{provider}`` — run the configured Playwright scraper for
  ``provider`` (one of openai/anthropic/together).  Returns the parsed
  balance.  503 with a short reason if cookies expired / DOM changed /
  scrape timed out.
* ``POST /cookies/{provider}`` — accepts an *already encrypted* Fernet blob
  from the gateway and writes it to the shared volume.  We never see
  plaintext cookies on the wire here.

All non-health endpoints require ``X-Internal-Token`` matching
``SCRAPER_INTERNAL_TOKEN``.
"""

from __future__ import annotations

import secrets
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated, Literal

import structlog
from cryptography.fernet import InvalidToken
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from playwright.async_api import Page
from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from pydantic import BaseModel, Field

from scraper.browser import BrowserPool
from scraper.config import get_settings
from scraper.crypto import CookieCipher
from scraper.providers import (
    CookieExpiredError,
    DomChangedError,
    ScrapeError,
    ScrapeResult,
)
from scraper.providers import anthropic as anthropic_scraper
from scraper.providers import openai as openai_scraper
from scraper.providers import together as together_scraper
from scraper.storage import CookieStore

log = structlog.get_logger("brikko.scraper")

# Provider registry — maps the URL slug to its scrape entrypoint.
ScrapeFn = Callable[[Page], Awaitable[ScrapeResult]]

PROVIDER_SCRAPERS: dict[str, ScrapeFn] = {
    "openai": openai_scraper.scrape,
    "anthropic": anthropic_scraper.scrape,
    "together": together_scraper.scrape,
}


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    structlog.configure(
        processors=[
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.add_log_level,
            structlog.processors.JSONRenderer(),
        ]
    )
    settings = get_settings()
    log.info("scraper_startup", port=settings.port, cookies_dir=str(settings.cookies_dir))

    # Cipher: we require ENCRYPTION_KEY to be non-empty; otherwise we can't
    # decrypt anything we'd store.  Raising here fails the container start.
    try:
        cipher = CookieCipher(settings.encryption_key.get_secret_value())
    except ValueError as exc:
        log.error("scraper_cipher_init_failed", error=str(exc))
        raise

    store = CookieStore(settings.cookies_dir)

    pool = BrowserPool(settings)
    await pool.start()
    log.info("scraper_browser_pool_ready")

    app.state.settings = settings
    app.state.cipher = cipher
    app.state.cookie_store = store
    app.state.pool = pool

    try:
        yield
    finally:
        log.info("scraper_shutdown_started")
        await pool.stop()
        log.info("scraper_shutdown_complete")


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class HealthResponse(BaseModel):
    status: Literal["ok"]
    version: str


class ScrapeResponse(BaseModel):
    provider: str
    balance_native: Decimal
    currency: str
    raw: str = Field(..., description="Trimmed raw text we parsed for the balance.")
    fetched_at: datetime


class CookieUploadResponse(BaseModel):
    provider: str
    size_bytes: int
    stored_at: datetime


# ---------------------------------------------------------------------------
# Auth dep
# ---------------------------------------------------------------------------


def _require_internal_token(
    request: Request,
    x_internal_token: Annotated[str | None, Header(alias="X-Internal-Token")] = None,
) -> None:
    expected = request.app.state.settings.internal_token.get_secret_value()
    if not x_internal_token or not secrets.compare_digest(x_internal_token, expected):
        # No body — defence in depth, no oracle for token shape.
        raise HTTPException(status_code=401, detail="invalid internal token")


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------


def create_app() -> FastAPI:
    from scraper import __version__

    app = FastAPI(
        title="Brikko Balance Scraper",
        version=__version__,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=_lifespan,
    )

    @app.get("/healthz", response_model=HealthResponse)
    async def healthz() -> HealthResponse:
        return HealthResponse(status="ok", version=__version__)

    @app.post(
        "/scrape/{provider}",
        response_model=ScrapeResponse,
        dependencies=[Depends(_require_internal_token)],
    )
    async def scrape_provider(provider: str, request: Request) -> ScrapeResponse:
        if provider not in PROVIDER_SCRAPERS:
            raise HTTPException(status_code=404, detail=f"unknown provider {provider!r}")

        store: CookieStore = request.app.state.cookie_store
        cipher: CookieCipher = request.app.state.cipher
        pool: BrowserPool = request.app.state.pool

        if not store.exists(provider):
            raise HTTPException(
                status_code=503,
                detail="cookies not uploaded",
            )

        try:
            ciphertext = store.load(provider)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=503, detail="cookies not uploaded") from exc

        try:
            plaintext = cipher.decrypt(ciphertext)
        except InvalidToken as exc:
            log.error("scraper_cookie_decrypt_failed", provider=provider)
            raise HTTPException(
                status_code=503, detail="cookie decrypt failed (wrong key?)"
            ) from exc

        scrape_fn = PROVIDER_SCRAPERS[provider]
        t0 = time.monotonic()
        try:
            async with pool.page_with_cookies(plaintext) as page:
                result = await scrape_fn(page)
        except CookieExpiredError as exc:
            log.warning("scraper_cookie_expired", provider=provider, error=str(exc))
            raise HTTPException(status_code=503, detail="cookie expired or DOM changed") from exc
        except DomChangedError as exc:
            log.warning("scraper_dom_changed", provider=provider, error=str(exc))
            raise HTTPException(status_code=503, detail=f"dom_changed: {exc}") from exc
        except ScrapeError as exc:
            log.error("scraper_scrape_error", provider=provider, error=str(exc))
            raise HTTPException(status_code=503, detail=f"scrape_failed: {exc}") from exc
        except (TimeoutError, PlaywrightTimeoutError) as exc:
            log.warning("scraper_timeout", provider=provider, error=str(exc))
            raise HTTPException(status_code=503, detail="scrape timeout") from exc
        except ValueError as exc:
            # invalid cookies JSON shape, etc.
            log.error("scraper_invalid_cookies", provider=provider, error=str(exc))
            raise HTTPException(status_code=503, detail=f"invalid cookies blob: {exc}") from exc

        elapsed_ms = int((time.monotonic() - t0) * 1000)
        log.info(
            "scraper_scrape_ok",
            provider=provider,
            currency=result.currency,
            elapsed_ms=elapsed_ms,
        )
        return ScrapeResponse(
            provider=provider,
            balance_native=result.balance_native,
            currency=result.currency,
            raw=result.raw_text,
            fetched_at=datetime.now(UTC),
        )

    @app.post(
        "/cookies/{provider}",
        response_model=CookieUploadResponse,
        dependencies=[Depends(_require_internal_token)],
    )
    async def upload_cookies(
        provider: str,
        request: Request,
    ) -> CookieUploadResponse:
        if provider not in PROVIDER_SCRAPERS:
            raise HTTPException(status_code=404, detail=f"unknown provider {provider!r}")
        ciphertext = await request.body()
        if not ciphertext:
            raise HTTPException(status_code=400, detail="empty body")
        # Hard cap: a Playwright cookies JSON for one site is ~5-10 KB.
        # 64 KB is generous and prevents a misuse-as-storage scenario.
        if len(ciphertext) > 65_536:
            raise HTTPException(status_code=413, detail="cookies blob too large")

        store: CookieStore = request.app.state.cookie_store
        cipher: CookieCipher = request.app.state.cipher

        # Round-trip decrypt as a smoke test — we'd rather fail the upload
        # than accept a blob we can't later decrypt.
        try:
            cipher.decrypt(ciphertext)
        except InvalidToken as exc:
            raise HTTPException(
                status_code=400,
                detail="ciphertext does not decrypt with our ENCRYPTION_KEY",
            ) from exc

        try:
            size = store.store(provider, ciphertext)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        log.info("scraper_cookies_uploaded", provider=provider, size_bytes=size)
        return CookieUploadResponse(
            provider=provider,
            size_bytes=size,
            stored_at=datetime.now(UTC),
        )

    @app.exception_handler(HTTPException)
    async def _http_exception_handler(_req: Request, exc: HTTPException) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {
                    "status": exc.status_code,
                    "detail": exc.detail,
                }
            },
        )

    return app


app = create_app()
