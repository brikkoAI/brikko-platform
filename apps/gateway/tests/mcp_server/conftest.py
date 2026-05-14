"""Fixtures specific to MCP-server tests.

The top-level ``tests/conftest.py`` provides the SQLite + fakeredis +
ASGI client stack. Here we add an ``mcp_token_fixture`` analogous to
``api_key_fixture`` — a user + account + MCP token triple seeded into
the in-memory DB. Tests can then call the tool handlers directly (with
the principal bound via ``MCP_PRINCIPAL_CTX``) or hit ``/mcp`` over HTTP.
"""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from voltari_gateway.auth.keys import generate_mcp_token
from voltari_gateway.db.models import (
    Account,
    AccountStatus,
    McpScope,
    McpToken,
    McpTokenStatus,
    Tariff,
    User,
)
from voltari_gateway.mcp_server.auth import set_mcp_redis
from voltari_gateway.mcp_server.context import MCP_PRINCIPAL_CTX, McpPrincipal
from voltari_gateway.mcp_server.rate_limit import (
    McpRateLimiter,
    set_mcp_rate_limiter,
)


@dataclass
class McpTokenFixture:
    user: User
    account: Account
    token: McpToken
    plaintext: str

    @property
    def auth_header(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.plaintext}"}


async def _seed_user_account_token(
    db: AsyncSession,
    *,
    tariff: Tariff = Tariff.PRO,
    scope: McpScope = McpScope.READ_ACCOUNT,
    account_status: AccountStatus = AccountStatus.ACTIVE,
    balance_kopecks: int = 100_000,
    token_status: McpTokenStatus = McpTokenStatus.ACTIVE,
) -> McpTokenFixture:
    user = User(
        email=f"mcp-user-{uuid.uuid4().hex[:8]}@test.local",
        password_hash="$argon2id$v=19$m=65536,t=2,p=2$dummy",
        email_verified=True,
    )
    db.add(user)
    await db.flush()

    account = Account(
        owner_id=user.id,
        name="MCP test account",
        balance_kopecks=balance_kopecks,
        tariff=tariff,
        status=account_status,
        store_prompts=True,
    )
    db.add(account)
    await db.flush()

    generated = generate_mcp_token()
    token = McpToken(
        account_id=account.id,
        name="default-mcp",
        token_hash=generated.token_hash,
        token_prefix=generated.prefix,
        scope=scope,
        status=token_status,
    )
    db.add(token)
    await db.commit()
    await db.refresh(token)
    await db.refresh(account)
    await db.refresh(user)
    return McpTokenFixture(user=user, account=account, token=token, plaintext=generated.plaintext)


@pytest_asyncio.fixture(scope="function")
async def mcp_token_fixture(db: AsyncSession) -> McpTokenFixture:
    """Default fixture: PRO tariff, ``read_account`` scope, active."""
    return await _seed_user_account_token(db)


@pytest_asyncio.fixture(scope="function")
async def seed_mcp_token(db: AsyncSession):
    """Factory variant for tests that need custom scope / tariff / status."""

    async def _make(**kwargs) -> McpTokenFixture:
        return await _seed_user_account_token(db, **kwargs)

    return _make


@pytest_asyncio.fixture(scope="function", autouse=True)
async def mcp_singletons(redis_client) -> AsyncIterator[None]:
    """Wire the global MCP auth-cache + rate-limiter to the test redis.

    Autouse so every test gets a fresh binding. Without this, the
    rate-limit singleton from app startup would be ``None`` and the
    /mcp endpoint would degrade to "allow all" — which masks the test
    case that checks 429.
    """
    set_mcp_redis(redis_client)
    set_mcp_rate_limiter(McpRateLimiter(redis_client, limit_per_min=60))
    yield
    set_mcp_redis(None)
    set_mcp_rate_limiter(None)


@pytest_asyncio.fixture(scope="function")
async def bind_principal():
    """Bind an ``McpPrincipal`` to the contextvar for the duration of a test.

    Tool handlers read the principal via ``current_principal()``; in unit
    tests we don't go through the ASGI wrapper, so we bind manually.
    """
    tokens: list = []

    def _bind(fixture: McpTokenFixture, scope: McpScope | None = None) -> McpPrincipal:
        principal = McpPrincipal(
            account_id=fixture.account.id,
            token_id=fixture.token.id,
            user_id=fixture.user.id,
            scope=scope or fixture.token.scope,
            tariff=fixture.account.tariff.value,
        )
        tokens.append(MCP_PRINCIPAL_CTX.set(principal))
        return principal

    yield _bind
    # Reset in reverse order — innermost binding cleared first. Suppress
    # because the same test may have already called reset() explicitly.
    for t in reversed(tokens):
        with contextlib.suppress(Exception):
            MCP_PRINCIPAL_CTX.reset(t)


@pytest.fixture
def csrf_unused() -> None:
    """No-op marker fixture — MCP tokens are bearer, not cookie.

    Exists so a future contributor doesn't accidentally pull in
    ``csrf_headers`` thinking MCP needs it. Bearer-auth requests never
    carry CSRF artefacts.
    """
    return None


@pytest.fixture
def mcp_session_manager(app):
    """Return the MCP session manager attached by ``create_app``.

    Tests that exercise the streamable-HTTP transport must enter the
    manager's ``run()`` context themselves:

        async with mcp_session_manager.run():
            response = await client.post("/mcp/", ...)

    Why not auto-enter in a fixture: ``StreamableHTTPSessionManager.run()``
    uses an anyio task group whose cancel scope is bound to the task
    that entered it. pytest-asyncio fixtures finalise in a different
    task than the test body in some flows, which trips a
    "cancel scope in a different task" RuntimeError. Doing the
    ``async with`` inside the test body keeps both halves on one task.
    """
    return app.state.mcp_session_manager
