"""End-to-end tests for /v1/account/admin/provider_cookies*.

Coverage:
* GET — без сессии 401, не-admin 403, admin 200 со списком из 3 (scrape providers).
* POST upload — без сессии 401, не-admin 403, admin 200 (с моком scraper),
  unknown provider 400, invalid JSON 400, scraper unreachable 503.
* DELETE — admin 200 после upload, 400 если нет cookies.

Scraper HTTP is mocked via respx.  Fernet encryption key is set via env
(``ENCRYPTION_KEY``) so the gateway can actually encrypt.
"""

from __future__ import annotations

import json
import uuid
from unittest.mock import patch

import pytest
import respx
from cryptography.fernet import Fernet
from pydantic import SecretStr
from sqlalchemy import select

from tests.auth.conftest import csrf_headers as _csrf_headers
from voltari_gateway.auth.password import hash_password
from voltari_gateway.config import get_settings
from voltari_gateway.db.models import (
    Account,
    AccountStatus,
    ProviderCookies,
    Tariff,
    User,
)

_PASSWORD = "correct horse battery staple"
SCRAPER_URL = "http://brikko-scraper:9100"
SCRAPER_TOKEN = "test-internal-token-32-bytes-min"


async def _seed_user(db, email: str | None = None) -> User:
    user = User(
        email=email or f"u-{uuid.uuid4().hex[:10]}@example.com",
        password_hash=hash_password(_PASSWORD),
        email_verified=True,
    )
    db.add(user)
    await db.flush()
    db.add(
        Account(
            owner_id=user.id,
            name="Acme",
            balance_kopecks=100_000,
            tariff=Tariff.PRO,
            status=AccountStatus.ACTIVE,
            store_prompts=False,
            settings={},
        )
    )
    await db.commit()
    await db.refresh(user)
    return user


async def _login(client, user) -> None:
    r = await client.post(
        "/v1/auth/login",
        json={"email": user.email, "password": _PASSWORD},
    )
    assert r.status_code == 200, r.text


def _set_settings(**overrides):
    """Patch multiple settings attrs at once.

    The autouse ``clear_settings_cache`` already monkeypatched a few env
    vars; here we override settings instance attributes for the duration
    of the test.
    """
    settings = get_settings()
    patchers = [patch.object(settings, k, v) for k, v in overrides.items()]
    return patchers


def _enter(patchers):
    for p in patchers:
        p.start()


def _exit(patchers):
    for p in patchers:
        p.stop()


def _admin_context():
    """Return a list of patchers for the most common admin-allowed setup."""
    fernet_key = Fernet.generate_key().decode("utf-8")
    return [
        patch.object(get_settings(), "admin_emails", "ceo@brikko.ru"),
        patch.object(get_settings(), "scraper_url", SCRAPER_URL),
        patch.object(get_settings(), "scraper_internal_token", SecretStr(SCRAPER_TOKEN)),
        patch.object(get_settings(), "encryption_key", SecretStr(fernet_key)),
    ]


# ---------------------------------------------------------------------------
# GET /provider_cookies
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_requires_session(client) -> None:
    r = await client.get("/v1/account/admin/provider_cookies")
    assert r.status_code in (401, 403)


@pytest.mark.asyncio
async def test_list_non_admin_403(client, db) -> None:
    user = await _seed_user(db, email="not-admin@example.com")
    await _login(client, user)
    patchers = [patch.object(get_settings(), "admin_emails", "ceo@brikko.ru")]
    _enter(patchers)
    try:
        r = await client.get("/v1/account/admin/provider_cookies")
    finally:
        _exit(patchers)
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_list_admin_returns_three_providers(client, db) -> None:
    user = await _seed_user(db, email="ceo@brikko.ru")
    await _login(client, user)
    patchers = _admin_context()
    _enter(patchers)
    try:
        r = await client.get("/v1/account/admin/provider_cookies")
    finally:
        _exit(patchers)
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    assert len(items) == 3
    providers = {it["provider"] for it in items}
    assert providers == {"openai", "anthropic", "together"}
    # All start with has_cookies=False.
    for it in items:
        assert it["has_cookies"] is False
        assert it["uploaded_at"] is None


# ---------------------------------------------------------------------------
# POST /provider_cookies/{provider}
# ---------------------------------------------------------------------------


def _good_cookies_json() -> bytes:
    return json.dumps(
        [
            {
                "name": "session",
                "value": "abc",
                "domain": "platform.openai.com",
                "path": "/",
            }
        ]
    ).encode("utf-8")


@pytest.mark.asyncio
async def test_upload_requires_session(client) -> None:
    r = await client.post(
        "/v1/account/admin/provider_cookies/openai",
        content=_good_cookies_json(),
    )
    assert r.status_code in (401, 403)


@pytest.mark.asyncio
async def test_upload_non_admin_403(client, db) -> None:
    user = await _seed_user(db, email="not-admin@example.com")
    await _login(client, user)
    headers = await _csrf_headers(client)
    patchers = _admin_context()
    _enter(patchers)
    try:
        r = await client.post(
            "/v1/account/admin/provider_cookies/openai",
            content=_good_cookies_json(),
            headers=headers,
        )
    finally:
        _exit(patchers)
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_upload_admin_unknown_provider_400(client, db) -> None:
    user = await _seed_user(db, email="ceo@brikko.ru")
    await _login(client, user)
    headers = await _csrf_headers(client)
    patchers = _admin_context()
    _enter(patchers)
    try:
        r = await client.post(
            "/v1/account/admin/provider_cookies/deepseek",
            content=_good_cookies_json(),
            headers=headers,
        )
    finally:
        _exit(patchers)
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "unknown_provider"


@pytest.mark.asyncio
async def test_upload_admin_invalid_json_400(client, db) -> None:
    user = await _seed_user(db, email="ceo@brikko.ru")
    await _login(client, user)
    headers = await _csrf_headers(client)
    patchers = _admin_context()
    _enter(patchers)
    try:
        r = await client.post(
            "/v1/account/admin/provider_cookies/openai",
            content=b"not json",
            headers=headers,
        )
    finally:
        _exit(patchers)
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "invalid_cookies_json"


@pytest.mark.asyncio
async def test_upload_admin_empty_body_400(client, db) -> None:
    user = await _seed_user(db, email="ceo@brikko.ru")
    await _login(client, user)
    headers = await _csrf_headers(client)
    patchers = _admin_context()
    _enter(patchers)
    try:
        r = await client.post(
            "/v1/account/admin/provider_cookies/openai",
            content=b"",
            headers=headers,
        )
    finally:
        _exit(patchers)
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_upload_admin_scraper_unreachable_503(client, db) -> None:
    user = await _seed_user(db, email="ceo@brikko.ru")
    await _login(client, user)
    headers = await _csrf_headers(client)
    patchers = _admin_context()
    _enter(patchers)
    try:
        with respx.mock() as mock:
            mock.post(f"{SCRAPER_URL}/cookies/openai").respond(503, text="unavailable")
            r = await client.post(
                "/v1/account/admin/provider_cookies/openai",
                content=_good_cookies_json(),
                headers=headers,
            )
    finally:
        _exit(patchers)
    assert r.status_code == 503
    assert r.json()["error"]["code"] == "scraper_unavailable"


@pytest.mark.asyncio
async def test_upload_admin_happy_path(client, db) -> None:
    user = await _seed_user(db, email="ceo@brikko.ru")
    await _login(client, user)
    headers = await _csrf_headers(client)
    patchers = _admin_context()
    _enter(patchers)
    try:
        with respx.mock(assert_all_called=True) as mock:
            mock.post(f"{SCRAPER_URL}/cookies/openai").respond(
                200,
                json={
                    "provider": "openai",
                    "size_bytes": 128,
                    "stored_at": "2026-05-11T12:00:00Z",
                },
            )
            r = await client.post(
                "/v1/account/admin/provider_cookies/openai",
                content=_good_cookies_json(),
                headers=headers,
            )
    finally:
        _exit(patchers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["provider"] == "openai"
    assert body["size_bytes"] == 128

    # DB row written.
    row = (
        await db.execute(select(ProviderCookies).where(ProviderCookies.provider == "openai"))
    ).scalar_one()
    assert row.cookie_path == "openai.enc"
    assert row.size_bytes == 128
    assert row.uploaded_by == user.id


@pytest.mark.asyncio
async def test_upload_then_list_shows_metadata(client, db) -> None:
    user = await _seed_user(db, email="ceo@brikko.ru")
    await _login(client, user)
    headers = await _csrf_headers(client)
    patchers = _admin_context()
    _enter(patchers)
    try:
        with respx.mock() as mock:
            mock.post(f"{SCRAPER_URL}/cookies/anthropic").respond(
                200,
                json={
                    "provider": "anthropic",
                    "size_bytes": 200,
                    "stored_at": "2026-05-11T12:00:00Z",
                },
            )
            r1 = await client.post(
                "/v1/account/admin/provider_cookies/anthropic",
                content=_good_cookies_json(),
                headers=headers,
            )
            assert r1.status_code == 200, r1.text
        r2 = await client.get("/v1/account/admin/provider_cookies")
    finally:
        _exit(patchers)
    assert r2.status_code == 200
    by_provider = {it["provider"]: it for it in r2.json()["items"]}
    anthropic = by_provider["anthropic"]
    assert anthropic["has_cookies"] is True
    assert anthropic["size_bytes"] == 200
    assert anthropic["uploaded_at"] is not None
    # Other providers still empty.
    assert by_provider["openai"]["has_cookies"] is False
    assert by_provider["together"]["has_cookies"] is False


# ---------------------------------------------------------------------------
# DELETE /provider_cookies/{provider}
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_admin_no_cookies_400(client, db) -> None:
    user = await _seed_user(db, email="ceo@brikko.ru")
    await _login(client, user)
    headers = await _csrf_headers(client)
    patchers = _admin_context()
    _enter(patchers)
    try:
        r = await client.delete(
            "/v1/account/admin/provider_cookies/openai",
            headers=headers,
        )
    finally:
        _exit(patchers)
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_delete_admin_happy_path(client, db) -> None:
    user = await _seed_user(db, email="ceo@brikko.ru")
    await _login(client, user)
    headers = await _csrf_headers(client)
    patchers = _admin_context()
    _enter(patchers)
    try:
        # Upload first.
        with respx.mock() as mock:
            mock.post(f"{SCRAPER_URL}/cookies/together").respond(
                200,
                json={
                    "provider": "together",
                    "size_bytes": 150,
                    "stored_at": "2026-05-11T12:00:00Z",
                },
            )
            r1 = await client.post(
                "/v1/account/admin/provider_cookies/together",
                content=_good_cookies_json(),
                headers=headers,
            )
            assert r1.status_code == 200, r1.text

        # Now delete.
        r2 = await client.delete(
            "/v1/account/admin/provider_cookies/together",
            headers=headers,
        )
    finally:
        _exit(patchers)
    assert r2.status_code == 200
    assert r2.json()["has_cookies"] is False

    # DB row gone.
    row = (
        await db.execute(select(ProviderCookies).where(ProviderCookies.provider == "together"))
    ).scalar_one_or_none()
    assert row is None
