"""Pytest fixtures.

Strategy:
- aiosqlite in-memory DB per test session — fast, no Docker required.
- fakeredis for the auth cache.
- A stub OpenAI provider attached to ``app.state.openai_provider`` so chat
  tests never touch the real upstream API.

Each test gets a freshly-seeded user/account/api_key triple (``api_key_fixture``).
"""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

# Configure env BEFORE importing the app — pydantic-settings reads at import.
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("OPENAI_API_KEY", "sk-test-DUMMY")
os.environ.setdefault("APP_ENV", "local")
# Strong defaults so the secret-strength validator stays silent during tests.
# Values are obviously test-fixtures (>=32 bytes, no forbidden substrings).
os.environ.setdefault(
    "JWT_SECRET",
    "fixture-jwt-secret-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
)
os.environ.setdefault(
    "EMAIL_TOKEN_SECRET",
    "fixture-email-pepper-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
)

import fakeredis.aioredis
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from voltari_gateway.auth.keys import generate_api_key
from voltari_gateway.auth.middleware import set_redis
from voltari_gateway.db import session as db_session
from voltari_gateway.db.models import (
    Account,
    AccountStatus,
    ApiKey,
    ApiKeyStatus,
    Base,
    Tariff,
    User,
)
from voltari_gateway.main import create_app
from voltari_gateway.providers.base import (
    ChatCompletionResponse,
    ChatCompletionUsage,
    Provider,
)

# --- DB fixtures -------------------------------------------------------------


@pytest_asyncio.fixture(scope="function")
async def engine():
    """Fresh in-memory SQLite per test, shared across connections via StaticPool.

    Without StaticPool every aiosqlite connection sees its own ":memory:"
    database, so the `db` fixture and the FastAPI dependency would not see
    each other's tables.
    """
    eng = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        future=True,
        poolclass=StaticPool,
        connect_args={"check_same_thread": False, "uri": False},
    )
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture(scope="function")
async def session_factory(engine) -> async_sessionmaker[AsyncSession]:
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    # Hook the global db.session module to use our test factory
    db_session._engine = engine
    db_session._session_factory = factory
    return factory


@pytest_asyncio.fixture(scope="function")
async def db(session_factory) -> AsyncIterator[AsyncSession]:
    async with session_factory() as s:
        yield s


# --- Redis (fakeredis) -------------------------------------------------------


@pytest_asyncio.fixture(scope="function")
async def redis_client():
    client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    set_redis(client)
    yield client
    set_redis(None)
    await client.aclose()


# --- Stub OpenAI provider ----------------------------------------------------


class StubProvider(Provider):
    name = "openai"

    def __init__(self) -> None:
        self.last_request = None
        self.next_response: ChatCompletionResponse | None = None
        self.next_stream_chunks: list[bytes] | None = None

    async def chat_completion(self, req):
        self.last_request = req
        if self.next_response is None:
            return ChatCompletionResponse(
                raw={
                    "id": "chatcmpl-stub",
                    "object": "chat.completion",
                    "created": 1730000000,
                    "model": req.model.id,
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": "stub-reply"},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 10,
                        "completion_tokens": 20,
                        "total_tokens": 30,
                    },
                },
                usage=ChatCompletionUsage(prompt_tokens=10, completion_tokens=20, total_tokens=30),
                model_id=req.model.id,
                provider="openai",
            )
        return self.next_response

    async def chat_completion_stream(self, req):
        self.last_request = req
        chunks = self.next_stream_chunks or [
            b'data: {"id":"chatcmpl-stub","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"role":"assistant","content":"hi"},"finish_reason":null}]}\n\n',
            b'data: {"id":"chatcmpl-stub","object":"chat.completion.chunk","choices":[{"index":0,"delta":{},"finish_reason":"stop"}],"usage":{"prompt_tokens":10,"completion_tokens":2,"total_tokens":12}}\n\n',
            b"data: [DONE]\n\n",
        ]

        async def _gen():
            for c in chunks:
                yield c

        return _gen()

    async def aclose(self) -> None:
        return None


# --- App / client ------------------------------------------------------------


@pytest_asyncio.fixture(scope="function")
async def app(engine, session_factory, redis_client):
    """FastAPI app with stubbed provider and in-memory DB."""
    application = create_app()
    stub = StubProvider()
    application.state.openai_provider = stub
    # Build a registry that exposes the stub as the OpenAI adapter so the
    # router/failover paths can resolve a provider for any OpenAI model.
    from voltari_gateway.providers.registry import ProviderRegistry
    from voltari_gateway.router.catalog import Provider as ProviderEnum
    from voltari_gateway.router.router import Router as RouterEngine

    registry = ProviderRegistry()
    registry.register(ProviderEnum.OPENAI, stub)
    application.state.provider_registry = registry
    application.state.router_engine = RouterEngine()
    # Sub-millisecond backoffs so failover walks complete in tens of ms.
    application.state.failover_backoffs_ms = (1, 1, 1)
    application.state.failover_timeout_s = 5.0
    yield application


@pytest_asyncio.fixture(scope="function")
async def client(app) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


# --- Seeded principal --------------------------------------------------------


@dataclass
class ApiKeyFixture:
    user: User
    account: Account
    api_key: ApiKey
    plaintext: str

    @property
    def auth_header(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.plaintext}"}


@pytest_asyncio.fixture(scope="function")
async def api_key_fixture(db: AsyncSession) -> ApiKeyFixture:
    user = User(
        email=f"user-{uuid.uuid4().hex[:8]}@test.local",
        password_hash="$argon2id$v=19$m=65536,t=2,p=2$dummy",
        email_verified=True,
    )
    db.add(user)
    await db.flush()

    account = Account(
        owner_id=user.id,
        name="Test account",
        balance_kopecks=100_000,
        tariff=Tariff.PRO,
        status=AccountStatus.ACTIVE,
        store_prompts=True,
    )
    db.add(account)
    await db.flush()

    generated = generate_api_key()
    api_key = ApiKey(
        account_id=account.id,
        name="default",
        key_hash=generated.key_hash,
        key_prefix=generated.prefix,
        status=ApiKeyStatus.ACTIVE,
    )
    db.add(api_key)
    await db.commit()
    await db.refresh(api_key)
    await db.refresh(account)
    await db.refresh(user)

    return ApiKeyFixture(user=user, account=account, api_key=api_key, plaintext=generated.plaintext)


# --- helpers -----------------------------------------------------------------


def parse_sse(body: str) -> list[dict[str, Any] | str]:
    """Parse an SSE response body to a list of decoded events."""
    out: list[dict[str, Any] | str] = []
    for line in body.splitlines():
        if not line.startswith("data: "):
            continue
        payload = line[len("data: ") :]
        if payload == "[DONE]":
            out.append("[DONE]")
            continue
        try:
            out.append(json.loads(payload))
        except json.JSONDecodeError:
            out.append(payload)
    return out
