"""Authorization-code persistence — hash at rest, single-use, TTL-checked.

Why hash at rest:

* The plaintext code is the ``code`` query-param sent in the
  redirect to the client. It is short-lived (10 min) but a leaked DB
  snapshot during that window would give an attacker a usable code.
  SHA-256 (no salt — input is high-entropy 32-byte token) is enough.
* SHA-256 is also fast — auth code exchange is on the user's
  onboarding hot path; we don't need argon2 here.

Why single-use:

* RFC 6749 §4.1.2 — "If an authorization code is used more than once,
  the authorization server MUST deny the request." We mark ``used_at``
  inside the same DB transaction that mints the access token; the
  UNIQUE on ``code_hash`` plus the ``used_at IS NOT NULL`` short-circuit
  guarantees idempotent rejection of replays.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from voltari_gateway.db.models import OAuthAuthorizationCode

CODE_BYTES = 32  # → 43-char URL-safe b64 string


class OAuthCodeError(ValueError):
    """OAuth-specific code errors. ``str(exc)`` is ``invalid_grant`` always."""


def _hash_code(plaintext: str) -> str:
    return hashlib.sha256(plaintext.encode("ascii")).hexdigest()


async def issue_authorization_code(
    db: AsyncSession,
    *,
    client_id: str,
    user_id: uuid.UUID,
    account_id: uuid.UUID,
    scopes: list[str],
    redirect_uri: str,
    code_challenge: str,
    code_challenge_method: str,
    ttl_seconds: int,
) -> str:
    """Mint a fresh auth code, persist its hash, return the plaintext.

    Caller is responsible for ``await db.commit()`` (this function only
    flushes so a subsequent ``consume_authorization_code`` in the same
    request can see the row if the test doesn't commit).
    """
    plaintext = secrets.token_urlsafe(CODE_BYTES)
    row = OAuthAuthorizationCode(
        code_hash=_hash_code(plaintext),
        client_id=client_id,
        user_id=user_id,
        account_id=account_id,
        scopes=scopes,
        redirect_uri=redirect_uri,
        code_challenge=code_challenge,
        code_challenge_method=code_challenge_method,
        expires_at=datetime.now(UTC) + timedelta(seconds=ttl_seconds),
    )
    db.add(row)
    await db.flush()
    return plaintext


async def consume_authorization_code(
    db: AsyncSession,
    plaintext: str,
    *,
    client_id: str,
) -> OAuthAuthorizationCode:
    """Atomically claim an unused, unexpired code for ``client_id``.

    Raises ``OAuthCodeError("invalid_grant")`` on:
        * unknown code
        * client_id mismatch
        * already used
        * expired

    On success, sets ``used_at`` and flushes (caller commits).
    """
    h = _hash_code(plaintext)
    row = (
        await db.execute(
            select(OAuthAuthorizationCode).where(OAuthAuthorizationCode.code_hash == h)
        )
    ).scalar_one_or_none()
    if row is None:
        raise OAuthCodeError("invalid_grant")
    if row.client_id != client_id:
        raise OAuthCodeError("invalid_grant")
    if row.used_at is not None:
        raise OAuthCodeError("invalid_grant")

    # Normalise SQLite's naive datetime to UTC-aware so the comparison is
    # well-defined (matches the pattern in auth/middleware.py).
    expires_at = row.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    if expires_at <= datetime.now(UTC):
        raise OAuthCodeError("invalid_grant")

    row.used_at = datetime.now(UTC)
    await db.flush()
    return row
