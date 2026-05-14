"""Auth-layer tests for the MCP endpoint.

Covers:

* Bearer extraction (happy path, missing, malformed, wrong scheme).
* DB verify (active token, revoked, expired, wrong-prefix, wrong-hash).
* Redis cache hit / miss / decode-failure → fallback.
* Account status gating (suspended, closed, purged).
* Principal cache round-trip (to_cache / from_cache).
* Cache invalidation by token id.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from voltari_gateway.db.models import (
    AccountStatus,
    McpScope,
    McpTokenStatus,
)
from voltari_gateway.mcp_server.auth import (
    _cache_index_key,
    _cache_key,
    extract_bearer,
    invalidate_cache_for_token,
    resolve_mcp_principal,
    set_mcp_redis,
)

# ---------------------------------------------------------------------------
# extract_bearer
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ("Bearer mcp-brk-abcdef123456", "mcp-brk-abcdef123456"),
        ("bearer mcp-brk-abc", "mcp-brk-abc"),
        ("BEARER mcp-brk-xyz", "mcp-brk-xyz"),
        ("  Bearer mcp-brk-ws  ", "mcp-brk-ws"),  # outer whitespace stripped by SDK
    ],
)
def test_extract_bearer_happy(header, expected):
    """Various capitalisations and whitespace patterns yield the token."""
    assert extract_bearer(header) == expected


@pytest.mark.parametrize(
    "header",
    [
        None,
        "",
        "mcp-brk-no-scheme",
        "Basic mcp-brk-wrong-scheme",
        "Bearer ",
        "Bearer    ",
    ],
)
def test_extract_bearer_rejects_garbage(header):
    """Missing / malformed / wrong-scheme headers return None."""
    assert extract_bearer(header) is None


# ---------------------------------------------------------------------------
# resolve_mcp_principal — DB path
# ---------------------------------------------------------------------------


async def test_resolve_returns_principal_for_active_token(db, mcp_token_fixture):
    """Happy path: live token + active account → McpPrincipal."""
    principal = await resolve_mcp_principal(f"Bearer {mcp_token_fixture.plaintext}", db)
    assert principal is not None
    assert principal.account_id == mcp_token_fixture.account.id
    assert principal.token_id == mcp_token_fixture.token.id
    assert principal.user_id == mcp_token_fixture.user.id
    assert principal.scope == mcp_token_fixture.token.scope
    assert principal.tariff == mcp_token_fixture.account.tariff.value


async def test_resolve_returns_none_for_missing_header(db):
    assert await resolve_mcp_principal(None, db) is None
    assert await resolve_mcp_principal("", db) is None


async def test_resolve_returns_none_for_wrong_literal(db):
    """``sk-brk-`` is a chat key — never an MCP token, even if real."""
    assert await resolve_mcp_principal("Bearer sk-brk-abcdef123456", db) is None


async def test_resolve_returns_none_for_random_garbage(db):
    """Right literal but no row → 401, not a leak."""
    assert await resolve_mcp_principal("Bearer mcp-brk-nonsense12345", db) is None


async def test_resolve_returns_none_for_revoked_token(db, mcp_token_fixture):
    mcp_token_fixture.token.status = McpTokenStatus.REVOKED
    mcp_token_fixture.token.revoked_at = datetime.now(UTC)
    await db.commit()
    assert await resolve_mcp_principal(f"Bearer {mcp_token_fixture.plaintext}", db) is None


async def test_resolve_returns_none_for_expired_token(db, mcp_token_fixture):
    mcp_token_fixture.token.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    await db.commit()
    assert await resolve_mcp_principal(f"Bearer {mcp_token_fixture.plaintext}", db) is None


async def test_resolve_returns_principal_when_expires_at_in_future(db, mcp_token_fixture):
    mcp_token_fixture.token.expires_at = datetime.now(UTC) + timedelta(days=30)
    await db.commit()
    principal = await resolve_mcp_principal(f"Bearer {mcp_token_fixture.plaintext}", db)
    assert principal is not None


async def test_resolve_returns_none_for_suspended_account(db, mcp_token_fixture):
    mcp_token_fixture.account.status = AccountStatus.SUSPENDED
    await db.commit()
    assert await resolve_mcp_principal(f"Bearer {mcp_token_fixture.plaintext}", db) is None


async def test_resolve_returns_none_for_purged_account(db, mcp_token_fixture):
    """``closed_at != None`` = post-cron purge, hard-block."""
    mcp_token_fixture.account.closed_at = datetime.now(UTC)
    await db.commit()
    assert await resolve_mcp_principal(f"Bearer {mcp_token_fixture.plaintext}", db) is None


async def test_resolve_passes_grace_period(db, mcp_token_fixture):
    """30-day grace (``closure_scheduled_at`` set, ``closed_at`` None) still works."""
    mcp_token_fixture.account.closure_scheduled_at = datetime.now(UTC) + timedelta(days=30)
    await db.commit()
    principal = await resolve_mcp_principal(f"Bearer {mcp_token_fixture.plaintext}", db)
    assert principal is not None


async def test_resolve_updates_last_used_at(db, mcp_token_fixture):
    """Successful resolve touches ``token.last_used_at``."""
    assert mcp_token_fixture.token.last_used_at is None
    await resolve_mcp_principal(f"Bearer {mcp_token_fixture.plaintext}", db)
    await db.refresh(mcp_token_fixture.token)
    assert mcp_token_fixture.token.last_used_at is not None


# ---------------------------------------------------------------------------
# resolve_mcp_principal — Redis cache path
# ---------------------------------------------------------------------------


async def test_resolve_caches_principal_in_redis(db, mcp_token_fixture, redis_client):
    """First call hits DB, populates cache; cache contains principal payload."""
    await resolve_mcp_principal(f"Bearer {mcp_token_fixture.plaintext}", db)

    cache_key = _cache_key(mcp_token_fixture.plaintext)
    cached = await redis_client.get(cache_key)
    assert cached is not None
    payload = json.loads(cached)
    assert payload["account_id"] == str(mcp_token_fixture.account.id)
    assert payload["scope"] == "read_account"


async def test_resolve_uses_cache_on_second_call(db, mcp_token_fixture, redis_client):
    """Second call doesn't touch the DB — verified via index-key presence."""
    await resolve_mcp_principal(f"Bearer {mcp_token_fixture.plaintext}", db)

    # Mutate DB underneath: revoke the token. If the cache is used, the
    # next resolve should still succeed (returns stale principal).
    mcp_token_fixture.token.status = McpTokenStatus.REVOKED
    await db.commit()

    principal = await resolve_mcp_principal(f"Bearer {mcp_token_fixture.plaintext}", db)
    assert principal is not None
    assert principal.scope == McpScope.READ_ACCOUNT


async def test_resolve_falls_back_to_db_on_corrupt_cache(db, mcp_token_fixture, redis_client):
    """Cache row that doesn't json-decode → silent fallback to DB."""
    cache_key = _cache_key(mcp_token_fixture.plaintext)
    await redis_client.setex(cache_key, 60, "not-valid-json")

    principal = await resolve_mcp_principal(f"Bearer {mcp_token_fixture.plaintext}", db)
    assert principal is not None


async def test_resolve_works_without_redis(db, mcp_token_fixture):
    """``set_mcp_redis(None)`` → DB-only mode, still resolves."""
    set_mcp_redis(None)
    principal = await resolve_mcp_principal(f"Bearer {mcp_token_fixture.plaintext}", db)
    assert principal is not None


# ---------------------------------------------------------------------------
# invalidate_cache_for_token
# ---------------------------------------------------------------------------


async def test_invalidate_drops_both_keys(db, mcp_token_fixture, redis_client):
    """After invalidate, primary + index both gone; next resolve hits DB."""
    await resolve_mcp_principal(f"Bearer {mcp_token_fixture.plaintext}", db)

    primary = _cache_key(mcp_token_fixture.plaintext)
    index = _cache_index_key(mcp_token_fixture.token.id)
    assert await redis_client.get(primary) is not None
    assert await redis_client.get(index) is not None

    await invalidate_cache_for_token(redis_client, mcp_token_fixture.token.id)

    assert await redis_client.get(primary) is None
    assert await redis_client.get(index) is None


async def test_invalidate_is_noop_for_unknown_token(redis_client):
    """No throw, no harm. Idempotent."""
    await invalidate_cache_for_token(redis_client, uuid.uuid4())


async def test_invalidate_handles_none_redis():
    """``None`` Redis client → silent no-op."""
    await invalidate_cache_for_token(None, uuid.uuid4())


# ---------------------------------------------------------------------------
# Scope isolation
# ---------------------------------------------------------------------------


async def test_principal_carries_scope_from_token(seed_mcp_token):
    """Each scope round-trips through the principal — no enum coercion bugs."""
    for scope in (
        McpScope.READ_ACCOUNT,
        McpScope.READ_USAGE,
        McpScope.RECOMMEND_MODEL,
    ):
        fixture = await seed_mcp_token(scope=scope)
        # Need the DB for this — pull fresh from a different session.
        from voltari_gateway.db.session import get_session_factory

        async with get_session_factory()() as inner_db:
            principal = await resolve_mcp_principal(f"Bearer {fixture.plaintext}", inner_db)
        assert principal is not None
        assert principal.scope == scope
