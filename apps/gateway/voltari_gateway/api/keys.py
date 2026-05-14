"""Management API — API-key CRUD.

Mounted under ``/v1/keys``. All endpoints are session-cookie protected via
``require_session`` and enforce double-submit CSRF (``X-CSRF-Token`` header
== ``vlt_csrf`` cookie) on mutating verbs (TD-036, Sprint 3 Поток H).

Surface:

    GET    /v1/keys           — list keys for the active account
    POST   /v1/keys           — create a key (returns plaintext **once**)
    PATCH  /v1/keys/{id}      — rename only (scope is immutable post-create)
    DELETE /v1/keys/{id}      — soft-delete: status=REVOKED + revoked_at=now()

Security model:

* Plaintext is generated server-side, hashed with argon2, then **discarded**.
  The DB only ever holds ``key_hash`` + ``key_prefix`` (first 14 chars).
  ``POST`` is the only path that returns the plaintext — listing or fetching
  later returns the prefix only.
* Ownership is enforced on every PATCH/DELETE: ``api_key.account_id ==
  session.account_id``. A 404 (not 403) is returned for keys belonging to
  other accounts so we don't leak existence.
* Revoke = ``status=REVOKED`` + ``revoked_at=now()``. The bearer-auth cache
  in Redis is also explicitly purged via the ``api_key_id → cache_key``
  index, so a freshly-revoked key 401s on the next chat call (no 60s wait
  for the cache TTL).

Tariff limits (per CEO 28.04 pricing):

    PAYG = 3, PRO = 10, TEAM = 30, BUSINESS = 100, BUSINESS_PLUS = unlimited.

Counted against **non-revoked** keys only — revoking a key frees a slot.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, Path, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from voltari_gateway.auth.audit import write_audit
from voltari_gateway.auth.keys import generate_api_key
from voltari_gateway.auth.middleware import get_redis, invalidate_cache_for_key
from voltari_gateway.auth.session_middleware import (
    SessionPrincipal,
    require_session,
)
from voltari_gateway.db.models import (
    ApiKey,
    ApiKeyScope,
    ApiKeyStatus,
    Tariff,
)
from voltari_gateway.db.session import get_db
from voltari_gateway.utils.errors import GatewayError
from voltari_gateway.utils.logging import get_logger

log = get_logger(__name__)

router = APIRouter(tags=["keys"])

# Per-tariff active-key cap. ``None`` = unlimited (Business+ — individually
# negotiated). Counted against status != REVOKED so revoke frees a slot.
KEY_LIMITS_BY_TARIFF: dict[Tariff, int | None] = {
    Tariff.PAYG: 3,
    Tariff.PRO: 10,
    Tariff.TEAM: 30,
    Tariff.BUSINESS: 100,
    Tariff.BUSINESS_PLUS: None,
}


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------


class KeyListItem(BaseModel):
    """Compact key descriptor — never includes plaintext."""

    id: str
    name: str
    prefix: str
    scope: str
    status: str
    last_used_at: datetime | None = None
    created_at: datetime
    revoked_at: datetime | None = None
    expires_at: datetime | None = None


# Allowed expiry presets. Open-ended ``int`` is rejected to keep the dashboard
# consistent and to make policy decisions (revocation reminders, audit) easy.
ExpiresInDays = Literal[30, 90, 180, 365]


class CreateKeyRequest(BaseModel):
    """Body of POST /v1/keys.

    ``scope`` exposes the API-friendly literals ``read|write|all``. We map
    ``"all"`` onto ``ApiKeyScope.WRITE`` server-side because the underlying
    enum has only two states; "all" is reserved for V2 when admin / billing
    scopes are added.

    TD-009: ``expires_in_days`` is an opt-in expiry preset (30, 90, 180,
    365). Omitting the field keeps the legacy "never expires" behaviour
    so existing SDK callers don't break.
    """

    name: str = Field(min_length=1, max_length=255)
    scope: str = Field(default="write", pattern=r"^(read|write|all)$")
    expires_in_days: ExpiresInDays | None = Field(default=None)


class CreateKeyResponse(BaseModel):
    """One-shot reveal of the plaintext. Never log this body."""

    id: str
    name: str
    full_key: str
    prefix: str
    scope: str
    created_at: datetime
    expires_at: datetime | None = None


class UpdateKeyRequest(BaseModel):
    """PATCH body — only ``name`` is honoured.

    ``scope`` would be silently dropped by Pydantic (extra="ignore" default).
    We explicitly reject it with 400 so the SPA fails fast instead of the user
    thinking the change went through.
    """

    name: str = Field(min_length=1, max_length=255)

    model_config = {"extra": "forbid"}


# ---- Bulk operations (Sprint 8 F5) ----------------------------------------
#
# Hard cap on the number of keys per bulk call. 50 covers any realistic
# rotation campaign (Business plan = 100 keys; ops would split a full
# rotation into 2 batches). Pasting 1000 IDs in one POST is almost
# certainly a copy-paste mistake / abuse — refuse with 400.

BULK_MAX_ITEMS: int = 50

# Grace period for rotated keys before they actually 401.
BULK_ROTATE_GRACE_DAYS: int = 7


class BulkKeyIdsRequest(BaseModel):
    """Body for both bulk-revoke and bulk-rotate.

    ``key_ids`` MUST be unique — duplicates are silently de-duplicated
    server-side rather than 400'ing, since duplicate UUIDs in the
    payload don't break the operation, just inflate the count.
    """

    key_ids: list[uuid.UUID] = Field(min_length=1, max_length=BULK_MAX_ITEMS)

    model_config = {"extra": "forbid"}


class BulkRevokeResponse(BaseModel):
    """Per-key outcome for ``POST /v1/keys/bulk-revoke``.

    ``revoked`` are the keys we actually flipped to status=REVOKED (this
    call). ``skipped`` are keys that were already revoked (we don't
    re-stamp ``revoked_at`` so the audit trail stays clean) or weren't
    owned by the caller. Cross-account IDs are silently dropped from the
    result entirely — see ``_get_owned_key`` rationale: same response
    whether the key exists or not, prevents enumeration.
    """

    revoked: list[str]
    skipped: list[str]
    not_found: list[str]


class BulkRotatedKey(BaseModel):
    """One element of ``POST /v1/keys/bulk-rotate``'s response.

    ``new_full_key`` is the only place the plaintext appears — same
    one-shot reveal contract as POST /v1/keys.
    """

    old_id: str
    old_prefix: str
    new_id: str
    new_full_key: str
    new_prefix: str
    grace_until: datetime


class BulkRotateResponse(BaseModel):
    rotated: list[BulkRotatedKey]
    skipped: list[str]
    not_found: list[str]
    grace_days: int


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _to_list_item(api_key: ApiKey) -> KeyListItem:
    return KeyListItem(
        id=str(api_key.id),
        name=api_key.name,
        prefix=api_key.key_prefix,
        scope=api_key.scope.value,
        status=api_key.status.value,
        last_used_at=api_key.last_used_at,
        created_at=api_key.created_at,
        revoked_at=api_key.revoked_at,
        expires_at=api_key.expires_at,
    )


def _resolve_scope(api_scope: str) -> ApiKeyScope:
    """Map API literal ``read|write|all`` to the DB enum.

    ``"all"`` maps to WRITE for now (the admin / billing scopes don't exist
    in the enum yet). Returning a 400 instead would surprise SPA users who
    pick the obvious-looking option.
    """
    if api_scope == "read":
        return ApiKeyScope.READ
    return ApiKeyScope.WRITE


async def _count_active_keys(db: AsyncSession, account_id: uuid.UUID) -> int:
    """Count non-revoked keys for the account (limit-check denominator)."""
    stmt = (
        select(func.count())
        .select_from(ApiKey)
        .where(ApiKey.account_id == account_id)
        .where(ApiKey.status != ApiKeyStatus.REVOKED)
    )
    return int((await db.execute(stmt)).scalar_one())


async def _get_owned_key(db: AsyncSession, key_id: uuid.UUID, account_id: uuid.UUID) -> ApiKey:
    """Fetch a key that belongs to ``account_id`` or 404.

    Returning 404 (not 403) for keys of *other* accounts avoids leaking key
    existence — an attacker probing UUIDs sees the same response regardless
    of whether the key exists.
    """
    api_key = await db.get(ApiKey, key_id)
    if api_key is None or api_key.account_id != account_id:
        raise GatewayError(
            status_code=404,
            message="API key not found.",
            type="invalid_request_error",
            code="key_not_found",
        )
    return api_key


# ---------------------------------------------------------------------------
# 1) GET /v1/keys
# ---------------------------------------------------------------------------


@router.get(
    "",
    response_model=list[KeyListItem],
    summary="List API keys for the active account",
    description=(
        "Returns active and revoked keys (sorted by creation time, newest "
        "first). Plaintext is **never** returned here — only ``prefix`` (first "
        "14 chars). Frontend filters revoked client-side."
    ),
    responses={
        200: {"description": "List of keys (possibly empty)."},
        401: {"description": "Session cookie missing or expired."},
    },
)
async def list_keys(
    db: AsyncSession = Depends(get_db),
    principal: SessionPrincipal = Depends(require_session),
) -> list[KeyListItem]:
    """List all keys for the active account, including revoked ones.

    Frontend filters revoked / active client-side so the audit log stays
    visible. Sort by ``created_at DESC`` so the newest is on top.
    """
    stmt = (
        select(ApiKey)
        .where(ApiKey.account_id == principal.account.id)
        .order_by(ApiKey.created_at.desc())
    )
    result = await db.execute(stmt)
    return [_to_list_item(k) for k in result.scalars().all()]


# ---------------------------------------------------------------------------
# 2) POST /v1/keys
# ---------------------------------------------------------------------------


@router.post(
    "",
    response_model=CreateKeyResponse,
    status_code=201,
    summary="Create a new API key",
    description=(
        "Generates a fresh ``sk-vlt-...`` key. The plaintext is shown **only "
        "once** in the response — it's not recoverable later. Optional "
        "``expires_in_days`` (30/90/180/365) sets a hard expiry; omit for "
        "the legacy never-expires behaviour."
    ),
    responses={
        201: {"description": "Key created; plaintext returned **once**."},
        401: {"description": "Session cookie missing or expired."},
        403: {"description": "Tariff per-key limit reached."},
    },
)
async def create_key(
    payload: CreateKeyRequest,
    db: AsyncSession = Depends(get_db),
    principal: SessionPrincipal = Depends(require_session),
) -> CreateKeyResponse:
    """Create a new API key.

    The plaintext is shown **once** in this response. After this call there
    is no server-side way to recover it — only ``key_hash`` + ``key_prefix``
    are persisted. Tariff limits enforced before generation so we don't burn
    crypto on a request we'll reject.
    """
    account = principal.account

    # --- Tariff cap ---
    limit = KEY_LIMITS_BY_TARIFF.get(account.tariff)
    if limit is not None:
        active = await _count_active_keys(db, account.id)
        if active >= limit:
            raise GatewayError(
                status_code=403,
                message=(
                    f"Tariff '{account.tariff.value}' allows up to {limit} "
                    f"active keys. Revoke an existing key or upgrade."
                ),
                type="invalid_request_error",
                code="key_limit_reached",
            )

    scope_enum = _resolve_scope(payload.scope)

    expires_at: datetime | None = None
    if payload.expires_in_days is not None:
        expires_at = datetime.now(UTC) + timedelta(days=payload.expires_in_days)

    # --- Generate + persist ---
    generated = generate_api_key()
    api_key = ApiKey(
        account_id=account.id,
        name=payload.name.strip(),
        key_hash=generated.key_hash,
        key_prefix=generated.prefix,
        scope=scope_enum,
        status=ApiKeyStatus.ACTIVE,
        expires_at=expires_at,
    )
    db.add(api_key)
    await db.commit()
    await db.refresh(api_key)

    log.info(
        "api_key_created",
        account_id=str(account.id),
        key_id=str(api_key.id),
        scope=scope_enum.value,
        expires_at=expires_at.isoformat() if expires_at else None,
    )

    return CreateKeyResponse(
        id=str(api_key.id),
        name=api_key.name,
        full_key=generated.plaintext,
        prefix=api_key.key_prefix,
        scope=api_key.scope.value,
        created_at=api_key.created_at,
        expires_at=api_key.expires_at,
    )


# ---------------------------------------------------------------------------
# 3) PATCH /v1/keys/{key_id}
# ---------------------------------------------------------------------------


@router.patch(
    "/{key_id}",
    response_model=KeyListItem,
    summary="Rename an API key",
    description=(
        "Updates only the ``name``. ``scope`` is intentionally immutable — to "
        "change permissions, create a new key and revoke the old one."
    ),
    responses={
        200: {"description": "Key renamed."},
        400: {"description": "Body contained ``scope`` (forbidden field)."},
        404: {"description": "Key not found or belongs to another account."},
    },
)
async def update_key(
    payload: UpdateKeyRequest,
    key_id: uuid.UUID = Path(...),
    db: AsyncSession = Depends(get_db),
    principal: SessionPrincipal = Depends(require_session),
) -> KeyListItem:
    """Rename a key. Scope is intentionally immutable post-create — to change
    permissions, the user creates a new key and revokes the old one.

    Rejecting an existing-but-revoked key here would surprise users who want
    to update names of revoked keys (e.g. for forensic notes). We allow it.
    """
    api_key = await _get_owned_key(db, key_id, principal.account.id)
    api_key.name = payload.name.strip()
    await db.commit()
    await db.refresh(api_key)
    return _to_list_item(api_key)


# ---------------------------------------------------------------------------
# 4) DELETE /v1/keys/{key_id}
# ---------------------------------------------------------------------------


@router.delete(
    "/{key_id}",
    status_code=204,
    summary="Revoke an API key (soft delete)",
    description=(
        "Sets ``status=REVOKED`` + ``revoked_at=now()``. Bearer auth Redis "
        "cache is purged immediately so the next chat call 401s in <1s. The "
        "row is NOT hard-deleted — it stays for audit/forensics."
    ),
    responses={
        204: {"description": "Key revoked."},
        404: {"description": "Key not found, already revoked, or other account."},
    },
)
async def revoke_key(
    key_id: uuid.UUID = Path(...),
    db: AsyncSession = Depends(get_db),
    principal: SessionPrincipal = Depends(require_session),
) -> Response:
    """Soft-delete an API key.

    Hard-delete is intentionally avoided: revoke must be reversible-by-audit
    even if the key itself can never be reactivated, and the foreign key
    from ``usage_events.api_key_id`` would orphan rows on a hard delete.

    Cache invalidation: the bearer-auth Redis cache key is keyed by the
    plaintext-hash, which we don't store. We use the
    ``auth:key:by_id:<key_id>`` index written by the middleware to purge the
    cache entry (if any). Without this, a revoked key would still pass auth
    for up to ``AUTH_CACHE_TTL_SECONDS`` (default 60s) — too slow.
    """
    api_key = await _get_owned_key(db, key_id, principal.account.id)

    if api_key.status == ApiKeyStatus.REVOKED:
        # Already revoked — treat as 404 so callers can't probe whether a key
        # *was* revoked (timing-side-channel for forensic enumeration).
        raise GatewayError(
            status_code=404,
            message="API key not found.",
            type="invalid_request_error",
            code="key_not_found",
        )

    api_key.status = ApiKeyStatus.REVOKED
    api_key.revoked_at = datetime.now(UTC)
    await db.commit()

    # Best-effort cache purge — if Redis is down the worst case is the key
    # still passes auth until the cache entry expires (≤ 60s).
    redis = get_redis()
    await invalidate_cache_for_key(redis, api_key.id)

    log.info(
        "api_key_revoked",
        account_id=str(principal.account.id),
        key_id=str(api_key.id),
    )
    return Response(status_code=204)


# ---------------------------------------------------------------------------
# 5) POST /v1/keys/bulk-revoke
# ---------------------------------------------------------------------------


async def _load_owned_keys(
    db: AsyncSession,
    *,
    account_id: uuid.UUID,
    key_ids: list[uuid.UUID],
) -> dict[uuid.UUID, ApiKey]:
    """Load keys belonging to ``account_id``; foreign keys silently dropped.

    Returns a dict keyed by key_id so callers can iterate the requested
    list and detect missing IDs in O(1).
    """
    if not key_ids:
        return {}
    stmt = select(ApiKey).where(
        ApiKey.account_id == account_id,
        ApiKey.id.in_(key_ids),
    )
    rows = (await db.execute(stmt)).scalars().all()
    return {k.id: k for k in rows}


@router.post(
    "/bulk-revoke",
    response_model=BulkRevokeResponse,
    summary="Revoke up to 50 API keys in one call",
    description=(
        "Marks the listed keys as ``status=REVOKED`` in a single transaction. "
        "Keys already revoked or owned by another account are reported in "
        "``skipped`` / ``not_found`` rather than failing the whole call. "
        "Bearer-auth Redis cache is purged for each successfully-revoked key "
        "so the next chat request 401s in <1s."
    ),
    responses={
        200: {"description": "Bulk revoke executed (partial successes allowed)."},
        400: {"description": "More than 50 IDs requested or empty list."},
    },
)
async def bulk_revoke_keys(
    payload: BulkKeyIdsRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    principal: SessionPrincipal = Depends(require_session),
) -> BulkRevokeResponse:
    """Revoke many keys at once.

    Same per-key semantics as DELETE /v1/keys/{id} (soft delete +
    cache purge). One audit_log row is written for the *bulk* event
    rather than N rows so the timeline stays readable; the meta
    captures the full list of revoked IDs for forensics.
    """
    # De-dupe — duplicates inflate counts but don't change behaviour.
    requested = list(dict.fromkeys(payload.key_ids))
    owned = await _load_owned_keys(db, account_id=principal.account.id, key_ids=requested)

    revoked_ids: list[str] = []
    skipped_ids: list[str] = []
    not_found_ids: list[str] = []
    revoked_now: list[ApiKey] = []
    now = datetime.now(UTC)

    for kid in requested:
        api_key = owned.get(kid)
        if api_key is None:
            not_found_ids.append(str(kid))
            continue
        if api_key.status == ApiKeyStatus.REVOKED:
            skipped_ids.append(str(kid))
            continue
        api_key.status = ApiKeyStatus.REVOKED
        api_key.revoked_at = now
        revoked_ids.append(str(api_key.id))
        revoked_now.append(api_key)

    # Single audit row for the batch — keeps the activity feed tidy.
    if revoked_ids:
        await write_audit(
            db,
            user_id=principal.user.id,
            account_id=principal.account.id,
            action="api_keys_bulk_revoked",
            request=request,
            meta={"count": len(revoked_ids), "key_ids": revoked_ids},
        )
    await db.commit()

    # Best-effort cache purge — Redis-down means revoked keys still pass
    # auth for ≤AUTH_CACHE_TTL_SECONDS (60s). Acceptable trade-off vs.
    # blocking the bulk on Redis availability.
    redis = get_redis()
    for k in revoked_now:
        try:
            await invalidate_cache_for_key(redis, k.id)
        except Exception as exc:  # pragma: no cover — defensive
            log.warning(
                "bulk_revoke_cache_purge_failed",
                key_id=str(k.id),
                error=str(exc),
            )

    log.info(
        "api_keys_bulk_revoked",
        account_id=str(principal.account.id),
        revoked=len(revoked_ids),
        skipped=len(skipped_ids),
        not_found=len(not_found_ids),
    )
    return BulkRevokeResponse(
        revoked=revoked_ids,
        skipped=skipped_ids,
        not_found=not_found_ids,
    )


# ---------------------------------------------------------------------------
# 6) POST /v1/keys/bulk-rotate
# ---------------------------------------------------------------------------


@router.post(
    "/bulk-rotate",
    response_model=BulkRotateResponse,
    summary="Rotate up to 50 API keys with a 7-day grace window",
    description=(
        "For each input key, generates a fresh plaintext (returned **once**) "
        "and stamps ``expires_at = now + 7d`` on the OLD key — giving the "
        "customer a week to swap deployments before the old token 401s. "
        "Old keys keep ``status=ACTIVE`` so traffic continues during the "
        "grace; expiry is enforced by the bearer-auth pipeline (TD-009). "
        "Tariff key cap is applied to the **new** keys only — exceeding "
        "the cap fails the whole batch with 403 ``key_limit_reached``."
    ),
    responses={
        200: {"description": "Bulk rotate executed."},
        400: {"description": "More than 50 IDs or empty list."},
        403: {"description": "Tariff key cap would be exceeded by the new keys."},
    },
)
async def bulk_rotate_keys(
    payload: BulkKeyIdsRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    principal: SessionPrincipal = Depends(require_session),
) -> BulkRotateResponse:
    """Rotate many keys at once.

    Each rotation = (a) generate a new plaintext + persist hash + prefix,
    (b) set ``expires_at`` on the old key to ``now + 7d``. Old keys
    remain ACTIVE during the grace window so existing deployments keep
    working until the customer swaps tokens.

    Tariff cap check is on the **post-rotation** active count: each
    rotation adds 1 (the new key); expiring the old key doesn't free
    its slot until ``expires_at`` passes. We pre-compute the projected
    count and reject the whole batch up-front so we don't half-rotate.
    """
    requested = list(dict.fromkeys(payload.key_ids))
    owned = await _load_owned_keys(db, account_id=principal.account.id, key_ids=requested)

    # Cap check: projected_active = current_active + len(rotatable)
    rotatable: list[ApiKey] = []
    skipped_ids: list[str] = []
    not_found_ids: list[str] = []
    for kid in requested:
        api_key = owned.get(kid)
        if api_key is None:
            not_found_ids.append(str(kid))
            continue
        if api_key.status == ApiKeyStatus.REVOKED:
            skipped_ids.append(str(kid))
            continue
        rotatable.append(api_key)

    if not rotatable:
        return BulkRotateResponse(
            rotated=[],
            skipped=skipped_ids,
            not_found=not_found_ids,
            grace_days=BULK_ROTATE_GRACE_DAYS,
        )

    limit = KEY_LIMITS_BY_TARIFF.get(principal.account.tariff)
    if limit is not None:
        active_now = await _count_active_keys(db, principal.account.id)
        # Each rotation adds one new key. Old keys stay ACTIVE during grace.
        projected = active_now + len(rotatable)
        if projected > limit:
            raise GatewayError(
                status_code=403,
                message=(
                    f"Tariff '{principal.account.tariff.value}' allows up to "
                    f"{limit} active keys; rotating {len(rotatable)} would push "
                    f"the count to {projected}. Revoke unused keys first."
                ),
                type="invalid_request_error",
                code="key_limit_reached",
            )

    grace_until = datetime.now(UTC) + timedelta(days=BULK_ROTATE_GRACE_DAYS)
    # Collect (old_key, new_key, plaintext) triples in one pass — the
    # plaintext only ever exists on this stack frame (we hash + discard
    # before returning).
    triples: list[tuple[ApiKey, ApiKey, str]] = []

    for old_key in rotatable:
        generated = generate_api_key()
        new_key = ApiKey(
            account_id=principal.account.id,
            name=f"{old_key.name} (rotated)",
            key_hash=generated.key_hash,
            key_prefix=generated.prefix,
            scope=old_key.scope,
            status=ApiKeyStatus.ACTIVE,
            expires_at=None,  # new key never expires (default)
        )
        db.add(new_key)
        # Old key gets a sunset clause. Status stays ACTIVE so existing
        # traffic doesn't 401 mid-deploy; expires_at gates the bearer
        # auth pipeline (TD-009 in middleware).
        old_key.expires_at = grace_until
        triples.append((old_key, new_key, generated.plaintext))

    # One flush so the new keys get IDs for the response.
    await db.flush()

    rotated_out: list[BulkRotatedKey] = [
        BulkRotatedKey(
            old_id=str(old_key.id),
            old_prefix=old_key.key_prefix,
            new_id=str(new_key.id),
            new_full_key=plaintext,
            new_prefix=new_key.key_prefix,
            grace_until=grace_until,
        )
        for old_key, new_key, plaintext in triples
    ]

    if rotated_out:
        await write_audit(
            db,
            user_id=principal.user.id,
            account_id=principal.account.id,
            action="api_keys_bulk_rotated",
            request=request,
            meta={
                "count": len(rotated_out),
                "old_ids": [r.old_id for r in rotated_out],
                "new_ids": [r.new_id for r in rotated_out],
                "grace_days": BULK_ROTATE_GRACE_DAYS,
            },
        )
    await db.commit()

    log.info(
        "api_keys_bulk_rotated",
        account_id=str(principal.account.id),
        rotated=len(rotated_out),
        skipped=len(skipped_ids),
        not_found=len(not_found_ids),
        grace_days=BULK_ROTATE_GRACE_DAYS,
    )
    return BulkRotateResponse(
        rotated=rotated_out,
        skipped=skipped_ids,
        not_found=not_found_ids,
        grace_days=BULK_ROTATE_GRACE_DAYS,
    )


__all__ = ["router"]
