"""In-memory rate limiter for auth endpoints.

Anti-bruteforce / anti-spam, per-process. Named buckets exposed:

* ``signup``         — 5 / minute, key = client IP
* ``login``          — 5 / minute, key = email + IP
* ``forgot``         — 3 / hour,   key = email
* ``accept_invite``  — 5 / hour,   key = email or IP (anonymous endpoint)
* ``email_resend``   — 3 / hour,   key = email (verify-email re-send)
* ``totp``           — 5 / 30 min, key = user_id (failed TOTP attempts)
* ``password_change``— 5 / hour,   key = user_id (failed change-password)

Why in-memory and not Redis?

* MVP runs on one box. A second box doubles the effective limit; this is
  acceptable for a 5/min limiter. We can swap to a Redis token-bucket once
  we deploy multiple gateway instances; the public ``check`` API will stay
  the same.
* Removes a Redis round-trip from the hottest auth path.
* Tests don't need to clear Redis — calling ``reset()`` between tests is
  enough.

The implementation is a sliding window of timestamps with a fixed cap on
the deque length. Memory is bounded by ``max_keys * limit`` floats.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from typing import Final


class RateLimiter:
    """Sliding-window rate limiter over a per-key deque of timestamps."""

    def __init__(self, *, limit: int, window_seconds: float, max_keys: int = 50_000):
        if limit <= 0:
            raise ValueError("limit must be > 0")
        self._limit = limit
        self._window = window_seconds
        self._max_keys = max_keys
        self._buckets: dict[str, deque[float]] = {}
        # The mutation surface is small + only called from request handlers;
        # asyncio is single-threaded so a lock is technically not needed, but
        # we add a Lock anyway so swapping the loop (or Gunicorn workers in
        # threaded mode) doesn't subtly corrupt the deques.
        self._lock = threading.Lock()

    def _gc(self) -> None:
        """Drop the oldest key when over capacity. Cheap O(1) approximation."""
        if len(self._buckets) <= self._max_keys:
            return
        # Pop arbitrary entry — doesn't matter which, eviction is a safety net.
        try:
            self._buckets.pop(next(iter(self._buckets)))
        except StopIteration:  # pragma: no cover
            return

    def check(self, key: str) -> bool:
        """Record a hit. Returns True iff under limit (request allowed)."""
        now = time.monotonic()
        cutoff = now - self._window
        with self._lock:
            self._gc()
            bucket = self._buckets.setdefault(key, deque(maxlen=self._limit + 1))
            # Drop expired timestamps from the left.
            while bucket and bucket[0] < cutoff:
                bucket.popleft()
            if len(bucket) >= self._limit:
                return False
            bucket.append(now)
            return True

    def reset(self, key: str | None = None) -> None:
        """Clear one key (``key=...``) or all of them (``key=None``)."""
        with self._lock:
            if key is None:
                self._buckets.clear()
            else:
                self._buckets.pop(key, None)


# --- module-level singletons ------------------------------------------------
# Limits are a sane default for solo-MVP (~1 box). Settings overrides are
# applied lazily on first ``check_*`` call so test code that imports this
# module before settings are ready doesn't blow up.

_signup_limiter: RateLimiter | None = None
_login_limiter: RateLimiter | None = None
_forgot_limiter: RateLimiter | None = None
_accept_limiter: RateLimiter | None = None
_email_resend_limiter: RateLimiter | None = None
_password_change_limiter: RateLimiter | None = None
_oauth_limiter: RateLimiter | None = None

# TOTP needs a different shape — we don't want a "burst" of 5 successful
# verifications to lock the user out; we want to record only *failures*
# and lock them out after N consecutive bad codes. Implemented as a
# separate counter map (``_TOTPFailureTracker``) below.

_DEFAULT_SIGNUP_LIMIT: Final[int] = 5
_DEFAULT_LOGIN_LIMIT: Final[int] = 5
_DEFAULT_FORGOT_LIMIT: Final[int] = 3
_DEFAULT_ACCEPT_LIMIT: Final[int] = 5
_DEFAULT_EMAIL_RESEND_LIMIT: Final[int] = 3
_DEFAULT_PASSWORD_CHANGE_LIMIT: Final[int] = 5
_DEFAULT_OAUTH_LIMIT: Final[int] = 5
TOTP_MAX_FAILURES: Final[int] = 5
TOTP_LOCKOUT_SECONDS: Final[int] = 1800  # 30 minutes


def _signup() -> RateLimiter:
    global _signup_limiter
    if _signup_limiter is None:
        from voltari_gateway.config import get_settings

        limit = get_settings().signup_rate_per_minute or _DEFAULT_SIGNUP_LIMIT
        _signup_limiter = RateLimiter(limit=limit, window_seconds=60.0)
    return _signup_limiter


def _login() -> RateLimiter:
    global _login_limiter
    if _login_limiter is None:
        from voltari_gateway.config import get_settings

        limit = get_settings().login_rate_per_minute or _DEFAULT_LOGIN_LIMIT
        _login_limiter = RateLimiter(limit=limit, window_seconds=60.0)
    return _login_limiter


def _forgot() -> RateLimiter:
    global _forgot_limiter
    if _forgot_limiter is None:
        from voltari_gateway.config import get_settings

        limit = get_settings().forgot_rate_per_hour or _DEFAULT_FORGOT_LIMIT
        _forgot_limiter = RateLimiter(limit=limit, window_seconds=3600.0)
    return _forgot_limiter


def _accept() -> RateLimiter:
    """Lazy singleton for the accept-invite limiter.

    Anonymous endpoint (no cookie auth) — without a limit a leaked invite
    link could be re-tried in a tight loop, and a guess-and-check pattern
    could be used to enumerate valid tokens. 5/hour is plenty for a real
    user clicking a link a few times.
    """
    global _accept_limiter
    if _accept_limiter is None:
        from voltari_gateway.config import get_settings

        limit = get_settings().accept_invite_rate_per_hour or _DEFAULT_ACCEPT_LIMIT
        _accept_limiter = RateLimiter(limit=limit, window_seconds=3600.0)
    return _accept_limiter


def _email_resend() -> RateLimiter:
    global _email_resend_limiter
    if _email_resend_limiter is None:
        _email_resend_limiter = RateLimiter(
            limit=_DEFAULT_EMAIL_RESEND_LIMIT, window_seconds=3600.0
        )
    return _email_resend_limiter


def _password_change() -> RateLimiter:
    global _password_change_limiter
    if _password_change_limiter is None:
        _password_change_limiter = RateLimiter(
            limit=_DEFAULT_PASSWORD_CHANGE_LIMIT, window_seconds=3600.0
        )
    return _password_change_limiter


def _oauth() -> RateLimiter:
    global _oauth_limiter
    if _oauth_limiter is None:
        _oauth_limiter = RateLimiter(limit=_DEFAULT_OAUTH_LIMIT, window_seconds=60.0)
    return _oauth_limiter


# ---------------------------------------------------------------------------
# TOTP failure tracker — records consecutive *failed* TOTP verifications and
# locks the user out for ``TOTP_LOCKOUT_SECONDS`` after ``TOTP_MAX_FAILURES``.
# Successful verification resets the counter. Stored in-memory, keyed by
# ``user_id`` string; safe for solo MVP (one box).
# ---------------------------------------------------------------------------


class _TOTPFailureTracker:
    """Counter + lockout per ``user_id``. Single-process, thread-safe."""

    def __init__(self) -> None:
        self._failures: dict[str, list[float]] = {}
        # Locked-until UNIX monotonic; 0.0 = not locked.
        self._locked_until: dict[str, float] = {}
        self._lock = threading.Lock()
        # Bound memory: same approach as RateLimiter._gc.
        self._max_keys = 50_000

    def _gc(self) -> None:
        if len(self._failures) <= self._max_keys:
            return
        try:
            key = next(iter(self._failures))
        except StopIteration:  # pragma: no cover
            return
        self._failures.pop(key, None)
        self._locked_until.pop(key, None)

    def record_failure(self, user_id: str) -> int:
        """Append one failure. Returns the new failure count.

        On the N-th failure (where N = ``TOTP_MAX_FAILURES``) we set the
        lockout — caller checks ``is_locked`` before letting the next
        attempt through.
        """
        now = time.monotonic()
        cutoff = now - TOTP_LOCKOUT_SECONDS
        with self._lock:
            self._gc()
            bucket = self._failures.setdefault(user_id, [])
            # Drop expired failures so a 31-min-old failure doesn't keep
            # contributing to the count.
            bucket[:] = [t for t in bucket if t >= cutoff]
            bucket.append(now)
            count = len(bucket)
            if count >= TOTP_MAX_FAILURES:
                self._locked_until[user_id] = now + TOTP_LOCKOUT_SECONDS
            return count

    def is_locked(self, user_id: str) -> tuple[bool, int]:
        """Return ``(is_locked, retry_after_seconds)``."""
        with self._lock:
            until = self._locked_until.get(user_id, 0.0)
            now = time.monotonic()
            if until > now:
                return True, int(until - now) + 1
            # Lockout expired — clear it lazily.
            if until:
                self._locked_until.pop(user_id, None)
            return False, 0

    def reset(self, user_id: str | None = None) -> None:
        with self._lock:
            if user_id is None:
                self._failures.clear()
                self._locked_until.clear()
                return
            self._failures.pop(user_id, None)
            self._locked_until.pop(user_id, None)


_totp_tracker: _TOTPFailureTracker | None = None


def _totp() -> _TOTPFailureTracker:
    global _totp_tracker
    if _totp_tracker is None:
        _totp_tracker = _TOTPFailureTracker()
    return _totp_tracker


def check_signup(ip: str) -> bool:
    """Return True if ``ip`` is under the signup rate limit (request OK)."""
    return _signup().check(ip)


def check_login(email: str, ip: str) -> bool:
    """Return True if (email, ip) pair is under the login rate limit."""
    return _login().check(f"{email.lower()}|{ip}")


def check_forgot(email: str) -> bool:
    """Return True if ``email`` is under the forgot-password rate limit."""
    return _forgot().check(email.lower())


def check_accept_invite(ip: str) -> bool:
    """Return True if ``ip`` is under the accept-invite rate limit."""
    return _accept().check(ip)


def check_email_resend(email: str) -> bool:
    """Return True if ``email`` is under the verify-email re-send limit."""
    return _email_resend().check(email.lower())


def check_password_change(user_id: str) -> bool:
    """Return True if ``user_id`` is under the change-password retry limit.

    Counts every attempt — the caller hits this once per request before
    verifying the old password. After ``_DEFAULT_PASSWORD_CHANGE_LIMIT``
    attempts in an hour the user is rate-limited regardless of success.
    """
    return _password_change().check(user_id)


def check_oauth(ip: str) -> bool:
    """Return True if ``ip`` is under the OAuth endpoint rate limit (5/min)."""
    return _oauth().check(ip)


def record_totp_failure(user_id: str) -> int:
    """Record one failed TOTP / recovery code verification. Returns count."""
    return _totp().record_failure(user_id)


def check_totp_locked(user_id: str) -> tuple[bool, int]:
    """Return ``(is_locked, retry_after_seconds)`` for the user."""
    return _totp().is_locked(user_id)


def clear_totp_failures(user_id: str) -> None:
    """Clear the failure counter on successful verification."""
    _totp().reset(user_id)


def reset_all() -> None:
    """Test helper — drop every limiter's state."""
    for lim in (
        _signup_limiter,
        _login_limiter,
        _forgot_limiter,
        _accept_limiter,
        _email_resend_limiter,
        _password_change_limiter,
        _oauth_limiter,
    ):
        if lim is not None:
            lim.reset()
    if _totp_tracker is not None:
        _totp_tracker.reset()


def reload_from_settings() -> None:
    """Test helper — reset to None so next call re-reads Settings."""
    global _signup_limiter, _login_limiter, _forgot_limiter, _accept_limiter
    global _email_resend_limiter, _password_change_limiter, _oauth_limiter, _totp_tracker
    _signup_limiter = None
    _login_limiter = None
    _forgot_limiter = None
    _accept_limiter = None
    _email_resend_limiter = None
    _password_change_limiter = None
    _oauth_limiter = None
    _totp_tracker = None


__all__ = [
    "TOTP_LOCKOUT_SECONDS",
    "TOTP_MAX_FAILURES",
    "RateLimiter",
    "check_accept_invite",
    "check_email_resend",
    "check_forgot",
    "check_login",
    "check_oauth",
    "check_password_change",
    "check_signup",
    "check_totp_locked",
    "clear_totp_failures",
    "record_totp_failure",
    "reload_from_settings",
    "reset_all",
]
