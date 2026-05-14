"""PKCE helpers — RFC 7636 S256 only."""

from __future__ import annotations

import base64
import hashlib

import pytest

from voltari_gateway.auth.oauth_pkce import (
    PKCE_MAX_LEN,
    PkceError,
    validate_code_challenge,
    validate_code_verifier,
    verify_pkce,
)


def _challenge_for(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def test_round_trip_succeeds():
    verifier = "a" * 64
    challenge = _challenge_for(verifier)
    verify_pkce(verifier=verifier, challenge=challenge, method="S256")  # no raise


def test_wrong_verifier_raises():
    challenge = _challenge_for("a" * 64)
    with pytest.raises(PkceError, match="invalid_grant"):
        verify_pkce(verifier="b" * 64, challenge=challenge, method="S256")


def test_plain_method_rejected():
    with pytest.raises(PkceError, match="unsupported_method"):
        verify_pkce(verifier="a" * 64, challenge="anything", method="plain")


def test_short_verifier_rejected():
    with pytest.raises(PkceError, match="too_short"):
        validate_code_verifier("short")


def test_long_verifier_rejected():
    with pytest.raises(PkceError, match="too_long"):
        validate_code_verifier("x" * (PKCE_MAX_LEN + 1))


def test_verifier_with_invalid_chars_rejected():
    with pytest.raises(PkceError, match="invalid_chars"):
        validate_code_verifier("a" * 50 + "!")


def test_challenge_must_be_url_safe_b64_no_padding():
    # Challenges from real clients are always 43 chars (256-bit SHA → 32B → 43 b64 url chars).
    with pytest.raises(PkceError, match="invalid_challenge"):
        validate_code_challenge("a" * 43 + "=")  # padding not allowed
    with pytest.raises(PkceError, match="invalid_challenge"):
        validate_code_challenge("not enough!!chars")
