"""PKCE (RFC 7636) helpers — S256 only.

We deliberately do NOT support ``plain`` because it adds zero security
beyond not having PKCE at all. A first-party client we control (Studio)
has no excuse to ask for ``plain`` and a third-party client demanding it
should be told no.

RFC 7636 §4.1: ``code_verifier`` is 43–128 chars from the unreserved
URL set (``[A-Za-z0-9-._~]``). §4.2: ``code_challenge`` for S256 is
``BASE64URL-ENCODE(SHA256(ASCII(verifier)))`` with stripped padding —
always 43 chars.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import re

PKCE_MIN_LEN = 43
PKCE_MAX_LEN = 128
PKCE_VERIFIER_RE = re.compile(r"^[A-Za-z0-9._~\-]+$")
PKCE_CHALLENGE_RE = re.compile(r"^[A-Za-z0-9_\-]{43}$")  # exactly 43 chars, no '='


class PkceError(ValueError):
    """Raised on any PKCE validation failure.

    The ``str(exc)`` is one of:
        ``too_short``, ``too_long``, ``invalid_chars``,
        ``invalid_challenge``, ``unsupported_method``, ``invalid_grant``.

    Callers map these to OAuth ``error`` codes per RFC 6749 §5.2.
    """


def validate_code_verifier(verifier: str) -> None:
    if len(verifier) < PKCE_MIN_LEN:
        raise PkceError("too_short")
    if len(verifier) > PKCE_MAX_LEN:
        raise PkceError("too_long")
    if not PKCE_VERIFIER_RE.match(verifier):
        raise PkceError("invalid_chars")


def validate_code_challenge(challenge: str) -> None:
    if not PKCE_CHALLENGE_RE.match(challenge):
        raise PkceError("invalid_challenge")


def _s256(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def verify_pkce(*, verifier: str, challenge: str, method: str) -> None:
    """Constant-time PKCE check. Raises ``PkceError`` on any mismatch.

    Caller must already have asserted ``code_challenge_method == 'S256'``
    when storing the row; we re-check here as a defence-in-depth.
    """
    if method != "S256":
        raise PkceError("unsupported_method")
    validate_code_verifier(verifier)
    validate_code_challenge(challenge)
    expected = _s256(verifier)
    if not hmac.compare_digest(expected, challenge):
        raise PkceError("invalid_grant")
