"""Bearer-token authentication for the MCP endpoint.

Mirrors the design of ``voltari_gateway.auth.middleware`` for the API-key
path, but with three deliberate differences:

1. **Different plaintext literal.** ``mcp-brk-*`` (not ``sk-brk-*``) so
   the credential is visually unmistakable in logs, screen-shares, and
   clipboard managers.
2. **Different scope contract.** MCP tokens carry exactly one of
   ``read_account | read_usage | recommend_model`` (S1 enum). The auth
   layer surfaces the scope on the principal; per-tool checks happen
   inside the tool handlers (see ``mcp_server.tools.read_account`` etc).
3. **Independent Redis cache namespace.** ``auth:mcp:<sha256>`` so the
   ``api_keys`` revoke path (which scans ``auth:key:*``) can never
   accidentally invalidate an MCP cache row (or vice versa). Cross-table
   collisions are the kind of bug you only catch in production at 3am.

Cache lifetime: ``MCP_AUTH_CACHE_TTL_SECONDS`` (60s default). Revocations
propagate within one window — matches the contract of the chat-completions
auth cache.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from typing import Any

from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from voltari_gateway.auth.keys import extract_mcp_prefix, verify_mcp_token
from voltari_gateway.config import get_settings
from voltari_gateway.db.models import (
    Account,
    AccountStatus,
    McpScope,
    McpToken,
    McpTokenStatus,
)
from voltari_gateway.mcp_server.context import McpPrincipal
from voltari_gateway.utils.logging import get_logger

log = get_logger(__name__)


# Module-level Redis handle, lazily injected at app startup. None = caching
# disabled (DB-only verify path). Mirrors ``auth.middleware.set_redis``.
_redis: Redis | None = None


def set_mcp_redis(client: Redis | None) -> None:
    """Inject (or reset) the Redis client used by the MCP auth cache.

    Called from ``main.py`` lifespan once the global Redis client is
    constructed, and from tests injecting fakeredis.
    """
    global _redis
    _redis = client


def get_mcp_redis() -> Redis | None:
    return _redis


# ---------------------------------------------------------------------------
# Cache key helpers
# ---------------------------------------------------------------------------


def _cache_key(plaintext: str) -> str:
    """Primary cache key: ``auth:mcp:<sha256(plaintext)>``.

    SHA-256 (not the plaintext) so a Redis dump never contains live
    credentials. Distinct prefix from ``auth:key:`` (api_keys) keeps the
    two surfaces isolated — see module docstring.
    """
    digest = hashlib.sha256(plaintext.encode("utf-8")).hexdigest()
    return f"auth:mcp:{digest}"


def _cache_index_key(token_id: uuid.UUID) -> str:
    """Secondary index: ``token_id → primary cache key``.

    Lets the management API (``DELETE /v1/mcp/tokens/{id}``) purge a
    single bearer-cache entry without knowing the plaintext. Symmetrical
    to ``auth.middleware._cache_index_key`` for api_keys.
    """
    return f"auth:mcp:by_id:{token_id}"


async def invalidate_cache_for_token(redis: Redis | None, token_id: uuid.UUID) -> None:
    """Drop the auth cache entry for ``token_id`` (primary + index).

    Idempotent. Called from the S1 revoke endpoint in a follow-up wire-up;
    in S2 it's exposed so tests can flush between scenarios.
    """
    if redis is None:
        return
    try:
        index_key = _cache_index_key(token_id)
        primary = await redis.get(index_key)
        async with redis.pipeline(transaction=False) as pipe:
            if primary is not None:
                primary_str = primary if isinstance(primary, str) else primary.decode("utf-8")
                pipe.delete(primary_str)
            pipe.delete(index_key)
            await pipe.execute()
    except Exception as exc:
        log.warning("mcp_auth_cache_invalidate_failed", token_id=str(token_id), error=str(exc))


# ---------------------------------------------------------------------------
# Principal serialisation (Redis cache payload)
# ---------------------------------------------------------------------------


def _principal_to_cache(p: McpPrincipal) -> dict[str, Any]:
    return {
        "account_id": str(p.account_id),
        "token_id": str(p.token_id),
        "user_id": str(p.user_id),
        "scope": p.scope.value,
        "tariff": p.tariff,
    }


def _principal_from_cache(data: dict[str, Any]) -> McpPrincipal:
    return McpPrincipal(
        account_id=uuid.UUID(data["account_id"]),
        token_id=uuid.UUID(data["token_id"]),
        user_id=uuid.UUID(data["user_id"]),
        scope=McpScope(data["scope"]),
        tariff=str(data["tariff"]),
    )


# ---------------------------------------------------------------------------
# Bearer extraction
# ---------------------------------------------------------------------------


def extract_bearer(authorization: str | None) -> str | None:
    """Parse ``Authorization: Bearer <token>`` → plaintext, or ``None``.

    Spec-compliant: case-insensitive scheme, single whitespace tolerance
    (multiple spaces collapse via ``split(None, 1)``). Returns ``None``
    on any malformed input — callers map that to 401.
    """
    if not authorization:
        return None
    parts = authorization.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    token = parts[1].strip()
    return token or None


# ---------------------------------------------------------------------------
# DB verify
# ---------------------------------------------------------------------------


async def _verify_against_db(plaintext: str, db: AsyncSession) -> tuple[McpToken, Account] | None:
    """Verify ``plaintext`` against ``mcp_tokens``. None on any failure.

    Failure cases (all map to a single 401 — never disclose which):

    * Bad prefix literal (not ``mcp-brk-`` or shorter than 14 chars).
    * No row with matching prefix.
    * argon2 mismatch.
    * Token revoked (``status=REVOKED``).
    * Token expired (``expires_at <= now``).
    * Account not active (``status != ACTIVE``) or fully purged
      (``closed_at IS NOT NULL``). The 30-day grace window before the
      purge cron keeps working — same contract as the api_keys path.
    """
    prefix = extract_mcp_prefix(plaintext)
    if prefix is None:
        return None

    stmt = (
        select(McpToken)
        .where(McpToken.token_prefix == prefix)
        .where(McpToken.status == McpTokenStatus.ACTIVE)
    )
    rows = (await db.execute(stmt)).scalars().all()
    now = datetime.now(UTC)
    for token in rows:
        if not verify_mcp_token(plaintext, token.token_hash):
            continue
        if token.expires_at is not None:
            expires_at = token.expires_at
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=UTC)
            if expires_at <= now:
                log.info(
                    "mcp_token_expired",
                    token_id=str(token.id),
                    expires_at=expires_at.isoformat(),
                )
                return None
        account = await db.get(Account, token.account_id)
        if account is None:
            return None
        if account.closed_at is not None:
            return None
        if account.status != AccountStatus.ACTIVE:
            return None
        return token, account
    return None


# ---------------------------------------------------------------------------
# Public resolver
# ---------------------------------------------------------------------------


async def resolve_mcp_principal(
    authorization: str | None,
    db: AsyncSession,
) -> McpPrincipal | None:
    """Resolve an ``Authorization`` header to an ``McpPrincipal``.

    Pipeline:
      1. Parse the ``Bearer`` value. Missing or malformed → None.
      2. Redis cache lookup (``auth:mcp:<sha256>``). Hit → return.
      3. DB verify (prefix → argon2 → status checks). Success → cache + return.

    Returns ``None`` on any failure — callers map that to a single 401.
    Never raises on Redis errors: a Redis outage degrades to a slower DB
    path, not a hard outage of the MCP endpoint.
    """
    plaintext = extract_bearer(authorization)
    if not plaintext:
        return None

    settings = get_settings()
    redis_client = get_mcp_redis()
    cache_key = _cache_key(plaintext)

    # 1) Redis fast-path
    if redis_client is not None:
        try:
            cached = await redis_client.get(cache_key)
        except Exception as exc:
            log.warning("mcp_auth_redis_get_failed", error=str(exc))
            cached = None
        if cached:
            try:
                payload = json.loads(cached if isinstance(cached, str) else cached.decode("utf-8"))
                return _principal_from_cache(payload)
            except Exception as exc:
                log.warning("mcp_auth_cache_decode_failed", error=str(exc))

    # 2) DB verify
    result = await _verify_against_db(plaintext, db)
    if result is None:
        return None
    token, account = result

    principal = McpPrincipal(
        account_id=account.id,
        token_id=token.id,
        user_id=account.owner_id,
        scope=token.scope,
        tariff=account.tariff.value,
    )

    # 3) Touch last_used_at without blocking on commit failures.
    try:
        token.last_used_at = datetime.now(UTC)
        await db.commit()
    except Exception as exc:
        log.warning("mcp_auth_last_used_update_failed", error=str(exc))
        await db.rollback()

    # 4) Cache success — primary + index, single RTT pipeline.
    if redis_client is not None:
        try:
            async with redis_client.pipeline(transaction=False) as pipe:
                pipe.setex(
                    cache_key,
                    settings.mcp_auth_cache_ttl_seconds,
                    json.dumps(_principal_to_cache(principal)),
                )
                pipe.setex(
                    _cache_index_key(token.id),
                    settings.mcp_auth_cache_ttl_seconds,
                    cache_key,
                )
                await pipe.execute()
        except Exception as exc:
            log.warning("mcp_auth_redis_set_failed", error=str(exc))

    return principal


__all__ = [
    "extract_bearer",
    "get_mcp_redis",
    "invalidate_cache_for_token",
    "resolve_mcp_principal",
    "set_mcp_redis",
]
