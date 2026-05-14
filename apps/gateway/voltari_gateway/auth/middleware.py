"""Bearer-token authentication dependency.

Pipeline (per-request, hot path):

    1. Read ``Authorization: Bearer <plaintext>`` header.
    2. Look up cached principal in Redis (key = sha256(plaintext)). If hit and
       fresh — populate ``request.state`` and return.
    3. Otherwise, fetch candidate ``api_keys`` rows by prefix, run argon2
       verify, validate status / revocation / account status. Cache success.
    4. On miss / mismatch / revoked — raise 401 with OpenAI-compatible body.

We intentionally keep auth as a FastAPI dependency (rather than ASGI
middleware) so failures surface through the standard exception handler and
the rest of the dependency tree (DB session, Redis, request_id) is already
available. The `state` object is still populated, so handlers and downstream
deps can rely on `request.state.account` exactly like with middleware.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from fastapi import Cookie, Depends, Header, Request
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from voltari_gateway.auth.csrf import CSRF_COOKIE, CSRF_HEADER, verify_csrf
from voltari_gateway.auth.keys import extract_prefix, verify_api_key
from voltari_gateway.config import get_settings
from voltari_gateway.db.models import Account, AccountStatus, ApiKey, ApiKeyStatus, User
from voltari_gateway.db.session import get_db
from voltari_gateway.utils.errors import authentication_error
from voltari_gateway.utils.logging import get_logger

log = get_logger(__name__)

# Module-level Redis handle, lazily created. Tests inject via ``set_redis()``.
_redis: Redis | None = None


def set_redis(client: Redis | None) -> None:
    """Inject (or reset) the Redis client used by the auth cache.

    Used both by app startup and by tests / fakeredis.
    """
    global _redis
    _redis = client


def get_redis() -> Redis | None:
    return _redis


@dataclass(frozen=True)
class AuthPrincipal:
    """The fully-resolved caller for the current request."""

    account_id: uuid.UUID
    api_key_id: uuid.UUID
    user_id: uuid.UUID
    tariff: str
    balance_kopecks: int
    store_prompts: bool
    # Sprint 5 perf: cached on the principal so /v1/chat/completions can skip
    # a per-request `SELECT pii_masking_enabled FROM accounts WHERE id=...`.
    # The Redis principal cache (TTL 60s) bounds staleness; toggling this
    # flag in the dashboard takes effect within one cache window.
    pii_masking_enabled: bool = False
    # Sprint 7 — routing preferences. Same caching rationale as the PII
    # flag: AccountContext needs them on the chat hot path; the Redis
    # cache TTL bounds staleness when the user updates settings.
    routing_mode: str = "smart"
    routing_strategy: str = "cheap"
    routing_allowed_providers: tuple[str, ...] | None = None
    routing_allowed_models: tuple[str, ...] | None = None
    # Sprint S1 — Smart Router v2 per-account opt-in flag (Alembic 0022).
    # Cached on the principal so the chat hot path doesn't issue an
    # extra SELECT per request. Refreshed within one auth-cache window
    # (60s default) after the admin endpoint flips the column.
    smart_router_v2_enabled: bool = False

    def to_cache_dict(self) -> dict[str, Any]:
        return {
            "account_id": str(self.account_id),
            "api_key_id": str(self.api_key_id),
            "user_id": str(self.user_id),
            "tariff": self.tariff,
            "balance_kopecks": self.balance_kopecks,
            "store_prompts": self.store_prompts,
            "pii_masking_enabled": self.pii_masking_enabled,
            "routing_mode": self.routing_mode,
            "routing_strategy": self.routing_strategy,
            "routing_allowed_providers": (
                list(self.routing_allowed_providers)
                if self.routing_allowed_providers is not None
                else None
            ),
            "routing_allowed_models": (
                list(self.routing_allowed_models)
                if self.routing_allowed_models is not None
                else None
            ),
            "smart_router_v2_enabled": self.smart_router_v2_enabled,
        }

    @classmethod
    def from_cache_dict(cls, data: dict[str, Any]) -> AuthPrincipal:
        rp = data.get("routing_allowed_providers")
        rm = data.get("routing_allowed_models")
        return cls(
            account_id=uuid.UUID(data["account_id"]),
            api_key_id=uuid.UUID(data["api_key_id"]),
            user_id=uuid.UUID(data["user_id"]),
            tariff=data["tariff"],
            balance_kopecks=int(data["balance_kopecks"]),
            store_prompts=bool(data["store_prompts"]),
            # Backwards-compat: pre-Sprint-5 cache entries don't carry this
            # field; default to False so a stale entry just falls through to
            # the in-handler default (matches Account.pii_masking_enabled
            # column default).
            pii_masking_enabled=bool(data.get("pii_masking_enabled", False)),
            routing_mode=str(data.get("routing_mode", "smart")),
            routing_strategy=str(data.get("routing_strategy", "cheap")),
            routing_allowed_providers=tuple(rp) if rp is not None else None,
            routing_allowed_models=tuple(rm) if rm is not None else None,
            # Backwards-compat: pre-S1 cache entries lack this key;
            # default to False matches the column default. The next
            # principal refresh after a flip will populate it correctly.
            smart_router_v2_enabled=bool(data.get("smart_router_v2_enabled", False)),
        )


def _cache_key(plaintext: str) -> str:
    digest = hashlib.sha256(plaintext.encode("utf-8")).hexdigest()
    return f"auth:key:{digest}"


def _cache_index_key(api_key_id: uuid.UUID) -> str:
    """Secondary index: api_key_id → primary cache key.

    Lets the management API (``/v1/keys`` DELETE) purge a single bearer-token
    cache entry without knowing the plaintext. Without this, a revoke would
    have to wait ``AUTH_CACHE_TTL_SECONDS`` (default 60s) for the entry to
    expire — too slow for the "<10s revoke" SLA.
    """
    return f"auth:key:by_id:{api_key_id}"


async def invalidate_cache_for_key(redis: Redis | None, api_key_id: uuid.UUID) -> None:
    """Drop the auth cache entry for ``api_key_id`` (both primary + index).

    Idempotent and safe to call when no cache entry exists. Logs but does
    not raise on Redis I/O failures — the DB-side ``revoked_at`` is the
    source of truth; cache is just an optimisation.
    """
    if redis is None:
        return
    try:
        index_key = _cache_index_key(api_key_id)
        primary = await redis.get(index_key)
        # Pipeline both DELETE commands into one RTT: previously this path
        # did GET → DEL → DEL = 3 round-trips. Revoke is on the warm path
        # only when the dashboard issues "delete key" — not chat-hot — but
        # 33% RTT savings is free.
        async with redis.pipeline(transaction=False) as pipe:
            if primary is not None:
                primary_str = primary if isinstance(primary, str) else primary.decode("utf-8")
                pipe.delete(primary_str)
            pipe.delete(index_key)
            await pipe.execute()
    except Exception as exc:
        log.warning("auth_cache_invalidate_failed", api_key_id=str(api_key_id), error=str(exc))


def _extract_bearer(authorization: str | None) -> str | None:
    if not authorization:
        return None
    parts = authorization.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    token = parts[1].strip()
    return token or None


async def _verify_against_db(plaintext: str, db: AsyncSession) -> tuple[ApiKey, Account] | None:
    """Verify ``plaintext`` against the ``api_keys`` table.

    Returns ``(api_key, account)`` on success, ``None`` on any failure
    (no row, hash mismatch, revoked status, suspended account, **expired
    key**). Callers map ``None`` to a single 401 — we never disclose
    *why* the key failed, only that it did.

    TD-009: keys with ``expires_at IS NOT NULL AND expires_at <= NOW()``
    are treated as not-found. We don't surface a distinct
    ``api_key_expired`` code to the client — that would tell an
    attacker the prefix matches a real (just-expired) key. The dashboard
    sees the expiry directly via ``GET /v1/keys`` so legitimate users
    get a clear answer through the management API instead.
    """
    prefix = extract_prefix(plaintext)
    if prefix is None:
        return None

    # All non-revoked candidates with matching prefix. In practice this is 1 row
    # (collisions on 14 base62 chars are negligible) — but multi-row support
    # keeps the code correct against future widening.
    stmt = (
        select(ApiKey)
        .where(ApiKey.key_prefix == prefix)
        .where(ApiKey.status.in_([ApiKeyStatus.ACTIVE, ApiKeyStatus.ROTATING]))
    )
    rows = (await db.execute(stmt)).scalars().all()
    now = datetime.now(UTC)
    for api_key in rows:
        if not verify_api_key(plaintext, api_key.key_hash):
            continue
        # TD-009 — opt-in expiry. NULL means "never expires" (backwards
        # compat with all keys created before Alembic 0008).
        # SQLite returns offset-naive datetimes; normalise to UTC-aware
        # so the comparison is well-defined on both Postgres and SQLite.
        if api_key.expires_at is not None:
            expires_at = api_key.expires_at
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=UTC)
            if expires_at <= now:
                log.info(
                    "auth_key_expired",
                    api_key_id=str(api_key.id),
                    expires_at=expires_at.isoformat(),
                )
                return None
        account = await db.get(Account, api_key.account_id)
        if account is None:
            return None
        # Sprint 7 — fully-closed accounts (post-cron) are dead. The
        # 30-day grace period (closure_scheduled_at in the future,
        # closed_at IS NULL) is intentionally NOT blocked here: the
        # user should keep working until the cron actually purges them.
        if account.closed_at is not None:
            log.info(
                "auth_account_closed",
                api_key_id=str(api_key.id),
                account_id=str(account.id),
            )
            return None
        if account.status != AccountStatus.ACTIVE:
            return None
        return api_key, account
    return None


async def require_api_key(
    request: Request,
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> AuthPrincipal:
    """FastAPI dependency: resolve and authenticate the bearer token."""
    plaintext = _extract_bearer(authorization)
    if not plaintext:
        raise authentication_error("Missing Bearer token in Authorization header.")

    settings = get_settings()
    redis_client = get_redis()
    cache_key = _cache_key(plaintext)

    # 1) Redis fast-path
    if redis_client is not None:
        try:
            cached = await redis_client.get(cache_key)
        except Exception as exc:
            log.warning("auth_redis_get_failed", error=str(exc))
            cached = None
        if cached:
            try:
                payload = json.loads(cached if isinstance(cached, str) else cached.decode("utf-8"))
                principal = AuthPrincipal.from_cache_dict(payload)
                _attach_to_request(request, principal)
                # Prometheus — cache hit on the auth path. Best-effort,
                # never breaks the request when metrics are off.
                try:
                    from voltari_gateway.utils.observability import record_cache

                    record_cache(request.app, cache_name="auth", hit=True)
                except Exception:
                    pass
                return principal
            except Exception as exc:
                log.warning("auth_cache_decode_failed", error=str(exc))
        # Either no cached entry or decode failed — count as miss so the
        # cache_hit_ratio dashboard correctly attributes the DB lookup that
        # follows.
        try:
            from voltari_gateway.utils.observability import record_cache

            record_cache(request.app, cache_name="auth", hit=False)
        except Exception:
            pass

    # 2) DB verify
    result = await _verify_against_db(plaintext, db)
    if result is None:
        raise authentication_error("Invalid or revoked API key.")
    api_key, account = result

    principal = AuthPrincipal(
        account_id=account.id,
        api_key_id=api_key.id,
        user_id=account.owner_id,
        tariff=account.tariff.value,
        balance_kopecks=account.balance_kopecks,
        store_prompts=account.store_prompts,
        pii_masking_enabled=account.pii_masking_enabled,
        routing_mode=account.routing_mode,
        routing_strategy=account.routing_strategy,
        routing_allowed_providers=(
            tuple(account.routing_allowed_providers)
            if account.routing_allowed_providers is not None
            else None
        ),
        routing_allowed_models=(
            tuple(account.routing_allowed_models)
            if account.routing_allowed_models is not None
            else None
        ),
        smart_router_v2_enabled=account.smart_router_v2_enabled,
    )

    # 3) Touch last_used_at without blocking the request on commit failures.
    try:
        api_key.last_used_at = datetime.now(UTC)
        await db.commit()
    except Exception as exc:
        log.warning("auth_last_used_update_failed", error=str(exc))
        await db.rollback()

    # 4) Cache success — also write a key_id → cache_key index entry so the
    # management API can invalidate this specific cache record on revoke
    # without scanning the keyspace.
    # Sprint 5 perf: pipeline both SETEX into one RTT. With Redis on the
    # same host this is ~0.2-0.4 ms saved per auth-cache miss; the win
    # adds up for first-traffic SDK clients that hit a cold cache.
    if redis_client is not None:
        try:
            async with redis_client.pipeline(transaction=False) as pipe:
                pipe.setex(
                    cache_key,
                    settings.auth_cache_ttl_seconds,
                    json.dumps(principal.to_cache_dict()),
                )
                pipe.setex(
                    _cache_index_key(principal.api_key_id),
                    settings.auth_cache_ttl_seconds,
                    cache_key,
                )
                await pipe.execute()
        except Exception as exc:
            log.warning("auth_redis_set_failed", error=str(exc))

    _attach_to_request(request, principal)
    return principal


def _attach_to_request(request: Request, principal: AuthPrincipal) -> None:
    request.state.account_id = principal.account_id
    request.state.api_key_id = principal.api_key_id
    request.state.user_id = principal.user_id
    request.state.principal = principal


# --------------------------------------------------------------------------
# Dual auth (Bearer OR cookie session) — used by /v1/billing/* so the same
# endpoint serves both M2M traffic (CLI/SDK with sk-vlt-... bearer) and the
# browser dashboard (cookie-authenticated SPA on /app/billing).
#
# Design notes:
#   * Bearer is tried first — cheap path, already cached in Redis.
#   * Cookie path is only consulted if Bearer is missing or invalid. This is
#     intentional: a request that explicitly carries a Bearer header should
#     never silently fall through to cookies (would mask broken keys).
#   * CSRF (double-submit X-CSRF-Token + vlt_csrf cookie) is enforced ONLY
#     for cookie auth on mutating verbs. Bearer flow is by definition not
#     CSRF-able — there are no ambient credentials a third-party site
#     could replay. Legacy ``X-Requested-With`` fallback was removed in
#     Sprint 3 Поток H (TD-036).
#   * The unified ``Principal`` carries ``auth_method`` so handlers and
#     audit logs can tell the two apart. ``user`` and ``api_key_id`` are
#     populated only when relevant.
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Principal:
    """Unified caller identity, regardless of auth method.

    For Bearer-token auth: ``api_key_id`` is set, ``user`` is None.
    For cookie-session auth: ``user`` is set (full ORM row),
    ``api_key_id`` is None.
    """

    account_id: uuid.UUID
    user_id: uuid.UUID
    tariff: str
    balance_kopecks: int
    store_prompts: bool
    auth_method: str  # "api_key" | "session"
    api_key_id: uuid.UUID | None = None
    user: User | None = None
    account: Account | None = None


async def _try_api_key(
    request: Request,
    authorization: str | None,
    db: AsyncSession,
) -> Principal | None:
    """Resolve a Bearer token to a Principal. None on any failure (no raise).

    Mirrors ``require_api_key`` but is non-fatal: if the header is missing
    or the key is invalid, return None so the cookie path can be tried.
    Genuine surprises (Redis I/O, DB errors) propagate — those aren't
    "no auth" cases.
    """
    plaintext = _extract_bearer(authorization)
    if not plaintext:
        return None

    settings = get_settings()
    redis_client = get_redis()
    cache_key = _cache_key(plaintext)

    # 1) Redis fast-path
    if redis_client is not None:
        try:
            cached = await redis_client.get(cache_key)
        except Exception as exc:
            log.warning("auth_redis_get_failed", error=str(exc))
            cached = None
        if cached:
            try:
                payload = json.loads(cached if isinstance(cached, str) else cached.decode("utf-8"))
                auth_p = AuthPrincipal.from_cache_dict(payload)
                _attach_to_request(request, auth_p)
                return Principal(
                    account_id=auth_p.account_id,
                    user_id=auth_p.user_id,
                    tariff=auth_p.tariff,
                    balance_kopecks=auth_p.balance_kopecks,
                    store_prompts=auth_p.store_prompts,
                    auth_method="api_key",
                    api_key_id=auth_p.api_key_id,
                )
            except Exception as exc:
                log.warning("auth_cache_decode_failed", error=str(exc))

    # 2) DB verify
    result = await _verify_against_db(plaintext, db)
    if result is None:
        return None
    api_key, account = result

    auth_p = AuthPrincipal(
        account_id=account.id,
        api_key_id=api_key.id,
        user_id=account.owner_id,
        tariff=account.tariff.value,
        balance_kopecks=account.balance_kopecks,
        store_prompts=account.store_prompts,
        pii_masking_enabled=account.pii_masking_enabled,
        routing_mode=account.routing_mode,
        routing_strategy=account.routing_strategy,
        routing_allowed_providers=(
            tuple(account.routing_allowed_providers)
            if account.routing_allowed_providers is not None
            else None
        ),
        routing_allowed_models=(
            tuple(account.routing_allowed_models)
            if account.routing_allowed_models is not None
            else None
        ),
        smart_router_v2_enabled=account.smart_router_v2_enabled,
    )

    try:
        api_key.last_used_at = datetime.now(UTC)
        await db.commit()
    except Exception as exc:
        log.warning("auth_last_used_update_failed", error=str(exc))
        await db.rollback()

    if redis_client is not None:
        # Same pipeline-batching as require_api_key — see note there.
        try:
            async with redis_client.pipeline(transaction=False) as pipe:
                pipe.setex(
                    cache_key,
                    settings.auth_cache_ttl_seconds,
                    json.dumps(auth_p.to_cache_dict()),
                )
                pipe.setex(
                    _cache_index_key(auth_p.api_key_id),
                    settings.auth_cache_ttl_seconds,
                    cache_key,
                )
                await pipe.execute()
        except Exception as exc:
            log.warning("auth_redis_set_failed", error=str(exc))

    _attach_to_request(request, auth_p)
    return Principal(
        account_id=auth_p.account_id,
        user_id=auth_p.user_id,
        tariff=auth_p.tariff,
        balance_kopecks=auth_p.balance_kopecks,
        store_prompts=auth_p.store_prompts,
        auth_method="api_key",
        api_key_id=auth_p.api_key_id,
        account=account,
    )


async def _try_session(
    request: Request,
    vlt_access: str | None,
    db: AsyncSession,
) -> Principal | None:
    """Resolve a session cookie to a Principal. None on any failure.

    Imported lazily to dodge a circular import (`session_middleware`
    reaches into this module for ``get_redis``).
    """
    if not vlt_access:
        return None

    # Local import — session_middleware imports from this module via
    # `auth.cookies` -> `auth.session` chain at startup.
    from voltari_gateway.auth.session import verify_access_token

    claims = verify_access_token(vlt_access)
    if claims is None:
        return None

    user = await db.get(User, claims.user_id)
    if user is None:
        return None
    account = await db.get(Account, claims.account_id)
    if account is None:
        return None
    # See note in ``_verify_against_db`` — only the post-cron CLOSED
    # state blocks here. The 30-day grace window keeps working.
    if account.closed_at is not None:
        return None
    if account.status != AccountStatus.ACTIVE:
        return None

    # Make state available to downstream code (e.g. audit log) the same way
    # `require_session` does — handlers can rely on either path.
    request.state.user = user
    request.state.account = account
    request.state.account_id = account.id
    request.state.user_id = user.id

    return Principal(
        account_id=account.id,
        user_id=user.id,
        tariff=account.tariff.value,
        balance_kopecks=account.balance_kopecks,
        store_prompts=account.store_prompts,
        auth_method="session",
        user=user,
        account=account,
    )


async def require_api_key_or_session(
    request: Request,
    authorization: str | None = Header(default=None),
    vlt_access: str | None = Cookie(default=None),
    csrf_cookie: str | None = Cookie(default=None, alias=CSRF_COOKIE),
    csrf_header: str | None = Header(default=None, alias=CSRF_HEADER),
    db: AsyncSession = Depends(get_db),
) -> Principal:
    """FastAPI dependency: accept either Bearer or cookie session.

    Resolution order: Bearer first, cookie second. CSRF check applies only
    to the cookie path (Bearer is immune by construction).

    Raises:
        GatewayError 401 — neither method authenticates.
        GatewayError 403 — cookie session present, but mutating request
                           lacks the double-submit CSRF artefacts
                           (``X-CSRF-Token`` header == ``vlt_csrf`` cookie).
    """
    bearer = await _try_api_key(request, authorization, db)
    if bearer is not None:
        return bearer

    session = await _try_session(request, vlt_access, db)
    if session is not None:
        verify_csrf(request, csrf_cookie=csrf_cookie, csrf_header=csrf_header)
        return session

    raise authentication_error("Authentication required (Bearer token or session cookie).")
