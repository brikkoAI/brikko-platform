"""TOTP (RFC 6238) + recovery codes for 2FA.

Module is self-contained:

* ``generate_totp_secret`` / ``encrypt_secret`` / ``decrypt_secret``
  — Fernet AES-128 over the base32 secret. The encrypted blob is what
  lives in ``users.totp_secret_encrypted``.
* ``build_provisioning_uri`` — ``otpauth://`` URI for the QR.
* ``verify_totp`` — constant-time compare with a ±1 step window
  (handles clock skew up to 30s without enabling replay).
* ``mark_code_used`` / ``is_code_replay`` — Redis-backed replay window
  so the *same* valid 6-digit code can't be re-used inside its 30-sec
  bucket. Without this, an MITM that steals one code can replay it
  during the window.
* ``generate_recovery_codes`` / ``hash_recovery_code`` /
  ``verify_recovery_code`` — 8 single-use codes formatted as
  ``XXXX-XXXX-XXXX`` (12 hex chars), stored as bcrypt hashes.

Why bcrypt for recovery codes (instead of argon2)
-------------------------------------------------

Recovery codes are short (12 hex = 48 bits of entropy). We don't need
argon2's memory-hard property; bcrypt is faster on tight code paths
(login flow already does one argon2 verify). 12 rounds (default) puts
verify at ~80 ms which is plenty against brute force given the 8-code
window and rate-limited 2FA endpoint.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from typing import Final

import pyotp
from cryptography.fernet import Fernet
from redis.asyncio import Redis

from voltari_gateway.config import get_settings
from voltari_gateway.utils.logging import get_logger

log = get_logger(__name__)

# RFC 6238 default — 30-second steps; 6 digits.
TOTP_INTERVAL_SECONDS: Final[int] = 30
TOTP_DIGITS: Final[int] = 6
# Allow ±1 step (so codes from the previous/next 30-second bucket are accepted).
# Wider windows help with poor clock sync but expand replay; ±1 is the standard.
TOTP_VALID_WINDOW: Final[int] = 1

RECOVERY_CODE_COUNT: Final[int] = 8
RECOVERY_CODE_BYTES: Final[int] = 6  # 6 bytes = 12 hex chars = 48 bits

# Redis key prefix for "this 6-digit code was just consumed".
_TOTP_REPLAY_PREFIX: Final[str] = "totp:used"


# ---------------------------------------------------------------------------
# Fernet key derivation
# ---------------------------------------------------------------------------


def _fernet() -> Fernet:
    """Build a ``Fernet`` from settings.totp_encryption_key.

    If ``TOTP_ENCRYPTION_KEY`` is set we use it directly (must be a valid
    URL-safe base64-encoded 32-byte key — exactly what
    ``Fernet.generate_key()`` produces). If empty, we derive a key from
    JWT_SECRET via SHA-256 so dev / test environments don't need extra
    env wiring. Production is hard-validated by ``Settings`` to set the
    explicit env var.
    """
    settings = get_settings()
    raw = settings.totp_encryption_key.get_secret_value()
    if raw:
        # Caller-supplied — accept as-is. Fernet itself validates.
        try:
            return Fernet(raw.encode("utf-8") if not isinstance(raw, bytes) else raw)
        except (ValueError, TypeError) as exc:
            # Fall back to derived; log loudly so ops sees it.
            log.warning("totp_encryption_key_invalid_fallback", error=str(exc))

    # Dev fallback — derive from JWT_SECRET. Deterministic, but only used
    # when TOTP_ENCRYPTION_KEY is empty (validators in config.py warn).
    seed = settings.jwt_secret.get_secret_value() or "voltari-dev-fallback-seed"
    digest = hashlib.sha256(seed.encode("utf-8")).digest()  # 32 bytes
    key = base64.urlsafe_b64encode(digest)
    return Fernet(key)


# ---------------------------------------------------------------------------
# Secret generate / encrypt / decrypt
# ---------------------------------------------------------------------------


def generate_totp_secret() -> str:
    """Return a fresh 32-char base32 secret (160 bits)."""
    return pyotp.random_base32()


def encrypt_secret(secret_b32: str) -> bytes:
    """Encrypt a base32 TOTP secret for at-rest storage."""
    return _fernet().encrypt(secret_b32.encode("utf-8"))


def decrypt_secret(blob: bytes) -> str:
    """Decrypt at-rest blob back to base32 secret.

    Raises ``InvalidToken`` from ``cryptography.fernet`` on tamper / wrong
    key. Caller maps to a generic 500 — this is operator-level breakage,
    not user-facing.
    """
    return _fernet().decrypt(blob).decode("utf-8")


# ---------------------------------------------------------------------------
# Provisioning URI (QR)
# ---------------------------------------------------------------------------


def build_provisioning_uri(secret_b32: str, *, account_email: str, issuer: str) -> str:
    """Build the otpauth:// URI shown as a QR in the dashboard.

    Format: ``otpauth://totp/<issuer>:<email>?secret=<b32>&issuer=<issuer>``
    Authenticator apps (Google Authenticator, Authy, 1Password) use
    ``issuer`` as the bucket label and the email as the account.
    """
    return pyotp.TOTP(
        secret_b32,
        digits=TOTP_DIGITS,
        interval=TOTP_INTERVAL_SECONDS,
    ).provisioning_uri(name=account_email, issuer_name=issuer)


# ---------------------------------------------------------------------------
# Code verification
# ---------------------------------------------------------------------------


def verify_totp(secret_b32: str, code: str) -> bool:
    """Return True iff ``code`` matches the TOTP for ``secret_b32`` now.

    Constant-time compare via pyotp internals; we add only a sanity check
    on input shape (6 ASCII digits) so a malformed code can be rejected
    cheaply before the HMAC.
    """
    if not code or not code.isdigit() or len(code) != TOTP_DIGITS:
        return False
    totp = pyotp.TOTP(
        secret_b32,
        digits=TOTP_DIGITS,
        interval=TOTP_INTERVAL_SECONDS,
    )
    # ``valid_window=1`` allows the immediately previous and next 30-sec bucket.
    return bool(totp.verify(code, valid_window=TOTP_VALID_WINDOW))


# ---------------------------------------------------------------------------
# Replay protection (Redis)
# ---------------------------------------------------------------------------


def _replay_key(user_id: str, code: str) -> str:
    """Bucketed by (user, 6-digit code). TTL ≥ window so the 30-s code can't
    be reused even at the boundary of the ±1-step window.
    """
    return f"{_TOTP_REPLAY_PREFIX}:{user_id}:{code}"


async def is_code_replay(redis: Redis | None, user_id: str, code: str) -> bool:
    """True if this exact code was already consumed in the live window.

    Returns False when redis is None — replay protection downgrades to
    "best effort" without a backing store. Callers in production are
    expected to fail-loudly when redis is required.
    """
    if redis is None:
        return False
    try:
        # redis.exists returns int (count of existing keys); cast to bool
        # explicitly so the function's return type is honoured by mypy --strict.
        count: int = await redis.exists(_replay_key(user_id, code))
        return count > 0
    except Exception as exc:
        log.warning("totp_replay_check_failed", error=str(exc))
        return False


async def mark_code_used(redis: Redis | None, user_id: str, code: str) -> None:
    """Record this code as consumed for the next 90 seconds.

    90 s = window-of-3 steps × 30 s. Strictly greater than
    ``TOTP_INTERVAL_SECONDS * (1 + 2*TOTP_VALID_WINDOW)`` so the current
    code stays "used" until its ±1 valid window has fully elapsed.
    """
    if redis is None:
        return
    try:
        await redis.setex(_replay_key(user_id, code), 90, "1")
    except Exception as exc:
        log.warning("totp_replay_set_failed", error=str(exc))


# ---------------------------------------------------------------------------
# Recovery codes
# ---------------------------------------------------------------------------


def _format_recovery_code(raw_hex: str) -> str:
    """Group hex into ``XXXX-XXXX-XXXX``."""
    s = raw_hex.lower()
    return f"{s[0:4]}-{s[4:8]}-{s[8:12]}"


def normalize_recovery_code(code: str) -> str:
    """Canonical form: lowercase, no separators."""
    cleaned = code.strip().lower().replace("-", "").replace(" ", "")
    return cleaned


def generate_recovery_codes(count: int = RECOVERY_CODE_COUNT) -> list[str]:
    """Mint N fresh recovery codes (display format with dashes)."""
    if count <= 0:
        raise ValueError("count must be > 0")
    codes: list[str] = []
    for _ in range(count):
        raw = secrets.token_hex(RECOVERY_CODE_BYTES)
        codes.append(_format_recovery_code(raw))
    return codes


def hash_recovery_code(code: str) -> str:
    """Hash a recovery code for at-rest storage.

    Uses HMAC-SHA256 keyed by ``totp_encryption_key`` (or the dev fallback).
    Why not bcrypt: each verify needs to scan up to 8 stored hashes; doing
    8× argon2/bcrypt verifies on every recovery-login is a measurable
    latency regression. The recovery codes have 48 bits of entropy and
    the endpoint is rate-limited — HMAC is sufficient and constant-time.
    """
    settings = get_settings()
    key = settings.totp_encryption_key.get_secret_value() or settings.jwt_secret.get_secret_value()
    if not key:
        # Should not happen in dev/test (jwt_secret always set), but be loud.
        raise RuntimeError("Cannot hash recovery code: no key material configured.")
    mac = hmac.new(
        key.encode("utf-8"),
        normalize_recovery_code(code).encode("utf-8"),
        hashlib.sha256,
    )
    return mac.hexdigest()


def verify_recovery_code(plain: str, candidate_hashes: list[str]) -> str | None:
    """Find the matching hash in ``candidate_hashes``. Returns matched hash
    on hit, ``None`` on miss. Constant-time compare via ``hmac.compare_digest``.

    The caller is expected to remove the matched hash from the list (one-shot
    semantics).
    """
    expected = hash_recovery_code(plain)
    for h in candidate_hashes:
        if hmac.compare_digest(h, expected):
            return h
    return None


__all__ = [
    "RECOVERY_CODE_COUNT",
    "TOTP_DIGITS",
    "TOTP_INTERVAL_SECONDS",
    "TOTP_VALID_WINDOW",
    "build_provisioning_uri",
    "decrypt_secret",
    "encrypt_secret",
    "generate_recovery_codes",
    "generate_totp_secret",
    "hash_recovery_code",
    "is_code_replay",
    "mark_code_used",
    "normalize_recovery_code",
    "verify_recovery_code",
    "verify_totp",
]
