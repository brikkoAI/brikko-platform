"""Argon2id password hashing for end-user accounts.

Mirrors the parameters used by ``auth/keys.py`` for API-key hashes so we have
one tuning knob across the auth surface. Both functions are constant-time
where it matters and never raise on bad input.

Usage::

    user.password_hash = hash_password(plain)
    ok = verify_password(plain, user.password_hash)
"""

from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

# Same parameters as auth/keys.py — keeps the verify cost predictable across
# both API-key auth and end-user login. ~5–10 ms on modern hardware.
_HASHER = PasswordHasher(time_cost=2, memory_cost=64 * 1024, parallelism=2)

# Reasonable bounds. Argon2 itself can hash much longer strings, but we don't
# want a 10MB payload to consume CPU before we even know the user exists.
MIN_PASSWORD_LEN = 8
MAX_PASSWORD_LEN = 128


def hash_password(plain: str) -> str:
    """Hash a plaintext password with argon2id.

    Raises ``ValueError`` for inputs outside the configured length window so
    callers get a deterministic failure rather than a billion-laughs DoS.
    """
    if not isinstance(plain, str):
        raise TypeError("password must be a string")
    if len(plain) < MIN_PASSWORD_LEN:
        raise ValueError(f"password must be at least {MIN_PASSWORD_LEN} characters")
    if len(plain) > MAX_PASSWORD_LEN:
        raise ValueError(f"password must be at most {MAX_PASSWORD_LEN} characters")
    return _HASHER.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    """Constant-time argon2 verify. Returns False on any failure, never raises.

    Empty / malformed inputs all collapse to False — callers should not be
    able to distinguish "no such user" from "wrong password" via timing.
    """
    if not plain or not hashed:
        return False
    try:
        return _HASHER.verify(hashed, plain)
    except VerifyMismatchError:
        return False
    except InvalidHashError:
        return False
    except Exception:
        return False


def needs_rehash(hashed: str) -> bool:
    """True when the stored hash uses outdated parameters and should be
    re-hashed on next successful login. Wraps argon2's check so callers don't
    import the underlying library.
    """
    try:
        return _HASHER.check_needs_rehash(hashed)
    except Exception:
        return False
