"""Signed, single-use tokens for email verification and password reset.

Uses ``itsdangerous.URLSafeTimedSerializer`` rather than JWT because:

1. The payloads are tiny (just a user_id + email or just user_id).
2. We don't need claim semantics — only a tamper-evident, time-bounded blob
   that's URL-safe.
3. Different *purposes* (verify vs reset) MUST produce non-interchangeable
   tokens; ``itsdangerous`` ``salt`` parameter gives us exactly this.

The token stored in the DB (``users.verification_token`` /
``password_reset_token``) is a **HMAC-SHA-256** of the plaintext token,
keyed by ``EMAIL_TOKEN_SECRET``. We deliberately do **not** use a plain
SHA-256 digest:

* A plain digest of an itsdangerous-style token leaks the token format. If
  the DB is leaked but the signing secret is not, an attacker can mint
  candidate plaintexts (timestamps, salts) and check the digest column to
  discover valid live tokens.
* HMAC with a secret pepper turns that database column into a useless blob
  for anyone without the pepper. Even if the attacker knows our token
  format, they can't precompute hashes without the key.

Comparisons go through ``hmac.compare_digest`` so a timing oracle can't
distinguish "wrong token but right user" from "no such token".
"""

from __future__ import annotations

import hmac
import uuid
from hashlib import sha256
from typing import Final

from itsdangerous import BadData, SignatureExpired, URLSafeTimedSerializer

from voltari_gateway.config import get_settings

_SALT_VERIFY: Final[str] = "voltari.email-verify.v1"
_SALT_RESET: Final[str] = "voltari.password-reset.v1"


def _serializer() -> URLSafeTimedSerializer:
    """Mint the signer used for itsdangerous payloads.

    Note: this uses ``JWT_SECRET`` (the signer's purpose is to make the
    *plaintext* token tamper-evident at verify time), distinct from
    ``EMAIL_TOKEN_SECRET`` (which only peppers the DB-stored hash).
    """
    return URLSafeTimedSerializer(get_settings().jwt_secret.get_secret_value())


def _hmac_key() -> bytes:
    """Return the HMAC key for hashing tokens at rest.

    Falls back to ``JWT_SECRET`` when ``EMAIL_TOKEN_SECRET`` is unset — which
    is only allowed in dev/test (the prod validator in ``config.py``
    rejects empty values for production-like envs). The fallback exists so
    a freshly cloned repo with a minimal ``.env`` still works locally.
    """
    settings = get_settings()
    pepper = settings.email_token_secret.get_secret_value()
    if not pepper:
        pepper = settings.jwt_secret.get_secret_value()
    return pepper.encode("utf-8")


def hash_token(plaintext: str) -> str:
    """HMAC-SHA-256 hex digest of the plaintext token.

    The DB-stored value of any single-use token. Safe to expose to logs
    only via redaction; safe to compare with ``hmac.compare_digest``.
    """
    return hmac.new(_hmac_key(), plaintext.encode("utf-8"), sha256).hexdigest()


def verify_token_hash(plaintext: str, expected_hash: str) -> bool:
    """Constant-time check that ``hash_token(plaintext) == expected_hash``."""
    candidate = hash_token(plaintext)
    return hmac.compare_digest(candidate, expected_hash)


# --- email verification ----------------------------------------------------


def generate_verification_token(user_id: uuid.UUID, email: str) -> str:
    """Mint an email-verification token. TTL is enforced at verify time."""
    return _serializer().dumps(
        {"user_id": str(user_id), "email": email},
        salt=_SALT_VERIFY,
    )


def verify_verification_token(token: str) -> tuple[uuid.UUID, str] | None:
    """Verify + decode. Returns ``(user_id, email)`` on success, None on any
    failure (signature, expiry, malformed payload).
    """
    settings = get_settings()
    max_age = settings.email_verification_ttl_hours * 3600
    try:
        payload = _serializer().loads(token, salt=_SALT_VERIFY, max_age=max_age)
    except SignatureExpired:
        return None
    except BadData:
        return None
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    try:
        return uuid.UUID(payload["user_id"]), str(payload["email"])
    except (KeyError, ValueError, TypeError):
        return None


# --- password reset --------------------------------------------------------


def generate_password_reset_token(user_id: uuid.UUID) -> str:
    """Mint a password-reset token. TTL enforced at verify time."""
    return _serializer().dumps({"user_id": str(user_id)}, salt=_SALT_RESET)


def verify_password_reset_token(token: str) -> uuid.UUID | None:
    """Verify + decode. Returns user_id on success, None otherwise."""
    settings = get_settings()
    max_age = settings.password_reset_ttl_minutes * 60
    try:
        payload = _serializer().loads(token, salt=_SALT_RESET, max_age=max_age)
    except SignatureExpired:
        return None
    except BadData:
        return None
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    try:
        return uuid.UUID(payload["user_id"])
    except (KeyError, ValueError, TypeError):
        return None
